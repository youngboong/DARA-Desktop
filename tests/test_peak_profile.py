"""Single-peak profile fits (Kα1 FWHM), Scherrer sizes and the Williamson-Hall fit."""

import asyncio
import csv
import io

import numpy as np
import pandas as pd
import pytest

from dara.export import peak_profile
from dara.export.peak_list import (
    BELOW_RESOLUTION, OBSERVED_COLUMNS, corrected_fwhm, observed_peak_rows, scherrer_nm, williamson_hall,
)
from dara.export.peak_profile import doublet, fit_peak_profiles
from dara.export.pattern_treatment import kalpha_pair
from dara.server import api_router, quick_refine
from test_observed_peaks_and_pattern_data import loaded  # noqa: F401 - fixture
from test_peak_list import PHASE

PAIR = kalpha_pair("Cu")
LAM = PAIR[0]


def _pattern(peaks, *, background=200.0, seed=0, pair=PAIR, x=None):
    """Poisson-noisy pattern of ``(center, height, fwhm, eta)`` doublets on a flat background."""
    x = np.arange(20.0, 90.0, 0.01) if x is None else x
    clean = background + sum(doublet(x, c, h, w, e, pair) for c, h, w, e in peaks)
    return x, np.random.default_rng(seed).poisson(clean).astype(float), np.full(x.shape, background)


def test_noisy_doublets_recover_the_kalpha1_fwhm():
    truth = [(30.0, 3000, 0.12, 0.3), (45.0, 2500, 0.15, 0.7), (62.0, 2000, 0.20, 0.5), (80.0, 1500, 0.26, 0.4)]
    x, y, background = _pattern(truth)
    fits = fit_peak_profiles(x, y, background, [c + 0.01 for c, *_ in truth], "Cu")
    for (center, _, width, _), fit in zip(truth, fits):
        assert fit["fit_note"] is None
        assert fit["fwhm_deg"] == pytest.approx(width, rel=0.05)
        assert fit["two_theta_fit"] == pytest.approx(center, abs=0.005)
        assert 0 < fit["fwhm_esd_deg"] < 0.05 * width


def test_two_overlapping_peaks_are_separated():
    truth = [(40.0, 2000, 0.10, 0.5), (40.18, 1200, 0.10, 0.5)]
    x, y, background = _pattern(truth, seed=1)
    fits = fit_peak_profiles(x, y, background, [40.01, 40.17], "Cu")
    for (center, _, width, _), fit in zip(truth, fits):
        assert fit["fit_note"] is None
        assert fit["two_theta_fit"] == pytest.approx(center, abs=0.01)
        assert fit["fwhm_deg"] == pytest.approx(width, rel=0.1)


def test_single_line_for_a_numeric_wavelength_and_failure_notes():
    x, y, background = _pattern([(50.0, 2000, 0.1, 0.5)], pair=None)
    (fit,) = fit_peak_profiles(x, y, background, [50.0], "0.0826")
    assert fit["fwhm_deg"] == pytest.approx(0.1, rel=0.05)
    x, y, background = _pattern([(50.0, 3, 0.1, 0.5)], seed=2)
    assert fit_peak_profiles(x, y, background, [50.0], "Cu")[0]["fit_note"] == "too weak"
    crowd = [(50.0 + 0.16 * i, 1000, 0.1, 0.5) for i in range(5)]
    x, y, background = _pattern(crowd)
    assert {fit["fit_note"] for fit in fit_peak_profiles(x, y, background, [c for c, *_ in crowd], "Cu")} == {"overlapped"}
    assert fit_peak_profiles(x, y, background, [10.0], "Cu")[0]["fit_note"] == "fit failed"  # outside the data
    assert fit_peak_profiles(x, y, background, [], "Cu") == []


def test_a_peak_found_twice_is_fitted_once():
    x, y, background = _pattern([(40.0, 2000, 0.15, 0.5)], seed=3)
    weak, strong = fit_peak_profiles(x, y, background, [40.08, 40.0], "Cu")  # e.g. a split on the Kα2 side
    assert weak["fit_note"] == "merged" and weak["fwhm_deg"] is None
    assert strong["fit_note"] is None and strong["fwhm_deg"] == pytest.approx(0.15, rel=0.05)


def test_instrument_correction_and_scherrer():
    assert corrected_fwhm(0.2, 0.3, None) == 0.2
    assert corrected_fwhm(0.2, 0.3, 0.12) == pytest.approx(0.16)  # Gaussian-like: quadrature
    assert corrected_fwhm(0.2, 0.7, 0.12) == pytest.approx(0.08)  # Lorentzian-like: linear
    assert corrected_fwhm(0.13, 0.7, 0.12) is None  # within 1.1 × the instrument
    frame = pd.DataFrame({"2theta": [40.0, 60.0], "intensity": [10.0, 5.0]})
    fits = [{"fwhm_deg": 0.2, "fwhm_esd_deg": 0.004, "eta": 0.3, "fit_note": None},
            {"fwhm_deg": None, "fwhm_esd_deg": None, "eta": None, "fit_note": "too weak"}]
    rows = observed_peak_rows(frame, [], wavelength="Cu", profiles=fits)
    expected = 0.89 * LAM / 10 / (np.radians(0.2) * np.cos(np.radians(20)))
    assert rows[0]["size_nm"] == pytest.approx(expected, abs=0.1) and rows[0]["size_note"] is None
    assert rows[1]["size_nm"] is None and rows[1]["fit_note"] == "too weak" and rows[1]["size_note"] is None
    corrected = observed_peak_rows(frame, [], wavelength="Cu", profiles=fits, k=1.0, instrument_fwhm=0.12)
    assert corrected[0]["size_nm"] == pytest.approx(expected / 0.89 * 0.2 / 0.16, abs=0.1)
    tight = observed_peak_rows(frame, [], wavelength="Cu", profiles=fits, instrument_fwhm=0.19)
    assert tight[0]["size_nm"] is None and tight[0]["size_note"] == BELOW_RESOLUTION


def _wh_rows(size_nm, strain, angles, *, k=0.89, instrument=None, eta=0.3):
    rows = []
    for number, two_theta in enumerate(angles):
        theta = np.radians(two_theta / 2)
        beta = np.degrees(k * LAM / 10 / (size_nm * np.cos(theta)) + 4 * strain * np.tan(theta))
        fwhm = np.hypot(beta, instrument) if instrument else beta
        rows.append({"phase": PHASE, "phase_label": "Test", "hkl": f"{number} 0 0", "two_theta_deg": two_theta,
                     "fwhm_deg": fwhm, "fwhm_esd_deg": 0.002, "eta": eta})
    return rows


@pytest.mark.parametrize("instrument", [None, 0.07])
def test_williamson_hall_recovers_size_and_strain(instrument):
    rows = _wh_rows(60.0, 0.002, [25.0, 38.0, 47.0, 62.0, 78.0], instrument=instrument)
    rows.append({**rows[0], "fwhm_deg": None})  # no FWHM: left out and counted
    (phase,) = williamson_hall(rows, wavelength="Cu", instrument_fwhm=instrument)
    assert phase["peaks_used"] == 5 and phase["unresolved"] == 1
    assert phase["size_nm"] == pytest.approx(60.0, rel=0.05)
    assert phase["strain_percent"] == pytest.approx(0.2, rel=0.05)
    assert phase["reliable"] is True and phase["note"] is None
    point = phase["points"][0]
    assert point["x"] == pytest.approx(4 * np.sin(np.radians(12.5))) and point["y_err"] > 0
    assert scherrer_nm(1.0, 30.0, LAM, 1.0) == pytest.approx(LAM / 10 / (np.radians(1.0) * np.cos(np.radians(15))))


def test_williamson_hall_requirements_and_reliability_gate():
    assert "at least 4" in williamson_hall(_wh_rows(60.0, 0.002, [25.0, 40.0, 60.0]), wavelength="Cu")[0]["note"]
    narrow = williamson_hall(_wh_rows(60.0, 0.002, [30.0, 35.0, 40.0, 45.0]), wavelength="Cu")[0]
    assert narrow["size_nm"] is None and "span" in narrow["note"] and narrow["reliable"] is False
    scatter = _wh_rows(60.0, 0.002, [25.0, 40.0, 55.0, 70.0, 85.0])
    for row, factor in zip(scatter, [1.6, 0.6, 1.6, 0.6, 1.6]):
        row["fwhm_deg"] *= factor
    noisy = williamson_hall(scatter, wavelength="Cu")[0]
    assert noisy["reliable"] is False and noisy["r_squared"] < 0.5
    assert noisy["note"].startswith(f"Scatter too large for a Williamson–Hall estimate (R² {noisy['r_squared']:.2f})")
    strain_only = williamson_hall(_wh_rows(1e9, 0.003, [25.0, 40.0, 55.0, 70.0]), wavelength="Cu")[0]
    assert strain_only["intercept"] == pytest.approx(0, abs=1e-6)
    no_size = _wh_rows(1e9, 0.003, [25.0, 40.0, 55.0, 70.0])
    no_size[0]["fwhm_deg"] *= 0.9  # pushes the intercept below zero
    gated = williamson_hall(no_size, wavelength="Cu")[0]
    assert gated["size_nm"] is None and gated["reliable"] is False and "no size broadening resolved" in gated["note"]
    negative = williamson_hall(_wh_rows(40.0, -0.0005, [25.0, 40.0, 55.0, 70.0]), wavelength="Cu")[0]
    assert negative["strain_percent"] == 0.0 and negative["reliable"] is False
    assert negative["note"] == "Width falls with angle: instrument broadening is not removed (enter the instrument FWHM or use the Rietveld value)"
    vague = _wh_rows(1500.0, 0.002, [25.0, 35.0, 45.0, 60.0, 75.0, 90.0])
    for row, factor in zip(vague, [1.05, 0.95, 1.05, 0.95, 1.05, 0.95]):
        row["fwhm_deg"] *= factor
    unsure = williamson_hall(vague, wavelength="Cu")[0]
    assert unsure["size_esd_nm"] >= unsure["size_nm"] and unsure["r_squared"] >= 0.5
    assert unsure["reliable"] is False and unsure["note"] == "Size uncertainty exceeds the value"


def test_profile_fits_are_cached_per_fit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(quick_refine, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(peak_profile, "fit_peak_profiles", lambda *args: calls.append(args) or [{"fwhm_deg": 0.1}])
    uuid = "12345678-1234-5678-1234-567812345678"
    first = quick_refine.task_profile_fits(uuid, [1.0, 2.0], [3.0, 4.0], [0.0, 0.0], [1.5], "Cu", None)
    again = quick_refine.task_profile_fits(uuid, [1.0, 2.0], [3.0, 4.0], [0.0, 0.0], [1.5], "Cu", None)
    other = quick_refine.task_profile_fits(uuid, [1.0, 2.0], [3.0, 5.0], [0.0, 0.0], [1.5], "Cu", None)
    assert first == again == other == [{"fwhm_deg": 0.1}] and len(calls) == 2


def test_observed_peaks_and_williamson_hall_endpoints(loaded):  # noqa: F811
    body = asyncio.run(api_router.task_peaks_measured(5, None, 0.94, 0.05))
    assert body["columns"] == list(OBSERVED_COLUMNS) and body["k"] == 0.94 and body["instrument_fwhm"] == 0.05
    assert "breadth_deg" not in body["columns"] and "b1" not in body["rows"][0]
    first = body["rows"][0]  # 29.36: a Gaussian of FWHM 0.30 (fake fit), fitted as a Cu doublet
    assert first["fwhm_deg"] == pytest.approx(0.30, rel=0.1) and first["size_nm"] > 0
    response = asyncio.run(api_router.task_peaks_measured_csv(5, None, 0.89, None))
    header = next(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert tuple(header) == OBSERVED_COLUMNS
    wh = asyncio.run(api_router.task_williamson_hall(5, None, 0.89, None))
    (phase,) = wh["phases"]
    assert phase["phase"] == PHASE and phase["reliable"] is False and "at least 4" in phase["note"]
    assert set(phase["rietveld"]) == {"size_nm", "size_esd_nm", "strain_percent", "strain_esd_percent"}
    assert wh["k"] == 0.89 and wh["instrument_fwhm"] is None
