import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from maggma.stores import MontyStore

from dara.server import api_router
from dara.server.task_summary import (
    SUMMARY_REFRESH_GAVE_UP,
    SUMMARY_SCHEMA,
    compact_summary,
    runtime_seconds,
)


def _store(path):
    return MontyStore(
        database_name="dara", collection_name="jobs", database_path=str(path),
        storage="sqlite", key=["uuid", "index"],
    )


@pytest.fixture
def populated(tmp_path, monkeypatch):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = [
        {"uuid": "u1", "index": 1, "status": "COMPLETED", "user": "amy", "submitted_time": base,
         "job": {"name": "BiFeO3_800C.xrdml"},
         "summary": {"schema": 1, "best_rwp": 6.3, "gof": 1.8, "final_phases": ["A"],
                     "final_phase_labels": ["Anatase"], "n_candidates": 2}},
        {"uuid": "u2", "index": 2, "status": "FIZZLED", "user": "bob",
         "submitted_time": base.replace(day=2), "job": {"name": "other (rutile).xy"},
         "error": "Traceback...\nRuntimeError: Error in BGMN refinement. The exit code is -1."},
        {"uuid": "u3", "index": 3, "status": "COMPLETED", "user": "amy",
         "submitted_time": base.replace(day=3),
         "job": {"name": "bifeo3_second.xy", "metadata": {"submission_settings": {"sample": "NCM811 batch 3"}}},
         "review": {"selection": 2, "notes": ""}},
    ]
    with _store(tmp_path) as store:
        for record in records:
            store.update(record)
    # A fresh store object per call, as in production.
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _store(tmp_path))
    # Never touch the real result store: refreshing a stale summary fails unless a test says otherwise.
    def no_result(uuid, runtime_s, task=None):
        raise LookupError("no stored result")

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", no_result)
    SUMMARY_REFRESH_GAVE_UP.clear()
    return tmp_path


def _list(**kwargs):
    kwargs.setdefault("page", 1)
    kwargs.setdefault("limit", 10)
    kwargs.setdefault("user", None)
    kwargs.setdefault("name", None)
    kwargs.setdefault("state", None)
    return asyncio.run(api_router.get_all_tasks(**kwargs))


def test_no_filter_returns_all_with_new_fields(populated):
    payload = _list()
    assert payload["total_tasks"] == 3
    by_id = {task["fw_id"]: task for task in payload["tasks"]}
    assert by_id[1]["summary"]["best_rwp"] == 6.3 and by_id[1]["adopted"] is None
    assert by_id[1]["error_summary"] is None
    assert by_id[2]["summary"] is None
    assert by_id[2]["error_summary"]["code"] == "bgmn_exit"
    assert "-1" in by_id[2]["error_summary"]["title"]
    assert by_id[3]["adopted"] == 2 and by_id[3]["error_summary"] is None
    assert {"fw_id", "name", "state", "created_on", "user"} <= set(by_id[1])


def test_series_and_list_share_the_summary_schema_constant():
    from dara.server import series_router

    assert not hasattr(series_router, "SUMMARY_SCHEMA_REQUIRED")
    assert SUMMARY_SCHEMA >= 3


def test_list_refreshes_stale_summaries_and_persists_them(populated, monkeypatch):
    calls = []

    def fresh(uuid, runtime_s, task=None):
        calls.append(uuid)
        return {"schema": SUMMARY_SCHEMA, "best_rwp": 1.5, "gof": 1.1, "phases": [],
                "confidence_overall": "check"}

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", fresh)
    by_id = {task["fw_id"]: task for task in _list()["tasks"]}
    assert sorted(calls) == ["u1", "u3"]  # completed, stale or missing; the fizzled one is skipped
    assert by_id[1]["summary"]["best_rwp"] == 1.5 and by_id[3]["summary"]["schema"] == SUMMARY_SCHEMA
    assert by_id[2]["summary"] is None
    with _store(populated) as store:
        assert store.query_one({"index": 1})["summary"]["confidence_overall"] == "check"
    _list()
    assert sorted(calls) == ["u1", "u3"]  # current summaries are not recomputed


def test_list_refresh_is_bounded_per_request(populated, monkeypatch):
    calls = []

    def fresh(uuid, runtime_s, task=None):
        calls.append(uuid)
        return {"schema": SUMMARY_SCHEMA, "best_rwp": 1.5}

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", fresh)
    monkeypatch.setattr(api_router, "LIST_SUMMARY_REFRESH_MAX", 1)
    first = {task["fw_id"]: task for task in _list()["tasks"]}
    assert len(calls) == 1
    assert sum(1 for t in first.values() if (t["summary"] or {}).get("schema") == SUMMARY_SCHEMA) == 1
    _list()
    assert len(calls) == 2  # the next request continues with the remaining stale task


def test_list_refresh_failure_keeps_the_old_summary_and_is_not_retried(populated, monkeypatch):
    calls = []

    def boom(uuid, runtime_s, task=None):
        calls.append(uuid)
        raise LookupError("gone")

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", boom)
    by_id = {task["fw_id"]: task for task in _list()["tasks"]}
    assert by_id[1]["summary"]["best_rwp"] == 6.3 and by_id[3]["summary"] is None
    n = len(calls)
    _list()
    assert len(calls) == n


def test_name_filter_is_case_insensitive_substring(populated):
    payload = _list(name="BIFEO")
    assert sorted(task["fw_id"] for task in payload["tasks"]) == [1, 3]
    assert payload["total_tasks"] == 2
    assert _list(name="  bifeo3_second ")["total_tasks"] == 1
    assert _list(name="")["total_tasks"] == 3
    assert _list(name="nomatch")["tasks"] == []


def test_name_filter_treats_regex_metacharacters_literally(populated):
    payload = _list(name="(rutile)")
    assert [task["fw_id"] for task in payload["tasks"]] == [2]
    assert _list(name=".*")["total_tasks"] == 0
    assert _list(name="bifeo", user="amy")["total_tasks"] == 2
    assert _list(name="bifeo", user="bob")["total_tasks"] == 0


def _document(final_phases=("TiO2_141_(cod_7206075)-0",)):
    phases = {name: SimpleNamespace() for name in final_phases}
    return SimpleNamespace(
        best_rwp=6.26, results=[1, 2, 3],
        final_result=SimpleNamespace(lst_data=SimpleNamespace(rwp=6.31, rexp=3.0, phases_results=phases)),
    )


def test_compact_summary_shape():
    summary = compact_summary(_document(), 312.4)
    assert summary["schema"] == SUMMARY_SCHEMA
    assert summary["best_rwp"] == 6.26 and summary["final_rwp"] == 6.31
    assert summary["gof"] == pytest.approx(6.31 / 3.0)
    assert summary["final_phases"] == ["TiO2_141_(cod_7206075)-0"]
    assert summary["final_phase_labels"][0].startswith("Anatase")
    assert summary["n_candidates"] == 3 and summary["runtime_s"] == 312.4
    empty = compact_summary(SimpleNamespace(best_rwp=None, results=None, final_result=None), None)
    assert empty["gof"] is None and empty["final_phases"] == [] and empty["n_candidates"] == 0


def test_runtime_seconds_tolerates_naive_and_aware_datetimes():
    naive = datetime(2026, 1, 1, 0, 0)
    aware = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    assert runtime_seconds(naive, aware) == 300
    assert runtime_seconds(None, aware) is None


def test_state_filter_is_applied_before_paging(populated):
    payload = _list(state="fizzled", limit=1)
    assert payload["total_tasks"] == 1
    assert [task["fw_id"] for task in payload["tasks"]] == [2]
    assert _list(state="COMPLETED,FIZZLED")["total_tasks"] == 3

def test_sample_name_is_listed_and_searchable(populated):
    payload = _list(name="ncm811")
    assert [task["fw_id"] for task in payload["tasks"]] == [3]
    assert payload["tasks"][0]["sample"] == "NCM811 batch 3"
    assert {task["fw_id"] for task in _list(name="BiFeO3")["tasks"]} == {1, 3}  # file names still match
    assert {task["fw_id"]: task["sample"] for task in _list()["tasks"]}[1] is None


def test_adopted_candidate_is_the_listed_fit_and_an_older_digest_is_stale():
    from dara.server.task_summary import summary_is_current

    class Doc(SimpleNamespace):
        def model_copy(self, update):
            return Doc(**{**vars(self), **update})

    def fit(rwp, name):
        return SimpleNamespace(lst_data=SimpleNamespace(rwp=rwp, rexp=2.0, phases_results={name: SimpleNamespace()}))

    automatic, adopted = fit(3.01, "Y2O3_206_(cod_1)-0"), fit(3.12, "Y2O3_199_(cod_2)-0")
    document = Doc(best_rwp=3.01, results=[([], automatic), ([], adopted)], final_result=automatic)
    summary = compact_summary(document, 1.0, {"uuid": "u", "review": {"selection": 1}})
    assert summary["adopted"] == 1 and summary["final_rwp"] == 3.12
    assert summary["final_phases"] == ["Y2O3_199_(cod_2)-0"] and summary["n_candidates"] == 2
    record = {"review": {"selection": 1}}
    assert summary_is_current(summary, record)
    assert not summary_is_current({**summary, "adopted": None}, record)  # stored before this rule
    assert summary_is_current({**summary, "adopted": None}, {"review": {"selection": "best"}})
    assert not summary_is_current({**summary, "schema": SUMMARY_SCHEMA - 1}, {})


def test_list_filters_and_sorts_across_pages(tmp_path, monkeypatch):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)

    def summary(rwp, gof, level):
        return {"schema": SUMMARY_SCHEMA, "final_rwp": rwp, "gof": gof, "confidence_overall": level}

    records = [
        {"uuid": "a", "index": 1, "status": "COMPLETED", "user": "Verify", "submitted_time": base,
         "job": {"name": "b.xy", "metadata": {"submission_settings": {"analysis_mode": "elemental"}}},
         "summary": summary(5.0, 1.5, "check")},
        {"uuid": "b", "index": 2, "status": "COMPLETED", "user": "verify-2", "submitted_time": base.replace(day=2),
         "job": {"name": "A.xy", "metadata": {"submission_settings": {
             "analysis_mode": "known_mixture", "source_task_id": 1}}},
         "summary": summary(3.0, 2.5, "high")},
        {"uuid": "c", "index": 3, "status": "COMPLETED", "user": "amy", "submitted_time": base.replace(day=3),
         "job": {"name": "c.xy", "metadata": {"submission_settings": {"analysis_mode": "identification"}}},
         "summary": summary(None, None, None)},
        {"uuid": "d", "index": 4, "status": "READY", "user": "amy", "submitted_time": base.replace(day=4),
         "job": {"name": "d.xy"}},
        {"uuid": "e", "index": 5, "status": "READY", "user": "amy", "submitted_time": base.replace(day=5),
         "job": {"name": "e.xy"}},
    ]
    with _store(tmp_path) as store:
        for record in records:
            store.update(record)
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _store(tmp_path))
    from dara.server import worker

    monkeypatch.setattr(worker, "get_worker_store", lambda: _store(tmp_path))
    assert _list(user="verify")["total_tasks"] == 2  # case-insensitive substring
    assert _list(user="VERIFY-")["total_tasks"] == 1
    assert [t["fw_id"] for t in _list(sort="rwp")["tasks"]][:2] == [2, 1]  # no Rwp sorts last
    assert [t["fw_id"] for t in _list(sort="rwp", order="desc")["tasks"]][:2] == [1, 2]
    assert [t["fw_id"] for t in _list(sort="name")["tasks"]] == [2, 1, 3, 4, 5]
    assert [t["fw_id"] for t in _list(sort="verdict", limit=1, page=2)["tasks"]] == [1]  # high, check, ...
    assert [t["fw_id"] for t in _list(verdict="high,check")["tasks"]] == [2, 1]
    assert _list(verdict="no-fit")["total_tasks"] == 3
    assert [t["fw_id"] for t in _list(analysis_mode="identification")["tasks"]] == [3]
    rows = {t["fw_id"]: t for t in _list()["tasks"]}
    assert rows[2]["source_task_id"] == 1 and rows[1]["source_task_id"] is None
    assert (rows[4]["queue_position"], rows[5]["queue_position"]) == (1, 2) and rows[1]["queue_position"] is None
    assert asyncio.run(api_router.result(5))["queue_position"] == 2  # the task page gives the same number
    for bad in ({"sort": "colour"}, {"order": "up"}, {"verdict": "great"}):
        with pytest.raises(Exception) as caught:
            _list(**bad)
        assert getattr(caught.value, "status_code", None) == 400


def test_list_labels_write_hydroxides_as_on_the_task_page():
    from dara.server.task_summary import _label

    assert _label("La(HO)3_176_(mp_mp-625394)-0").startswith("La(OH)3")


def test_users_are_distinct_with_counts_and_case_variants_merged(populated):
    with _store(populated) as store:
        store.update({"uuid": "u4", "index": 4, "status": "READY", "user": "Amy", "job": {"name": "x.xy"}})
        store.update({"uuid": "u5", "index": 5, "status": "READY", "user": None, "job": {"name": "y.xy"}})
    users = asyncio.run(api_router.task_users())["users"]
    # "amy" (2 tasks) and "Amy" (1) are one entry under the spelling used most; no entry without a user.
    assert users == [{"user": "amy", "count": 3}, {"user": "bob", "count": 1}]
