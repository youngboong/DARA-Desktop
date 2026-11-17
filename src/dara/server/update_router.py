"""In-app update of DARA Desktop from its GitHub releases.

``GET /api/update`` compares the running version with the newest release. ``POST /api/update/install``
downloads the release installer ``DARA-Setup-<version>.exe``, checks its size and SHA-256 against the
release metadata and asks DARA.exe to run it: DARA.exe checks the hash again, starts the installer
with ``/UPDATE`` and closes; the installer replaces the files and starts the new DARA.exe
(packaging/launcher/DaraLauncher.cs, packaging/DARA.iss).

Only the bundled app started by DARA.exe can install updates: DARA.exe passes its window handle
and update folder in ``DARA_LAUNCHER_WINDOW`` and ``DARA_UPDATE_DIR``. Nothing but the
``User-Agent`` is sent to GitHub.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

import dara
from dara.server import presence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

FEED_URL = "https://api.github.com/repos/youngboong/DARA-Desktop/releases/latest"
FEED_ENV = "DARA_UPDATE_FEED"  # tests only: another feed URL or a local JSON file
WINDOW_ENV = "DARA_LAUNCHER_WINDOW"
DIR_ENV = "DARA_UPDATE_DIR"
UPDATE_MESSAGE = "DARA-Desktop-Update"  # registered window message, same name in DaraLauncher.cs
DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
CACHE_S = 6 * 3600
TIMEOUT_S = 5
DOWNLOAD_TIMEOUT_S = 60
MAX_FEED_BYTES = 2 * 2**20
MAX_NOTES = 20000
UNVERIFIABLE = "The update cannot be verified"
BUSY = ("downloading", "verifying", "installing")

_lock = threading.Lock()
_check: dict = {"at": None, "result": None}
_job: dict = {"state": "idle", "progress": None, "error": None}


def parse_version(text) -> tuple[int, ...] | None:
    """``"v1.3.10"`` -> ``(1, 3, 10)``; ``None`` for anything but dot-separated numbers."""
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(latest, current) -> bool:
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def current_version() -> str:
    return dara.__version__


def is_packaged() -> bool:
    """The bundled app started by DARA.exe (a source checkout has pyproject.toml next to src)."""
    root = Path(dara.__file__).resolve().parents[2]
    return not (root / "pyproject.toml").is_file() and bool(os.environ.get(WINDOW_ENV)) and bool(os.environ.get(DIR_ENV))


def check_download_url(url) -> str:
    parsed = urlparse(str(url or ""))
    if os.environ.get(FEED_ENV):  # a test feed serves its installer from a local server
        allowed = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    else:
        allowed = parsed.scheme == "https" and (parsed.hostname or "").lower() in DOWNLOAD_HOSTS
    if not allowed:
        raise ValueError(f"Updates are only downloaded from GitHub, not from {parsed.hostname or url!r}.")
    return str(url)


class _CheckedRedirects(HTTPRedirectHandler):
    """GitHub redirects release downloads to its storage hosts; never follow a redirect elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str, accept: str) -> Request:
    return Request(url, headers={"User-Agent": f"DARA-Desktop/{current_version()}", "Accept": accept})


def _download(url: str, timeout: float):
    return build_opener(_CheckedRedirects).open(_request(check_download_url(url), "application/octet-stream"), timeout=timeout)


def _fetch_text(url) -> str:
    with _download(url, TIMEOUT_S) as response:
        return response.read(4096).decode("ascii", errors="replace")


def _read_feed() -> dict:
    source = os.environ.get(FEED_ENV, "").strip() or FEED_URL
    if not source.lower().startswith(("http://", "https://")):
        return json.loads(Path(source).read_text(encoding="utf-8"))
    with build_opener().open(_request(source, "application/vnd.github+json"), timeout=TIMEOUT_S) as response:
        data = response.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("The release feed is too large")
    return json.loads(data)


def _empty(error=None) -> dict:
    return {"latest": None, "available": False, "notes": "", "published_at": None, "size": None, "url": None,
            "sha256": None, "error": error}


def parse_release(data: dict, current: str, fetch_text=_fetch_text) -> dict:
    """The newest release (GitHub ``releases/latest`` JSON) and whether it can be installed."""
    latest = str(data.get("tag_name") or "").strip().lstrip("vV")
    result = _empty()
    result.update(latest=latest or None, notes=str(data.get("body") or "")[:MAX_NOTES],
                  published_at=data.get("published_at"))
    if parse_version(latest) is None:
        result["error"] = "The newest release has no version number"
        return result
    if not is_newer(latest, current):
        return result
    name = f"DARA-Setup-{latest}.exe"
    assets = {asset.get("name"): asset for asset in data.get("assets") or [] if isinstance(asset, dict)}
    asset = assets.get(name)
    if asset is None:
        result["error"] = f"The release has no {name}"
        return result
    size, url = asset.get("size"), asset.get("browser_download_url")
    result.update(size=size, url=url)
    digest = str(asset.get("digest") or "")
    sha256 = digest[len("sha256:"):] if digest.lower().startswith("sha256:") else ""
    if not sha256 and f"{name}.sha256" in assets:
        try:  # "<hex>" or "<hex>  DARA-Setup-x.y.z.exe"
            sha256 = (fetch_text(assets[f"{name}.sha256"].get("browser_download_url")).split() or [""])[0]
        except Exception as error:
            logger.info("Could not read %s.sha256: %s", name, error)
    sha256 = sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not isinstance(size, int) or size <= 0 or not url:
        result["error"] = UNVERIFIABLE
        return result
    result.update(sha256=sha256, available=True)
    return result


def _set(**fields):
    with _lock:
        _job.update(fields)


def check(refresh: bool = False) -> dict:
    """The cached release check (6 h); network and parse failures only set ``error``."""
    with _lock:
        cached, at = _check["result"], _check["at"]
        if cached is not None and not refresh and time.monotonic() - at < CACHE_S:
            return cached
        checking = _job["state"] in ("idle", "error")
        if checking:
            _job.update(state="checking", error=None)
    try:
        result = parse_release(_read_feed(), current_version())
    except HTTPError as error:
        # 404: the repository has no published release yet.
        result = _empty(None if error.code == 404 else f"Could not check for updates (HTTP {error.code})")
    except Exception as error:
        logger.info("Update check failed: %s", error)
        result = _empty("Could not check for updates. Check the internet connection.")
    with _lock:
        _check.update(at=time.monotonic(), result=result)
        if checking and _job["state"] == "checking":
            _job["state"] = "idle"
    return result


def status(refresh: bool = False) -> dict:
    result = check(refresh)
    with _lock:
        job = dict(_job)
    size = result["size"]
    return {
        "current": current_version(),
        "packaged": is_packaged(),
        "latest": result["latest"],
        "available": result["available"],
        "notes": result["notes"],
        "published_at": result["published_at"],
        "size_mb": round(size / 2**20, 1) if result["available"] else None,
        "state": job["state"],
        "progress": job["progress"],
        "error": job["error"] or result["error"],
    }


def _start_installer(installer: Path, sha256: str) -> None:
    """Ask DARA.exe to run the installer; it checks the hash again, starts it and closes."""
    import ctypes

    request = installer.parent / "install-request.txt"
    request.write_text(f"{installer}\n{sha256}\n", encoding="utf-8")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterWindowMessageW.restype = ctypes.c_uint
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    message = user32.RegisterWindowMessageW(UPDATE_MESSAGE)
    if not message or not user32.PostMessageW(int(os.environ[WINDOW_ENV]), message, 0, 0):
        request.unlink(missing_ok=True)
        raise ValueError(f"DARA could not start the installer. Close DARA and run {installer}.")


def _install(release: dict) -> None:
    folder = Path(os.environ[DIR_ENV])
    target = folder / f"DARA-Setup-{release['latest']}.exe"
    partial = target.with_name(target.name + ".part")
    size = release["size"]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in [*folder.glob("DARA-Setup-*"), folder / "install-request.txt"]:
            old.unlink(missing_ok=True)
        digest, done = hashlib.sha256(), 0
        with _download(release["url"], DOWNLOAD_TIMEOUT_S) as response, partial.open("wb") as out:
            while chunk := response.read(2**20):
                done += len(chunk)
                if done > size:
                    break
                digest.update(chunk)
                out.write(chunk)
                _set(progress=min(99, done * 100 // size))
        _set(state="verifying", progress=100)
        if done != size or digest.hexdigest() != release["sha256"]:
            raise ValueError("The downloaded update is damaged (its size or checksum does not match the release). Try again.")
        os.replace(partial, target)
        _set(state="installing")
        _start_installer(target, release["sha256"])
    except Exception as error:
        logger.warning("Update failed: %s", error)
        partial.unlink(missing_ok=True)
        message = str(error) if isinstance(error, ValueError) else (
            "The update could not be downloaded. Check the internet connection and try again.")
        _set(state="error", progress=None, error=message)


@router.get("/update")
def get_update(refresh: bool = False):
    """Installed and newest version, and the state of an update being installed."""
    return status(refresh)


@router.post("/update/install")
async def install_update(force: bool = False):
    """Download, verify and install the newest release (DARA closes and reopens)."""
    if not is_packaged():
        raise HTTPException(409, "Only the installed DARA desktop app can update itself. Install the new version with its installer.")
    # Read on the event-loop thread, like the idle shutdown (montydb opens fail from other threads).
    if not force and presence._jobs_active():
        # "force": the page offers "Update anyway" (POST again with force=1).
        raise HTTPException(409, {"message": "Analyses are running or waiting in the queue. Updating now stops them; "
                                             "they are marked as failed and can be run again later.", "force": True})
    release = await run_in_threadpool(check)
    if not release["available"]:
        raise HTTPException(409, release["error"] or f"DARA {current_version()} is up to date.")
    with _lock:
        if _job["state"] in BUSY:
            raise HTTPException(409, "An update is already being installed.")
        _job.update(state="downloading", progress=0, error=None)
    threading.Thread(target=_install, args=(release,), name="dara-update", daemon=True).start()
    return await run_in_threadpool(status)
