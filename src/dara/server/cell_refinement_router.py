"""Unit-cell refinement from indexed peak positions and 2θ correction with an internal standard
(``dara.cell_refinement``) of one fit of a task."""

from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from dara import cell_refinement as cr
from dara.cif_names import cif_stem
from dara.server import api_router
from dara.server.utils import download_name

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

POSITION_CHECK_DEG = 0.01  # the Rietveld cell must reproduce the listed reflection positions
# Unit weights by default: the profile-fit position e.s.d. hold counting statistics only, and on
# real patterns the scatter is dominated by systematic profile errors (asymmetry), so 1/σ² weights
# let a few strong peaks dominate. weights=esd uses them.
_WEIGHTS = Query("unit", pattern="^(unit|esd)$", description="unit (default) or esd: 1/σ² from the profile fits")


def _cells(refinement, meta) -> dict:
    """``{phase: {a, b, c, alpha, beta, gamma}}`` of the refined cells (refinement setting)."""
    lattices = api_router._lattice_by_phase(refinement, meta.get("cifs"), meta)
    return {name: dict(zip(cr.KEYS, (*lattice.abc, *lattice.angles))) for name, lattice in lattices.items()
            if lattice is not None}


def _rietveld_shift(refinement, document) -> dict:
    """Zero (BGMN EPS1: Δ2θ = −2·EPS1 rad) and specimen displacement (EPS2 = s/R) of the fit."""
    from dara.server.confidence import _value_esd, sample_displacement

    lst = refinement.lst_data
    profile = (document.search_kwargs or {}).get("instrument_profile")
    eps1 = getattr(lst, "EPS1", None)
    if eps1 is None:
        eps1 = (getattr(lst, "model_extra", None) or {}).get("EPS1")
    eps1, eps1_esd = _value_esd(eps1)
    shift = sample_displacement(lst, profile)
    return {
        "eps1": eps1 or 0.0, "eps2": shift["eps2"] or 0.0,
        "zero_deg": {"value": -np.degrees(2 * (eps1 or 0.0)) + 0.0, "esd": abs(np.degrees(2 * eps1_esd)) if eps1_esd else None},
        "displacement_mm": None if shift["displacement_mm"] is None else
        {"value": shift["displacement_mm"], "esd": shift["displacement_mm_esd"]},
        "radius_mm": shift["radius_mm"],
    }


def _position_mismatch(reflections, cell, wavelength, shift) -> float:
    """Largest |2θ listed − 2θ from ``cell`` with the fit's EPS1/EPS2| of a phase's reflections."""
    from dara.utils import angular_correction

    hkls = [(r["h"], r["k"], r["l"]) for r in reflections]
    if not hkls:
        return 0.0
    tt = cr.bragg_two_theta(hkls, cell, wavelength)
    tt = tt + angular_correction(tt, shift["eps1"], shift["eps2"])
    return float(np.nanmax(np.abs(tt - np.array([r["two_theta_deg"] for r in reflections]))))


async def _fit_inputs(task_id: int, sel):
    task, reflections, wavelength, selection, document, refinement, meta = await api_router._peak_rows_of(task_id, sel)
    rows = await api_router._observed_rows(task, document, refinement, reflections, wavelength)
    from dara.export.peak_list import wavelength_angstrom

    lam = wavelength_angstrom(wavelength)
    if not lam:
        raise HTTPException(status_code=400, detail=f"Unknown wavelength {wavelength!r}")
    return task, selection, document, refinement, meta, reflections, rows, lam, wavelength


def _standard_cell(name, refinement, meta, cell) -> tuple[dict, str]:
    """True cell of standard phase ``name`` in its refinement setting and where it comes from:
    the NIST certified cell for Si / LaB6, the bundled reference structure for Al2O3, else the
    phase's database reference CIF (never the cell refined from these very peaks)."""
    from pymatgen.core import Lattice

    from dara.export.refined_cif import phase_structure
    from dara.instrument_profiles import STANDARDS, standard_cif
    from dara.setting_cell import setting_transforms

    reference = next((cif for cif in meta.get("cifs") or [] if cif_stem(cif) == name), None)
    formula = phase_structure(name, refinement, reference, (meta.get("phases") or {}).get(name))[0] \
        .composition.reduced_formula
    if formula in cr.CERTIFIED_CELLS:
        values, system, source = cr.CERTIFIED_CELLS[formula]
        free, ties = cr._system(system, cell)
        true = cr.full_cell(values, ties, {**cell, **values})
    elif formula in STANDARDS:
        lattice = Lattice.from_parameters(*_cif_cell(standard_cif(formula)))
        true = dict(zip(cr.KEYS, (*lattice.abc, *lattice.angles)))
        source = f"bundled reference structure {STANDARDS[formula]} (not a certified value)"
    elif reference is not None:
        lattice = reference.to_structure().lattice
        true = dict(zip(cr.KEYS, (*lattice.abc, *lattice.angles)))
        source = f"database reference CIF of {name} (not a certified value)"
    else:
        raise HTTPException(status_code=400, detail=f"No certified or reference cell for {name}")
    transform = setting_transforms(refinement).get(name)
    if transform is not None:  # database setting -> refinement setting
        lattice = Lattice(np.linalg.inv(transform) @ Lattice.from_parameters(*(true[k] for k in cr.KEYS)).matrix)
        true = dict(zip(cr.KEYS, (*lattice.abc, *lattice.angles)))
        source += ", transformed to the refinement setting"
    return true, source


def _cif_cell(path):
    from dara.cif import Cif

    lattice = Cif.from_file(path).to_structure().lattice
    return (*lattice.abc, *lattice.angles)


def _default_standard(task, refinement, meta) -> str:
    """Fitted phase of the task's internal standard; 400 when none is set or found."""
    from pymatgen.core import Composition

    from dara.export.refined_cif import phase_structure

    spec = api_router._internal_standard_spec(task)
    if not spec:
        raise HTTPException(status_code=400, detail="No internal standard is set for this task: give standard=<phase name>.")
    if spec.get("phase") in refinement.lst_data.phases_results:
        return spec["phase"]
    target = Composition(spec["formula"]).reduced_formula
    for name in refinement.lst_data.phases_results:
        reference = next((cif for cif in meta.get("cifs") or [] if cif_stem(cif) == name), None)
        try:
            if phase_structure(name, refinement, reference)[0].composition.reduced_formula == target:
                return name
        except Exception:  # noqa: BLE001 - a phase without a structure is not the standard
            continue
    raise HTTPException(status_code=400, detail=f"The internal standard {spec['formula']} is not among the fitted phases: give standard=<phase name>.")


def _sigmas(picked, weights):
    return [p["sigma"] for p in picked] if weights == "esd" else None


def _correction(name, refinement, meta, reflections, rows, lam, shift, cells, weights="unit") -> dict:
    if name not in refinement.lst_data.phases_results:
        raise HTTPException(status_code=404, detail=f"Phase {name} is not in this fit")
    true, source = _standard_cell(name, refinement, meta, cells[name])
    picked = [p for p in cr.select_peaks(rows, reflections, name) if p["used"]]
    try:
        model = cr.fit_two_theta_correction(
            [p["hkl"] for p in picked], [p["two_theta_obs"] for p in picked], _sigmas(picked, weights),
            lam, true, radius_mm=shift["radius_mm"])
    except cr.CellRefinementError as exc:
        raise HTTPException(status_code=400, detail=f"Standard {name}: {exc}") from exc
    for peak, item in zip(model["peaks"], picked):
        peak["hkl"] = " ".join(map(str, peak["hkl"]))
        peak["hkl_database_setting"] = item["hkl_database_setting"]
    displacement = model["displacement"]
    return {
        "standard": name, "phase_label": _label(name),
        "certified_cell": {k: true[k] for k in cr.KEYS}, "certified_cell_source": source,
        "model": {"zero_deg": model["zero_deg"],
                  "displacement_mm": displacement if displacement and displacement["unit"] == "mm" else None,
                  "displacement_s_over_r": displacement if displacement and displacement["unit"] == "s/R" else None},
        "peaks": model["peaks"], "rms_deg": model["rms_deg"], "dof": model["dof"],
        "_model": model, "_radius": shift["radius_mm"],
        "note": None if displacement else "Zero shift only: specimen displacement needs at least 4 standard peaks "
                                          "spanning at least 40° 2θ.",
    }


def _label(name):
    from dara.export.peak_list import _label as label

    return label(name)


def _refine_phase(name, reflections, rows, lam, cells, refinement, shift, zero, displacement, correction, weights):
    from dara.server.api_router import _phase_refinement_details

    mine = [r for r in reflections if r["phase"] == name]
    entry = {"phase": name, "phase_label": _label(name), "crystal_system": None, "parameters": None,
             "volume": None, "zero_deg": None, "displacement_mm": None, "peaks": [], "rms_deg": None,
             "peaks_used": 0, "dof": None, "rietveld": None, "note": None}
    cell = cells.get(name)
    if cell is None:
        entry["note"] = "No refined cell for this phase"
        return entry
    if getattr(refinement, "identification", False):
        # no Rietveld fit to compare with; the reflections sit at the search-match's own shift of this phase
        shift = {**shift, "eps1": 0.0, "eps2": refinement.lst_data.phases_results[name].eps2}
    else:
        try:
            lattice = _phase_refinement_details(refinement).get(name, {}).get("lattice_parameters") or {}
        except Exception:  # noqa: BLE001 - the comparison values are an addition
            lattice = {}
        entry["rietveld"] = {**{k: {"value": v["value"], "esd": v["esd"]} for k, v in lattice.items()},
                             "volume": cr.volume(cell)}
    system = cr.crystal_system(getattr(refinement.lst_data.phases_results[name], "spacegroup_no", None), cell)
    entry["crystal_system"] = system
    mismatch = _position_mismatch(mine, cell, lam, shift)
    if mismatch > POSITION_CHECK_DEG:
        entry["note"] = (f"The Rietveld cell does not reproduce this phase's reflection positions "
                         f"(up to {mismatch:.3f}°); its hkl do not refer to that cell.")
        return entry
    picked = cr.select_peaks(rows, reflections, name)
    fitted = [p for p in picked if p["two_theta_obs"] is not None]
    observed = np.array([p["two_theta_obs"] for p in fitted], dtype=float)
    if correction is not None and len(observed):
        observed = cr.correct_two_theta(observed, correction["_model"], correction["_radius"])
    try:
        result = cr.refine_cell(
            [p["hkl"] for p in fitted], observed, _sigmas(fitted, weights), lam, system, cell,
            refine_zero=zero, refine_displacement=displacement, radius_mm=shift["radius_mm"],
            use=[p["used"] for p in fitted])
    except cr.CellRefinementError as exc:
        result, entry["note"] = None, str(exc)
    reasons = {id(p): p["reason"] for p in fitted}
    if result is not None:
        for index in result["excluded"]:
            reasons[id(fitted[index])] = "outlier (|Δ2θ| > 3 × r.m.s. and > 0.05°)"
        moved = result["displacement"]
        entry.update(
            parameters=result["parameters"], volume=result["volume"], zero_deg=result["zero_deg"],
            displacement_mm=moved if moved and moved["unit"] == "mm" else None,
            rms_deg=result["rms_deg"], peaks_used=result["peaks_used"], dof=result["dof"],
        )
        r = result["correlation_zero_displacement"]
        if r is not None and abs(r) > 0.95:
            entry["note"] = (f"Zero shift and displacement are strongly correlated over these peaks (r = {r:.3f}); "
                             "refine one of them, or correct with an internal standard.")
        if moved and moved["unit"] == "s/R":
            entry["displacement_s_over_r"] = moved
            entry["note"] = "The goniometer radius is unknown: the displacement is the coefficient s/R."
    calc = {id(p): result["peaks"][i] for i, p in enumerate(fitted)} if result else {}
    for p in picked:
        row = calc.get(id(p)) or {}
        entry["peaks"].append({
            "hkl": " ".join(map(str, p["hkl"])), "hkl_database_setting": p["hkl_database_setting"],
            "two_theta_obs": None if p["two_theta_obs"] is None else float(row.get("two_theta_obs", p["two_theta_obs"])),
            "two_theta_calc": row.get("two_theta_calc"), "delta": row.get("delta"),
            "used": bool(row.get("used")), "reason": reasons.get(id(p), p["reason"]),
        })
    return entry


async def _cell_refinement(task_id, sel, phase, zero, displacement, correct_with, weights="unit"):
    task, selection, document, refinement, meta, reflections, rows, lam, wavelength = await _fit_inputs(task_id, sel)
    names = list(refinement.lst_data.phases_results)
    if phase and phase not in names:
        raise HTTPException(status_code=404, detail=f"Phase {phase} is not in this fit")
    shift = _rietveld_shift(refinement, document)
    cells = _cells(refinement, meta)
    correction = None
    if correct_with:
        correction = _correction(correct_with, refinement, meta, reflections, rows, lam, shift, cells, weights)
        zero = displacement = False  # already removed with the standard's correction
        names = [name for name in names if name != correct_with]
    phases = [
        _refine_phase(name, reflections, rows, lam, cells, refinement, shift, zero, displacement, correction, weights)
        for name in ([phase] if phase else names)
    ]
    if correction:
        correction = {k: v for k, v in correction.items() if not k.startswith("_")}
    return task, selection, {
        "task_id": task_id, "selection": selection, "wavelength": wavelength, "wavelength_angstrom": lam,
        "refine_zero": zero, "refine_displacement": displacement, "weights": weights, "correction": correction,
        "rietveld_shift": None if getattr(refinement, "identification", False)
        else {k: shift[k] for k in ("zero_deg", "displacement_mm", "radius_mm")},
        "phases": phases,
    }


def _flag(value) -> bool:
    return value is True or value == 1


def _text(value, default):
    return value if isinstance(value, str) else default  # direct calls leave the Query default objects


@router.get("/task/{task_id}/cell-refinement")
async def task_cell_refinement(
    task_id: int, sel: str | None = Query(None), phase: str | None = Query(None),
    zero: int = Query(0, ge=0, le=1), displacement: int = Query(0, ge=0, le=1),
    correct_with: str | None = Query(None, description="Standard phase whose 2θ correction is applied first"),
    weights: str = _WEIGHTS,
):
    """Lattice parameters ± e.s.d. of each phase (or ``phase``) of ``sel`` refined by least squares
    on the Kα1 positions of its assigned observed peaks, beside the Rietveld cell."""
    _, _, body = await _cell_refinement(task_id, sel, phase, _flag(zero), _flag(displacement), correct_with,
                                        _text(weights, "unit"))
    return body


CSV_COLUMNS = ("phase", "phase_label", "item", "hkl", "value", "esd", "rietveld_value", "rietveld_esd",
               "two_theta_obs", "two_theta_calc", "delta", "used", "note")


@router.get("/task/{task_id}/cell-refinement.csv")
async def task_cell_refinement_csv(
    task_id: int, sel: str | None = Query(None), phase: str | None = Query(None),
    zero: int = Query(0, ge=0, le=1), displacement: int = Query(0, ge=0, le=1),
    correct_with: str | None = Query(None), weights: str = _WEIGHTS,
):
    from dara.export.peak_list import peak_csv_bytes

    task, selection, body = await _cell_refinement(task_id, sel, phase, _flag(zero), _flag(displacement), correct_with,
                                                   _text(weights, "unit"))
    rows = []
    for entry in body["phases"]:
        base = {"phase": entry["phase"], "phase_label": entry["phase_label"]}
        rietveld = entry["rietveld"] or {}
        for key, item in (entry["parameters"] or {}).items():
            rows.append({**base, "item": key, "value": item["value"], "esd": item["esd"],
                         "rietveld_value": (rietveld.get(key) or {}).get("value"),
                         "rietveld_esd": (rietveld.get(key) or {}).get("esd")})
        for key in ("volume", "zero_deg", "displacement_mm", "displacement_s_over_r"):
            if entry.get(key):
                rows.append({**base, "item": key, "value": entry[key]["value"], "esd": entry[key]["esd"],
                             "rietveld_value": rietveld.get("volume") if key == "volume" else None})
        if entry["rms_deg"] is not None:
            rows.append({**base, "item": "rms_delta_two_theta_deg", "value": entry["rms_deg"]})
        if entry["note"]:
            rows.append({**base, "item": "note", "note": entry["note"]})
        for peak in entry["peaks"]:
            rows.append({**base, "item": "peak", "hkl": peak["hkl"], "two_theta_obs": peak["two_theta_obs"],
                         "two_theta_calc": peak["two_theta_calc"], "delta": peak["delta"],
                         "used": peak["used"], "note": peak["reason"]})
    filename = download_name(task_id, task["job"]["name"], "cell_refinement", selection, "csv")
    return api_router._csv_response(peak_csv_bytes(rows, CSV_COLUMNS), filename)


@router.get("/task/{task_id}/two-theta-correction")
async def task_two_theta_correction(task_id: int, sel: str | None = Query(None), standard: str | None = Query(None),
                                    weights: str = _WEIGHTS):
    """Zero shift (+ specimen displacement with ≥ 4 peaks over ≥ 40°) fitted to the observed peaks
    of the standard phase at the positions of its certified/reference cell, and every observed
    peak's corrected 2θ."""
    task, selection, document, refinement, meta, reflections, rows, lam, wavelength = await _fit_inputs(task_id, sel)
    standard = _text(standard, None) or _default_standard(task, refinement, meta)
    shift = _rietveld_shift(refinement, document)
    body = _correction(standard, refinement, meta, reflections, rows, lam, shift, _cells(refinement, meta),
                       _text(weights, "unit"))
    positions = np.array([row["two_theta_fit"] if row.get("two_theta_fit") is not None else row["two_theta_deg"]
                          for row in rows], dtype=float)
    corrected = cr.correct_two_theta(positions, body.pop("_model"), body.pop("_radius")) if len(positions) else []
    body["corrected"] = [{"two_theta_obs": float(a), "two_theta_corrected": float(b), "phase": row.get("phase")}
                         for a, b, row in zip(positions, corrected, rows)]
    return {"task_id": task_id, "selection": selection, "wavelength": wavelength, **body}
