"""Peak-list endpoints, state-specific 409 wording and download names (design 2.3, 2.1 J/K)."""

import asyncio
import csv
import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dara.export.peak_list import COLUMNS
from dara.server import api_router, quick_refine, review_router
from dara.server.utils import download_name, not_completed_detail
from test_peak_list import PHASE, _refinement


@pytest.fixture
def loaded(monkeypatch):
    refinement = _refinement([29.35, 50.0], [100.0, 40.0])
    refinement.lst_data = SimpleNamespace(phases_results={PHASE: SimpleNamespace()})
    document = SimpleNamespace(
        final_result=refinement, results=[], input_cifs=[], search_kwargs={"wavelength": "Cu"}
    )
    task = {"index": 5, "uuid": "u", "status": "COMPLETED", "job": {"name": "a b.xrdml"}}

    async def load(task_id):
        return task, document

    monkeypatch.setattr(review_router, "_load_completed_task", load)
    return task


def test_peaks_json_lists_rows_in_column_order(loaded):
    body = asyncio.run(api_router.task_peaks(5, None))
    assert body["columns"] == list(COLUMNS)
    assert body["wavelength"] == "Cu" and body["selection"] == "best"
    assert [row["two_theta_deg"] for row in body["rows"]] == [29.35, 50.0]
    assert body["rows"][0]["fwhm_deg"] == pytest.approx(0.30, abs=0.002)


def test_peaks_csv_has_bom_header_and_download_name(loaded):
    response = asyncio.run(api_router.task_peaks_csv(5, None))
    assert response.body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert tuple(rows[0]) == COLUMNS and len(rows) == 3
    assert 'filename="dara_task_5_a_b_peaks.csv"' in response.headers["content-disposition"]


def test_peaks_selection_is_validated(loaded):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.task_peaks(5, "nonsense"))
    assert caught.value.status_code == 422
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.task_peaks(5, "7"))
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    ("state", "word"), [("FIZZLED", "failed"), ("CANCELLED", "cancelled"), ("RUNNING", "Wait")]
)
def test_not_completed_wording_follows_the_state(monkeypatch, state, word):
    assert word in not_completed_detail(state)

    class Store:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            return {"index": 27, "status": state}

    async def load(task_id):
        raise HTTPException(409, "Wait for the task to complete first.")

    monkeypatch.setattr(quick_refine, "load_completed", load)
    monkeypatch.setattr(review_router, "get_worker_store", lambda: Store())
    with pytest.raises(HTTPException) as caught:
        asyncio.run(review_router._load_completed_task(27))
    assert caught.value.status_code == 409 and word in caught.value.detail


def test_download_names():
    assert download_name(34, "NH4H2PO4-Na2CO3_200C.xrdml", "all_results") == (
        "dara_task_34_NH4H2PO4-Na2CO3_200C_all_results.zip"
    )
    assert download_name(34, "a b.xy", "report", "version:3", "html") == (
        "dara_task_34_a_b_report_version_3.html"
    )
    assert download_name(9, "s.xy", "topas", 0) == "dara_task_9_s_topas_candidate_1.zip"
    assert download_name(9, "s.xy", "table", "best", "csv") == "dara_task_9_s_table.csv"
    assert download_name(9, "Na12.67P4O64.67_165_(cod_9007527)-None", "peaks", ext="csv") == (
        "dara_task_9_Na12.67P4O64.67_165__cod_9007527_peaks.csv"
    )
