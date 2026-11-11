"""Structure-refinement model for one phase: site table, request validation, ``.str`` rewrite.

Pure functions (``site_table`` reads a CIF). The rules and the BGMN syntax come from
``docs/design/round-2026-11.md`` (1.2 V1-V10 and 2.6). ``cif2str`` is used unchanged: the site
order here is the order of the ``E=`` lines it writes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from dara.cif2str import CIF2StrError, check_wyckoff, process_specie_string
from dara.utils import load_symmetrized_structure

COORD_RANGE = 0.02  # fractional coordinates: start +- 0.02
B_START, B_LOWER, B_UPPER = 0.01, 0.001, 0.04  # TDS in nm^2 (0.1-4.0 A^2, start 1.0 A^2)
OCC_LIMITS = {"exchange": (0.02, "0", "0.2"), "substitution": (0.02, "0", "0.3"), "vacancy": (0.95, "0.7", "1")}
MAX_PARAMETERS = 12
MIN_WEIGHT, MIN_OCC_WEIGHT = 5.0, 10.0
MAX_RWP, MAX_GOF = 15.0, 4.0


def _best_setting(spg, structure):
    """Same setting search as ``cif2str``; returns ``(spacegroup_setting, element_settings)``."""
    hall_number = str(spg.get_symmetry_dataset().hall_number)
    db = Path(__file__).parent / "data" / "spglib_db" / "spg.json"
    with db.open("r", encoding="utf-8") as f:
        settings = json.load(f)[hall_number]["settings"]
    best = None
    for setting in settings:
        element_settings, errors = check_wyckoff(setting, structure)
        if best is None or errors < best[2]:
            best = (setting, element_settings, errors)
        if errors == 0:
            break
    if best[2] > 0:
        raise CIF2StrError("Cannot find a valid lattice symmetry setting.")
    return best[0], best[1]


def site_table(cif_path) -> list[dict]:
    """Symmetry-independent sites in the order of the ``E=`` lines ``cif2str`` writes.

    Each site: ``index, label, wyckoff ("4c"), multiplicity, species [{element, occupancy}],
    xyz, free`` (``free`` = x/y/z the Wyckoff position leaves free).
    """
    structure, spg = load_symmetrized_structure(Path(cif_path))
    setting, element_settings = _best_setting(spg, structure)
    counts: dict[str, int] = {}
    sites = []
    for index, (group, es) in enumerate(zip(structure.equivalent_indices, element_settings)):
        site = structure[group[0]]
        species = [
            {"element": getattr(sp, "symbol", str(sp)), "occupancy": float(occ)}
            for sp, occ in sorted(site.species.items())
        ]
        symbol = structure.wyckoff_symbols[index]
        match = re.match(r"\d+", symbol)
        wyckoffs = setting["wyckoffs"].get(es["Wyckoff"], {})
        notation = wyckoffs.get("std_notations", ["x y z"])[0]
        element = species[0]["element"]
        counts[element] = counts.get(element, 0) + 1
        sites.append(
            {
                "index": index,
                "label": f"{element}{counts[element]}",
                "wyckoff": symbol,
                "multiplicity": int(match.group(0)) if match else len(group),
                "species": species,
                "xyz": [float(es["x"]), float(es["y"]), float(es["z"])],
                "free": [v for v in "xyz" if re.search(rf"\b{v}\b", notation)],
            }
        )
    return sites


# ---------------------------------------------------------------- validation


def _z(element: str) -> Optional[int]:
    try:
        from pymatgen.core import Element

        return Element(element).Z
    except Exception:
        return None


def _primary(site: dict) -> str:
    return max(site["species"], key=lambda s: s["occupancy"])["element"]


def _has(site: dict, element: str) -> bool:
    return any(s["element"] == element for s in site["species"])


def _b_names(thermal: str, sites: list[dict]) -> list[str]:
    if thermal == "overall":
        return ["Ball"]
    if thermal == "per_element":
        names = []
        for site in sites:
            name = f"B{process_specie_string(_primary(site))}"
            if name not in names:
                names.append(name)
        return names
    return []


def validate(spec: dict, sites: list[dict], context: dict) -> dict:
    """Apply refusal rules R1-R9 and the warnings of design 2.6.

    ``context``: ``weight_percent, narrow, refinement_status, spacegroup_number,
    database_spacegroup_number, n_reflections, base_rwp, base_gof`` (missing/None = unknown).
    Returns ``{"refusals": [{"code", "message"}], "warnings": [str], "n_parameters", "max_parameters"}``.
    """
    refusals: list[dict] = []
    warnings: list[str] = []

    def refuse(code: str, message: str) -> None:
        item = {"code": code, "message": message}
        if item not in refusals:
            refusals.append(item)

    thermal = spec.get("thermal") or "fixed"
    positions = list(spec.get("positions") or [])
    occupancy = list(spec.get("occupancy") or [])
    phase = spec.get("phase") or "The phase"

    # R9: site indices and elements (checked first; later rules rely on valid indices)
    def site_ok(i, element=None) -> bool:
        if not isinstance(i, int) or not 0 <= i < len(sites):
            refuse("bad_site", f"Site {i} does not exist.")
            return False
        if element is not None and not _has(sites[i], element):
            refuse("bad_site", f"Site {i} does not contain {element}.")
            return False
        return True

    bad = False
    used_sites: list[int] = []
    for i in positions:
        bad |= not site_ok(i)
    for c in occupancy:
        kind = c.get("kind")
        if kind == "exchange":
            first, second = c.get("sites", [None, None])
            element_a, element_b = c.get("elements", [None, None])
            ok = site_ok(first, element_a) & site_ok(second, element_b)
            if ok and first == second:
                refuse("bad_site", "An exchange needs two different sites.")
                ok = False
            bad |= not ok
            used_sites += [first, second]
        elif kind in ("substitution", "vacancy"):
            element = c.get("host" if kind == "substitution" else "element")
            bad |= not site_ok(c.get("site"), element)
            used_sites.append(c.get("site"))
        else:
            refuse("bad_site", f"Unknown occupancy constraint {kind}.")
            bad = True
    for i in set(used_sites):
        if used_sites.count(i) > 1:
            refuse("bad_site", f"Site {i} is used by more than one occupancy constraint.")
            bad = True

    # parameter count: coordinates + B + occupancy constraints
    n_coordinates = 0 if bad else sum(len(sites[i]["free"]) for i in set(positions))
    n_parameters = n_coordinates + (0 if bad else len(_b_names(thermal, sites))) + len(occupancy)
    n_reflections = context.get("n_reflections")
    max_parameters = MAX_PARAMETERS if n_reflections is None else min(MAX_PARAMETERS, n_reflections // 5)

    # R1
    status = context.get("refinement_status") or {}
    if status.get("level") == "failed" or {"lattice_fixed", "lattice_at_limit"} & set(status.get("codes") or []):
        refuse("lattice_not_stable", f"Refine the lattice first: {phase} has lattice parameters that are fixed or at the limit.")
    # R2
    weight = context.get("weight_percent")
    if context.get("narrow") or (weight is not None and weight < MIN_WEIGHT):
        refuse("minor_phase", "Structure refinement is not offered for minor or newly added phases (< 5 wt%).")
    # R3
    database_number = context.get("database_spacegroup_number")
    if database_number and database_number != context.get("spacegroup_number"):
        refuse("subgroup_setting", "This phase is refined in a lower-symmetry setting; structure parameters would not follow its true symmetry.")
    # R4
    if n_parameters > max_parameters:
        refuse("too_many_parameters", f"Too many structure parameters for the data: {n_parameters} requested, at most {max_parameters} allowed.")
    # R5
    if not bad:
        hydrogen = any(_has(sites[i], "H") for i in positions)
        hydrogen |= thermal == "per_element" and any(_primary(s) == "H" for s in sites)
        for c in occupancy:
            hydrogen |= "H" in {*c.get("elements", []), c.get("host"), c.get("dopant"), c.get("element")}
        if hydrogen:
            refuse("hydrogen", "Hydrogen positions and occupancies cannot be refined from X-ray data.")
    # R6 and the weak-contrast warning
    for c in occupancy:
        pair = c.get("elements") if c.get("kind") == "exchange" else [c.get("host"), c.get("dopant")]
        if c.get("kind") == "vacancy" or not pair or None in pair:
            continue
        z1, z2 = _z(pair[0]), _z(pair[1])
        if z1 is None or z2 is None:
            continue
        if abs(z1 - z2) < 3:
            refuse("low_contrast", f"{pair[0]} and {pair[1]} scatter X-rays too similarly to refine their occupancy.")
        elif abs(z1 - z2) <= 7:
            warnings.append(f"Weak X-ray contrast between {pair[0]} and {pair[1]}; treat the occupancy as indicative.")
    # R7
    rwp, gof = context.get("base_rwp"), context.get("base_gof")
    if (rwp is not None and rwp > MAX_RWP) or (gof is not None and gof > MAX_GOF):
        shown = "unknown" if rwp is None else f"{rwp:.1f}"
        refuse("poor_base_fit", f"The base fit is not good enough (Rwp {shown} %). Improve the phase model first.")
    # R8
    if occupancy and weight is not None and weight < MIN_OCC_WEIGHT:
        refuse("occupancy_minor", "Occupancy refinement needs a phase with at least 10 wt%.")

    if any(c.get("kind") == "substitution" for c in occupancy):
        warnings.append("Substitution changes the phase composition.")
    if thermal == "per_element":
        warnings.append("Element-specific B values from laboratory XRD are often unstable.")

    return {
        "refusals": refusals,
        "warnings": warnings,
        "n_parameters": n_parameters,
        "max_parameters": max_parameters,
    }


# ---------------------------------------------------------------- .str rewrite


def _e_value(host: str, other: str, name: str) -> str:
    return f"({process_specie_string(host)},{process_specie_string(other)}({name}))"


def apply_to_str(str_text: str, spec: dict, sites: list[dict]) -> str:
    """Rewrite a ``cif2str`` ``.str`` text for the structure model ``spec`` (design 2.6).

    ``sites`` is ``site_table`` of the same CIF; the ``E=`` lines of ``str_text`` must be in
    that order. ``spec["reset"]`` (or an empty spec) returns the text unchanged.
    """
    if not spec or spec.get("reset"):
        return str_text
    lines = str_text.split("\n")
    e_lines = [n for n, line in enumerate(lines) if line.lstrip().startswith("E=")]
    if len(e_lines) != len(sites):
        raise ValueError(f"The .str has {len(e_lines)} sites, the site table {len(sites)}.")

    thermal = spec.get("thermal") or "fixed"
    params: list[str] = []

    # B (TDS is in nm^2)
    b_of_site: dict[int, str] = {}
    for name in _b_names(thermal, sites):
        params.append(f"PARAM={name}={B_START:.6f}_{B_LOWER}^{B_UPPER} //")
    for i, site in enumerate(sites):
        if thermal == "overall":
            b_of_site[i] = "Ball"
        elif thermal == "per_element":
            b_of_site[i] = f"B{process_specie_string(_primary(site))}"

    # occupancy
    e_value: dict[int, str] = {}
    for k, c in enumerate(spec.get("occupancy") or []):
        kind = c["kind"]
        start, lower, upper = OCC_LIMITS[kind]
        name = f"occ{k}"
        params.append(f"PARAM={name}={start:.6f}_{lower}^{upper} //")
        if kind == "exchange":
            first, second = c["sites"]
            element_a, element_b = c["elements"]
            params.append(f"{name}b={name}*{sites[first]['multiplicity']}/{sites[second]['multiplicity']} //")
            e_value[first] = _e_value(element_a, element_b, name)
            e_value[second] = _e_value(element_b, element_a, f"{name}b")
        elif kind == "substitution":
            e_value[c["site"]] = _e_value(c["host"], c["dopant"], name)
        else:  # vacancy
            e_value[c["site"]] = f"({process_specie_string(c['element'])}({name}))"

    # site lines
    free_positions = set(spec.get("positions") or [])
    for i, n in enumerate(e_lines):
        line = lines[n]
        if i in free_positions:
            for axis in sites[i]["free"]:
                line = re.sub(
                    rf"(?<=\s){axis}=(-?\d+\.\d+)",
                    lambda m: f"PARAM={axis}={m.group(1)}_{float(m.group(1)) - COORD_RANGE:.6f}^{float(m.group(1)) + COORD_RANGE:.6f}",
                    line,
                    count=1,
                )
        if i in e_value:
            line = re.sub(r"^(\s*E=)\S+", lambda m: m.group(1) + e_value[i], line, count=1)
        if i in b_of_site:
            line = re.sub(r"TDS=\S+", f"TDS={b_of_site[i]}", line, count=1)
        lines[n] = line

    lines[e_lines[0] : e_lines[0]] = params
    return "\n".join(lines)
