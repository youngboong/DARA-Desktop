"""Semi-quantitative analysis by the reference intensity ratio (I/Ic) method (dara.rir)."""

import asyncio
import csv
import io
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.integrate import trapezoid

from dara.cif import Cif
from dara.export.peak_list import observed_peak_rows
from dara.export.peak_profile import doublet, doublet_area, fit_peak_profiles, pseudo_voigt
from dara.export.pattern_treatment import kalpha_pair
from dara.rir import RIR_COLUMNS, anomalous_factors, calculated_rir, diffracting_power, rir_quantification
from dara.structure_db import cod_cache_dir
from test_observed_peaks_and_pattern_data import loaded  # noqa: F401 - fixture

DATA = Path(__file__).parent / "test_data" / "rir"
PAIR = kalpha_pair("Cu")


def _structure(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return Cif.from_file(path).to_structure()


ZNO = DATA / "ZnO_zincite_cod9004178.cif"
RUTILE = DATA / "TiO2_rutile_cod9007432.cif"


# Reference I/Ic for Cu Kα: ZnO 5.33 ± 0.05 (NIST SRM 674a certificate, measured); rutile 3.54 and
# anatase 5.04 (ICDD "Quantitative Analysis RIR" tutorial, PDF-4+ values). Calculated values
# scatter around measured ones; the bar is 15 %.
@pytest.mark.parametrize("path, reference, needs_anomalous", [
    (ZNO, 5.33, True),  # Zn f' = -1.55 for Cu Kα: without it I/Ic is ~20 % high
    (RUTILE, 3.54, False),
    (cod_cache_dir() / "7206075.cif", 5.04, False),  # anatase, local COD cache only
])
def test_calculated_rir_matches_tabulated_values(path, reference, needs_anomalous):
    if not path.is_file():
        pytest.skip(f"{path.name} is not available locally")
    if needs_anomalous and not anomalous_factors("Cu"):
        pytest.skip("BGMN anomalous-dispersion tables are not installed")
    assert calculated_rir(_structure(path), "Cu")["rir"] == pytest.approx(reference, rel=0.15)


def test_doublet_area_is_the_integral_of_the_profile():
    x = np.arange(20.0, 60.0, 0.001)
    for eta in (0.0, 0.4, 1.0):
        numeric = np.trapezoid(doublet(x, 40.0, 1000.0, 0.12, eta, PAIR), x) if hasattr(np, "trapezoid") \
            else np.trapz(doublet(x, 40.0, 1000.0, 0.12, eta, PAIR), x)
        # the Lorentzian tails beyond ±20° hold about 0.2 % of the area
        assert doublet_area(1000.0, 0.12, eta, PAIR) == pytest.approx(numeric, rel=3e-3)
    assert doublet_area(1.0, 0.1, 0.0, None) == pytest.approx(trapezoid(pseudo_voigt(x, 40, 0.1, 0.0), x), rel=1e-4)


WEIGHTS = {"ZnO": 0.3, "TiO2": 0.7}
FWHM, ETA = 0.1, 0.5


@pytest.fixture(scope="module")
def mixture():
    """Pattern of a ZnO/rutile mixture built from the same intensity model: the area of each line is
    ∝ w · I/(V²ρ) (Hubbard & Snyder 1988 eq. 1), as Kα1+Kα2 pseudo-Voigt doublets with Poisson noise."""
    structures = {"ZnO": _structure(ZNO), "TiO2": _structure(RUTILE)}
    x = np.arange(20.0, 90.0, 0.01)
    clean = np.full(x.shape, 100.0)
    reflections, positions = [], []
    unit = doublet_area(1.0, FWHM, ETA, PAIR)
    scale = 4.0e7
    for phase, structure in structures.items():
        lines = calculated_rir(structure, "Cu")["lines"]
        for two_theta, intensity, d, hkl in zip(lines["two_theta"], lines["intensity"], lines["d"], lines["hkl"]):
            if two_theta > 89:
                continue
            area = scale * WEIGHTS[phase] * diffracting_power(structure, intensity)
            clean += doublet(x, two_theta, area / unit, FWHM, ETA, PAIR)
            reflections.append({"phase": phase, "phase_label": phase, "h": hkl[0], "k": hkl[1], "l": hkl[2],
                                "two_theta_deg": float(two_theta), "d_angstrom": float(d), "intensity": area,
                                "fwhm_deg": FWHM, "fwhm_note": None, "hkl_database_setting": None})
            if area / unit > 30:  # what a peak search finds above the noise
                positions.append(float(two_theta))
    y = np.random.default_rng(1).poisson(clean).astype(float)
    background = np.full(x.shape, 100.0)
    profiles = fit_peak_profiles(x, y, background, positions, "Cu")
    frame = pd.DataFrame({"2theta": positions, "intensity": [1.0] * len(positions)})
    observed = observed_peak_rows(frame, reflections, wavelength="Cu", profiles=profiles)
    return structures, observed, reflections


def _by_phase(result):
    return {row["phase"]: row for row in result["phases"]}


def test_two_phase_mixture_recovers_the_weight_fractions(mixture):
    structures, observed, reflections = mixture
    result = rir_quantification(structures, observed, reflections, wavelength="Cu", rietveld={"ZnO": 31.0})
    rows = _by_phase(result)
    assert result["complete"] and result["assumptions"]
    for phase, weight in WEIGHTS.items():
        assert rows[phase]["weight_percent_rir"] == pytest.approx(100 * weight, rel=0.03)
        assert rows[phase]["scaled_from_strongest"] is False and rows[phase]["rir_source"] == "calculated from structure"
    assert rows["ZnO"]["line_hkl"] == "1 0 1" and rows["TiO2"]["line_hkl"] == "1 1 0"
    assert rows["ZnO"]["weight_percent_rietveld"] == 31.0 and rows["TiO2"]["weight_percent_rietveld"] is None
    assert set(RIR_COLUMNS) <= set(rows["ZnO"])


def test_overlapped_strongest_line_falls_back_to_the_next_line(mixture):
    structures, observed, reflections = mixture
    strongest = next(row for row in observed if row["phase"] == "ZnO" and row["hkl"] == "1 0 1")
    # another phase's reflection on top of the ZnO 101 peak: that peak is no longer clean
    intruder = {**reflections[0], "phase": "Other", "two_theta_deg": strongest["two_theta_deg"],
                "intensity": strongest["area"]}
    result = rir_quantification(structures, observed, reflections + [intruder], wavelength="Cu")
    rows = _by_phase(result)
    assert rows["ZnO"]["scaled_from_strongest"] is True and rows["ZnO"]["line_hkl"] != "1 0 1"
    assert "not usable" in rows["ZnO"]["note"]
    for phase, weight in WEIGHTS.items():
        assert rows[phase]["weight_percent_rir"] == pytest.approx(100 * weight, rel=0.05)


def test_phase_without_usable_line_is_reported_and_incomplete(mixture):
    structures, observed, reflections = mixture
    weak = [{**row, "fit_note": "too weak", "area": None} if row["phase"] == "TiO2" else row for row in observed]
    result = rir_quantification({**structures, "Missing": "refined structure unavailable"}, weak, reflections,
                                wavelength="Cu")
    rows = _by_phase(result)
    assert result["complete"] is False
    assert rows["TiO2"]["weight_percent_rir"] is None and rows["TiO2"]["note"].startswith("no usable line")
    assert rows["TiO2"]["rir"] is not None  # I/Ic is known, the measured line is not
    assert rows["Missing"]["rir"] is None and rows["Missing"]["note"].startswith("no structure")
    assert rows["ZnO"]["weight_percent_rir"] == 100.0  # normalised among the phases with a result


def test_rir_endpoint_and_csv(loaded, monkeypatch, tmp_path):
    from dara.server import api_router, quick_refine
    from test_peak_list import PHASE

    # the fake (1 1 1) reflection lies at 29.35°: a Si structure with that d spacing
    si = _structure(Path(api_router.__file__).parents[1] / "data" / "standards" / "Si_9013102.cif")
    a = 1.540598 / (2 * np.sin(np.radians(29.35 / 2))) * np.sqrt(3)
    si.scale_lattice(a**3)
    loaded.lst_data.phases_results[PHASE].get_structure = lambda lattice_fallback=None: si
    loaded.get_phase_weights = lambda: {PHASE: 1.0}
    monkeypatch.setattr(quick_refine, "task_cache", lambda uuid: tmp_path)
    body = asyncio.run(api_router.task_rir_quantification(5, None))
    assert body["columns"] == list(RIR_COLUMNS) and body["selection"] == "best" and body["wavelength"] == "Cu"
    (row,) = body["phases"]
    assert row["phase"] == PHASE and row["line_hkl"] == "1 1 1" and row["scaled_from_strongest"] is False
    assert row["rir"] > 0 and row["weight_percent_rir"] == 100.0 and row["weight_percent_rietveld"] == 100.0
    assert body["complete"] is True and body["assumptions"]
    assert list((tmp_path / "rir").glob("*.json"))  # cached per fit
    assert asyncio.run(api_router.task_rir_quantification(5, None)) == body
    response = asyncio.run(api_router.task_rir_quantification_csv(5, None))
    rows = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert tuple(rows[0]) == RIR_COLUMNS and len(rows) == 2
    assert 'filename="dara_task_5_a_b_rir.csv"' in response.headers["content-disposition"]

