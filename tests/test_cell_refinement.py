"""Unit-cell refinement from indexed peak positions and 2θ correction with an internal standard."""

import itertools

import numpy as np
import pytest

from dara.cell_refinement import (
    CellRefinementError, bragg_two_theta, correct_two_theta, displacement_shift, fit_two_theta_correction,
    inverse_d_squared, refine_cell, select_peaks, volume,
)

LAM = 1.540598
RADIUS = 240.0


def _cell(a, b=None, c=None, alpha=90.0, beta=90.0, gamma=90.0):
    return {"a": a, "b": b or a, "c": c or a, "alpha": alpha, "beta": beta, "gamma": gamma}


def _peaks(cell, max_index=3, low=15.0, high=120.0, zero=0.0, shift=0.0, noise=0.002, seed=0, limit=None):
    """Distinct-d reflections of ``cell`` with observed 2θ = true + zero + displacement + noise."""
    seen, hkls = [], []
    for hkl in itertools.product(range(-max_index, max_index + 1), repeat=3):
        if hkl == (0, 0, 0):
            continue
        tt = float(bragg_two_theta([hkl], cell, LAM)[0])
        if not low <= tt <= high or any(abs(tt - other) < 0.3 for other in seen):
            continue
        seen.append(tt)
        hkls.append(hkl)
    hkls = hkls[:limit] if limit else hkls
    true = bragg_two_theta(hkls, cell, LAM)
    rng = np.random.default_rng(seed)
    observed = true + zero + displacement_shift(true, shift, RADIUS) + rng.normal(0, noise, len(true))
    return np.array(hkls), observed


CASES = [
    ("cubic", _cell(8.0837)),
    ("tetragonal", _cell(4.594, c=2.959)),
    ("hexagonal", _cell(3.2498, c=5.2066, gamma=120.0)),
    ("orthorhombic", _cell(5.01, b=6.12, c=7.35)),
    ("monoclinic", _cell(5.10, b=4.80, c=6.30, beta=101.5)),
]


@pytest.mark.parametrize("system,cell", CASES)
def test_parameters_recovered_within_three_esd(system, cell):
    hkls, observed = _peaks(cell)
    start = {k: v * 1.002 if k in "abc" else v for k, v in cell.items()}
    result = refine_cell(hkls, observed, None, LAM, system, start)
    for key, item in result["parameters"].items():
        assert abs(item["value"] - cell[key]) < 3 * item["esd"] + 1e-6, (key, item)
        assert 0 < item["esd"] < 0.005  # 0.002° noise: e.s.d. of order 1e-4 Å
    assert abs(result["volume"]["value"] - volume(cell)) < 3 * result["volume"]["esd"] + 1e-6
    assert result["rms_deg"] == pytest.approx(0.002, rel=0.5)
    assert result["dof"] == result["peaks_used"] - len(result["parameters"])


def test_zero_shift_and_displacement_recovered():
    cell = _cell(8.0837)
    hkls, observed = _peaks(cell, zero=0.03, shift=-0.3, noise=0.001)
    # unaccounted, the shifts bias the cell
    biased = refine_cell(hkls, observed, None, LAM, "cubic", cell)
    assert abs(biased["parameters"]["a"]["value"] - 8.0837) > 0.005
    result = refine_cell(hkls, observed, None, LAM, "cubic", cell, refine_zero=True, refine_displacement=True,
                         radius_mm=RADIUS)
    a, zero, shift = result["parameters"]["a"], result["zero_deg"], result["displacement"]
    assert abs(a["value"] - 8.0837) < 3 * a["esd"]
    assert abs(zero["value"] - 0.03) < 3 * zero["esd"] and abs(shift["value"] + 0.3) < 3 * shift["esd"]
    assert shift["unit"] == "mm"
    # without a radius the lumped coefficient s/R is refined
    lumped = refine_cell(hkls, observed, None, LAM, "cubic", cell, refine_displacement=True, refine_zero=True)
    assert lumped["displacement"]["unit"] == "s/R"
    assert lumped["displacement"]["value"] * RADIUS == pytest.approx(shift["value"], rel=1e-4)
    assert lumped["displacement"]["esd"] * RADIUS == pytest.approx(shift["esd"], rel=1e-3)


def test_too_few_peaks_and_singular_cases_are_refused():
    cell = _cell(3.2498, c=5.2066, gamma=120.0)
    hkls, observed = _peaks(cell, limit=3)
    with pytest.raises(CellRefinementError, match="at least 4"):
        refine_cell(hkls, observed, None, LAM, "hexagonal", cell)
    hk0 = np.array([(1, 0, 0), (1, 1, 0), (2, 0, 0), (2, 1, 0), (3, 0, 0), (2, 2, 0)])
    with pytest.raises(CellRefinementError, match="do not determine c"):
        refine_cell(hk0, bragg_two_theta(hk0, cell, LAM), None, LAM, "hexagonal", cell)


def test_one_outlier_pass():
    cell = _cell(4.1768)
    hkls, observed = _peaks(cell, noise=0.002)
    observed[2] += 0.2
    result = refine_cell(hkls, observed, None, LAM, "cubic", cell)
    assert result["excluded"] == [2] and not result["peaks"][2]["used"]
    assert result["peaks_used"] == len(observed) - 1
    assert abs(result["parameters"]["a"]["value"] - 4.1768) < 3 * result["parameters"]["a"]["esd"]
    assert result["peaks"][2]["delta"] == pytest.approx(0.2, abs=0.01)


def test_internal_standard_correction_then_sample_cell():
    zero, shift = -0.04, 0.25
    silicon = _cell(5.431144)
    si_hkls, si_obs = _peaks(silicon, zero=zero, shift=shift, noise=0.001, seed=1)
    model = fit_two_theta_correction(si_hkls, si_obs, None, LAM, silicon, radius_mm=RADIUS)
    assert abs(model["zero_deg"]["value"] - zero) < 3 * model["zero_deg"]["esd"]
    assert abs(model["displacement"]["value"] - shift) < 3 * model["displacement"]["esd"]
    sample = _cell(3.2498, c=5.2066, gamma=120.0)
    hkls, observed = _peaks(sample, zero=zero, shift=shift, noise=0.001, seed=2)
    corrected = correct_two_theta(observed, model, RADIUS)
    result = refine_cell(hkls, corrected, None, LAM, "hexagonal", {**sample, "a": 3.25, "c": 5.2})
    for key in ("a", "c"):
        assert abs(result["parameters"][key]["value"] - sample[key]) < 3 * result["parameters"][key]["esd"] + 2e-5


def test_correction_needs_two_peaks_and_spans_for_displacement():
    silicon = _cell(5.431144)
    hkls, observed = _peaks(silicon, zero=0.02, limit=2, noise=0.0)
    model = fit_two_theta_correction(hkls, observed, None, LAM, silicon)
    assert model["displacement"] is None and model["zero_deg"]["value"] == pytest.approx(0.02, abs=1e-6)
    with pytest.raises(CellRefinementError, match="at least 2"):
        fit_two_theta_correction(hkls[:1], observed[:1], None, LAM, silicon)


def test_peak_selection():
    reflections = [
        {"phase": "A", "phase_label": "A", "h": 1, "k": 1, "l": 1, "two_theta_deg": 30.0, "d_angstrom": 2.976},
        {"phase": "A", "phase_label": "A", "h": 2, "k": 0, "l": 0, "two_theta_deg": 40.0, "d_angstrom": 2.252},
        {"phase": "A", "phase_label": "A", "h": 2, "k": 1, "l": 0, "two_theta_deg": 40.01, "d_angstrom": 2.2515},
        {"phase": "B", "phase_label": "B", "h": 1, "k": 0, "l": 0, "two_theta_deg": 50.03, "d_angstrom": 1.82},
        {"phase": "A", "phase_label": "A", "h": 2, "k": 2, "l": 0, "two_theta_deg": 50.0, "d_angstrom": 1.8226},
        {"phase": "A", "phase_label": "A", "h": 3, "k": 1, "l": 1, "two_theta_deg": 60.0, "d_angstrom": 1.5406},
    ]

    def row(tt, fit, note=None):
        return {"two_theta_deg": tt, "two_theta_fit": fit, "two_theta_esd_deg": 0.001, "fwhm_deg": 0.1,
                "fit_note": note, "phase": "A"}

    rows = [row(30.01, 30.005), row(40.0, 40.002), row(50.0, 50.01), row(60.0, 60.0, "too weak"),
            row(30.05, 30.06)]
    picked = select_peaks(rows, reflections, "A")
    assert [p["used"] for p in picked] == [True, False, False, False, False]
    assert "different d" in picked[1]["reason"] and "overlaps B" in picked[2]["reason"]
    assert picked[3]["reason"] == "profile fit: too weak" and "closer" in picked[4]["reason"]
    assert picked[0]["hkl"] == [1, 1, 1] and picked[0]["two_theta_obs"] == 30.005


# ---- endpoints ---------------------------------------------------------------------------

SAMPLE, STANDARD = "ZnO_186_(cod_1)", "Si_227_(cod_2)"


@pytest.fixture
def task(monkeypatch):
    """A fit with a hexagonal sample and Si: Rietveld-listed reflections carry the fit's EPS2
    shift; the observed Kα1 positions carry a zero shift and a displacement."""
    from types import SimpleNamespace

    from dara.export import refined_cif
    from dara.server import api_router, confidence
    from pymatgen.core import Lattice, Structure

    zero, shift_mm = -0.04, 0.25
    cells = {SAMPLE: _cell(3.2498, c=5.2066, gamma=120.0), STANDARD: _cell(5.431144)}
    eps2 = shift_mm / RADIUS
    reflections, rows = [], []
    for seed, (name, cell) in enumerate(cells.items()):
        hkls, observed = _peaks(cell, zero=zero, shift=shift_mm, noise=0.001, seed=seed, high=110.0)
        listed = bragg_two_theta(hkls, cell, LAM) + displacement_shift(bragg_two_theta(hkls, cell, LAM), eps2, None)
        for hkl, tt, obs in zip(hkls, listed, observed):
            d = 1 / np.sqrt(inverse_d_squared([hkl], cell)[0])
            reflections.append({"phase": name, "phase_label": name.split("_")[0], "h": int(hkl[0]), "k": int(hkl[1]),
                                "l": int(hkl[2]), "two_theta_deg": float(tt), "d_angstrom": float(d),
                                "hkl_database_setting": None})
            rows.append({"two_theta_deg": float(obs), "two_theta_fit": float(obs), "two_theta_esd_deg": 0.001,
                         "fwhm_deg": 0.1, "fit_note": None, "phase": name})
    numbers = {SAMPLE: 186, STANDARD: 227}
    lst = SimpleNamespace(phases_results={n: SimpleNamespace(spacegroup_no=numbers[n]) for n in cells}, EPS2=eps2)
    refinement = SimpleNamespace(lst_data=lst)
    document = SimpleNamespace(search_kwargs={"wavelength": "Cu", "instrument_profile": "test"})
    record = {"index": 7, "uuid": "u", "job": {"name": "x.xy"}, "internal_standard": {"formula": "Si", "wt_percent": 10}}

    async def peak_rows_of(task_id, sel):
        return record, reflections, "Cu", "best", document, refinement, {"cifs": [], "phases": {}}

    async def observed_rows(*args, **kwargs):
        return rows

    lattice = {n: Lattice.from_parameters(*(c[k] for k in ("a", "b", "c", "alpha", "beta", "gamma"))) for n, c in cells.items()}
    species = {SAMPLE: ["Zn", "O"], STANDARD: ["Si", "Si"]}
    monkeypatch.setattr(api_router, "_peak_rows_of", peak_rows_of)
    monkeypatch.setattr(api_router, "_observed_rows", observed_rows)
    monkeypatch.setattr(api_router, "_lattice_by_phase", lambda refinement, cifs, meta=None: lattice)
    monkeypatch.setattr(api_router, "_phase_refinement_details", lambda refinement: {})
    monkeypatch.setattr(confidence, "instrument_radius_mm", lambda profile: RADIUS)
    monkeypatch.setattr(refined_cif, "phase_structure", lambda name, *args: (
        Structure(lattice[name], species[name], [[0, 0, 0], [0.25, 0.25, 0.25]]), False))
    return cells, zero, shift_mm


def test_cell_refinement_endpoint(task):
    import asyncio

    from dara.server import cell_refinement_router as router

    cells, zero, shift_mm = task
    body = asyncio.run(router.task_cell_refinement(7, None, SAMPLE, 1, 0, None, "unit"))
    (entry,) = body["phases"]
    assert entry["crystal_system"] == "hexagonal" and entry["zero_deg"] is not None
    assert body["rietveld_shift"]["displacement_mm"]["value"] == pytest.approx(shift_mm)
    # corrected with the Si standard (certified SRM 640f cell): sample cell recovered
    body = asyncio.run(router.task_cell_refinement(7, None, None, 0, 0, STANDARD, "unit"))
    assert [e["phase"] for e in body["phases"]] == [SAMPLE]
    assert body["correction"]["certified_cell_source"].startswith("NIST SRM 640f")
    params = body["phases"][0]["parameters"]
    for key in ("a", "c"):
        assert abs(params[key]["value"] - cells[SAMPLE][key]) < 3 * params[key]["esd"] + 2e-5
    response = asyncio.run(router.task_cell_refinement_csv(7, None, None, 0, 1, None, "esd"))
    assert response.body.decode("utf-8-sig").splitlines()[0].startswith("phase,phase_label,item,hkl,value,esd")


def test_two_theta_correction_endpoint(task):
    import asyncio

    from fastapi import HTTPException

    from dara.server import cell_refinement_router as router

    cells, zero, shift_mm = task
    body = asyncio.run(router.task_two_theta_correction(7, None, None, "unit"))  # the task's internal standard
    model = body["model"]
    assert body["standard"] == STANDARD
    assert abs(model["zero_deg"]["value"] - zero) < 3 * model["zero_deg"]["esd"] + 1e-4
    assert abs(model["displacement_mm"]["value"] - shift_mm) < 3 * model["displacement_mm"]["esd"] + 1e-3
    assert len(body["corrected"]) == len(body["peaks"]) + len([p for p in body["corrected"] if p["phase"] == SAMPLE])
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.task_two_theta_correction(7, None, "nope", "unit"))
    assert error.value.status_code == 404
