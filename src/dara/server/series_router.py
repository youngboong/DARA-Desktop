"""Series batch analysis: submit several patterns as one ordered series and inspect it."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dara.cif import Cif
from dara.series import parse_series_variables, series_order
from dara.server.task_summary import SUMMARY_REFRESH_GAVE_UP
from dara.server.utils import convert_to_local_tz, get_worker_store
from dara.xrd import load_pattern

logger = logging.getLogger("dara.server.series")

router = APIRouter(prefix="/api")

MAX_SERIES_FILES = 50
MIN_SERIES_FILES = 2
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Tasks whose summary could not be upgraded to the current schema; do not decode
# their stored documents again on every poll of the series page (shared with /api/tasks).
_summary_refresh_gave_up = SUMMARY_REFRESH_GAVE_UP


def _build_job(pattern, name, **fields):
    """Build one analysis job (wrapper so tests can replace it)."""
    from dara.server.api_router import build_submission_job

    return build_submission_job(pattern, name, **fields)


def _queue(job, user):
    """Queue a job and return its index (wrapper so tests can replace it)."""
    from dara.server.worker import add_job_to_queue

    return add_job_to_queue(job, user=user)


def _wavelength_mismatch(pattern, path, wavelength):
    """Recorded-vs-selected wavelength check (wrapper so tests can replace it)."""
    from dara.server.api_router import _wavelength_mismatch as check

    return check(pattern, path, wavelength)


class SeriesPreviewRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list, max_length=MAX_SERIES_FILES)


def _preview(filenames: list[str]) -> dict:
    parsed = parse_series_variables(filenames)
    order = series_order(filenames, parsed["variables"])
    return {
        "unit": parsed["unit"],
        "items": [
            {"filename": name, "order": position, "variable": variable}
            for name, position, variable in zip(filenames, order, parsed["variables"])
        ],
    }


@router.post("/series/preview")
async def preview_series(request: SeriesPreviewRequest):
    """Parse the ordering variable from file names without touching any file."""
    return _preview([Path(name).name for name in request.filenames])


# Modes that search the element system; the others refine the phases the user entered.
PER_FILE_CHEMISTRY_MODES = ("elemental", "search_match")


def file_chemistry(filename: str, enabled: bool) -> dict:
    """``elements`` / ``precursor_formulas`` form fields from the file name (``{}`` when off or none)."""
    import json

    from dara.filename_chemistry import suggest_filename_chemistry

    if not enabled:
        return {}
    chemistry = suggest_filename_chemistry(filename)
    if not chemistry.get("elements"):
        return {}
    return {"elements": json.dumps(chemistry["elements"]), "precursor_formulas": json.dumps(chemistry.get("formulas") or [])}


def _prefixed(exc: HTTPException, filename: str) -> HTTPException:
    if isinstance(exc.detail, str):
        return HTTPException(status_code=exc.status_code, detail=f"{filename}: {exc.detail}")
    return exc


@router.post("/submit-series")
async def submit_series(
    user: Annotated[str, Form()],
    series_name: Annotated[str, Form()],
    pattern_files: Annotated[list[UploadFile], File()],
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
    additional_phases: Annotated[list[UploadFile], File()] = None,
    library_cif_ids: Annotated[str, Form()] = "[]",
    two_theta_min: Annotated[float | None, Form()] = None,
    two_theta_max: Annotated[float | None, Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    refinement_controls: Annotated[str, Form()] = "{}",
    confirm_wavelength: Annotated[bool, Form()] = False,
    candidate_prefilter: Annotated[Optional[bool], Form()] = None,
    screening_timeout_s: Annotated[Optional[float], Form()] = None,
    max_analysis_minutes: Annotated[Optional[float], Form()] = None,
    phase_set_task_id: Annotated[Optional[int], Form()] = None,
    per_file_chemistry: Annotated[bool, Form()] = False,
):
    """Validate and build every job first, then queue them all in series order.

    ``per_file_chemistry``: each file runs with the elements and formulas its name gives (a batch
    of different samples); a name without chemistry runs with the form's elements and formulas. Only
    the modes that search the element system take it: the phases entered for a Known-phase or
    Reaction run are never replaced by a file name."""
    per_file_chemistry = per_file_chemistry and analysis_mode in PER_FILE_CHEMISTRY_MODES
    name = (series_name or "").strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(status_code=400, detail="Series name must be 1 to 120 characters")
    files = list(pattern_files or [])
    if not MIN_SERIES_FILES <= len(files) <= MAX_SERIES_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A series needs {MIN_SERIES_FILES} to {MAX_SERIES_FILES} pattern files",
        )
    filenames = [Path(item.filename or "pattern.xy").name for item in files]
    duplicates = sorted({item for item in filenames if filenames.count(item) > 1})
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate file names in the series: {', '.join(duplicates)}",
        )

    try:
        cifs = []
        if additional_phases:
            with tempfile.TemporaryDirectory() as temp_dir:
                for phase in additional_phases:
                    phase_name = Path(phase.filename or "reference.cif").name
                    with open(Path(temp_dir) / phase_name, "wb") as handle:
                        shutil.copyfileobj(phase.file, handle)
                    cif_file = Cif.from_file(Path(temp_dir) / phase_name)
                    if not cif_file.data:
                        raise HTTPException(status_code=400, detail="Invalid additional CIF file provided")
                    cifs.append(cif_file)

        fields = {
            "analysis_mode": analysis_mode,
            "precursor_formulas": precursor_formulas,
            "elements": elements,
            "instrument_profile": instrument_profile,
            "wavelength": wavelength,
            "temperature": temperature,
            "atmosphere": atmosphere,
            "use_cod": use_cod,
            "use_mp": use_mp,
            "mp_e_hull_cutoff": mp_e_hull_cutoff,
            "max_mp_structures": max_mp_structures,
            "library_cif_ids": library_cif_ids,
            "two_theta_min": two_theta_min,
            "two_theta_max": two_theta_max,
            "excluded_ranges": excluded_ranges,
            "refinement_controls": refinement_controls,
            "candidate_prefilter": candidate_prefilter,
            "screening_timeout_s": screening_timeout_s,
            "max_analysis_minutes": max_analysis_minutes,
        }
        if phase_set_task_id is not None:  # every file of the series gets the same exact structures
            from dara.server.api_router import resolve_phase_set

            fields["phase_set_cifs"] = await resolve_phase_set(phase_set_task_id)
            fields["phase_set_from"] = phase_set_task_id
        jobs = []
        for upload, filename in zip(files, filenames):
            try:
                with tempfile.TemporaryDirectory() as pattern_dir:
                    pattern_path = Path(pattern_dir) / filename
                    with open(pattern_path, "wb") as handle:
                        shutil.copyfileobj(upload.file, handle)
                    try:
                        pattern = load_pattern(pattern_path)
                    except Exception as exc:
                        raise HTTPException(
                            status_code=400, detail=f"Could not read pattern: {exc}"  # prefixed below
                        )
                    if not confirm_wavelength:
                        mismatch = _wavelength_mismatch(pattern, pattern_path, wavelength)
                        if mismatch:
                            raise HTTPException(status_code=400, detail={**mismatch, "filename": filename})
                jobs.append(_build_job(pattern, filename, **{**fields, **file_chemistry(filename, per_file_chemistry)},
                                       additional_cifs=list(cifs)))
            except HTTPException as exc:
                raise _prefixed(exc, filename) from exc
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"{filename}: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Series submission failed", exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed = parse_series_variables(filenames)
    order = series_order(filenames, parsed["variables"])
    series_id = uuid4().hex[:12]
    for job, position, variable in zip(jobs, order, parsed["variables"]):
        job.metadata["series"] = {
            "id": series_id,
            "name": name,
            "order": position,
            "size": len(jobs),
            "variable": variable,
        }
    queued = sorted(range(len(jobs)), key=lambda index: order[index])
    task_ids = [_queue(jobs[index], user) for index in queued]
    return {
        "message": "submitted",
        "series_id": series_id,
        "task_ids": task_ids,
        "variables": [parsed["variables"][index] for index in queued],
        "unit": parsed["unit"],
    }


# --- listing -------------------------------------------------------------------------

_LIST_PROPERTIES = ["index", "status", "submitted_time", "job.metadata.series", "job.name"]
_DETAIL_PROPERTIES = [
    "index", "uuid", "status", "submitted_time", "start_time", "end_time", "summary",
    "job.metadata", "job.name", "user", "review.selection", "error",
]
# Fields of a series item taken from its Results list row (``api_router.list_row``).
_ROW_FIELDS = ("sample", "adopted", "created_on", "user", "error_summary", "progress")


def _series_of(record: dict) -> dict | None:
    series = ((record.get("job") or {}).get("metadata") or {}).get("series")
    return series if isinstance(series, dict) and series.get("id") else None


def _query_series(properties: list[str], series_id: str | None = None) -> list[dict]:
    criteria = (
        {"job.metadata.series.id": series_id}
        if series_id
        else {"job.metadata.series": {"$exists": True}}
    )
    with get_worker_store() as store:
        try:
            records = list(store.query(criteria=criteria, properties=properties))
        except Exception:
            logger.info("Series query failed; scanning all tasks", exc_info=True)
            records = list(store.query({}, properties=properties))
    return [record for record in records if (series := _series_of(record)) and (not series_id or series["id"] == series_id)]


def _stamp(value) -> str | None:
    if value is None:
        return None
    try:
        return convert_to_local_tz(value).strftime(TIME_FORMAT)
    except Exception:
        return None


def _unit_of(variables) -> str | None:
    return next((item.get("unit") for item in variables if isinstance(item, dict)), None)


@router.get("/series")
async def list_series():
    """Series overview, newest first."""
    groups: dict[str, dict] = {}
    for record in _query_series(_LIST_PROPERTIES):
        series = _series_of(record)
        group = groups.setdefault(
            series["id"],
            {"series_id": series["id"], "name": series.get("name") or "", "times": [], "n_tasks": 0,
             "n_completed": 0, "variables": []},
        )
        group["n_tasks"] += 1
        group["n_completed"] += record.get("status") == "COMPLETED"
        group["variables"].append(series.get("variable"))
        if record.get("submitted_time") is not None:
            group["times"].append(record["submitted_time"])
    items = []
    for group in groups.values():
        created = min(group["times"]) if group["times"] else None
        items.append((
            created,
            {
                "series_id": group["series_id"],
                "name": group["name"],
                "created_on": _stamp(created),
                "n_tasks": group["n_tasks"],
                "n_completed": group["n_completed"],
                "unit": _unit_of(group["variables"]),
            },
        ))
    items.sort(key=lambda pair: (_sort_time(pair[0]), pair[1]["series_id"]), reverse=True)
    return {"series": [item for _, item in items]}


def _sort_time(value) -> float:
    try:
        return value.timestamp() if value.tzinfo else value.replace(tzinfo=None).timestamp()
    except Exception:
        return 0.0


def _refreshed_summary(record: dict) -> dict | None:
    """Return a current-schema summary for a completed task, upgrading and persisting if needed."""
    from dara.server import task_summary

    # ``get_worker_store`` is resolved here so tests can replace it on this module.
    return task_summary.refresh_stale_summary(record, lambda: get_worker_store())


@router.get("/series/{series_id}")
async def get_series(series_id: str):
    records = _query_series(_DETAIL_PROPERTIES, series_id)
    if not records:
        raise HTTPException(status_code=404, detail="Series not found")
    from dara.server.api_router import list_row

    entries = []
    for record in records:
        series = _series_of(record)
        summary = _refreshed_summary(record)
        row = list_row(record, summary)
        entries.append({
            **{key: row[key] for key in _ROW_FIELDS},
            "task_id": record["index"],
            "name": (record.get("job") or {}).get("name"),
            "order": series.get("order", 0),
            "variable": series.get("variable"),
            "state": record.get("status"),
            "summary": summary,
            "_time": record.get("submitted_time"),
        })
    entries.sort(key=lambda item: (item["order"], item["task_id"]))
    times = [item.pop("_time") for item in entries if item["_time"] is not None]
    for item in entries:
        item.pop("_time", None)
    first = _series_of(records[0])
    return {
        "series_id": series_id,
        "name": first.get("name") or "",
        "unit": _unit_of([item["variable"] for item in entries]),
        "created_on": _stamp(min(times)) if times else None,
        "items": entries,
    }


# ---------------------------------------------------------------------------
# stacked-pattern figure of a series
# ---------------------------------------------------------------------------

DEFAULT_MIN_WEIGHT_PERCENT = 1.0  # refined phases below this in every task start unselected
_FIGURE_MEDIA = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}


def _one_phase(a: tuple, b: tuple) -> bool:
    """Same phase across tasks: the same-phase rule of the confidence checks, and, when both
    database space groups are known, the same one (anatase and rutile stay two strips)."""
    from dara.server.confidence import same_phase

    return same_phase(a, b) and (a[1] is None or b[1] is None or a[1] == b[1])


def _plain_phase_label(name: str) -> str:
    import re
    from html import unescape

    from dara.plot import hydroxide, phase_display_name

    # the database record stays in the report; the picker names the phase as the figure does
    return hydroxide(unescape(re.sub(r"<[^>]+>", "", phase_display_name(name))))


def group_phases(tasks: list[tuple[int, list[str], dict]]) -> list[dict]:
    """One entry per phase across ``tasks`` (``(task id, phase names, {name: weight fraction})`` in
    series order): ``{key, identity, names: {task id: name}, max_weight, first}``, the largest amount
    first, otherwise in order of appearance. Two phases of one task are never merged."""
    from dara.server.confidence import phase_identity

    groups: list[dict] = []
    for task_id, names, weights in tasks:
        for name in names:
            identity = phase_identity(name)
            group = next((group for group in groups if task_id not in group["names"]
                          and _one_phase(identity, group["identity"])), None)
            if group is None:
                group = {"key": name, "identity": identity, "names": {}, "max_weight": None, "first": len(groups)}
                groups.append(group)
            group["names"][task_id] = name
            if name in weights:
                group["max_weight"] = max(group["max_weight"] or 0.0, 100.0 * float(weights[name]))
    groups.sort(key=lambda group: (-(group["max_weight"] or 0.0), group["first"]))
    return groups


async def _series_figure_data(series_id: str) -> dict:
    """Completed tasks of a series in series order with their adopted result (or identification),
    and the union of their phases grouped into one entry per phase."""
    from dara.server import review_router
    from dara.server.api_router import list_row

    records = _query_series(_DETAIL_PROPERTIES, series_id)
    if not records:
        raise HTTPException(status_code=404, detail="Series not found")
    records.sort(key=lambda record: (_series_of(record).get("order", 0), record["index"]))
    tasks, phase_lists = [], []
    for record in records:
        if record.get("status") != "COMPLETED":
            continue
        try:
            task, document = await review_router._load_completed_task(record["index"])
            selection = review_router._report_selection(None, False, review_router._review_metadata(task), None)
            refinement, _ = review_router._resolve(document, selection, task)
            names = list(refinement.lst_data.phases_results)
        except Exception:  # noqa: BLE001 - a task without a usable result is left out of the figure
            logger.info("Series figure: task %s has no usable result", record.get("index"), exc_info=True)
            continue
        try:
            weights = refinement.get_phase_weights()
        except Exception:  # noqa: BLE001 - an identification has no weight fractions
            weights = {}
        file_name = (record.get("job") or {}).get("name") or f"Task {record['index']}"
        sample = list_row(record, _refreshed_summary(record)).get("sample")
        tasks.append({"task_id": record["index"], "sample": sample, "stem": Path(str(file_name)).stem,
                      "document": document, "refinement": refinement})
        phase_lists.append((record["index"], names, weights))
    # File names of a series share most of their text; the figure shows only the part that differs.
    shortened = iter(short_labels([item["stem"] for item in tasks if not item["sample"]]))
    for item in tasks:
        item["label"] = item["sample"] or next(shortened)
    groups = group_phases(phase_lists)
    for group in groups:
        group["label"] = _plain_phase_label(group["key"])
    return {"tasks": tasks, "groups": groups}


def _default_phase(group: dict) -> bool:
    return group["max_weight"] is None or group["max_weight"] >= DEFAULT_MIN_WEIGHT_PERCENT


def short_labels(stems: list[str]) -> list[str]:
    """The part of each file name that differs within the series: the text shared by all at the start
    and at the end is dropped at a separator (``_``, ``-``, space, ``.``). A single name, or one that
    would become empty, is kept whole."""
    if len(stems) < 2:
        return list(stems)
    separators = "_- ."

    def shared_prefix(values):
        first = values[0]
        n = 0
        while n < len(first) and all(len(v) > n and v[n] == first[n] for v in values):
            n += 1
        while n > 0 and first[n - 1] not in separators:  # back to a separator
            n -= 1
        return n

    start = shared_prefix(stems)
    end = shared_prefix([stem[::-1] for stem in stems])
    out = []
    for stem in stems:
        part = stem[start:len(stem) - end] if len(stem) - end > start else ""
        part = part.strip(separators)
        out.append(part or stem)
    return out


@router.get("/series/{series_id}/figure-options")
async def series_figure_options(series_id: str):
    """Tasks and phases a series figure can show, with the default selection."""
    data = await _series_figure_data(series_id)
    return {
        "tasks": [{"task_id": item["task_id"], "label": item["label"]} for item in data["tasks"]],
        "phases": [{
            "key": group["key"], "label": group["label"], "tasks": list(group["names"]),
            "max_weight_percent": None if group["max_weight"] is None else round(group["max_weight"], 2),
            "default": _default_phase(group),
        } for group in data["groups"]],
    }


@router.get("/series/{series_id}/figure.{fmt}")
async def series_figure(
    series_id: str,
    fmt: str,
    tasks: Optional[str] = None,
    phases: Optional[str] = None,
    normalize: bool = False,
    width_mm: float = 170,
    dpi: int = 600,
    download: bool = False,
    peaks: Literal["hkl", "symbols", "none"] = "hkl",
    names: Literal["legend", "strips"] = "legend",
    bars: bool = True,
    labels: Optional[str] = None,
):
    """Stacked measured patterns of the series (first task at the bottom) with one reference strip
    per phase and each phase's peaks marked where it was found. ``tasks``: comma-separated task ids
    (default: all completed); ``phases``: comma-separated phase keys of ``figure-options`` (default:
    its default selection; empty: none); ``normalize``: each pattern scaled to its own maximum;
    ``peaks`` (hkl over the top pattern, symbols on every pattern, none), ``names`` (legend or strips)
    and ``bars`` (reference strips): the style."""
    import numpy as np
    from starlette.concurrency import run_in_threadpool

    from dara.server import review_router
    from dara.server.report.figures import (
        hkl_candidates, interleaved_hkl, pattern_figure, peak_marks, phase_strip_label, result_lines,
    )
    from dara.server.utils import safe_download_stem

    if fmt not in _FIGURE_MEDIA:
        raise HTTPException(404, "Unsupported figure format; use png, svg or pdf")
    data = await _series_figure_data(series_id)
    chosen = data["tasks"]
    if tasks is not None:
        wanted = {part.strip() for part in tasks.split(",") if part.strip()}
        chosen = [item for item in chosen if str(item["task_id"]) in wanted]
    if not chosen:
        raise HTTPException(422, "No completed task of this series is selected")
    if labels:  # the analyst's own names, JSON {task id: text}
        try:
            custom = {str(key): str(value).strip()[:80] for key, value in json.loads(labels).items()}
        except (ValueError, AttributeError):
            raise HTTPException(422, "labels must be a JSON object {task id: text}")
        chosen = [{**item, "label": custom.get(str(item["task_id"])) or item["label"]} for item in chosen]
    if phases is None:
        groups = [group for group in data["groups"] if _default_phase(group)]
    else:
        # shortcut: keys are phase names joined by commas; a name with a comma would need a list parameter
        wanted = {part for part in phases.split(",") if part}
        groups = [group for group in data["groups"] if group["key"] in wanted]

    def render() -> bytes:
        low = min(float(np.nanmin(item["refinement"].plot_data.x)) for item in chosen)
        high = max(float(np.nanmax(item["refinement"].plot_data.x)) for item in chosen)
        strips = []
        for group in groups:
            # the reference of the first shown task that has the phase
            task_id = next((item["task_id"] for item in chosen if item["task_id"] in group["names"]),
                           next(iter(group["names"])))
            item = next(entry for entry in data["tasks"] if entry["task_id"] == task_id)
            name = group["names"][task_id]
            lines = review_router.figure_references(item["document"], item["refinement"], [name]).get(name)
            tt, relative = lines if lines is not None else result_lines(item["refinement"], name, low, high)
            strips.append({"label": phase_strip_label(name), "two_theta": tt, "intensity": relative})
        patterns = []
        for item in chosen:
            refinement = item["refinement"]
            marks = {index: peak_marks(refinement, group["names"][item["task_id"]], low, high)
                     for index, group in enumerate(groups) if item["task_id"] in group["names"]} if peaks == "symbols" else {}
            patterns.append({"x": refinement.plot_data.x, "y": refinement.plot_data.y_obs,
                             "label": item["label"], "marks": marks})
        # hkl over the top pattern (the last task), for the phases present in it, in their strip colours
        last = chosen[-1]
        hkl = interleaved_hkl([
            hkl_candidates(last["refinement"], group["names"][last["task_id"]], low, high)
            if last["task_id"] in group["names"] else [] for group in groups]) if peaks == "hkl" else None
        return pattern_figure(patterns, strips, fmt=fmt, dpi=dpi, width_mm=width_mm, normalize=normalize,
                              radiation=review_router.figure_radiation(chosen[0]["document"]), hkl=hkl or None,
                              symbols=peaks == "symbols", names=names, bars=bars)

    try:
        content = await run_in_threadpool(render)
    except Exception as exc:
        raise HTTPException(500, f"Figure rendering failed: {exc}") from exc
    series = _series_of(_query_series(_LIST_PROPERTIES, series_id)[0]) or {}
    filename = f"dara_series_{safe_download_stem(series.get('name') or series_id)}_patterns_{round(float(width_mm))}mm.{fmt}"
    disposition = "attachment" if download else "inline"
    return Response(
        content,
        media_type=_FIGURE_MEDIA[fmt],
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )
