"""Plot annotations of a task: stored on the task record, bounded and validated."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI

from dara.server import annotations_router, quick_refine
from test_review_router import ASGIClient


@pytest.fixture
def context(monkeypatch):
    task = {"index": 9, "uuid": "task-9", "status": "COMPLETED", "review": {"notes": "keep"}}

    class Store:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            return deepcopy(task) if criteria.get("index") == task.get("index") else None

        def update(self, value):
            task.clear()
            task.update(deepcopy(value))

    monkeypatch.setattr(annotations_router, "get_worker_store", Store)
    monkeypatch.setattr(quick_refine, "get_worker_store", Store)
    app = FastAPI()
    app.include_router(annotations_router.router)
    return ASGIClient(app), task


NOTE = {"id": "a1", "two_theta": 31.5, "intensity": 1200.0, "text": "  <b>ZnO</b> 100  "}


def test_annotations_round_trip_and_keep_the_task_record(context):
    client, task = context
    assert client.get("/api/task/9/annotations").json() == {"annotations": []}
    saved = client.put("/api/task/9/annotations", json={"annotations": [NOTE]})
    assert saved.status_code == 200
    # Stored as plain text (trimmed, not interpreted); the client escapes it for drawing.
    assert saved.json()["annotations"][0]["text"] == "<b>ZnO</b> 100"
    assert client.get("/api/task/9/annotations").json()["annotations"] == saved.json()["annotations"]
    assert task["review"] == {"notes": "keep"} and task["annotations_updated_at"]
    assert client.put("/api/task/9/annotations", json={"annotations": []}).json() == {"annotations": []}


@pytest.mark.parametrize("change", [
    {"text": ""}, {"text": "x" * 121}, {"text": "two\nlines"}, {"id": "bad id"}, {"id": "x" * 41},
    {"two_theta": 181}, {"two_theta": -1}, {"intensity": 1e13}, {"extra": 1},
])
def test_invalid_annotations_are_refused(context, change):
    client, task = context
    response = client.put("/api/task/9/annotations", json={"annotations": [{**NOTE, **change}]})
    assert response.status_code == 422
    assert "annotations" not in task


def test_list_bounds_and_task_state(context):
    client, task = context
    many = [{**NOTE, "id": f"a{i}"} for i in range(51)]
    assert client.put("/api/task/9/annotations", json={"annotations": many}).status_code == 422
    assert client.put("/api/task/9/annotations", json={"annotations": many[:50]}).status_code == 200
    assert client.put("/api/task/9/annotations", json={"annotations": [NOTE, NOTE]}).status_code == 422
    assert client.get("/api/task/999/annotations").status_code == 404
    task["status"] = "RUNNING"
    assert client.get("/api/task/9/annotations").status_code == 409
    assert client.put("/api/task/9/annotations", json={"annotations": []}).status_code == 409


def test_damaged_stored_entries_are_left_out(context):
    client, task = context
    task["annotations"] = [NOTE, {"id": "b", "text": "no position"}, "junk"]
    assert [item["id"] for item in client.get("/api/task/9/annotations").json()["annotations"]] == ["a1"]


def test_report_figure_writes_annotations_as_plain_text():
    from dara.server.report.figures import fit_figure

    phase = SimpleNamespace(model_extra={})
    refinement = SimpleNamespace(
        lst_data=SimpleNamespace(phases_results={"CaF2_225_(cod_2300449)-0": phase}),
        plot_data=SimpleNamespace(x=np.linspace(20, 40, 50), y_obs=np.ones(50), y_calc=np.ones(50), y_bkg=np.zeros(50)),
    )
    svg = fit_figure(refinement, fmt="svg", annotations=[{"two_theta": 30, "intensity": 1, "text": "cost $5 & <tag>"}]).decode()
    assert "cost $5 &amp; &lt;tag&gt;" in svg


def test_downloaded_figure_and_report_show_the_task_annotations(monkeypatch):
    """The figure download and the report figure draw the task's stored labels."""
    import test_review_router

    client, task, _ = test_review_router.context.__wrapped__(monkeypatch)
    task["annotations"] = [{"id": "a1", "two_theta": 21.0, "intensity": 50.0, "text": "peak <A>"}, "damaged"]
    svg = client.get("/api/task/9/figure.svg")
    assert svg.status_code == 200 and "peak &lt;A&gt;" in svg.text
    report = client.get("/api/task/9/report")
    assert report.status_code == 200 and "peak &lt;A&gt;" in report.text
