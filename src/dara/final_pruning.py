"""Phases to drop from a final refinement before it is reported.

The phase search adds a phase whenever it lowers the screening Rwp a little. In the full
refinement such a phase can collapse: its scale goes to zero and BGMN leaves the lattice
undefined (``Refinement failed``), or a second database record of a phase already in the fit
takes a small share of its intensity. Those phases carry no information, so the final fit is
refined again without them (see ``dara.jobs``) and kept only when the fit stays as good.
"""

from __future__ import annotations

from pymatgen.core import Composition

# Below 0.5 wt% a phase is under the lab-XRD detection limit (~1 wt%); it is dropped only when
# the refit without it fits as well, so a minor phase that explains peaks stays.
MIN_WEIGHT_FRACTION = 0.005


def _formula_of(name: str) -> Composition | None:
    """Composition from a reference name such as ``ZrO2_14_(cod_1010912)-0``."""
    try:
        return Composition(name.split("_", 1)[0]).fractional_composition
    except Exception:
        return None


def _same_compound(a: Composition | None, b: Composition | None) -> bool:
    return a is not None and b is not None and a.almost_equals(b, rtol=0.01, atol=1e-3)


def phases_to_prune(
    phases_results: dict,
    weights: dict[str, float],
    protected: set[str] | frozenset[str] = frozenset(),
    min_fraction: float = MIN_WEIGHT_FRACTION,
) -> dict[str, str]:
    """``{phase name: reason}`` of phases to try dropping from one refined hypothesis, in order.

    Reasons: ``"failed"`` (weight or lattice undefined in the BGMN listing), ``"negligible"``
    (below ``min_fraction``), ``"duplicate"`` (same compound and space group as a heavier phase
    of the fit; the heavier one stays). ``protected`` phases (user-supplied CIFs) are never
    dropped. At least one phase always remains.
    """
    drop: dict[str, str] = {}
    for name, phase in phases_results.items():
        if name in protected:
            continue
        if getattr(phase, "gewicht", None) is None or getattr(phase, "a", None) is None:
            drop[name] = "failed"
        elif float(weights.get(name, 0.0)) < min_fraction:
            drop[name] = "negligible"

    kept = sorted(
        (name for name in phases_results if name not in drop),
        key=lambda name: -float(weights.get(name, 0.0)),
    )
    for index, name in enumerate(kept):
        if name in protected:
            continue
        group = getattr(phases_results[name], "spacegroup_no", None)
        heavier = kept[:index]
        if any(
            other not in drop
            and getattr(phases_results[other], "spacegroup_no", None) == group
            and _same_compound(_formula_of(other), _formula_of(name))
            for other in heavier
        ):
            drop[name] = "duplicate"

    if phases_results and len(drop) >= len(phases_results):
        best = max(phases_results, key=lambda name: float(weights.get(name, 0.0)))
        drop.pop(best, None)
    # Failed phases first, then the lightest: the order in which they are tried one by one.
    order = sorted(drop, key=lambda name: (drop[name] != "failed", float(weights.get(name, 0.0))))
    return {name: drop[name] for name in order}


def refit_is_acceptable(old_rwp: float, new_rwp: float) -> bool:
    """The refit without the dropped phases fits as well: Rwp up by at most 0.05 or 1 %."""
    return new_rwp <= old_rwp + max(0.05, 0.01 * old_rwp)
