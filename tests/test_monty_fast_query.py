"""DaraMontyStore's fast read path returns exactly what montydb returns."""

from datetime import datetime, timedelta

import pytest

from dara.server.utils import DaraMontyStore, _MontyStoreClass


def _store(path, name="jobs"):
    return DaraMontyStore(database_name="dara", collection_name=name, database_path=str(path),
                          storage="sqlite", key=["uuid", "index"])


def _docs():
    start = datetime(2026, 11, 1, 12, 0, 0)
    docs = []
    for i in range(1, 9):
        docs.append({
            "uuid": f"u{i % 3}" if i > 5 else f"u{i}",  # u0/u1/u2 repeat (several rows per uuid)
            "index": i,
            "status": ["COMPLETED", "FIZZLED", "RUNNING"][i % 3],
            "submitted_time": start + timedelta(minutes=(i * 7) % 5),  # ties on purpose
            "summary": {"final_rwp": i / 3, "phases": [{"name": f"P{i}", "w": [i, i + 1]}]} if i % 2 else None,
            "job": {"name": f"Sample_{i}.xy", "function_kwargs": {"pattern": list(range(50))},
                    "metadata": {"submission_settings": {"sample": "Fe2O3" if i % 4 == 0 else None}}},
            "tags": [{"k": "a", "v": i}, {"k": "b"}],
            **({"user": "kim"} if i % 2 else {}),
            **({"review": {"selection": "best", "note": "x"}} if i == 3 else {}),
        })
    return docs


QUERIES = [
    ({"index": 4}, None, None, 0, 0),
    ({"index": 4}, ["uuid", "status"], None, 0, 1),
    ({"index": 99}, None, None, 0, 0),
    ({"uuid": "u1"}, None, None, 0, 0),
    ({"uuid": "u1"}, ["index", "summary.final_rwp"], None, 0, 0),
    ({"uuid": "u2", "index": 8}, ["index"], None, 0, 0),
    ({"index": "4"}, None, None, 0, 0),
    ({}, ["index", "status", "summary", "job.name", "job.metadata", "user", "review.selection"],
     {"submitted_time": -1}, 2, 3),
    ({"status": {"$in": ["COMPLETED", "RUNNING"]}}, ["index"], {"submitted_time": -1, "index": 1}, 0, 0),
    ({"$or": [{"job.name": {"$regex": "sample_1", "$options": "i"}},
              {"job.metadata.submission_settings.sample": {"$regex": "fe2", "$options": "i"}}]},
     ["index", "job.name"], {"submitted_time": -1}, 0, 25),
    ({"user": None}, ["index", "user"], None, 0, 0),
    ({"tags.k": "b", "tags.v": 3}, ["tags.v", "index"], None, 0, 0),
    ({"summary.phases.w": 6}, ["summary.phases.name"], {"index": -1}, 1, 0),
    ({"job": {"$exists": True}}, ["job.function_kwargs.pattern", "job"], None, 0, 2),
]


@pytest.mark.parametrize(("criteria", "properties", "sort", "skip", "limit"), QUERIES)
def test_fast_query_matches_montydb(tmp_path, criteria, properties, sort, skip, limit):
    store = _store(tmp_path)
    with store:
        store.update(_docs())
        fast = store._fast_query(criteria, properties, sort, skip, limit)  # raises if unsupported
        assert list(store.query(criteria, properties, sort, skip=skip, limit=limit)) == fast
        slow = list(_MontyStoreClass.query(store, criteria, properties, sort, skip=skip, limit=limit))
        assert fast == slow
        assert [list(doc) for doc in fast] == [list(doc) for doc in slow]  # same key order
        assert store.count(criteria) == _MontyStoreClass.count(store, criteria)


def test_key_index_follows_inserts_updates_and_deletes(tmp_path):
    store = _store(tmp_path, "results")
    with store:
        store.update(_docs())
        assert store.query_one({"index": 4})["status"] == "FIZZLED"  # builds the key index
        store.update({**store.query_one({"index": 4}), "status": "COMPLETED"})
        store.remove_docs({"index": 2})
        store.update({"uuid": "new", "index": 2, "status": "READY"})
        store.update({"uuid": "u1", "index": 42, "status": "READY"})
        for criteria in ({"index": 4}, {"index": 2}, {"uuid": "u1"}, {"uuid": "new"}):
            assert list(store.query(criteria)) == list(_MontyStoreClass.query(store, criteria))
        assert store.query_one({"index": 4})["status"] == "COMPLETED"


def test_stored_key_reads_one_top_level_field(tmp_path):
    import sqlite3

    import bson
    from bson.int64 import Int64

    from dara.server.utils import _ANY, _MISSING, _stored_key

    conn = sqlite3.connect(tmp_path / "c.sqlite")
    conn.execute("CREATE TABLE documents (k text NOT NULL, v text NOT NULL, PRIMARY KEY(k))")
    doc = {"_id": bson.ObjectId(), "a_much_longer_name_than_index": "x" * 5000,
           "job": {"pattern": list(range(1000))}, "flag": True, "none": None,
           "when": datetime(2026, 1, 1), "big": Int64(2**40), "rwp": 1.5, "uuid": "u-1", "index": 7}
    conn.execute("INSERT INTO documents VALUES (?, ?)", ("k1", bson.encode(doc)))
    rowid = conn.execute("SELECT rowid FROM documents").fetchone()[0]
    for field in ("uuid", "index", "big", "rwp"):
        assert _stored_key(conn, rowid, field) == doc[field]
    for field in ("job", "flag", "none", "when", "_id"):
        assert _stored_key(conn, rowid, field) is _ANY
    assert _stored_key(conn, rowid, "missing") is _MISSING
    assert _stored_key(conn, rowid, "ind") is _MISSING
    assert _stored_key(conn, rowid + 1, "index") is None  # deleted row
