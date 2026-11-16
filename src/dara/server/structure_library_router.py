"""Structure library endpoints: pre-download pack structures for offline use and instant ranking.

The download runs ``python -m dara.structure_library download --progress-json`` as a child
process (one at a time) and keeps its last progress line on the server, so a browser
refresh shows the running download again.

* ``GET  /api/structure-library[?elements=Li,O]`` packs, estimates, cache status, the job
* ``POST /api/structure-library/download`` ``{packs, elements, sources, sticks}``
* ``GET  /api/structure-library/progress``
* ``POST /api/structure-library/cancel``
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dara.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

CANCEL_GRACE_S = 20.0
_LOCK = threading.Lock()
_JOB: dict = {"process": None}
_MEMO: dict = {}
STATUS_TTL_S = 15.0
ESTIMATE_TTL_S = 600.0


def _job_view() -> dict | None:
    with _LOCK:
        if not _JOB.get("started"):
            return None
        process = _JOB.get("process")
        running = process is not None and process.poll() is None
        return {
            "running": running,
            "request": _JOB.get("request"),
            "started_at": _JOB.get("started"),
            "last": _JOB.get("last"),
            "phases": dict(_JOB.get("phases") or {}),
            "exit_code": None if running else _JOB.get("exit_code"),
            "cancelling": bool(_JOB.get("cancelling")) and running,
        }


def _memo(key, ttl, compute):
    hit = _MEMO.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = compute()
    _MEMO[key] = (time.monotonic(), value)
    return value


def _overview(elements: list[str]) -> dict:
    from dara import structure_library as library

    data = library.load_packs()
    packs = []
    for pack in data["packs"]:
        packs.append({
            **{k: pack[k] for k in ("id", "name", "description", "elements")},
            "estimate": _memo(("estimate", pack["id"]), ESTIMATE_TTL_S, lambda p=pack: library.estimate([p["id"]], (), data)),
            "status": _memo(("status", pack["id"]), STATUS_TTL_S, lambda p=pack: library.status([p["id"]], (), data)),
        })
    custom = None
    if elements:
        key = tuple(sorted(elements))
        custom = {
            "elements": list(key),
            "estimate": _memo(("estimate", key), ESTIMATE_TTL_S, lambda: library.estimate((), key, data)),
            "status": _memo(("status", key), STATUS_TTL_S, lambda: library.status((), key, data)),
        }
    from dara import known_phases

    return {"packs": packs, "custom": custom, "mp_configured": known_phases.mp_configured(),
            "measured": data["measured"]}


@router.get("/structure-library")
async def structure_library(elements: str = ""):
    symbols = [s for s in elements.replace(",", " ").split() if s]
    try:
        from dara.structure_library import element_set

        if symbols:
            symbols = element_set((), symbols)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid elements: {exc}")
    overview = await asyncio.to_thread(_overview, symbols)
    return {**overview, "job": _job_view()}


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packs: list[str] = Field(default_factory=list, max_length=8)
    elements: list[str] = Field(default_factory=list, max_length=118)
    sources: list[Literal["cod", "mp"]] = Field(default_factory=lambda: ["cod", "mp"], min_length=1)
    sticks: bool = True


def _reader(process, cancel_file: Path) -> None:
    for raw in process.stdout:
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        with _LOCK:
            _JOB["last"] = line
            if line.get("phase") in ("cod", "mp", "sticks"):
                _JOB.setdefault("phases", {})[line["phase"]] = line
    code = process.wait()
    with _LOCK:
        if _JOB.get("cancelling") and code not in (0, 2):
            # killed after the grace period (e.g. inside a blocking query): a cancel, not an error
            code = 2
            _JOB["last"] = {"phase": "cancelled", "done": 0, "total": 0, "bytes": 0, "eta_s": None,
                            "message": "Stopped; run the download again to resume."}
        _JOB["exit_code"] = code
        _JOB["finished"] = time.time()
    cancel_file.unlink(missing_ok=True)
    _MEMO.clear()  # the cache status changed


def _command(request: DownloadRequest, cancel_file: Path) -> list[str]:
    command = [sys.executable, "-m", "dara.structure_library", "download", "--progress-json",
               "--sources", ",".join(request.sources), "--cancel-file", str(cancel_file)]
    if request.packs:
        command += ["--packs", *request.packs]
    if request.elements:
        command += ["--elements", *request.elements]
    if not request.sticks:
        command.append("--no-sticks")
    return command


def _log_path() -> Path:
    from dara.search.quick_rank import stick_cache_dir

    return stick_cache_dir().parent / "structure_library.log"


@router.post("/structure-library/download")
async def start_download(request: DownloadRequest):
    from dara.structure_library import element_set

    try:
        element_set(request.packs, request.elements)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with _LOCK:
        process = _JOB.get("process")
        if process is not None and process.poll() is None:
            raise HTTPException(status_code=409, detail="A structure download is already running.")
        cancel_file = Path(tempfile.gettempdir()) / f"dara-structure-library-{os.getpid()}.cancel"
        cancel_file.unlink(missing_ok=True)
        log = _log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with open(log, "ab") as stderr:
            process = subprocess.Popen(
                _command(request, cancel_file), stdout=subprocess.PIPE, stderr=stderr,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                creationflags=flags,
            )
        _JOB.clear()
        _JOB.update(process=process, started=time.time(), request=request.model_dump(), last=None,
                    phases={}, exit_code=None, cancel_file=str(cancel_file), cancelling=False)
    threading.Thread(target=_reader, args=(process, cancel_file), daemon=True).start()
    return {"job": _job_view()}


@router.get("/structure-library/progress")
async def download_progress():
    return {"job": _job_view()}


def _kill_later(process) -> None:
    try:
        process.wait(timeout=CANCEL_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()  # every file is written atomically, so a hard stop is safe too


@router.post("/structure-library/cancel")
async def cancel_download():
    with _LOCK:
        process = _JOB.get("process")
        if process is None or process.poll() is not None:
            return {"job": _job_view()}
        Path(_JOB["cancel_file"]).touch()
        _JOB["cancelling"] = True
    threading.Thread(target=_kill_later, args=(process,), daemon=True).start()
    return {"job": _job_view()}


@atexit.register
def _stop_on_exit() -> None:
    process = _JOB.get("process")
    if process is not None and process.poll() is None:
        process.kill()
