"""Identification results: phases accepted from the search-match, stored as a task without refinement.

``POST /api/identification`` creates the task (``analysis_mode: "identification"``) in the task
store the Results list reads. It is stored RUNNING, computed in a worker thread
(:func:`dara.identification.identify`, then the observed peaks and the RIR analysis are warmed)
and answered after at most ``WAIT_S`` with its state; a client polls ``GET /api/task/{id}`` as
for any task. The result tools read it through :class:`dara.identification.IdentificationFit`;
the tools that need a refinement answer 409 (:func:`refuse_refinement_tools`).
``POST /api/task/{id}/refine`` hands the phases to a Known-phase Rietveld refinement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from traceback import format_exc
from typing import Annotated, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from dara import known_phases
from dara.identification import MODE, NOT_AVAILABLE
from dara.server.utils import convert_to_local_tz, get_result_store, get_worker_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

WAIT_S = 6.0
MAX_PHASES = known_phases.MAX_KNOWN_FORMULAS  # the Known-phase refinement of "Refine with Rietveld"
_FINISHING: set = set()  # running finishers (kept referenced)
# Tools that need a Rietveld fit (versions, quick-refine with its background / structure / Le Bail
# editors, fit comparison, internal standard, refinement exports, retry): 409 for an identification task.
_REFINEMENT_ONLY = re.compile(
    r"^/api/task/(\d+)/(?:versions(?:/.*)?|structure-sites|fit-curves|fit-compare|quick-refine|"
    r"internal-standard|refine-selected|phase-options|candidate-comparison|peak-candidates|"
    r"export/.*|download|table\.csv|retry|retry-plan)$"
)


def is_identification(record: dict | None) -> bool:
    settings = (((record or {}).get("job") or {}).get("metadata") or {}).get("submission_settings") or {}
    return settings.get("analysis_mode") == MODE


async def refuse_refinement_tools(request, call_next):
    """HTTP middleware: 409 for a refinement-only tool of an identification task."""
    match = _REFINEMENT_ONLY.match(request.url.path)
    if match:
        with get_worker_store() as store:
            record = store.query_one({"index": int(match.group(1))}, ["job.metadata"])
        if is_identification(record):
            return JSONResponse({"detail": NOT_AVAILABLE}, status_code=409)
    return await call_next(request)


@router.post("/identification")
async def create_identification(
    pattern_file: Annotated[UploadFile, File()],
    user: Annotated[str, Form()],
    reference_ids: Annotated[str, Form()],
    elements: Annotated[str, Form()] = "[]",
    precursor_formulas: Annotated[str, Form()] = "[]",
    wavelength: Annotated[str, Form()] = "Cu",
    two_theta_min: Annotated[Optional[float], Form()] = None,
    two_theta_max: Annotated[Optional[float], Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    instrument_profile: Annotated[str, Form()] = "Aeris-fds-Pixcel1d-Medipix3",
    sample: Annotated[str, Form()] = "",
    confirm_wavelength: Annotated[bool, Form()] = False,
):
    """Store the accepted search-match phases (``reference_ids``: JSON list of cached ``cod:<id>`` /
    ``mp:<id>``, 1-12, order kept) as an identification task; returns ``{wf_id, status}``."""
    from dara.search.quick_rank import reference_path
    from dara.server.api_router import _available_instrument_profiles, _parse_string_list
    from dara.server.quick_rank_router import _elements_and_precursors, read_upload
    from dara.server.worker import add_task_record

    element_list, precursors = _elements_and_precursors(elements, precursor_formulas)
    ids = list(dict.fromkeys(_parse_string_list(reference_ids)))
    if not 1 <= len(ids) <= MAX_PHASES:
        raise HTTPException(status_code=400, detail=f"Accept 1 to {MAX_PHASES} phases.")
    try:
        for ref in ids:
            reference_path(ref)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Structure {exc.args[0]} is not in the local cache; "
                                                    "run the search-match again.")
    details = None
    if instrument_profile.startswith("user-"):
        from dara.instrument_profiles import registered_profiles

        details = registered_profiles().get(instrument_profile)
    if details is None and instrument_profile not in _available_instrument_profiles():
        raise HTTPException(status_code=400, detail=f"Unknown instrument profile {instrument_profile}")
    name, _, pattern, range_params = await read_upload(
        pattern_file, two_theta_min, two_theta_max, excluded_ranges, None if confirm_wavelength else wavelength)
    try:
        wavelength = float(wavelength)  # a number is a synchrotron wavelength in nm, as /api/submit
    except ValueError:
        pass
    search_kwargs = {"instrument_profile": instrument_profile, "wavelength": wavelength,
                     "refinement_params": range_params}
    settings = {"filename": name, "analysis_mode": MODE, "reference_ids": ids}
    if sample.strip():
        settings["sample"] = sample.strip()[:120]
    metadata = {"submission_settings": settings}
    if details:
        metadata["instrument_profile_details"] = details
    now = datetime.now(timezone.utc)
    record = {
        "uuid": str(uuid4()), "status": "RUNNING", "submitted_time": now, "start_time": now, "user": user,
        "job": {"name": name, "metadata": metadata, "function_kwargs": {
            "xrd_data": pattern.as_dict(), "elements": element_list, "precursors": precursors,
            "analysis_mode": MODE, "search_kwargs": search_kwargs, "cif_dbs": [],
        }},
    }
    index = add_task_record(record)
    task = asyncio.create_task(_finish(index, record["uuid"], pattern, ids, search_kwargs, element_list, precursors))
    _FINISHING.add(task)
    task.add_done_callback(_FINISHING.discard)
    await asyncio.wait({task}, timeout=WAIT_S)
    with get_worker_store() as store:
        stored = store.query_one({"index": index}, ["status"]) or {}
    return {"message": "submitted", "wf_id": index, "status": stored.get("status", "RUNNING")}


def _update_record(index: int, uuid: str, fields: dict) -> str | None:
    """Merge ``fields`` into the task record (event-loop thread); a cancel request turns the
    task CANCELLED instead. Returns the stored status, ``None`` when the task is gone."""
    with get_worker_store() as store:
        current = store.query_one({"index": index})
        if current is None or current.get("uuid") != uuid:
            return None
        if current["status"] == "CANCELLING":
            fields = {"status": "CANCELLED", "end_time": datetime.now(timezone.utc)}
        current.update(fields)
        store.update(current)
        return current["status"]


def _compute(pattern, ids, search_kwargs):
    from dara.identification import identify
    from dara.search.quick_rank import reference_cifs

    rng = search_kwargs["refinement_params"]
    cifs = reference_cifs(ids)
    return (cifs, *identify(pattern.angles, pattern.intensities, cifs, ids, wavelength=search_kwargs["wavelength"],
                            wmin=rng.get("wmin"), wmax=rng.get("wmax"), excluded_ranges=rng.get("excluded_ranges")))


async def _finish(index, uuid, pattern, ids, search_kwargs, elements, precursors) -> None:
    from dara.identification import IdentificationDocument
    from dara.server.task_summary import identification_summary

    started = datetime.now(timezone.utc)
    try:
        cifs, phases, explained = await asyncio.to_thread(_compute, pattern, ids, search_kwargs)
        document = IdentificationDocument(pattern, search_kwargs, cifs, phases, explained, elements, precursors)
        with get_result_store() as store:
            store.update({"uuid": uuid, "index": 1, "output": document.as_dict()})
        end = datetime.now(timezone.utc)
        status = _update_record(index, uuid, {"status": "COMPLETED", "end_time": end, "summary": identification_summary(
            document, (end - started).total_seconds())})
    except Exception as exc:  # noqa: BLE001 - stored like a failed refinement
        logger.warning("Identification task %s failed", index, exc_info=True)
        error = str(exc) if isinstance(exc, ValueError) else format_exc()
        _update_record(index, uuid, {"status": "FIZZLED", "end_time": datetime.now(timezone.utc), "error": error})
        return
    if status != "COMPLETED":
        return
    try:  # observed peaks, profile fits and RIR are cached on disk: the first page view is instant
        with get_worker_store() as store:
            job = store.query_one({"index": index})
        await identification_payload(job, document)
    except Exception:  # noqa: BLE001 - the result stays; its tools report their own errors
        logger.warning("Warming identification task %s failed", index, exc_info=True)


async def identification_payload(job: dict, document) -> dict:
    """``GET /api/task/{id}`` payload of a COMPLETED identification task.

    ``phases``: the accepted phases in order with the search-match values (``score``,
    ``explained`` alone, ``gain`` in the combination, ``matched``/``expected`` strong peaks,
    ``lattice_scale``, ``shift``, ``amplitude``), ``reflections`` (count), ``observed_peaks``
    (assigned to it), ``rir`` (I/Ic) and ``rir_weight_percent``; ``weight_percent`` is null (no
    Rietveld fit). ``peak_counts`` / ``unexplained_peaks`` come from the observed-peak list.
    """
    from dara.export.peak_list import _label
    from dara.server.api_router import _measured_peak_rows_of, _rir_of, _spacegroup_symbol
    from dara.server.display_formula import cif_display_formulas
    from dara.server.task_summary import runtime_seconds

    index = job["index"]
    rows = rir = None
    try:
        rows = (await _measured_peak_rows_of(index, None))[1]
        rir = (await _rir_of(index, None))[3]
    except HTTPException:
        logger.warning("Observed peaks / RIR of identification task %s unavailable", index, exc_info=True)
    by_phase = {row["phase"]: row for row in (rir or {}).get("phases", [])}
    phases = []
    for phase in document.phases:
        name, number = phase["name"], phase.get("spacegroup")
        phases.append({
            **{key: value for key, value in phase.items() if key != "reflections"},
            "label": _label(name),
            "spacegroup_symbol": _spacegroup_symbol(number) if number else None,
            "reflections": len(phase["reflections"]["two_theta"]),
            "observed_peaks": None if rows is None else sum(row["phase"] == name for row in rows),
            "rir": (by_phase.get(name) or {}).get("rir"),
            "rir_weight_percent": (by_phase.get(name) or {}).get("weight_percent_rir"),
            "weight_percent": None,
        })
    unexplained = [row for row in rows or [] if row["phase"] is None]

    def stamp(key):
        return convert_to_local_tz(job[key]).strftime("%Y-%m-%d %H:%M:%S") if job.get(key) else None

    metadata = job["job"].get("metadata") or {}
    return {
        "status": job["status"], "task_label": job["job"]["name"], "analysis_mode": MODE,
        "phases": phases, "explained": document.explained,
        "peak_counts": None if rows is None else {
            "observed": len(rows), "assigned": len(rows) - len(unexplained), "unexplained": len(unexplained)},
        "unexplained_peaks": [[row["two_theta_deg"], row["intensity"]] for row in unexplained],
        "rir_complete": None if rir is None else rir["complete"],
        "best_rwp": None, "final_result": None, "all_results": [],
        "reference_ids": (metadata.get("submission_settings") or {}).get("reference_ids"),
        "refined_as": job.get("refined_as") or [],
        "precursors": document.precursors, "elements": document.elements,
        "submitted_on": stamp("submitted_time"), "start_time": stamp("start_time"), "end_time": stamp("end_time"),
        "runtime": runtime_seconds(job.get("start_time"), job.get("end_time")),
        "additional_search_options": document.search_kwargs,
        "instrument_profile_details": metadata.get("instrument_profile_details"),
        "display_formulas": cif_display_formulas(document.input_cifs),
        "internal_standard": None,
        # Download menu items of this result kind (the refinement-only ones answer 409)
        "capabilities": {"report": True, "report_bundle": True, "figure": True, "table_csv": False,
                         "result_archive": False},
    }


@router.post("/task/{task_id}/refine")
async def refine_identification(task_id: int):
    """Refine an identification task's phases with Rietveld: a Known-phase refinement of its
    ``reference_ids`` with the same pattern and settings, submitted as a new task (the path of the
    search-match's "Refine selected"). The tasks are linked by ``refined_as`` / ``identified_from``."""
    from dara.server.api_router import _saved_task_input, build_submission_job
    from dara.server.utils import not_completed_detail
    from dara.server.worker import add_job_to_queue

    record, kwargs, pattern = _saved_task_input(task_id)
    if not is_identification(record):
        raise HTTPException(status_code=409, detail="Only an identification result is refined this way.")
    if record.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail=not_completed_detail(record.get("status")))
    settings = record["job"]["metadata"]["submission_settings"]
    search = kwargs.get("search_kwargs") or {}
    rng = search.get("refinement_params") or {}
    job = build_submission_job(
        pattern, settings.get("filename") or record["job"]["name"], analysis_mode="known_mixture",
        reference_ids=settings["reference_ids"], elements=kwargs.get("elements") or [],
        instrument_profile=search["instrument_profile"], wavelength=search["wavelength"],
        two_theta_min=rng.get("wmin"), two_theta_max=rng.get("wmax"),
        excluded_ranges=json.dumps(rng.get("excluded_ranges") or []), sample=settings.get("sample") or "",
    )
    job.metadata["submission_settings"]["identified_from"] = task_id
    new_id = add_job_to_queue(job, record.get("user", ""))
    with get_worker_store() as store:
        current = store.query_one({"index": task_id})
        if current is not None and current.get("uuid") == record["uuid"]:
            current["refined_as"] = [*(current.get("refined_as") or []), new_id]
            store.update(current)
    return {"message": "submitted", "wf_id": new_id, "identified_from": task_id}
