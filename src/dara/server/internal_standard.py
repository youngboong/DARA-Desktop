"""Internal-standard (absolute) quantification from refined weight fractions.

Pure function, no server or database access. See ``docs/design/round-2026-11.md`` (2.4).
"""

from __future__ import annotations

import math
from typing import Optional

from pymatgen.core import Composition

NOT_FOUND_MESSAGE = (
    "The internal standard {formula} is not among the fitted phases; "
    "absolute amounts cannot be computed."
)
NEGATIVE_MESSAGE = (
    "The fitted standard amount is higher than added (amorphous fraction below zero). "
    "Check the weighed amount, the standard's purity, or microabsorption."
)
UNRELIABLE_MESSAGE = (
    "The internal standard phase is not reliably refined; the absolute amounts are uncertain."
)


def _reduced(formula: Optional[str]) -> Optional[str]:
    try:
        return Composition(formula).reduced_formula
    except Exception:  # unparsable formula never matches
        return None


def _r(value: float, digits: int = 1) -> float:
    return round(value + 0.0, digits)  # + 0.0 turns -0.0 into 0.0


def quantify(phases: list[dict], spec: dict) -> dict:
    """Absolute phase amounts from the refined wt% of an internal standard.

    Args:
        phases: ``[{"name", "formula", "weight_percent", "weight_esd_percent",
            "weight_flag", "refinement_level"}]``.
        spec: ``{"formula", "wt_percent", "phase"}`` (``phase`` picks one of several phases
            with the standard's formula).

    The standard's own row is left out of ``phases`` (its amount is the added one).
    Errors are propagated without correlations (design 2.4).
    """
    w_std = float(spec["wt_percent"])
    if not 0 < w_std < 100:
        raise ValueError("wt_percent must be between 0 and 100")
    result = {
        "spec": {
            "formula": spec["formula"],
            "wt_percent": w_std,
            "phase": spec.get("phase"),
        },
        "status": "ok",
        "standard_phase": None,
        "fitted_wt_percent": None,
        "fitted_esd_percent": None,
        "scale": None,
        "phases": [],
        "amorphous": None,
        "message": None,
    }

    target = _reduced(spec["formula"])
    matches = [p for p in phases if target is not None and _reduced(p.get("formula")) == target]
    if spec.get("phase"):
        matches = [p for p in matches if p.get("name") == spec["phase"]]
    if len(matches) > 1:
        result["status"] = "ambiguous_standard"
        result["message"] = (
            f"More than one fitted phase has the formula {spec['formula']}; choose the standard phase."
        )
        result["candidates"] = [p.get("name") for p in matches]
        return result
    standard = matches[0] if matches else None
    fitted = None if standard is None else standard.get("weight_percent")
    if not fitted or fitted <= 0:
        result["status"] = "standard_not_found"
        result["message"] = NOT_FOUND_MESSAGE.format(formula=spec["formula"])
        return result

    fitted_esd = standard.get("weight_esd_percent") or 0.0
    k = w_std / fitted
    k_esd = k * fitted_esd / fitted
    to_sample = 1 - w_std / 100

    rows = []
    for phase in phases:
        if phase is standard:
            continue
        weight = phase.get("weight_percent") or 0.0
        weight_esd = phase.get("weight_esd_percent") or 0.0
        absolute = weight * k
        absolute_esd = math.hypot(k * weight_esd, weight * k_esd)
        rows.append(
            {
                "name": phase.get("name"),
                "absolute_wt_percent": _r(absolute),
                "absolute_esd_percent": _r(absolute_esd, 2),
                "sample_wt_percent": _r(absolute / to_sample),
                "sample_esd_percent": _r(absolute_esd / to_sample, 2),
            }
        )

    amorphous = 100 * (1 - k)
    amorphous_esd = 100 * k_esd
    not_detected = amorphous < 0 and -amorphous <= 2 * amorphous_esd
    result.update(
        standard_phase=standard.get("name"),
        fitted_wt_percent=_r(fitted),
        fitted_esd_percent=_r(fitted_esd, 2),
        scale=round(k, 4),
        phases=rows,
        amorphous={
            "mixture_wt_percent": _r(amorphous),
            "sample_wt_percent": _r(amorphous / to_sample),
            "esd_percent": _r(amorphous_esd, 2),
            "not_detected": not_detected,  # shown as "0 (not detected)"; the raw value is the tooltip
        },
    )

    messages = []
    if standard.get("weight_flag") or standard.get("refinement_level") == "failed":
        result["status"] = "standard_unreliable"
        messages.append(UNRELIABLE_MESSAGE)
    if amorphous < 0 and not not_detected:
        if result["status"] == "ok":
            result["status"] = "negative_amorphous"
        messages.append(NEGATIVE_MESSAGE)
    result["message"] = " ".join(messages) or None
    return result
