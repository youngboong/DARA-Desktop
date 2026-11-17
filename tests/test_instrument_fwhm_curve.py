"""Instrument FWHM curve (Caglioti) from a line-broadening standard, and its use in Scherrer / W-H."""

import asyncio
import io

import numpy as np
import pytest
from fastapi import HTTPException, UploadFile

from dara import instrument_profiles
from dara.export.pattern_treatment import kalpha_pair
from dara.export.peak_list import observed_peak_rows, williamson_hall
from dara.export.peak_profile import doublet, fit_peak_profiles
from dara.search.prefilter import simulate_reflections
from dara.server import api_router, instrument_router, review_router
from test_observed_peaks_and_pattern_data import _frame, loaded  # noqa: F401 - fixture

PAIR = kalpha_pair("Cu")
LAM = PAIR[0]
U, V, W = 0.02, -0.005, 0.004  # b grows from 0.06 deg at 25 deg to 0.19 deg at 110 deg 2theta
BUNDLED = "Aeris-fds-Pixcel1d-Medipix3"


def instrument_b(two_theta):
    t = np.tan(np.radians(np.asarray(two_theta) / 2))
    return np.sqrt(U * t**2 + V * t + W)


def standard_pattern(lo=15.0, hi=120.0, seed=0):
    """LaB6 reflections (Kalpha doublets, eta 0.3) with the instrument's width, Poisson noise."""
    x = np.arange(lo, hi, 0.01)
    centers, heights, _ = simulate_reflections(instrument_profiles.standard_cif("LaB6"), LAM, (lo + 0.5, hi - 0.5))
    clean = 300 + sum(doublet(x, c, 60 * h, float(instrument_b(c)), 0.3, PAIR) for c, h in zip(centers, heights))
    return x, np.random.default_rng(seed).poisson(clean).astype(float)


@pytest.fixture(scope="module")
def curve():
    return instrument_profiles.fwhm_curve_from_standard(*standard_pattern(), "LaB6", "Cu")


def test_curve_reproduces_the_instrument_width(curve):
    assert curve["usable"] is True and curve["reason"] is None
    assert curve["peak_count"] >= 10 and curve["two_theta_max"] - curve["two_theta_min"] > 80
    assert curve["rms_deg"] < 0.01 and all(curve[key] > 0 for key in ("u_esd", "v_esd", "w_esd"))
    for two_theta in np.linspace(curve["two_theta_min"], curve["two_theta_max"], 25):
        b, clamped = instrument_profiles.caglioti_fwhm(curve, two_theta)
        assert b == pytest.approx(instrument_b(two_theta), rel=0.05) and not clamped


def test_too_few_peaks_is_not_usable():
    narrow = instrument_profiles.fwhm_curve_from_standard(*standard_pattern(18.0, 46.0), "LaB6", "Cu")
    assert narrow["usable"] is False and "at least 5" in narrow["reason"] and "span" in narrow["reason"]
    flat = instrument_profiles.fwhm_curve_from_standard(np.arange(20, 80, 0.02), np.full(3000, 100.0), "Si", "Cu")
    assert flat["usable"] is False and flat["u"] is None and flat["reason"]


def test_width_outside_the_range_is_the_edge_value(curve):
    low, _ = instrument_profiles.caglioti_fwhm(curve, curve["two_theta_min"])
    high, _ = instrument_profiles.caglioti_fwhm(curve, curve["two_theta_max"])
    assert instrument_profiles.caglioti_fwhm(curve, 5.0) == (low, True)
    assert instrument_profiles.caglioti_fwhm(curve, 150.0) == (high, True)


def sample_fits(size_nm, strain, angles, seed=1):
    """Gaussian-like (eta 0.2) sample peaks: B^2 = b^2 + beta^2, beta = K lambda / (D cos) + 4 strain tan."""
    x = np.arange(20.0, 118.0, 0.01)
    theta = np.radians(np.asarray(angles) / 2)
    beta = np.degrees(0.89 * LAM / 10 / (size_nm * np.cos(theta)) + 4 * strain * np.tan(theta))
    widths = np.hypot(beta, instrument_b(angles))
    clean = 200 + sum(doublet(x, c, 4000, w, 0.2, PAIR) for c, w in zip(angles, widths))
    y = np.random.default_rng(seed).poisson(clean).astype(float)
    return fit_peak_profiles(x, y, np.full(x.shape, 200.0), list(angles), "Cu")


ANGLES = [25.0, 38.0, 50.0, 62.0, 75.0, 88.0, 100.0, 112.0]


def test_scherrer_with_the_curve_recovers_the_size(curve):
    fits = sample_fits(80.0, 0.0, ANGLES)
    frame = _frame(ANGLES, [1.0] * len(ANGLES))
    with_curve = observed_peak_rows(frame, [], wavelength="Cu", profiles=fits,
                                    instrument_fwhm=lambda tt: instrument_profiles.caglioti_fwhm(curve, tt)[0])
    sizes = [row["size_nm"] for row in with_curve]
    assert all(size == pytest.approx(80.0, rel=0.1) for size in sizes), sizes
    assert with_curve[-1]["instrument_fwhm_deg"] == pytest.approx(instrument_b(112.0), rel=0.05)
    constant = observed_peak_rows(frame, [], wavelength="Cu", profiles=fits, instrument_fwhm=float(instrument_b(25.0)))
    assert np.mean([row["size_nm"] for row in constant]) < 0.9 * 80.0  # high-angle width left in


def test_williamson_hall_with_the_curve_recovers_size_and_strain(curve):
    fits = sample_fits(80.0, 0.001, ANGLES, seed=2)
    rows = [{"phase": "P", "phase_label": "P", "hkl": None, "two_theta_deg": fit["two_theta_fit"],
             "fwhm_deg": fit["fwhm_deg"], "fwhm_esd_deg": fit["fwhm_esd_deg"], "eta": fit["eta"]} for fit in fits]
    (phase,) = williamson_hall(rows, wavelength="Cu",
                               instrument_fwhm=lambda tt: instrument_profiles.caglioti_fwhm(curve, tt)[0])
    assert phase["size_nm"] == pytest.approx(80.0, rel=0.1)
    assert phase["strain_percent"] == pytest.approx(0.1, rel=0.1) and phase["reliable"] is True


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(instrument_profiles, "registry_directory", lambda: tmp_path / "profiles")
    monkeypatch.setattr(instrument_router, "registry_directory", lambda: tmp_path / "profiles")
    return tmp_path / "profiles"


def test_storage_round_trip(registry, curve):
    stored = {**curve, "pattern": "lab6.xy", "measured_on": "2026-11-17"}
    instrument_profiles.save_fwhm_curve(BUNDLED, stored)
    assert instrument_profiles.load_fwhm_curve(BUNDLED) == stored
    summary = instrument_profiles.stored_fwhm_curves()[BUNDLED]
    assert summary["standard"] == "LaB6" and summary["measured_on"] == "2026-11-17"
    assert summary["rms_deg"] == curve["rms_deg"]
    assert instrument_profiles.delete_fwhm_curve(BUNDLED) is True
    assert instrument_profiles.load_fwhm_curve(BUNDLED) is None and not instrument_profiles.delete_fwhm_curve(BUNDLED)
    assert instrument_profiles.load_fwhm_curve("no-such-profile") is None
    for bad in ("no-such-profile", "../escape", "user-../../escape"):
        with pytest.raises((ValueError, FileNotFoundError)):
            instrument_profiles.save_fwhm_curve(bad, stored)


def upload(name, content):
    return UploadFile(file=io.BytesIO(content), filename=name)


def xy_bytes(x, y):
    return "\n".join(f"{a:.3f} {b:.0f}" for a, b in zip(x, y)).encode()


def test_fwhm_curve_endpoints(registry):
    with pytest.raises(HTTPException) as error:
        asyncio.run(instrument_router.measure_fwhm_curve("nope", "LaB6", "Cu", upload("s.xy", b"1 2\n")))
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        asyncio.run(instrument_router.measure_fwhm_curve(BUNDLED, "LaB6", "Cu", upload("s.bad", b"x")))
    assert error.value.status_code == 400
    narrow = asyncio.run(instrument_router.measure_fwhm_curve(BUNDLED, "LaB6", "Cu", upload("s.xy", xy_bytes(*standard_pattern(18, 46)))))
    assert narrow["stored"] is False and narrow["usable"] is False
    with pytest.raises(HTTPException) as error:
        asyncio.run(instrument_router.fwhm_curve(BUNDLED))
    assert error.value.status_code == 404
    body = asyncio.run(instrument_router.measure_fwhm_curve(BUNDLED, "LaB6", "Cu", upload("lab6.xy", xy_bytes(*standard_pattern()))))
    assert body["stored"] is True and body["profile"] == BUNDLED and body["pattern"] == "lab6.xy" and body["measured_on"]
    assert asyncio.run(instrument_router.fwhm_curve(BUNDLED))["u"] == body["u"]
    listing = asyncio.run(api_router.instrument_profiles())
    assert listing["fwhm_curves"][BUNDLED]["standard"] == "LaB6" and BUNDLED in listing["profiles"]
    assert asyncio.run(instrument_router.delete_fwhm_curve(BUNDLED))["deleted"] is True
    assert BUNDLED not in asyncio.run(api_router.instrument_profiles())["fwhm_curves"]


def test_task_endpoints_choose_the_correction(registry, curve, loaded):  # noqa: F811
    _, document = asyncio.run(review_router._load_completed_task(5))
    document.search_kwargs = {"wavelength": "Cu", "instrument_profile": BUNDLED}
    body = asyncio.run(api_router.task_peaks_measured(5, None, 0.89, None))
    assert body["instrument"]["mode"] == "none" and body["rows"][0]["instrument_fwhm_deg"] is None
    assert asyncio.run(api_router.task_peaks_measured(5, None, 0.89, 0.05))["instrument"]["mode"] == "constant"
    for mode, fwhm in (("curve", None), ("constant", None)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(api_router.task_peaks_measured(5, None, 0.89, fwhm, mode))
        assert error.value.status_code == 400
    instrument_profiles.save_fwhm_curve(BUNDLED, {**curve, "measured_on": "2026-11-17"})
    body = asyncio.run(api_router.task_peaks_measured(5, None, 0.89, 0.05))  # the curve wins by default
    applied = body["instrument"]
    assert applied["mode"] == "curve" and applied["standard"] == "LaB6" and applied["profile"] == BUNDLED
    assert applied["two_theta_range"] == [curve["two_theta_min"], curve["two_theta_max"]] and applied["clamped"] == 0
    first = body["rows"][0]
    assert first["instrument_fwhm_deg"] == pytest.approx(instrument_profiles.caglioti_fwhm(curve, first["two_theta_deg"])[0], abs=1e-4)
    assert asyncio.run(api_router.task_peaks_measured(5, None, 0.89, 0.05, "constant"))["rows"][0]["instrument_fwhm_deg"] == 0.05
    assert asyncio.run(api_router.task_peaks_measured(5, None, 0.89, None, "none"))["instrument"]["mode"] == "none"
    wh = asyncio.run(api_router.task_williamson_hall(5, None, 0.89, None))
    assert wh["instrument"]["mode"] == "curve"
    document.search_kwargs["wavelength"] = "Co"  # a Cu curve does not describe a Co measurement
    assert asyncio.run(api_router.task_peaks_measured(5, None, 0.89, None))["instrument"]["mode"] == "none"


def test_calibration_stores_the_curve_on_the_calibrated_profile(registry, monkeypatch):
    import threading

    monkeypatch.setattr(instrument_profiles, "compile_device_file", lambda text: (text.encode(), b"ger"))
    monkeypatch.setattr(instrument_profiles, "fit_standard", lambda folder, path, standard, wavelength, broadening: (
        5 + np.log(instrument_profiles.device_detw((folder / "instrument.geq").read_text()) / 0.11) ** 2, None))
    built = asyncio.run(instrument_router.build_profile(instrument_router.InstrumentSetup(
        label="Lab D8", radiation="Cu", radius=280, divergence="fixed", divergence_angle=0.5, divergence_distance=180,
        detector="linear", detector_length=14.4, strip_width=0.075)))
    job = asyncio.run(instrument_router.calibrate_profile(
        profile=built["id"], standard="LaB6", wavelength="Cu", pattern_file=upload("lab6.xy", xy_bytes(*standard_pattern()))))
    for _ in range(400):
        if instrument_router._CALIBRATIONS[job["id"]]["status"] != "running":
            break
        threading.Event().wait(0.05)
    done = asyncio.run(instrument_router.calibration_status(job["id"]))
    assert done["status"] == "done", done
    stored = done["result"]
    assert stored["calibration"]["fwhm_curve"] is True and stored["calibration"]["fwhm_curve_note"] is None
    curve = instrument_profiles.load_fwhm_curve(stored["id"])
    assert curve["usable"] and curve["pattern"] == "lab6.xy" and curve["standard"] == "LaB6"
    assert instrument_profiles.load_fwhm_curve(built["id"]) is None  # only the calibrated profile gets it
