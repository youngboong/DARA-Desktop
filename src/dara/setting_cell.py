"""Cell and Miller indices of a phase refined in a subgroup setting, in its database setting.

A phase whose database space group BGMN cannot use is refined in a lower-symmetry subgroup
setting (Fe2O3 R-3c as C2/c). Readers compare lattice parameters and hkl with cards and papers
in the database setting, so both are mapped back here. ``S`` is the matrix with
``database basis = S @ refined basis`` (rows are basis vectors). It can be rational: a
C-centred cell maps onto the hexagonal R cell with det 3/2. Miller indices transform the same
way (``hkl_db = S @ hkl``).
"""

from __future__ import annotations

import numpy as np

_KEYS = ("a", "b", "c", "alpha", "beta", "gamma")


def setting_transform(structure, database_number: int) -> np.ndarray | None:
    """``S`` mapping the refined cell of ``structure`` onto the conventional cell of the
    database space group, or ``None`` when the structure is not found in that group."""
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    for symprec in (0.01, 0.05, 0.1):
        analyzer = SpacegroupAnalyzer(structure, symprec=symprec)
        if analyzer.get_space_group_number() != database_number:
            continue
        dataset = analyzer.get_symmetry_dataset()
        matrix = getattr(dataset, "transformation_matrix", None)
        if matrix is None and isinstance(dataset, dict):
            matrix = dataset.get("transformation_matrix")
        if matrix is None:
            continue
        # spglib: (a_s, b_s, c_s) = (a, b, c) P^-1 with basis vectors as columns; rows here.
        transform = np.linalg.inv(np.asarray(matrix, dtype=float)).T
        if np.linalg.det(transform) > 0:
            return np.round(transform, 6)
    return None


def _parameters(values: dict, transform: np.ndarray) -> np.ndarray:
    from pymatgen.core import Lattice

    refined = Lattice.from_parameters(*(values[key] for key in _KEYS))
    return np.array(Lattice(transform @ refined.matrix).parameters)


def transform_cell(lattice: dict, transform: np.ndarray) -> dict | None:
    """Database-setting lattice from the refined ``lattice`` (``{key: {"value", "esd"}}``, Å and
    degrees). e.s.d. are propagated numerically from the refined e.s.d., treated as uncorrelated
    (BGMN reports no covariances), so they are approximate. An entry with ``"same_as": key`` is
    constrained equal to ``key`` and shifts with it."""
    if not all(lattice.get(key, {}).get("value") is not None for key in _KEYS):
        return None
    values = {key: float(lattice[key]["value"]) for key in _KEYS}
    centre = _parameters(values, transform)
    variance = np.zeros(6)
    for key in _KEYS:
        esd = lattice[key].get("esd")
        if not esd:
            continue
        # axes tied to this one by symmetry (``same_as``) shift with it
        tied = [key] + [other for other in _KEYS if lattice[other].get("same_as") == key]
        shifted = _parameters({**values, **{other: values[other] + float(esd) for other in tied}}, transform)
        variance += (shifted - centre) ** 2
    refined_any = any(lattice[key].get("esd") for key in _KEYS)
    return {
        key: {
            "value": float(value),
            "esd": float(np.sqrt(var)) if refined_any and var > 0 else None,
            "unit": "deg" if key in ("alpha", "beta", "gamma") else "Å",
        }
        for key, value, var in zip(_KEYS, centre, variance)
    }


def transform_hkl(hkl, transform: np.ndarray) -> tuple[int, int, int] | None:
    """Miller indices of a reflection in the database setting (``None`` if not integral)."""
    values = np.asarray(transform, dtype=float) @ np.asarray(hkl, dtype=float)
    if not np.allclose(values, np.round(values), atol=1e-3):
        return None
    return tuple(int(round(value)) for value in values)


def database_number_of(phase_name) -> int | None:
    """Space-group number of the database record: ``Fe2O3_167_(cod_…)`` -> 167."""
    tokens = str(phase_name).split("_")
    return int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else None


def setting_transforms(refinement) -> dict:
    """``{phase name: S}`` for the phases of ``refinement`` refined in a subgroup setting."""
    out = {}
    phases = getattr(getattr(refinement, "lst_data", None), "phases_results", None) or {}
    for name, phase in phases.items():
        number = database_number_of(name)
        if number is None or getattr(phase, "spacegroup_no", None) in (None, number):
            continue
        try:
            transform = setting_transform(phase.get_structure(), number)
        except Exception:  # noqa: BLE001 - the refined-setting indices are still listed
            transform = None
        if transform is not None:
            out[name] = transform
    return out
