"""Task result page fixes (bug sweep 2026-11-24, T1-T22)."""

from dara.server.report.builder import _quantified_sentence


def test_report_summary_says_relative_beside_a_le_bail_phase():
    # T3: a Le Bail fit gives no amount; the other fractions are relative among themselves
    details = {
        "ZnO_186_(cod_9004178)-0": {"le_bail": True, "weight_percent": None},
        "NiO_225_(cod_1)-0": {"weight_percent": 100.0, "weight_esd_percent": None},
    }
    sentence = _quantified_sentence(details)
    assert "Le Bail fit, no amount" in sentence
    assert "100.0 wt%, relative" in sentence
    plain = _quantified_sentence({"NiO_225_(cod_1)-0": {"weight_percent": 100.0}})
    assert "relative" not in plain and _quantified_sentence({}) is None


def test_undetermined_cell_replaces_the_rounded_at_bound_caution_and_blocks_adoption():
    # T4: BGMN printed "A=0.8+-4.1" (nm) for a cell refined within 0.819-0.852 nm (degenerate fit)
    from dara.server import versions_router
    from dara.server.fit_cautions import lattice_cautions, undetermined_lattice

    bad, good = "Ni31.84O32_225_(cod_9013980)", "ZnO_186_(cod_9004178)-0"
    details = {
        bad: {"lattice_parameters": {"a": {"value": 8.0, "esd": 41.0}}},
        good: {"lattice_parameters": {"a": {"value": 3.2498, "esd": 0.0002}, "c": {"value": 5.206, "esd": None}}},
    }
    assert undetermined_lattice(details) == {bad: ["a"]}
    stored = [{"code": "lattice_at_bound", "phase": bad, "level": "warning", "message": "x"},
              {"code": "poor_fit", "phase": None, "level": "warning", "message": "y"}]
    cautions = lattice_cautions(stored, details)
    assert [item["code"] for item in cautions] == ["poor_fit", "lattice_undetermined"]
    assert lattice_cautions(cautions, details) == cautions  # applied twice: no duplicate
    record = {"version": 2, "summary": {"phase_details": details, "cautions": stored}, "phases": [bad, good]}
    entry = versions_router._version_entry(record)
    assert entry["adoptable"] is False and entry["cautions_count"] == 2
    record["summary"]["phase_details"] = {good: details[good]}
    assert versions_router._version_entry(record)["adoptable"] is True


def test_review_refuses_a_version_with_an_undetermined_cell(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from dara.server import review_router, versions

    summary = {"phase_details": {"A": {"lattice_parameters": {"a": {"value": 8.0, "esd": 41.0}}}}}
    monkeypatch.setattr(versions, "load_version", lambda uuid, number, light=False: {"summary": summary})
    with pytest.raises(HTTPException) as error:
        review_router._refuse_undetermined({"uuid": "u"}, "version:2")
    assert error.value.status_code == 409
    review_router._refuse_undetermined({"uuid": "u"}, "best")  # candidates and v1 are not checked


def test_usual_hkl_is_shared_by_plot_list_and_figures():
    # T8: ZnO (P6_3mc) reflections listed by BGMN as (1 -1 0) / (0 1 -1) read (100) / (101), as in papers
    from types import SimpleNamespace

    from pymatgen.core import Lattice

    from dara.miller import usual_hkl, usual_hkl_lookup

    hexagonal = Lattice.hexagonal(3.25, 5.21)
    assert usual_hkl((1, -1, 0), 186, hexagonal) == (1, 0, 0)
    assert usual_hkl((0, 1, -1), 186, hexagonal) == (1, 0, 1)
    phase = SimpleNamespace(get_structure=lambda: SimpleNamespace(lattice=hexagonal))
    refinement = SimpleNamespace(lst_data=SimpleNamespace(phases_results={"ZnO_186_(cod_9004178)-0": phase}))
    usual = usual_hkl_lookup(refinement)
    assert usual("ZnO_186_(cod_9004178)-0", (1, -1, 0)) == (1, 0, 0)
    assert usual("Unknown", (1, -1, 0)) == (1, -1, 0)  # no space group or lattice: as listed


def test_table_counts_unexplained_peaks_like_the_report():
    # T13: table.csv said 23 (noise included) where the page and the report say 16
    from dara.server.table_export import task_table_rows

    phase = "ZnO_186_(cod_9004178)-0"
    result = {"rwp": 7.0, "phases": [phase], "highlighted_phases": [phase], "weight_percent": {phase: 100.0},
              "phase_details": {phase: {"weight_percent": 100.0}},
              "peak_diagnostics": {"counts": {"missing": 23}, "counts_by_class": {"noise": 7, "minor": 10, "significant": 6}}}
    rows = task_table_rows(5, {}, {"task_label": "s", "final_result": result, "all_results": []})
    assert rows[0]["missing_peaks"] == 16


def test_observed_peaks_at_counted_unexplained_positions_stay_unassigned():
    # T11: an unexplained peak was assigned to a zero-intensity reflection nearby
    from dara.server.api_router import unassign_unexplained

    rows = [{"two_theta_deg": 15.74, "phase": "Ni", "phase_label": "Ni", "hkl": "1 1 1", "delta_two_theta_deg": 0.01},
            {"two_theta_deg": 20.00, "phase": "Ni", "phase_label": "Ni", "hkl": "2 0 0", "delta_two_theta_deg": 0.0},
            {"two_theta_deg": 30.00, "phase": "Ni", "phase_label": "Ni", "hkl": "2 2 0", "delta_two_theta_deg": 0.0}]
    diagnostics = {"missing_classified": [{"two_theta": 15.741, "class": "significant"},
                                          {"two_theta": 30.0, "class": "noise"}]}
    out = unassign_unexplained(rows, diagnostics)
    assert [row["phase"] for row in out] == [None, "Ni", "Ni"]
    assert [row.get("unexplained_class") for row in out] == ["significant", None, "noise"]
    assert unassign_unexplained(rows, {}) == rows  # no classification: unchanged


def test_reference_search_finds_a_hydroxide_by_any_spelling(monkeypatch):
    # T15: index rows are spelled LaH3O3 / La(HO)3; the query may be La(OH)3
    from types import SimpleNamespace

    from dara import structure_db
    from dara.server import reference_library_router as router

    rows = {"H-La-O": [["LaH3O3", "9001", 176, None], ["La2O3", "9002", 164, None]]}
    monkeypatch.setattr(structure_db, "shared_cod_database", lambda: SimpleNamespace(preparsed_info=rows))
    monkeypatch.setattr(router, "_read_registry", lambda: [])
    monkeypatch.setattr(structure_db, "cod_cache_dir", lambda: __import__("pathlib").Path("."))
    monkeypatch.setattr(router, "_mp_rows", lambda reduced: [])  # COD only here; MP has its own test
    for query in ("La(OH)3", "LaH3O3", "La(HO)3"):
        assert [item["database_id"] for item in router.search_references(query)] == ["9001"]


def test_report_conditions_name_the_identification_task():
    # T16: a refinement submitted from an identification read "Known mixture ... precursors"
    from types import SimpleNamespace

    from dara.server.report.builder import _Numbering, _conditions_table

    document = SimpleNamespace(analysis_mode="known_mixture", elements=None, precursors=["ZnO", "NiO"])
    html = _conditions_table(_Numbering(), document=document, search_settings={}, metadata=None, identified_from=121)
    assert "phases identified in task 121" in html and "Identified phases" in html
    assert "Known mixture" not in html and "Precursor formulas" not in html
    plain = _conditions_table(_Numbering(), document=document, search_settings={}, metadata=None)
    assert "Known mixture" in plain and "Precursor formulas" in plain


def test_le_bail_phase_is_written_last_in_the_control_file(tmp_path):
    # T4: with the Le Bail phase as STRUC[1], BGMN 4.2.22 printed the other phase's cell as
    # "A=0.8+-4.1" (nm) from a shifted covariance; written last, the same fit lists A=0.835377+-0.000035
    import numpy as np

    from dara.generate_control_file import generate_control_file

    (tmp_path / "ZnO.str").write_text("PHASE=ZnO //\nRP=4 GEWICHT=SPHAR4 //\nLeBail=1\n")
    (tmp_path / "NiO.str").write_text("PHASE=NiO //\nRP=4 GEWICHT=SPHAR4 //\n")
    np.savetxt(tmp_path / "p.xy", np.column_stack([np.linspace(10, 80, 50), np.full(50, 100.0)]))
    text = generate_control_file(
        tmp_path / "p.xy", [tmp_path / "ZnO.str", tmp_path / "NiO.str"], "Aeris-fds-Pixcel1d-Medipix3", tmp_path
    ).read_text()
    assert "STRUC[1]=NiO.str" in text and "STRUC[2]=ZnO.str" in text
    assert "sum=NiO\n" in text and "QZnO" not in text


def test_identification_counts_unexplained_peaks_by_the_search_match_rule(monkeypatch):
    # The task page of an identification counts the peaks the Search-match workspace reported for
    # the same selection (quick_rank.residual_step -> residual_maxima), not its own assignment.
    from pathlib import Path
    from types import SimpleNamespace

    import numpy as np

    from dara.identification import IdentificationDocument
    from dara.search import quick_rank as qr

    calls = {}
    ctx = SimpleNamespace(profile=SimpleNamespace(lo=10.0, hi=80.0), lam1=1.5406)
    monkeypatch.setattr(qr, "_context", lambda *a, **k: (ctx, None))
    monkeypatch.setattr(qr, "reference_path", lambda source: Path(f"{source}.cif"))
    monkeypatch.setattr(qr, "simulate_sticks", lambda *a: ([20.0, 30.0], [100.0, 50.0], [4.4, 3.0]))

    def residual_step(angles, intensities, candidates, selected, **kwargs):
        calls["selected"], calls["background"] = selected, kwargs["background"]
        return {"unexplained_peaks": [[15.74, 1885.7, 5.6255]]}

    monkeypatch.setattr(qr, "residual_step", residual_step)
    xrd = SimpleNamespace(angles=np.linspace(10, 80, 50), intensities=np.full(50, 100.0))
    phases = [{"name": "ZnO_186_(cod_1)", "source": "cod:1", "formula": "ZnO", "spacegroup": 186}]
    document = IdentificationDocument(xrd, {"wavelength": "Cu", "refinement_params": {}}, [], phases)
    assert document.unexplained_peaks == [[15.74, 1885.7, 5.6255]]
    assert calls["selected"] == ["cod:1"] and calls["background"] == {"method": "rolling_minimum"}
    # a phase without a source (cannot be simulated again): the page keeps its own assignment
    assert IdentificationDocument(xrd, {}, [], [{"name": "x"}]).unexplained_peaks is None


def test_reference_search_finds_materials_project_structures(tmp_path, monkeypatch):
    """La(OH)3 is not in the COD index; the downloaded MP library rows find it by any spelling."""
    import json

    from dara import structure_db
    from dara.server import reference_library_router as ref

    monkeypatch.setattr(structure_db, "mp_cache_dir", lambda: tmp_path)
    monkeypatch.delenv("MP_API_KEY", raising=False)
    (tmp_path / "library-rows-x.json").write_text(json.dumps({"rows": [
        {"material_id": "mp-625394", "formula": "La(HO)3", "spacegroup": 176, "e_above_hull": 0.0},
        {"material_id": "mp-1", "formula": "LaO", "spacegroup": 225, "e_above_hull": 0.1},
    ]}), encoding="utf-8")
    for query in ("La(OH)3", "LaH3O3", "La(HO)3"):
        found = [item for item in ref.search_references(query) if item["database"] == "MP"]
        assert [item["source"] for item in found] == ["mp:mp-625394"]
        assert found[0]["ref"] == "La(HO)3_176_(mp_mp-625394)-0" and found[0]["cached"] is False
