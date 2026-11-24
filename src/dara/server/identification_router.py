"""Identification results: phases accepted from the search-match, stored as a task without refinement.

``POST /api/identification`` creates the task (``analysis_mode: "identification"``) in the task
store the Results list reads. It is stored RUNNING, computed in a worker thread
(:func:`dara.identification.identify`, then the observed peaks and the RIR analysis are warmed)
and answered after at most ``WAIT_S`` with its state; a client polls ``GET /api/task/{id}`` as
for any task. The result tools read it through :class:`dara.identification.IdentificationFit`;
the tools that need a refinement answer 409 (:func:`refuse_refinement_tools`).
``POST /api/task/{id}/refine`` hands the phases to a Known-phase Rietveld refinement.

``automatic=true`` (no ``reference_ids``) is the batch path of "Identify all": the worker runs the
search-match first and accepts its suggestion (the greedy combination without the weak picks) before
the identification; it answers at once with the RUNNING task. Search-matches of automatic tasks run
one at a time (an asyncio semaphore; no thread waits meanwhile). ``series`` (JSON ``{id, name,
order, size, variable}``) groups the tasks of one batch as a series, as ``/api/submit-series`` does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import weakref
from datetime import datetime, timezone
from traceback import format_exc
from typing import Annotated, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dara import known_phases
from dara.identification import MODE, NOT_AVAILABLE
from dara.server.utils import convert_to_local_tz, get_result_store, get_worker_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

WAIT_S = 6.0
MAX_PHASES = known_phases.MAX_KNOWN_FORMULAS  # the Known-phase refinement of "Refine with Rietveld"
_FINISHING: set = set()  # running finishers (kept referenced)
_SEARCH_SLOTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # event loop -> semaphore
NO_SUGGESTION = ("The search-match suggests no phase for this pattern (no candidate explains a significant "
                 "share of it). Check the elements and databases, or pick phases with Search-match.")
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
    user: Annotated[str, Form()],
    reference_ids: Annotated[str, Form()] = "[]",
    pattern_file: Annotated[Optional[UploadFile], File()] = None,
    source_task_id: Annotated[Optional[int], Form()] = None,
    elements: Annotated[str, Form()] = "[]",
    precursor_formulas: Annotated[str, Form()] = "[]",
    wavelength: Annotated[str, Form()] = "Cu",
    two_theta_min: Annotated[Optional[float], Form()] = None,
    two_theta_max: Annotated[Optional[float], Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    instrument_profile: Annotated[str, Form()] = "Aeris-fds-Pixcel1d-Medipix3",
    sample: Annotated[str, Form()] = "",
    confirm_wavelength: Annotated[bool, Form()] = False,
    use_cod: Annotated[Optional[bool], Form()] = None,
    use_mp: Annotated[Optional[bool], Form()] = None,
    mp_e_hull_cutoff: Annotated[Optional[float], Form()] = None,
    max_mp_structures: Annotated[Optional[int], Form()] = None,
    include_theoretical: Annotated[Optional[bool], Form()] = None,
    background: Annotated[str, Form()] = "",
    automatic: Annotated[bool, Form()] = False,
    library_cif_ids: Annotated[str, Form()] = "[]",
    series: Annotated[str, Form()] = "",
    alternatives: Annotated[str, Form()] = "[]",
):
    """Store the accepted search-match phases (``reference_ids``: JSON list of cached ``cod:<id>`` /
    ``mp:<id>``, 1-12, order kept) as an identification task; returns ``{wf_id, status}``.

    The search-match's database settings (``use_cod``, ``use_mp``, ``mp_e_hull_cutoff``,
    ``max_mp_structures``, ``include_theoretical``; each optional) are stored in
    ``function_kwargs.search_match`` so ``/rerun-settings`` and a new search-match reproduce them.
    ``background`` (JSON, as ``/api/quick-ranking``) is stored in ``search_kwargs.background``.
    ``automatic``: no ``reference_ids``; the phases are the search-match suggestion (module doc)."""
    from dara.search.quick_rank import reference_path
    from dara.server.api_router import _available_instrument_profiles, _parse_string_list
    from dara.server.quick_rank_router import _elements_and_precursors, background_param, read_upload
    from dara.server.worker import add_task_record

    element_list, precursors = _elements_and_precursors(elements, precursor_formulas)
    search_match = {key: value for key, value in {
        "use_cod": use_cod, "use_mp": use_mp, "mp_e_hull_cutoff": mp_e_hull_cutoff,
        "max_mp_structures": max_mp_structures, "include_theoretical": include_theoretical}.items()
        if value is not None}
    if not 0 <= search_match.get("mp_e_hull_cutoff", 0) <= 1 or not 1 <= search_match.get("max_mp_structures", 1) <= 1000:
        raise HTTPException(status_code=400, detail="Invalid Materials Project limits")
    ids = list(dict.fromkeys(_parse_string_list(reference_ids)))
    library_ids = []
    if automatic:
        if ids:
            raise HTTPException(status_code=400, detail="An automatic identification takes no reference_ids.")
        library_ids = _library_ids(library_cif_ids)
    elif not 1 <= len(ids) <= MAX_PHASES:
        raise HTTPException(status_code=400, detail=f"Accept 1 to {MAX_PHASES} phases.")
    try:
        for ref in ids:
            reference_path(ref)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Structure {exc.args[0]} is not in the local cache; "
                                                    "run the search-match again.")
    batch = _series(series)
    alternates = _alternatives(alternatives, ids)
    details = None
    if instrument_profile.startswith("user-"):
        from dara.instrument_profiles import registered_profiles

        details = registered_profiles().get(instrument_profile)
    if details is None and instrument_profile not in _available_instrument_profiles():
        raise HTTPException(status_code=400, detail=f"Unknown instrument profile {instrument_profile}")
    if (pattern_file is None) == (source_task_id is None):
        raise HTTPException(status_code=400, detail="Upload a pattern or name a source task (one of them).")
    if pattern_file is not None:
        name, _, pattern, range_params = await read_upload(
            pattern_file, two_theta_min, two_theta_max, excluded_ranges, None if confirm_wavelength else wavelength)
    else:
        name, pattern, range_params = _stored_pattern(
            source_task_id, two_theta_min, two_theta_max, excluded_ranges, None if confirm_wavelength else wavelength)
    try:
        wavelength = float(wavelength)  # a number is a synchrotron wavelength in nm, as /api/submit
    except ValueError:
        pass
    search_kwargs = {"instrument_profile": instrument_profile, "wavelength": wavelength,
                     "refinement_params": range_params, "background": background_param(background)}
    settings = {"filename": name, "analysis_mode": MODE, "reference_ids": ids}
    if automatic:
        settings["automatic"] = True
    if alternates:
        settings["alternatives"] = alternates
    if sample.strip():
        settings["sample"] = sample.strip()[:120]
    metadata = {"submission_settings": settings}
    if batch:
        metadata["series"] = batch
    if details:
        metadata["instrument_profile_details"] = details
    now = datetime.now(timezone.utc)
    record = {
        "uuid": str(uuid4()), "status": "RUNNING", "submitted_time": now, "start_time": now, "user": user,
        "job": {"name": name, "metadata": metadata, "function_kwargs": {
            "xrd_data": pattern.as_dict(), "elements": element_list, "precursors": precursors,
            "analysis_mode": MODE, "search_kwargs": search_kwargs, "cif_dbs": [], "search_match": search_match,
        }},
    }
    index = add_task_record(record)
    if automatic:
        params = _common_params(
            pattern, elements=element_list, precursors=precursors, wavelength=wavelength, range_params=range_params,
            e_hull=search_match.get("mp_e_hull_cutoff", 0.05), use_cod=search_match.get("use_cod", True),
            use_mp=search_match.get("use_mp", True), max_mp=search_match.get("max_mp_structures", 100),
            include_theoretical=search_match.get("include_theoretical", False), library_ids=library_ids,
            background=search_kwargs["background"],
        )
        work = _finish_automatic(index, record["uuid"], pattern, params, search_kwargs, element_list, precursors)
    else:
        work = _finish(index, record["uuid"], pattern, ids, search_kwargs, element_list, precursors)
    task = asyncio.create_task(work)
    _FINISHING.add(task)
    task.add_done_callback(_FINISHING.discard)
    if not automatic:  # a batch answers at once; its client polls the task
        await asyncio.wait({task}, timeout=WAIT_S)
    with get_worker_store() as store:
        stored = store.query_one({"index": index}, ["status"]) or {}
    return {"message": "submitted", "wf_id": index, "status": stored.get("status", "RUNNING")}


def _library_ids(raw) -> list[str]:
    from dara.server.quick_rank_router import _library_ids as checked

    return checked(raw)


def _common_params(pattern, **fields) -> dict:
    from dara.server.quick_rank_router import _common_params as params

    return params(pattern, **fields)


def _series(raw: str) -> dict | None:
    """Validated series membership of a batch task (``{id, name, order, size, variable}``); 400 when invalid."""
    if not (raw or "").strip():
        return None
    try:
        value = json.loads(raw)
        series = {"id": str(value["id"]), "name": str(value["name"]).strip(), "order": int(value["order"]),
                  "size": int(value["size"]), "variable": value.get("variable")}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid series: {exc}")
    if (not re.fullmatch(r"[0-9a-f]{6,32}", series["id"]) or not 1 <= len(series["name"]) <= 120
            or not 0 <= series["order"] < series["size"] <= 500
            or not (series["variable"] is None or isinstance(series["variable"], dict))):
        raise HTTPException(status_code=400, detail="Invalid series")
    return series


def _alternatives(raw: str, ids: list[str]) -> list[dict]:
    """Search-match alternatives of the accepted phases (``[{source, formula, spacegroup, of}]``,
    ``of`` an accepted id): candidates peak matching cannot tell apart from it. 400 when invalid."""
    try:
        items = json.loads(raw or "[]")
        cleaned = [{"source": str(item["source"])[:80], "formula": str(item.get("formula") or "")[:80],
                    "spacegroup": item.get("spacegroup") if isinstance(item.get("spacegroup"), int) else None,
                    "of": str(item["of"])} for item in items]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid alternatives: {exc}")
    if len(cleaned) > 50 or any(item["of"] not in ids or item["source"] in ids for item in cleaned):
        raise HTTPException(status_code=400, detail="Invalid alternatives")
    return cleaned


def ranking_alternatives(result: dict, ids: list[str]) -> list[dict]:
    """Alternatives of the accepted ``ids`` from a search-match result's ranking rows."""
    rows = {row["source"]: row for row in result.get("ranking") or []}
    return [{"source": other, "formula": rows[other]["formula"], "spacegroup": rows[other].get("spacegroup"), "of": source}
            for source in ids if source in rows
            for other in rows[source].get("alternatives") or [] if other in rows and other not in ids]


def suggested_ids(result: dict) -> list[str]:
    """Reference ids of the search-match suggestion that the workspace ticks by default: the greedy
    combination without its weak picks, in order, at most MAX_PHASES."""
    return [step["source"] for step in result.get("combination") or [] if not step.get("weak")][:MAX_PHASES]


def _search_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _SEARCH_SLOTS:
        _SEARCH_SLOTS[loop] = asyncio.Semaphore(1)
    return _SEARCH_SLOTS[loop]


async def _finish_automatic(index, uuid, pattern, params, search_kwargs, elements, precursors) -> None:
    """Search-match, accept its suggestion, then the identification (``_finish``)."""
    from dara.server.quick_rank_router import _run

    try:
        async with _search_slot():  # one search-match at a time: the others wait without a thread
            with get_worker_store() as store:
                current = store.query_one({"index": index}, ["uuid", "status"])
            if current is None or current.get("uuid") != uuid or current.get("status") != "RUNNING":
                _update_record(index, uuid, {})  # cancelled while waiting
                return
            result = await asyncio.to_thread(_run, params)
        ids = suggested_ids(result)
        if not ids:
            weak = [step["formula"] for step in result.get("combination") or []]
            raise ValueError(f"The search-match suggests only weak phases ({', '.join(weak)}); pick phases with "
                             "Search-match." if weak else NO_SUGGESTION)
    except Exception as exc:  # noqa: BLE001 - stored like a failed refinement
        logger.warning("Automatic identification %s failed", index, exc_info=True)
        error = str(exc) if isinstance(exc, ValueError) else f"The search-match failed: {type(exc).__name__}: {exc}"
        _update_record(index, uuid, {"status": "FIZZLED", "end_time": datetime.now(timezone.utc), "error": error})
        return
    with get_worker_store() as store:  # the accepted phases, stored as "Save identification" stores them
        current = store.query_one({"index": index})
        if current is None or current.get("uuid") != uuid:
            return
        current["job"]["metadata"]["submission_settings"]["reference_ids"] = ids
        alternates = ranking_alternatives(result, ids)
        if alternates:
            current["job"]["metadata"]["submission_settings"]["alternatives"] = alternates
        store.update(current)
    await _finish(index, uuid, pattern, ids, search_kwargs, elements, precursors)


def _stored_pattern(task_id, two_theta_min, two_theta_max, excluded_ranges, wavelength=None):
    """``(file name, pattern, range params)`` of a stored task's pattern (any state), with the
    upload's checks: the analysis range, and with ``wavelength`` the XRDML wavelength check (400)."""
    from pathlib import Path

    from dara.analysis_range import validate_analysis_range
    from dara.server.api_router import _saved_task_input, _wavelength_mismatch

    record, _, pattern = _saved_task_input(task_id)
    settings = ((record.get("job") or {}).get("metadata") or {}).get("submission_settings") or {}
    name = Path(settings.get("filename") or (record.get("job") or {}).get("name") or "pattern.xy").name
    if wavelength is not None:
        mismatch = _wavelength_mismatch(pattern, Path(name), wavelength)
        if mismatch:
            raise HTTPException(status_code=400, detail=mismatch)
    try:
        range_params = validate_analysis_range(pattern.angles, two_theta_min, two_theta_max, excluded_ranges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return name, pattern, range_params


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
                            wmin=rng.get("wmin"), wmax=rng.get("wmax"), excluded_ranges=rng.get("excluded_ranges"),
                            background=search_kwargs.get("background")))


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
    alternates = ((job["job"].get("metadata") or {}).get("submission_settings") or {}).get("alternatives") or []
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
            # candidates peak matching cannot tell apart from this phase ("Tell apart with Rietveld")
            "alternatives": [item for item in alternates if item["of"] == phase.get("source")],
        })
    unassigned = [row for row in rows or [] if row["phase"] is None]
    # The Search-match workspace's rule (quick_rank.residual_maxima) for the same selection, so the
    # page counts the peaks the workspace reported; the bare assignment only when it cannot be rerun.
    open_peaks = await asyncio.to_thread(lambda: document.unexplained_peaks)
    unexplained = ([[peak[0], peak[1]] for peak in open_peaks] if open_peaks is not None
                   else [[row["two_theta_deg"], row["intensity"]] for row in unassigned])

    def stamp(key):
        return convert_to_local_tz(job[key]).strftime("%Y-%m-%d %H:%M:%S") if job.get(key) else None

    metadata = job["job"].get("metadata") or {}
    return {
        "status": job["status"], "task_label": job["job"]["name"], "analysis_mode": MODE,
        "phases": phases, "explained": document.explained,
        "peak_counts": None if rows is None else {
            "observed": len(rows), "assigned": len(rows) - len(unassigned), "unexplained": len(unexplained)},
        "unexplained_peaks": unexplained,
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


class RefineRequest(BaseModel):
    """``extra_reference_ids``: stored alternatives of the phases refined with them ("Tell apart
    with Rietveld")."""

    extra_reference_ids: list[str] = Field(default_factory=list, max_length=MAX_PHASES)


@router.post("/task/{task_id}/refine")
async def refine_identification(task_id: int, request: RefineRequest | None = None):
    """Refine an identification task's phases with Rietveld: a Known-phase refinement of its
    ``reference_ids`` (and the requested stored alternatives) with the same pattern and settings,
    submitted as a new task (the path of the search-match's "Refine selected"). The tasks are
    linked by ``refined_as`` / ``identified_from``."""
    from dara.server.api_router import _saved_task_input, build_submission_job
    from dara.server.utils import not_completed_detail
    from dara.server.worker import add_job_to_queue

    record, kwargs, pattern = _saved_task_input(task_id)
    if not is_identification(record):
        raise HTTPException(status_code=409, detail="Only an identification result is refined this way.")
    if record.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail=not_completed_detail(record.get("status")))
    settings = record["job"]["metadata"]["submission_settings"]
    extra = list(dict.fromkeys((request or RefineRequest()).extra_reference_ids))
    stored = {item["source"] for item in settings.get("alternatives") or []}
    if any(ref not in stored for ref in extra) or len(settings["reference_ids"]) + len(extra) > MAX_PHASES:
        raise HTTPException(status_code=400, detail=f"Only stored alternatives can be added, {MAX_PHASES} phases at most.")
    search = kwargs.get("search_kwargs") or {}
    rng = search.get("refinement_params") or {}
    job = build_submission_job(
        pattern, settings.get("filename") or record["job"]["name"], analysis_mode="known_mixture",
        reference_ids=[*settings["reference_ids"], *extra], elements=kwargs.get("elements") or [],
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


async def _phase_names(task_id: int) -> tuple[str, list[str] | None]:
    """``(status, phase names)`` of a task: an identification's accepted phases, a refinement's
    final phases; names ``None`` unless COMPLETED."""
    from dara.server import quick_refine

    with get_worker_store() as store:
        record = store.query_one({"index": task_id}, ["status"])
    if record is None:
        return "MISSING", None
    if record.get("status") != "COMPLETED":
        return record.get("status"), None
    _, document = await quick_refine.load_completed(task_id)
    if getattr(document, "analysis_mode", None) == MODE:
        return "COMPLETED", [phase["name"] for phase in document.phases]
    final = getattr(document, "final_result", None)
    return "COMPLETED", list(final.lst_data.phases_results) if final is not None else []


@router.get("/task/{task_id}/phase-agreement")
async def phase_agreement(task_id: int):
    """Whether an identification and its Rietveld refinements (``refined_as`` / ``identified_from``)
    found the same phases, by the confidence assessment's same-phase rule: ``{items: [{task_id,
    status, agree, only_here, only_there}]}`` (labels; ``agree`` null until the other task is done)."""
    from dara.export.peak_list import _label
    from dara.server.confidence import phase_set_differences

    with get_worker_store() as store:
        record = store.query_one({"index": task_id}, ["job.metadata", "refined_as", "status"])
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    settings = ((record.get("job") or {}).get("metadata") or {}).get("submission_settings") or {}
    if is_identification(record):
        others = record.get("refined_as") or []
    else:
        others = [settings["identified_from"]] if settings.get("identified_from") is not None else []
    _, here = await _phase_names(task_id)
    items = []
    for other in others:
        status, there = await _phase_names(int(other))
        item = {"task_id": int(other), "status": status, "agree": None, "only_here": [], "only_there": []}
        if here is not None and there is not None:
            only_here, only_there = phase_set_differences(here, there)
            item.update(agree=not only_here and not only_there, only_here=[_label(n) for n in only_here],
                        only_there=[_label(n) for n in only_there])
        items.append(item)
    return {"items": items}
