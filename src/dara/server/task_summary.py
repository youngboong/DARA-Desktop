"""Compact per-task summary stored on the task record for the results list."""

from __future__ import annotations

import logging
import math
import re
from html import unescape

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = 4  # single source of truth: every stale-summary check compares against this

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

        return unescape(re.sub(r"<[^>]+>", "", phase_display_name(phase_name)))
    except Exception:
        return str(phase_name)


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
    """``(document view of the adopted version, selection)``; ``(document, None)`` without one.

    The results list shows the adopted fit when a re-refinement version was adopted.
    """
    if not isinstance(task, dict) or not task.get("uuid"):
        return document, None
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

    view, adopted = _adopted_view(document, task)
    uuid = (task or {}).get("uuid")
    try:
        formulas = task_display_formulas(view, uuid)
    except Exception:
        formulas = {}
    with display_formulas_scope(formulas):
        summary = _compact_summary(view, document, runtime_s)
    summary["adopted"] = adopted
    return summary


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
    if summary is not None and summary.get("schema", 0) >= SUMMARY_SCHEMA:
        return summary
    if not uuid or uuid in SUMMARY_REFRESH_GAVE_UP:
        return summary
    try:
        runtime = runtime_seconds(record.get("start_time"), record.get("end_time"))
        adopted = str((record.get("review") or {}).get("selection")).startswith("version:")
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
    if fresh.get("schema", 0) < SUMMARY_SCHEMA:
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
