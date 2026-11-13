import os
import re
import sqlite3
import threading
import zoneinfo
from configparser import ConfigParser
from contextlib import closing
from datetime import datetime

import bson
from bson.raw_bson import RawBSONDocument
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

# Fast read path of DaraMontyStore. montydb decodes every whole document of a collection
# on each query (a task record carries its pattern, a result several MB), so a lookup of
# one task cost a full scan. The fast path reads the same sqlite rows, decodes only the
# fields a query needs (or only the rows an ``index``/``uuid`` equality can match) and then
# runs montydb's own filter, sort, skip/limit and projection on them.
_KEYED_FIELDS = ("index", "uuid")
_KEY_INDEX: dict = {}  # (collection file, field) -> {row key: (rowid, field value)}
_KEY_INDEX_LOCK = threading.Lock()
_MISSING = object()  # the record has no such field
_ANY = object()  # a value equality cannot rule out (array, document, bool, ...)


class _Unsupported(Exception):
    """The fast path cannot reproduce this query exactly; montydb runs it."""


def _is_scalar(value) -> bool:
    return isinstance(value, (int, float, str)) and not isinstance(value, bool)


def _criteria_paths(spec):
    """Field paths a filter reads (logical operators followed, other top-level ``$`` refused)."""
    for key, value in spec.items():
        if key in ("$and", "$or", "$nor"):
            if not isinstance(value, list) or not all(isinstance(clause, dict) for clause in value):
                raise _Unsupported(key)
            for clause in value:
                yield from _criteria_paths(clause)
        elif key.startswith("$"):
            raise _Unsupported(key)
        else:
            yield key


def _path_tree(paths) -> dict:
    """Nested dict of field paths; ``None`` marks a subtree that is copied whole."""
    tree: dict = {}
    for path in sorted(paths, key=lambda item: item.count(".")):
        node, parts = tree, path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if node is None:  # an ancestor is already copied whole
                break
        else:
            node[parts[-1]] = None
    return tree


def _plain(value, codec_options):
    if isinstance(value, RawBSONDocument):
        return bson.decode(value.raw, codec_options=codec_options)
    if isinstance(value, list):
        return [_plain(item, codec_options) for item in value]
    return value


def _pick(raw, tree: dict, codec_options) -> dict:
    """The ``tree`` paths of a raw document, decoded, in document order.

    A path stops at the first value that is not a sub-document and copies it whole, so
    montydb's array traversal sees exactly what it would see in the full document.
    """
    picked = {}
    for key in raw:
        if key in tree:
            value, sub = raw[key], tree[key]
            nested = sub is not None and isinstance(value, RawBSONDocument)
            picked[key] = _pick(value, sub, codec_options) if nested else _plain(value, codec_options)
    return picked


_BSON_FIXED_SIZE = {0x01: 8, 0x06: 0, 0x07: 12, 0x08: 1, 0x09: 8, 0x0A: 0, 0x10: 4, 0x11: 8,
                    0x12: 8, 0x13: 16, 0x7F: 0, 0xFF: 0}
_BSON_SIZED = {0x02: 4, 0x03: 0, 0x04: 0, 0x05: 5, 0x0D: 4, 0x0E: 4, 0x0F: 0}  # int32 + this = size
_BSON_SCALAR = {0x01, 0x02, 0x10, 0x12}  # double, string, int32, int64: what _is_scalar accepts


def _stored_key(conn, rowid: int, field: str):
    """``field`` of a stored document as the key index keeps it (value, ``_ANY`` or ``_MISSING``).

    Reads only the top-level element headers, seeking past the values, so a record that
    carries a pattern or a whole result costs a few small reads instead of a full decode.
    """
    wanted = field.encode()
    try:
        blob = conn.blobopen("documents", "v", rowid, readonly=True)
    except sqlite3.Error:
        return None  # deleted since the row list was read
    with blob:
        size, pos = len(blob), 4
        while pos < size - 1:
            blob.seek(pos)
            head = blob.read(len(wanted) + 2)
            if head[0] == 0:
                break
            kind, name_end = head[0], head.find(b"\0", 1)
            name = head[1:name_end]
            if name_end < 0:  # a name longer than the wanted one: only its end is needed
                blob.seek(pos + 1)
                name, name_end = None, blob.read(4096).find(b"\0") + 1
                if name_end <= 0:
                    raise _Unsupported("key name")
            start = pos + name_end + 1
            if kind in _BSON_FIXED_SIZE:
                length = _BSON_FIXED_SIZE[kind]
            elif kind in _BSON_SIZED:
                blob.seek(start)
                length = int.from_bytes(blob.read(4), "little", signed=True) + _BSON_SIZED[kind]
            else:
                raise _Unsupported("BSON element type")
            if length < 0 or start + length > size:
                raise _Unsupported("BSON element size")
            if name == wanted:  # stored from a dict, so a key never repeats
                if kind not in _BSON_SCALAR:
                    return _ANY
                blob.seek(start)
                element = bytes([kind]) + b"k\0" + blob.read(length)
                return bson.decode((len(element) + 5).to_bytes(4, "little") + element + b"\0")["k"]
            pos = start + length
    return _MISSING


def _matching_keys(conn, path: str, field: str, value) -> list:
    """Row keys whose ``field`` may equal ``value``, in storage (rowid) order.

    shortcut: a row's ``index``/``uuid`` is read once per (key, rowid); maggma replaces
    records matched on those fields, so they never change in place. Re-read every row
    instead if a writer ever edits them.
    """
    with _KEY_INDEX_LOCK:
        known = _KEY_INDEX.get((path, field), {})
        current = {}
        for rowid, key in conn.execute("SELECT rowid, k FROM documents").fetchall():
            seen = known.get(key)
            if seen is None or seen[0] != rowid:
                try:
                    stored = _stored_key(conn, rowid, field)
                    if stored is None:  # deleted since the row list was read
                        continue
                except _Unsupported:  # unusual BSON: decode the whole document
                    row = conn.execute("SELECT v FROM documents WHERE k = ?", (key,)).fetchone()
                    if row is None:
                        continue
                    stored = RawBSONDocument(row[0]).get(field, _MISSING)
                seen = (rowid, stored if stored is _MISSING or _is_scalar(stored) else _ANY)
            current[key] = seen
        _KEY_INDEX[(path, field)] = current
    ordered = sorted(current.items(), key=lambda item: item[1][0])
    return [key for key, (_, stored) in ordered
            if stored is _ANY or (stored is not _MISSING and stored == value)]


def _run_monty_query(docs, criteria, projection, sort_list, skip, limit) -> list:
    """montydb's filter, sort, skip/limit and projection (``MontyCursor.__query``)."""
    from montydb.base import _index_document
    from montydb.engine.project import Projector
    from montydb.engine.queries import QueryFilter, ordering

    queryfilter = QueryFilter(criteria)
    projector = Projector(projection, queryfilter) if projection else None
    walkers = [queryfilter.fieldwalker for doc in docs if queryfilter(doc)]
    if sort_list:
        walkers = ordering(walkers, _index_document(sort_list))
    if skip and not limit:
        walkers = walkers[skip:]
    elif limit:
        walkers = walkers[skip: skip + abs(limit)]
    if projector:
        for walker in walkers:
            projector(walker)
    return [walker.doc for walker in walkers]


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

    def _fast_query(self, criteria, properties, sort, skip, limit) -> list:
        """The documents montydb would return, read without decoding whole collections."""
        criteria = criteria or {}
        projection = {p: 1 for p in properties} if isinstance(properties, list) else properties
        if (self.storage != "sqlite" or not self.storage_kwargs.get("use_bson")
                or self.database_path == ":memory:" or self.default_sort is not None
                or not isinstance(criteria, dict) or skip < 0):
            raise _Unsupported("store")
        if projection is not None and (not projection or not all(
                value is True or (type(value) is int and value == 1) for value in projection.values())):
            raise _Unsupported("projection")  # exclusions and operators need the whole document
        if sort and not all(type(value) is int and value in (1, -1) for value in sort.values()):
            raise _Unsupported("sort")
        key_field = next((field for field in _KEYED_FIELDS if _is_scalar(criteria.get(field))), None)
        if key_field is None and projection is None:
            raise _Unsupported("whole documents of a whole collection")
        tree = None if projection is None else _path_tree(
            {"_id", *projection, *_criteria_paths(criteria), *(sort or {})})

        coll = self._collection
        path = os.path.join(coll.database.client._storage._repository,
                            self.database_name, self.collection_name + ".collection")
        if not os.path.isfile(path):
            raise _Unsupported("no collection file")
        options = coll.codec_options
        raw_options = options.with_options(document_class=RawBSONDocument)

        def decode(value):
            if tree is None:
                return bson.decode(value, codec_options=options)
            return _pick(RawBSONDocument(value, raw_options), tree, options)

        with closing(sqlite3.connect(path, timeout=5)) as conn:
            if key_field is None:
                docs = [decode(value) for (value,) in conn.execute("SELECT v FROM documents")]
            else:
                docs = []
                for key in _matching_keys(conn, path, key_field, criteria[key_field]):
                    row = conn.execute("SELECT v FROM documents WHERE k = ?", (key,)).fetchone()
                    if row is not None:  # deleted since the key index was read
                        docs.append(decode(row[0]))
        sort_list = list(sort.items()) if sort else None
        return _run_monty_query(docs, criteria, projection, sort_list, skip, limit)

    def query(self, criteria=None, properties=None, sort=None, hint=None, skip=0, limit=0, **kwargs):
        if not hint and not kwargs:
            try:
                yield from self._fast_query(criteria, properties, sort, skip, limit)
                return
            except (_Unsupported, sqlite3.Error):
                pass
        yield from super().query(criteria, properties, sort, hint, skip, limit, **kwargs)

    def count(self, criteria=None, hint=None):
        if not hint:
            try:
                return len(self._fast_query(criteria, ["_id"], None, 0, 0))
            except (_Unsupported, sqlite3.Error):
                pass
        return super().count(criteria, hint)


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


def warm_key_indexes() -> None:
    """Build the per-process ``index``/``uuid`` key indexes before the first task page needs them."""
    from montydb.configure import provide_repository

    for name in ("jobs", "results", "versions"):
        store = get_store(name)
        if not (isinstance(store, DaraMontyStore) and store.storage == "sqlite"
                and store.storage_kwargs.get("use_bson") and store.database_path != ":memory:"):
            return
        # The same path string _fast_query builds, so its lookups find these indexes.
        path = os.path.join(provide_repository(store.database_path), store.database_name,
                            name + ".collection")
        if os.path.isfile(path):
            with closing(sqlite3.connect(path, timeout=5)) as conn:
                for field in _KEYED_FIELDS:
                    _matching_keys(conn, path, field, None)


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
