"""Quick ranking ("search-match") endpoints: candidates to refine, never a result.

``POST /api/quick-ranking`` (multipart pattern upload) and ``POST /api/task/{id}/quick-ranking``
start a ranking in a worker thread (``asyncio.to_thread``) and wait a few seconds for it.
A warm pool answers ``{"status": "done", ...}`` directly; otherwise the answer is
``{"status": "pending", "job_id"}`` and ``GET /api/quick-ranking/{job_id}`` returns the same
shape until the ranking is done. The same inputs reuse a running job, or a finished one for
``REUSE_DONE_S``. The pending view carries ``phase`` ("listing", "downloading", "simulating",
"ranking"), ``done`` and ``total`` (0 when unknown); ``DELETE /api/quick-ranking/{job_id}``
stops the job at its next check and forgets it (downloaded files stay).
``POST /api/quick-ranking/{job_id}/residual`` accepts phases and searches again against what is
left (``quick_rank.residual_step``) on the finished job's pool.
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

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

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


def _add_symbols(rows) -> None:
    from dara.server.api_router import _spacegroup_symbol

    for row in rows:
        number = row.get("spacegroup")
        try:
            row["spacegroup_symbol"] = _spacegroup_symbol(number) if number else None
        except Exception:  # noqa: BLE001
            row["spacegroup_symbol"] = None


def _run(params: dict) -> dict:
    from dara.search.quick_rank import quick_ranking
    from dara.server.reference_library_router import library_candidates

    params = dict(params)
    extra = library_candidates(params.pop("library_ids", None) or [])
    with _RUN_LOCK:
        result = quick_ranking(**params, extra_candidates=extra)
    _add_symbols(result["ranking"] + result["combination"])
    return result


def _job_view(job_id: str, job: dict) -> dict:
    if job["cancel"].is_set():
        return {"status": "cancelled", "job_id": job_id}
    mismatch = job.get("wavelength_mismatch")
    if job["status"] == "done":
        result = {**job["result"], "status": "done", "job_id": job_id, "wavelength_mismatch": mismatch}
        if mismatch:  # not blocking (the user may be right), but stated first
            result["caveats"] = [mismatch["message"], *(result.get("caveats") or [])]
        return result
    if job["status"] == "error":
        return {"status": "error", "job_id": job_id, "error": job["error"]}
    phase, done, total = job["progress"]  # one tuple, swapped atomically by the worker thread
    return {"status": "pending", "job_id": job_id, "elapsed_s": round(time.monotonic() - job["started"], 1),
            "phase": phase, "done": done, "total": total, "wavelength_mismatch": mismatch}


async def _start_and_wait(params: dict, key_parts, wavelength_mismatch: dict | None = None) -> dict:
    job_id = hashlib.sha1(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job["status"] == "error":
            job = None  # a failed ranking is tried again
        elif job is not None and job["status"] == "done" and time.monotonic() - job["finished"] > REUSE_DONE_S:
            job = None  # the library or the MP key may have changed since
        if job is None:
            job = {"status": "pending", "started": time.monotonic(), "result": None, "error": None, "task": None,
                   "progress": ("listing", 0, 0), "cancel": threading.Event(), "params": params,
                   "wavelength_mismatch": wavelength_mismatch}
            _JOBS[job_id] = job
            while len(_JOBS) > _JOBS_MAX:
                _JOBS.popitem(last=False)
            fresh = True
        else:
            _JOBS.move_to_end(job_id)
            fresh = False
    if fresh:
        from dara.structure_library import Cancelled

        def progress(phase, done, total):
            job["progress"] = (phase, int(done), int(total))

        async def runner():
            try:
                result = await asyncio.to_thread(
                    _run, {**params, "progress": progress, "should_stop": job["cancel"].is_set}
                )
                job["candidates"] = result.pop("_candidates", None)  # pool of the residual steps
                job["result"] = result
                job["finished"] = time.monotonic()
                job["status"] = "done"
            except Cancelled:
                job["status"] = "cancelled"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Quick ranking failed", exc_info=True)
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["status"] = "error"

        job["task"] = asyncio.create_task(runner())
    task = job.get("task")
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=WAIT_S)
    return _job_view(job_id, job)


def _exists(ref: str) -> bool:
    from dara.search.quick_rank import reference_path

    try:
        reference_path(ref)
        return True
    except KeyError:
        return False


def _library_ids(raw) -> list[str]:
    """Reference-library CIF ids to rank with the databases (``/api/submit``'s ``library_cif_ids``);
    400 naming an unknown id."""
    from dara.server.api_router import _parse_string_list
    from dara.server.reference_library_router import library_candidates

    ids = list(dict.fromkeys(raw if isinstance(raw, list) else _parse_string_list(raw)))
    try:
        library_candidates(ids)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown reference library CIF: {exc.args[0]}")
    return ids


def background_param(raw) -> dict:
    """Validated search-match background settings (``prefilter.background_settings``) of a request:
    a JSON text (form field) or a dict; empty means the default. 400 when invalid."""
    from dara.search.prefilter import background_settings

    try:
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else None
        if raw is not None and not isinstance(raw, dict):
            raise ValueError("Background settings must be an object.")
        return background_settings(raw)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise HTTPException(status_code=400, detail=f"Invalid background settings: {exc}")


def _common_params(pattern, *, elements, precursors, wavelength, range_params, e_hull, use_cod, use_mp,
                   max_mp, include_theoretical, library_ids=(), background=None) -> dict:
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
        "library_ids": list(library_ids),
        "background": background or background_param(None),
    }


def _elements_and_precursors(elements_raw, precursors_raw) -> tuple[list[str], list[str]]:
    from dara.server.api_router import _parse_string_list, element_symbols, formula_elements
    from dara.server.mass_balance import split_precursor_entries

    try:
        precursors, _ = split_precursor_entries(_parse_string_list(precursors_raw))
        elements = element_symbols(_parse_string_list(elements_raw))
        if not elements:
            elements = formula_elements(precursors)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid elements or formulas: {exc}")
    if not elements:
        raise HTTPException(status_code=400, detail="Enter the elements (or formulas) of the sample.")
    if len(elements) > MAX_ELEMENTS:  # the pool spans every sub-system: 2^n chemical systems
        raise HTTPException(
            status_code=422,
            detail=f"The search-match takes at most {MAX_ELEMENTS} elements ({len(elements)} given); "
            "remove elements that cannot form crystalline phases in the sample.",
        )
    return elements, precursors


async def read_upload(pattern_file, two_theta_min, two_theta_max, excluded_ranges, wavelength=None):
    """``(file name, bytes, pattern, range params)`` of an uploaded pattern (400 when unreadable or
    the range is invalid); with ``wavelength`` also 400 when the file names another anode
    (``/api/submit``'s check)."""
    from dara.analysis_range import validate_analysis_range
    from dara.xrd import load_pattern

    name = Path(pattern_file.filename or "pattern.xy").name
    content = await pattern_file.read()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / name
        path.write_bytes(content)
        try:
            pattern = load_pattern(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")
        if wavelength is not None:
            from dara.server.api_router import _wavelength_mismatch

            mismatch = _wavelength_mismatch(pattern, path, wavelength)
            if mismatch:
                raise HTTPException(status_code=400, detail=mismatch)
    try:
        range_params = validate_analysis_range(pattern.angles, two_theta_min, two_theta_max, excluded_ranges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return name, content, pattern, range_params


def _mismatch(pattern, filename: str, wavelength) -> dict | None:
    """``/api/submit``'s recorded-vs-chosen wavelength check (XRDML), reported, not blocking."""
    from dara.server.api_router import _wavelength_mismatch

    try:
        return _wavelength_mismatch(pattern, Path(filename), str(wavelength))
    except Exception:  # noqa: BLE001 - the ranking goes on without the check
        logger.warning("Wavelength check failed", exc_info=True)
        return None


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
    library_cif_ids: Annotated[str, Form()] = "[]",
    background: Annotated[str, Form()] = "",
):
    """Search-match of an uploaded pattern. ``background``: JSON ``{method: "auto" |
    "sonneveld_visser" | "rolling_minimum", granularity, bending_factor, anchors: [[2theta, y]]}``."""
    element_list, precursors = _elements_and_precursors(elements, precursor_formulas)
    library_ids = _library_ids(library_cif_ids)
    settings = background_param(background)
    if not 0 <= mp_e_hull_cutoff <= 1 or not 1 <= max_mp_structures <= 1000:
        raise HTTPException(status_code=400, detail="Invalid Materials Project limits")
    name, content, pattern, range_params = await read_upload(pattern_file, two_theta_min, two_theta_max, excluded_ranges)
    params = _common_params(
        pattern, elements=element_list, precursors=precursors, wavelength=wavelength,
        range_params=range_params, e_hull=mp_e_hull_cutoff, use_cod=use_cod, use_mp=use_mp,
        max_mp=max_mp_structures, include_theoretical=include_theoretical, library_ids=library_ids,
        background=settings,
    )
    key = [hashlib.sha1(content).hexdigest(), {k: v for k, v in params.items() if k not in ("angles", "intensities")}]
    return await _start_and_wait(params, key, _mismatch(pattern, name, wavelength))


class TaskQuickRankingRequest(BaseModel):
    """Options of the upload variant; each one left out takes the task's stored value."""

    model_config = ConfigDict(extra="forbid")
    include_theoretical: Optional[bool] = None
    library_cif_ids: list[str] = Field(default_factory=list, max_length=50)
    background: Optional[dict] = None
    elements: Optional[list[str]] = None
    precursor_formulas: Optional[list[str]] = None
    wavelength: Optional[str] = None
    two_theta_min: Optional[float] = None
    two_theta_max: Optional[float] = None
    excluded_ranges: Optional[list[list[float]]] = None
    use_cod: Optional[bool] = None
    use_mp: Optional[bool] = None
    mp_e_hull_cutoff: Optional[float] = None
    max_mp_structures: Optional[int] = None


@router.post("/task/{task_id}/quick-ranking")
async def quick_ranking_task(task_id: int, request: TaskQuickRankingRequest | None = None):
    """Search-match of a stored task's pattern (e.g. reopening an identification result) with the
    task's settings, each overridable as on the upload variant; the XRDML wavelength check of the
    stored file applies as there (reported, not blocking)."""
    from dara.analysis_range import validate_analysis_range
    from dara.server.api_router import _saved_task_input, stored_search_match

    request = request or TaskQuickRankingRequest()
    record, kwargs, pattern = _saved_task_input(task_id)  # store access: event-loop thread
    search = kwargs.get("search_kwargs") or {}
    refine = search.get("refinement_params") or {}
    elements, precursors = _elements_and_precursors(
        json.dumps(request.elements if request.elements is not None else list(kwargs.get("elements") or [])),
        json.dumps(request.precursor_formulas if request.precursor_formulas is not None
                   else list(kwargs.get("precursors") or [])),
    )
    if any(value is not None for value in (request.two_theta_min, request.two_theta_max, request.excluded_ranges)):
        try:
            refine = validate_analysis_range(
                pattern.angles,
                request.two_theta_min if request.two_theta_min is not None else refine.get("wmin"),
                request.two_theta_max if request.two_theta_max is not None else refine.get("wmax"),
                json.dumps(request.excluded_ranges if request.excluded_ranges is not None
                           else refine.get("excluded_ranges") or []),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    classes = {db.get("@class") for db in kwargs.get("cif_dbs") or [] if isinstance(db, dict)}
    mp_db = next((db for db in kwargs.get("cif_dbs") or []
                  if isinstance(db, dict) and db.get("@class") == "MaterialsProjectDatabase"), {})
    saved = ((record.get("job") or {}).get("metadata") or {}).get("submission_settings") or {}
    ranked = stored_search_match(kwargs, saved)  # an identification task's own search-match settings
    library_ids = request.library_cif_ids or [  # its accepted library structures that still exist
        ref[4:] for ref in saved.get("reference_ids") or [] if str(ref).startswith("lib:") and _exists(ref)]

    def option(name, stored):
        value = getattr(request, name)
        return stored if value is None else value

    e_hull = option("mp_e_hull_cutoff", ranked.get("mp_e_hull_cutoff", kwargs.get("database_e_hull_filter", 0.05)))
    max_mp = option("max_mp_structures", ranked.get("max_mp_structures", mp_db.get("max_structures", 100)))
    if not 0 <= e_hull <= 1 or not 1 <= max_mp <= 1000:
        raise HTTPException(status_code=400, detail="Invalid Materials Project limits")
    params = _common_params(
        pattern, elements=elements, precursors=precursors,
        wavelength=option("wavelength", search.get("wavelength", "Cu")),
        range_params=refine, e_hull=e_hull,
        use_cod=option("use_cod", ranked.get("use_cod", True if not classes else "CODDatabase" in classes)),
        use_mp=option("use_mp", ranked.get("use_mp", True if not classes else "MaterialsProjectDatabase" in classes)),
        max_mp=max_mp,
        include_theoretical=option("include_theoretical", ranked.get("include_theoretical", False)),
        library_ids=_library_ids(library_ids),
        # an identification task's own background, unless the request sets one
        background=background_param(request.background if request.background is not None else search.get("background")),
    )
    key = [f"task:{task_id}:{record.get('uuid')}", {k: v for k, v in params.items() if k not in ("angles", "intensities")}]
    return await _start_and_wait(params, key, _mismatch(pattern, record["job"].get("name") or "", params["wavelength"]))


@router.get("/quick-ranking/{job_id}")
async def quick_ranking_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired search-match; start it again.")
    task = job.get("task")
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=2.0)
    return _job_view(job_id, job)


class ResidualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: list[str] = Field(default_factory=list, max_length=12)


@router.post("/quick-ranking/{job_id}/residual")
async def quick_ranking_residual(job_id: str, request: ResidualRequest):
    """Accept ``selected`` (source ids, in order) and look at what is left: the share of the net
    intensity they explain together, the residual net pattern, its unexplained peaks and the
    ranking against the residual (``next``), as one more step of the greedy combination. Reuses
    the finished job's pool; nothing is downloaded or simulated."""
    from dara.search.quick_rank import residual_step

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None or job["cancel"].is_set():
        raise HTTPException(status_code=404, detail="Unknown or expired search-match; start it again.")
    if job["status"] != "done" or not job.get("candidates"):
        raise HTTPException(status_code=409, detail="The search-match has not finished.")
    params = job["params"]
    selected = list(dict.fromkeys(source.strip() for source in request.selected))
    try:
        result = await asyncio.to_thread(
            residual_step, params["angles"], params["intensities"], job["candidates"], selected,
            wavelength=params["wavelength"], wmin=params["wmin"], wmax=params["wmax"],
            excluded_ranges=params["excluded_ranges"], background=params.get("background"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"{exc.args[0]} is not a ranked structure of this search-match.")
    _add_symbols(result["next"])
    return {"job_id": job_id, "selected": selected, **result}


@router.post("/quick-ranking/{job_id}/background")
async def quick_ranking_background(job_id: str, settings: Optional[dict] = Body(None)):
    """Background curve only (no ranking) of the job's pattern for ``settings`` (the ranking's
    ``background`` object): ``{two_theta, observed, intensity, settings}``, decimated like the
    ranking's ``background``, so the settings can be tried before the ranking is run with them."""
    from dara.search.prefilter import observed_profile
    from dara.search.quick_rank import CONFIG, background_view

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None or job["cancel"].is_set():
        raise HTTPException(status_code=404, detail="Unknown or expired search-match; start it again.")
    params, chosen = job["params"], background_param(settings)
    profile = await asyncio.to_thread(
        observed_profile, params["angles"], params["intensities"], wmin=params["wmin"], wmax=params["wmax"],
        excluded_ranges=params["excluded_ranges"], config=CONFIG, background=chosen,
    )
    if profile.background is None:
        raise HTTPException(status_code=422, detail="The pattern is too short for a background.")
    return background_view(profile, chosen)


@router.delete("/quick-ranking/{job_id}")
async def quick_ranking_cancel(job_id: str):
    """Stop a ranking at its next check and forget it, so the same request starts afresh."""
    with _JOBS_LOCK:
        job = _JOBS.pop(job_id, None)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired search-match; start it again.")
    job["cancel"].set()
    return {"status": "cancelled", "job_id": job_id}
