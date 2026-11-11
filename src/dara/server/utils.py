import os
import re
import zoneinfo
from configparser import ConfigParser
from datetime import datetime

from jobflow import JobStore
from maggma.core import Store
from maggma.stores import MongoStore, MontyStore

from dara.server.setting import get_dara_server_settings


def convert_to_local_tz(dt):
    """Converts a UTC datetime object to the system's local timezone."""
    local_tz = datetime.now().astimezone().tzinfo  # gets system local tz
    return dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(local_tz)


def _storage_configured(repository: str, storage: str, storage_kwargs: dict) -> bool:
    """True when the montydb repository already holds exactly this storage configuration."""
    try:
        with open(os.path.join(repository, ".monty.storage"), encoding="utf-8") as handle:
            if handle.readline().strip() != storage:
                return False
        parser = ConfigParser()
        if not parser.read(os.path.join(repository, "monty.storage.cfg"), encoding="utf-8"):
            return False
    except OSError:
        return False
    if not parser.has_section(storage):
        return False
    for option, value in storage_kwargs.items():
        expected = ("yes" if value else "") if isinstance(value, bool) else str(value)
        if parser.get(storage, option, fallback=None) != expected:
            return False
    return True


# maggma wraps MontyStore in a ``requires`` function; subclass the class underneath.
_MontyStoreClass = getattr(MontyStore, "__wrapped__", MontyStore)


class DaraMontyStore(_MontyStoreClass):
    """MontyStore that leaves an existing, identical storage configuration alone.

    maggma rewrites ``monty.storage.cfg`` on every connect. The server, the worker
    supervisor and the job process all connect concurrently, and a reader that opens the
    file mid-rewrite gets an empty storage name ("ValueError: Empty module name").
    """

    def connect(self, force_reset: bool = False):
        if not self._coll or force_reset:
            from montydb import MontyClient, set_storage

            if self.database_path != ":memory:" and not _storage_configured(
                self.database_path, self.storage, self.storage_kwargs
            ):
                set_storage(self.database_path, storage=self.storage, **self.storage_kwargs)
            client = MontyClient(self.database_path, **self.client_kwargs)
            self._coll = client[self.database_name][self.collection_name]


def get_store(collection_name):
    """Get a MontyStore instance for job management."""
    setting = get_dara_server_settings()
    if setting.database_backend == "monty":
        store = DaraMontyStore(
            database_name="dara",
            collection_name=collection_name,
            database_path=setting.montydb_path,
            storage="sqlite",
            key=["uuid", "index"],
        )
    elif setting.database_backend == "mongodb":
        store = MongoStore(
            database=setting.mongodb_database,
            collection_name=collection_name,
            host=setting.mongodb_host,
            port=setting.mongodb_port,
            username=setting.mongodb_username,
            password=setting.mongodb_password,
        )
    else:
        raise ValueError(f"Unsupported database backend: {setting.database_backend}")

    return store


def get_worker_store() -> MontyStore:
    """Get a MontyStore instance for job management."""
    return get_store("jobs")


def get_result_store() -> MontyStore:
    """Get a MontyStore instance for job management."""
    return get_store("results")


def get_versions_store():
    """Store of re-refinement versions (``uuid`` = task uuid, ``index`` = version number)."""
    return get_store("versions")


def get_job_store(docs_store: Store) -> JobStore:
    return JobStore(docs_store=docs_store)


def not_completed_detail(state) -> str:
    """409 message for a task that has no results to serve, by task state."""
    if state == "FIZZLED":
        return "This task failed; there are no results to export. Rerun it from the task page."
    if state == "CANCELLED":
        return "This task was cancelled; there are no results to export."
    return "Wait for the task to complete first."


def safe_download_stem(value) -> str:
    """Filesystem-safe stem of a file or phase name.

    Directories and a trailing pattern or ``.cif`` extension are removed (other dots stay);
    the ``-None`` suffix of a phase without an energy above hull is dropped.
    """
    from dara.cif_names import strip_none
    from dara.xrd import SUPPORTED_PATTERN_EXTENSIONS

    stem = strip_none(os.path.basename(str(value).replace("\\", "/")))
    suffix = os.path.splitext(stem)[1]
    if suffix.lower() in {*SUPPORTED_PATTERN_EXTENSIONS, ".cif"}:
        stem = stem[: -len(suffix)]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "dara_result"


def download_name(task_id, pattern_name, kind: str, selection=None, ext: str = "zip") -> str:
    """``dara_task_{id}_{pattern_stem}_{kind}[_version_{n}|_candidate_{n}].{ext}``.

    ``selection``: ``None`` / ``"best"``, ``"version:<n>"`` (or ``("version", n)``) or a
    candidate source index (shown 1-based).
    """
    suffix = ""
    if isinstance(selection, tuple) and len(selection) == 2:
        selection = f"{selection[0]}:{selection[1]}"
    if isinstance(selection, str) and selection.startswith("version:"):
        suffix = "_version_" + selection.split(":", 1)[1]
    elif isinstance(selection, int) and not isinstance(selection, bool):
        suffix = f"_candidate_{selection + 1}"
    return f"dara_task_{task_id}_{safe_download_stem(pattern_name)}_{kind}{suffix}.{ext}"
