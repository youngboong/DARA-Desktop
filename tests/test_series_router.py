import asyncio
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from maggma.stores import MontyStore

from dara.server import series_router
from dara.server.task_summary import SUMMARY_SCHEMA


class ASGIClient:
    """Exercise the real HTTP schema without the optional httpx dependency."""

    def __init__(self, app):
        self.app = app

    def request(self, method, url, payload=None):
        async def run():
            parts = urlsplit(url)
            body = json.dumps(payload).encode() if payload is not None else b""
            messages = []

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                messages.append(message)

            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": parts.path, "root_path": "",
                "query_string": parts.query.encode(), "server": ("test", 80),
                "client": ("test", 123), "headers": [(b"content-type", b"application/json")],
            }, receive, send)
            start = next(item for item in messages if item["type"] == "http.response.start")
            raw = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            headers = {key.decode(): value.decode() for key, value in start.get("headers", [])}
            return SimpleNamespace(status_code=start["status"], json=lambda: json.loads(raw), content=raw,
                                   headers=headers)

        return asyncio.run(run())

    def get(self, url):
        return self.request("GET", url)

    def post(self, url, json=None):
        return self.request("POST", url, json)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(series_router.router)
    return ASGIClient(app)


def test_preview_endpoint(client):
    response = client.post("/api/series/preview", {"filenames": ["ZnO_500C.xy", "ZnO_300C.xy", "ZnO_700C.xy"]})
    assert response.status_code == 200
    body = response.json()
    assert body["unit"] == "°C"
    by_name = {item["filename"]: item for item in body["items"]}
    assert [by_name[n]["order"] for n in ("ZnO_300C.xy", "ZnO_500C.xy", "ZnO_700C.xy")] == [0, 1, 2]
    assert by_name["ZnO_300C.xy"]["variable"]["value"] == 300
    plain = client.post("/api/series/preview", {"filenames": ["b.xy", "a.xy"]}).json()
    assert plain["unit"] is None and [item["order"] for item in plain["items"]] == [1, 0]


# --- submission ------------------------------------------------------------------------


class FakeQueue:
    def __init__(self):
        self.built = []
        self.queued = []

    def build(self, pattern, name, **fields):
        self.built.append((name, pattern, fields))
        return SimpleNamespace(name=name, metadata={})

    def queue(self, job, user):
        self.queued.append((job, user))
        return 100 + len(self.queued)


@pytest.fixture
def fake(monkeypatch):
    queue = FakeQueue()
    monkeypatch.setattr(series_router, "_build_job", queue.build)
    monkeypatch.setattr(series_router, "_queue", queue.queue)
    monkeypatch.setattr(series_router, "_wavelength_mismatch", lambda pattern, path, wavelength: None)

    def load(path):
        if "broken" in path.name:
            raise ValueError("not a pattern")
        return SimpleNamespace(name=path.name)

    monkeypatch.setattr(series_router, "load_pattern", load)
    return queue


def _uploads(*names):
    return [UploadFile(file=io.BytesIO(b"20 1\n30 2\n"), filename=name) for name in names]


def _submit(names, **kwargs):
    kwargs.setdefault("user", "amy")
    kwargs.setdefault("series_name", "Anneal")
    kwargs.setdefault("pattern_files", _uploads(*names))
    return asyncio.run(series_router.submit_series(**{**_defaults(), **kwargs}))


def _defaults():
    return {
        "precursor_formulas": "[]", "elements": '["Zn", "O"]', "analysis_mode": "elemental",
        "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "wavelength": "Cu", "temperature": -1,
        "atmosphere": "air", "use_cod": True, "use_mp": False, "mp_e_hull_cutoff": 0.05,
        "max_mp_structures": 100, "additional_phases": None, "library_cif_ids": "[]",
        "two_theta_min": None, "two_theta_max": None, "excluded_ranges": "[]",
        "refinement_controls": "{}", "confirm_wavelength": False,
    }


def test_submit_three_files_queues_in_variable_order(fake):
    names = ["ZnO_700C.xy", "ZnO_300C.xy", "ZnO_500C.xy"]
    body = _submit(names)
    assert body["message"] == "submitted" and body["unit"] == "°C"
    assert [fake.queued[i][0].name for i in range(3)] == ["ZnO_300C.xy", "ZnO_500C.xy", "ZnO_700C.xy"]
    assert body["task_ids"] == [101, 102, 103]
    assert [v["value"] for v in body["variables"]] == [300, 500, 700]
    series_ids = {job.metadata["series"]["id"] for job, _ in fake.queued}
    assert series_ids == {body["series_id"]} and len(body["series_id"]) == 12
    first = fake.queued[0][0].metadata["series"]
    assert first["name"] == "Anneal" and first["order"] == 0 and first["size"] == 3
    assert first["variable"]["raw"] == "300C"
    assert all(user == "amy" for _, user in fake.queued)
    # Settings reach every job unchanged, with their own copy of the extra CIF list.
    for _, _, fields in fake.built:
        assert fields["elements"] == '["Zn", "O"]' and fields["additional_cifs"] == []
        assert fields["library_cif_ids"] == "[]"


def test_per_file_chemistry_takes_each_file_name(fake):
    """A batch of different samples: each file runs with the chemistry of its own name."""
    _submit(["CoO-ZnO_1100C.xy", "Fe2O3-2TiO2_1000C.xy", "sample_7.xy"], per_file_chemistry=True)
    by_name = {name: fields for name, _, fields in fake.built}
    assert json.loads(by_name["CoO-ZnO_1100C.xy"]["elements"]) == ["Co", "O", "Zn"]
    assert json.loads(by_name["Fe2O3-2TiO2_1000C.xy"]["precursor_formulas"]) == ["Fe2O3", "TiO2"]
    assert by_name["sample_7.xy"]["elements"] == '["Zn", "O"]'  # no chemistry in the name: the form's


@pytest.mark.parametrize("mode", ["known_mixture", "reaction"])
def test_per_file_chemistry_never_replaces_entered_phases(fake, mode):
    """Known-phase and Reaction runs refine the entered phases: the flag is ignored."""
    _submit(["CoO-ZnO_1100C.xy", "Fe2O3-2TiO2_1000C.xy"], per_file_chemistry=True, analysis_mode=mode,
            precursor_formulas='["ZnO"]')
    assert {fields["precursor_formulas"] for _, _, fields in fake.built} == {'["ZnO"]'}


def test_unreadable_file_rejects_everything_and_queues_nothing(fake):
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xy", "broken_500C.xy", "c_700C.xy"])
    assert caught.value.status_code == 400
    assert "broken_500C.xy" in caught.value.detail and "Could not read pattern" in caught.value.detail
    assert caught.value.detail.count("broken_500C.xy") == 1  # the file name is not repeated
    assert caught.value.detail.startswith("broken_500C.xy: Could not read pattern")
    assert fake.queued == []


def test_job_build_error_names_the_file_and_queues_nothing(fake, monkeypatch):
    def build(pattern, name, **fields):
        if "500" in name:
            raise HTTPException(status_code=400, detail="Invalid element symbols")
        return SimpleNamespace(name=name, metadata={})

    monkeypatch.setattr(series_router, "_build_job", build)
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xy", "a_500C.xy"])
    assert caught.value.detail == "a_500C.xy: Invalid element symbols"
    assert fake.queued == []


def test_wavelength_mismatch_without_confirmation_names_the_file(fake, monkeypatch):
    mismatch = {"code": "wavelength_mismatch", "message": "differs"}
    monkeypatch.setattr(
        series_router, "_wavelength_mismatch",
        lambda pattern, path, wavelength: mismatch if "500" in path.name else None,
    )
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xrdml", "a_500C.xrdml"])
    assert caught.value.detail["code"] == "wavelength_mismatch"
    assert caught.value.detail["filename"] == "a_500C.xrdml"
    assert fake.queued == []
    body = _submit(["a_300C.xrdml", "a_500C.xrdml"], confirm_wavelength=True)
    assert body["message"] == "submitted" and len(fake.queued) == 2


def test_file_count_limits_name_and_duplicates(fake):
    with pytest.raises(HTTPException) as too_many:
        _submit([f"s_{i}C.xy" for i in range(series_router.MAX_SERIES_FILES + 1)])
    assert too_many.value.status_code == 400
    with pytest.raises(HTTPException) as too_few:
        _submit(["only.xy"])
    assert too_few.value.status_code == 400
    with pytest.raises(HTTPException) as duplicate:
        _submit(["a.xy", "a.xy"])
    assert "a.xy" in duplicate.value.detail
    with pytest.raises(HTTPException) as blank:
        _submit(["a.xy", "b.xy"], series_name="   ")
    assert blank.value.status_code == 400
    with pytest.raises(HTTPException) as long_name:
        _submit(["a.xy", "b.xy"], series_name="x" * 121)
    assert long_name.value.status_code == 400
    assert fake.queued == []


def test_exactly_fifty_files_are_accepted(fake):
    body = _submit([f"s_{i}C.xy" for i in range(series_router.MAX_SERIES_FILES)])
    assert len(body["task_ids"]) == 50


# --- listing and detail ----------------------------------------------------------------


def _store(path):
    return MontyStore(
        database_name="dara", collection_name="jobs", database_path=str(path),
        storage="sqlite", key=["uuid", "index"],
    )


def _series(series_id, name, order, size, value):
    return {"id": series_id, "name": name, "order": order, "size": size,
            "variable": None if value is None else {"raw": f"{value}C", "value": float(value), "unit": "°C"}}


@pytest.fixture
def populated(tmp_path, monkeypatch):
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    schema2 = {"schema": SUMMARY_SCHEMA, "best_rwp": 5.0, "gof": 1.5, "phases": [], "confidence_overall": "high"}
    records = [
        {"uuid": "u1", "index": 1, "status": "COMPLETED", "submitted_time": base,
         "start_time": base, "end_time": base.replace(minute=5),
         "job": {"name": "a_500C.xy", "metadata": {"series": _series("s1", "Anneal", 1, 3, 500)}},
         "summary": schema2},
        {"uuid": "u2", "index": 2, "status": "COMPLETED", "submitted_time": base,
         "start_time": base, "end_time": base.replace(minute=7),
         "job": {"name": "a_300C.xy", "metadata": {"series": _series("s1", "Anneal", 0, 3, 300)}},
         "summary": {"schema": 1, "best_rwp": 6.0, "gof": 1.9}},
        {"uuid": "u3", "index": 3, "status": "RUNNING", "submitted_time": base,
         "job": {"name": "a_700C.xy", "metadata": {"series": _series("s1", "Anneal", 2, 3, 700)}}},
        {"uuid": "u4", "index": 4, "status": "COMPLETED", "submitted_time": base.replace(day=9),
         "job": {"name": "b_1.xy", "metadata": {"series": _series("s2", "Later", 0, 2, None)}},
         "summary": schema2},
        {"uuid": "u5", "index": 5, "status": "READY", "submitted_time": base.replace(day=9),
         "job": {"name": "b_2.xy", "metadata": {"series": _series("s2", "Later", 1, 2, None)}}},
        {"uuid": "u6", "index": 6, "status": "COMPLETED", "submitted_time": base,
         "job": {"name": "single.xy", "metadata": {}}, "summary": schema2},
    ]
    with _store(tmp_path) as store:
        for record in records:
            store.update(record)
    monkeypatch.setattr(series_router, "get_worker_store", lambda: _store(tmp_path))
    series_router._summary_refresh_gave_up.clear()
    return tmp_path


def test_list_groups_series_newest_first(populated, client):
    body = client.get("/api/series").json()
    assert [item["series_id"] for item in body["series"]] == ["s2", "s1"]
    s1 = body["series"][1]
    assert s1["name"] == "Anneal" and s1["n_tasks"] == 3 and s1["n_completed"] == 2
    assert s1["unit"] == "°C" and s1["created_on"].startswith("2026-05-01")
    assert body["series"][0]["unit"] is None and body["series"][0]["n_completed"] == 1


def test_detail_sorted_by_order_and_stale_summary_refreshed(populated, client, monkeypatch):
    calls = []

    def fake_summarize(uuid, runtime_s):
        calls.append((uuid, runtime_s))
        return {"schema": SUMMARY_SCHEMA, "best_rwp": 4.0, "gof": 1.2, "phases": [], "confidence_overall": "check"}

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", fake_summarize)
    body = client.get("/api/series/s1").json()
    assert body["name"] == "Anneal" and body["unit"] == "°C"
    assert [item["task_id"] for item in body["items"]] == [2, 1, 3]
    assert [item["order"] for item in body["items"]] == [0, 1, 2]
    assert body["items"][0]["variable"]["value"] == 300
    # Only the completed task with a schema-1 summary was refreshed (300 s runtime from the record).
    assert calls == [("u2", 420.0)]
    assert body["items"][0]["summary"]["confidence_overall"] == "check"
    assert body["items"][1]["summary"]["schema"] == SUMMARY_SCHEMA
    assert body["items"][2]["state"] == "RUNNING" and body["items"][2]["summary"] is None
    # Persisted: a second call neither recomputes nor loses the new summary.
    again = client.get("/api/series/s1").json()
    assert calls == [("u2", 420.0)]
    assert again["items"][0]["summary"]["schema"] == SUMMARY_SCHEMA
    with _store(populated) as store:
        assert store.query_one({"index": 2})["summary"]["best_rwp"] == 4.0


def test_refresh_failure_keeps_the_old_summary(populated, client, monkeypatch):
    def boom(uuid, runtime_s):
        raise LookupError("no stored result")

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", boom)
    body = client.get("/api/series/s1").json()
    assert body["items"][0]["summary"]["schema"] == 1


def test_detail_unknown_series_is_404(populated, client):
    assert client.get("/api/series/nope").status_code == 404


def test_detail_items_carry_the_list_row_fields(populated, client, monkeypatch):
    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", lambda uuid, runtime_s: None)
    with _store(populated) as store:
        record = store.query_one({"index": 1})
        record["job"]["metadata"]["submission_settings"] = {"sample": "Anneal 500"}
        record.update({"user": "amy", "review": {"selection": "version:2"}})
        store.update(record)
    items = {item["task_id"]: item for item in client.get("/api/series/s1").json()["items"]}
    assert items[1]["sample"] == "Anneal 500" and items[1]["user"] == "amy" and items[1]["adopted"] == "version:2"
    assert items[1]["created_on"].startswith("2026-05-01") and items[1]["error_summary"] is None
    assert items[2]["sample"] is None and items[3]["progress"] is None


# --- stacked-pattern figure -----------------------------------------------------------------


def test_group_phases_merges_one_phase_across_tasks_and_keeps_polymorphs_apart():
    groups = series_router.group_phases([
        (2, ["ZnO_186_(cod_1)-0", "TiO2_136_(cod_5)-0", "Fe2O3_167_(cod_2)-0"],
         {"ZnO_186_(cod_1)-0": 0.6, "TiO2_136_(cod_5)-0": 0.395, "Fe2O3_167_(cod_2)-0": 0.005}),
        (1, ["ZnO_186_(cod_3)-0", "TiO2_141_(cod_4)-0"], {"ZnO_186_(cod_3)-0": 0.7, "TiO2_141_(cod_4)-0": 0.3}),
    ])
    assert [group["key"] for group in groups] == [
        "ZnO_186_(cod_1)-0", "TiO2_136_(cod_5)-0", "TiO2_141_(cod_4)-0", "Fe2O3_167_(cod_2)-0"]
    assert groups[0]["names"] == {2: "ZnO_186_(cod_1)-0", 1: "ZnO_186_(cod_3)-0"}  # another record, one phase
    assert groups[0]["max_weight"] == pytest.approx(70.0)
    assert not series_router._default_phase(groups[3]) and series_router._default_phase(groups[2])
    # identifications carry no amounts: every phase is in the default selection, in order of appearance
    found = series_router.group_phases([(7, ["B_2_(cod_2)", "A_1_(cod_1)"], {})])
    assert [group["key"] for group in found] == ["B_2_(cod_2)", "A_1_(cod_1)"]
    assert all(series_router._default_phase(group) for group in found)


@pytest.fixture
def figure_series(populated, monkeypatch):
    import numpy as np
    import pandas as pd

    from dara.server import review_router

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", lambda uuid, runtime_s: None)
    x = np.linspace(20, 60, 400)
    phases = {2: {"ZnO_186_(cod_1)-0": 0.9, "Fe2O3_167_(cod_2)-0": 0.1}, 1: {"ZnO_186_(cod_3)-0": 1.0}}

    def refinement(task_id):
        names = list(phases[task_id])
        return SimpleNamespace(
            plot_data=SimpleNamespace(x=x, y_obs=10 + task_id + np.exp(-0.5 * ((x - 34.4) / 0.2) ** 2), y_calc=x * 0),
            lst_data=SimpleNamespace(phases_results={name: object() for name in names}),
            peak_data=pd.DataFrame({"2theta": [34.4, 33.2], "intensity": [100.0, 50.0], "phase": names[:1] + names[-1:]}),
            get_phase_weights=lambda: phases[task_id],
        )

    async def load(task_id):
        return {"index": task_id, "review": {}, "job": {}}, SimpleNamespace(search_kwargs={"wavelength": "Cu"})

    monkeypatch.setattr(review_router, "_load_completed_task", load)
    monkeypatch.setattr(review_router, "_resolve", lambda document, selection, task: (refinement(task["index"]), {}))
    monkeypatch.setattr(review_router, "figure_references",
                        lambda document, fit, names=None: {"ZnO_186_(cod_1)-0": (np.array([34.0]), np.array([100.0]))})
    return populated


def test_figure_options_list_completed_tasks_in_order_and_the_phase_union(figure_series, client):
    body = client.get("/api/series/s1/figure-options").json()
    assert [task["task_id"] for task in body["tasks"]] == [2, 1]  # series order; the running task 3 is left out
    assert [task["label"] for task in body["tasks"]] == ["300C", "500C"]  # the part of the file names that differs
    assert [phase["key"] for phase in body["phases"]] == ["ZnO_186_(cod_1)-0", "Fe2O3_167_(cod_2)-0"]
    assert body["phases"][0]["tasks"] == [2, 1] and body["phases"][1]["max_weight_percent"] == 10.0
    assert "COD" not in body["phases"][0]["label"] and all(phase["default"] for phase in body["phases"])
    assert client.get("/api/series/nope/figure-options").status_code == 404


def test_series_figure_renders_the_selection(figure_series, client):
    svg = client.get("/api/series/s1/figure.svg")
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert 'inline; filename="dara_series_Anneal_patterns_170mm.svg"' in svg.headers["content-disposition"]
    text = svg.content.decode()
    assert "300C" in text and "500C" in text and "a_300C" not in text and "Zincite" in text and "Intensity (a.u.)" in text
    assert "COD" not in text  # the database record is never in the figure
    one = client.get("/api/series/s1/figure.svg?tasks=1&phases=&download=true")
    assert one.status_code == 200 and "attachment" in one.headers["content-disposition"]
    assert "a_300C" not in one.content.decode() and "Zincite" not in one.content.decode()
    named = client.get('/api/series/s1/figure.svg?labels={"1":"Annealed 500 °C"}')
    assert named.status_code == 200 and "Annealed 500" in named.content.decode() and "300C" in named.content.decode()
    assert client.get("/api/series/s1/figure.svg?labels=[1]").status_code == 422
    png = client.get("/api/series/s1/figure.png?width_mm=85&dpi=100&normalize=true")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert client.get("/api/series/s1/figure.pdf").content.startswith(b"%PDF")
    assert client.get("/api/series/s1/figure.gif").status_code == 404
    assert client.get("/api/series/s1/figure.svg?tasks=99").status_code == 422


def test_series_figure_styles(figure_series, client):
    base = client.get("/api/series/s1/figure.svg").content
    drawn = {query: client.get(f"/api/series/s1/figure.svg?{query}").content
             for query in ("peaks=symbols", "peaks=none", "names=strips", "bars=0", "names=strips&bars=0")}
    assert all(content.startswith(b"<?xml") or b"<svg" in content for content in drawn.values())
    assert drawn["peaks=symbols"] != base and drawn["names=strips"] != base and drawn["bars=0"] != base
    assert drawn["names=strips&bars=0"] == drawn["bars=0"]  # without strips the names stay in the legend
    for bad in ("peaks=dots", "names=below", "bars=maybe"):
        assert client.get(f"/api/series/s1/figure.svg?{bad}").status_code == 422


def test_short_labels_keep_only_the_part_that_differs():
    from dara.server.series_router import short_labels

    assert short_labels(["NiO_0p7028-La(OH)3_0p1010-ZnO_0p2000--8min", "NiO_0p7028-La(OH)3_0p1010-ZnO_0p2000--2min"]) == ["8min", "2min"]
    assert short_labels(["CoO-ZnO_900C_60min", "CoO-ZnO_1000C_60min", "CoO-ZnO_1100C_60min"]) == ["900C", "1000C", "1100C"]
    assert short_labels(["sample_a", "sample_a"]) == ["sample_a", "sample_a"]  # nothing differs: names kept whole
    assert short_labels(["only one"]) == ["only one"]
