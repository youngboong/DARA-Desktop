"""Atomic-position blocks of a ``.lst`` with refined coordinates (design WP1, step 1).

The fixture is the lst of experiment V3 of the round-2026-11 design (YFeO3 + Y2O3 + Fe2O3,
coordinates, B and a Y/Fe exchange occupancy refined), copied verbatim.
"""

import pytest

from dara.result import lst_phase_sections_by_name, parse_atom_positions, parse_lst

V3_LST = """Rietveld refinement to file(s) pattern.xy
BGMN version 4.2.22, 8301 measured points, 336 peaks, 89 parameters
Start: Sun Nov  8 16:34:44 2026; End: Sun Nov  8 16:43:52 2026
169 iteration steps

Rp=2.01%  Rpb=31.23%  R=2.56%  Rwp=2.51% Rexp=2.06%
Durbin-Watson d=1.52
1-rho=2.91%

Global parameters and GOALs
****************************
QYFeO362cod2101386None=0.2978+-0.0033
QY2O3206cod15133000=0.3166+-0.0045
QFe2O3167cod21080270=0.3856+-0.0063
EPS2=-0.001959+-0.000013

Local parameters and GOALs for phase YFeO362cod2101386None
******************************************************
SpacegroupNo=62
HermannMauguin=P2_1/n2_1/m2_1/a
XrayDensity=5.694
Rphase=3.05%
UNIT=NM
A=0.559797+-0.000048
B=0.760273+-0.000053
C=0.528277+-0.000046
k1=1.00000
k2=0
B1=0.006007+-0.000084
GrainSize(1,1,1)=52.99+-0.74
yfBc=0
yfBa=0
pYF=0
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.0298445
Atomic positions for phase YFeO362cod2101386None
---------------------------------------------
  4     0.5657  0.2500  0.5094     E=(Y(1.0000),FE(0.0000))
x=0.56570+-0.00071
z=0.5094+-0.0021

  4     0.0000  0.0000  0.5000     E=(FE(1.0000),Y(0.0000))
  4     0.9742  0.2500  0.3845     E=(O(1.0000))
x=0.9742+-0.0054
z=0.3845+-0.0058

  8     0.3197  0.4390  0.6808     E=(O(1.0000))
x=0.3197+-0.0047
y=0.4390+-0.0028
z=0.6808+-0.0053


Local parameters and GOALs for phase Y2O3206cod15133000
******************************************************
SpacegroupNo=206
HermannMauguin=I2_1/a-3
XrayDensity=5.025
Rphase=3.24%
UNIT=NM
A=1.060864+-0.000042
k1=1.00000
k2=0
B1=0.008134+-0.000071
GrainSize(1,1,1)=39.13+-0.34
yoBc=0
yoBa=0
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.0317215
Atomic positions for phase Y2O3206cod15133000
---------------------------------------------
 24     0.4702  0.0000  0.2500     E=(Y(1.0000))
x=0.47020+-0.00028

  8     0.2500  0.2500  0.2500     E=(Y(1.0000))
 48     0.3521  0.6211  0.8875     E=(O(1.0000))
x=0.3521+-0.0017
y=0.6211+-0.0028
z=0.8875+-0.0020


Local parameters and GOALs for phase Fe2O3167cod21080270
******************************************************
SpacegroupNo=15
HermannMauguin=C12/c1
XrayDensity=5.262
Rphase=2.74%
UNIT=NM
A=0.87239+-0.00020
B=0.503934+-0.000034
C=0.54294+-0.00010
BETA=122.387+-0.017
k1=1.00000
k2=0
B1=0.00616+-0.00018
GrainSize(1,1,1)=51.7+-1.5
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.0386420
Atomic positions for phase Fe2O3167cod21080270
---------------------------------------------
  8     0.6053  0.7500  0.0658     E=(FE(1.0000))
  4     0.0000  0.4410  0.2500     E=(O(1.0000))
  8     0.3510  0.5940  0.7520     E=(O(1.0000))
"""

NAMES = ["YFeO3_62_(cod_2101386)-None", "Y2O3_206_(cod_1513300)-0", "Fe2O3_167_(cod_2108027)-0"]


def _parse(tmp_path, text):
    path = tmp_path / "pattern.lst"
    path.write_text(text)
    return parse_lst(path, NAMES)


@pytest.fixture()
def lst(tmp_path):
    return _parse(tmp_path, V3_LST)


def test_all_four_yfeo3_positions_are_kept(lst):
    phase = lst.phases_results[NAMES[0]]
    assert len(phase.atom_positions) == 4
    assert [p["multiplicity"] for p in phase.atom_positions] == [4, 4, 4, 8]
    assert len(phase.atom_positions_string.splitlines()) == 4  # position lines only


def test_refined_lines_belong_to_their_position(lst):
    positions = lst.phases_results[NAMES[0]].atom_positions
    assert positions[0]["refined"]["x"] == [0.5657, 0.00071]
    assert positions[0]["refined"]["z"] == [0.5094, 0.0021]
    assert positions[1]["refined"] == {}
    assert positions[3]["refined"]["y"] == [0.439, 0.0028]
    assert positions[0]["species"] == {"Y": 1.0, "Fe": 0.0}


def test_refined_lines_do_not_leak_into_phase_values(lst):
    phase = lst.phases_results[NAMES[0]]
    assert not {"x", "y", "z"} & set(phase.model_extra)
    assert phase.model_extra["pYF"] == 0
    # phases without refined coordinates are unchanged
    assert lst.phases_results[NAMES[2]].atom_positions[0]["refined"] == {}


def test_get_structure_with_refined_positions(lst):
    structure = lst.phases_results[NAMES[0]].get_structure()
    assert structure.composition.reduced_formula == "YFeO3"
    assert round(structure.composition["Y"]) == 4
    assert round(structure.composition["O"]) == 12


def test_get_structure_from_the_position_string_only(lst):
    # results stored before atom_positions existed carry only the string
    phase = lst.phases_results[NAMES[1]].model_copy(update={"atom_positions": None})
    assert phase.get_structure().composition.reduced_formula == "Y2O3"


def test_get_structure_uses_lattice_fallback_when_the_lattice_is_not_printed(tmp_path):
    lines = [
        line
        for line in V3_LST.splitlines()
        if not line.startswith(("A=", "B=", "C=", "BETA="))
    ]
    result = _parse(tmp_path, "\n".join(lines) + "\n")
    phase = result.phases_results[NAMES[2]]
    assert phase.a is None
    with pytest.raises(TypeError):
        phase.get_structure()
    fallback = {"a": 8.7239, "b": 5.03934, "c": 5.4294, "beta": 122.387}
    structure = phase.get_structure(lattice_fallback=fallback)
    assert structure.lattice.a == pytest.approx(8.7239)
    assert structure.composition.reduced_formula == "Fe2O3"


def test_sections_run_to_the_next_phase_head():
    sections = lst_phase_sections_by_name(V3_LST)
    assert sections[0][0] == "YFeO362cod2101386None"
    assert "z=0.6808+-0.0053" in sections[0][1]
    assert "Local parameters" not in sections[0][1]
    assert sections[-1][1].endswith("E=(O(1.0000))")


def test_parse_atom_positions_ignores_blank_lines():
    block = "  4     0.5657  0.2500  0.5094     E=(Y(1.0000),FE(0.0000))\nx=0.324600\n\nz=0.5+-0.1\n"
    (position,) = parse_atom_positions(block)
    assert position["refined"] == {"x": [0.3246, None], "z": [0.5, 0.1]}
