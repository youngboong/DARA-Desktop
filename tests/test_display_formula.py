from types import SimpleNamespace

from dara.server import display_formula as df

ADP_CIF = """data_9007583
_chemical_formula_structural   NH4H2PO4
_chemical_formula_sum   'H6 N O4 P'
_chemical_name_mineral   Biphosphammite
_space_group_IT_number   19
"""


def _cif(name, fields):
    return SimpleNamespace(filename=name, data={"block": SimpleNamespace(data=fields)})


def test_structural_formula_replaces_the_reduced_stem_formula():
    name = "PH6NO4_19_(cod_9007583)-3"
    assert df.formula_of_name(name) == "PH6NO4"
    assert df.text_display_formulas([{"filename": name, "cif_text": ADP_CIF}]) == {name: "NH4H2PO4"}
    cif = _cif(name, {"_chemical_formula_structural": "N H4 H2 P O4"})
    assert df.cif_display_formulas([cif]) == {name: "NH4H2PO4"}


def test_formula_is_normalized_for_display():
    assert df.display_formula({"_chemical_formula_structural": "La (Mn O3)"}, "LaMnO3") is None
    assert df.display_formula({"_chemical_formula_structural": "La2 O2 (C O3)"}, "La2CO5") == "La2O2CO3"
    assert df.display_formula({"_chemical_formula_structural": "Ca (O H)2"}, "Ca(HO)2") == "Ca(OH)2"
    assert df.display_formula({"_chemical_formula_structural": "H.5 Ti O2"}, "H1Ti2O4") == "H0.5TiO2"


def test_only_a_formula_with_the_same_composition_is_used():
    # a mineral or common name is not a formula; the formula of the name is kept
    assert df.display_formula({"_chemical_name_mineral": "Quartz"}, "SiO2") is None
    assert df.display_formula({"_chemical_name_common": "ammonium dihydrogen phosphate"}, "PH6NO4") is None
    # a common name that is a formula is accepted
    assert df.display_formula({"_chemical_name_common": "NH4H2PO4"}, "PH6NO4") == "NH4H2PO4"
    # a structural formula of another composition never relabels the phase
    assert df.display_formula({"_chemical_formula_structural": "NH4PO3"}, "PH6NO4") is None
    for bad in ("?", ".", "", None, ["NH4H2PO4"], "N" * 60):
        assert df.display_formula({"_chemical_formula_structural": bad}, "PH6NO4") is None
    # nothing to change: no entry
    assert df.cif_display_formulas([_cif("Fe2O3_167_(cod_1)-0", {"_chemical_formula_structural": "Fe2 O3"})]) == {}
    assert df.cif_display_formulas([_cif("", {}), SimpleNamespace(filename="x", data=None)]) == {}


def test_display_formulas_do_not_change_phase_identity():
    from dara.server.confidence import phase_identity

    name = "PH6NO4_19_(cod_9007583)-3"
    assert phase_identity(name) == phase_identity("PH6NO4_19_(cod_1234567)-0")
    assert df.text_display_formulas([{"filename": name, "cif_text": ADP_CIF}, {"filename": None}, "x"]) == {
        name: "NH4H2PO4"
    }


ADP = "PH6NO4_19_(cod_9007583)-3"


def test_phase_display_name_uses_the_scoped_formula():
    from dara.plot import display_formula_of, display_formulas_scope, phase_display_name
    from dara.server.fit_cautions import _display_name
    from dara.server.report.builder import _name_html, _plain_name
    from dara.server.table_export import _phase_label

    assert _display_name(ADP).startswith("PH6NO4 · ")
    with display_formulas_scope({ADP: "NH4H2PO4"}):
        assert phase_display_name(ADP).startswith("NH<sub>4</sub>H<sub>2</sub>PO<sub>4</sub> · ")
        # another variant of the same record, and every server text helper
        assert display_formula_of("PH6NO4_19_(cod_9007583)-0") == "NH4H2PO4"
        assert _display_name(ADP).startswith("NH4H2PO4 · ")
        assert _phase_label(ADP).startswith("NH4H2PO4")
        assert _plain_name(ADP, "P2_12_12_1") == "NH4H2PO4"
        assert _name_html(ADP) == "NH<sub>4</sub>H<sub>2</sub>PO<sub>4</sub>"
        assert _display_name("Na4P2O7_19_(cod_1522370)-0").startswith("Na4P2O7 · ")
    # the scope ends with the block; identities never change
    assert _display_name(ADP).startswith("PH6NO4 · ")
    assert display_formula_of(ADP) is None


def test_summarize_document_scopes_the_documents_formulas(monkeypatch):
    from dara.plot import display_formula_of
    from dara.server import api_router

    seen = {}
    monkeypatch.setattr(api_router, "document_display_formulas", lambda document: {ADP: "NH4H2PO4"})
    monkeypatch.setattr(
        api_router, "_summarize_document",
        lambda job, document, persist_summary=True: seen.setdefault("formula", display_formula_of(ADP)),
    )
    assert api_router.summarize_document({}, object(), persist_summary=False) == "NH4H2PO4"
    assert display_formula_of(ADP) is None


def test_stored_texts_are_relabeled_but_names_and_identities_kept():
    stored = {
        "overall": {"reasons": ["Weak evidence for PH6NO4 · P212121 (10.1 wt%).", "PH6NO4."]},
        "phases": {ADP: {"phase": ADP, "formula": "PH6NO4", "text": "Selected reference: " + ADP}},
        "cautions": [{"message": "PH6NO4 · P212121: lattice parameter b ended near its limit"}],
        "other": "XPH6NO4 PH6NO4x",
    }
    out = df.relabel_stored_text(stored, {ADP: "NH4H2PO4"})
    assert out["overall"]["reasons"] == ["Weak evidence for NH4H2PO4 · P212121 (10.1 wt%).", "NH4H2PO4."]
    assert out["phases"] == {ADP: {"phase": ADP, "formula": "PH6NO4", "text": "Selected reference: " + ADP}}
    assert out["cautions"][0]["message"].startswith("NH4H2PO4 · P212121:")
    assert out["other"] == "XPH6NO4 PH6NO4x"
    assert stored["overall"]["reasons"][0].startswith("Weak evidence for PH6NO4")
    assert df.relabel_stored_text(stored, {}) is stored


def test_unwrapped_group_merges_adjacent_same_element():
    assert df._clean("Fe2 (Ti O3) O2") == "Fe2TiO5"
    assert df.display_formula({"_chemical_formula_structural": "Fe2 (Ti O3) O2"}, "Fe4Ti2O10") == "Fe2TiO5"
    assert df._clean("H.5 Ti O2") == "H0.5TiO2"  # nothing to merge: unchanged
