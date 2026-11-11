"""Internal-standard API: validation, submission, stored spec, result block, PUT (design 2.4)."""

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from dara.server import api_router
from dara.server.api_router import (
    INTERNAL_STANDARD_ERROR,
    _internal_standard_spec,
    internal_standard_block,
    parse_internal_standard,
)


def test_parse_accepts_json_dict_and_empty():
    assert parse_internal_standard("") is None
    assert parse_internal_standard(None) is None
    assert parse_internal_standard("null") is None
    assert parse_internal_standard('{"formula": "Si", "wt_percent": 10}') == {
        "formula": "Si", "wt_percent": 10.0, "phase": None,
    }
    assert parse_internal_standard({"formula": "Al2O3", "wt_percent": 20, "phase": "x"})["phase"] == "x"


@pytest.mark.parametrize(
    "value",
    [
        '{"formula": "Si", "wt_percent": 0}',
        '{"formula": "Si", "wt_percent": 100}',
        '{"formula": "Si", "wt_percent": -3}',
        '{"formula": "Si", "wt_percent": "ten"}',
        '{"formula": "Si", "wt_percent": NaN}',
        '{"formula": "Xx9", "wt_percent": 10}',
        '{"formula": "", "wt_percent": 10}',
        '{"wt_percent": 10}',
        "not json",
    ],
)
def test_parse_rejects_with_the_one_message(value):
    with pytest.raises(HTTPException) as caught:
        parse_internal_standard(value)
    assert caught.value.status_code == 400 and caught.value.detail == INTERNAL_STANDARD_ERROR


def _final(standard_weight=12.5):
    return {
        "phase_details": {
            "Si_227_(cod_9008565)-0": {"weight_percent": standard_weight, "weight_esd_percent": 0.3},
            "ZnO_186_(cod_1)-0": {
                "weight_percent": 87.5, "weight_esd_percent": 0.3,
                "refinement_status": {"level": "ok"},
            },
        },
        "confidence": {"phases": {"ZnO_186_(cod_1)-0": {"weight_flag": None}}},
    }


def test_block_follows_the_stored_spec():
    record = {"job": {"metadata": {"submission_settings": {
        "internal_standard": {"formula": "Si", "wt_percent": 10.0, "phase": None}}}}}
    block = internal_standard_block(record, _final())
    assert block["status"] == "ok" and block["scale"] == 0.8
    assert block["amorphous"]["mixture_wt_percent"] == 20.0
    assert [item["name"] for item in block["phases"]] == ["ZnO_186_(cod_1)-0"]
    # the top-level field written by PUT wins; None means "removed"
    record["internal_standard"] = None
    assert internal_standard_block(record, _final()) is None
    record["internal_standard"] = {"formula": "ZnO", "wt_percent": 50.0}
    assert internal_standard_block(record, _final())["standard_phase"] == "ZnO_186_(cod_1)-0"
    assert internal_standard_block({}, _final()) is None
    assert internal_standard_block(record, None) is None
    assert _internal_standard_spec({"internal_standard": {"formula": "", "wt_percent": 5}}) is None


class _Store:
    def __init__(self, record):
        self.record = record

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        return self.record if criteria.get("index") == self.record["index"] else None

    def update(self, document):
        self.record = document


def test_put_sets_and_removes_the_standard(monkeypatch):
    record = {"index": 4, "uuid": "u", "status": "COMPLETED"}
    store = _Store(record)
    monkeypatch.setattr(api_router, "get_worker_store", lambda: store)

    async def fake_result(task_id):
        return {"internal_standard": internal_standard_block(store.record, _final())}

    monkeypatch.setattr(api_router, "result", fake_result)
    answer = asyncio.run(
        api_router.set_internal_standard(4, {"formula": "Si", "wt_percent": 10, "phase": None})
    )
    assert store.record["internal_standard"]["formula"] == "Si"
    assert answer["internal_standard"]["scale"] == 0.8
    answer = asyncio.run(api_router.set_internal_standard(4, None))
    assert store.record["internal_standard"] is None and answer["internal_standard"] is None
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.set_internal_standard(4, {"formula": "Si", "wt_percent": 101}))
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.set_internal_standard(9, None))
    assert caught.value.status_code == 404
    store.record["status"] = "FIZZLED"
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.set_internal_standard(4, None))
    assert caught.value.status_code == 409 and "failed" in caught.value.detail


def _submit(monkeypatch, **extra):
    queued = []
    monkeypatch.setattr(api_router, "add_job_to_queue", lambda job, user: queued.append(job) or 77)
    pattern_path = Path(__file__).parent / "test_data" / "BiFeO3.xy"
    upload = UploadFile(filename=pattern_path.name, file=BytesIO(pattern_path.read_bytes()))
    return queued, asyncio.run(
        api_router.submit(
            pattern_file=upload, user="test", precursor_formulas='["BiFeO3"]',
            elements='["Bi", "Fe", "O"]', analysis_mode="elemental",
            instrument_profile="Aeris-fds-Pixcel1d-Medipix3", wavelength="Cu", temperature=-1,
            atmosphere="air", use_cod=True, use_mp=False, mp_e_hull_cutoff=0.05,
            max_mp_structures=100, use_rxn_predictor=None, additional_phases=None, **extra,
        )
    )


def test_submit_adds_the_standard_elements_and_stores_the_spec(monkeypatch):
    queued, answer = _submit(
        monkeypatch, internal_standard='{"formula": "CeO2", "wt_percent": 10}'
    )
    assert answer["wf_id"] == 77 and answer["warnings"] == []
    job = queued[0]
    assert job.metadata["submission_settings"]["internal_standard"] == {
        "formula": "CeO2", "wt_percent": 10.0, "phase": None,
    }
    assert set(job.function_kwargs["elements"]) == {"Bi", "Fe", "O", "Ce"}
    # without a standard nothing is added
    queued, _ = _submit(monkeypatch)
    assert "internal_standard" not in queued[0].metadata["submission_settings"]
    assert set(queued[0].function_kwargs["elements"]) == {"Bi", "Fe", "O"}


def test_submit_rejects_a_bad_standard(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _submit(monkeypatch, internal_standard='{"formula": "Si", "wt_percent": 150}')
    assert caught.value.status_code == 400 and caught.value.detail == INTERNAL_STANDARD_ERROR


def test_precursor_typo_gets_suggestions(monkeypatch):
    from dara import structure_db

    info = {"H-N-O-P": [("PH6NO4", 1, 19, 0.0)], "Na-O": [("Na2O", 2, 225, 0.0)]}
    monkeypatch.setattr(structure_db, "_COD_INSTANCE", SimpleNamespace(preparsed_info=info))
    api_router._cod_formulas.cache_clear()
    try:
        found = api_router._precursor_warnings('["NH3H2PO4", "Na2O"]', True)
        assert [item["precursor"] for item in found] == ["NH3H2PO4"]
        assert found[0]["suggestions"] == ["PH6NO4"] and "Did you mean" in found[0]["message"]
        assert api_router._precursor_warnings('["Na2O"]', True) == []
        assert api_router._precursor_warnings('["NH3H2PO4"]', False) == []
    finally:
        api_router._cod_formulas.cache_clear()
