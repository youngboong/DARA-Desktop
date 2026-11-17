"""Observed peak list (EFLECH peaks assigned to reflections) and pattern data downloads."""

import asyncio
import csv
import io
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dara.export.peak_list import OBSERVED_COLUMNS, observed_peak_rows, peak_rows
from dara.server import api_router, quick_refine, review_router
from test_peak_list import PHASE, _refinement


def _frame(angles, intensities):
    return pd.DataFrame({"2theta": angles, "intensity": intensities})


def test_observed_peaks_are_assigned_within_tolerance():
    reflections = peak_rows(_refinement([29.35, 50.0], [100.0, 40.0]), wavelength="Cu")
    rows = observed_peak_rows(
        _frame([50.05, 29.40, 40.0], [20.0, 80.0, 5.0]), reflections, wavelength="Cu"
    )
    assert [row["two_theta_deg"] for row in rows] == [29.4, 40.0, 50.05]  # ordered by 2theta
    assert rows[0]["phase"] == PHASE and rows[0]["hkl"] == "1 1 1"
    assert rows[0]["delta_two_theta_deg"] == pytest.approx(0.05)  # observed - calculated
    assert rows[0]["intensity_rel_percent"] == 100.0 and rows[2]["intensity_rel_percent"] == 25.0
    assert rows[0]["d_angstrom"] == pytest.approx(1.540598 / (2 * np.sin(np.radians(29.4 / 2))), abs=1e-4)
    assert rows[1]["phase"] is None and rows[1]["hkl"] is None  # unassigned
    assert rows[2]["hkl"] == "2 1 1"


def test_assignment_window_is_the_calculated_fwhm_when_wider_than_0_1_deg():
    reflections = peak_rows(_refinement([30.0]), wavelength="Cu")  # FWHM 0.30
    rows = observed_peak_rows(_frame([30.25, 30.4], [1.0, 1.0]), reflections, wavelength="Cu")
    assert rows[0]["phase"] == PHASE  # 0.25 <= 0.30
    assert rows[1]["phase"] is None  # 0.40 > 0.30
    assert observed_peak_rows(_frame([30.0], [1.0]), [], wavelength="Cu")[0]["phase"] is None


@pytest.fixture
def loaded(monkeypatch):
    refinement = _refinement([29.35, 50.0], [100.0, 40.0])
    x = np.asarray(refinement.plot_data.x)
    curve = np.asarray(refinement.plot_data.structs[PHASE])
    refinement.plot_data.y_bkg = (np.full(x.shape, 10.0)).tolist()
    refinement.plot_data.y_calc = (curve + 10.0).tolist()
    refinement.plot_data.y_obs = (curve * 1.02 + 10.5).tolist()
    refinement.lst_data = SimpleNamespace(phases_results={PHASE: SimpleNamespace()})
    document = SimpleNamespace(
        final_result=refinement, results=[], input_cifs=[], search_kwargs={"wavelength": "Cu"},
        xrd_data=SimpleNamespace(angles=[5.0, 5.02, 5.04], intensities=[100.0, 101.0, 99.5]),
    )
    task = {"index": 5, "uuid": "u", "status": "COMPLETED", "job": {"name": "a b.xrdml"}}

    async def load(task_id):
        return task, document

    monkeypatch.setattr(review_router, "_load_completed_task", load)
    monkeypatch.setattr(quick_refine, "task_peaks", lambda uuid, document: _frame([29.36, 44.0], [50.0, 10.0]))
    return refinement


def test_observed_peaks_endpoint_and_csv(loaded):
    body = asyncio.run(api_router.task_peaks_measured(5, None))
    assert body["columns"] == list(OBSERVED_COLUMNS) and body["selection"] == "best"
    assert body["unassigned"] == 1 and len(body["rows"]) == 2
    assert body["rows"][0]["phase"] == PHASE
    response = asyncio.run(api_router.task_peaks_measured_csv(5, None))
    rows = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert tuple(rows[0]) == OBSERVED_COLUMNS and len(rows) == 3
    assert 'filename="dara_task_5_a_b_peaks_observed.csv"' in response.headers["content-disposition"]


def test_pattern_xy_measured_and_background_subtracted(loaded):
    response = asyncio.run(api_router.task_pattern_xy(5, "measured", None))
    lines = response.body.decode().splitlines()
    assert lines[0].startswith("# DARA measured pattern | task 5: a b.xrdml | wavelength Cu (Ka1 1.540598 A)")
    assert lines[1:] == ["5.000000 100.000000", "5.020000 101.000000", "5.040000 99.500000"]
    assert 'filename="dara_task_5_a_b_measured.xy"' in response.headers["content-disposition"]
    response = asyncio.run(api_router.task_pattern_xy(5, "background_subtracted", None))
    data = np.loadtxt(io.StringIO(response.body.decode()), comments="#")
    expected = np.asarray(loaded.plot_data.y_obs) - np.asarray(loaded.plot_data.y_bkg)
    assert data.shape == (len(loaded.plot_data.x), 2)
    assert np.allclose(data[:, 1], expected, atol=1e-5)
    assert 'filename="dara_task_5_a_b_bgsub.xy"' in response.headers["content-disposition"]


def test_pattern_csv_columns_add_up(loaded):
    response = asyncio.run(api_router.task_pattern_csv(5, None))
    assert 'filename="dara_task_5_a_b_fit.csv"' in response.headers["content-disposition"]
    frame = pd.read_csv(io.StringIO(response.body.decode("utf-8-sig")))
    assert list(frame.columns) == [
        "two_theta_deg", "d_angstrom", "observed", "calculated", "background", "difference", "Test",
    ]
    assert np.allclose(frame["observed"] - frame["calculated"], frame["difference"], atol=1e-4)
    assert np.allclose(frame["background"] + frame["Test"], frame["calculated"], atol=1e-4)
    assert frame["d_angstrom"].iloc[0] == pytest.approx(1.540598 / (2 * np.sin(np.radians(5.0))), rel=1e-6)
