"""Refinement status, size/strain and structure results (design WP1, step 2)."""

from types import SimpleNamespace

import pytest

from dara.cif_names import cif_stem, strip_none
from dara.refinement_status import (
    lattice_states,
    phase_status,
    size_strain,
    structure_results,
    undefined_keys,
)
from dara.result import parse_atom_positions, parse_lst_section

# task 34 (v3): lattice held fixed, B1 refined to 0, k1 and GrainSize undefined
NA1267 = """******************************************************
SpacegroupNo=165
HermannMauguin=P-32/c1
XrayDensity=1.552
Rphase=23.86%
UNIT=NM
k1=UNDEF
k2=0.000289+-0.000034
B1=0
GrainSize(1,1,1)=ERROR
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.269123
Atomic positions for phase Na1267P4O6467165cod90075270
---------------------------------------------
 12     0.3455  0.9801  0.0259     E=(NA(1.0000))
  2     0.0000  0.0000  0.0000     E=(NA(0.3350),O(0.3350))
  4     0.3333  0.6667  0.7161     E=(P(1.0000))"""

PH6NO4 = """******************************************************
SpacegroupNo=19
HermannMauguin=P2_12_12_1
XrayDensity=1.718
Rphase=15.25%
UNIT=NM
A=0.75578+-0.00047
B=0.764980
C=0.76914+-0.00017
k1=1.00000
k2=0.0000030+-0.0000024
B1=0.00243+-0.00077
GrainSize(1,1,1)=131+-41
GEWICHT=SPHAR2, MeanValue(GEWICHT)=0.0936961
Atomic positions for phase PH6NO419cod90075833
---------------------------------------------
  4     0.0000  0.0000  0.0000     E=(P(1.0000))"""

NA4P2O7 = """******************************************************
SpacegroupNo=19
Rphase=15.19%
UNIT=NM
A=0.54002+-0.00014
B=0.93606+-0.00016
C=1.34989+-0.00015
k1=0
k2=0.00000222+-0.00000070
B1=0.00554+-0.00041
GrainSize(1,1,1)=76.6+-5.7
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.565631
Atomic positions for phase Na4P2O719cod15223700
---------------------------------------------
  4     0.7652  0.4919  0.4860     E=(NA(1.0000))"""

# task 33: k2 and k1 on their limits
Y2O3 = """******************************************************
SpacegroupNo=206
Rphase=3.24%
A=1.060864+-0.000042
k1=1.00000
k2=0
B1=0.008134+-0.000071
GrainSize(1,1,1)=39.13+-0.34
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.0317215
Atomic positions for phase Y2O3206cod15133000
---------------------------------------------
 24     0.4702  0.0000  0.2500     E=(Y(1.0000))"""

# the task 34 bundle re-run: k2 on its upper limit
PH6NO4_BUNDLE = """******************************************************
SpacegroupNo=19
Rphase=37.39%
A=0.770900
B=0.780280
C=0.784520
k1=1.00000
k2=0.00100000
B1=0.0148+-0.0029
GrainSize(1,1,1)=21.5+-4.2
GEWICHT=SPHAR2, MeanValue(GEWICHT)=0.0356144
Atomic positions for phase PH6NO419cod90075833
---------------------------------------------
  4     0.0000  0.5000  0.5000     E=(P(1.0000))"""

GEWICHT_LINE = "GEWICHT=SPHAR2, MeanValue(GEWICHT)=0.0936961"


def test_undefined_keys_keep_undef_and_error_apart():
    assert undefined_keys(NA1267) == {"k1": "UNDEF", "GrainSize(1,1,1)": "ERROR"}
    assert undefined_keys(PH6NO4) == {}


def test_fixed_lattice_with_zero_size_broadening_is_limited_not_failed():
    status = phase_status(NA1267, lattice_spec="fixed", overall_rwp=21.10)
    assert status["level"] == "limited"
    assert status["codes"] == ["lattice_fixed", "size_not_resolvable"]
    assert status["undefined"] == {"k1": "UNDEF", "GrainSize(1,1,1)": "ERROR"}
    assert status["message"] == (
        "Lattice held at the database values; size broadening refined to zero (k1 undefined)."
    )


def test_undefined_lattice_value_fails_the_phase():
    text = PH6NO4.replace("A=0.75578+-0.00047", "A=UNDEF")
    status = phase_status(text, lattice_spec="0.01")
    assert status["level"] == "failed"
    assert status["undefined"] == {"A": "UNDEF"}
    assert "A undefined" in status["message"]


def test_missing_block_or_gewicht_fails_the_phase():
    assert phase_status("")["level"] == "failed"
    assert phase_status(PH6NO4.replace(GEWICHT_LINE, ""))["level"] == "failed"
    assert phase_status(PH6NO4.replace(GEWICHT_LINE, "GEWICHT=UNDEF"))["level"] == "failed"


def test_lattice_value_without_esd_is_at_limit():
    assert lattice_states(PH6NO4, "0.01") == {"a": "refined", "b": "at_limit", "c": "refined"}
    assert lattice_states(PH6NO4, "fixed")["b"] == "fixed"
    assert lattice_states(NA1267, "fixed") == {}
    status = phase_status(PH6NO4, lattice_spec="0.01", overall_rwp=21.10)
    assert status["level"] == "limited"
    assert status["codes"] == ["lattice_at_limit"]


def test_clean_phase_is_ok():
    status = phase_status(NA4P2O7, lattice_spec="0.01", b1_spec="0.01_0^0.1", overall_rwp=21.10)
    assert (status["level"], status["codes"], status["message"]) == ("ok", [], "")


def test_profile_limit_and_high_phase_r_factor():
    status = phase_status(PH6NO4_BUNDLE, lattice_spec="fixed", k2_spec="0_0^0.001", overall_rwp=22.68)
    assert "profile_at_limit" in status["codes"]
    assert "high_rphase" in status["codes"]  # 37.39 > 1.5 * 22.68 and > 10
    ok = phase_status(NA4P2O7, lattice_spec="0.01", overall_rwp=21.10)
    assert "high_rphase" not in ok["codes"]


def test_refined_coordinate_at_its_limit_is_structure_at_limit():
    text = NA4P2O7 + "\nx=0.324600"
    assert "structure_at_limit" in phase_status(text, lattice_spec="0.01")["codes"]
    refined = text.replace("x=0.324600", "x=0.3246+-0.0010")
    assert "structure_at_limit" not in phase_status(refined, lattice_spec="0.01")["codes"]


def test_size_and_strain_of_a_separable_phase():
    parsed = parse_lst_section(NA4P2O7)
    result = size_strain(
        parsed, {}, b1_spec="0_0^0.1", k1_spec="1_0^1", k2_spec="0_0^0.001", size_status="refined"
    )
    assert result["size_nm"] == {"value": 76.6, "esd": 5.7}
    assert result["microstrain_percent"]["value"] == pytest.approx(0.149, abs=5e-4)
    # esd(k2) / (2 sqrt(k2)) = 7.0e-7 / (2 * 1.49e-3) = 0.0235 %
    assert result["microstrain_percent"]["esd"] == pytest.approx(0.0235, abs=1e-4)
    assert (result["strain_state"], result["separable"], result["note"]) == ("refined", True, None)
    assert result["raw"]["k2"] == [2.22e-06, 7e-07]
    assert result["raw"]["k1"] == [0.0, None]


def test_strain_not_detected_assigns_everything_to_size():
    parsed = parse_lst_section(Y2O3)
    result = size_strain(parsed, {}, k1_spec="1_0^1", k2_spec="0_0^0.001")
    assert result["strain_state"] == "not_detected"
    assert result["microstrain_percent"] == {"value": 0.0, "esd": None}
    assert result["separable"] is False
    assert result["note"] == (
        "No strain broadening detected; all excess broadening was assigned to crystallite size. "
        "Size assumes the upper limit of the size-distribution parameter (k1 = 1)."
    )


def test_strain_at_limit_and_poorly_determined():
    at_limit = size_strain(parse_lst_section(PH6NO4_BUNDLE), {}, k2_spec="0_0^0.001")
    assert at_limit["strain_state"] == "at_limit"
    assert at_limit["microstrain_percent"]["value"] is None
    poor = size_strain(parse_lst_section(PH6NO4), {}, k2_spec="0_0^0.001")
    assert poor["strain_state"] == "poorly_determined"  # esd/k2 = 0.8
    assert poor["note"].startswith("Size and strain cannot be separated")
    assert poor["microstrain_percent"]["value"] == pytest.approx(0.1732, abs=1e-3)


def test_size_unresolvable_with_refined_strain():
    undefined = {"k1": "UNDEF", "GrainSize(1,1,1)": "ERROR"}
    result = size_strain(
        parse_lst_section(NA1267), undefined, k2_spec="0_0^0.001", size_status="not_resolvable"
    )
    assert result["size_nm"] == {"value": None, "esd": None}
    assert result["microstrain_percent"]["value"] == pytest.approx(1.70, abs=0.01)
    assert result["microstrain_percent"]["esd"] == pytest.approx(0.10, abs=0.005)
    assert result["note"] == (
        "All excess broadening was assigned to microstrain; "
        "crystallite size is beyond the resolvable range."
    )
    assert result["raw"]["k1"] == [None, None]


def test_fixed_k2_is_not_refined_and_has_no_note():
    result = size_strain(parse_lst_section(NA4P2O7), {}, k2_spec="fixed")
    assert result["strain_state"] == "not_refined"
    assert result["note"] is None
    assert size_strain(parse_lst_section(NA4P2O7), {}, failed=True)["size_state"] == "failed"


BLOCK = """  4     0.5685  0.2500  0.5179     E=(Y(0.9495),FE(0.0505))
x=0.56570+-0.00071
z=0.5094+-0.0021

  4     0.0000  0.0000  0.5000     E=(FE(0.9495),Y(0.0505))
  4     0.9742  0.2500  0.3845     E=(O(1.0000))
x=0.324600
"""


def test_structure_results_for_an_exchange_model():
    phase = SimpleNamespace(atom_positions=parse_atom_positions(BLOCK))
    spec = {
        "positions": [0, 2],
        "thermal": "overall",
        "occupancy": [{"kind": "exchange", "sites": [0, 1], "elements": ["Y", "Fe"]}],
    }
    result = structure_results(phase, spec, {"occ0": (0.0505, 0.014), "Ball": (0.0, None)})
    assert result["indicative"] is True
    assert [site["label"] for site in result["sites"]] == ["Y1", "Fe1", "O1"]
    first = result["sites"][0]
    assert first["xyz"][0] == {"value": 0.5657, "esd": 0.00071, "state": "refined"}
    assert first["xyz"][1]["state"] == "fixed"
    assert first["species"][0]["occupancy"] == {"value": 0.9495, "esd": None}
    assert first["species"][1]["occupancy"] == {"value": 0.0505, "esd": 0.014}
    assert result["sites"][1]["species"][1]["occupancy"]["esd"] == 0.014  # equal multiplicity
    assert result["sites"][2]["xyz"][0] == {"value": 0.3246, "esd": None, "state": "at_limit"}
    assert first["b_iso"] == {"value": 0.0, "esd": None, "state": "at_limit"}
    assert {p["name"]: p["state"] for p in result["parameters"]} == {
        "occ0": "refined",
        "Ball": "at_limit",
    }
    assert result["parameters"][0]["label"] == "Y/Fe exchange (Y1-Fe1)"
    assert structure_results(phase, None, {}) is None


def test_structure_results_fixed_b_and_vacancy():
    phase = SimpleNamespace(atom_positions=parse_atom_positions(BLOCK))
    spec = {"occupancy": [{"kind": "vacancy", "site": 2, "element": "O"}]}
    result = structure_results(phase, spec, {"occ0": [0.964, 0.029]})
    assert result["sites"][0]["b_iso"] == {"value": 1.0, "esd": None, "state": "fixed"}
    assert result["sites"][2]["species"][0]["occupancy"]["esd"] == 0.029


def test_cif_stem_keeps_dots_inside_phase_names():
    assert cif_stem("Na12.67P4O64.67_165_(cod_9007527)-0") == "Na12.67P4O64.67_165_(cod_9007527)-0"
    assert cif_stem("abc.cif") == "abc"
    assert cif_stem(SimpleNamespace(filename="Fe2O3_167_(cod_2108027)-0.cif")) == "Fe2O3_167_(cod_2108027)-0"


def test_strip_none():
    assert strip_none("YFeO3_62_(cod_2101386)-None") == "YFeO3_62_(cod_2101386)"
    assert strip_none("YFeO3_62_(cod_2101386)-3") == "YFeO3_62_(cod_2101386)-3"
