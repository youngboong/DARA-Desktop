"""Per-phase refinement status, size/strain and structure results from BGMN ``.lst`` text.

Pure functions: they read the raw ``.lst`` section of a phase (or its parsed values) and the
refinement specs, never a file or a database. See ``docs/design/round-2026-11.md``
(2.1 A and E, 2.2, 2.6).
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Optional

from dara.result import parse_atom_positions, parse_lst_section
from dara.utils import parse_refinement_param

LATTICE_KEYS = ("A", "B", "C", "ALPHA", "BETA", "GAMMA")
# keys of a phase section that are neither lattice nor structure parameters
_PROFILE_KEYS = {"k1", "k2", "k3", "B1"}
_UNDEF_RE = re.compile(r"^\s*([^=\s]+)=(UNDEF|ERROR)\s*$", re.MULTILINE)
_STRUCTURE_NAME_RE = re.compile(r"^(?:occ\d+|Ball|B[A-Z]{1,2})$")

SIZE_STRAIN_DEFINITION = "BGMN: size from B1 (GrainSize 111), microstrain = sqrt(k2)"


def undefined_keys(section_text: str) -> dict[str, str]:
    """``{key: "UNDEF" | "ERROR"}`` of a raw phase section (the parser maps both to None)."""
    return {key: kind for key, kind in _UNDEF_RE.findall(section_text or "")}


def _value_esd(value) -> tuple[Optional[float], Optional[float]]:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return float(value[0]), (None if value[1] is None else float(value[1]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), None
    return None, None


def _window(spec) -> tuple[bool, Optional[float], Optional[float]]:
    """``(fixed, lower, upper)`` of a ``start_lo^hi`` spec; unknown -> ``(False, None, None)``."""
    if spec is None:
        return False, None, None
    try:
        _, lower, upper = parse_refinement_param(spec)
    except (TypeError, ValueError):
        return False, None, None
    if spec == "fixed" or (lower is not None and lower == upper):
        return True, lower, upper
    return False, lower, upper


def _close(value: float, target: Optional[float]) -> bool:
    return target is not None and abs(value - target) <= 1e-3 * max(abs(target), 1e-9)


def lattice_states(section_text: str, lattice_spec) -> dict[str, str]:
    """State of each lattice key the listing prints: ``refined`` | ``at_limit`` | ``fixed``.

    A lattice key printed without ``+-esd`` sits on a refinement limit (or is held fixed when
    ``lattice_spec == "fixed"``). Keys the listing does not print (dependent values of the
    crystal system, or a fixed lattice) are absent; callers mark them ``dependent`` /
    ``fixed_reference``. Keys are lower case (``a``, ``b``, ``c``, ``alpha``, ...).
    """
    fixed = lattice_spec == "fixed"
    parsed = parse_lst_section(section_text or "")
    states = {}
    for key in LATTICE_KEYS:
        value = parsed.get(key)
        if value is None:
            continue
        has_esd = isinstance(value, (tuple, list)) and value[1] is not None
        states[key.lower()] = "refined" if has_esd else ("fixed" if fixed else "at_limit")
    return states


def _structure_at_limit(section_text: str, parsed: dict, extra_names: Iterable[str]) -> bool:
    names = set(extra_names)
    for key, value in parsed.items():
        if key in names or _STRUCTURE_NAME_RE.match(key):
            number, esd = _value_esd(value)
            if number is not None and esd is None:
                return True
    atoms = (section_text or "").partition("\nAtomic positions for phase")[2]
    for position in parse_atom_positions(atoms.partition("\n")[2]):
        if any(esd is None for _, esd in position["refined"].values()):
            return True
    return False


def phase_status(
    section_text: str,
    parsed: Optional[dict] = None,
    *,
    lattice_spec=None,
    b1_spec=None,
    k2_spec=None,
    overall_rwp: Optional[float] = None,
    structure_params: Iterable[str] = (),
) -> dict:
    """``refinement_status`` of one phase (rules of design 2.1 A; first match wins).

    Args:
        section_text: raw ``.lst`` section of the phase ("" when the block is missing).
        parsed: ``parse_lst_section(section_text)``; computed when omitted.
        lattice_spec / b1_spec / k2_spec: effective refinement specs (``"fixed"`` or
            ``"start_lo^hi"``). ``lattice_spec == "fixed"`` marks a lattice held at the
            database values.
        overall_rwp: Rwp of the whole fit, in percent.
        structure_params: names of refined structure parameters (besides ``occ<n>`` and
            ``B<EL>``) whose limit hits count as ``structure_at_limit``.
    """
    undefined = undefined_keys(section_text)
    if not (section_text or "").strip():
        return {
            "level": "failed",
            "codes": [],
            "undefined": undefined,
            "message": "The phase block is missing from the BGMN listing.",
        }
    parsed = parse_lst_section(section_text) if parsed is None else parsed

    failed_keys = [
        key
        for key in undefined
        if key not in _PROFILE_KEYS and not key.startswith("GrainSize")
    ]
    if "GEWICHT" not in undefined and parsed.get("GEWICHT") is None:
        failed_keys.insert(0, "GEWICHT")
    if failed_keys:
        return {
            "level": "failed",
            "codes": [],
            "undefined": undefined,
            "message": f"Refinement failed ({', '.join(failed_keys)} undefined).",
        }

    pieces: dict[str, str] = {}
    states = lattice_states(section_text, lattice_spec)
    if lattice_spec == "fixed":
        pieces["lattice_fixed"] = "Lattice held at the database values"
    elif any(state == "at_limit" for state in states.values()):
        limited = ", ".join(key for key, state in states.items() if state == "at_limit")
        pieces["lattice_at_limit"] = f"lattice parameter {limited} at the refinement limit"

    b1, _ = _value_esd(parsed.get("B1"))
    grain_undefined = any(key.startswith("GrainSize") for key in undefined)
    if b1 == 0 and ("k1" in undefined or grain_undefined):
        what = "k1" if "k1" in undefined else "GrainSize"
        pieces["size_not_resolvable"] = f"size broadening refined to zero ({what} undefined)"

    at_limit_profile = []
    for key, spec in (("B1", b1_spec), ("k2", k2_spec)):
        fixed, _, upper = _window(spec)
        value, esd = _value_esd(parsed.get(key))
        if not fixed and value is not None and esd is None and _close(value, upper):
            at_limit_profile.append(key)
    if at_limit_profile:
        pieces["profile_at_limit"] = f"{' and '.join(at_limit_profile)} at the refinement limit"

    if _structure_at_limit(section_text, parsed, structure_params):
        pieces["structure_at_limit"] = "a refined structure parameter is at its limit"

    rphase, _ = _value_esd(parsed.get("Rphase"))
    if rphase is not None and overall_rwp and rphase > 1.5 * overall_rwp and rphase > 10:
        pieces["high_rphase"] = (
            f"phase R factor {rphase:g} % is high compared with the overall Rwp"
        )

    codes = list(pieces)
    message = "; ".join(pieces.values())
    return {
        "level": "limited" if codes else "ok",
        "codes": codes,
        "undefined": undefined,
        "message": (message[0].upper() + message[1:] + ".") if message else "",
    }


def size_strain(
    parsed: dict,
    undefined: dict,
    *,
    b1_spec=None,
    k1_spec=None,
    k2_spec=None,
    size_status: str = "refined",
    failed: bool = False,
) -> dict:
    """Crystallite size and microstrain of a phase (design 2.2).

    ``size_status`` is the ``crystallite_size_status`` value (``failed=True`` overrides it).
    Microstrain is ``sqrt(k2)`` in percent with ``esd = esd(k2) / (2 sqrt(k2))``.
    """
    grain = next(
        (value for key, value in parsed.items() if key.lower().startswith("grainsize")), None
    )
    size, size_esd = _value_esd(grain)
    size_state = "failed" if failed else size_status

    k2, k2_esd = _value_esd(parsed.get("k2"))
    fixed_k2, _, k2_upper = _window(k2_spec)
    strain = {"value": None, "esd": None}
    if "k2" in undefined:
        strain_state = "poorly_determined"
    elif fixed_k2 or k2 is None:
        strain_state = "not_refined"
    elif k2 == 0 and k2_esd is None:
        strain_state = "not_detected"
        strain = {"value": 0.0, "esd": None}
    elif k2_esd is None and _close(k2, k2_upper):
        strain_state = "at_limit"
    else:
        root = math.sqrt(k2) if k2 > 0 else 0.0
        strain = {
            "value": root * 100,
            "esd": k2_esd / (2 * root) * 100 if (k2_esd is not None and root > 0) else None,
        }
        poor = k2_esd is not None and k2 > 0 and k2_esd / k2 > 0.5
        strain_state = "poorly_determined" if poor else "refined"

    separable = size_state == "refined" and strain_state == "refined"
    note = None
    if size_state == "not_resolvable" and strain_state == "refined":
        note = (
            "All excess broadening was assigned to microstrain; "
            "crystallite size is beyond the resolvable range."
        )
    elif size_state == "refined" and strain_state == "not_detected":
        note = "No strain broadening detected; all excess broadening was assigned to crystallite size."
    elif not separable and strain_state != "not_refined":
        note = "Size and strain cannot be separated from this pattern."
    k1, k1_esd = _value_esd(parsed.get("k1"))
    fixed_k1, _, k1_upper = _window(k1_spec)
    if (
        size is not None
        and not fixed_k1
        and k1 is not None
        and k1_esd is None
        and _close(k1, 1.0 if k1_upper is None else k1_upper)
    ):
        limit = "Size assumes the upper limit of the size-distribution parameter (k1 = 1)."
        note = f"{note} {limit}" if note else limit

    def raw(key):
        if key in undefined:
            return [None, None]
        if key not in parsed:
            return None
        value, esd = _value_esd(parsed[key])
        return [value, esd]

    return {
        "size_nm": {"value": size, "esd": size_esd},
        "size_state": size_state,
        "microstrain_percent": strain,
        "strain_state": strain_state,
        "separable": separable,
        "definition": SIZE_STRAIN_DEFINITION,
        "note": note,
        "raw": {"B1": raw("B1"), "k1": raw("k1"), "k2": raw("k2")},
    }


def _occupancy_label(constraint: dict, labels: list) -> Optional[str]:
    """Reader-facing name of an occupancy parameter: "Li/Ni exchange (Li1-Ni1)", "Mg on Li1"."""
    label = lambda index: labels[index] if isinstance(index, int) and 0 <= index < len(labels) else "?"  # noqa: E731
    try:
        kind = constraint.get("kind")
        if kind == "exchange":
            first, second = constraint["sites"]
            a, b = (e.capitalize() for e in constraint["elements"])
            return f"{a}/{b} exchange ({label(first)}-{label(second)})"
        if kind == "substitution":
            return f"{constraint['dopant'].capitalize()} on {label(constraint['site'])}"
        if kind == "vacancy":
            return f"{constraint['element'].capitalize()} occupancy on {label(constraint['site'])}"
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _site_labels(positions: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    labels = []
    for position in positions:
        first = next(iter(position["species"]), "X")
        match = re.match(r"[A-Z][a-z]?", first)
        element = match.group(0) if match else first
        counts[element] = counts.get(element, 0) + 1
        labels.append(f"{element}{counts[element]}")
    return labels


def structure_results(
    phase_result, spec: Optional[dict], param_values: Optional[dict]
) -> Optional[dict]:
    """``structure_refinement`` JSON of a phase refined with a structure model (design 2.6).

    Args:
        phase_result: ``dara.result.PhaseResult`` (``atom_positions``; the listing values
            are the BGMN-standardised positions).
        spec: the structure request (``positions``, ``thermal``, ``occupancy``); ``None``
            returns ``None``.
        param_values: ``{parameter name: value | (value, esd)}`` of the phase section
            (``occ<k>``, ``Ball`` / ``B<element>``; B in nm^2).
    """
    if not spec:
        return None
    positions = getattr(phase_result, "atom_positions", None)
    if positions is None:
        positions = parse_atom_positions(getattr(phase_result, "atom_positions_string", None))
    values = {str(k).lower(): v for k, v in (param_values or {}).items()}
    labels = _site_labels(positions)
    free_sites = set(spec.get("positions") or [])

    parameters: list[dict] = []

    def add_parameter(name: str, label: Optional[str] = None) -> None:
        number, esd = _value_esd(values.get(name.lower()))
        if number is not None and not any(p["name"] == name for p in parameters):
            state = "refined" if esd is not None else "at_limit"
            entry = {"name": name, "value": number, "esd": esd, "state": state}
            if label:
                entry["label"] = label
            parameters.append(entry)

    # the explicitly parameterised species carries the esd; its complement has none
    esd_for: dict[tuple[int, str], Optional[float]] = {}
    for k, constraint in enumerate(spec.get("occupancy") or []):
        add_parameter(f"occ{k}", _occupancy_label(constraint, labels))
        esd = _value_esd(values.get(f"occ{k}"))[1]
        kind = constraint.get("kind")
        if kind == "exchange":
            first, second = constraint["sites"]
            element_a, element_b = (e.capitalize() for e in constraint["elements"])
            ratio = positions[first]["multiplicity"] / positions[second]["multiplicity"]
            esd_for[(first, element_b)] = esd
            esd_for[(second, element_a)] = None if esd is None else esd * ratio
        elif kind == "substitution":
            esd_for[(constraint["site"], constraint["dopant"].capitalize())] = esd
        elif kind == "vacancy":
            esd_for[(constraint["site"], constraint["element"].capitalize())] = esd

    thermal = spec.get("thermal", "fixed")
    sites = []
    for index, position in enumerate(positions):
        xyz = []
        for axis, listed in zip("xyz", position["xyz"]):
            refined = position["refined"].get(axis)
            if refined is not None:
                value, esd = refined
                state = "refined" if esd is not None else "at_limit"
            else:
                value, esd, state = listed, None, "fixed"
            xyz.append({"value": value, "esd": esd, "state": state})
        species = [
            {
                "element": element,
                "occupancy": {"value": occupancy, "esd": esd_for.get((index, element))},
            }
            for element, occupancy in position["species"].items()
        ]
        b_iso = {"value": 1.0, "esd": None, "state": "fixed"}  # Angstrom^2, the DARA default
        if thermal in {"overall", "per_element"}:
            first = next(iter(position["species"]), "")
            name = "Ball" if thermal == "overall" else f"B{first}"
            add_parameter(name)
            number, esd = _value_esd(values.get(name.lower()))
            if number is not None:  # TDS is in nm^2: 0.01 nm^2 = 1.0 Angstrom^2
                b_iso = {
                    "value": number * 100,
                    "esd": None if esd is None else esd * 100,
                    "state": "refined" if esd is not None else "at_limit",
                }
        sites.append(
            {
                "index": index,
                "label": labels[index],
                "refined_positions": index in free_sites,
                "xyz": xyz,
                "species": species,
                "b_iso": b_iso,
            }
        )
    return {"spec": spec, "sites": sites, "parameters": parameters, "indicative": True}
