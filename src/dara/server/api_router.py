from __future__ import annotations

import asyncio
import csv
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from ast import literal_eval
from collections import OrderedDict
from dataclasses import dataclass, field
from copy import deepcopy
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
from traceback import print_exc
from typing import Annotated, Any, Optional

import numpy as np
from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from monty.serialization import MontyDecoder
from pydantic import BaseModel
from pymatgen.core import Composition, Element

from dara import known_phases, structure_db
from dara.cif import Cif
from dara.cif_names import cif_stem, strip_none
from dara.analysis_range import validate_analysis_range
from dara.jobs import PhaseSearchMaker
from dara.plot import peaks_in_fit_range, spacegroup_symbol, visualize
from dara.server.data_quality import intensities_look_like_counts, pattern_quality
from dara.server.display_formula import document_display_formulas, task_display_formulas
from dara.server.failure_summary import summarize_failure
from dara.server import confidence as confidence_module
from dara.server import peak_significance
from dara.server.fit_cautions import (
    broad_background_caution,
    cached_lattice_flags,
    displacement_caution,
    fit_cautions,
    lattice_bound_flags,
    lattice_range_resolver,
    search_cautions,
)
from dara.server import mass_balance as mass_balance_module
from dara.server.mass_balance import split_precursor_entries
from dara.server.table_export import (
    MULTI_TASK_EXTRA_COLUMNS,
    TABLE_COLUMNS,
    multi_task_rows,
    rows_to_csv,
    task_table_rows,
)
from dara.server.task_summary import (
    SUMMARY_SCHEMA,
    compact_summary,
    SUMMARY_REFRESH_GAVE_UP,
    refresh_stale_summary,
    runtime_seconds,
)
from dara.refinement_status import lattice_states, phase_status, size_strain
from dara.result import parse_lst_section
from dara.server.utils import (
    convert_to_local_tz,
    download_name,
    get_result_store,
    get_worker_store,
    not_completed_detail,
    safe_download_stem,
)
from dara.server.worker import (
    add_job_to_queue,
)
from dara.structure_db import CODDatabase, MaterialsProjectDatabase
from dara.utils import (
    get_compositional_clusters,
    get_head_of_compositional_cluster,
    parse_refinement_param,
)
from dara.xrd import (
    SUPPORTED_PATTERN_EXTENSIONS,
    get_xrdml_metadata,
    load_pattern,
    recommend_xrdml_instrument_profile,
)

logger = logging.getLogger("dara.server.api")

router = APIRouter(prefix="/api")


def _available_instrument_profiles() -> list[str]:
    device_dir = Path(__file__).parents[1] / "data" / "BGMN-Templates" / "Devices"
    return sorted(path.stem for path in device_dir.glob("*.geq"))


@lru_cache(maxsize=256)
def _spacegroup_symbol(number) -> str:
    """Hermann-Mauguin symbol of a space-group number (building the group is slow)."""
    return spacegroup_symbol(number)


def _refined_phase_metadata(refinement) -> dict[str, dict]:
    """Return user-facing crystallographic labels for refined phases."""
    metadata = {}
    for phase_name, phase_result in refinement.lst_data.phases_results.items():
        spacegroup_number = phase_result.spacegroup_no
        try:
            spacegroup_symbol = _spacegroup_symbol(spacegroup_number)
        except (TypeError, ValueError):
            spacegroup_symbol = phase_result.hermann_mauguin
        metadata[phase_name] = {
            "spacegroup_number": spacegroup_number,
            "spacegroup_symbol": spacegroup_symbol,
        }
        database_number = _database_spacegroup_number(phase_name)
        if database_number is not None:
            try:
                database_symbol = _spacegroup_symbol(database_number)
            except (TypeError, ValueError):
                database_symbol = None
            metadata[phase_name].update(
                database_spacegroup_number=database_number,
                database_spacegroup_symbol=database_symbol,
            )
            if database_symbol and spacegroup_number is not None and database_number != spacegroup_number:
                metadata[phase_name]["setting_note"] = (
                    f"Refined in the {str(spacegroup_symbol).replace('_', '')} subgroup setting of "
                    f"{database_symbol.replace('_', '')} (same structure, lower-symmetry description)."
                )
    return metadata


@lru_cache(maxsize=512)
def _setting_transform_cached(structure_json: str, database_number: int):
    from pymatgen.core import Structure

    from dara.setting_cell import setting_transform

    found = setting_transform(Structure.from_str(structure_json, fmt="json"), database_number)
    return None if found is None else tuple(map(tuple, found.tolist()))


def _database_setting_lattice(name, phase, lattice: dict) -> dict:
    """``{"database_lattice": ...}`` for a phase refined in a subgroup setting (Fe2O3 R-3c as
    C2/c): its cell in the database setting, so it can be checked against cards. Empty when
    the phase is refined in its database group or the mapping is not found."""
    database_number = _database_spacegroup_number(name)
    if database_number is None or getattr(phase, "spacegroup_no", None) in (None, database_number):
        return {}
    try:
        from dara.setting_cell import transform_cell

        structure = phase.get_structure()
        transform = _setting_transform_cached(structure.to_json(), database_number)
        if transform is None:
            return {}
        # BGMN lists only the free parameters; symmetry-fixed ones come from the refined cell.
        full = dict(zip(("a", "b", "c", "alpha", "beta", "gamma"),
                        ({"value": value, "esd": None} for value in (*structure.lattice.abc, *structure.lattice.angles))))
        for key in ("b", "c"):  # an axis equal to a by symmetry moves with a
            if key not in lattice and "a" in lattice and abs(full[key]["value"] - lattice["a"]["value"]) < 1e-4:
                full[key]["same_as"] = "a"
        cell = transform_cell({**full, **lattice}, np.array(transform))
        return {"database_lattice": {**cell, "spacegroup_number": database_number,
                                     "transform": [list(row) for row in transform]}} if cell else {}
    except Exception:  # noqa: BLE001 - the refined-setting cell is still reported
        logger.debug("Database-setting cell of %s unavailable", name, exc_info=True)
        return {}


def _database_spacegroup_number(phase_name) -> int | None:
    """Space-group number of the database record: second ``_`` token of ``Fe2O3_167_(cod_…)``."""
    tokens = strip_none(str(phase_name)).split("_")
    return int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else None


def _value_and_esd(value) -> tuple[float | None, float | None]:
    """Return a BGMN value and its estimated standard deviation, when present."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return float(value[0]), float(value[1])
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value), None
    return None, None


def _fit_quality(refinement) -> dict:
    """Expose refinement statistics without inventing unavailable metrics."""
    fit = refinement.lst_data
    rexp = fit.rexp
    gof = fit.rwp / rexp if rexp else None
    angles = np.asarray(refinement.plot_data.x, dtype=float)
    angles = angles[np.isfinite(angles)]
    return {
        "rp": fit.rp,
        "rpb": fit.rpb,
        "r": fit.r,
        "rwp": fit.rwp,
        "rexp": rexp,
        "gof": gof,
        "chi_squared": gof**2 if gof is not None else None,
        "durbin_watson": fit.d,
        "one_minus_rho": fit.rho,
        "iterations": fit.num_steps,
        "sample_displacement_eps2": _value_and_esd(getattr(fit, "EPS2", None))[0],
        "two_theta_min": float(angles.min()) if angles.size else None,
        "two_theta_max": float(angles.max()) if angles.size else None,
        "data_points": int(angles.size),
    }


def _quantitative_phase_details(refinement) -> dict[str, dict]:
    """Return normalized phase fractions and propagated BGMN e.s.d. values."""
    raw = {
        name: _value_and_esd(phase.gewicht)
        for name, phase in refinement.lst_data.phases_results.items()
    }
    total = sum(value for value, _ in raw.values() if value is not None)
    if not total:
        return {}

    all_have_esd = all(error is not None for _, error in raw.values())
    details = {}
    for name, (value, error) in raw.items():
        if value is None:
            continue
        normalized_error = None
        if all_have_esd:
            variance = (((total - value) / total**2) * error) ** 2
            variance += sum(
                ((value / total**2) * other_error) ** 2
                for other_name, (_, other_error) in raw.items()
                if other_name != name
            )
            normalized_error = variance**0.5 * 100
        details[name] = {
            "weight_percent": value / total * 100,
            "weight_esd_percent": normalized_error,
        }
    return details


def _b1_window(spec) -> tuple[bool, float | None]:
    """``(fixed, upper)`` of a ``b1`` phase-parameter spec; ``(False, None)`` when unknown."""
    if spec is None:
        return False, None
    if spec == "fixed":
        return True, None
    try:
        _, lower, upper = parse_refinement_param(spec)
    except (TypeError, ValueError):
        return False, None
    if lower is not None and upper is not None and lower == upper:
        return True, None
    return False, upper


def _close(value: float, target: float | None) -> bool:
    return target is not None and abs(value - target) <= 1e-3 * max(abs(target), 1e-9)


def crystallite_size_status(extra: dict, grain_sizes: list, b1_spec=None) -> str:
    """Why a phase does or does not report a crystallite size.

    ``refined`` (a size is reported), ``not_resolvable`` (B1 refined to 0 / GrainSize
    ERROR: no size broadening beyond the instrument), ``not_refined`` (b1 held fixed,
    instrument-only peak width), ``at_limit`` (B1 at the upper bound of the window it was
    refined in: crystallites too small for the window), ``stale`` (B1 stuck without esd at a
    bound that is not the window's own bound, typically the pre-fix screening window).
    """
    if grain_sizes:
        return "refined"
    fixed, upper = _b1_window(b1_spec)
    if fixed:
        return "not_refined"
    if "B1" not in extra:
        return "not_refined"
    b1, b1_esd = _value_and_esd(extra.get("B1"))
    sizes = [value for key, value in extra.items() if key.lower().startswith("grainsize")]
    if b1 is None or b1 <= 0 or any(value is None for value in sizes):
        return "not_resolvable"
    if b1_esd is None and _close(b1, upper):
        return "at_limit"
    return "stale"


def _phase_spec_resolver(job: dict | None, document):
    """``(phase name, key) -> effective spec`` (``b1``, ``k1``, ``k2``, ``lattice_range``) of the
    final refinement (``None`` when unknown).

    Global ``final_refinement_params``, replaced by ``additional_cif_params`` for uploaded
    CIFs and by explicit ``final_refinement_overrides`` (the ``dara.jobs`` recipe).
    """
    params = getattr(document, "final_refinement_params", None) or {}
    kwargs = ((job or {}).get("job") or {}).get("function_kwargs") or {}
    overrides = kwargs.get("final_refinement_overrides")
    overrides = overrides if isinstance(overrides, dict) else {}
    uploaded_params = kwargs.get("additional_cif_params")
    uploaded_params = uploaded_params if isinstance(uploaded_params, dict) else {}
    memo: dict[str, set[str]] = {}

    def uploaded_names() -> set[str]:
        # decoding the uploaded CIFs is only worth it when a key is not overridden
        if "names" not in memo:
            try:
                from dara.final_refinement_spec import job_parity_inputs

                memo["names"] = job_parity_inputs(kwargs)["additional_names"]
            except Exception:  # noqa: BLE001 - the status is cosmetic; never break the payload
                memo["names"] = set()
        return memo["names"]

    def resolve(name: str, key: str):
        if key in overrides:
            return overrides[key]
        if key in uploaded_params and str(name) in uploaded_names():
            return uploaded_params[key]
        return params.get(key)

    return resolve


def _b1_spec_resolver(job: dict | None, document):
    """``phase name -> effective b1 spec`` of the final refinement (``None`` when unknown)."""
    resolve = _phase_spec_resolver(job, document)
    return lambda name: resolve(name, "b1")


def _params_spec_resolver(effective_phase_params: dict | None):
    """``(phase name, key) -> spec`` from a stored ``effective_phase_params`` of a version."""
    params = effective_phase_params if isinstance(effective_phase_params, dict) else {}
    return lambda name, key: (params.get(name) or {}).get(key)


def _lst_section_text(refinement, name: str, index: int) -> str | None:
    """Raw ``.lst`` section of phase ``name`` (``None`` when the stored listing lacks it).

    Matched by phase name, else by position when the listing has one section per phase.
    """
    raw = getattr(getattr(refinement, "lst_data", None), "raw_lst", None)
    if not isinstance(raw, str) or not raw:
        return None
    from dara.result import lst_phase_sections_by_name

    try:
        sections = lst_phase_sections_by_name(raw)
        by_name = dict(sections)
        if name in by_name:
            return by_name[name]
        if len(sections) == len(refinement.lst_data.phases_results) and index < len(sections):
            return sections[index][1]
    except Exception:  # noqa: BLE001
        logger.debug("Raw .lst fallback failed", exc_info=True)
    return None


def _lst_fallback_section(refinement, name: str, index: int) -> dict:
    """Parsed section of phase ``name`` re-read from the stored raw ``.lst`` text."""
    text = _lst_section_text(refinement, name, index)
    return parse_lst_section(text) if text else {}


_ANGLE_KEYS = {"alpha", "beta", "gamma"}


def _add_status_fields(entry, refinement, name, index, extra, spec, b1_spec, references) -> None:
    """Add ``refinement_status``, ``size_strain`` and the lattice ``state`` of one phase."""
    text = _lst_section_text(refinement, name, index)
    lattice = entry["lattice_parameters"]

    def spec_of(key):
        return spec(name, key) if spec is not None else None

    lattice_spec = spec_of("lattice_range")
    parsed = parse_lst_section(text) if text else {}
    if text and not any(key in parsed for key in ("A", "B", "C")):
        # the listing carries no lattice: it was held at the database values (a per-phase
        # setting the global specs do not show)
        lattice_spec = "fixed"
    status = None
    states: dict[str, str] = {}
    if text is not None:
        states = lattice_states(text, lattice_spec)
        status = phase_status(
            text, parsed, lattice_spec=lattice_spec, b1_spec=b1_spec, k2_spec=spec_of("k2"),
            overall_rwp=_value_and_esd(getattr(refinement.lst_data, "rwp", None))[0],
        )
        entry["refinement_status"] = status
    for key, item in lattice.items():
        item["state"] = states.get(key) or (
            "refined" if item["esd"] is not None else "fixed" if lattice_spec == "fixed" else "at_limit"
        )
    if lattice_spec == "fixed":
        reference = next((cif for cif in references or [] if cif_stem(cif) == name), None)
        if reference is not None:
            try:
                cell = reference.to_structure().lattice
                for key, value in zip(("a", "b", "c", "alpha", "beta", "gamma"), (*cell.abc, *cell.angles)):
                    if key not in lattice:
                        lattice[key] = {
                            "value": float(value), "esd": None, "state": "fixed_reference",
                            "unit": "deg" if key in _ANGLE_KEYS else "Å",
                        }
            except Exception:  # noqa: BLE001 - the reference is only a display fallback
                logger.debug("Reference lattice of %s unavailable", name, exc_info=True)
    entry["size_strain"] = size_strain(
        {**extra, **parsed}, (status or {}).get("undefined") or {}, b1_spec=b1_spec,
        k1_spec=spec_of("k1"), k2_spec=spec_of("k2"),
        size_status=entry["crystallite_size_status"],
        failed=(status or {}).get("level") == "failed",
    )


def _phase_refinement_details(
    refinement, b1_spec=None, *, spec=None, references=None
) -> dict[str, dict]:
    """Return crystallographic and profile values available in the BGMN output.

    ``b1_spec``: optional ``phase name -> b1 spec`` callable used for the
    ``crystallite_size_status`` of each phase (see ``crystallite_size_status``).
    ``spec``: optional ``(phase name, key) -> spec`` callable (``_phase_spec_resolver``) for the
    refinement status and size/strain; ``references``: reference CIFs (``Cif``) whose lattice
    fills a phase refined with a fixed lattice (state ``fixed_reference``).
    """
    quantitative = _quantitative_phase_details(refinement)
    output = {}
    for index, (name, phase) in enumerate(refinement.lst_data.phases_results.items()):
        bgmn_unit = str(getattr(phase, "unit", "NM"))
        lattice = {}
        for key in ("a", "b", "c", "alpha", "beta", "gamma"):
            value, esd = _value_and_esd(getattr(phase, key, None))
            if value is None:
                continue
            unit = "deg" if key in {"alpha", "beta", "gamma"} else "Å"
            factor = 1 if unit == "deg" or bgmn_unit.upper() != "NM" else 10
            lattice[key] = {
                "value": value * factor,
                "esd": esd * factor if esd is not None else None,
                "unit": unit,
            }

        grain_sizes = []
        extra = dict(phase.model_extra or {})
        if not any(key.lower().startswith("grainsize") for key in extra):
            # documents parsed before 2026-09-08 dropped parenthesised keys such as
            # GrainSize(1,1,1); the stored raw listing still has them
            for key, value in _lst_fallback_section(refinement, name, index).items():
                if key.lower().startswith("grainsize") or key in {"B1", "k1"}:
                    extra.setdefault(key, value)
        # BGMN prints the start-value GrainSize for every phase whose size broadening stayed
        # unrefined (no esd, B1 not refined); that number is not a result.
        b1_refined = isinstance(extra.get("B1"), (tuple, list))
        for key, raw_value in extra.items():
            if not key.lower().startswith("grainsize"):
                continue
            value, esd = _value_and_esd(raw_value)
            if value is not None and (esd is not None or b1_refined):
                grain_sizes.append({
                    "direction": key,
                    "value": value,
                    "esd": esd,
                    "unit": "nm" if bgmn_unit.upper() == "NM" else bgmn_unit,
                })

        b1_value = b1_spec(name) if b1_spec is not None else (spec(name, "b1") if spec else None)
        output[name] = {
            **quantitative.get(name, {}),
            "lattice_parameters": lattice,
            **_database_setting_lattice(name, phase, lattice),
            "xray_density": phase.xray_density,
            "rphase": phase.rphase,
            "grain_sizes": grain_sizes,
            "crystallite_size_status": crystallite_size_status(extra, grain_sizes, b1_value),
        }
        try:
            _add_status_fields(output[name], refinement, name, index, extra, spec, b1_value, references)
        except Exception:  # noqa: BLE001 - the status fields are additions; never break the payload
            logger.debug("Refinement status of %s unavailable", name, exc_info=True)
    try:
        # Orientation-refined phases report only a mean GEWICHT; take their weight esd from
        # BGMN's Q<phase> goals so every consumer (task page, tables, summary) agrees.
        return confidence_module.fill_weight_esd(refinement, output)
    except Exception:
        logger.debug("Weight esd could not be filled from Q goals", exc_info=True)
        return output


def _peak_diagnostics(refinement, missing=None, extra=None, mismatch=None) -> dict:
    """Return candidate diagnostic arrays and their display counts."""
    metrics = getattr(refinement, "refinement_metrics", None)
    if metrics is not None:
        missing = metrics.missing_peaks
        extra = metrics.extra_peaks
        mismatch = metrics.intensity_mismatch_peaks

    def as_list(value):
        if value is None:
            return None
        return peaks_in_fit_range(value, refinement.plot_data.x).tolist()

    values = {
        "missing": as_list(missing),
        "extra": as_list(extra),
        "intensity_mismatch": as_list(mismatch),
    }
    return {
        **values,
        "counts": {key: len(value or []) for key, value in values.items()},
    }


def unexplained_count(diagnostics) -> int | None:
    """Unexplained peaks as on the task card: significant + minor (instrument/noise peaks
    excluded); the raw missing-peak count for payloads without the classification."""
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    by_class = diagnostics.get("counts_by_class")
    if isinstance(by_class, dict):
        try:
            return int(by_class.get("significant") or 0) + int(by_class.get("minor") or 0)
        except (TypeError, ValueError):
            pass
    missing = (diagnostics.get("counts") or {}).get("missing")
    return missing if isinstance(missing, int) and not isinstance(missing, bool) else None


def unexplained_minor_count(diagnostics) -> int | None:
    """Minor share of `unexplained_count`, so tables can print `significant (+minor)`."""
    by_class = (diagnostics if isinstance(diagnostics, dict) else {}).get("counts_by_class")
    try:
        return int(by_class.get("minor") or 0) if isinstance(by_class, dict) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# size / signal-to-noise aware unexplained-peak classification
# ---------------------------------------------------------------------------
_REFERENCE_MEMO_MAX = 512
_REFERENCE_MEMO: "OrderedDict[tuple, tuple | None]" = OrderedDict()
_REFERENCE_MEMO_LOCK = threading.Lock()


@dataclass
class _PeakContext:
    """Per-document inputs of the missing-peak classification (built once)."""

    document: object
    wavelength: dict | None
    counts_like: bool | None
    range_params: dict
    hint_mode: str
    job_key: object = None
    #: Set by ``_reference_patterns``: background simulations that could add trace hints are
    #: still outstanding, so a later request may return more hints.
    hints_pending: bool = field(default=False, repr=False)
    _cod: object = field(default=None, repr=False)
    _cod_done: bool = field(default=False, repr=False)

    def measured_range(self, refinement=None) -> tuple[float, float] | None:
        """Angular range of the measurement (falls back to the refinement's profile)."""
        for source in (
            lambda: self.document.xrd_data.angles,
            lambda: refinement.plot_data.x,
            lambda: self.document.final_result.plot_data.x,
        ):
            try:
                values = np.asarray(source(), dtype=float)
                values = values[np.isfinite(values)]
                if values.size:
                    return float(values.min()), float(values.max())
            except Exception:
                continue
        return None

    def cod_patterns(self, refinement=None) -> list | None:
        """Cached COD reference patterns (one lookup per document)."""
        if self._cod_done:
            return self._cod
        window = self.measured_range(refinement)
        if self.wavelength is None or window is None:
            return None
        elements = list(getattr(self.document, "elements", None) or [])
        precursors = list(getattr(self.document, "precursors", None) or [])
        patterns = peak_significance.cod_reference_patterns(
            elements, precursors, self.wavelength["alpha1_a"], window, mode=self.hint_mode
        )
        if patterns is not None or self.hint_mode != "background":
            self._cod, self._cod_done = patterns, True
        return patterns


def _peak_context(document, hint_mode: str | None = None, job_key=None) -> _PeakContext:
    """Wavelength, count-type and analysis-range inputs shared by all candidates."""
    settings = getattr(document, "search_kwargs", None) or {}
    refinement_params = settings.get("refinement_params") or {}
    range_params = {}
    if isinstance(refinement_params, dict):
        range_params = {
            key: refinement_params[key]
            for key in ("wmin", "wmax", "excluded_ranges")
            if refinement_params.get(key) is not None
        }
    return _PeakContext(
        document=document,
        wavelength=peak_significance.resolve_wavelength(settings.get("wavelength", "Cu")),
        counts_like=_counts_like(document),
        range_params=range_params,
        hint_mode=hint_mode or peak_significance.hint_mode(),
        job_key=job_key if job_key is not None else id(document),
    )


_NOT_SIMULATED = object()


def _reference_key(ctx: _PeakContext, stem: str, window) -> tuple:
    return (
        ctx.job_key, stem, round(ctx.wavelength["alpha1_a"], 5),
        round(window[0], 1), round(window[1], 1),
    )


def _simulated_reference(cif, stem: str, ctx: _PeakContext, window, *, compute: bool = True):
    """Simulated ``(two_theta, intensity)`` of one reference CIF.

    With ``compute=False`` only the memo is consulted and ``_NOT_SIMULATED`` is returned for
    a CIF that has not been simulated yet. ``None`` means "cannot be simulated".
    """
    key = _reference_key(ctx, stem, window)
    with _REFERENCE_MEMO_LOCK:
        if key in _REFERENCE_MEMO:
            _REFERENCE_MEMO.move_to_end(key)
            return _REFERENCE_MEMO[key]
    if not compute:
        return _NOT_SIMULATED
    result = None
    try:
        from dara.search.prefilter import simulate_structure_reflections

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = cif.to_structure()
            if structure.volume <= peak_significance.HINT_MAX_VOLUME_A3:
                two_theta, intensity, _ = simulate_structure_reflections(
                    structure, ctx.wavelength["alpha1_a"], window
                )
                result = (np.asarray(two_theta, float), np.asarray(intensity, float))
    except Exception:
        logger.debug("Reference %s could not be simulated", stem, exc_info=True)
    with _REFERENCE_MEMO_LOCK:
        _REFERENCE_MEMO[key] = result
        while len(_REFERENCE_MEMO) > _REFERENCE_MEMO_MAX:
            _REFERENCE_MEMO.popitem(last=False)
    return result


def _simulate_references_in_background(todo: list, ctx: _PeakContext, window) -> bool:
    """Fill the reference memo for ``todo`` ``[(cif, stem)]`` on the low-priority worker.

    Returns whether that job is queued or running now (``False`` once the worker is stopped,
    so the hints are not reported as pending forever).
    """

    def job() -> None:
        for cif, stem in todo:
            if peak_significance.stop_requested():
                return
            _simulated_reference(cif, stem, ctx, window)
            peak_significance.yield_cpu()

    key = ("references", ctx.job_key, round(ctx.wavelength["alpha1_a"], 5),
           round(window[0], 1), round(window[1], 1))
    peak_significance.submit_background(key, job)
    return peak_significance.has_pending(key)


def _reference_patterns(document, refinement, ctx: _PeakContext) -> list[dict]:
    """Simulated reference patterns: candidate CIFs, input CIFs, then the COD cache.

    In ``background`` mode no structure is simulated on the calling thread: references that
    are not memoized yet are simulated by the background worker and appear in the hints of a
    later request. ``sync`` mode (benchmark, offline) simulates them immediately.
    """
    patterns: list[dict] = []
    ctx.hints_pending = False
    window = ctx.measured_range(refinement)
    if ctx.wavelength is None or window is None:
        return patterns
    sources = [
        (cif, "candidate")
        for entry in getattr(document, "results", None) or []
        for cif in _flatten_cifs(entry[0])
    ] + [
        (cif, "input")
        for cif in _flatten_cifs(list(getattr(document, "input_cifs", None) or []))
    ]
    background = ctx.hint_mode == "background"
    seen: set[str] = set()
    todo: list = []
    for cif, source in sources:
        stem = Path(str(getattr(cif, "filename", "") or "")).stem
        if not stem or stem in seen:
            continue
        seen.add(stem)
        simulated = _simulated_reference(cif, stem, ctx, window, compute=not background)
        if simulated is _NOT_SIMULATED:
            todo.append((cif, stem))
            continue
        if simulated is None:
            continue
        formula, number = confidence_module.phase_identity(stem)
        patterns.append({
            "name": stem,
            "formula": formula or stem.split("_")[0],
            "spacegroup_number": number,
            "spacegroup_symbol": _spacegroup_symbol(number) if number else None,
            "source": source,
            "popularity": 1,
            "two_theta": simulated[0],
            "intensity": simulated[1],
        })
    if todo and _simulate_references_in_background(todo, ctx, window):
        ctx.hints_pending = True
    cod = ctx.cod_patterns(refinement)
    if cod is None and background and peak_significance.cod_pool_pending(
        getattr(document, "elements", None) or [], getattr(document, "precursors", None) or [],
        ctx.wavelength["alpha1_a"], window,
    ):
        # This document's COD pool is still being simulated (other tasks' jobs do not count).
        ctx.hints_pending = True
    patterns.extend(cod or [])
    return patterns


def _classified_peak_diagnostics(
    document, refinement, missing=None, extra=None, mismatch=None, *,
    hint_mode: str | None = None, context: _PeakContext | None = None,
) -> dict:
    """``_peak_diagnostics`` plus the size/S-N classification of the missing peaks.

    The legacy keys are unchanged. The new keys (``missing_classified``,
    ``counts_by_class``, ``missing_significance``, ``trace_hints``) are omitted when the
    refinement profile is not available (old payloads), so consumers fall back to counts.
    ``hint_mode`` ("off", "background", "sync") overrides the context's trace-hint mode;
    "off" skips every reference simulation.
    """
    diag = _peak_diagnostics(refinement, missing, extra, mismatch)
    try:
        ctx = context or _peak_context(document, hint_mode)
        mode = hint_mode or ctx.hint_mode
        classified = peak_significance.classify_missing_peaks(
            refinement, diag["missing"], wavelength=ctx.wavelength,
            counts_like=ctx.counts_like, range_params=ctx.range_params,
        )
        if classified is None:
            return diag
        has_targets = any(
            entry["class"] in ("minor", "significant")
            for entry in classified["missing_classified"]
        )
        if has_targets and mode != "off" and ctx.wavelength is not None:
            if mode != ctx.hint_mode:
                ctx = _PeakContext(
                    document=ctx.document, wavelength=ctx.wavelength,
                    counts_like=ctx.counts_like, range_params=ctx.range_params,
                    hint_mode=mode, job_key=ctx.job_key,
                    hints_pending=ctx.hints_pending,
                )
            patterns = _reference_patterns(document, refinement, ctx)
            classified = peak_significance.attach_trace_hints(
                classified, refinement, patterns,
                elements=getattr(document, "elements", None) or [],
                precursors=getattr(document, "precursors", None) or [],
            )
            if ctx.hints_pending:
                classified["hints_pending"] = True
        return {**diag, **classified}
    except Exception:
        logger.debug("Missing-peak classification failed", exc_info=True)
        return diag


def _parse_string_list(value: str | None) -> list[str]:
    """Accept JSON/Python lists or a comma-separated string from old clients."""
    if not value:
        return []
    try:
        parsed = literal_eval(value)
    except (ValueError, SyntaxError):
        parsed = value.split(",")
    if isinstance(parsed, str):
        parsed = parsed.split(",")
    if not isinstance(parsed, (list, tuple, set)):
        raise HTTPException(status_code=400, detail="Expected a comma-separated list")
    return [str(item).strip() for item in parsed if str(item).strip()]


@router.get("/instrument-profiles")
async def instrument_profiles():
    """Return the instrument profiles actually bundled with this server."""
    from dara.instrument_profiles import device_detw, device_file_text, registered_profiles

    custom = registered_profiles()
    profiles = _available_instrument_profiles() + list(custom)
    calibratable = []
    for name in profiles:  # a device file (.sav) with a numeric DetW can be calibrated with a standard
        try:
            device = device_file_text(name)
            if device is not None and device_detw(device) is not None:
                calibratable.append(name)
        except (OSError, ValueError):
            continue
    return {"profiles": profiles, "details": custom, "calibratable": calibratable}


@router.post("/pattern/inspect")
async def inspect_pattern(pattern_file: Annotated[UploadFile, File()]):
    """Validate an uploaded pattern and suggest XRDML acquisition settings."""
    from dara.filename_chemistry import suggest_filename_chemistry

    filename = Path(pattern_file.filename or "pattern").name
    try:
        with tempfile.TemporaryDirectory() as pattern_temp_dir:
            temp_path = Path(pattern_temp_dir) / filename
            with open(temp_path, "wb") as temp_file:
                shutil.copyfileobj(pattern_file.file, temp_file)
            pattern = load_pattern(temp_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")

    angles = np.asarray(pattern.angles, dtype=float)
    finite_angles = angles[np.isfinite(angles)]
    metadata = {}
    suggestion = None
    if temp_path.suffix.lower() == ".xrdml" and getattr(pattern, "xrdml_dict", None):
        metadata = get_xrdml_metadata(pattern.xrdml_dict)
        suggestion = recommend_xrdml_instrument_profile(metadata)
        available = set(_available_instrument_profiles())
        if suggestion and suggestion["profile"] not in available:
            suggestion = None

    # B's reader may add load warnings and an intensity unit; older readers do not.
    intensity_unit = getattr(pattern, "intensity_unit", None)
    load_warnings = [str(item) for item in (getattr(pattern, "load_warnings", None) or [])]
    try:
        quality_checks, quality_stats = pattern_quality(
            angles, np.asarray(pattern.intensities, dtype=float), intensity_unit
        )
    except Exception:
        quality_checks, quality_stats = [], {}

    return {
        "filename": filename,
        "format": temp_path.suffix.lower().lstrip("."),
        "two_theta_min": float(finite_angles.min()) if finite_angles.size else None,
        "two_theta_max": float(finite_angles.max()) if finite_angles.size else None,
        "data_points": int(angles.size),
        "metadata": metadata,
        "profile_suggestion": suggestion,
        "recommended_wavelength": metadata.get("radiation"),
        "filename_chemistry": suggest_filename_chemistry(filename),
        "intensity_unit": intensity_unit,
        "load_warnings": load_warnings,
        "quality_checks": quality_checks,
        "quality_stats": quality_stats,
    }


def _saved_task_input(task_id: int) -> tuple[dict, dict, object]:
    """Restore only a stored pattern/input snapshot, never execute a stored job."""
    with get_worker_store() as worker_store:
        record = worker_store.query_one({"index": task_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Source task not found")
    raw_job = record.get("job", {})
    kwargs = deepcopy(raw_job.get("function_kwargs", {}))
    raw_pattern = kwargs.get("xrd_data")
    if raw_pattern is None and raw_job.get("function_args"):
        raw_pattern = raw_job["function_args"][0]
    if raw_pattern is None:
        raise HTTPException(status_code=409, detail="Original pattern is not available for this task")
    try:
        pattern = MontyDecoder().process_decoded(raw_pattern)
        if not hasattr(pattern, "angles") or not hasattr(pattern, "intensities"):
            raise ValueError("Not an XRD pattern")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="The saved original pattern could not be restored") from exc
    return record, kwargs, pattern


async def resolve_phase_set(task_id: int) -> list[Cif]:
    """Reference CIFs of the phases in a finished task's adopted result (the adopted version,
    else the automatic best fit): the exact structures to reuse on other files.

    Raises ``HTTPException`` 404 (unknown task), 409 (not finished, quick check, or the
    structures are not stored).
    """
    from dara.server import review_router, versions

    task, document = await review_router._load_completed_task(task_id)
    selection = review_router._report_selection(None, False, review_router._review_metadata(task))
    review_router._refuse_quick_check(task, selection)
    refinement, meta = versions.resolve_refinement(document, task, selection)
    names = list(refinement.lst_data.phases_results)
    by_stem = {cif.filename: cif for cif in meta["cifs"] if cif.filename}
    missing = [name for name in names if name not in by_stem]
    if not names or missing:
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id}: the structure of {missing[0] if missing else 'its phases'} is not stored.",
        )
    return [by_stem[name] for name in names]


def _phase_set_entries(cifs) -> list[dict]:
    """(name, formula, space group) of each reused structure."""
    phases = []
    for cif in cifs:
        parts = (cif.filename or cif.name or "").split("_")
        number = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        phases.append({
            "name": cif.filename or cif.name,
            "formula": known_phases.formula_of_cif(cif),
            "spacegroup": _spacegroup_symbol(number) if number else None,
        })
    return phases


@router.get("/task/{task_id}/phase-set")
async def phase_set(task_id: int):
    """The phases (formula, space group) a new submission would reuse from this task."""
    return {"task_id": task_id, "phases": _phase_set_entries(await resolve_phase_set(task_id))}


# Submit-form defaults (ui/src/pages/index.js initialValues); also used to fill settings an
# older task did not record when it is rerun.
DEFAULT_CANDIDATE_PREFILTER = True
DEFAULT_SCREENING_TIMEOUT_S = 300.0


def _rerun_settings(record: dict, kwargs: dict, pattern) -> dict:
    """Return an explicit UI allowlist; credentials and private DB config stay server-side."""
    raw_job = record.get("job", {})
    saved = raw_job.get("metadata", {}).get("submission_settings", {})
    search = kwargs.get("search_kwargs") or {}
    refine = search.get("refinement_params") or {}
    predict = kwargs.get("predict_kwargs") or {}
    context = kwargs.get("context") or {}
    dbs = kwargs.get("cif_dbs") or []
    classes = {db.get("@class") for db in dbs if isinstance(db, dict)}
    mp_db = next((db for db in dbs if isinstance(db, dict) and db.get("@class") == "MaterialsProjectDatabase"), {})
    mode = saved.get("analysis_mode", kwargs.get("analysis_mode", "reaction" if predict else "elemental"))
    if mode not in {"elemental", "reaction", "known_mixture"}:
        mode = "elemental"
    settings = {
        "user": record.get("user", ""),
        "analysis_mode": mode,
        "elements": kwargs.get("elements") or context.get("elements") or [],
        "precursor_formulas": (
            saved.get("precursor_input") or kwargs.get("precursors") or context.get("precursors") or []
        ),
        "instrument_profile": search.get("instrument_profile", "Aeris-fds-Pixcel1d-Medipix3"),
        "wavelength": search.get("wavelength", "Cu"),
        "temperature": predict.get("temp", 1073) - 273,
        "atmosphere": saved.get("atmosphere", {"O_air": "air", "O": "oxygen"}.get(predict.get("open_elem"), "inert")),
        "use_cod": "CODDatabase" in classes or (not dbs and not kwargs.get("cifs") and not kwargs.get("additional_cifs")),
        "use_mp": "MaterialsProjectDatabase" in classes,
        "mp_e_hull_cutoff": kwargs.get("database_e_hull_filter", mp_db.get("e_hull_filter", 0.05)),
        "max_mp_structures": mp_db.get("max_structures", 100),
        "two_theta_min": refine.get("wmin"),
        "two_theta_max": refine.get("wmax"),
        "excluded_ranges": refine.get("excluded_ranges", []),
        # Settings a task did not record (older tasks) take today's defaults, so a rerun is
        # not stuck with the slow legacy screening (no pre-filter, 1200 s per reference).
        # An explicit choice saved with the task is kept.
        "candidate_prefilter": (
            DEFAULT_CANDIDATE_PREFILTER
            if kwargs.get("candidate_prefilter") is None
            else bool(kwargs["candidate_prefilter"])
        ),
        "screening_timeout_s": (
            DEFAULT_SCREENING_TIMEOUT_S
            if kwargs.get("screening_timeout_s") is None
            else kwargs["screening_timeout_s"]
        ),
        "max_analysis_minutes": (
            kwargs["max_runtime_s"] / 60 if kwargs.get("max_runtime_s") else None
        ),
        "internal_standard": saved.get("internal_standard"),
        "sample": saved.get("sample"),
        "phase_set_from": saved.get("phase_set_from"),
    }
    angles = np.asarray(pattern.angles, dtype=float)
    finite = angles[np.isfinite(angles)]
    return {
        "source_task_id": record["index"],
        "filename": saved.get("filename", raw_job.get("name", "saved_pattern.xy")),
        "settings": settings,
        "additional_cif_count": len(kwargs.get("additional_cifs") or kwargs.get("cifs") or []),
        # The structures stored with a task that reused a phase set: a rerun refines these again.
        "phase_set": _phase_set_entries(MontyDecoder().process_decoded(kwargs.get("fixed_phase_cifs") or [])),
        "pattern": {
            "two_theta_min": float(finite.min()) if finite.size else None,
            "two_theta_max": float(finite.max()) if finite.size else None,
            "data_points": int(angles.size),
        },
    }


@router.get("/task/{task_id}/rerun-settings")
async def rerun_settings(task_id: int):
    return _rerun_settings(*_saved_task_input(task_id))


LAB_WAVELENGTHS_ANGSTROM = {
    "Cu": 1.540598, "Co": 1.789010, "Cr": 2.289760, "Fe": 1.936042, "Mo": 0.70930,
}
WAVELENGTH_TOLERANCE_ANGSTROM = 0.03


def _selected_wavelength_angstrom(wavelength: str) -> float | None:
    """Selected wavelength in Å: a lab target name, or a numeric value in nm."""
    text = str(wavelength).strip()
    try:
        return float(text) * 10
    except ValueError:
        return LAB_WAVELENGTHS_ANGSTROM.get(text.capitalize())


def _wavelength_mismatch(pattern, pattern_path: Path, wavelength: str) -> dict | None:
    """Describe a recorded-vs-selected wavelength disagreement for an XRDML upload."""
    if pattern_path.suffix.lower() != ".xrdml" or not getattr(pattern, "xrdml_dict", None):
        return None
    try:
        recorded = get_xrdml_metadata(pattern.xrdml_dict).get("k_alpha1_angstrom")
        selected = _selected_wavelength_angstrom(wavelength)
        if recorded is None or selected is None:
            return None
        recorded, selected = float(recorded), float(selected)
    except (TypeError, ValueError):
        return None
    if abs(recorded - selected) <= WAVELENGTH_TOLERANCE_ANGSTROM:
        return None
    return {
        "code": "wavelength_mismatch",
        "message": (
            f"The file records a wavelength of {recorded:.4f} Å, but {selected:.4f} Å "
            "is selected. A mismatch shifts all calculated peak positions."
        ),
        "recorded_angstrom": recorded,
        "selected_angstrom": selected,
    }


INTERNAL_STANDARD_ERROR = (
    "Internal standard: enter a formula and an added amount between 0 and 100 wt%."
)


def parse_internal_standard(value) -> dict | None:
    """Validated ``{"formula", "wt_percent", "phase"}`` of an internal-standard request.

    ``None``, an empty string and ``"null"`` mean "no standard". Raises ``HTTPException(400)``.
    """
    if value is None or (isinstance(value, str) and value.strip() in ("", "null")):
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else dict(value)
        formula = str(data["formula"]).strip()
        weight = float(data["wt_percent"])
        for element in Composition(formula).elements:
            Element(str(element))  # a dummy species such as "Xx" is no element
        if not formula or not 0 < weight < 100:  # NaN fails the comparison too
            raise ValueError(formula)
    except Exception:  # noqa: BLE001 - any malformed input gets the one message
        raise HTTPException(status_code=400, detail=INTERNAL_STANDARD_ERROR) from None
    phase = data.get("phase")
    return {
        "formula": formula,
        "wt_percent": weight,
        "phase": phase if isinstance(phase, str) and phase else None,
    }


def build_submission_job(
    pattern,                                   # dara.xrd.XRDData (already loaded)
    name: str,
    *,
    analysis_mode: str = "",
    precursor_formulas: list[str] | str = "[]",
    elements: list[str] | str = "[]",
    instrument_profile: str = "Aeris-fds-Pixcel1d-Medipix3",
    wavelength: str | float = "Cu",
    temperature: int = -1,
    atmosphere: str = "air",
    use_cod: bool = True,
    use_mp: bool = True,
    mp_e_hull_cutoff: float = 0.05,
    max_mp_structures: int = 100,
    use_rxn_predictor: bool | None = None,
    additional_cifs: list | None = None,       # list[dara.cif.Cif], already parsed
    library_cif_ids: list[str] | str = "[]",   # reference library ids
    two_theta_min: float | None = None,
    two_theta_max: float | None = None,
    excluded_ranges: str | list = "[]",
    refinement_controls: str | dict = "{}",
    original_kwargs: dict | None = None,       # restored kwargs of a source task (clone), else None
    source_task_id: int | None = None,
    candidate_prefilter: bool | None = None,   # None keeps the legacy behaviour (no pre-filter)
    screening_timeout_s: float | None = None,  # None keeps the legacy 1200 s per reference
    max_analysis_minutes: float | None = None,  # None means no analysis time limit
    internal_standard: str | dict | None = None,  # {"formula", "wt_percent"} JSON, optional
    sample: str = "",                           # sample name shown in Results (optional)
    phase_set_cifs: list | None = None,         # exact structures of an earlier result (known-phase mode)
    phase_set_from: int | None = None,          # task the structures came from (recorded only)
):
    """Validate submission fields and return an unqueued jobflow Job.

    Raises ``HTTPException(400)`` on invalid input.
    """
    try:
        original_kwargs = original_kwargs if original_kwargs is not None else {}
        additional_cifs = list(additional_cifs or [])
        range_params = validate_analysis_range(
            pattern.angles, two_theta_min, two_theta_max, excluded_ranges
        )

        precursor_formulas = (
            list(precursor_formulas)
            if isinstance(precursor_formulas, (list, tuple))
            else _parse_string_list(precursor_formulas)
        )
        elements = (
            list(elements) if isinstance(elements, (list, tuple)) else _parse_string_list(elements)
        )
        library_ids = (
            [str(item) for item in library_cif_ids]
            if isinstance(library_cif_ids, (list, tuple))
            else _parse_string_list(library_cif_ids)
        )

        phase_set_cifs = list(phase_set_cifs or [])
        if phase_set_cifs:
            if analysis_mode not in ("", "known_mixture"):
                raise HTTPException(status_code=400, detail="Reusing a phase set needs Known-phase refinement")
            analysis_mode = "known_mixture"
            precursor_formulas = known_phases.formulas_of_cifs(phase_set_cifs)

        # Compatibility with the original web/API contract.
        if not analysis_mode:
            analysis_mode = "reaction" if use_rxn_predictor else "elemental"
            if analysis_mode == "elemental" and not elements:
                elements, precursor_formulas = precursor_formulas, []
        if analysis_mode not in {"reaction", "elemental", "known_mixture"}:
            raise HTTPException(status_code=400, detail="Invalid analysis mode")
        # Optional amounts ("2 TiO2", "TiO2:0.2g") feed only the mass-balance check;
        # every existing consumer keeps receiving plain formulas.
        precursor_input = list(precursor_formulas)
        try:
            precursor_formulas, precursor_amounts = split_precursor_entries(precursor_formulas)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid precursor formulas: {exc}")

        if library_ids:
            try:
                from dara.server.reference_library_router import load_library_cifs
            except ImportError:
                raise HTTPException(
                    status_code=400,
                    detail="The reference CIF library is not available on this server.",
                )
            try:
                additional_cifs.extend(load_library_cifs(library_ids))
            except KeyError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown reference library CIF: {exc.args[0]}",
                )

        try:
            [Composition(p) for p in precursor_formulas]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid precursor formulas")
        if analysis_mode == "known_mixture":  # the 12-phase limit counts distinct formulas
            precursor_formulas = known_phases.distinct_formulas(precursor_formulas)
        try:
            elements = sorted({str(Element(element)) for element in elements})
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid element symbols")

        if not elements and precursor_formulas:
            elements = sorted(
                {
                    str(element)
                    for formula in precursor_formulas
                    for element in Composition(formula).elements
                }
            )

        if analysis_mode in {"reaction", "known_mixture"} and not precursor_formulas:
            raise HTTPException(
                status_code=400,
                detail="This analysis mode requires precursor/known-phase formulas",
            )
        if analysis_mode == "known_mixture" and (
            max(len(precursor_formulas), len(phase_set_cifs)) > known_phases.MAX_KNOWN_FORMULAS
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Known-phase refinement takes at most {known_phases.MAX_KNOWN_FORMULAS} phases.",
            )
        if analysis_mode == "elemental" and not elements:
            raise HTTPException(status_code=400, detail="Elemental mode requires elements")
        standard = parse_internal_standard(internal_standard)
        if standard is not None:
            # the phase search must be able to find the standard's own phase
            elements = sorted({*elements, *(str(el) for el in Composition(standard["formula"]).elements)})
        if not 0 <= mp_e_hull_cutoff <= 1:
            raise HTTPException(status_code=400, detail="MP E-hull cutoff must be 0 to 1 eV/atom")
        if not 1 <= max_mp_structures <= 1000:
            raise HTTPException(status_code=400, detail="MP structure limit must be 1 to 1000")
        if max_analysis_minutes is not None and not 5 <= max_analysis_minutes <= 720:
            raise HTTPException(
                status_code=400,
                detail="Maximum analysis time must be between 5 and 720 minutes",
            )
        if screening_timeout_s is not None and not 60 <= screening_timeout_s <= 1200:
            raise HTTPException(
                status_code=400,
                detail="Screening time limit must be between 60 and 1200 seconds",
            )

        try:
            wavelength = float(wavelength)
        except ValueError:
            pass

        cif_dbs = []
        if use_cod and not phase_set_cifs:
            cif_dbs.append(CODDatabase())
        if use_mp and not phase_set_cifs:
            try:
                import mp_api  # noqa: F401
            except ImportError:
                raise HTTPException(
                    status_code=400,
                    detail="Materials Project support is unavailable. Please install mp_api.",
                )
            if not os.environ.get("MP_API_KEY"):
                raise HTTPException(
                    status_code=400,
                    detail="MP_API_KEY is not set in the server environment.",
                )
            cif_dbs.append(
                MaterialsProjectDatabase(
                    max_structures=max_mp_structures,
                    e_hull_filter=mp_e_hull_cutoff,
                )
            )
        if not cif_dbs and not additional_cifs and not phase_set_cifs:
            raise HTTPException(status_code=400, detail="Select COD, MP, or upload reference CIFs")

        search_kwargs = {**(original_kwargs.get("search_kwargs") or {}), **{
            "instrument_profile": instrument_profile,
            "wavelength": wavelength,
        }}
        inherited_refine = dict(search_kwargs.get("refinement_params") or {})
        for key in ("wmin", "wmax", "excluded_ranges"):
            inherited_refine.pop(key, None)
        inherited_refine.update(range_params)
        if inherited_refine:
            search_kwargs["refinement_params"] = inherited_refine
        else:
            search_kwargs.pop("refinement_params", None)
        if range_params:
            search_kwargs["enable_angular_cut"] = False
        if original_kwargs.get("analysis_mode") == "known_mixture" and analysis_mode != "known_mixture":
            search_kwargs.pop("max_phases", None)
        if analysis_mode == "known_mixture":
            search_kwargs["max_phases"] = max(1, min(len(phase_set_cifs) or len(precursor_formulas), 5))
        maker_kwargs = {
            "xrd_data": pattern,
            "precursors": precursor_formulas,
            "elements": elements,
            "analysis_mode": analysis_mode,
            "database_e_hull_filter": mp_e_hull_cutoff,
            "cif_dbs": cif_dbs,
            "additional_cifs": additional_cifs,
            "additional_cif_params": (
                deepcopy(original_kwargs["additional_cif_params"])
                if original_kwargs.get("additional_cif_params") is not None
                else {"lattice_range": 0.05}
            ),
            "search_kwargs": search_kwargs,
        }
        if phase_set_cifs:
            maker_kwargs["fixed_phase_cifs"] = phase_set_cifs
        # Run-time controls are sent explicitly by the UI and never inherited from a
        # source task; omitted when None so API/benchmark jobs stay on the legacy path.
        if candidate_prefilter is not None:
            maker_kwargs["candidate_prefilter"] = bool(candidate_prefilter)
        if screening_timeout_s is not None:
            maker_kwargs["screening_timeout_s"] = float(screening_timeout_s)
        if max_analysis_minutes is not None:
            maker_kwargs["max_runtime_s"] = float(max_analysis_minutes) * 60
        if original_kwargs.get("final_refinement_params") is not None:
            maker_kwargs["final_refinement_params"] = deepcopy(original_kwargs["final_refinement_params"])
        from dara.refinement_controls import RefinementControls, apply_controls

        if isinstance(refinement_controls, dict):
            controls = RefinementControls.model_validate(refinement_controls)
        else:
            controls = RefinementControls.model_validate_json(refinement_controls)
        maker_kwargs["final_refinement_params"] = apply_controls(
            maker_kwargs.get("final_refinement_params"), controls)
        maker_kwargs["final_refinement_overrides"] = {
            **(original_kwargs.get("final_refinement_overrides") or {}),
            **apply_controls({}, controls),
        }

        if analysis_mode == "reaction":
            if not use_mp:
                raise HTTPException(
                    status_code=400,
                    detail="Reaction mode requires Materials Project",
                )
            if temperature < 27 or temperature > 1727:
                raise HTTPException(
                    status_code=400,
                    detail="Reaction temperature must be between 27 and 1727 °C",
                )
            open_element = {"air": "O_air", "oxygen": "O"}.get(atmosphere)
            job = PhaseSearchMaker(name=name, verbose=False, max_num_results=5).make(
                predict_kwargs={
                    "temp": temperature + 273,
                    "open_elem": open_element,
                    "e_hull_cutoff": mp_e_hull_cutoff,
                },
                **maker_kwargs,
            )
        else:
            job = PhaseSearchMaker(
                name=name, verbose=False, phase_predictor=None, max_num_results=5
            ).make(**maker_kwargs)
        job.metadata["submission_settings"] = {
            "filename": name, "analysis_mode": analysis_mode, "atmosphere": atmosphere,
            "source_task_id": source_task_id, "library_cif_ids": library_ids,
            "candidate_prefilter": candidate_prefilter,
            "screening_timeout_s": screening_timeout_s,
            "max_analysis_minutes": max_analysis_minutes,
        }
        if phase_set_cifs and phase_set_from is not None:
            job.metadata["submission_settings"]["phase_set_from"] = phase_set_from
        if standard is not None:
            job.metadata["submission_settings"]["internal_standard"] = standard
        if (sample or "").strip():
            job.metadata["submission_settings"]["sample"] = sample.strip()[:120]
        if precursor_amounts:
            job.metadata["submission_settings"]["precursor_input"] = precursor_input
            job.metadata["submission_settings"]["precursor_amounts"] = precursor_amounts
        if instrument_profile.startswith("user-"):
            from dara.instrument_profiles import registered_profiles, registered_profile_path

            registered_profile_path(instrument_profile)
            job.metadata["instrument_profile_details"] = registered_profiles().get(instrument_profile)
        return job
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/submit")
async def submit(
    user: Annotated[str, Form()],
    pattern_file: Annotated[UploadFile | None, File()] = None,
    source_task_id: Annotated[int | None, Form()] = None,
    precursor_formulas: Annotated[str, Form()] = "[]",
    elements: Annotated[str, Form()] = "[]",
    analysis_mode: Annotated[str, Form()] = "",
    instrument_profile: Annotated[str, Form()] = "Aeris-fds-Pixcel1d-Medipix3",
    wavelength: Annotated[str, Form()] = "Cu",
    temperature: Annotated[int, Form()] = -1,
    atmosphere: Annotated[str, Form()] = "air",
    use_cod: Annotated[bool, Form()] = True,
    use_mp: Annotated[bool, Form()] = True,
    mp_e_hull_cutoff: Annotated[float, Form()] = 0.05,
    max_mp_structures: Annotated[int, Form()] = 100,
    use_rxn_predictor: Annotated[Optional[bool], Form()] = None,
    additional_phases: Annotated[list[UploadFile], File()] = None,
    reuse_additional_cifs: Annotated[bool, Form()] = True,
    two_theta_min: Annotated[float | None, Form()] = None,
    two_theta_max: Annotated[float | None, Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    refinement_controls: Annotated[str, Form()] = "{}",
    confirm_wavelength: Annotated[bool, Form()] = False,
    library_cif_ids: Annotated[str, Form()] = "[]",
    candidate_prefilter: Annotated[Optional[bool], Form()] = None,
    screening_timeout_s: Annotated[Optional[float], Form()] = None,
    max_analysis_minutes: Annotated[Optional[float], Form()] = None,
    internal_standard: Annotated[str, Form()] = "",
    sample: Annotated[str, Form()] = "",
    phase_set_task_id: Annotated[int | None, Form()] = None,
    reuse_phase_set: Annotated[bool, Form()] = True,
):
    try:
        original_kwargs = {}
        if source_task_id is not None:
            source_record, original_kwargs, pattern = _saved_task_input(source_task_id)
            name = _rerun_settings(source_record, original_kwargs, pattern)["filename"]
        if pattern_file is not None:
            name = Path(pattern_file.filename or "pattern.xy").name
            with tempfile.TemporaryDirectory() as pattern_temp_dir:
                temp_path = Path(pattern_temp_dir) / name
                with open(temp_path, "wb") as temp_file:
                    shutil.copyfileobj(pattern_file.file, temp_file)
                try:
                    pattern = load_pattern(temp_path)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")
                if not confirm_wavelength:
                    mismatch = _wavelength_mismatch(pattern, temp_path, wavelength)
                    if mismatch:
                        raise HTTPException(status_code=400, detail=mismatch)
        elif source_task_id is None:
            raise HTTPException(status_code=400, detail="Upload an XRD pattern or select a source task")
        additional_cifs = []
        if source_task_id is not None and reuse_additional_cifs:
            additional_cifs = MontyDecoder().process_decoded(
                original_kwargs.get("additional_cifs") or original_kwargs.get("cifs") or []
            )
        if additional_phases:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir = Path(temp_dir)
                for phase in additional_phases:
                    phase_name = Path(phase.filename or "reference.cif").name
                    with open(temp_dir / phase_name, "wb") as f:
                        shutil.copyfileobj(phase.file, f)
                    cif_file = Cif.from_file(temp_dir / phase_name)
                    if not cif_file.data:
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid additional CIF file provided",
                        )
                    additional_cifs.append(cif_file)

        phase_set_cifs, phase_set_from, phase_set_warnings = None, phase_set_task_id, []
        if phase_set_task_id is not None:
            phase_set_cifs = await resolve_phase_set(phase_set_task_id)
        elif source_task_id is not None and reuse_phase_set and analysis_mode in ("", "known_mixture"):
            # A rerun of a task that reused a phase set reuses the same stored structures
            # (the UI sends reuse_phase_set=false once the user enters formulas instead).
            saved_set = MontyDecoder().process_decoded(original_kwargs.get("fixed_phase_cifs") or [])
            if saved_set:
                phase_set_cifs = saved_set
                phase_set_from = (
                    source_record.get("job", {}).get("metadata", {}).get("submission_settings", {}).get("phase_set_from")
                )
                phase_set_warnings = _phase_set_formula_warnings(
                    precursor_formulas, saved_set, phase_set_from or source_task_id
                )

        job = build_submission_job(
            pattern,
            name,
            phase_set_cifs=phase_set_cifs,
            phase_set_from=phase_set_from,
            analysis_mode=analysis_mode,
            precursor_formulas=precursor_formulas,
            elements=elements,
            instrument_profile=instrument_profile,
            wavelength=wavelength,
            temperature=temperature,
            atmosphere=atmosphere,
            use_cod=use_cod,
            use_mp=use_mp,
            mp_e_hull_cutoff=mp_e_hull_cutoff,
            max_mp_structures=max_mp_structures,
            use_rxn_predictor=use_rxn_predictor,
            additional_cifs=additional_cifs,
            library_cif_ids=library_cif_ids,
            two_theta_min=two_theta_min,
            two_theta_max=two_theta_max,
            excluded_ranges=excluded_ranges,
            refinement_controls=refinement_controls,
            original_kwargs=original_kwargs,
            source_task_id=source_task_id,
            candidate_prefilter=candidate_prefilter,
            screening_timeout_s=screening_timeout_s,
            max_analysis_minutes=max_analysis_minutes,
            internal_standard=internal_standard,
            sample=sample,
        )
        job_index = add_job_to_queue(job, user=user)
        return {
            "message": "submitted",
            "wf_id": job_index,
            "warnings": phase_set_warnings if phase_set_cifs else _precursor_warnings(
                precursor_formulas, use_cod, analysis_mode,
                user_formulas=[known_phases.formula_of_cif(cif) for cif in additional_cifs],
            ),
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        print_exc()
        raise HTTPException(status_code=400, detail=str(e))


def _phase_set_formula_warnings(precursor_formulas, phase_set_cifs, task_id) -> list[dict]:
    """Warning when a rerun that keeps a stored phase set was submitted with other formulas."""
    try:
        entered, _ = split_precursor_entries(_parse_string_list(precursor_formulas))
    except (ValueError, HTTPException):
        return []
    keys = {known_phases.reduced_formula(formula) for formula in entered}
    stored = {known_phases.reduced_formula(formula) for formula in known_phases.formulas_of_cifs(phase_set_cifs)}
    if not entered or keys == stored:
        return []
    return [{
        "code": "phase_set_formulas_ignored",
        "message": (
            f"The formulas you entered were ignored: the stored structures of Task {task_id} are used "
            "(choose 'Enter formulas instead' to refine other phases)."
        ),
        "suggestions": [],
    }]


@lru_cache(maxsize=1)
def _cod_formulas() -> frozenset:
    """Reduced formulas of the local COD index (only built once the index is loaded)."""
    info = structure_db._COD_INSTANCE.preparsed_info
    return frozenset(row[0] for rows in info.values() for row in rows)


def _precursor_warnings(
    precursor_formulas, use_cod: bool, analysis_mode: str = "", user_formulas=()
) -> list[dict]:
    """Non-blocking hints for precursors that are not in the COD index (probably typos).

    Known-phase mode takes such a formula from Materials Project (for this run only) when an
    API key is configured, so its hint says that instead of suggesting other formulas. A
    formula that a user CIF (``user_formulas``) already supplies needs no hint.
    """
    if not use_cod or structure_db._COD_INSTANCE is None:  # never wait for the index to load
        return []
    try:
        import difflib

        entries, _ = split_precursor_entries(
            precursor_formulas
            if isinstance(precursor_formulas, (list, tuple))
            else _parse_string_list(precursor_formulas)
        )
        known = _cod_formulas()
        supplied = {known_phases.reduced_formula(formula) for formula in user_formulas}
        if analysis_mode == "known_mixture":
            entries = known_phases.distinct_formulas(entries)
        warnings_out = []
        for entry in entries:
            reduced = Composition(entry).reduced_formula
            if reduced in known:
                continue
            if analysis_mode == "known_mixture":
                if reduced in supplied:
                    continue
                from_mp = _mp_configured()
                warnings_out.append({
                    "code": "precursor_from_mp" if from_mp else "precursor_no_structure",
                    "precursor": entry,
                    "message": (
                        f"No COD entry has the formula {entry}; its structure will be taken from "
                        "Materials Project for this run."
                        if from_mp else
                        f"No COD entry has the formula {entry} and Materials Project is not "
                        "configured; it will be reported as having no structure unless you add a CIF."
                    ),
                    "suggestions": [],
                })
                continue
            suggestions = difflib.get_close_matches(reduced, sorted(known), n=3, cutoff=0.8)
            message = f"No COD entry has the formula {entry} ({reduced})."
            if suggestions:
                message += f" Did you mean {', '.join(suggestions)}?"
            warnings_out.append({
                "code": "precursor_not_in_cod", "precursor": entry, "message": message,
                "suggestions": suggestions,
            })
        return warnings_out
    except Exception:  # noqa: BLE001 - a hint must never fail a submission
        logger.debug("Precursor check failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Submission estimate (candidate counts and run time before the job is queued)
# ---------------------------------------------------------------------------

MP_ESTIMATE_TIMEOUT_S = 15
MP_ESTIMATE_TTL_S = 600
_MP_ESTIMATE_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_MP_ESTIMATE_LOCKS: dict[tuple, threading.Lock] = {}
_MP_ESTIMATE_GUARD = threading.Lock()

# Fallbacks used when ``dara.search.candidate_cost`` is not available.
_COST_DEFAULTS = {
    "LIGHT_MAX": 400,
    "HEAVY_MAX": 1200,
    "DEFAULT_ASSUMED_REFLECTIONS": 300,
    "PREFILTER_MIN_CANDIDATES": 40,
    "PREFILTER_TYPICAL_KEEP_FRACTION": 0.85,
}
REACTION_ESTIMATE_CANDIDATES = 40
LONG_RUN_S = 1800
VERY_LONG_RUN_S = 5400
DEDUP_KEEP_FRACTION = 0.85


class EstimateRequest(BaseModel):
    analysis_mode: str = "elemental"
    elements: list[str] | str = []
    precursor_formulas: list[str] | str = []
    use_cod: bool = True
    use_mp: bool = True
    mp_e_hull_cutoff: float = 0.05
    max_mp_structures: int = 100
    candidate_prefilter: bool = False
    wavelength: str | float = "Cu"
    two_theta_max: float | None = None
    data_points: int | None = None
    max_analysis_minutes: float | None = None


def _candidate_cost_module():
    """The engine's cost model, or ``None`` while it is not installed."""
    try:
        return importlib.import_module("dara.search.candidate_cost")
    except (ImportError, AttributeError):
        return None


def _cost_constant(module, name: str):
    return getattr(module, name, _COST_DEFAULTS[name]) if module is not None else _COST_DEFAULTS[name]


def _mp_configured() -> bool:
    return known_phases.mp_configured()


def _mp_rows_for_estimate(elements: list[str], cutoff: float, limit: int) -> list[dict]:
    """MP metadata rows for the estimate, cached for ``MP_ESTIMATE_TTL_S`` (failures are not)."""
    key = (tuple(sorted(elements)), cutoff, limit)
    with _MP_ESTIMATE_GUARD:
        lock = _MP_ESTIMATE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        cached = _MP_ESTIMATE_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < MP_ESTIMATE_TTL_S:
            return cached[1]
        rows = MaterialsProjectDatabase(
            max_structures=limit, e_hull_filter=cutoff
        ).count_by_chemsys(
            list(key[0]), e_hull_filter=cutoff, limit=limit, timeout_s=MP_ESTIMATE_TIMEOUT_S
        )
        _MP_ESTIMATE_CACHE[key] = (time.monotonic(), rows)
        return rows


def _unique_formula_sg(pairs) -> int:
    return len({(str(formula), str(sg)) for formula, sg in pairs})


def _runtime_level(expected_s: float | None) -> str:
    if expected_s is None:
        return "unknown"
    if expected_s > VERY_LONG_RUN_S:
        return "very_long"
    if expected_s > LONG_RUN_S:
        return "long"
    return "ok"


def _reflection_summary(cost, mp_rows, wavelength, two_theta_max) -> tuple[dict, list[int]]:
    """Median reflection count and the number of very heavy MP structures."""
    summary = {"median": None, "very_heavy": 0, "source": "default"}
    counts: list[int] = []
    function = getattr(cost, "reflections_from_cell", None) if cost is not None else None
    if function is None or not mp_rows:
        return summary, counts
    for row in mp_rows:
        try:
            counts.append(
                int(
                    function(
                        row.get("volume"), row.get("spacegroup"), wavelength,
                        two_theta_max or 90, primitive=True,
                    )
                )
            )
        except Exception:
            continue
    if not counts:
        return summary, counts
    ordered = sorted(counts)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    heavy = _cost_constant(cost, "HEAVY_MAX")
    return {
        "median": round(median),
        "very_heavy": sum(count >= heavy for count in counts),
        "source": "mp",
    }, counts


def _runtime_estimate(cost, n_candidates, reflection_counts, data_points) -> tuple[dict | None, list[str]]:
    function = getattr(cost, "estimate_runtime_s", None) if cost is not None else None
    if function is None:
        return None, []
    try:
        from dara.hardware import detect_cpu_count

        workers = detect_cpu_count()
        estimate = function(n_candidates, reflection_counts, workers, data_points=data_points)
        return {
            "low_s": estimate["low_s"], "expected_s": estimate["expected_s"],
            "high_s": estimate["high_s"], "workers": workers,
        }, [str(item) for item in estimate.get("assumptions") or []]
    except Exception:
        logger.warning("Run-time estimate failed", exc_info=True)
        return None, []


async def _estimate_submission(request: EstimateRequest) -> dict:
    started = time.perf_counter()
    mode = request.analysis_mode
    if mode not in {"reaction", "elemental", "known_mixture"}:
        raise HTTPException(status_code=400, detail="Invalid analysis mode")
    precursors = (
        list(request.precursor_formulas)
        if isinstance(request.precursor_formulas, (list, tuple))
        else _parse_string_list(request.precursor_formulas)
    )
    elements = (
        list(request.elements)
        if isinstance(request.elements, (list, tuple))
        else _parse_string_list(request.elements)
    )
    try:
        precursors, _ = split_precursor_entries(precursors)
        [Composition(p) for p in precursors]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid precursor formulas")
    try:
        elements = sorted({str(Element(element)) for element in elements})
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid element symbols")
    if not elements and precursors:
        elements = sorted(
            {str(el) for formula in precursors for el in Composition(formula).elements}
        )
    if mode in {"reaction", "known_mixture"} and not precursors:
        raise HTTPException(
            status_code=400,
            detail="This analysis mode requires precursor/known-phase formulas",
        )
    if mode == "known_mixture":
        precursors = known_phases.distinct_formulas(precursors)
    if mode == "elemental" and not elements:
        raise HTTPException(status_code=400, detail="Elemental mode requires elements")
    if not 0 <= request.mp_e_hull_cutoff <= 1:
        raise HTTPException(status_code=400, detail="MP E-hull cutoff must be 0 to 1 eV/atom")
    if not 1 <= request.max_mp_structures <= 1000:
        raise HTTPException(status_code=400, detail="MP structure limit must be 1 to 1000")
    wavelength = request.wavelength
    try:
        wavelength = float(wavelength)
    except ValueError:
        pass

    cost = _candidate_cost_module()
    min_candidates = _cost_constant(cost, "PREFILTER_MIN_CANDIDATES")
    keep_fraction = _cost_constant(cost, "PREFILTER_TYPICAL_KEEP_FRACTION")
    cutoff = request.mp_e_hull_cutoff
    limit = request.max_mp_structures
    prefilter = bool(request.candidate_prefilter)

    candidates = {
        "cod": None, "mp": None, "mp_capped": False, "total_before_dedup": None,
        "expected_after_dedup": None, "prefilter_expected_after": None, "note": None,
    }
    mp_status = "disabled"
    mp_rows: list[dict] = []
    after: int | None = None

    if mode == "reaction":
        candidates["note"] = (
            "Candidates come from the predicted product set (typically 20-60 structures)."
        )
        n_runtime = REACTION_ESTIMATE_CANDIDATES
    else:
        known = mode == "known_mixture"
        use_mp = request.use_mp and not known
        if known:
            candidates["note"] = "Known-phase mode uses only the listed formulas."

        async def count_cod():
            if not request.use_cod:
                return None
            return await asyncio.to_thread(
                structure_db.cod_candidate_rows, elements, precursors, cutoff,
                formulas_only=known,
            )

        async def query_mp():
            if not use_mp:
                return "disabled", []
            if not _mp_configured():
                return "not_configured", []
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(_mp_rows_for_estimate, elements, cutoff, limit),
                    MP_ESTIMATE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return "timeout", []
            except Exception:
                logger.warning("Materials Project estimate query failed", exc_info=True)
                return "error", []
            return "ok", rows

        cod_rows, (mp_status, mp_rows) = await asyncio.gather(count_cod(), query_mp())
        if known and cod_rows is not None:
            # Same rule as the job: a formula without a COD structure is taken from MP (this run only).
            present = {row[0] for row in cod_rows}
            absent = [f for f in precursors if known_phases.reduced_formula(f) not in present]
            if absent:
                candidates["no_cod_formulas"] = absent
                candidates["no_cod_source"] = "mp" if _mp_configured() else "none"
                if candidates["no_cod_source"] == "mp":
                    candidates["note"] += " Formulas without a COD structure add Materials Project records (not counted)."
        cod_pairs = [(row[0], row[2]) for row in cod_rows or []]
        mp_ok = mp_status == "ok"
        candidates["cod"] = len(cod_rows) if cod_rows is not None else None
        candidates["mp"] = len(mp_rows) if mp_ok else None
        candidates["mp_capped"] = bool(mp_ok and len(mp_rows) >= limit)
        total = (candidates["cod"] or 0) + (candidates["mp"] or 0)
        mp_pairs = [(row.get("formula"), row.get("spacegroup")) for row in mp_rows]
        after = min(
            total,
            max(
                round(DEDUP_KEEP_FRACTION * total),
                _unique_formula_sg(cod_pairs),
                _unique_formula_sg(mp_pairs),
            ),
        )
        candidates["total_before_dedup"] = total
        candidates["expected_after_dedup"] = after
        n_runtime = (
            max(min_candidates, round(after * keep_fraction))
            if prefilter and after > min_candidates
            else after
        )
        candidates["prefilter_expected_after"] = n_runtime

    reflections, reflection_counts = _reflection_summary(
        cost, mp_rows, wavelength, request.two_theta_max
    )
    runtime, cost_assumptions = _runtime_estimate(
        cost, n_runtime, reflection_counts, request.data_points
    )
    level = _runtime_level(runtime["expected_s"] if runtime else None)

    suggestions = []
    if level in {"long", "very_long"} and request.use_mp and request.max_mp_structures > 50:
        suggestions.append(
            "Lower 'Maximum MP structures' (for example to 50) to reduce the number of candidates."
        )
    if level == "very_long" and len(elements) >= 5:
        suggestions.append("Restrict the element list to the elements expected in crystalline phases.")
    if not prefilter and after is not None and after > min_candidates:
        suggestions.append(
            "Enable the candidate pre-filter to skip references whose strongest peaks are absent."
        )
    if level in {"long", "very_long"} and request.max_analysis_minutes is None:
        suggestions.append(
            "Set a maximum analysis time to receive the best result found within that time."
        )
    if mp_status in {"timeout", "error"}:
        suggestions.append("Materials Project could not be reached; the estimate counts COD only.")

    return {
        "analysis_mode": mode,
        "candidates": candidates,
        "reflections": reflections,
        "runtime_s": (
            {key: round(runtime[f"{key}_s"]) for key in ("low", "expected", "high")}
            if runtime else None
        ),
        "workers": runtime["workers"] if runtime else None,
        "level": level,
        "suggestions": suggestions,
        "mp_status": mp_status,
        "assumptions": cost_assumptions + [
            "MP precursor-formula references are not counted.",
            "Duplicates are estimated, not computed.",
        ],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


@router.post("/submission/estimate")
async def estimate_submission(request: EstimateRequest):
    """Estimate candidate counts and run time for the form values; never queues a job."""
    return await _estimate_submission(request)


def _warm_cod_database() -> None:
    """Load the COD index in the background so the first estimate is fast."""
    try:
        structure_db.shared_cod_database()
    except Exception:
        logger.debug("COD index warm-up failed", exc_info=True)


if "pytest" not in sys.modules and not os.environ.get("DARA_SKIP_COD_WARMUP"):
    threading.Thread(target=_warm_cod_database, name="cod-warmup", daemon=True).start()


def _counts_like(document) -> bool | None:
    """Whether the stored pattern looks like raw counts; ``None`` when unknown."""
    try:
        xrd_data = document.xrd_data
        return intensities_look_like_counts(
            xrd_data.intensities, getattr(xrd_data, "intensity_unit", None)
        )
    except Exception:
        return None


def _matching_result_index(document, refinement) -> int | None:
    """Search result with ``refinement``'s phases: same references first, else same identities."""
    names = list(refinement.lst_data.phases_results)
    results = list(getattr(document, "results", None) or [])
    exact, wanted = tuple(sorted(names)), _search_result_key(names)
    for index, (_, candidate) in enumerate(results):
        if tuple(sorted(candidate.lst_data.phases_results)) == exact:
            return index
    for index, (_, candidate) in enumerate(results):
        if _search_result_key(list(candidate.lst_data.phases_results)) == wanted:
            return index
    return None


def _matching_candidate_cifs(document, refinement) -> list[Cif]:
    """CIFs of the search result whose phase set equals ``refinement``'s phases."""
    try:
        index = _matching_result_index(document, refinement)
        if index is not None:
            return _flatten_cifs(document.results[index][0])
    except Exception:
        logger.debug("Could not match the final result to a candidate", exc_info=True)
    return []


def _final_lattice_flags(job: dict, document) -> list[dict]:
    """Refined lattice parameters near their refinement bounds (cached per task).

    Each phase is checked against the range it was actually refined with: the global
    final-refinement ``lattice_range``, overridden per phase (user-uploaded CIFs use
    ``additional_cif_params``; explicit controls use ``final_refinement_overrides``;
    computed Materials Project structures use the widened ``mp_lattice_window``).
    """
    params = getattr(document, "final_refinement_params", None) or {}
    kwargs = ((job.get("job") or {}).get("function_kwargs")) or {}
    summary = getattr(document, "candidate_summary", None)
    mp_window = (summary if isinstance(summary, dict) else {}).get("mp_lattice_window")
    mp_window = mp_window.get("range") if isinstance(mp_window, dict) else None

    def compute():
        try:
            additional = MontyDecoder().process_decoded(
                deepcopy(kwargs.get("additional_cifs") or [])
            )
        except Exception:
            additional = []
        references = (
            list(getattr(document, "input_cifs", None) or [])
            + list(additional or [])
            + _matching_candidate_cifs(document, document.final_result)
        )
        resolver = lattice_range_resolver(
            params.get("lattice_range", 0.02),
            additional,
            kwargs.get("additional_cif_params"),
            kwargs.get("final_refinement_overrides"),
            mp_window=mp_window,
        )
        return lattice_bound_flags(document.final_result, references, resolver)

    return cached_lattice_flags(job.get("uuid"), compute)


def _backfill_summary(job: dict, document, runtime) -> None:
    """Persist the results-list summary for tasks finished before it existed."""
    existing = job.get("summary")
    if isinstance(existing, dict) and existing.get("schema", 0) >= SUMMARY_SCHEMA:
        return
    try:
        summary = compact_summary(document, runtime, job)
        with get_worker_store() as store:
            current = store.query_one({"index": job["index"]})
            if current is None or current.get("uuid") != job.get("uuid"):
                return
            current["summary"] = summary
            store.update(current)
    except Exception:
        logger.warning("Could not store the result summary", exc_info=True)


@router.get("/task/{task_id}")
async def result(task_id: int):
    payload = await _result(task_id)
    if isinstance(payload, dict):
        with get_worker_store() as worker_store:
            job = worker_store.query_one({"index": task_id}) or {}
        payload["sample"] = (((job.get("job") or {}).get("metadata") or {}).get("submission_settings") or {}).get("sample")
    return payload


async def _result(task_id: int):
    # Deliberately not offloaded to a thread: montydb cannot be opened from several threads
    # at once (ConfigurationError / "Empty module name"), and every handler opens stores.
    # get the task state
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})

    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")

    job_name = job["job"]["name"]

    if job["status"] == "READY":
        return {
            "task_label": job_name,
            "status": job["status"],
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    if job["status"] in {"RUNNING", "CANCELLING", "CANCELLED"}:
        return {
            "task_label": job_name,
            "status": job["status"],
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "start_time": convert_to_local_tz(job.get("start_time", job["submitted_time"])).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    if job["status"] == "FIZZLED":
        def _stamp(key):
            value = job.get(key)
            if value is None:
                return None
            return convert_to_local_tz(value).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "task_label": job_name,
            "status": job["status"],  # FIZZLED
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "start_time": _stamp("start_time"),
            "end_time": _stamp("end_time"),
            "error_tb": job.get("error"),
            "error_summary": summarize_failure(job.get("error")),
        }

    with get_result_store() as result_store:
        d = result_store.query_one({"uuid": job["uuid"]})
    if d is None:
        raise HTTPException(status_code=404, detail="Task result not found")
    d = MontyDecoder().process_decoded(d["output"])
    payload = summarize_document(job, d)
    # Reference name -> conventional formula from its CIF (display only, e.g. NH4H2PO4 for
    # PH6NO4_...), incl. references added by versions. Here, not in summarize_document:
    # that also runs on quick-refine worker threads, which must not open the stores.
    if isinstance(payload, dict):
        payload["display_formulas"] = task_display_formulas(d, job.get("uuid"))
        payload["internal_standard"] = internal_standard_block(job, payload.get("final_result"))
    return payload


# ---------------------------------------------------------------------------
# Internal-standard (absolute) quantification
# ---------------------------------------------------------------------------
def _internal_standard_spec(record: dict | None) -> dict | None:
    """Stored standard of a task: the top-level field (set by PUT; ``None`` = removed), else the
    one saved with the submission."""
    if not isinstance(record, dict):
        return None
    if "internal_standard" in record:
        spec = record["internal_standard"]
    else:
        settings = ((record.get("job") or {}).get("metadata") or {}).get("submission_settings")
        spec = (settings or {}).get("internal_standard")
    return spec if isinstance(spec, dict) and spec.get("formula") else None


def internal_standard_block(record: dict | None, final_result: dict | None) -> dict | None:
    """``internal_standard`` JSON (design 2.4) of a final-result summary; ``None`` without a standard."""
    spec = _internal_standard_spec(record)
    if spec is None or not isinstance(final_result, dict):
        return None
    from dara.server.internal_standard import quantify

    verdicts = (final_result.get("confidence") or {}).get("phases") or {}
    phases = []
    for name, detail in (final_result.get("phase_details") or {}).items():
        detail = detail or {}
        phases.append({
            "name": name,
            "formula": confidence_module.phase_identity(name)[0],
            "weight_percent": detail.get("weight_percent"),
            "weight_esd_percent": detail.get("weight_esd_percent"),
            "weight_flag": (verdicts.get(name) or {}).get("weight_flag"),
            "refinement_level": (detail.get("refinement_status") or {}).get("level"),
        })
    try:
        return quantify(phases, spec)
    except Exception:  # noqa: BLE001
        logger.warning("Internal-standard quantification failed", exc_info=True)
        return None


@router.put("/task/{task_id}/internal-standard")
async def set_internal_standard(task_id: int, body: Any = Body(None)):
    """Set (``{"formula", "wt_percent", "phase"}``) or remove (``null``) the internal standard."""
    spec = parse_internal_standard(body)
    with get_worker_store() as store:
        current = store.query_one({"index": task_id})
        if current is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if current["status"] != "COMPLETED":
            raise HTTPException(status_code=409, detail=not_completed_detail(current["status"]))
        current["internal_standard"] = spec
        store.update(current)
    payload = await result(task_id)
    return {"spec": spec, "internal_standard": payload.get("internal_standard")}


_VERSION_ENRICHED: "OrderedDict[tuple, dict]" = OrderedDict()
_VERSION_ENRICHED_MAX = 16


def enrich_version_summary(task: dict, document, record: dict, summary):
    """Stored version summary plus the round-2026-11 fields it was saved without.

    A summary saved before the refinement status, size/strain, lattice states and weight flags
    existed is recomputed from the stored refinement (the stored record is not changed).
    """
    details = summary.get("phase_details") if isinstance(summary, dict) else None
    if not isinstance(details, dict) or all(
        isinstance(item, dict) and "refinement_status" in item for item in details.values()
    ):
        return summary
    number = record.get("version") or record.get("index")
    key = (task.get("uuid"), number)
    fresh = _VERSION_ENRICHED.get(key)
    if fresh is None:
        try:
            from dara.server import versions

            fresh = versions.build_version_summary(
                task, document, versions.load_version(task["uuid"], number)
            )
        except Exception:  # noqa: BLE001 - the stored summary is still served
            logger.warning("Could not recompute version %s summary", number, exc_info=True)
            return summary
        _VERSION_ENRICHED[key] = fresh
        while len(_VERSION_ENRICHED) > _VERSION_ENRICHED_MAX:
            _VERSION_ENRICHED.popitem(last=False)
    out = dict(summary)
    for field_name in ("phase_details", "phase_metadata", "confidence", "cautions"):
        if field_name in fresh:
            out[field_name] = deepcopy(fresh[field_name])
    return out


def _confidence_blocks(
    document, evidence_rows, final_quality, final_details, final_metadata,
    final_diagnostics, final_lattice_flags, counts_like,
):
    """Confidence JSON for the final result and each displayed candidate (never raises)."""
    none_blocks = [None] * len(evidence_rows)
    try:
        displayed = []
        for number, (index, refinement, quality, details, metadata, _) in enumerate(
            evidence_rows, start=1
        ):
            cifs = _flatten_cifs(document.results[index][0])
            displayed.append(
                confidence_module.build_evidence(
                    f"candidate:{index}", number, index, refinement, quality, details,
                    metadata, cifs=cifs,
                )
            )
        final = document.final_result
        cif_pool = list(getattr(document, "input_cifs", None) or []) + _matching_candidate_cifs(
            document, final
        )
        target = confidence_module.build_evidence(
            "final", None, None, final, final_quality, final_details, final_metadata,
            lattice_flags=final_lattice_flags, cifs=cif_pool,
        )
        kwargs = dict(
            counts_like=counts_like,
            search_summary=getattr(document, "search_summary", None),
            instrument_profile=confidence_module.instrument_profile_of(document),
        )
        final_block = confidence_module.evaluate_confidence(
            target, displayed,
            missing_peaks=(final_diagnostics.get("counts") or {}).get("missing"),
            missing_summary=final_diagnostics, **kwargs,
        )
        blocks = [
            confidence_module.evaluate_confidence(
                evidence, displayed,
                missing_peaks=(row[5].get("counts") or {}).get("missing"),
                missing_summary=row[5], **kwargs,
            )
            for evidence, row in zip(displayed, evidence_rows)
        ]
        return final_block, blocks
    except Exception:
        logger.warning("Confidence could not be computed", exc_info=True)
        return None, none_blocks


def _mass_balance_amounts(job: dict, document) -> tuple[list[dict] | None, str | None]:
    """Precursor amounts from the submission, else from the pattern file name (never raises)."""
    try:
        raw_job = job.get("job") or {}
        saved = (raw_job.get("metadata") or {}).get("submission_settings") or {}
        return mass_balance_module.resolve_amounts(
            saved.get("precursor_amounts"),
            getattr(document, "precursors", None) or [],
            saved.get("filename") or raw_job.get("name"),
            partial=saved.get("analysis_mode") == "known_mixture",
        )
    except Exception:
        logger.debug("Precursor amounts could not be resolved", exc_info=True)
        return None, None


def _mass_balance_block(
    amounts, source, weight_percent, phase_details, predict_kwargs, cautions, confidence
) -> dict:
    """Mass-balance JSON for one refinement; appends its caution and confidence reason."""
    block = mass_balance_module.mass_balance(
        amounts,
        weight_percent,
        weight_esd={
            name: (details or {}).get("weight_esd_percent")
            for name, details in (phase_details or {}).items()
        },
        open_element=(predict_kwargs or {}).get("open_elem"),
        source=source or "input",
    )
    message = mass_balance_module.mass_balance_message(block)
    if message:
        cautions.append({
            "code": "mass_balance",
            "level": "warning" if block["level"] == "warning" else "info",
            "message": message,
            "phase": None,
        })
        mass_balance_module.apply_to_confidence(confidence, block)
    return block


def _weight_percent(refinement) -> dict:
    try:
        return {
            phase: round(fraction * 100, 4)
            for phase, fraction in refinement.get_phase_weights().items()
        }
    except (TypeError, ValueError, ZeroDivisionError):
        return {}


def summarize_document(job: dict, document, *, persist_summary: bool = True) -> dict:
    """Return the COMPLETED-task payload served by GET /api/task/{id}.

    ``job`` needs ``uuid``, ``index``, ``status``, ``submitted_time``, ``start_time``,
    ``end_time`` and ``job`` (``Job.as_dict()`` form: ``name``, ``function_kwargs``,
    ``metadata``). With ``persist_summary=False`` the worker store is never touched.
    Generated texts (verdicts, cautions, labels) name phases by the conventional formulas of
    the document's CIFs; this needs no store (a version's document view carries its CIFs).
    """
    from dara.plot import display_formulas_scope

    with display_formulas_scope(document_display_formulas(document)):
        return _summarize_document(job, document, persist_summary=persist_summary)


def _no_result_payload(job: dict, document) -> dict:
    """200 payload of a COMPLETED task whose search found no phase combination (no final fit)."""
    stamps = {}
    for key in ("submitted_time", "start_time", "end_time"):
        try:
            stamps[key] = convert_to_local_tz(job[key]).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            stamps[key] = None
    runtime = runtime_seconds(job.get("start_time"), job.get("end_time"))
    return {
        "status": job["status"],
        "task_label": job["job"]["name"],
        "no_result": True,
        "message": "The search finished without a phase combination that fits the pattern.",
        "search_summary": getattr(document, "search_summary", None) or {},
        "candidate_summary": getattr(document, "candidate_summary", None) or {},
        "runtime_s": runtime,
        "runtime": runtime,
        "final_result": None,
        "all_results": [],
        "precursors": getattr(document, "precursors", None),
        "elements": getattr(document, "elements", None),
        "submitted_on": stamps["submitted_time"],
        "start_time": stamps["start_time"],
        "end_time": stamps["end_time"],
    }


def _reference_pool(document, refinement) -> list:
    """Reference CIFs that may belong to ``refinement``: input CIFs and the matching candidate's."""
    return list(getattr(document, "input_cifs", None) or []) + _matching_candidate_cifs(
        document, refinement
    )


def _summarize_document(job: dict, document, *, persist_summary: bool = True) -> dict:
    d = document
    if d.final_result is None:
        return _no_result_payload(job, d)
    counts_like = _counts_like(d)
    try:
        peak_context = _peak_context(d, job_key=job.get("uuid"))
    except Exception:  # classification is optional; fall back to the legacy diagnostics
        logger.debug("Peak context could not be built", exc_info=True)
        peak_context = None

    all_results = []
    evidence_rows = []
    spec_resolver = _phase_spec_resolver(job, d)

    def b1_spec(name):
        return spec_resolver(name, "b1")

    def screening_rwp(index):
        if d.screening_rwps and index < len(d.screening_rwps):
            return d.screening_rwps[index]
        return d.results[index][1].lst_data.rwp

    # A search can reach one phase hypothesis in another insertion order or with another
    # database record of the same compound (same formula and space group). Each group is
    # shown once by its lowest-Rwp result; the others stay addressable via merged_results.
    # Polymorphs (different space group) remain separate results.
    for group in candidate_groups(d):
        result_index = group.representative
        result = d.results[result_index]
        result_weight_percent = _weight_percent(result[1])
        grouped_phases = deepcopy(d.grouped_phases[result_index]) if d.grouped_phases else None
        phases = [[cif.filename for cif in cifs] for cifs in result[0]]
        highlighted_phases = list(result[1].lst_data.phases_results)
        phase_metadata = _refined_phase_metadata(result[1])
        diagnostics = _classified_peak_diagnostics(
            d,
            result[1],
            d.missing_peaks[result_index] if d.missing_peaks else None,
            d.extra_peaks[result_index] if d.extra_peaks else None,
            (
                d.intensity_mismatch_peaks[result_index]
                if getattr(d, "intensity_mismatch_peaks", None)
                else None
            ),
            context=peak_context,
        )

        if not grouped_phases:  # for backward compatibility
            grouped_phases = []
            for phases_ in phases:
                grouped_phase = get_compositional_clusters(list(phases_))
                grouped_phase_with_head = [
                    (get_head_of_compositional_cluster(cluster), cluster)
                    for cluster in grouped_phase
                ]
                grouped_phases.append(grouped_phase_with_head)

        # convert composition into formula
        for i in range(len(grouped_phases)):
            for j in range(len(grouped_phases[i])):
                grouped_phases[i][j] = (
                    grouped_phases[i][j][0]
                    .reduced_composition.to_html_string()
                    .replace("<sub>1</sub>", ""),
                    grouped_phases[i][j][1],
                )

        fit_quality = _fit_quality(result[1])
        phase_details = _phase_refinement_details(
            result[1], b1_spec, spec=spec_resolver,
            references=_flatten_cifs(result[0]) + list(getattr(d, "input_cifs", None) or []),
        )
        shown = set(highlighted_phases)
        merged_results = []
        for member in group.members[1:]:
            refinement = d.results[member][1]
            names = list(refinement.lst_data.phases_results)
            merged_results.append({
                "source_index": member,
                "rwp": refinement.lst_data.rwp,
                "screening_rwp": screening_rwp(member),
                "highlighted_phases": names,
                "phase_metadata": _refined_phase_metadata(refinement),
                "weight_percent": _weight_percent(refinement),
                "fit_quality": _fit_quality(refinement),
                "differing_phases": [name for name in names if name not in shown],
            })
        all_results.append(
            {
                "rwp": result[1].lst_data.rwp,
                "screening_rwp": screening_rwp(result_index),
                "is_final_refined": bool(d.screening_rwps),
                "phases": phases,
                "highlighted_phases": highlighted_phases,
                "phase_metadata": phase_metadata,
                "fit_quality": fit_quality,
                "phase_details": phase_details,
                "peak_diagnostics": diagnostics,
                "cautions": [],  # filled below, once the phase confidence is known
                "grouped_phases": grouped_phases,
                "weight_percent": result_weight_percent,
                "source_index": result_index,
                "equivalent_result_count": len(group.members),
                "merged_results": merged_results,
            }
        )
        evidence_rows.append(
            (result_index, result[1], fit_quality, phase_details, phase_metadata, diagnostics)
        )

    start_time = convert_to_local_tz(job["start_time"])
    end_time = convert_to_local_tz(job["end_time"])
    runtime = (end_time - start_time).total_seconds()
    predict_kwargs = d.predict_kwargs or {}
    weight_percent = _weight_percent(d.final_result)
    final_phase_metadata = _refined_phase_metadata(d.final_result)
    final_peak_diagnostics = _classified_peak_diagnostics(
        d, d.final_result, context=peak_context
    )
    final_fit_quality = _fit_quality(d.final_result)
    final_phase_details = _phase_refinement_details(
        d.final_result, b1_spec, spec=spec_resolver, references=_reference_pool(d, d.final_result)
    )
    final_lattice_flags = _final_lattice_flags(job, d)
    final_confidence, candidate_confidence = _confidence_blocks(
        d, evidence_rows, final_fit_quality, final_phase_details, final_phase_metadata,
        final_peak_diagnostics, final_lattice_flags, counts_like,
    )
    # the cautions about uncertain amounts and duplicate phases read the phase confidence
    final_cautions = fit_cautions(
        final_fit_quality,
        final_phase_details,
        final_peak_diagnostics,
        counts_like=counts_like,
        lattice_flags=final_lattice_flags,
        phase_confidence=(final_confidence or {}).get("phases"),
    )
    for entry, row, block in zip(all_results, evidence_rows, candidate_confidence):
        entry["cautions"] = fit_cautions(
            entry["fit_quality"], entry["phase_details"], row[5], counts_like=counts_like,
            phase_confidence=(block or {}).get("phases"),
        )
    for block, cautions in [(final_confidence, final_cautions)] + [
        (candidate_confidence[i], all_results[i]["cautions"]) for i in range(len(all_results))
    ]:
        if not block:
            continue
        for extra in (
            displacement_caution(block.get("sample_displacement")),
            broad_background_caution(block.get("broad_background")),
        ):
            if extra is not None:
                cautions.append(extra)
    mass_amounts, mass_source = _mass_balance_amounts(job, d)
    final_mass_balance = _mass_balance_block(
        mass_amounts, mass_source, weight_percent, final_phase_details, predict_kwargs,
        final_cautions, final_confidence,
    )
    for i, entry in enumerate(all_results):
        entry["mass_balance"] = _mass_balance_block(
            mass_amounts, mass_source, entry["weight_percent"], entry["phase_details"],
            predict_kwargs, entry["cautions"], candidate_confidence[i],
        )
    # Search-level cautions describe the whole run, so they belong to the final result only.
    final_cautions.extend(
        search_cautions(
            getattr(d, "candidate_summary", None), getattr(d, "search_summary", None)
        )
    )
    for entry, block in zip(all_results, candidate_confidence):
        if block is not None:
            entry["confidence"] = block
    if persist_summary:
        _backfill_summary(job, d, runtime)
    return {
        "status": job["status"],
        "task_label": job["job"]["name"],
        "best_rwp": d.best_rwp,
        "final_result": {
            "rwp": d.final_result.lst_data.rwp,
            "phases": list(d.final_result.lst_data.phases_results),
            "weight_percent": weight_percent,
            "phase_metadata": final_phase_metadata,
            "fit_quality": final_fit_quality,
            "phase_details": final_phase_details,
            "peak_diagnostics": final_peak_diagnostics,
            "cautions": final_cautions,
            "mass_balance": final_mass_balance,
            **({"confidence": final_confidence} if final_confidence is not None else {}),
        },
        "all_results": all_results,
        "duplicate_results_removed": len(d.results) - len(all_results),
        "precursors": d.precursors,
        "elements": getattr(d, "elements", None),
        "analysis_mode": getattr(d, "analysis_mode", "reaction" if d.phase_predictor else "elemental"),
        "candidate_sources": getattr(d, "candidate_sources", []),
        "candidate_summary": getattr(d, "candidate_summary", {}),
        "search_summary": getattr(d, "search_summary", {}),
        "temperature": None if predict_kwargs.get("temp") is None else predict_kwargs["temp"] - 273,
        "use_rxn_predictor": d.phase_predictor is not None,
        "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": runtime,
        "additional_search_options": d.search_kwargs,
        "final_refinement_params": d.final_refinement_params,
        "instrument_profile_details": job["job"].get("metadata", {}).get("instrument_profile_details"),
    }
    raise HTTPException(status_code=404, detail="No phases identified in the pattern")


def _safe_download_name(value: str) -> str:
    """Return a filesystem-safe stem for a downloaded file (archive, table or CIF).

    Directories and a trailing pattern or CIF extension are removed (other dots stay, so
    ``Nb16.8O42_55_(cod_2106534)`` keeps its name); a trailing ``-None`` is dropped.
    """
    return safe_download_stem(value)


def _phase_identity(filename: str) -> str:
    """Identity of a reference for candidate grouping: reduced formula + database space group.

    Falls back to the name itself when either is unknown, so references that cannot be
    identified are never merged.
    """
    stem = str(filename)
    if stem.lower().endswith(".cif"):
        stem = stem[:-4]
    formula, number = confidence_module.phase_identity(stem)
    if formula is None or number is None:
        return f"name:{stem}"
    return f"{formula}_{number}"


def _search_result_key(phases: list[str]) -> tuple[str, ...]:
    """Canonicalize a selected phase hypothesis independent of insertion order and record."""
    return tuple(sorted(_phase_identity(phase) for phase in phases))


@dataclass
class CandidateGroup:
    key: tuple[str, ...]
    representative: int  # source index with the lowest Rwp (tie: lowest index)
    members: list[int]  # all source indices, representative first, then by Rwp


def _rwp_sort_value(refinement) -> float:
    try:
        value = float(refinement.lst_data.rwp)
    except (AttributeError, TypeError, ValueError):
        return float("inf")
    return value if np.isfinite(value) else float("inf")


def candidate_groups(document) -> list[CandidateGroup]:
    """Displayed candidates: search results with the same phase identities, in first-seen order.

    Results whose phases share reduced formula and space group (they differ only in phase order
    or database record) form one group shown by its lowest-Rwp member. A document may pin a
    source index (``pinned_result_index``, set by version views) as its group's representative.
    """
    order: list[tuple[str, ...]] = []
    members: dict[tuple[str, ...], list[int]] = {}
    results = list(getattr(document, "results", None) or [])
    for index, (_, refinement) in enumerate(results):
        key = _search_result_key(list(refinement.lst_data.phases_results))
        if key not in members:
            order.append(key)
            members[key] = []
        members[key].append(index)
    pinned = getattr(document, "pinned_result_index", None)
    groups = []
    for key in order:
        ranked = sorted(members[key], key=lambda i: (_rwp_sort_value(results[i][1]), i))
        if pinned in ranked:
            ranked.remove(pinned)
            ranked.insert(0, pinned)
        groups.append(CandidateGroup(key, ranked[0], ranked))
    return groups


def group_of(document, source_index: int) -> tuple[int, CandidateGroup] | None:
    """``(displayed 1-based number, group)`` of the group holding ``source_index``."""
    for number, group in enumerate(candidate_groups(document), start=1):
        if source_index in group.members:
            return number, group
    return None


def displayed_number_of(document, source_index: int) -> int | None:
    found = group_of(document, source_index)
    return found[0] if found else None


def _flatten_cifs(value) -> list[Cif]:
    """Flatten the candidate CIF grouping used in stored search results."""
    if isinstance(value, Cif):
        return [value]
    if isinstance(value, (list, tuple)):
        return [cif for item in value for cif in _flatten_cifs(item)]
    return []


def export_meta(
    task_id, job, document, refinement, selection=None, *, references=None, spec=None, details=None,
    structure_models=None,
) -> dict:
    """``meta`` of ``export.bundles.build_bundle`` / ``refined_cif_text`` for one refinement.

    Per-phase refinement status, lattice states and effective refinement specs come from the
    listing; ``selection`` is ``None`` for the final fit, ``"version:<n>"`` or a text.
    ``structure_models`` (``versions.structure_models(record)``) adds each structure-refined
    phase's spec so its CIF carries the refined B, e.s.d. and a matching special-details note.
    """
    spec = spec or _phase_spec_resolver(job, document)
    if details is None:
        try:
            details = _phase_refinement_details(refinement, spec=spec, references=references)
        except Exception:  # noqa: BLE001 - the CIFs are still written, without the extra details
            logger.debug("Export details unavailable", exc_info=True)
            details = {}
    phases = {}
    for name, item in details.items():
        phases[name] = {
            "weight_percent": item.get("weight_percent"),
            "weight_esd_percent": item.get("weight_esd_percent"),
            "refinement_status": item.get("refinement_status"),
            "lattice_states": {
                key: value["state"]
                for key, value in (item.get("lattice_parameters") or {}).items()
                if value.get("state")
            },
            "effective_params": {
                key: spec(name, key) for key in ("lattice_range", "k1", "k2", "b1")
            },
            "structure_spec": (structure_models or {}).get(name),
        }
    return {
        "task_id": task_id,
        "selection": selection,
        "pattern_name": ((job or {}).get("job") or {}).get("name"),
        "phases": phases,
    }


def _lattice_by_phase(refinement, references, meta=None) -> dict:
    """Refined lattice of every phase (reference lattice for a fixed one) for the peak list."""
    from dara.export.refined_cif import phase_structure

    lattices = {}
    for name in refinement.lst_data.phases_results:
        reference = next((cif for cif in references or [] if cif_stem(cif) == name), None)
        try:
            lattices[name] = phase_structure(
                name, refinement, reference, ((meta or {}).get("phases") or {}).get(name)
            )[0].lattice
        except Exception:  # noqa: BLE001 - the peak list falls back to Bragg's law
            lattices[name] = None
    return lattices


def peak_table(document, refinement, references=None, meta=None) -> list[dict]:
    """Rows of the peak list of ``refinement`` (see ``dara.export.peak_list``)."""
    from dara.export.peak_list import peak_rows

    wavelength = (getattr(document, "search_kwargs", None) or {}).get("wavelength", "Cu")
    return peak_rows(
        refinement, wavelength=wavelength,
        lattice_by_phase=_lattice_by_phase(refinement, references, meta),
    )


def _write_refinement_files(
    output: zipfile.ZipFile,
    refinement,
    folder: str,
    reference_cifs: list[Cif],
    missing_peaks=None,
    extra_peaks=None,
    *,
    document=None,
    meta: dict | None = None,
    peak_list: bool = False,
) -> None:
    """Write one refinement hypothesis into an open result archive.

    ``meta`` (see ``export_meta``) feeds the refined CIFs; ``peak_list`` adds ``peak_list.csv``.
    """
    folder = f"{folder.strip('/')}/" if folder else ""
    phase_parameters = {
        name: phase.model_dump(by_alias=True, exclude={"atom_positions_string"})
        for name, phase in refinement.lst_data.phases_results.items()
    }
    output.writestr(
        f"{folder}phase_parameters.json",
        json.dumps(phase_parameters, ensure_ascii=False, indent=2, default=str),
    )

    plot_data = refinement.plot_data
    csv_buffer = StringIO(newline="")
    writer = csv.writer(csv_buffer)
    phase_names = list(plot_data.structs)
    writer.writerow(
        ["two_theta", "observed", "calculated", "background", "difference"]
        + phase_names
    )
    for idx, angle in enumerate(plot_data.x):
        writer.writerow(
            [
                angle,
                plot_data.y_obs[idx],
                plot_data.y_calc[idx],
                plot_data.y_bkg[idx],
                plot_data.y_obs[idx] - plot_data.y_calc[idx],
            ]
            + [plot_data.structs[name][idx] for name in phase_names]
        )
    output.writestr(f"{folder}refinement.csv", csv_buffer.getvalue())

    raw_lst = getattr(refinement.lst_data, "raw_lst", None)
    if raw_lst:
        output.writestr(f"{folder}refinement.lst", raw_lst)

    metrics = getattr(refinement, "refinement_metrics", None)
    figure = (
        refinement.visualize()
        if metrics is not None
        else visualize(
            result=refinement,
            missing_peaks=missing_peaks,
            extra_peaks=extra_peaks,
        )
    )
    output.writestr(
        f"{folder}plot.html",
        figure.to_html(full_html=True, include_plotlyjs="cdn"),
    )

    # refined CIFs; a phase that cannot be written as refined gets its reference CIF in
    # reference_cifs/ and a phase without any CIF is listed (never skipped silently)
    from dara.export.bundles import _phase_meta, safe_name
    from dara.export.refined_cif import CifNotExportable, refined_cif_text

    not_exported: list[tuple[str, str]] = []
    for phase_name in refinement.lst_data.phases_results:
        reference = next((cif for cif in reference_cifs if cif_stem(cif) == phase_name), None)
        try:
            cif_text, cif_folder = refined_cif_text(
                phase_name, refinement, reference_cif=reference,
                meta=_phase_meta(refinement, document, meta, phase_name),
            )
        except CifNotExportable as exc:
            not_exported.append((phase_name, str(exc)))
            continue
        output.writestr(f"{folder}{cif_folder}/{safe_name(phase_name)}.cif", cif_text)
    if not_exported:
        output.writestr(
            f"{folder}phases_not_exported.txt",
            "Phases without a CIF in this export\n"
            + "".join(f"{name}: {reason}\n" for name, reason in not_exported),
        )

    if peak_list:
        try:
            from dara.export.peak_list import peak_csv_bytes

            rows = peak_table(document, refinement, reference_cifs, meta)
            if rows:
                output.writestr(f"{folder}peak_list.csv", peak_csv_bytes(rows))
        except Exception:  # noqa: BLE001
            logger.warning("Could not write peak_list.csv", exc_info=True)


def _make_result_archive(
    task_id: int,
    document,
    summary: dict,
    result_index: int | None = None,
    review_selection=None,
    *,
    job: dict | None = None,
    adopted: tuple | None = None,
) -> BytesIO:
    """Build a ZIP for all hypotheses or one selected search result.

    ``adopted`` is ``(selection, refinement, resolved, record)`` of the adopted re-refinement
    (``versions.resolve_refinement`` result and its stored record): its files go to
    ``adopted_version_<n>/`` and the table gets its rows.
    """
    from dara.server import versions

    adopted_selection = adopted[0] if adopted else None
    adopted_rwp = adopted[1].lst_data.rwp if adopted else None
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        if result_index is None:
            export_summary = summary
            description = (
                "final/: final re-refinement\n"
                "search_results/result_N/: every ranked search hypothesis\n"
                "search_results/result_N/alternative_references/: the same phases refined "
                "with other database records (when present)\n"
            )
        else:
            export_summary = {
                key: value for key, value in summary.items() if key != "all_results"
            }
            displayed_index, displayed_result = next(
                (
                    (index, result)
                    for index, result in enumerate(summary["all_results"])
                    if result.get("source_index", index) == result_index
                    or any(
                        member.get("source_index") == result_index
                        for member in result.get("merged_results") or []
                    )
                ),
                (result_index, None),
            )
            if displayed_result is None:
                raise ValueError("Search result is not part of the displayed results")
            export_summary["search_result_number"] = displayed_index + 1
            export_summary["search_result"] = displayed_result
            description = f"Selected search hypothesis: {displayed_index + 1}\n"
            representative = displayed_result.get("source_index", displayed_index)
            if representative != result_index:
                # Same phases as the displayed result, refined with other database records
                # (or reached in another phase order).
                from dara.server.versions import merged_label

                export_summary["reference_variant_of"] = representative
                member = next(
                    (
                        item
                        for item in displayed_result.get("merged_results") or []
                        if item.get("source_index") == result_index
                    ),
                    {},
                )
                label = merged_label(
                    displayed_index + 1, result_index, member.get("differing_phases")
                )
                description = f"Selected search hypothesis: {label}\n"

        export_summary = {**export_summary, "adopted": adopted_selection, "adopted_rwp": adopted_rwp}
        if adopted:
            description = (
                f"Adopted fit: version {adopted[0].split(':', 1)[1]} (Rwp {adopted_rwp:.2f}%). "
                f"Files of the adopted fit are in adopted_{adopted[0].replace(':', '_')}/.\n"
                + description
            )
        output.writestr(
            "summary.json",
            json.dumps(export_summary, ensure_ascii=False, indent=2, default=str),
        )
        output.writestr(
            "README.txt",
            "DARA result export\n"
            f"Task ID: {task_id}\n"
            f"{description}"
            "summary.json: task settings, phases, and Rwp values\n"
            "pattern.xy: submitted diffraction pattern\n"
            "results_table.csv: one row per candidate and phase (weight %, lattice "
            "parameters with their state, size and microstrain, refinement status, fit "
            "statistics, cautions); present in the all-results archive. "
            "sample_displacement_eps2 is the BGMN EPS2 value (sample displacement in "
            "goniometer-radius units of the BGMN instrument file); microstrain is sqrt(k2) in %.\n"
            "Each result folder contains refinement.csv, phase_parameters.json, "
            "refinement.lst (raw BGMN listing), plot.html, and refined_cifs/ (refined "
            "lattice with e.s.d.; atomic positions and occupancies are those of the reference "
            "unless _refine_special_details says otherwise). The final and adopted folders "
            "also hold peak_list.csv.\n"
            "reference_cifs/, when present, holds the unrefined reference CIF of a phase "
            "whose refined structure could not be written; phases_not_exported.txt lists "
            "phases without any CIF.\n"
            "plot.html loads Plotly from its public CDN when opened.\n",
        )

        if result_index is None:
            try:
                adopted_record = None
                if adopted:
                    adopted_record = dict(adopted[3])
                    adopted_record["summary"] = enrich_version_summary(
                        job or {}, document, adopted_record, adopted_record.get("summary")
                    )
                table_rows = task_table_rows(
                    task_id, job or {"review": {"selection": review_selection}}, summary,
                    adopted_record,
                )
                output.writestr("results_table.csv", rows_to_csv(table_rows, TABLE_COLUMNS))
            except Exception:
                logger.warning("Could not write results_table.csv", exc_info=True)

        xy_columns = [document.xrd_data.angles, document.xrd_data.intensities]
        if document.xrd_data.errors is not None:
            xy_columns.append(document.xrd_data.errors)
        xy_buffer = StringIO()
        np.savetxt(xy_buffer, np.column_stack(xy_columns), fmt="%.8f")
        output.writestr("pattern.xy", xy_buffer.getvalue())

        if result_index is not None:
            candidate_cifs, candidate_result = document.results[result_index]
            candidate_references = _flatten_cifs(candidate_cifs)
            _write_refinement_files(
                output,
                candidate_result,
                "",
                candidate_references,
                document.missing_peaks[result_index] if document.missing_peaks else None,
                document.extra_peaks[result_index] if document.extra_peaks else None,
                document=document,
                meta=export_meta(
                    task_id, job, document, candidate_result, f"candidate {result_index + 1}",
                    references=candidate_references,
                ),
                peak_list=True,
            )
        else:
            if document.final_result is not None:
                final_references = _reference_pool(document, document.final_result)
                _write_refinement_files(
                    output,
                    document.final_result,
                    "final",
                    final_references,
                    document=document,
                    meta=export_meta(
                        task_id, job, document, document.final_result, references=final_references
                    ),
                    peak_list=True,
                )
            if adopted:
                resolved = adopted[2]
                _write_refinement_files(
                    output,
                    adopted[1],
                    f"adopted_{adopted[0].replace(':', '_')}",
                    resolved["cifs"],
                    document=document,
                    meta=export_meta(
                        task_id, job, document, adopted[1], adopted[0], references=resolved["cifs"],
                        spec=_params_spec_resolver(
                            (resolved.get("record") or {}).get("effective_phase_params")
                        ),
                        structure_models=versions.structure_models(resolved.get("record")),
                    ),
                    peak_list=True,
                )
            displayed_results = summary.get("all_results", [])
            for displayed_idx, displayed_result in enumerate(displayed_results):
                idx = displayed_result.get("source_index", displayed_idx)
                candidate_cifs, candidate_result = document.results[idx]
                candidate_references = _flatten_cifs(candidate_cifs)
                _write_refinement_files(
                    output,
                    candidate_result,
                    f"search_results/result_{displayed_idx + 1}",
                    candidate_references,
                    document.missing_peaks[idx] if document.missing_peaks else None,
                    document.extra_peaks[idx] if document.extra_peaks else None,
                    document=document,
                    meta=export_meta(
                        task_id, job, document, candidate_result, f"candidate {displayed_idx + 1}",
                        references=candidate_references,
                    ),
                )
                merged = displayed_result.get("merged_results") or []
                for number, member in enumerate(merged, start=1):
                    member_idx = member["source_index"]
                    member_cifs, member_result = document.results[member_idx]
                    member_references = _flatten_cifs(member_cifs)
                    _write_refinement_files(
                        output,
                        member_result,
                        f"search_results/result_{displayed_idx + 1}/alternative_references/ref_{number}",
                        member_references,
                        document.missing_peaks[member_idx] if document.missing_peaks else None,
                        document.extra_peaks[member_idx] if document.extra_peaks else None,
                        document=document,
                        meta=export_meta(
                            task_id, job, document, member_result,
                            f"candidate {displayed_idx + 1} (other reference)",
                            references=member_references,
                        ),
                    )

    archive.seek(0)
    return archive


@router.get("/task/{task_id}/download")
async def download_result(task_id: int, idx: int | None = Query(None, ge=0)):
    """Download all hypotheses or one selected search result as a ZIP archive."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if job["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail=not_completed_detail(job["status"]))

    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": job["uuid"]})
    if stored is None:
        raise HTTPException(status_code=404, detail="Task result not found")
    document = MontyDecoder().process_decoded(stored["output"])
    if idx is not None and idx >= len(document.results or []):
        raise HTTPException(status_code=404, detail="Search result index out of range")
    summary = await result(task_id)
    adopted = _resolve_adopted(document, job) if idx is None else None
    archive = _make_result_archive(
        task_id,
        document,
        summary,
        result_index=idx,
        review_selection=(job.get("review") or {}).get("selection"),
        job=job,
        adopted=adopted,
    )
    kind = "all_results" if idx is None else f"search_result_{idx + 1}"
    filename = download_name(task_id, job["job"]["name"], kind)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _adopted_version_record(job: dict) -> dict | None:
    """Stored record of the adopted re-refinement (``version:<n>``; never a quick check)."""
    selection = (job.get("review") or {}).get("selection")
    if not (isinstance(selection, str) and selection.startswith("version:")):
        return None
    from dara.server import versions

    try:
        parsed = versions.parse_selection(selection)
        if parsed == "best":
            return None
        record = versions.load_version(job["uuid"], parsed[1], light=True)
    except HTTPException:
        return None  # the adopted version was deleted
    return None if versions.is_quick_check(record) else record


def _resolve_adopted(document, job: dict) -> tuple | None:
    """``(selection, refinement, resolved, record)`` of the adopted version, or ``None``."""
    record = _adopted_version_record(job)
    if record is None:
        return None
    from dara.server import versions

    selection = job["review"]["selection"]
    try:
        refinement, resolved = versions.resolve_refinement(
            document, job, versions.parse_selection(selection)
        )
    except HTTPException:
        return None
    return selection, refinement, resolved, record


@router.get("/task/{task_id}/table.csv")
async def download_task_table(task_id: int):
    """Flat CSV with one row per (candidate, phase) of a completed task."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if job["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail=not_completed_detail(job["status"]))
    summary = await result(task_id)
    adopted = _adopted_version_record(job)
    if adopted is not None and isinstance(adopted.get("summary"), dict):
        from dara.server import quick_refine

        adopted = dict(adopted)
        adopted["summary"] = enrich_version_summary(
            job, await quick_refine.load_document(job["uuid"]), adopted, adopted["summary"]
        )
    rows = task_table_rows(task_id, job, summary, adopted)
    filename = download_name(task_id, job["job"]["name"], "table", ext="csv")
    return _csv_response(rows_to_csv(rows, TABLE_COLUMNS), filename)


async def _peak_rows_of(task_id: int, sel: str | None):
    """``(task, rows, wavelength, selection)`` of the peak list of one selection."""
    from dara.server import review_router, versions

    task, document = await review_router._load_completed_task(task_id)
    review = review_router._review_metadata(task)
    selection = review_router._report_selection(None, False, review, sel)
    review_router._refuse_quick_check(task, selection)
    refinement, meta = versions.resolve_refinement(document, task, selection)
    try:
        rows = peak_table(document, refinement, meta["cifs"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Peak list of task %s failed", task_id, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Peak list failed: {exc}") from exc
    wavelength = (getattr(document, "search_kwargs", None) or {}).get("wavelength", "Cu")
    return task, rows, wavelength, selection


@router.get("/task/{task_id}/peaks")
async def task_peaks(task_id: int, sel: str | None = Query(None)):
    """Reflection list (hkl, 2theta, d, intensity, FWHM) of ``sel``: ``best``, a candidate index
    or ``version:<n>`` (default: the adopted fit)."""
    from dara.export.peak_list import COLUMNS

    _, rows, wavelength, selection = await _peak_rows_of(task_id, sel)
    return {
        "columns": list(COLUMNS), "rows": rows, "wavelength": wavelength, "selection": selection,
    }


@router.get("/task/{task_id}/peaks.csv")
async def task_peaks_csv(task_id: int, sel: str | None = Query(None)):
    from dara.export.peak_list import peak_csv_bytes

    task, rows, _, selection = await _peak_rows_of(task_id, sel)
    filename = download_name(task_id, task["job"]["name"], "peaks", selection, "csv")
    return _csv_response(peak_csv_bytes(rows), filename)


def _parse_task_ids(ids: str | None) -> list[int]:
    """Parse ``1,2,3`` into unique ids in the given order (1 to 50 ids)."""
    parts = [part.strip() for part in (ids or "").split(",") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="Provide task ids, e.g. ids=1,2,3")
    parsed: list[int] = []
    for part in parts:
        if not re.fullmatch(r"\d{1,12}", part):
            raise HTTPException(status_code=400, detail=f"Invalid task id: {part[:20]}")
        value = int(part)
        if value not in parsed:
            parsed.append(value)
    if len(parsed) > 50:
        raise HTTPException(status_code=400, detail="Export at most 50 tasks at a time")
    return parsed


@router.get("/tasks/table.csv")
async def download_tasks_table(ids: str = Query(...)):
    """Final-result rows for several tasks; unfinished tasks get a status row."""
    task_ids = _parse_task_ids(ids)
    entries = []
    for task_id in task_ids:
        with get_worker_store() as worker_store:
            job = worker_store.query_one({"index": task_id})
        summary = None
        if job is not None and job["status"] == "COMPLETED":
            try:
                summary = await result(task_id)
            except HTTPException:
                summary = None
        entries.append((task_id, job, summary))
    rows = multi_task_rows(entries)
    return _csv_response(
        rows_to_csv(rows, TABLE_COLUMNS + MULTI_TASK_EXTRA_COLUMNS), "dara_tasks_table.csv"
    )


@router.delete("/task/{task_id}")
async def delete_result(task_id: int):
    """Delete a finished/failed task and its stored analysis document."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
        if job is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if job["status"] in {"READY", "RUNNING", "CANCELLING"}:
            raise HTTPException(status_code=409, detail="A queued or running task cannot be deleted")
        worker_store.remove_docs({"index": task_id})

    with get_result_store() as result_store:
        result_store.remove_docs({"uuid": job["uuid"]})
    _plot_json.cache_clear()
    # Quick re-refinements: cancel running/queued jobs first, then drop versions and caches.
    from dara.server import quick_refine, versions

    try:
        quick_refine.discard_task(job["uuid"])
    except ValueError:
        pass  # legacy non-UUID record: nothing was cached for it
    except Exception:  # noqa: BLE001 - leftovers must not turn a successful delete into an error
        logger.warning("Could not clean the quick-refine cache of task %s", task_id, exc_info=True)
    try:
        versions.delete_task_versions(job["uuid"])
    except Exception:  # noqa: BLE001
        logger.warning("Could not delete the versions of task %s", task_id, exc_info=True)
    from dara.server.task_control_router import task_directory
    try:
        checkpoint_dir = task_directory(job["uuid"])
    except ValueError:
        checkpoint_dir = None  # Legacy non-UUID records have no checkpoints.
    if checkpoint_dir is not None and checkpoint_dir.is_dir() and not checkpoint_dir.is_symlink():
        # UUID validation fixes the exact target below the checkpoint parent.
        shutil.rmtree(checkpoint_dir)
    return {"message": "deleted", "task_id": task_id}


@lru_cache(maxsize=24)
def _plot_json(uuid: str, idx: int | None) -> str:
    """Build an immutable completed-result plot once per server process."""
    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": uuid})
    if stored is None:
        raise HTTPException(status_code=404, detail="Task not found")
    document = MontyDecoder().process_decoded(stored["output"])
    if idx is None:
        if document.final_result is None:
            raise HTTPException(status_code=404, detail="Final result not found")
        figure = document.final_result.visualize()
    elif 0 <= idx < len(document.results or []):
        result = document.results[idx][1]
        missing_peaks = document.missing_peaks[idx] if document.missing_peaks else None
        extra_peaks = document.extra_peaks[idx] if document.extra_peaks else None
        figure = (
            result.visualize()
            if getattr(result, "refinement_metrics", None) is not None
            else visualize(result=result, missing_peaks=missing_peaks, extra_peaks=extra_peaks)
        )
    else:
        raise HTTPException(status_code=404, detail="Index out of range")
    # Kα1 wavelength lets the browser show d-spacings next to 2θ on hover.
    try:
        from dara.search.candidate_cost import wavelength_angstrom

        spec = (getattr(document, "search_kwargs", None) or {}).get("wavelength", "Cu")
        figure.update_layout(meta={"wavelength_angstrom": round(wavelength_angstrom(spec), 6)})
    except Exception:
        logger.debug("Plot wavelength unavailable", exc_info=True)
    return figure.to_json()


@lru_cache(maxsize=24)
def _version_plot_json(uuid: str, number: int) -> str:
    """Plot of a saved re-refinement version (numbers are never reused, so this is immutable)."""
    from dara.server import versions

    record = versions.load_version(uuid, number)
    figure = versions.version_refinement(record).visualize()
    try:
        from dara.search.candidate_cost import wavelength_angstrom

        with get_result_store() as result_store:
            stored = result_store.query_one({"uuid": uuid})
        spec = (((stored or {}).get("output") or {}).get("search_kwargs") or {}).get("wavelength", "Cu")
        figure.update_layout(meta={"wavelength_angstrom": round(wavelength_angstrom(spec), 6)})
    except Exception:
        logger.debug("Plot wavelength unavailable", exc_info=True)
    return figure.to_json()


@router.get("/task/{task_id}/plot")
async def plot(task_id: int, idx: int | None = Query(None, ge=0), sel: str | None = Query(None)):
    with get_worker_store() as worker_store:
        task = worker_store.query_one({"index": task_id}, ["uuid", "status"])
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    sel = sel if isinstance(sel, str) else None  # direct calls leave the Query default object
    if sel is not None:
        from dara.server import versions

        if idx is not None:
            raise HTTPException(status_code=422, detail="Choose either idx or sel, not both")
        parsed = versions.parse_selection(sel)
        if isinstance(parsed, tuple):
            payload = _version_plot_json(task["uuid"], parsed[1])
            return Response(
                content=payload,
                media_type="application/json",
                headers={"Cache-Control": "private, max-age=3600"},
            )
        idx = None if parsed == "best" else parsed
    payload = _plot_json(task["uuid"], idx)
    # Returning the serialized figure directly avoids FastAPI JSON-encoding the
    # JSON string a second time (extra copies, escaping and client-side parse).
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "private, max-age=3600"},
    )


LIST_SUMMARY_REFRESH_MAX = 3  # stale summaries upgraded per list request (bounds the latency)
# Record fields a Results row reads; the measured pattern inside ``job`` is left out.
LIST_TASK_FIELDS = ["index", "uuid", "status", "summary", "submitted_time", "user",
                    "job.name", "job.metadata", "review.selection", "error"]


def _refresh_listed_summaries(tasks: list[dict]) -> dict[int, dict | None]:
    """Upgrade at most ``LIST_SUMMARY_REFRESH_MAX`` stale summaries of the listed tasks.

    The remaining stale tasks are refreshed by later requests; until then they keep their
    stored (older) summary. Runs inline like the series refresh (montydb is not thread-safe).
    """
    fresh: dict[int, dict | None] = {}
    budget = LIST_SUMMARY_REFRESH_MAX
    for task in tasks:
        if budget <= 0:
            break
        summary = task.get("summary")
        if task.get("status") != "COMPLETED" or (
            isinstance(summary, dict) and summary.get("schema", 0) >= SUMMARY_SCHEMA
        ):
            continue
        if not task.get("uuid") or task["uuid"] in SUMMARY_REFRESH_GAVE_UP:
            continue
        budget -= 1
        try:
            # The list reads only some fields; the refresh may need the whole record.
            with get_worker_store() as worker_store:
                record = worker_store.query_one({"index": task["index"]}) or task
            fresh[task["index"]] = refresh_stale_summary(record, lambda: get_worker_store())
        except Exception:
            logger.debug("List summary refresh failed", exc_info=True)
    return fresh


@router.get("/tasks")
async def get_all_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user: Optional[str] = None,
    name: Optional[str] = None,
    state: Optional[str] = None,
):
    # Calculate the skip value based on the page and limit
    skip = (page - 1) * limit

    query_dict = {}
    if user is not None:
        query_dict["user"] = user
    if user == "unknown":
        query_dict["user"] = None
    if name is not None and name.strip():
        # Literal, case-insensitive substring match on the pattern file name.
        # file name or sample name (literal, case-insensitive substring)
        pattern = {"$regex": re.escape(name.strip()), "$options": "i"}
        query_dict["$or"] = [{"job.name": pattern}, {"job.metadata.submission_settings.sample": pattern}]
    states = [item.strip().upper() for item in (state or "").split(",") if item.strip()]
    if states:
        # Status filter of the Results table, applied before paging so it covers every page.
        query_dict["status"] = {"$in": states}
    with get_worker_store() as worker_store:
        tasks = list(
            worker_store.query(
                criteria=query_dict, properties=LIST_TASK_FIELDS,
                sort={"submitted_time": -1}, skip=skip, limit=limit
            )
        )

    tasks_result = []
    refreshed = _refresh_listed_summaries(tasks)

    for task in tasks:
        index = task["index"]
        state = task["status"]
        summary = refreshed.get(index, task.get("summary"))
        tasks_result.append(
            {
                "fw_id": index,
                "name": task["job"]["name"],
                "sample": ((task["job"].get("metadata") or {}).get("submission_settings") or {}).get("sample"),
                "state": state,
                "created_on": convert_to_local_tz(task["submitted_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "user": task.get("user", "unknown"),
                "summary": summary if isinstance(summary, dict) else None,
                "series": ((task.get("job") or {}).get("metadata") or {}).get("series"),
                "adopted": (task.get("review") or {}).get("selection"),
                "error_summary": (
                    summarize_failure(task.get("error")) if state == "FIZZLED" else None
                ),
            }
        )

    with get_worker_store() as worker_store:
        total_tasks = worker_store.count(query_dict)

    # Return the tasks data
    return {
        "tasks": tasks_result,
        "total_tasks": total_tasks,
        "page": page,
        "total_page": (total_tasks + limit - 1) // limit,
    }
