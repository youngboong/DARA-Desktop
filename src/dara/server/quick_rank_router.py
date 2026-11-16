"""Quick ranking ("search-match") endpoints: candidates to refine, never a result.

``POST /api/quick-ranking`` (multipart pattern upload) and ``POST /api/task/{id}/quick-ranking``
start a ranking in a worker thread (``asyncio.to_thread``) and wait a few seconds for it.
A warm pool answers ``{"status": "done", ...}`` directly; otherwise the answer is
``{"status": "pending", "job_id"}`` and ``GET /api/quick-ranking/{job_id}`` returns the same
shape until the ranking is done. The same inputs reuse a running job, or a finished one for
``REUSE_DONE_S``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from dara.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

WAIT_S = 6.0
REUSE_DONE_S = 60.0  # a finished ranking is reused this long (its caveats may go stale)
MAX_ELEMENTS = 12
_JOBS_MAX = 16
_JOBS: "OrderedDict[str, dict]" = OrderedDict()
_JOBS_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()  # one ranking at a time: stick simulation is CPU and memory heavy


def _run(params: dict) -> dict:
    from dara.search.quick_rank import quick_ranking
    from dara.server.api_router import _spacegroup_symbol

    with _RUN_LOCK:
        result = quick_ranking(**params)
    for row in result["ranking"] + result["combination"]:
        number = row.get("spacegroup")
        try:
            row["spacegroup_symbol"] = _spacegroup_symbol(number) if number else None
        except Exception:  # noqa: BLE001
            row["spacegroup_symbol"] = None
    return result


def _job_view(job_id: str, job: dict) -> dict:
    if job["status"] == "done":
        return {**job["result"], "status": "done", "job_id": job_id}
    if job["status"] == "error":
        return {"status": "error", "job_id": job_id, "error": job["error"]}
    return {"status": "pending", "job_id": job_id, "elapsed_s": round(time.monotonic() - job["started"], 1)}


async def _start_and_wait(params: dict, key_parts) -> dict:
    job_id = hashlib.sha1(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job["status"] == "error":
            job = None  # a failed ranking is tried again
        elif job is not None and job["status"] == "done" and time.monotonic() - job["finished"] > REUSE_DONE_S:
            job = None  # the library or the MP key may have changed since
        if job is None:
            job = {"status": "pending", "started": time.monotonic(), "result": None, "error": None, "task": None}
            _JOBS[job_id] = job
            while len(_JOBS) > _JOBS_MAX:
                _JOBS.popitem(last=False)
            fresh = True
        else:
            _JOBS.move_to_end(job_id)
            fresh = False
    if fresh:
        async def runner():
            try:
                job["result"] = await asyncio.to_thread(_run, params)
                job["finished"] = time.monotonic()
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Quick ranking failed", exc_info=True)
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["status"] = "error"

        job["task"] = asyncio.create_task(runner())
    task = job.get("task")
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=WAIT_S)
    return _job_view(job_id, job)


def _common_params(pattern, *, elements, precursors, wavelength, range_params, e_hull, use_cod, use_mp,
                   max_mp, include_theoretical) -> dict:
    return {
        "angles": [float(v) for v in pattern.angles],
        "intensities": [float(v) for v in pattern.intensities],
        "elements": elements,
        "precursors": precursors,
        "wavelength": wavelength,
        "wmin": range_params.get("wmin"),
        "wmax": range_params.get("wmax"),
        "excluded_ranges": range_params.get("excluded_ranges"),
        "e_hull_cutoff": float(e_hull),
        "use_cod": bool(use_cod),
        "use_mp": bool(use_mp),
        "mp_limit": int(max_mp),
        "include_theoretical": bool(include_theoretical),
    }


def _elements_and_precursors(elements_raw, precursors_raw) -> tuple[list[str], list[str]]:
    from pymatgen.core import Composition, Element

    from dara.server.api_router import _parse_string_list
    from dara.server.mass_balance import split_precursor_entries

    try:
        precursors, _ = split_precursor_entries(_parse_string_list(precursors_raw))
        elements = sorted({str(Element(e)) for e in _parse_string_list(elements_raw)})
        if not elements:
            elements = sorted({str(e) for f in precursors for e in Composition(f).elements})
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid elements or formulas: {exc}")
    if not elements:
        raise HTTPException(status_code=400, detail="Enter the elements (or formulas) of the sample.")
    if len(elements) > MAX_ELEMENTS:  # the pool spans every sub-system: 2^n chemical systems
        raise HTTPException(
            status_code=422,
            detail=f"The quick ranking takes at most {MAX_ELEMENTS} elements ({len(elements)} given); "
            "remove elements that cannot form crystalline phases in the sample.",
        )
    return elements, precursors


@router.post("/quick-ranking")
async def quick_ranking_upload(
    pattern_file: Annotated[UploadFile, File()],
    elements: Annotated[str, Form()] = "[]",
    precursor_formulas: Annotated[str, Form()] = "[]",
    wavelength: Annotated[str, Form()] = "Cu",
    two_theta_min: Annotated[Optional[float], Form()] = None,
    two_theta_max: Annotated[Optional[float], Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    include_theoretical: Annotated[bool, Form()] = False,
    use_cod: Annotated[bool, Form()] = True,
    use_mp: Annotated[bool, Form()] = True,
    mp_e_hull_cutoff: Annotated[float, Form()] = 0.05,
    max_mp_structures: Annotated[int, Form()] = 100,
):
    from dara.analysis_range import validate_analysis_range
    from dara.xrd import load_pattern

    element_list, precursors = _elements_and_precursors(elements, precursor_formulas)
    if not 0 <= mp_e_hull_cutoff <= 1 or not 1 <= max_mp_structures <= 1000:
        raise HTTPException(status_code=400, detail="Invalid Materials Project limits")
    name = Path(pattern_file.filename or "pattern.xy").name
    content = await pattern_file.read()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / name
        path.write_bytes(content)
        try:
            pattern = load_pattern(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")
    try:
        range_params = validate_analysis_range(pattern.angles, two_theta_min, two_theta_max, excluded_ranges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    params = _common_params(
        pattern, elements=element_list, precursors=precursors, wavelength=wavelength,
        range_params=range_params, e_hull=mp_e_hull_cutoff, use_cod=use_cod, use_mp=use_mp,
        max_mp=max_mp_structures, include_theoretical=include_theoretical,
    )
    key = [hashlib.sha1(content).hexdigest(), {k: v for k, v in params.items() if k not in ("angles", "intensities")}]
    return await _start_and_wait(params, key)


class TaskQuickRankingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_theoretical: bool = False


@router.post("/task/{task_id}/quick-ranking")
async def quick_ranking_task(task_id: int, request: TaskQuickRankingRequest | None = None):
    from dara.server.api_router import _saved_task_input

    request = request or TaskQuickRankingRequest()
    record, kwargs, pattern = _saved_task_input(task_id)  # store access: event-loop thread
    search = kwargs.get("search_kwargs") or {}
    refine = search.get("refinement_params") or {}
    elements, precursors = _elements_and_precursors(
        json.dumps(list(kwargs.get("elements") or [])), json.dumps(list(kwargs.get("precursors") or []))
    )
    classes = {db.get("@class") for db in kwargs.get("cif_dbs") or [] if isinstance(db, dict)}
    mp_db = next((db for db in kwargs.get("cif_dbs") or []
                  if isinstance(db, dict) and db.get("@class") == "MaterialsProjectDatabase"), {})
    params = _common_params(
        pattern, elements=elements, precursors=precursors, wavelength=search.get("wavelength", "Cu"),
        range_params=refine, e_hull=kwargs.get("database_e_hull_filter", 0.05),
        use_cod=True if not classes else "CODDatabase" in classes,
        use_mp=True if not classes else "MaterialsProjectDatabase" in classes,
        max_mp=mp_db.get("max_structures", 100), include_theoretical=request.include_theoretical,
    )
    key = [f"task:{task_id}:{record.get('uuid')}", {k: v for k, v in params.items() if k not in ("angles", "intensities")}]
    return await _start_and_wait(params, key)


@router.get("/quick-ranking/{job_id}")
async def quick_ranking_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired quick ranking; start it again.")
    task = job.get("task")
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=2.0)
    return _job_view(job_id, job)
