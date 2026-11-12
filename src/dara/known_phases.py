"""Known-phase refinement: which listed phases a fit must contain, and what became of each.

In ``analysis_mode == "known_mixture"`` the tree search picks the best database record of the
listed formulas, but a minor listed phase it does not pick would just be missing from the
result. Here every listed formula (and every user CIF) is a *group* of reference structures;
``dara.jobs`` tries each group the best fit lacks (one trial refit, kept only when it explains
observed peaks the fit without it left unexplained), and the final fit reports one outcome per
group (``refined``, ``not_detected``, ``no_structure``).
Pure functions, so they are testable without BGMN.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pymatgen.core import Composition

from dara.search.prefilter import stem_formula

#: More listed formulas than this are refused at submit (one search + one fit would not finish).
MAX_KNOWN_FORMULAS = 12


@dataclass(frozen=True)
class Group:
    """One listed formula (all its candidate structures) or one exact structure."""

    label: str
    stems: tuple[str, ...]  # candidate reference stems; empty: no structure was found
    exact: bool = False  # a user CIF / reused structure: always required, never replaced


def reduced_formula(text: str) -> str | None:
    """Reduced formula of a formula string (``La(OH)3`` == ``La(HO)3``), or ``None``."""
    try:
        return Composition(text).reduced_formula
    except Exception:  # noqa: BLE001
        return None


def formula_of_cif(cif) -> str:
    """Formula of a reference CIF, from its structure (never from the file name: ``In_house_ZrO2`` is ZrO2).

    The reduced composition, written as the head of the file name (``Bi2Fe4O9`` of ``Bi2Fe4O9_55_(cod_1)-0``)
    when that is the same formula. An unreadable CIF keeps its file name; the search rejects it later.
    """
    name = cif.filename or cif.name
    try:
        formula = cif.to_structure().composition.reduced_formula
    except Exception:  # noqa: BLE001
        return name
    head = _head(name)
    return head if head and reduced_formula(head) == formula else formula


def formulas_of_cifs(cifs: Iterable) -> list[str]:
    """Distinct formulas of reference CIFs."""
    return distinct_formulas(formula_of_cif(cif) for cif in cifs)


def mp_configured() -> bool:
    """Materials Project can be queried: an API key is set and ``mp_api`` is installed."""
    return bool(os.environ.get("MP_API_KEY")) and importlib.util.find_spec("mp_api") is not None


def distinct_formulas(formulas: Iterable[str]) -> list[str]:
    """Listed formulas without repeats by reduced formula (first spelling, input order)."""
    seen: dict[str | None, str] = {}
    for formula in formulas:
        seen.setdefault(reduced_formula(formula), formula)
    return list(seen.values())


def build_groups(
    formulas: Iterable[str], candidate_stems: Iterable[str], exact: Mapping[str, str] | None = None
) -> list[Group]:
    """Groups in input order: one per distinct listed formula, then the unlisted user structures.

    ``exact`` maps the stem of every user-supplied structure (uploaded or library CIF, reused
    phase set) to its formula. Those structures satisfy a listed formula of the same reduced
    formula: its group is then the user structures only (one exact group each, no database
    candidates), at the formula's position.
    """
    exact = dict(exact or {})
    exact_keys = {stem: reduced_formula(formula) for stem, formula in exact.items()}
    candidates = [stem for stem in candidate_stems if stem not in exact]
    listed = {}
    for formula in formulas:
        listed.setdefault(reduced_formula(formula), formula)

    def exact_groups(key):
        return [Group(listed.get(key, exact[stem]), (stem,), True) for stem in exact if exact_keys[stem] == key]

    groups = []
    for key, formula in listed.items():
        user = exact_groups(key)
        stems = tuple(sorted(stem for stem in candidates if stem_formula(stem) == key))
        groups.extend(user or [Group(formula, stems)])
    for key in dict.fromkeys(exact_keys.values()):
        if key not in listed:
            groups.extend(exact_groups(key))
    return groups


def _head(stem: str) -> str | None:
    """Formula as written in a reference name (``ZrO2`` of ``ZrO2_14_(cod_1010912)-0``)."""
    head = stem.split("_")[0]
    return head if reduced_formula(head) else None


def formulas_without_structure(formulas: Iterable[str], candidate_stems: Iterable[str]) -> list[str]:
    """Listed formulas for which no candidate structure exists yet."""
    return [group.label for group in build_groups(formulas, candidate_stems) if not group.stems]


def pick_stem(group: Group, rwp: Mapping[str, float]) -> str | None:
    """Best single-phase structure of a group: lowest screening Rwp; no score ranks last, then stem order."""
    if not group.stems:
        return None
    return min(group.stems, key=lambda stem: (stem not in rwp, rwp.get(stem, 0.0), stem))


def known_phase_report(
    groups: Sequence[Group],
    weights: Mapping[str, float],
    removed: Mapping[str, tuple[str, float | None]],
    reason: str | None = None,
) -> list[dict]:
    """``search_summary["known_phases"]``: one outcome per group, in input order.

    ``weights`` are the weight fractions of the adopted fit by phase name; ``removed`` maps the
    phases pruned from it, or whose trial fit was rejected, to ``(reason, weight fraction before
    removal or ``None``)``. ``reason`` is given to a ``not_detected`` group without a removal
    (``"no_fit"`` when the search found no fit at all).
    """
    report = []
    for group in groups:
        entry = {
            "formula": group.label, "status": "no_structure", "phases": [],
            "weight_percent": None, "removed_weight_percent": None, "reason": None,
        }
        refined = [stem for stem in group.stems if stem in weights]
        dropped = [stem for stem in group.stems if stem in removed]
        if refined:
            entry.update(
                status="refined", phases=refined,
                weight_percent=round(100 * sum(float(weights[stem]) for stem in refined), 2),
            )
        elif group.stems:
            entry.update(status="not_detected", reason=reason)
            if dropped:
                heaviest = max(dropped, key=lambda stem: removed[stem][1] or 0.0)
                held = [float(removed[stem][1]) for stem in dropped if removed[stem][1] is not None]
                entry.update(
                    reason=removed[heaviest][0],
                    removed_weight_percent=round(100 * sum(held), 2) if held else None,
                )
        report.append(entry)
    return report


def not_detected_text(entry: dict) -> str | None:
    """One sentence for a ``not_detected`` / ``no_structure`` entry (``None`` for refined ones)."""
    if entry.get("status") == "not_detected":
        weight, why = entry.get("removed_weight_percent"), entry.get("reason")
        if why == "not_checked":
            return f"{entry['formula']}: not checked (the time limit was reached)"
        if why == "no_fit":
            detail = " (no phase combination fitted the pattern)"
        elif why == "no_peak_evidence":
            detail = f" (a trial fit gave {weight or 0:g} wt% but explained no additional peak)"
        elif why == "removed_no_peak_evidence":
            detail = f" (refined to {weight or 0:g} wt%, but removing it left no additional peak unexplained)"
        elif why == "failed" and weight is None:
            detail = " (a trial fit with it failed)"
        else:
            detail = f" (refined to {weight:g} wt%, removed)" if weight is not None else ""
        return f"{entry['formula']}: not detected{detail}"
    if entry.get("status") == "no_structure":
        return (
            f"{entry['formula']}: no reference structure found "
            "(enable Materials Project or add a CIF)"
        )
    return None
