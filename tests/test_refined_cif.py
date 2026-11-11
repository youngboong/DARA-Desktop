"""Refined CIF of one phase (design 2.5): space group, e.s.d. of the lattice, fixed lattices.

The listing is the lst of experiment V3 of the round-2026-11 design (YFeO3 + Y2O3 + Fe2O3,
coordinates refined), copied verbatim.
"""

from types import SimpleNamespace

import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifParser, CifWriter

from dara.export.refined_cif import CifNotExportable, format_esd, refined_cif_text
from dara.result import parse_lst

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
META = {"task_id": 33, "selection": "version:3", "rwp": 2.51, "rexp": 2.06, "wavelength": "Cu",
        "weight_percent": 14.13, "weight_esd_percent": 0.48}


def _refinement(tmp_path, text=V3_LST):
    path = tmp_path / "pattern.lst"
    path.write_text(text)
    lst = parse_lst(path, NAMES)
    return SimpleNamespace(
        lst_data=lst, export_structure=lambda name: lst.phases_results[name].get_structure()
    )


def _reread(text, tmp_path):
    path = tmp_path / "back.cif"
    path.write_text(text)
    return CifParser(str(path)).parse_structures(primitive=False)[0]


def _details(text):
    return text.split("_refine_special_details\n;\n")[1].split("\n;")[0]


def test_format_esd():
    assert format_esd(4.93412, 0.0002) == "4.9341(2)"
    assert format_esd(4.93412, 0.00014) == "4.93412(14)"
    assert format_esd(14.1287, 0.48, digits=2) == "14.13(48)"
    assert format_esd(4.9341, None) == "4.93410"
    assert format_esd(120.0, 30.0) == "120(30)"


def test_hematite_is_written_in_the_database_setting(tmp_path):
    refinement = _refinement(tmp_path)
    assert refinement.lst_data.phases_results[NAMES[2]].spacegroup_no == 15
    text, folder = refined_cif_text(NAMES[2], refinement, meta=META)
    assert folder == "refined_cifs"
    assert "_symmetry_space_group_name_H-M   R-3c" in text
    assert "_database_code_COD   2108027" in text
    assert "written in R-3c (database setting)" in _details(text)
    structure = _reread(text, tmp_path)
    assert structure.get_space_group_info(symprec=0.01)[1] == 167
    assert structure.composition.reduced_formula == "Fe2O3"


def test_yfeo3_keeps_pnma_and_gets_esd(tmp_path):
    refinement = _refinement(tmp_path)
    text, folder = refined_cif_text(NAMES[0], refinement, meta=META)
    assert folder == "refined_cifs"
    assert "_symmetry_space_group_name_H-M   Pnma" in text
    assert "_cell_length_a   5.5980(5)" in text and "_cell_length_b   7.6027(5)" in text
    assert "_pd_phase_mass_%   14.13(48)" in text
    assert "_pd_proc_ls_prof_wR_factor   0.0251" in text
    assert "_diffrn_radiation_wavelength   1.54060" in text
    assert "_atom_site_B_iso_or_equiv" in text and "  1.000" in text
    details = _details(text)
    assert "task 33, version 3, Rwp 2.51 %" in details
    assert "Refined: lattice parameters a, b, c; scale; peak broadening B1; global sample" in details
    assert "atomic coordinates" in details.split("Held at reference values:")[0]
    assert "B(iso) = 1.0 A^2 for all sites" in details
    structure = _reread(text, tmp_path)
    source = refinement.lst_data.phases_results[NAMES[0]].get_structure()
    assert structure.get_space_group_info(symprec=0.01)[1] == 62
    for got, want in zip(structure.lattice.abc, source.lattice.abc):
        assert got == pytest.approx(want, abs=1e-4)


def test_refined_coordinates_carry_their_esd(tmp_path):
    text, _ = refined_cif_text(NAMES[0], _refinement(tmp_path), meta=META)
    rows = text.split("_atom_site_B_iso_or_equiv\n")[1].split("_refine_special_details")[0]
    assert "(" in rows  # e.g. the O(8d) row: x, y, z all refined
    assert any(row.split()[0] == "Fe" and "(" not in row for row in rows.splitlines() if row.strip())


def test_fixed_lattice_phase_uses_reference_lattice(tmp_path):
    lines = [
        line for line in V3_LST.splitlines()
        if not line.startswith(("A=0.87239", "B=0.503934", "C=0.54294", "BETA="))
    ]
    refinement = _refinement(tmp_path, "\n".join(lines) + "\n")
    assert refinement.lst_data.phases_results[NAMES[2]].a is None
    source = _refinement(tmp_path).lst_data.phases_results[NAMES[2]].get_structure()
    shifted = Structure(
        Lattice.from_parameters(8.73, 5.04, 5.43, 90, 122.4, 90), source.species, source.frac_coords
    )
    reference = SimpleNamespace(
        filename=NAMES[2], orig_string=str(CifWriter(shifted, symprec=0.01)),
        to_structure=lambda: shifted,
    )
    text, folder = refined_cif_text(
        NAMES[2], refinement, meta={**META, "selection": "best"}, reference_cif=reference
    )
    assert folder == "refined_cifs"
    assert "Held at reference values: lattice parameters" in _details(text)
    assert "(" not in text.split("_cell_length_a")[1].split("\n")[0]
    back = _reread(text, tmp_path)  # R-3c cell: same volume per atom as the reference C2/c cell
    assert back.volume / len(back) == pytest.approx(shifted.volume / len(shifted), rel=1e-3)


def test_unwritable_phase_falls_back_to_the_reference_cif(tmp_path):
    refinement = _refinement(tmp_path)
    refinement.lst_data.phases_results[NAMES[1]] = None
    refinement.export_structure = lambda name: (_ for _ in ()).throw(ValueError("no structure"))
    reference = SimpleNamespace(filename=NAMES[1], orig_string="data_ref\n")
    text, folder = refined_cif_text(NAMES[1], refinement, meta=META, reference_cif=reference)
    assert folder == "reference_cifs"
    assert text.startswith("# NOT REFINED by DARA: ")
    assert "# Task 33, version:3" in text and text.endswith("data_ref\n")
    with pytest.raises(CifNotExportable):
        refined_cif_text(NAMES[1], refinement, meta=META)
