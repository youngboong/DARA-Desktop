"""Immutable user instrument-profile registry. Registration is not calibration."""
import json
import math
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
