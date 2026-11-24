"""Compact per-task summary stored on the task record for the results list."""

from __future__ import annotations

import logging
import math
import re
from html import unescape

logger = logging.getLogger(__name__)

# single source of truth: every stale-summary check compares against this
# (5: labels with the CIFs' conventional formulas and ``display_formulas`` kept with the digest)
SUMMARY_SCHEMA = 5

# Task uuids whose summary could not be refreshed; they are not retried until restart.
SUMMARY_REFRESH_GAVE_UP: set[str] = set()


def _finite(value, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def _label(phase_name: str) -> str:
    try:
        from dara.plot import phase_display_name

        text = unescape(re.sub(r"<[^>]+>", "", phase_display_name(phase_name)))
    except Exception:
        return str(phase_name)
    # pymatgen writes hydroxides as (HO); the task page shows (OH) (ui composition.mjs, same rule)
    return text.replace("(HO)", "(OH)")


def _provenance_digest(phase_name: str) -> dict:
    try:
        from dara.structure_quality import reference_provenance

        info = reference_provenance(phase_name)
        return {
            "source": info.get("source", "unknown"),
            "database_id": info.get("database_id"),
            "label": info.get("label"),
        }
    except Exception:
        from dara.server.table_export import _provenance

        return {"source": "unknown", "database_id": None, "label": _provenance(phase_name)}


def _phase_digests(final) -> list[dict]:
    """Per-phase quantification, lattice and provenance of the final refinement."""
    from dara.server.api_router import _phase_refinement_details, _refined_phase_metadata
    from dara.server.table_export import lattice_with_symmetry

    details = _phase_refinement_details(final)
    metadata = _refined_phase_metadata(final)
    out = []
    for name, detail in details.items():
        number = (metadata.get(name) or {}).get("spacegroup_number")
        lattice = lattice_with_symmetry(detail.get("lattice_parameters"), number)
        out.append({
            "name": str(name),
            "label": _label(name),
            "weight_percent": _finite(detail.get("weight_percent"), 4),
            "weight_esd_percent": _finite(detail.get("weight_esd_percent"), 4),
            "spacegroup_number": number,
            "lattice": {
                key: {"value": _finite(value, 6), "esd": _finite(esd, 6)}
                for key, (value, esd) in lattice.items()
            },
            "provenance": _provenance_digest(str(name)),
        })
    return out


def _adopted_view(document, task: dict | None):
    """``(document view of the adopted fit, selection)``; ``(document, None)`` without one.

    The results list shows the adopted fit: a re-refinement version, or a candidate (an integer
    selection, ``document.results[selection]``) shown as the final result, as the task page does.
    """
    if not isinstance(task, dict) or not task.get("uuid"):
        return document, None
    selection = (task.get("review") or {}).get("selection")
    if isinstance(selection, int) and not isinstance(selection, bool):
        results = list(getattr(document, "results", None) or [])
        if not 0 <= selection < len(results):
            return document, None
        refinement = results[selection][1]
        return document.model_copy(update={
            "final_result": refinement, "best_rwp": refinement.lst_data.rwp,
            "pinned_result_index": selection,  # the adopted record represents its candidate group
        }), selection
    try:
        from dara.server import versions
        from dara.server.api_router import _adopted_version_record

        record = _adopted_version_record(task)
        if record is None:
            return document, None
        full = versions.load_version(task["uuid"], versions.parse_selection(task["review"]["selection"])[1])
        return versions.document_view(document, task, full), task["review"]["selection"]
    except Exception:
        logger.info("Adopted version of task %s unavailable for the summary", task.get("index"), exc_info=True)
        return document, None


def compact_summary(document, runtime_s: float | None, task: dict | None = None) -> dict:
    """Return a small JSON-safe digest of a completed analysis document.

    With ``task`` (the worker record) whose review adopted a re-refinement version, the digest
    describes that version (Rwp, phases, labels, weights); otherwise the automatic fit.
    """
    from dara.plot import display_formulas_scope
    from dara.server.display_formula import task_display_formulas

    if getattr(document, "analysis_mode", None) == "identification":
        return identification_summary(document, runtime_s)
    view, adopted = _adopted_view(document, task)
    uuid = (task or {}).get("uuid")
    try:
        formulas = task_display_formulas(view, uuid)
    except Exception:
        formulas = {}
    with display_formulas_scope(formulas):
        summary = _compact_summary(view, document, runtime_s)
    summary["adopted"] = adopted
    summary["display_formulas"] = dict(formulas)
    return summary


def identification_summary(document, runtime_s: float | None) -> dict:
    """The results-list digest of an identification result: the keys of a refinement's digest with
    no Rwp, GOF or weight fractions, and ``analysis_mode``."""
    from dara.plot import display_formulas_scope
    from dara.server.display_formula import cif_display_formulas

    try:
        formulas = cif_display_formulas(getattr(document, "input_cifs", None) or [])
    except Exception:  # noqa: BLE001 - labels then keep the names' formulas
        formulas = {}
    with display_formulas_scope(formulas):
        summary = _identification_summary(document, runtime_s)
    summary["display_formulas"] = dict(formulas)
    return summary


def _identification_summary(document, runtime_s: float | None) -> dict:
    names = [phase["name"] for phase in document.phases]
    return {
        "schema": SUMMARY_SCHEMA,
        "analysis_mode": "identification",
        "best_rwp": None, "final_rwp": None, "gof": None,
        "final_phases": names,
        "final_phase_labels": [_label(name) for name in names],
        "n_candidates": 0,
        "runtime_s": _finite(runtime_s),
        "phases": [{
            "name": phase["name"], "label": _label(phase["name"]), "weight_percent": None,
            "weight_esd_percent": None, "spacegroup_number": phase.get("spacegroup"), "lattice": {},
            "provenance": _provenance_digest(phase["name"]),
        } for phase in document.phases],
        "confidence_overall": None,
        "adopted": None,
    }


def _compact_summary(document, original, runtime_s: float | None) -> dict:
    final = getattr(document, "final_result", None)
    final_rwp = gof = None
    phases: list[str] = []
    if final is not None:
        fit = final.lst_data
        final_rwp = _finite(fit.rwp)
        rexp = _finite(getattr(fit, "rexp", None))
        gof = _finite(final_rwp / rexp) if final_rwp is not None and rexp else None
        phases = [str(name) for name in fit.phases_results]
    phase_digests: list[dict] = []
    confidence_overall = None
    if final is not None:
        try:
            phase_digests = _phase_digests(final)
        except Exception:
            phase_digests = []
        try:
            from dara.server.confidence import confidence_for_document

            block = confidence_for_document(document)
            confidence_overall = ((block or {}).get("overall") or {}).get("level")
        except Exception:
            confidence_overall = None
    return {
        "schema": SUMMARY_SCHEMA,
        "best_rwp": _finite(getattr(document, "best_rwp", None)),
        "final_rwp": final_rwp,
        "gof": gof,
        "final_phases": phases,
        "final_phase_labels": [_label(name) for name in phases],
        "n_candidates": len(getattr(original, "results", None) or []),
        "runtime_s": _finite(runtime_s),
        "phases": phase_digests,
        "confidence_overall": confidence_overall,
    }


def summarize_stored_result(uuid: str, runtime_s: float | None, task: dict | None = None) -> dict:
    """Read the stored result document for ``uuid`` and summarize it (``task``: see ``compact_summary``)."""
    from monty.serialization import MontyDecoder

    from dara.server.utils import get_result_store

    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": uuid})
    if stored is None:
        raise LookupError(f"No stored result for {uuid}")
    return compact_summary(MontyDecoder().process_decoded(stored["output"]), runtime_s, task)


def runtime_seconds(start, end) -> float | None:
    """Seconds between two stored datetimes, tolerating naive/aware mixtures."""
    try:
        if start is None or end is None:
            return None
        if (start.tzinfo is None) != (end.tzinfo is None):
            start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
        return (end - start).total_seconds()
    except Exception:
        return None


def summary_is_current(summary, record: dict | None) -> bool:
    """A stored digest of the current schema that describes the adopted candidate, if any (a digest
    stored before candidate adoptions were summarized describes the automatic fit)."""
    if not isinstance(summary, dict) or summary.get("schema", 0) < SUMMARY_SCHEMA:
        return False
    selection = ((record or {}).get("review") or {}).get("selection")
    if isinstance(selection, int) and not isinstance(selection, bool):
        return summary.get("adopted") == selection
    if isinstance(selection, str) and selection.startswith("version:"):
        return summary.get("adopted") == selection  # a digest of the automatic fit is stale once a version is adopted
    return True


def refresh_stale_summary(record: dict, open_store) -> dict | None:
    """Return a current-schema summary of a completed task, upgrading and persisting a stale one.

    ``record`` is a worker-store record and ``open_store`` a callable returning the worker
    store context manager. The refresh never raises and never simulates reference patterns
    (trace hints stay off); on failure the old summary is returned and the task is not retried.
    """
    summary = record.get("summary")
    summary = summary if isinstance(summary, dict) else None
    uuid = record.get("uuid")
    if record.get("status") != "COMPLETED":
        return summary
    if summary_is_current(summary, record):
        return summary
    if not uuid or uuid in SUMMARY_REFRESH_GAVE_UP:
        return summary
    try:
        runtime = runtime_seconds(record.get("start_time"), record.get("end_time"))
        selection = (record.get("review") or {}).get("selection")
        adopted = str(selection).startswith("version:") or (
            isinstance(selection, int) and not isinstance(selection, bool)
        )
        fresh = (
            summarize_stored_result(uuid, runtime, record)
            if adopted
            else summarize_stored_result(uuid, runtime)
        )
    except Exception:
        logger.info("Could not refresh the summary of task %s", record.get("index"), exc_info=True)
        SUMMARY_REFRESH_GAVE_UP.add(uuid)
        return summary
    if not isinstance(fresh, dict):
        return summary
    if not summary_is_current(fresh, record):  # e.g. an adopted candidate index that no longer exists
        SUMMARY_REFRESH_GAVE_UP.add(uuid)
    try:
        with open_store() as store:
            current = store.query_one({"index": record["index"]})
            if current is not None and current.get("uuid") == uuid:
                current["summary"] = fresh
                store.update(current)
    except Exception:
        logger.warning("Could not store the refreshed summary", exc_info=True)
    return fresh
