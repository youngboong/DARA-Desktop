"""Symmetry-equivalent Miller indices: the representative (hkl) a paper writes.

One rule for the report figures, the fit plot's reflection ticks and the reflection list.
"""

from __future__ import annotations

import re

import numpy as np


def laue_generators(number: int) -> list[np.ndarray]:
    """Generators of the Laue group of space group ``number`` acting on (h, k, l), standard setting
    (monoclinic b unique, hexagonal axes); trigonal groups keep Friedel pairs only (their axes vary)."""
    ops = [-np.eye(3, dtype=int)]
    two_b, two_a = np.diag([-1, 1, -1]), np.diag([1, -1, -1])
    four, six = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]]), np.array([[0, -1, 0], [1, 1, 0], [0, 0, 1]])
    swap = np.array([[0, 1, 0], [1, 0, 0], [0, 0, 1]])
    three = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]])
    if 3 <= number <= 15:
        ops.append(two_b)
    elif 16 <= number <= 74:
        ops += [two_a, two_b]
    elif 75 <= number <= 142:
        ops += [four] + ([two_a] if number >= 89 else [])
    elif 168 <= number <= 194:
        ops += [six] + ([swap] if number >= 177 else [])
    elif 195 <= number <= 230:
        ops += [three, np.diag([-1, -1, 1])] + ([swap] if number >= 207 else [])
    return ops


def usual_hkl(hkl: tuple[int, int, int], number: int, lattice) -> tuple[int, int, int]:
    """The symmetry-equivalent indices a paper writes: (100) rather than (1-10) for ZnO, preferring
    non-negative and then larger leading indices. Kept as listed unless the refined lattice confirms
    the same d-spacing (a phase refined in another setting is left alone)."""
    if lattice is None or not number:
        return hkl
    seen, todo = {hkl}, [hkl]
    generators = laue_generators(number)
    while todo:
        current = np.array(todo.pop())
        for op in generators:
            image = tuple(int(value) for value in op @ current)
            if image not in seen:
                seen.add(image)
                todo.append(image)
    best = max(seen, key=lambda item: (sum(value >= 0 for value in item), item))
    try:
        same = abs(float(lattice.d_hkl(best)) - float(lattice.d_hkl(hkl))) <= 1e-4 * float(lattice.d_hkl(hkl))
    except Exception:  # noqa: BLE001
        same = False
    return best if same else hkl


def space_group_number(name: str) -> int:
    """Space group number of a reference name such as ``ZnO_186_(cod_9004178)-0`` (0 if none)."""
    found = re.search(r"_(\d{1,3})_\(", str(name or ""))
    return int(found.group(1)) if found else 0


def refined_lattice(refinement, name: str):
    """Refined ``Lattice`` of phase ``name`` (``None`` when the listing has no structure for it)."""
    try:
        return refinement.lst_data.phases_results[name].get_structure().lattice
    except Exception:  # noqa: BLE001 - e.g. an identification: indices stay as listed
        return None


def usual_hkl_lookup(refinement):
    """``f(phase, (h, k, l)) -> (h, k, l)`` with the usual indices of ``refinement``'s phases (lattices cached)."""
    lattices: dict = {}

    def lookup(phase, hkl):
        if phase not in lattices:
            lattices[phase] = refined_lattice(refinement, phase)
        return usual_hkl(tuple(int(value) for value in hkl), space_group_number(phase), lattices[phase])

    return lookup
