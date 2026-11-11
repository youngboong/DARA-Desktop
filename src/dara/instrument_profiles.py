"""Immutable user instrument-profile registry. Registration is not calibration."""
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID


def registry_directory():
    from dara.server.setting import get_dara_server_settings

    return Path(get_dara_server_settings().montydb_path).resolve().parent / "instrument-profiles"


def registered_profiles():
    profiles = {}
    for path in registry_directory().glob("*/metadata.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            profiles[item["id"]] = item
        except (OSError, ValueError, KeyError):
            continue
    return profiles


def registered_profile_path(identifier):
    suffix = str(identifier).removeprefix("user-")
    canonical = str(UUID(suffix))
    if str(identifier) != f"user-{canonical}":
        raise ValueError("Invalid registered profile identifier")
    path = registry_directory() / canonical / "instrument.geq"
    if not path.is_file():
        raise FileNotFoundError("Registered instrument profile not found")
    return path


# ---- Profiles compiled from the instrument's own settings ----------------------------------

# Monte Carlo positions (deg 2theta) used by the bundled device files.
_ANGLES = (2, 4, 8, 13, 20, 30, 42, 56, 76, 90, 105, 120, 135, 150)


def setup_device_file(setup):
    """BGMN device file (.sav) for a Bragg-Brentano instrument described in datasheet units.

    ``setup`` keys: radius, focus_length, focus_width (mm); divergence ("fixed"/"automatic") with
    divergence_angle (deg) or irradiated_length (mm) and divergence_distance (mm);
    soller_primary, soller_secondary (deg, nominal); bruker_soller (BGMN wants half the nominal
    angle for Bruker Soller slits); sample_diameter, beam_mask, beam_mask_distance,
    antiscatter_width, antiscatter_distance (mm, the last four optional);
    detector ("linear"/"point") with detector_length, strip_width, detector_height (mm);
    monochromator ("none"/"graphite").
    """
    soller = 0.5 if setup.get("bruker_soller") else 1.0
    lines = ["SAVE=N", "VERZERR=instrument.ger", "GEQ=instrument.geq", "GEOMETRY=REFLEXION",
             f"R={setup['radius']:g}", f"FocusH={setup['focus_length']:g}", f"FocusW={setup['focus_width']:g}",
             f"HSlitR={setup['divergence_distance']:g}"]
    if setup["divergence"] == "fixed":
        lines += [f"div={setup['divergence_angle']:g}", "HSlitW=2*tan(div*pi/360)*(R-HSlitR)"]
    else:  # automatic slit: constant irradiated length (formula of the bundled ADS device files)
        lines += [f"irr={setup['irradiated_length']:g}",
                  "HSlitW=(2*(R-HSlitR)*irr*sin(pi*zweiTheta/360))/(2*R+irr*cos(pi*zweiTheta/360))"]
    lines += [f"PColl={math.radians(setup['soller_primary']) * soller:.5f}",
              f"SColl={math.radians(setup['soller_secondary']) * soller:.5f}",
              f"SamplD={setup['sample_diameter']:g}"]
    if setup.get("beam_mask") and setup.get("beam_mask_distance"):
        lines += [f"VSlitH={setup['beam_mask']:g}", f"VSlitR={setup['beam_mask_distance']:g}"]
    if setup.get("antiscatter_width") and setup.get("antiscatter_distance"):
        lines += [f"SSlitW={setup['antiscatter_width']:g}", f"SSlitR={setup['antiscatter_distance']:g}"]
    if setup["detector"] == "linear":
        lines += [f"DetArrayW={setup['detector_length']:g}", f"DetW={setup['strip_width']:g}"]
    else:  # point detector: DetW is the receiving slit
        lines += [f"DetW={setup['strip_width']:g}"]
    if setup.get("detector_height"):
        lines += [f"DetH={setup['detector_height']:g}"]
    if setup.get("monochromator") == "graphite":  # secondary graphite (002) for Cu, as in the bundled files
        lines += ["MonR=R+50", "POL=sqr(cos(26.6*pi/180))"]
    else:
        lines += ["POL=1"]
    lines += [f"zweiTheta[{index}]={angle}" for index, angle in enumerate(_ANGLES, 1)]
    lines += ["WMIN=2", "WMAX=150", "WSTEP=2*sin(pi*zweiTheta/180)", "GSUM=Y", "NTHREADS=8", "pi=2*acos(0)"]
    return "\n".join(lines) + "\n"


def compile_device_file(device_text, timeout=1800):
    """(GEQ, GER) bytes from BGMN's GEOMET and MAKEGEQ; takes about two minutes."""
    from dara.bgmn_worker import BGMNWorker, ensure_bgmn_env

    folder = BGMNWorker().bgmn_folder
    ensure_bgmn_env(folder)
    with tempfile.TemporaryDirectory(prefix="dara-geq-") as tmp:
        (Path(tmp) / "instrument.sav").write_text(device_text, encoding="utf-8")
        for program in ("GEOMET.EXE", "MAKEGEQ.EXE"):
            done = subprocess.run([str(folder / program), "instrument.sav"], cwd=tmp, capture_output=True,
                                  text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
            if done.returncode != 0:
                raise RuntimeError(f"{program} failed: {(done.stdout + done.stderr).strip()[-400:]}")
        geq, ger = Path(tmp) / "instrument.geq", Path(tmp) / "instrument.ger"
        if not geq.is_file() or not geq.stat().st_size:
            raise RuntimeError("BGMN did not write a GEQ file; check the settings.")
        return geq.read_bytes(), ger.read_bytes() if ger.is_file() else None


# ---- Calibration of DetW with a measured standard ---------------------------------------

DEVICES = Path(__file__).parent / "data" / "BGMN-Templates" / "Devices"
# Line-profile standards shipped with the package (COD ids in the file names).
STANDARDS = {"Si": "Si_9013102.cif", "LaB6": "LaB6_1000057.cif", "Al2O3": "Al2O3_1000032.cif"}
_DETW = re.compile(r"^([ \t]*DetW[ \t]*=[ \t]*)(\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)[ \t]*$", re.M)
DETW_BOUNDS = (0.005, 3.0)  # mm: physical strip width / receiving slit range
SCAN_FACTORS = (0.2, 0.45, 1, 1.75, 3)
SCAN_COMPILES = len(SCAN_FACTORS) + 1


def standard_cif(standard):
    return Path(__file__).parent / "data" / "standards" / STANDARDS[standard]


def device_file_text(profile):
    """Device file (.sav) behind a registered or bundled profile, or None when there is none."""
    if str(profile).startswith("user-"):
        path = registered_profile_path(profile).with_name("instrument.sav")
    else:
        name = Path(str(profile)).name
        if name != str(profile) or not (DEVICES / f"{name}.geq").is_file():
            raise FileNotFoundError("Instrument profile not found")
        path = next((p for p in (DEVICES / f"{name}.sav", DEVICES / f"{name}.SAV") if p.is_file()), None)
    if path is None or not path.is_file():
        return None
    # latin-1: some bundled files carry non-UTF-8 comment bytes. LF only: write_text adds CR on Windows.
    return path.read_bytes().decode("latin-1").replace("\r\n", "\n")


def device_detw(device_text):
    """Numeric DetW of a device file, or None when it is missing or an expression."""
    match = _DETW.search(device_text)
    return float(match.group(2)) if match else None


def with_detw(device_text, detw):
    """Device file with another DetW, writing instrument.geq/.ger like ``setup_device_file``."""
    text = _DETW.sub(lambda m: f"{m.group(1)}{detw:.5g}", device_text, count=1)
    text = re.sub(r"^[ \t]*GEQ[ \t]*=.*$", "GEQ=instrument.geq", text, flags=re.M)
    return re.sub(r"^[ \t]*VERZERR[ \t]*=.*$", "VERZERR=instrument.ger", text, flags=re.M)


def scan_detw(rwp_at, start):
    """DetW with the lowest Rwp: a log grid of 0.2-3x ``start``, then one parabolic step.

    Returns (best DetW, {DetW: Rwp}); ``rwp_at`` is called at most ``SCAN_COMPILES`` times.
    """
    import numpy as np

    grid = sorted({min(max(start * f, DETW_BOUNDS[0]), DETW_BOUNDS[1]) for f in SCAN_FACTORS})
    curve = {detw: rwp_at(detw) for detw in grid}
    best = min(curve, key=curve.get)
    index = grid.index(best)
    step = None
    if 0 < index < len(grid) - 1:
        a, b, _ = np.polyfit(np.log(grid[index - 1:index + 2]), [curve[d] for d in grid[index - 1:index + 2]], 2)
        if a > 0:
            step = float(np.exp(-b / (2 * a)))
            step = step if grid[index - 1] < step < grid[index + 1] else None
    if step is None:  # best at the edge or no minimum: halve the gap to the better neighbour
        neighbours = [grid[i] for i in (index - 1, index + 1) if 0 <= i < len(grid)]
        step = math.sqrt(best * min(neighbours, key=curve.get)) if neighbours else None
    if step is not None and round(step, 5) not in {round(d, 5) for d in curve}:
        curve[step] = rwp_at(step)
    return min(curve, key=curve.get), curve


def fit_standard(folder, pattern_path, standard, wavelength, broadening):
    """Rwp and apparent size (nm or None) of the standard refined with ``folder/instrument.geq``.

    Lattice (1 %), zero and displacement are free; ``broadening`` frees size and strain.
    """
    import numpy as np

    from dara.refine import do_refinement
    from dara.refinement_controls import RefinementControls, apply_controls
    from dara.utils import get_number

    controls = RefinementControls(lattice="refine", lattice_percent=1, orientation="isotropic",
                                  peak_width="refine" if broadening else "instrument_only")
    result = do_refinement(pattern_path, [standard_cif(standard)], wavelength=wavelength,
                           instrument_profile=Path(folder) / "instrument.geq",
                           working_dir=Path(folder) / ("size" if broadening else "fit"),
                           phase_params=apply_controls(None, controls),
                           refinement_params={"eps1": "0_-0.002^0.002"},
                           observed_peaks=np.zeros((0, 2)))  # no peak markers needed
    phase = next(iter(result.lst_data.phases_results.values()))
    size = get_number((phase.model_extra or {}).get("GrainSize(1,1,1)"))
    return result.lst_data.rwp, size


def calibrate_detw(device_text, pattern_path, standard, wavelength="Cu", progress=lambda done, total: None):
    """Fit DetW of ``device_text`` to a measured standard; returns the calibrated files and figures."""
    start = device_detw(device_text)
    if start is None:
        raise ValueError("The device file has no numeric DetW to calibrate.")
    with tempfile.TemporaryDirectory(prefix="dara-calibrate-") as tmp:
        folders = {}

        def rwp_at(detw):
            progress(len(folders), SCAN_COMPILES)
            folder = Path(tmp) / str(len(folders))
            folder.mkdir()
            geq, ger = compile_device_file(with_detw(device_text, detw))
            (folder / "instrument.geq").write_bytes(geq)
            if ger:
                (folder / "instrument.ger").write_bytes(ger)
            folders[detw] = folder
            return fit_standard(folder, pattern_path, standard, wavelength, False)[0]

        best, curve = scan_detw(rwp_at, start)
        progress(SCAN_COMPILES, SCAN_COMPILES)
        size = fit_standard(folders[best], pattern_path, standard, wavelength, True)[1]
        ger = folders[best] / "instrument.ger"
        return {"geq": (folders[best] / "instrument.geq").read_bytes(), "ger": ger.read_bytes() if ger.is_file() else None,
                "sav": with_detw(device_text, best), "detw": best, "detw_start": start,
                "rwp_start": curve[min(curve, key=lambda d: abs(d - start))], "rwp": curve[best],
                "apparent_size_nm": size, "curve": sorted(curve.items())}
