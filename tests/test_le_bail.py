"""Le Bail fit of a phase in a version refinement: request, .str, BGMN output, cautions, exports.

The fixture ``test_data/le_bail`` is BGMN 4.2.22 output of a two-phase fit (Zn1.7Co0.3O2 +
Co3O4) with ``LeBail=1`` in the Co3O4 ``.str`` (the ``.dia`` keeps every 10th point).
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from dara.cif import Cif
from dara.export.refined_cif import CifNotExportable, refined_cif_text
from dara.generate_control_file import generate_control_file
from dara.refinement_controls import RefinementControls
from dara.result import get_result, le_bail_phases
from dara.server import quick_refine, versions, versions_router
from dara.server.api_router import _mass_balance_block, _phase_refinement_details
from dara.server.fit_cautions import fit_cautions, le_bail_names
from dara.server.internal_standard import quantify
from dara.server.quick_refine import QuickRefineRequest
from dara.server.table_export import _rows_for_result
from test_quick_refine import _fake_refinement, _run, _submit, _wait, env  # noqa: F401
from test_selected_refinement import references  # noqa: F401

FIXTURE = Path(__file__).parent / "test_data" / "le_bail"
ZNO, CO3O4 = "Zn1.7Co0.3O2_186_(cod_1533020)", "Co3O4_227_(cod_9005896)"


@pytest.fixture(scope="module")
def fit():
    return get_result(FIXTURE / "xrd_data.sav")


# --------------------------------------------------------------------------- request
@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "le_bail"},  # nothing chosen
        {"origin": "le_bail", "le_bail": {"A": True}, "remove": ["B"]},
        {"origin": "le_bail", "le_bail": {"A": "yes"}},
        {"origin": "le_bail", "le_bail": {"A": True, "B": True}},  # one Le Bail phase per fit
        {"origin": "structure", "le_bail": {"A": True}, "structure": {"phase": "A", "thermal": "overall"}},
    ],
)
def test_invalid_le_bail_requests(kwargs):
    with pytest.raises(ValidationError):
        QuickRefineRequest(**kwargs)


def test_labels():
    assert quick_refine.default_label(QuickRefineRequest(origin="le_bail", le_bail={CO3O4: True})) == "Le Bail: Co3O4"
    assert quick_refine.default_label(
        QuickRefineRequest(origin="le_bail", le_bail={CO3O4: False})
    ) == "Le Bail off: Co3O4"
    manual = QuickRefineRequest(controls=RefinementControls(), le_bail={CO3O4: True})
    assert quick_refine.default_label(manual) == "Re-refinement: changed refinement controls, Le Bail: Co3O4"


# --------------------------------------------------------------------------- request -> .str -> version
def test_le_bail_reaches_only_the_chosen_str_and_is_inherited(monkeypatch, env):
    stem, other = env["references"][0].filename, env["references"][1].filename
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
        on = await run(QuickRefineRequest(origin="le_bail", le_bail={stem: True}))
        inherited = await run(QuickRefineRequest(base=f"version:{on}", controls=RefinementControls(lattice="refine")))
        off = await run(QuickRefineRequest(base=f"version:{inherited}", origin="le_bail", le_bail={stem: False}))
        return on, inherited, off

    on, inherited, off = _run(main())
    assert "LeBail=1" in seen[0][stem] and "LeBail" not in seen[0][other]
    assert "LeBail=1" in seen[1][stem]
    assert "LeBail" not in seen[2][stem]
    stored = {r["version"]: r for r in env["versions"].query({"uuid": env["record"]["uuid"]})}
    assert stored[on]["label"] == f"Le Bail: {stem.split('_')[0]}"
    assert stored[on]["origin"]["kind"] == "le_bail"
    assert versions.le_bail_stems(stored[on]) == versions.le_bail_stems(stored[inherited]) == [stem]
    assert versions.le_bail_stems(stored[off]) == []
    entry = versions_router._version_entry(stored[on])
    assert entry["le_bail_phases"] == [stem]


def test_a_second_le_bail_phase_fails_the_job(monkeypatch, env):
    stem, other = env["references"][0].filename, env["references"][1].filename
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())

    async def main():
        first = await _wait((await _submit(QuickRefineRequest(origin="le_bail", le_bail={stem: True})))["job_id"])
        second = await _submit(
            QuickRefineRequest(base=f"version:{first['version']}", origin="le_bail", le_bail={other: True})
        )
        return await _wait(second["job_id"])

    job = _run(main())
    assert job["status"] == "failed" and "Only one phase" in job["error"]


def test_control_file_leaves_the_le_bail_phase_out_of_the_fractions(tmp_path):
    shutil.copy(FIXTURE / f"{CO3O4}.str", tmp_path)
    (tmp_path / "ZnO.str").write_text("PHASE=ZnO //\nRP=4 GEWICHT=SPHAR4 //\n")
    np.savetxt(tmp_path / "p.xy", np.column_stack([np.linspace(10, 80, 50), np.full(50, 100.0)]))
    strs = [tmp_path / "ZnO.str", tmp_path / f"{CO3O4}.str"]
    text = generate_control_file(tmp_path / "p.xy", strs, "Aeris-fds-Pixcel1d-Medipix3", tmp_path).read_text()
    assert "sum=ZnO\n" in text and "QZnO=ZnO/sum" in text and "GOAL[1]=QZnO" in text
    assert "QCo3O4" not in text and "STRUC[2]=" in text
    alone = generate_control_file(tmp_path / "p.xy", strs[1:], "Aeris-fds-Pixcel1d-Medipix3", tmp_path).read_text()
    assert "sum=" not in alone and "GOAL" not in alone


# --------------------------------------------------------------------------- BGMN output
def test_bgmn_output_with_a_le_bail_phase(fit):
    assert fit.lst_data.rwp == pytest.approx(1.60)
    assert le_bail_phases(fit.lst_data) == {CO3O4}
    assert fit.get_phase_weights() == {ZNO: pytest.approx(1.0)}
    details = _phase_refinement_details(fit)
    lebail, rietveld = details[CO3O4], details[ZNO]
    assert lebail["le_bail"] is True and lebail["weight_percent"] is None and lebail["weight_esd_percent"] is None
    assert lebail["lattice_parameters"]["a"]["value"] == pytest.approx(8.08837)
    assert lebail["lattice_parameters"]["a"]["esd"] == pytest.approx(0.00027)
    assert lebail["grain_sizes"][0]["value"] == pytest.approx(74.8)
    assert lebail["rphase"] == pytest.approx(1.81)
    assert lebail["refinement_status"]["level"] != "failed"  # GEWICHT=UNDEF is expected here
    assert rietveld["weight_percent"] == pytest.approx(100.0) and "le_bail" not in rietveld
    assert le_bail_names(details) == [CO3O4]


def test_cautions_and_mass_balance(fit):
    details = _phase_refinement_details(fit)
    cautions = fit_cautions({"rwp": 1.6, "gof": 1.17}, details, None)
    relative = [c for c in cautions if c["code"] == "weights_relative"]
    assert len(relative) == 1 and relative[0]["message"].startswith("Weight fractions exclude the Le Bail phase(s)")
    assert not any(c["code"] == "refinement_failed" for c in cautions)
    extra = []
    block = _mass_balance_block([{"formula": "ZnO", "moles": 1}, {"formula": "Co3O4", "moles": 1}], "input",
                                {ZNO: 100.0}, details, {}, extra, None)
    assert block["status"] == "skipped" and "Le Bail" in block["reason"] and extra == []
    assert fit_cautions({}, {ZNO: {"weight_percent": 50.0}}, None) == []


def test_internal_standard_counts_the_le_bail_phase_in_the_remainder():
    phases = [
        {"name": "Si", "formula": "Si", "weight_percent": 20.0, "weight_esd_percent": 0.5},
        {"name": "A", "formula": "Fe2O3", "weight_percent": 80.0, "weight_esd_percent": 1.0},
        {"name": "B", "formula": "Co3O4", "weight_percent": None, "weight_esd_percent": None, "le_bail": True},
    ]
    out = quantify(phases, {"formula": "Si", "wt_percent": 10.0})
    rows = {row["name"]: row for row in out["phases"]}
    assert rows["A"]["absolute_wt_percent"] == 40.0 and rows["B"]["absolute_wt_percent"] is None
    assert out["amorphous"]["mixture_wt_percent"] == 50.0 and "Le Bail" in out["message"]


def test_refined_cif_of_a_le_bail_phase_is_the_reference_with_the_refined_cell(fit):
    with pytest.raises(CifNotExportable, match="Le Bail"):
        refined_cif_text(CO3O4, fit, meta={})
    reference = Cif.from_str((Path(__file__).parent / "test_data" / "BiFeO3.cif").read_text())
    text, folder = refined_cif_text(CO3O4, fit, meta={"task_id": 120}, reference_cif=reference)
    assert folder == "reference_cifs"
    assert text.startswith("# NOT REFINED by DARA: Le Bail fit") and "a 8.0884(3)" in text.splitlines()[0]
    _, folder = refined_cif_text(ZNO, fit, meta={})
    assert folder == "refined_cifs"


def test_table_rows_keep_a_null_amount(fit):
    details = _phase_refinement_details(fit)
    rows = _rows_for_result(120, "t", "final", None, True, {"phase_details": details, "cautions": []})
    by_phase = {row["phase"]: row for row in rows}
    assert by_phase[CO3O4]["weight_percent"] is None and by_phase[ZNO]["weight_percent"] == pytest.approx(100.0)


def test_gewicht_undef_with_a_q_goal_is_not_taken_for_le_bail(fit):
    raw = fit.lst_data.raw_lst.replace("EPS2=", "QCo3O4227cod9005896=UNDEF\nEPS2=", 1)
    assert le_bail_phases(fit.lst_data.model_copy(update={"raw_lst": raw})) == set()


def test_phase_status_of_a_le_bail_phase():
    from dara.refinement_status import phase_status

    section = "Rphase=1.81%\nUNIT=NM\nA=0.808837+-0.000027\nB1=0.00425+-0.00018\nGEWICHT=UNDEF\n"
    assert phase_status(section)["level"] == "failed"
    assert phase_status(section, le_bail=True)["level"] == "ok"
