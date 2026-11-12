"""Known-phase refinement: groups, probation of unpicked phases, summary, MP fallback rule, phase-set validation."""

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
from monty.json import MontyEncoder

from dara import known_phases as kp
from dara.cif import Cif
from dara.refine import RefinementPhase
from dara.server import api_router
from dara.xrd import load_pattern

DATA = Path(__file__).parent / "test_data"
ZR1, ZR2 = "ZrO2_14_(cod_1010912)-0", "ZrO2_14_(cod_2300296)-0"
LI = "Li2CO3_15_(cod_2310703)-0"
LA = "La(OH)3_176_(mp_mp-1)-0"


def test_groups_use_formula_equivalence_and_keep_input_order():
    stems = [ZR2, ZR1, LI, LA, "Foo_1_(user)"]
    groups = kp.build_groups(["Li2CO3", "La(HO)3", "ZrO2", "Li2CO3"], stems, {"Foo_1_(user)": "Foo"})
    assert [group.label for group in groups] == ["Li2CO3", "La(HO)3", "ZrO2", "Foo"]
    assert groups[1].stems == (LA,)  # La(HO)3 == La(OH)3
    assert groups[2].stems == (ZR1, ZR2)  # sorted, deterministic
    assert groups[3].exact and groups[3].stems == ("Foo_1_(user)",)
    assert kp.formulas_without_structure(["ZrO2", "NaCl"], [ZR1]) == ["NaCl"]


def test_a_user_structure_is_the_group_of_its_formula_and_nothing_else():
    # An uploaded / library / reused structure of ZrO2 satisfies the listed ZrO2: no database record
    # joins it and the formula has one outcome, at its input position.
    groups = kp.build_groups(["ZrO2", "Li2CO3"], [ZR1, ZR2, LI], {"mine": "ZrO2"})
    assert [(group.label, group.stems, group.exact) for group in groups] == [
        ("ZrO2", ("mine",), True), ("Li2CO3", (LI,), False),
    ]
    twins = kp.build_groups(["Zr1O2"], [ZR1], {"a": "ZrO2", "b": "ZrO2"})  # two polymorphs: one group each
    assert [(group.label, group.stems) for group in twins] == [("Zr1O2", ("a",)), ("Zr1O2", ("b",))]
    assert kp.formulas_without_structure(["ZrO2"], []) == ["ZrO2"]
    assert kp.distinct_formulas(["La(OH)3", "ZrO2", "La(HO)3", "Zr1O2"]) == ["La(OH)3", "ZrO2"]


def test_the_trial_structure_is_the_lowest_screening_rwp_else_stem_order():
    group = kp.build_groups(["ZrO2"], [ZR2, ZR1])[0]
    assert kp.pick_stem(group, {}) == ZR1  # no score at all: stem order
    assert kp.pick_stem(group, {ZR2: 4.0, ZR1: 9.0}) == ZR2
    assert kp.pick_stem(group, {ZR2: 4.0}) == ZR2 and kp.pick_stem(group, {ZR1: 9.0}) == ZR1  # unscored ranks last
    assert kp.pick_stem(kp.Group("NaCl", ()), {}) is None


def test_report_has_one_outcome_per_listed_formula():
    groups = kp.build_groups(["ZrO2", "Li2CO3", "La(OH)3", "NaCl"], [ZR1, ZR2, LI, LA, "x_1_(cod_9)-0"])
    groups[3] = kp.Group("NaCl", ())
    report = kp.known_phase_report(
        groups,
        {LI: 0.893, ZR1: 0.107},
        {ZR2: ("duplicate", 0.01), LA: ("negligible", 0.002)},
    )
    assert [entry["formula"] for entry in report] == ["ZrO2", "Li2CO3", "La(OH)3", "NaCl"]
    zr, li, la, na = report
    assert (zr["status"], zr["phases"], zr["weight_percent"]) == ("refined", [ZR1], 10.7)
    assert (li["status"], li["weight_percent"]) == ("refined", 89.3)
    assert (la["status"], la["reason"], la["removed_weight_percent"], la["phases"]) == (
        "not_detected", "negligible", 0.2, [],
    )
    assert na["status"] == "no_structure" and na["weight_percent"] is None
    assert kp.not_detected_text(la) == "La(OH)3: not detected (refined to 0.2 wt%, removed)"
    loo = kp.known_phase_report([kp.Group("ZrO2", (ZR1,))], {}, {ZR1: ("removed_no_peak_evidence", 0.003)})[0]
    assert loo["reason"] == "removed_no_peak_evidence"
    assert kp.not_detected_text(loo) == (  # left the best fit by leave-one-out: not a trial fit
        "ZrO2: not detected (refined to 0.3 wt%, but removing it left no additional peak unexplained)"
    )
    assert kp.not_detected_text(na).startswith("NaCl: no reference structure found")
    assert kp.not_detected_text(zr) is None


def test_trial_outcomes_have_their_own_reasons_and_texts():
    groups = [kp.Group("ZnO", ("ZnO_186_(cod_1)-0",)), kp.Group("NaCl", ("NaCl_225_(cod_5)-0",)), kp.Group("KCl", ("KCl_225_(cod_6)-0",))]
    zn, na, kcl = kp.known_phase_report(
        groups, {}, {"ZnO_186_(cod_1)-0": ("no_peak_evidence", 0.0017), "NaCl_225_(cod_5)-0": ("failed", None)}
    )
    assert (zn["reason"], zn["removed_weight_percent"]) == ("no_peak_evidence", 0.17)
    assert kp.not_detected_text(zn) == "ZnO: not detected (a trial fit gave 0.17 wt% but explained no additional peak)"
    assert (na["reason"], na["removed_weight_percent"]) == ("failed", None)
    assert kp.not_detected_text(na) == "NaCl: not detected (a trial fit with it failed)"
    assert kcl["reason"] is None and kp.not_detected_text(kcl) == "KCl: not detected"
    # nothing fitted at all: every formula with structures is not detected, the others have none
    empty = kp.known_phase_report(groups + [kp.Group("LiCl", ())], {}, {}, reason="no_fit")
    assert [(e["status"], e["reason"]) for e in empty] == [("not_detected", "no_fit")] * 3 + [("no_structure", None)]
    assert kp.not_detected_text(empty[0]) == "ZnO: not detected (no phase combination fitted the pattern)"


def test_screening_report_exposes_the_single_phase_rwp_of_every_reference(monkeypatch):
    from dara.search import tree

    phases = [RefinementPhase.make(Path(f"{stem}.cif")) for stem in (ZR1, ZR2, LA)]
    results = {
        phases[0]: SimpleNamespace(lst_data=SimpleNamespace(rwp=7.5)),
        phases[1]: None,  # not screened / failed: no score
        phases[2]: SimpleNamespace(lst_data=SimpleNamespace(rwp=3.0)),
    }
    monkeypatch.setattr(tree, "estimate_reflections", lambda *args, **kwargs: 100)
    monkeypatch.setattr(tree, "run_screening", lambda *args, **kwargs: (results, {"total": 3}))
    search = SimpleNamespace(
        refinement_params={}, wavelength="Cu", pinned_phases=[], pattern_path=Path("x.xy"), instrument_profile="p",
        phase_params={}, screening_timeouts={}, screening_deadline=None, screening_priority=None, screening_report=None,
    )
    assert tree.SearchTree._screen_phases(search, phases) is results
    assert search.screening_report == {"total": 3, "rwp_by_reference": {ZR1: 7.5, LA: 3.0}}


# ---- submit boundary --------------------------------------------------------------------
@pytest.fixture(scope="module")
def pattern():
    return load_pattern(DATA / "BiFeO3.xy")


def _build(pattern, **kwargs):
    defaults = dict(analysis_mode="known_mixture", use_mp=False)
    defaults.update(kwargs)
    return api_router.build_submission_job(pattern, "BiFeO3.xy", **defaults)


def test_the_twelve_formula_limit_counts_distinct_formulas(pattern):
    formulas = ["LiF", "NaF", "KF", "RbF", "CsF", "MgO", "CaO", "SrO", "BaO", "ZnO", "CdO", "NiO", "CoO"]
    with pytest.raises(HTTPException) as info:
        _build(pattern, precursor_formulas=formulas)
    assert info.value.status_code == 400 and "at most 12" in info.value.detail
    # the same 12 formulas listed again (another spelling too) are still 12 phases
    job = _build(pattern, precursor_formulas=[*formulas[:12], "LiF", "Zn1O1", "ZnO"])
    kwargs = job.function_kwargs
    assert kwargs["precursors"] == formulas[:12]  # first spelling, input order
    assert kwargs["search_kwargs"]["max_phases"] == 5  # min(distinct formulas, 5) again


def test_duplicate_spellings_of_one_formula_are_listed_once(pattern):
    job = _build(pattern, precursor_formulas=["La(OH)3", "ZrO2", "La(HO)3"])
    assert job.function_kwargs["precursors"] == ["La(OH)3", "ZrO2"]
    assert job.function_kwargs["search_kwargs"]["max_phases"] == 2


def test_a_phase_set_replaces_the_formula_input_and_records_its_source(pattern):
    cifs = [Cif.from_file(DATA / "BiFeO3.cif"), Cif.from_file(DATA / "Bi2Fe4O9.cif")]
    job = _build(pattern, phase_set_cifs=cifs, phase_set_from=12, analysis_mode="", precursor_formulas="[]")
    kwargs = job.function_kwargs
    assert kwargs["analysis_mode"] == "known_mixture" and kwargs["fixed_phase_cifs"] == cifs
    assert sorted(kwargs["precursors"]) == ["Bi2Fe4O9", "BiFeO3"]
    assert kwargs["search_kwargs"]["max_phases"] == 2 and kwargs["cif_dbs"] == []
    assert job.metadata["submission_settings"]["phase_set_from"] == 12
    with pytest.raises(HTTPException) as info:
        _build(pattern, phase_set_cifs=cifs, analysis_mode="elemental")
    assert info.value.status_code == 400


def _store(record):
    @contextmanager
    def store():
        yield SimpleNamespace(query_one=lambda query: record)

    return store


@pytest.mark.parametrize("record, status", [(None, 404), ({"index": 5, "status": "RUNNING"}, 409)])
def test_phase_set_task_must_exist_and_be_finished(monkeypatch, record, status):
    from dara.server import quick_refine

    monkeypatch.setattr(quick_refine, "get_worker_store", _store(record))
    with pytest.raises(HTTPException) as info:
        asyncio.run(api_router.resolve_phase_set(5))
    assert info.value.status_code == status


# ---- Materials Project fallback never changes the stored MP setting -----------------------
def test_mp_fallback_leaves_use_mp_off_in_the_stored_and_rerun_settings(pattern):
    job = _build(pattern, precursor_formulas=["La(OH)3", "ZrO2"], use_cod=True, use_mp=False)
    assert not any(type(db).__name__ == "MaterialsProjectDatabase" for db in job.function_kwargs["cif_dbs"])
    assert "use_mp" not in job.metadata["submission_settings"]
    kwargs = json.loads(json.dumps({"cif_dbs": job.function_kwargs["cif_dbs"]}, cls=MontyEncoder))
    # The fallback database is created inside the run, so it can never reach the saved kwargs.
    record = {"index": 1, "user": "u", "job": {"metadata": job.metadata, "name": "x"}}
    settings = api_router._rerun_settings(record, kwargs, pattern)["settings"]
    assert settings["use_mp"] is False and settings["use_cod"] is True


def test_report_states_missing_phases_and_the_mp_fallback():
    from dara.server.report.builder import _known_phase_notes

    document = SimpleNamespace(
        search_summary={"known_phases": [
            {"formula": "Li2CO3", "status": "refined"},
            {"formula": "ZrO2", "status": "not_detected", "removed_weight_percent": 0.2},
            {"formula": "NaCl", "status": "no_structure"},
            {"formula": "KCl", "status": "not_detected", "removed_weight_percent": 0.3, "reason": "removed_no_peak_evidence"},
        ]},
        candidate_summary={"mp_fallback_formulas": ["La(OH)3"]},
    )
    text = " ".join(_known_phase_notes(document, True))
    assert "ZrO2: not detected (refined to 0.2 wt%, removed)." in text
    assert "NaCl: no reference structure found" in text and "Li2CO3" not in text
    assert "KCl: not detected (refined to 0.3 wt%, but removing it left no additional peak unexplained)." in text
    assert "La(OH)3: structure taken from Materials Project (no COD structure)." in text
    # an adopted re-refinement is not the automatic fit the summary describes
    assert "ZrO2" not in " ".join(_known_phase_notes(document, False))


def test_known_mode_warning_names_the_materials_project_instead_of_suggestions(monkeypatch):
    from dara import structure_db

    info = {"La-O": [("La2O3", 1, 164, 0.0)], "O-Zr": [("ZrO2", 14, 14, 0.0)]}
    monkeypatch.setattr(structure_db, "_COD_INSTANCE", SimpleNamespace(preparsed_info=info))
    api_router._cod_formulas.cache_clear()
    try:
        monkeypatch.setattr(api_router, "_mp_configured", lambda: True)
        found = api_router._precursor_warnings('["La(OH)3", "ZrO2"]', True, "known_mixture")
        assert [item["code"] for item in found] == ["precursor_from_mp"]
        assert "Materials Project" in found[0]["message"] and found[0]["suggestions"] == []
        monkeypatch.setattr(api_router, "_mp_configured", lambda: False)
        found = api_router._precursor_warnings('["La(OH)3"]', True, "known_mixture")
        assert found[0]["code"] == "precursor_no_structure" and found[0]["suggestions"] == []
        # other modes keep the typo hint
        assert api_router._precursor_warnings('["La(OH)3"]', True)[0]["code"] == "precursor_not_in_cod"
    finally:
        api_router._cod_formulas.cache_clear()


# ---- the job: Materials Project fallback decided per formula, for this run only --------------
class _FakeDB:
    """Copies reference CIFs by formula; ``name`` / ``last_unavailable`` like a StructureDatabase."""

    def __init__(self, name, files):
        self.name, self.files, self.calls, self.last_unavailable = name, files, [], []

    def as_dict(self):  # jobflow serialises the job arguments
        return {"@class": "_FakeDB", "name": self.name}

    def get_cifs_by_formulas(self, formulas, e_hull_filter=None, copy_files=True, dest_dir=".", **kwargs):
        import shutil

        self.calls.append(list(formulas))
        for formula in formulas:
            for target, source in self.files.get(kp.reduced_formula(formula), []):
                shutil.copy(DATA / source, Path(dest_dir) / f"{target}.cif")


def _run_job(monkeypatch, tmp_path, pattern, cod, mp_files, precursors=("BiFeO3", "Bi2Fe4O9"), **kwargs):
    import dara.jobs as jobs

    fallback_instances = []

    class FakeMP(_FakeDB):
        def __init__(self, e_hull_filter=None):
            super().__init__("mp", mp_files)
            fallback_instances.append(self)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs, "MaterialsProjectDatabase", FakeMP)
    monkeypatch.setattr(jobs, "search_phases", lambda **kwargs: [])
    monkeypatch.setattr(kp, "mp_configured", lambda: True)
    maker = jobs.PhaseSearchMaker(phase_predictor=None, max_num_results=5)
    document = jobs.PhaseSearchMaker.make.original(  # the undecorated body: runs, no jobflow Job
        maker, xrd_data=pattern, cif_dbs=[cod], precursors=list(precursors),
        analysis_mode="known_mixture", search_kwargs={"max_phases": 2}, **kwargs,
    )
    return document, fallback_instances


def test_job_takes_only_the_formula_without_a_cod_structure_from_mp(monkeypatch, tmp_path, pattern):
    cod = _FakeDB("cod", {"FeBiO3": [("BiFeO3_161_(cod_1)-0", "BiFeO3.cif")]})
    mp_files = {"Fe4Bi2O9": [("Bi2Fe4O9_55_(mp_mp-1)-0", "Bi2Fe4O9.cif")]}
    document, fallbacks = _run_job(monkeypatch, tmp_path, pattern, cod, mp_files)
    assert cod.calls == [["BiFeO3", "Bi2Fe4O9"]]
    assert [instance.calls for instance in fallbacks] == [[["Bi2Fe4O9"]]]  # one call, only the missing formula
    assert document.candidate_summary["mp_fallback_formulas"] == ["Bi2Fe4O9"]
    assert document.candidate_sources == ["cod", "mp"]


def test_an_uploaded_cif_of_a_listed_formula_is_not_looked_up_nor_listed_twice(monkeypatch, tmp_path, pattern):
    uploaded = Cif.from_file(DATA / "BiFeO3.cif")  # satisfies the listed BiFeO3
    cod = _FakeDB("cod", {"FeBiO3": [("BiFeO3_161_(cod_1)-0", "BiFeO3.cif")]})
    mp_files = {
        "Fe4Bi2O9": [("Bi2Fe4O9_55_(mp_mp-1)-0", "Bi2Fe4O9.cif")],
        "FeBiO3": [("BiFeO3_161_(mp_mp-2)-0", "BiFeO3.cif")],
    }
    document, fallbacks = _run_job(monkeypatch, tmp_path, pattern, cod, mp_files, additional_cifs=[uploaded])
    assert cod.calls == [["Bi2Fe4O9"]]  # no COD record for BiFeO3
    assert [instance.calls for instance in fallbacks] == [[["Bi2Fe4O9"]]]  # and no MP fallback for it either
    assert document.candidate_summary["mp_fallback_formulas"] == ["Bi2Fe4O9"]
    # the search found nothing here; each listed formula has exactly one outcome
    assert [(entry["formula"], entry["status"]) for entry in document.search_summary["known_phases"]] == [
        ("BiFeO3", "not_detected"), ("Bi2Fe4O9", "not_detected"),
    ]
    assert document.search_summary["known_phases"][0]["phases"] == []


def test_a_search_without_any_fit_still_states_every_listed_formula(monkeypatch, tmp_path, pattern):
    cod = _FakeDB("cod", {"FeBiO3": [("BiFeO3_161_(cod_1)-0", "BiFeO3.cif")]})
    monkeypatch.setattr(kp, "mp_configured", lambda: False)
    document, _ = _run_job(monkeypatch, tmp_path, pattern, cod, {})
    assert document.final_result is None and document.results == []
    known = document.search_summary["known_phases"]
    assert [(e["formula"], e["status"], e["reason"]) for e in known] == [
        ("BiFeO3", "not_detected", "no_fit"), ("Bi2Fe4O9", "no_structure", None),
    ]


def test_the_failure_names_the_formulas_without_a_structure(monkeypatch, tmp_path, pattern):
    monkeypatch.setattr(kp, "mp_configured", lambda: False)
    with pytest.raises(ValueError) as info:
        _run_job(monkeypatch, tmp_path, pattern, _FakeDB("cod", {}), {}, precursors=("La(OH)3", "BiFeO3"))
    assert str(info.value) == (
        "No reference structure was found for La(OH)3, BiFeO3. Enable Materials Project or add a CIF."
    )


def test_job_does_not_query_mp_when_every_formula_has_a_cod_structure(monkeypatch, tmp_path, pattern):
    both = {
        "FeBiO3": [("BiFeO3_161_(cod_1)-0", "BiFeO3.cif")],
        "Fe4Bi2O9": [("Bi2Fe4O9_55_(cod_2)-0", "Bi2Fe4O9.cif")],
    }
    document, fallbacks = _run_job(monkeypatch, tmp_path, pattern, _FakeDB("cod", both), {})
    assert fallbacks == [] and document.candidate_summary["mp_fallback_formulas"] == []
    assert document.candidate_sources == ["cod"]


# ---- the job: probation of listed formulas the search did not pick ------------------------------
NACL, KCL, KCL2, RBCL = "NaCl_225_(cod_5)-0", "KCl_225_(cod_6)-0", "KCl_225_(cod_8)-0", "RbCl_225_(cod_7)-0"
KCL_P = "KCl_221_(cod_9)-0"  # another space group: a polymorph of KCl


def _slot_result(phases):
    """A search result with one slot (one phase) per structure."""
    from test_runtime_budget import FakeResult

    result = FakeResult(phases)
    result.phases = tuple((phase,) for phase in phases)
    result.foms = tuple((1.0,) for _ in phases)
    result.lattice_strains = tuple((0.0,) for _ in phases)
    return result


def _known_job(monkeypatch, tmp_path, table, *, fail=(), screening=None, files=None, step=0.1, picked=((NACL,),),
               precursors=("NaCl", "KCl", "RbCl", "LiCl"), rwp=None, classify=False, runtime=None, **run_options):
    """The search picks the structures of each tuple of ``picked`` (default: only NaCl). ``table`` maps the
    stems of a refined fit to ``(unexplained peaks, weight fractions)``; ``fail`` lists the fits whose
    refinement raises. A tuple ``(significant, noise)`` of unexplained peaks has the classifier tell them apart
    (``classify``); otherwise every unexplained peak counts. ``rwp(names)`` overrides the fit Rwp."""
    from test_runtime_budget import THREE, run_job

    from dara.server import peak_significance

    monkeypatch.setattr(kp, "mp_configured", lambda: False)
    if classify:  # the second column of a peak row: -1 is a noise peak
        def classified(refinement, missing, **kwargs):
            counts = {"significant": sum(1 for row in missing if row[1] > 0), "minor": 0}
            return {"counts_by_class": counts}

        monkeypatch.setattr(peak_significance, "classify_missing_peaks", classified)
    tried = []

    def refinement(phases):
        from test_runtime_budget import FakeRefinement

        names = frozenset(phase.path.stem for phase in phases)
        tried.append(names)
        if names in fail:
            raise RuntimeError("BGMN did not converge")
        missing, weights = table[names]
        significant, noise = missing if isinstance(missing, tuple) else (missing, 0)
        rows = [[0.0, 1.0]] * significant + [[0.0, -1.0]] * noise
        fit = FakeRefinement(rwp(names) if rwp else 5.0 - step * len(names))
        fit.plot_data = SimpleNamespace(x=np.linspace(0.0, 100.0, 5))
        fit.refinement_metrics = SimpleNamespace(
            missing_peaks=np.array(rows) if rows else None, extra_peaks=None, intensity_mismatch_peaks=None
        )
        fit.lst_data.phases_results = {
            name: SimpleNamespace(gewicht=weights[name], a=5.0, spacegroup_no=int(name.split("_")[1])) for name in names
        }
        total = sum(weights.values())
        fit.get_phase_weights = lambda: {name: weight / total for name, weight in weights.items()}
        return fit

    make_kwargs = {"analysis_mode": "known_mixture", "precursors": list(precursors), "search_kwargs": {"max_phases": 4}}
    if runtime:
        make_kwargs["max_runtime_s"] = runtime
    document, _ = run_job(
        monkeypatch, tmp_path, files or THREE, write_formulas=True, refinement_factory=refinement,
        search_returns=lambda by_stem: [_slot_result([by_stem[stem] for stem in stems]) for stems in picked],
        screening=screening, make_kwargs=make_kwargs, **run_options,
    )
    report = {entry["formula"]: entry for entry in document.search_summary["known_phases"]}
    return document, report, tried


def test_a_listed_phase_that_explains_more_peaks_joins_the_best_fit(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): (5, {NACL: 1.0}),
        frozenset({NACL, KCL}): (2, {NACL: 0.7, KCL: 0.3}),  # 3 fewer unexplained peaks: accepted
        frozenset({NACL, KCL, RBCL}): (2, {NACL: 0.69, KCL: 0.3, RBCL: 0.01}),  # no further drop: rejected
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table)
    assert tried == [frozenset({NACL}), frozenset({NACL, KCL}), frozenset({NACL, KCL, RBCL})]  # later trials build on it
    assert (report["KCl"]["status"], report["KCl"]["phases"], report["KCl"]["weight_percent"]) == ("refined", [KCL], 30.0)
    rb = report["RbCl"]
    assert (rb["status"], rb["reason"], rb["removed_weight_percent"]) == ("not_detected", "no_peak_evidence", 1.0)
    assert kp.not_detected_text(rb) == "RbCl: not detected (a trial fit gave 1 wt% but explained no additional peak)"
    assert report["LiCl"]["status"] == "no_structure" and report["NaCl"]["status"] == "refined"
    # the stored best result is the trial fit: its phases, its Rwp and its files agree
    assert sorted(cifs[0].filename for cifs in document.results[0][0]) == sorted([NACL, KCL])
    assert document.final_result.lst_data.rwp == pytest.approx(4.8) and document.best_rwp == pytest.approx(4.8)
    assert len(document.missing_peaks[0]) == 2  # the two peaks the trial fit left
    assert document.search_summary["pruned_phases"] == []
    assert sorted(path.name for path in tmp_path.glob("0_best_rwp_*")) == ["0_best_rwp_4.8"]
    assert not list(tmp_path.glob("_final_candidate_*"))  # no trial directory is left behind


def test_an_accepted_phase_goes_through_pruning_once(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): (5, {NACL: 1.0}),
        frozenset({NACL, KCL}): (2, {NACL: 0.997, KCL: 0.003}),  # explains peaks, but only 0.3 wt%
        frozenset({NACL, KCL, RBCL}): (2, {NACL: 0.99, KCL: 0.003, RBCL: 0.007}),
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, step=0.01)
    assert tried == [frozenset({NACL}), frozenset({NACL, KCL}), frozenset({NACL, KCL, RBCL}), frozenset({NACL})]
    assert (report["KCl"]["status"], report["KCl"]["reason"], report["KCl"]["removed_weight_percent"]) == (
        "not_detected", "negligible", 0.3,
    )
    assert report["RbCl"]["reason"] == "no_peak_evidence"
    assert document.search_summary["pruned_phases"] == [{"candidate": 1, "phase": KCL, "reason": "negligible"}]
    assert [cifs[0].filename for cifs in document.results[0][0]] == [NACL]


def test_a_listed_phase_without_peak_evidence_is_reported_and_the_fit_stays(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): (3, {NACL: 1.0}),
        frozenset({NACL, KCL}): (3, {NACL: 0.7, KCL: 0.3}),  # soaks up misfit, explains no peak
        frozenset({NACL, RBCL}): (4, {NACL: 0.99, RBCL: 0.01}),  # even more unexplained peaks
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table)
    assert tried == [frozenset({NACL}), frozenset({NACL, KCL}), frozenset({NACL, RBCL})]  # no prune refit
    assert (report["KCl"]["status"], report["KCl"]["reason"], report["KCl"]["removed_weight_percent"]) == (
        "not_detected", "no_peak_evidence", 30.0,
    )
    assert (report["RbCl"]["reason"], report["RbCl"]["removed_weight_percent"]) == ("no_peak_evidence", 1.0)
    assert report["NaCl"]["status"] == "refined" and report["NaCl"]["weight_percent"] == 100.0
    assert [cifs[0].filename for cifs in document.results[0][0]] == [NACL]
    assert document.final_result.lst_data.rwp == pytest.approx(4.9)  # the fit without the trials


def test_a_trial_that_fails_to_refine_is_a_rejection_and_later_trials_go_on(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): (3, {NACL: 1.0}),
        frozenset({NACL, RBCL}): (1, {NACL: 0.6, RBCL: 0.4}),
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, fail={frozenset({NACL, KCL})})
    assert (report["KCl"]["status"], report["KCl"]["reason"], report["KCl"]["removed_weight_percent"]) == (
        "not_detected", "failed", None,
    )
    assert kp.not_detected_text(report["KCl"]) == "KCl: not detected (a trial fit with it failed)"
    assert report["RbCl"]["status"] == "refined" and report["RbCl"]["weight_percent"] == 40.0
    assert sorted(cifs[0].filename for cifs in document.results[0][0]) == sorted([NACL, RBCL])


def test_the_trial_uses_the_structure_with_the_lowest_screening_rwp(monkeypatch, tmp_path):
    from test_runtime_budget import THREE, salt

    files = {**THREE, KCL2: salt("K", 6.2)}  # a second KCl record
    screening = {"rwp_by_reference": {KCL: 9.0, KCL2: 4.0, RBCL: 6.0}}
    table = {frozenset({NACL}): (3, {NACL: 1.0}), frozenset({NACL, KCL2}): (3, {NACL: 0.8, KCL2: 0.2}),
             frozenset({NACL, RBCL}): (3, {NACL: 0.8, RBCL: 0.2})}
    _, report, tried = _known_job(monkeypatch, tmp_path, table, files=files, screening=screening)
    assert frozenset({NACL, KCL2}) in tried and frozenset({NACL, KCL}) not in tried
    # no score at all: the first record in stem order (cod_6 before cod_8)
    table = {frozenset({NACL}): (3, {NACL: 1.0}), frozenset({NACL, KCL}): (3, {NACL: 0.8, KCL: 0.2}),
             frozenset({NACL, RBCL}): (3, {NACL: 0.8, RBCL: 0.2})}
    _, report, tried = _known_job(monkeypatch, tmp_path, table, files=files)
    assert frozenset({NACL, KCL}) in tried and frozenset({NACL, KCL2}) not in tried


def test_one_structure_per_formula_in_each_hypothesis_and_one_hypothesis_per_formula_set():
    from dara import jobs

    phase = {stem: RefinementPhase.make(Path(f"{stem}.cif")) for stem in (ZR1, ZR2, LI)}
    groups = kp.build_groups(["ZrO2", "Li2CO3"], [ZR1, ZR2, LI])
    results = [_slot_result([phase[LI], phase[ZR1], phase[ZR2]]), _slot_result([phase[LI], phase[ZR2]]),
               _slot_result([phase[LI], phase[ZR1]])]
    kept, rwps = jobs._one_structure_per_formula(results, [3.0, 4.0, 5.0], groups, {ZR1: 9.0, ZR2: 4.0})
    # the lowest screening Rwp of the structures of a hypothesis stays; one formula set, one hypothesis
    assert [[p[0].path.stem for p in result.phases] for result in kept] == [[LI, ZR2]]
    assert rwps == [3.0]


ANATASE, RUTILE, RUTILE2, BROOKITE = "TiO2_141_(cod_1)-0", "TiO2_136_(cod_2)-0", "TiO2_136_(cod_3)-0", "TiO2_61_(cod_4)-0"


def _tio2(*stems_per_hypothesis, formulas=("TiO2", "Li2CO3")):
    stems = {LI, *(stem for stems in stems_per_hypothesis for stem in stems)}
    phase = {stem: RefinementPhase.make(Path(f"{stem}.cif")) for stem in stems}
    groups = kp.build_groups(list(formulas), list(phase))
    return groups, [_slot_result([phase[stem] for stem in stems]) for stems in stems_per_hypothesis]


def _stems(results):
    return [sorted(p[0].path.stem for p in result.phases) for result in results]


def test_hypotheses_with_one_formula_set_and_other_extra_polymorphs_give_one_final_refinement():
    from dara import jobs

    groups, results = _tio2([LI, ANATASE], [LI, ANATASE, RUTILE], [LI, ANATASE, BROOKITE], [LI, RUTILE])
    kept, rwps = jobs._one_structure_per_formula(results, [4.84, 4.81, 4.9, 4.95], groups, {})
    assert _stems(kept) == [sorted([LI, ANATASE, RUTILE])] and rwps == [4.81]  # the best-rated one
    kept, rwps = jobs._one_structure_per_formula(results, [4.0, 4.1, 4.2, 4.3], groups, {})
    assert _stems(kept) == [sorted([LI, ANATASE])] and rwps == [4.0]


def test_the_best_rated_hypothesis_with_two_polymorphs_keeps_both_structures():
    from dara import jobs

    groups, results = _tio2([LI, ANATASE, RUTILE2, RUTILE], [LI, ANATASE, RUTILE])
    kept, rwps = jobs._one_structure_per_formula(results, [3.0, 4.0], groups, {RUTILE: 9.0, RUTILE2: 4.0})
    assert _stems(kept) == [sorted([LI, ANATASE, RUTILE2])] and rwps == [3.0]  # same space group collapsed only


def test_hypotheses_with_different_formula_sets_are_all_kept():
    from dara import jobs

    groups, results = _tio2([LI], [LI, ANATASE], [ANATASE, RUTILE])
    kept, rwps = jobs._one_structure_per_formula(results, [5.0, 4.0, 6.0], groups, {})
    assert _stems(kept) == _stems(results) and rwps == [5.0, 4.0, 6.0]


def test_two_polymorphs_in_the_best_fit_are_listed_under_one_formula(monkeypatch, tmp_path):
    from test_runtime_budget import THREE, salt

    files = {**THREE, KCL_P: salt("K", 3.1)}
    table = {
        frozenset({NACL, KCL, KCL_P}): (3, {NACL: 0.95, KCL: 0.03, KCL_P: 0.02}),
        frozenset({NACL, KCL}): (5, {NACL: 0.97, KCL: 0.03}),  # each one explains peaks: both stay
        frozenset({NACL, KCL_P}): (5, {NACL: 0.98, KCL_P: 0.02}),
        frozenset({NACL, KCL, KCL_P, RBCL}): (3, {NACL: 0.94, KCL: 0.03, KCL_P: 0.02, RBCL: 0.01}),
    }
    document, report, tried = _known_job(
        monkeypatch, tmp_path, table, files=files, picked=((NACL, KCL, KCL_P),), precursors=("NaCl", "KCl", "RbCl")
    )
    assert tried[0] == frozenset({NACL, KCL, KCL_P})  # not collapsed to one KCl structure
    assert (report["KCl"]["status"], report["KCl"]["phases"], report["KCl"]["weight_percent"]) == (
        "refined", [KCL_P, KCL], 5.0,  # both structures, the weights added
    )
    assert document.search_summary["pruned_phases"] == []


def test_leave_one_out_removes_one_polymorph_and_records_it(monkeypatch, tmp_path):
    from test_runtime_budget import THREE, salt

    files = {**THREE, KCL_P: salt("K", 3.1)}
    table = {
        frozenset({NACL, KCL, KCL_P}): (3, {NACL: 0.95, KCL: 0.03, KCL_P: 0.02}),
        frozenset({NACL, KCL}): (3, {NACL: 0.97, KCL: 0.03}),  # the polymorph explains no peak: removed
        frozenset({NACL}): (5, {NACL: 1.0}),  # KCl does: it stays
        frozenset({NACL, KCL, RBCL}): (3, {NACL: 0.96, KCL: 0.03, RBCL: 0.01}),
    }
    document, report, tried = _known_job(
        monkeypatch, tmp_path, table, files=files, picked=((NACL, KCL, KCL_P),), precursors=("NaCl", "KCl", "RbCl")
    )
    assert tried[:3] == [frozenset({NACL, KCL, KCL_P}), frozenset({NACL, KCL}), frozenset({NACL})]
    assert (report["KCl"]["status"], report["KCl"]["phases"], report["KCl"]["weight_percent"]) == ("refined", [KCL], 3.0)
    assert document.search_summary["pruned_phases"] == [
        {"candidate": 1, "phase": KCL_P, "reason": "removed_no_peak_evidence", "weight_percent": 2.0}
    ]
    assert sorted(cifs[0].filename for cifs in document.results[0][0]) == sorted([NACL, KCL])


def test_a_search_hypothesis_never_refines_two_structures_of_one_formula(monkeypatch, tmp_path):
    from test_runtime_budget import THREE, salt

    files = {**THREE, KCL2: salt("K", 6.2)}
    table = {frozenset({NACL, KCL}): (3, {NACL: 0.9, KCL: 0.1}), frozenset({NACL, KCL, RBCL}): (3, {NACL: 0.8, KCL: 0.1, RBCL: 0.1})}
    document, report, tried = _known_job(
        monkeypatch, tmp_path, table, files=files, picked=((NACL, KCL, KCL2),), precursors=("NaCl", "KCl", "RbCl")
    )
    assert tried[0] == frozenset({NACL, KCL})  # cod_6 before cod_8: no screening score, stem order
    assert all(not {KCL, KCL2} <= names for names in tried)
    assert len(document.results) == 1 and report["KCl"]["phases"] == [KCL]


def test_a_minor_phase_without_unexplained_peaks_to_explain_is_dropped(monkeypatch, tmp_path):
    table = {
        frozenset({NACL, KCL}): (3, {NACL: 0.97, KCL: 0.03}),
        frozenset({NACL}): (3, {NACL: 1.0}),  # the same 3 unexplained peaks without KCl
        frozenset({NACL, RBCL}): (3, {NACL: 0.99, RBCL: 0.01}),
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, picked=((NACL, KCL),))
    assert tried == [frozenset({NACL, KCL}), frozenset({NACL}), frozenset({NACL, RBCL})]  # KCl is not tried again
    assert (report["KCl"]["status"], report["KCl"]["reason"], report["KCl"]["removed_weight_percent"]) == (
        "not_detected", "removed_no_peak_evidence", 3.0,
    )
    assert report["RbCl"]["reason"] == "no_peak_evidence"  # a trial fit, not a removal
    assert [cifs[0].filename for cifs in document.results[0][0]] == [NACL]
    assert document.final_result.lst_data.rwp == pytest.approx(4.9)
    assert document.search_summary["pruned_phases"] == [
        {"candidate": 1, "phase": KCL, "reason": "removed_no_peak_evidence", "weight_percent": 3.0}
    ]
    assert sorted(path.name for path in tmp_path.glob("0_best_rwp_*")) == ["0_best_rwp_4.9"]
    assert not list(tmp_path.glob("_final_candidate_*"))


def test_a_minor_phase_that_explains_peaks_stays_and_a_heavier_one_is_not_tested(monkeypatch, tmp_path):
    table = {
        frozenset({NACL, KCL}): (3, {NACL: 0.97, KCL: 0.03}),
        frozenset({NACL}): (5, {NACL: 1.0}),  # two more unexplained peaks without it: it explains them
        frozenset({NACL, KCL, RBCL}): (3, {NACL: 0.9, KCL: 0.03, RBCL: 0.07}),
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, picked=((NACL, KCL),))
    assert tried == [frozenset({NACL, KCL}), frozenset({NACL}), frozenset({NACL, KCL, RBCL})]
    assert report["KCl"]["status"] == "refined" and report["KCl"]["weight_percent"] == 3.0
    # a phase of 5 wt% or more is not tested at all
    table[frozenset({NACL, KCL})] = (3, {NACL: 0.95, KCL: 0.05})
    table[frozenset({NACL, KCL, RBCL})] = (3, {NACL: 0.9, KCL: 0.05, RBCL: 0.05})
    _, report, tried = _known_job(monkeypatch, tmp_path, table, picked=((NACL, KCL),))
    assert tried == [frozenset({NACL, KCL}), frozenset({NACL, KCL, RBCL})]


def test_leave_one_out_never_drops_a_user_structure_or_the_last_phase(tmp_path):
    from dara import jobs

    phases = [RefinementPhase.make(Path(f"{stem}.cif")) for stem in (NACL, KCL)]
    fit = SimpleNamespace(
        lst_data=SimpleNamespace(phases_results={NACL: 1, KCL: 1}), refinement_metrics=None,
        get_phase_weights=lambda: {NACL: 0.97, KCL: 0.03},
    )
    result = _slot_result(phases)
    result.refinement_result = fit
    groups = [kp.Group("NaCl", (NACL,)), kp.Group("KCl", (KCL,), exact=True)]

    def refit(*args):
        raise AssertionError("no refit expected")

    best_dir = tmp_path / "best"
    best_dir.mkdir()
    removed = {}
    out = jobs._check_listed_phases(
        result, best_dir, groups, {}, {}, removed, prepare=lambda r: [], refit=refit,
        count=lambda refinement: 0, out_of_time=lambda: False,
    )
    assert out == (result, None, False) and removed == {}


def test_additive_trial_is_rejected_when_only_a_noise_peak_disappears(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): ((0, 1), {NACL: 1.0}),  # one peak left, classified as noise
        frozenset({NACL, KCL}): ((0, 0), {NACL: 0.99, KCL: 0.01}),  # it vanishes: 1 -> 0 raw, 0 -> 0 significant
        frozenset({NACL, RBCL}): ((0, 0), {NACL: 0.99, RBCL: 0.01}),
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, classify=True)
    assert tried == [frozenset({NACL})]  # the classified count is 0: no trial can lower it
    assert (report["KCl"]["reason"], report["KCl"]["removed_weight_percent"]) == ("no_unexplained_peaks", None)
    assert report["RbCl"]["status"] == "not_detected"
    assert [cifs[0].filename for cifs in document.results[0][0]] == [NACL]
    # a noise peak is not counted, but with one significant peak the trials run again
    table[frozenset({NACL})] = ((1, 1), {NACL: 1.0})
    _, report, tried = _known_job(monkeypatch, tmp_path, table, classify=True)
    assert len(tried) == 2 and report["KCl"]["status"] == "refined"  # 1 -> 0 accepted
    assert report["RbCl"]["reason"] == "no_unexplained_peaks"  # nothing left for it to explain


def test_additive_trial_is_accepted_when_the_significant_or_minor_count_drops(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): ((3, 1), {NACL: 1.0}),
        frozenset({NACL, KCL}): ((1, 1), {NACL: 0.7, KCL: 0.3}),  # 3 -> 1: accepted
        frozenset({NACL, KCL, RBCL}): ((1, 0), {NACL: 0.6, KCL: 0.3, RBCL: 0.1}),  # raw 2 -> 1, significant 1 -> 1
    }
    document, report, tried = _known_job(monkeypatch, tmp_path, table, classify=True)
    assert report["KCl"]["status"] == "refined"
    assert (report["RbCl"]["status"], report["RbCl"]["reason"]) == ("not_detected", "no_peak_evidence")


def test_no_trial_runs_when_the_fit_has_no_unexplained_peak(monkeypatch, tmp_path):
    table = {frozenset({NACL}): (0, {NACL: 1.0})}  # a missing entry would raise: no other fit is refined
    document, report, tried = _known_job(monkeypatch, tmp_path, table)
    assert tried == [frozenset({NACL})]
    for formula in ("KCl", "RbCl"):
        entry = report[formula]
        assert (entry["status"], entry["reason"], entry["removed_weight_percent"]) == ("not_detected", "no_unexplained_peaks", None)
        assert kp.not_detected_text(entry) == f"{formula}: not detected (the fit leaves no unexplained peak for it to explain)"
    assert report["LiCl"]["status"] == "no_structure" and report["NaCl"]["status"] == "refined"
    assert document.final_result.lst_data.rwp == pytest.approx(4.9)  # the fit as it was
    assert [cifs[0].filename for cifs in document.results[0][0]] == [NACL]
    assert document.search_summary["pruned_phases"] == []


def test_the_trials_stop_once_an_accepted_trial_leaves_no_unexplained_peak(monkeypatch, tmp_path):
    table = {
        frozenset({NACL}): (2, {NACL: 1.0}),
        frozenset({NACL, KCL}): (0, {NACL: 0.7, KCL: 0.3}),  # 2 -> 0: accepted; RbCl has nothing left to explain
    }
    _, report, tried = _known_job(monkeypatch, tmp_path, table)
    assert tried == [frozenset({NACL}), frozenset({NACL, KCL})]
    assert report["KCl"]["status"] == "refined"
    assert (report["RbCl"]["reason"], report["RbCl"]["removed_weight_percent"]) == ("no_unexplained_peaks", None)


def test_a_zero_count_without_peak_metrics_is_not_taken_as_no_unexplained_peaks(tmp_path):
    from dara import jobs

    phases = [RefinementPhase.make(Path(f"{NACL}.cif"))]
    fit = SimpleNamespace(
        lst_data=SimpleNamespace(phases_results={NACL: 1}), refinement_metrics=None, get_phase_weights=lambda: {NACL: 1.0}
    )
    result = _slot_result(phases)
    result.refinement_result = fit
    calls = []

    def refit(trial_phases, directory):
        calls.append([phase.path.stem for phase in trial_phases])
        return SimpleNamespace(lst_data=SimpleNamespace(phases_results={}), refinement_metrics=None)

    best_dir = tmp_path / "best"
    best_dir.mkdir()
    removed = {}
    jobs._check_listed_phases(
        result, best_dir, [kp.Group("KCl", (KCL,))], {}, {KCL: RefinementPhase.make(Path(f"{KCL}.cif"))}, removed,
        prepare=lambda r: [phase[0] for phase in r.phases], refit=refit, count=lambda refinement: 0,
        out_of_time=lambda: False,
    )
    assert calls == [[NACL, KCL]] and removed[KCL][0] == "no_peak_evidence"


def test_progress_advances_for_the_formulas_that_are_skipped(monkeypatch, tmp_path):
    from dara import jobs, task_progress

    steps = []
    monkeypatch.setattr(task_progress, "report_progress", lambda stage, done=None, total=None: steps.append((done, total)))
    fit = SimpleNamespace(
        lst_data=SimpleNamespace(phases_results={NACL: 1}), refinement_metrics=SimpleNamespace(missing_peaks=None),
        get_phase_weights=lambda: {NACL: 1.0},
    )
    result = _slot_result([RefinementPhase.make(Path(f"{NACL}.cif"))])
    result.refinement_result = fit
    removed = {}
    jobs._check_listed_phases(
        result, tmp_path, [kp.Group("KCl", (KCL,)), kp.Group("RbCl", (RBCL,))], {}, {}, removed,
        prepare=lambda r: [], refit=lambda *args: pytest.fail("no refit expected"), count=lambda refinement: 0,
        out_of_time=lambda: False,
    )
    assert steps == [(0, 2), (1, 2), (2, 2)] and set(removed) == {KCL, RBCL}


def test_the_time_limit_stops_the_trials_and_leaves_the_rest_not_checked(monkeypatch, tmp_path):
    state = {"t": 1000.0}

    def slow():  # the second fit ends after the deadline (budget 600 s)
        state["t"] += 400.0

    table = {
        frozenset({NACL}): (3, {NACL: 1.0}),
        frozenset({NACL, KCL}): (3, {NACL: 0.7, KCL: 0.3}),
    }
    document, report, tried = _known_job(
        monkeypatch, tmp_path, table, runtime=600, clock=lambda: state["t"], refinement_hook=slow
    )
    assert tried == [frozenset({NACL}), frozenset({NACL, KCL})]
    assert report["KCl"]["reason"] == "no_peak_evidence"
    rb = report["RbCl"]
    assert (rb["status"], rb["reason"], rb["removed_weight_percent"]) == ("not_detected", "not_checked", None)
    assert kp.not_detected_text(rb) == "RbCl: not checked (the time limit was reached)"
    assert report["LiCl"]["status"] == "no_structure"
    # leave-one-out stays too: a minor phase is kept when the time is already up
    state["t"] = 1000.0
    table = {frozenset({NACL, KCL}): (3, {NACL: 0.97, KCL: 0.03})}
    state_slow = lambda: state.__setitem__("t", state["t"] + 700.0)  # noqa: E731
    _, report, tried = _known_job(
        monkeypatch, tmp_path, table, picked=((NACL, KCL),), runtime=600, clock=lambda: state["t"],
        refinement_hook=state_slow,
    )
    assert tried == [frozenset({NACL, KCL})]
    assert report["KCl"]["status"] == "refined" and report["RbCl"]["reason"] == "not_checked"


def test_the_checked_candidate_stays_the_best_one_whatever_its_rwp(monkeypatch, tmp_path):
    table = {
        frozenset({NACL, KCL}): (3, {NACL: 0.97, KCL: 0.03}),  # Rwp 4.8: the best candidate
        frozenset({NACL}): (3, {NACL: 1.0}),  # KCl dropped: Rwp 4.9, worse than the second candidate
        frozenset({RBCL}): (3, {RBCL: 1.0}),  # Rwp 4.85
        frozenset({NACL, RBCL}): (3, {NACL: 0.9, RBCL: 0.1}),
    }
    rwp = lambda names: 4.85 if names == {RBCL} else 5.0 - 0.1 * len(names)  # noqa: E731
    document, report, _ = _known_job(monkeypatch, tmp_path, table, picked=((NACL, KCL), (RBCL,)), rwp=rwp)
    assert [[cifs[0].filename for cifs in result[0]] for result in document.results] == [[NACL], [RBCL]]
    assert report["KCl"]["reason"] == "removed_no_peak_evidence" and report["NaCl"]["status"] == "refined"
    assert [round(result[1].lst_data.rwp, 2) for result in document.results] == [4.9, 4.85]
    assert sorted(path.name for path in tmp_path.glob("*_rwp_*") if path.name[0] in "0f") == [
        "0_best_rwp_4.9", "final_candidate_2_rwp_4.85",
    ]


def test_a_candidate_pruned_to_a_phase_set_that_is_already_refined_is_not_refined_again(monkeypatch, tmp_path):
    table = {
        frozenset({NACL, KCL}): (2, {NACL: 0.8, KCL: 0.2}),
        frozenset({NACL, KCL, RBCL}): (2, {NACL: 0.797, KCL: 0.2, RBCL: 0.003}),  # RbCl is negligible
    }
    rwp = lambda names: 4.8  # noqa: E731
    document, report, tried = _known_job(
        monkeypatch, tmp_path, table, picked=((NACL, KCL), (NACL, KCL, RBCL)), rwp=rwp
    )
    # {NaCl, KCl} is refined once: the prune refit of candidate 2 would reproduce candidate 1
    # (the last refinement is the trial of RbCl on the best fit)
    assert tried == [frozenset({NACL, KCL}), frozenset({NACL, KCL, RBCL}), frozenset({NACL, KCL, RBCL})]
    assert [sorted(cifs[0].filename for cifs in result[0]) for result in document.results] == [sorted([NACL, KCL])] * 2
    assert document.search_summary["pruned_phases"] == [{"candidate": 2, "phase": RBCL, "reason": "negligible"}]
    assert sorted(path.name for path in tmp_path.glob("*_rwp_*") if path.name[0] in "0f") == [
        "0_best_rwp_4.8", "final_candidate_2_rwp_4.8",
    ]
    assert all((path / "phase_params.json").exists() for path in tmp_path.glob("*_rwp_*") if path.name[0] in "0f")  # files came along
    assert not list(tmp_path.glob("_final_candidate_*"))


def _pruning_case(tmp_path, monkeypatch, finished_phases):
    from dara import jobs

    weights = {NACL: 0.797, KCL: 0.2, RBCL: 0.003}
    refined = SimpleNamespace(
        lst_data=SimpleNamespace(rwp=4.7, phases_results={n: SimpleNamespace(gewicht=1.0, a=5.0, spacegroup_no=225) for n in weights}),
        get_phase_weights=lambda: weights,
    )
    earlier = SimpleNamespace(lst_data=SimpleNamespace(rwp=4.7))
    earlier_dir, working_dir = tmp_path / "first", tmp_path / "second"
    earlier_dir.mkdir()
    (earlier_dir / "marker.txt").write_text("first")
    working_dir.mkdir()
    refits = []

    def do_refinement(phases, working_dir, **kwargs):
        refits.append([phase.path.stem for phase in phases])
        return SimpleNamespace(lst_data=SimpleNamespace(rwp=4.7))

    monkeypatch.setattr(jobs, "do_refinement", do_refinement)
    phase = lambda stem, **params: RefinementPhase(path=Path(f"{stem}.cif"), params=params)  # noqa: E731
    phases = [phase(NACL), phase(KCL), phase(RBCL)]
    finished = {jobs._phases_key(finished_phases(phase)): (earlier, earlier_dir)}
    out = jobs._prune_final_fit(refined, phases, tmp_path / "p.xy", working_dir, {}, {}, set(), finished)
    return out, earlier, refits, working_dir


def test_a_pruned_phase_set_is_reused_only_for_the_same_phases_and_parameters(tmp_path, monkeypatch):
    (tmp_path / "same").mkdir()
    (fit, phases, dropped), earlier, refits, working_dir = _pruning_case(
        tmp_path / "same", monkeypatch, lambda phase: [phase(NACL), phase(KCL)]
    )
    assert fit is earlier and refits == [] and dropped == {RBCL: "negligible"}
    assert [p.path.stem for p in phases] == [NACL, KCL] and (working_dir / "marker.txt").read_text() == "first"
    for i, other in enumerate((lambda phase: [phase(NACL), phase(KCL, k1=1.0)], lambda phase: [phase(KCL), phase(NACL)])):
        tmp = tmp_path / f"other{i}"
        tmp.mkdir()
        (fit, phases, dropped), earlier, refits, working_dir = _pruning_case(tmp, monkeypatch, other)
        assert refits == [[NACL, KCL]] and fit is not earlier  # other parameters or order: refined as before


def test_the_formula_of_a_user_cif_comes_from_its_structure_not_its_file_name():
    from test_runtime_budget import CIF

    atoms = [f"Zr{i} Zr {x} {y} {z} 1" for i, (x, y, z) in enumerate([(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)])]
    atoms += [f"O{i} O {x} {y} {z} 1" for i, (x, y, z) in enumerate(
        (a, b, c) for a in (.25, .75) for b in (.25, .75) for c in (.25, .75)
    )]
    cif = Cif.from_str(CIF.format(name="ZrO2", a=5.1, atoms="\n".join(atoms)))
    cif.filename = "In_house_ZrO2"
    assert kp.formula_of_cif(cif) == "ZrO2"
    assert kp.formulas_of_cifs([cif]) == ["ZrO2"]
    cif.filename = "ZrO2_14_(cod_1010912)-0"  # a database name keeps its own spelling
    assert kp.formula_of_cif(cif) == "ZrO2"
    unreadable = Cif.from_str("data_x\n")
    unreadable.filename = "In_house_ZrO2"
    assert kp.formula_of_cif(unreadable) == "In_house_ZrO2"  # only an unreadable CIF falls back to the file name


def test_job_with_a_fixed_phase_set_uses_exactly_those_structures_and_no_database(monkeypatch, tmp_path):
    from test_runtime_budget import FakeResult, run_job, salt

    cifs = []
    for stem, cation, a in [("NaCl_225_(cod_5)-0", "Na", 5.64), ("KCl_225_(cod_6)-0", "K", 6.29)]:
        cif = Cif.from_str(salt(cation, a))
        cif.filename = stem
        cifs.append(cif)
    seen = {}

    def search(by_stem):
        seen["searched"] = sorted(by_stem)
        return [FakeResult([by_stem["NaCl_225_(cod_5)-0"]])]

    # The database (which would also offer RbCl) is not consulted at all.
    document, _ = run_job(
        monkeypatch, tmp_path, write_formulas=True, search_returns=search,
        make_kwargs={"analysis_mode": "known_mixture", "precursors": ["NaCl", "KCl"], "fixed_phase_cifs": cifs},
    )
    assert seen["searched"] == ["KCl_225_(cod_6)-0", "NaCl_225_(cod_5)-0"]
    known = document.search_summary["known_phases"]
    assert [entry["formula"] for entry in known] == ["NaCl", "KCl"]  # exact structures, one outcome each
    assert all(entry["status"] != "no_structure" for entry in known)


# ---- verdict and rerun ---------------------------------------------------------------------------
def test_no_structure_is_always_among_the_verdict_reasons():
    from dara.server.confidence import _overall

    weak = {"level": "weak", "evidence": {}}
    phases = {f"Phase{n}_1_(cod_{n})-0": weak for n in range(5)}  # four ahead of it, in order
    overall = _overall(
        SimpleNamespace(gof=None, rwp=None, phase_details={}), phases, {name: 1.0 for name in phases},
        {"level": "ok"}, {}, counts_like=None, missing_peaks=0,
        search_summary={"known_phases": [{"formula": "La(OH)3", "status": "no_structure"}]},
    )
    assert overall["level"] == "check" and len(overall["reasons"]) == 4
    assert overall["reasons"][0] == "La(OH)3: no reference structure found (enable Materials Project or add a CIF)."


def test_a_rerun_that_keeps_the_phase_set_warns_when_the_formulas_differ():
    from dara.server.api_router import _phase_set_formula_warnings

    from test_runtime_budget import salt

    stored = []
    for stem, cation in ((NACL, "Na"), (KCL, "K")):
        cif = Cif.from_str(salt(cation, 5.0))
        cif.filename = stem
        stored.append(cif)
    assert _phase_set_formula_warnings('["KCl", "NaCl"]', stored, 7) == []  # same set, any order
    assert _phase_set_formula_warnings("[]", stored, 7) == []  # nothing entered
    warning, = _phase_set_formula_warnings('["ZnO"]', stored, 7)
    assert warning["code"] == "phase_set_formulas_ignored" and "stored structures of Task 7 are used" in warning["message"]


def test_the_rerun_form_gets_the_stored_phase_set_apart_from_the_submission_settings(pattern):
    from test_runtime_budget import salt

    cif = Cif.from_str(salt("Na", 5.64))
    cif.filename = NACL
    kwargs = json.loads(json.dumps({"fixed_phase_cifs": [cif]}, cls=MontyEncoder))
    record = {"index": 3, "user": "u", "job": {"metadata": {"submission_settings": {"phase_set_from": 2}}, "name": "x"}}
    rerun = api_router._rerun_settings(record, kwargs, pattern)
    assert [(item["name"], item["formula"]) for item in rerun["phase_set"]] == [(NACL, "NaCl")]
    assert rerun["settings"]["phase_set_from"] == 2 and "phase_set" not in rerun["settings"]  # settings feed the retry job
    assert api_router._rerun_settings(record, {}, pattern)["phase_set"] == []
