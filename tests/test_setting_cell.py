import numpy as np

from pymatgen.core import Lattice, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from dara.setting_cell import setting_transform, transform_cell, transform_hkl


def _hematite():
    return Structure.from_spacegroup(
        "R-3c", Lattice.hexagonal(5.0356, 13.7489), ["Fe", "O"], [[0, 0, 0.35530], [0.3059, 0, 0.25]]
    )


def _as_lattice(structure, esd=None):
    keys = ("a", "b", "c", "alpha", "beta", "gamma")
    values = (*structure.lattice.abc, *structure.lattice.angles)
    return {key: {"value": value, "esd": (esd or {}).get(key)} for key, value in zip(keys, values)}


def test_rhombohedral_cell_maps_back_to_the_hexagonal_setting():
    primitive = SpacegroupAnalyzer(_hematite()).get_primitive_standard_structure()
    transform = setting_transform(primitive, 167)
    assert transform is not None
    cell = transform_cell(_as_lattice(primitive), transform)
    assert abs(cell["a"]["value"] - 5.0356) < 1e-3 and abs(cell["b"]["value"] - 5.0356) < 1e-3
    assert abs(cell["c"]["value"] - 13.7489) < 1e-3
    assert abs(cell["gamma"]["value"] - 120) < 1e-3
    assert cell["a"]["esd"] is None  # nothing refined, nothing propagated


def test_esd_is_propagated_and_hkl_follows_the_basis():
    primitive = SpacegroupAnalyzer(_hematite()).get_primitive_standard_structure()
    transform = setting_transform(primitive, 167)
    a_r = primitive.lattice.a
    cell = transform_cell(_as_lattice(primitive, {"a": 0.001, "b": 0.001, "c": 0.001}), transform)
    assert 0 < cell["c"]["esd"] < 0.01 and cell["a"]["esd"] > 0
    # a reflection keeps its d-spacing when its indices are mapped
    hkl = (1, 0, 0)
    hex_lattice = Lattice.from_parameters(*(cell[k]["value"] for k in ("a", "b", "c", "alpha", "beta", "gamma")))
    d_refined = primitive.lattice.d_hkl(hkl)
    d_database = hex_lattice.d_hkl(transform_hkl(hkl, transform))
    assert abs(d_refined - d_database) < 1e-4, (a_r, d_refined, d_database)


def test_structure_not_in_the_database_group_gives_none():
    assert setting_transform(_hematite(), 225) is None


def test_tied_axes_shift_together():
    primitive = SpacegroupAnalyzer(_hematite()).get_primitive_standard_structure()
    transform = setting_transform(primitive, 167)
    lattice = _as_lattice(primitive, {"a": 0.001})
    alone = transform_cell(lattice, transform)
    lattice["b"]["same_as"] = lattice["c"]["same_as"] = "a"
    tied = transform_cell(lattice, transform)
    assert tied["c"]["esd"] > alone["c"]["esd"]
