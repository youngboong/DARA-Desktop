"""Peak list rows: FWHM from the calculated curve, grouping, overlap, d spacing (design 2.3)."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dara.export.peak_list import COLUMNS, peak_csv_bytes, peak_rows, wavelength_angstrom

PHASE = "Test_225_(cod_1)-0"
FWHM = 0.30


def _refinement(angles, intensities=None, *, phases=None):
    """Fake refinement: one Gaussian of FWHM 0.30 degrees per reflection angle."""
    x = np.arange(10.0, 80.0, 0.01)
    sigma = FWHM / (2 * np.sqrt(2 * np.log(2)))
    curve = sum(np.exp(-0.5 * ((x - angle) / sigma) ** 2) * 100 for angle in angles)
    peaks = pd.DataFrame(
        {
            "2theta": angles,
            "intensity": intensities or [100.0] * len(angles),
            "b1": 0.0, "b2": 0.0,
            "h": list(range(1, len(angles) + 1)), "k": 1, "l": 1,
            "phase": phases or PHASE, "phase_idx": 0,
        }
    )
    return SimpleNamespace(
        peak_data=peaks, plot_data=SimpleNamespace(x=x.tolist(), structs={PHASE: curve.tolist()})
    )


def test_isolated_peak_fwhm_and_d_spacing():
    rows = peak_rows(_refinement([29.35, 50.0], [100.0, 40.0]), wavelength="Cu")
    assert [row["two_theta_deg"] for row in rows] == [29.35, 50.0]
    for row in rows:
        assert row["fwhm_deg"] == pytest.approx(FWHM, abs=0.002)
        assert row["fwhm_note"] is None and row["d_source"] == "bragg"
    assert rows[0]["d_angstrom"] == pytest.approx(1.540598 / (2 * np.sin(np.radians(29.35 / 2))), abs=1e-4)
    assert rows[1]["intensity_rel_phase_percent"] == 40.0
    assert rows[1]["intensity_rel_all_percent"] == 40.0
    assert wavelength_angstrom("0.0826") == pytest.approx(0.826)


def test_lattice_gives_d_from_hkl():
    rows = peak_rows(
        _refinement([29.35]), wavelength="Cu",
        lattice_by_phase={PHASE: dict(a=5.0, b=5.0, c=5.0, alpha=90, beta=90, gamma=90)},
    )
    assert rows[0]["d_source"] == "lattice"
    assert rows[0]["d_angstrom"] == pytest.approx(5.0 / np.sqrt(3), abs=1e-4)  # (1 1 1)


def test_close_reflections_share_one_peak():
    rows = peak_rows(_refinement([40.0, 40.01]), wavelength="Cu")
    assert rows[0]["fwhm_deg"] == pytest.approx(FWHM, abs=0.005)
    assert rows[1]["fwhm_deg"] is None and rows[1]["fwhm_note"] == "same peak as row above"


def test_neighbours_within_0_4_degrees_are_overlapped():
    rows = peak_rows(_refinement([40.0, 40.2]), wavelength="Cu")
    assert [row["fwhm_deg"] for row in rows] == [None, None]
    assert [row["fwhm_note"] for row in rows] == ["overlapped", "overlapped"]


def test_csv_has_the_documented_header_and_bom():
    data = peak_csv_bytes(peak_rows(_refinement([29.35]), wavelength="Cu"))
    assert data.startswith(b"\xef\xbb\xbf")
    header = data.decode("utf-8-sig").splitlines()[0].split(",")
    assert header[:12] == [
        "phase", "phase_label", "h", "k", "l", "two_theta_deg", "d_angstrom", "intensity",
        "intensity_rel_phase_percent", "intensity_rel_all_percent", "fwhm_deg", "fwhm_note",
    ]
    assert tuple(header) == COLUMNS
