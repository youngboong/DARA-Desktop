"""Structure refinement through quick refine (design 2.6, WP7): request, refusals, .str, versions."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from dara.refinement_controls import RefinementControls
from dara.result import parse_atom_positions
from dara.server import quick_refine, versions, versions_router
from dara.server.quick_refine import QuickBackground, QuickRefineRequest, QuickStructure
from test_quick_refine import _fake_refinement, _run, _submit, _wait, env  # noqa: F401
from test_selected_refinement import references  # noqa: F401

GOOD_CONTEXT = {
    "weight_percent": 60.0, "narrow": False, "base_rwp": 3.0, "base_gof": 1.5,
    "spacegroup_number": 161, "database_spacegroup_number": 161, "n_reflections": 80,
    "refinement_status": {"level": "ok", "codes": []},
}


def _structure(stem, **fields):
    return QuickRefineRequest(origin="structure", structure={"phase": stem, **fields})


def _good_context(monkeypatch, **changes):
    monkeypatch.setattr(quick_refine, "structure_context", lambda *args: {**GOOD_CONTEXT, **changes})


def _refusal_codes(request):
    with pytest.raises(HTTPException) as exc:
        _run(_submit(request))
    assert exc.value.status_code == 422
    return [item["code"] for item in exc.value.detail["refusals"]]


# --------------------------------------------------------------------------- request
@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "structure"},  # no structure block
        {"origin": "manual", "structure": {"phase": "X", "thermal": "overall"}},
        {"origin": "structure", "structure": {"phase": "X"}},  # nothing to refine
        {"origin": "structure", "structure": {"phase": "X", "thermal": "overall", "reset": True}},
        {"origin": "structure", "structure": {"phase": "X", "thermal": "bad"}},
        {"origin": "structure", "remove": ["X"], "structure": {"phase": "X", "thermal": "overall"}},
        {"origin": "structure", "structure": {"phase": "X", "occupancy": [{"kind": "exchange", "sites": [0, 1]}]}},
        {"origin": "structure", "structure": {"phase": "X", "occupancy": [{"kind": "vacancy", "site": 0, "element": "li"}]}},
        {"origin": "structure", "structure": {"phase": "X", "positions": [-1]}},
    ],
)
def test_invalid_structure_requests(kwargs):
    with pytest.raises(ValidationError):
        QuickRefineRequest(**kwargs)


def test_spec_is_canonical_and_label_follows_the_design():
    request = _structure(
        "YFeO3_62_(cod_2101386)-None", positions=[3, 0, 2, 0], thermal="overall",
        occupancy=[{"kind": "exchange", "sites": [0, 1], "elements": ["Y", "Fe"]}],
    )
    assert request.structure.spec() == {
        "phase": "YFeO3_62_(cod_2101386)-None", "positions": [0, 2, 3], "thermal": "overall",
        "occupancy": [{"kind": "exchange", "sites": [0, 1], "elements": ["Y", "Fe"]}],
    }
    assert quick_refine.default_label(request) == "Structure: YFeO3 (3 positions, overall B, Y/Fe exchange)"
    reset = _structure("YFeO3_62_(cod_2101386)-None", reset=True)
    assert quick_refine.default_label(reset) == "Structure model reset: YFeO3"
    custom = QuickRefineRequest(origin="structure", label=" mine ", structure={"phase": "X", "thermal": "overall"})
    assert quick_refine.default_label(custom) == "mine"


# --------------------------------------------------------------------------- refusals
def test_refusals_come_back_as_422_with_codes(monkeypatch, env):
    stem = env["references"][0].filename
    # the synthetic fit has no listing section (R1) and a single reflection (R4)
    assert _refusal_codes(_structure(stem, thermal="overall")) == ["lattice_not_stable", "too_many_parameters"]
    _good_context(monkeypatch, weight_percent=4.0, database_spacegroup_number=167, base_rwp=20.0)
    assert _refusal_codes(_structure(stem, thermal="overall")) == [
        "minor_phase", "subgroup_setting", "poor_base_fit",
    ]
    _good_context(monkeypatch)
    assert _refusal_codes(_structure(stem, positions=[99])) == ["bad_site"]


def test_unknown_phase_is_422_and_nothing_is_queued(monkeypatch, env):
    _good_context(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(_submit(_structure("NotThere", thermal="overall")))
    assert exc.value.status_code == 422 and "not part of the base fit" in exc.value.detail
    assert not quick_refine._JOBS


def test_sites_endpoint_lists_sites_and_refusals(env):
    stem = env["references"][0].filename
    reply = _run(versions_router.structure_sites(7, phase=stem, sel="best"))
    assert reply["phase"] == stem and reply["eligible"] is False
    assert [item["code"] for item in reply["refusals"]] == ["lattice_not_stable"]
    assert reply["max_parameters"] == 0 and reply["n_reflections"] == 1
    assert {"Bi", "Fe", "O"} <= set(reply["elements"])
    first = reply["sites"][0]
    assert {"index", "label", "wyckoff", "multiplicity", "species", "xyz", "free"} <= set(first)
    with pytest.raises(HTTPException) as exc:
        _run(versions_router.structure_sites(7, phase="NotThere", sel="best"))
    assert exc.value.status_code == 404


def test_unfinished_task_message_depends_on_the_state(env):
    env["workers"].docs[0]["status"] = "FIZZLED"
    with pytest.raises(HTTPException) as exc:
        quick_refine.completed_task(7)
    assert exc.value.status_code == 409 and "failed" in exc.value.detail
    env["workers"].docs[0]["status"] = "RUNNING"
    with pytest.raises(HTTPException) as exc:
        quick_refine.completed_task(7)
    assert exc.value.detail == "Wait for the task to complete first."


# --------------------------------------------------------------------------- run
def _str_texts(captured):
    return {path.stem: path.read_text() for path in captured["phases"]}


def test_structure_run_rewrites_the_str_and_stores_the_model(monkeypatch, env):
    _good_context(monkeypatch)
    stem = env["references"][0].filename
    captured = []

    def fake(**kwargs):
        captured.append({**kwargs, "texts": {p.stem: p.read_text() for p in kwargs["phases"]}})
        return _fake_refinement()(**kwargs)

    monkeypatch.setattr(quick_refine, "do_refinement", fake)

    async def main():
        first = await _submit(_structure(stem, positions=[0], thermal="overall"))
        job = await _wait(first["job_id"])
        assert job["status"] == "done", job
        return job

    _run(main())
    texts = captured[0]["texts"]
    assert "PARAM=Ball=0.010000_0.001^0.04" in texts[stem]
    assert "TDS=Ball" in texts[stem] and "TDS=0.010000" not in texts[stem]
    assert "PARAM=z=0.221200_0.201200^0.241200" in texts[stem]  # site 0 (Fe) frees z only
    assert "Ball" not in texts[env["references"][1].filename]  # other phases keep the plain .str
    assert captured[0]["refinement_params"]["itmax"] == quick_refine.QUICK_REFINE_ITMAX

    record = env["versions"].query({"uuid": env["record"]["uuid"]})[0]
    assert record["refine_mode"] == "full"
    assert record["label"] == "Structure: BiFeO3 (1 position, overall B)"
    assert record["origin"]["kind"] == "structure"
    model = record["effective_phase_params"][stem]["structure_model"]
    assert model["thermal"] == "overall" and model["positions"] == [0]
    assert "structure_model" not in record["effective_phase_params"][env["references"][1].filename]
    assert versions.structure_models(record) == {stem: model}


def test_spec_changes_the_cache_keys_and_a_repeat_reuses_the_version(monkeypatch, env):
    _good_context(monkeypatch)
    stem = env["references"][0].filename
    runs = []
    fake = _fake_refinement(runs)
    monkeypatch.setattr(quick_refine, "do_refinement", fake)

    async def run(request):
        job = await _wait((await _submit(request))["job_id"])
        assert job["status"] == "done", job
        return job

    async def main():
        plain = await run(QuickRefineRequest(controls=RefinementControls()))
        overall = await run(_structure(stem, thermal="overall"))
        per_element = await run(_structure(stem, thermal="per_element"))
        again = await run(_structure(stem, thermal="overall"))
        return plain, overall, per_element, again

    plain, overall, per_element, again = _run(main())
    keys = [r["fit_key"] for r in env["versions"].query({"uuid": env["record"]["uuid"]})]
    assert len(keys) == len(set(keys)) == 3
    assert again["reused"] is True and again["version"] == overall["version"]
    paths = {run_["phases"][0].parent for run_ in runs}
    assert len(paths) == 3  # three different .str cache folders


def test_later_versions_inherit_the_model_until_it_is_reset(monkeypatch, env):
    _good_context(monkeypatch)
    stem = env["references"][0].filename
    seen = []

    def fake(**kwargs):
        seen.append({p.stem: p.read_text() for p in kwargs["phases"]})
        return _fake_refinement()(**kwargs)

    monkeypatch.setattr(quick_refine, "do_refinement", fake)

    async def run(request):
        job = await _wait((await _submit(request))["job_id"])
        assert job["status"] == "done", job
        return job["version"]

    async def main():
        structured = await run(_structure(stem, thermal="overall"))
        inherited = await run(QuickRefineRequest(base=f"version:{structured}", background=QuickBackground(degree=4)))
        reset = await run(
            QuickRefineRequest(base=f"version:{inherited}", origin="structure", structure={"phase": stem, "reset": True})
        )
        return structured, inherited, reset

    structured, inherited, reset = _run(main())
    assert "TDS=Ball" in seen[0][stem] and "TDS=Ball" in seen[1][stem]
    assert "Ball" not in seen[2][stem]
    stored = {r["version"]: r for r in env["versions"].query({"uuid": env["record"]["uuid"]})}
    assert stored[inherited]["effective_phase_params"][stem]["structure_model"]["thermal"] == "overall"
    assert "structure_model" not in stored[reset]["effective_phase_params"][stem]
    assert stored[reset]["label"] == "Structure model reset: BiFeO3"


def test_requests_without_a_model_keep_their_cache_keys(env, tmp_path):
    stem, cif = env["references"][0].filename, str(env["references"][0])
    params = {"gewicht": "SPHAR4", "lattice_range": 0.03}
    path = quick_refine._cached_str(tmp_path, stem, cif, params)
    key = quick_refine._sha1(cif, json.dumps(params, sort_keys=True, default=str))[:16]
    assert path == tmp_path / "str" / key / f"{stem}.str"
    modelled = quick_refine._cached_str(
        tmp_path, stem, cif, {**params, "structure_model": {"thermal": "overall", "positions": [], "occupancy": []}}
    )
    assert modelled.parent != path.parent and "Ball" in modelled.read_text()
    assert "Ball" not in path.read_text()


# --------------------------------------------------------------------------- summary
BLOCK = """  4     0.5685  0.2500  0.5179     E=(Y(0.9495),FE(0.0505))
x=0.56570+-0.00071
z=0.5094+-0.0021

  4     0.0000  0.0000  0.5000     E=(FE(0.9495),Y(0.0505))
"""


def test_version_summary_gets_the_structure_table():
    spec = {"positions": [0], "thermal": "fixed", "occupancy": [{"kind": "exchange", "sites": [0, 1], "elements": ["Y", "Fe"]}]}
    phase = SimpleNamespace(atom_positions=parse_atom_positions(BLOCK), model_extra={"occ0": (0.0505, 0.014)})
    refinement = SimpleNamespace(lst_data=SimpleNamespace(phases_results={"P": phase}))
    record = {"effective_phase_params": {"P": {"lattice_range": "fixed", "structure_model": spec}, "Q": {}}}
    assert versions.structure_models(record) == {"P": spec}
    assert versions.structure_models(None) == {} and versions.structure_models({}) == {}
    summary = {"phase_details": {"P": {"a": 1}, "Q": {}}}
    versions._attach_structure_results(summary, refinement, versions.structure_models(record))
    table = summary["phase_details"]["P"]["structure_refinement"]
    assert table["indicative"] is True and table["spec"] == spec
    assert table["sites"][0]["xyz"][0]["esd"] == pytest.approx(0.00071)
    assert table["parameters"][0] == {"name": "occ0", "value": 0.0505, "esd": 0.014, "state": "refined", "label": "Y/Fe exchange (Y1-Fe1)"}
    assert "structure_refinement" not in summary["phase_details"]["Q"]
    broken = SimpleNamespace(lst_data=SimpleNamespace(phases_results={"P": SimpleNamespace(atom_positions=None)}))
    versions._attach_structure_results(summary, broken, {"P": spec})  # must not raise
