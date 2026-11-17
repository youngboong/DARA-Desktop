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


# ---- Instrument FWHM curve (Caglioti) from a measured standard ------------------------------

CURVE_MIN_PEAKS = 5
CURVE_MIN_SPAN_DEG = 40.0
CURVE_MAX_RMS_DEG = 0.02
_MIN_REL_INTENSITY = 1.0  # % of the strongest calculated reflection
_SNAP_DEG = 0.3  # a measured peak may sit this far from its calculated angle (zero shift, displacement)


def caglioti_fwhm(curve, two_theta):
    """Instrument FWHM b (deg) of ``curve`` at ``two_theta``: sqrt(U tan^2 + V tan + W).

    Outside the 2theta range of the standard's peaks the nearest edge value is used (no
    extrapolation). Returns ``(b, clamped)``; b is None when the curve is not positive there."""
    angle = min(max(float(two_theta), curve["two_theta_min"]), curve["two_theta_max"])
    t = math.tan(math.radians(angle / 2))
    square = curve["u"] * t * t + curve["v"] * t + curve["w"]
    return (math.sqrt(square) if square > 0 else None), angle != float(two_theta)


def fwhm_curve_from_standard(angles, intensities, standard, wavelength="Cu", excluded_ranges=None):
    """Caglioti curve FWHM^2 = U tan^2(theta) + V tan(theta) + W of a measured line-broadening standard.

    Background: the running-minimum estimate of the search pre-filter (``observed_profile``).
    Peak positions: the standard's own reflections (shipped CIF, Kalpha1), each moved to the
    highest net point within 0.3 deg, then pseudo-Voigt doublet fits (``fit_peak_profiles``)
    and a weighted least-squares fit of FWHM^2 against tan(theta).
    """
    import dataclasses

    import numpy as np

    from dara.export.peak_profile import fit_peak_profiles
    from dara.search.prefilter import PrefilterConfig, observed_profile, simulate_reflections
    from dara.search.quick_rank import wavelength_lines

    x = np.asarray(angles, dtype=float)
    y = np.asarray(intensities, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[finite], kind="stable")
    x, y = x[finite][order], y[finite][order]
    report = {"standard": standard, "wavelength": wavelength, "u": None, "v": None, "w": None,
              "u_esd": None, "v_esd": None, "w_esd": None, "two_theta_min": None, "two_theta_max": None,
              "peaks": [], "peak_count": 0, "rms_deg": None, "usable": False, "reason": None}
    # no smoothing, so that observed - net is the background alone
    profile = observed_profile(x, y, excluded_ranges=excluded_ranges,
                               config=dataclasses.replace(PrefilterConfig(), smooth_half_width_deg=0.0))
    if not profile.usable:
        report["reason"] = "The pattern is too short or too flat."
        return report
    net = profile.net
    background = y - net
    calculated, height, _ = simulate_reflections(standard_cif(standard), wavelength_lines(wavelength)[0],
                                                 (float(x[0]) + _SNAP_DEG, float(x[-1]) - _SNAP_DEG))
    positions = []
    for center in calculated[height >= _MIN_REL_INTENSITY * height.max() / 100]:
        near = np.flatnonzero(np.abs(x - center) <= _SNAP_DEG)
        if near.size:
            positions.append(float(x[near[np.argmax(net[near])]]))
    fits = fit_peak_profiles(x, y, background, positions, wavelength, excluded_ranges)
    points = sorted({(round(f["two_theta_fit"], 4), f["fwhm_deg"], f["fwhm_esd_deg"]) for f in fits
                     if f["fit_note"] is None and f["fwhm_esd_deg"] > 0})
    report["peaks"] = [{"two_theta": tt, "fwhm_deg": round(h, 5), "fwhm_esd_deg": round(e, 5)} for tt, h, e in points]
    report["peak_count"] = len(points)
    if len(points) < 3:
        report["reason"] = f"{len(points)} peaks of {standard} could be fitted; at least {CURVE_MIN_PEAKS} are needed."
        return report
    tt, h, e = (np.array(column) for column in zip(*points))
    t = np.tan(np.radians(tt / 2))
    design = np.column_stack((t * t, t, np.ones_like(t)))
    sigma = 2 * h * e  # esd of FWHM^2
    (u, v, w), *_ = np.linalg.lstsq(design / sigma[:, None], h * h / sigma, rcond=None)
    dof = len(points) - 3
    chi2 = float(np.sum(((design @ (u, v, w) - h * h) / sigma) ** 2)) / dof if dof else 1.0
    esd = np.sqrt(np.clip(np.diag(np.linalg.pinv((design / sigma[:, None]).T @ (design / sigma[:, None])) * chi2), 0, None))
    lo, hi = float(tt.min()), float(tt.max())
    report.update(u=float(u), v=float(v), w=float(w), u_esd=float(esd[0]), v_esd=float(esd[1]), w_esd=float(esd[2]),
                  two_theta_min=lo, two_theta_max=hi)
    grid = np.tan(np.radians(np.linspace(lo, hi, 200) / 2))
    positive = bool(np.all(u * grid**2 + v * grid + w > 0))
    if positive:
        fitted = np.sqrt(design @ (u, v, w))
        report["rms_deg"] = float(np.sqrt(np.mean((fitted - h) ** 2)))
    reasons = []
    if len(points) < CURVE_MIN_PEAKS:
        reasons.append(f"{len(points)} peaks fitted; at least {CURVE_MIN_PEAKS} are needed")
    if hi - lo < CURVE_MIN_SPAN_DEG:
        reasons.append(f"fitted peaks span {hi - lo:.1f} deg 2theta; at least {CURVE_MIN_SPAN_DEG:.0f} deg is needed")
    if not positive:
        reasons.append("the fitted FWHM^2 is not positive over the covered range")
    elif report["rms_deg"] > CURVE_MAX_RMS_DEG:
        reasons.append(f"residual RMS {report['rms_deg']:.3f} deg exceeds {CURVE_MAX_RMS_DEG} deg")
    report["usable"] = not reasons
    report["reason"] = "; ".join(reasons) or None
    return report


def profile_key(profile):
    """Canonical name of a registered (``user-<uuid>``) or bundled profile; raises when unknown."""
    profile = str(profile)
    if profile.startswith("user-"):
        registered_profile_path(profile)
        return profile
    if Path(profile).name != profile or not (DEVICES / f"{profile}.geq").is_file():
        raise FileNotFoundError("Instrument profile not found")
    return profile


def _curve_path(profile):
    # Registered profiles are immutable and bundled ones read-only: the curve is a separate record.
    return registry_directory() / "fwhm-curves" / f"{profile_key(profile)}.json"


def load_fwhm_curve(profile):
    """Stored FWHM curve of ``profile`` or None (also for an unknown profile)."""
    try:
        return json.loads(_curve_path(profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_fwhm_curve(profile, curve):
    path = _curve_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(curve), encoding="utf-8")
    temporary.replace(path)


def delete_fwhm_curve(profile):
    """True when a stored curve was removed."""
    path = _curve_path(profile)
    if not path.is_file():
        return False
    path.unlink()
    return True


def stored_fwhm_curves():
    """{profile: summary} of every stored curve, for the profile listing."""
    curves = {}
    for path in (registry_directory() / "fwhm-curves").glob("*.json"):
        try:
            curve = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        curves[path.stem] = {key: curve.get(key) for key in (
            "standard", "measured_on", "two_theta_min", "two_theta_max", "rms_deg", "peak_count", "pattern")}
    return curves
