"""Publication-style refined CIF of one phase (design 2.5 of round-2026-11)."""

from __future__ import annotations

import logging
import math
import re

import numpy as np

logger = logging.getLogger("dara.export")

_CELL = (
    ("a", "_cell_length_a", 10.0),
    ("b", "_cell_length_b", 10.0),
    ("c", "_cell_length_c", 10.0),
    ("alpha", "_cell_angle_alpha", 1.0),
    ("beta", "_cell_angle_beta", 1.0),
    ("gamma", "_cell_angle_gamma", 1.0),
)
_HELD = {"fixed", "fixed_reference"}


class CifNotExportable(ValueError):
    """Neither a refined nor a reference structure exists for the phase."""


def format_esd(value: float, esd: float | None, places: int = 5, digits: int | None = None) -> str:
    """``4.9341(2)``: value rounded to the e.s.d., which keeps 1 digit (2 when it starts with 1).

    ``digits`` forces the number of e.s.d. digits (``14.13(48)`` with 2). Without an e.s.d.
    (fixed value, or a value at its limit) the value is written with ``places`` decimals and
    no brackets.
    """
    if esd is None or not math.isfinite(esd) or esd <= 0:
        return f"{value:.{places}f}"
    mantissa, exponent = f"{esd:e}".split("e")
    keep = digits or (2 if mantissa[0] == "1" else 1)
    decimals = max(-int(exponent) + keep - 1, 0)
    return f"{value:.{decimals}f}({round(esd * 10 ** decimals)})"


def _pair(value):
    if isinstance(value, (tuple, list)):
        return (
            float(value[0]) if value and value[0] is not None else None,
            float(value[1]) if len(value) > 1 and value[1] is not None else None,
        )
    return (None, None) if value is None else (float(value), None)


def _database_id(name: str, database) -> tuple[str, str] | None:
    if database and database.get("id"):
        return str(database.get("source") or "cod").lower(), str(database["id"])
    found = re.search(r"\(cod_(\d+)\)", name)
    if found:
        return "cod", found.group(1)
    found = re.search(r"\((mp-\d+)\)", name)
    return ("mp", found.group(1)) if found else None


def _database_spacegroup(name: str, meta: dict) -> int | None:
    number = meta.get("database_spacegroup_number")
    if number is None:
        found = re.match(r"^[^_]+_(\d+)_", name)
        number = found.group(1) if found else None
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


def _reference_structure(reference_cif):
    if reference_cif is None:
        return None
    try:
        return reference_cif.to_structure()
    except Exception:
        try:
            from pymatgen.core import Structure

            return Structure.from_str(reference_cif.orig_string, fmt="cif")
        except Exception:
            return None


def _lattice_states(phase, meta: dict) -> dict[str, str]:
    """State of each lattice key the listing prints (``refined`` | ``at_limit`` | ``fixed``)."""
    given = meta.get("lattice_states") or {}
    fixed_spec = (meta.get("effective_params") or {}).get("lattice_range") == "fixed"
    states = {}
    for key, _, _ in _CELL:
        value, esd = _pair(getattr(phase, key, None))
        if value is None:
            continue
        states[key] = given.get(key) or (
            "refined" if esd is not None else "fixed" if fixed_spec else "at_limit"
        )
    return {key: state for key, state in states.items() if state != "dependent"}


def _match_position(ops, positions, element, xyz):
    """``(index, rotation)`` of the listing position that the CIF row ``xyz`` is an image of."""
    target = np.asarray(xyz, dtype=float)
    for index, position in enumerate(positions):
        if element and element not in position["species"]:
            continue
        for op in ops:
            delta = op.operate(position["xyz"]) - target
            if np.abs(delta - np.round(delta)).max() < 1e-3:
                return index, np.asarray(op.rotation_matrix, dtype=float)
    return None, None


def _bgmn_version(refinement) -> str:
    raw = getattr(getattr(refinement, "lst_data", None), "raw_lst", None) or ""
    found = re.search(r"BGMN version ([\d.]+)", raw)
    return found.group(1) if found else "4.2.22"


def _selection_text(selection) -> str:
    text = str(selection or "")
    return f"version {text.split(':', 1)[1]}" if text.startswith("version:") else text


def _loop_with(block, key):
    loops = block.loops.values() if isinstance(block.loops, dict) else block.loops
    return next((loop for loop in loops if key in loop), None)


def _write_structure(name, structure, refinement, phase, meta, *, from_reference: bool) -> str:
    from pymatgen.io.cif import CifWriter

    import dara
    from dara.export.peak_list import wavelength_angstrom
    from dara.plot import cached_spacegroup, spacegroup_symbol
    from dara.refinement_status import structure_results

    def write(symprec):
        writer = CifWriter(structure, symprec=symprec)
        return writer, next(iter(writer.cif_file.data.values()))

    writer, block = write(0.01)
    written_no = int(block.data["_symmetry_Int_Tables_number"])
    database_no = _database_spacegroup(name, meta)
    if database_no is not None and written_no != database_no:
        alt_writer, alt_block = write(0.1)
        if int(alt_block.data["_symmetry_Int_Tables_number"]) == database_no:
            writer, block, written_no = alt_writer, alt_block, database_no
    refined_no = int(getattr(phase, "spacegroup_no", None) or written_no)
    same_setting = refined_no == written_no and not from_reference

    # lattice with e.s.d.; the e.s.d. belong to the refined setting only
    states = _lattice_states(phase, meta)
    for key, cif_key, scale in _CELL:
        written = float(block.data[cif_key])
        value = esd = None
        if same_setting:
            for source in (key, "a") if key in ("b", "c") else (key,):
                number, error = _pair(getattr(phase, source, None))
                if number is not None and abs(number * scale - written) < 1e-3:
                    value, esd = number * scale, None if error is None else error * scale
                    break
        if value is not None and esd is not None and states.get(key, "refined") == "refined":
            block.data[cif_key] = format_esd(value, esd)
        else:
            block.data[cif_key] = f"{written:.{5 if scale > 1 else 3}f}"
    refined_cell = [key for key, state in states.items() if state == "refined"]
    limit_cell = [key for key, state in states.items() if state == "at_limit"]
    held_cell = not states or all(state in _HELD for state in states.values())

    # atom sites: B(iso) column and e.s.d. of refined coordinates
    positions = (getattr(phase, "atom_positions", None) or []) if same_setting else []
    spec = meta.get("structure_spec")
    result = None
    if spec and positions:
        try:
            result = structure_results(phase, spec, getattr(phase, "model_extra", None) or {})
        except Exception:
            logger.warning("Structure results of %s unavailable", name, exc_info=True)
    labels = block.data.get("_atom_site_label") or []
    symbols = block.data.get("_atom_site_type_symbol") or []
    coords = [block.data[f"_atom_site_fract_{axis}"] for axis in "xyz"]
    ops = cached_spacegroup(refined_no).symmetry_ops if positions else []
    b_column = []
    coords_refined = False
    for row in range(len(labels)):
        xyz = [float(column[row]) for column in coords]
        element = re.match(r"[A-Z][a-z]?", symbols[row] if row < len(symbols) else "")
        index, rotation = _match_position(ops, positions, element.group(0) if element else None, xyz)
        b_text = "1.000"
        if index is not None:
            listed = positions[index]["refined"]
            sigma = np.array([(listed.get(axis) or [0, None])[1] or 0.0 for axis in "xyz"])
            for axis in range(3):
                error = float(np.sqrt(((rotation[axis] * sigma) ** 2).sum()))
                if error > 0:
                    coords[axis][row] = format_esd(xyz[axis], error)
            coords_refined = coords_refined or bool(listed)
            if result:
                b_iso = result["sites"][index]["b_iso"]
                b_text = format_esd(b_iso["value"], b_iso["esd"], 3)
        b_column.append(b_text)
    block.data["_atom_site_B_iso_or_equiv"] = b_column
    loop = _loop_with(block, "_atom_site_occupancy") or _loop_with(block, "_atom_site_label")
    if loop is not None:
        loop.append("_atom_site_B_iso_or_equiv")

    # header items
    lst = getattr(refinement, "lst_data", None)
    rwp = meta.get("rwp")
    if rwp is None:
        rwp = getattr(lst, "rwp", None)
    rexp = meta.get("rexp")
    block.data["_audit_creation_method"] = f"DARA {dara.__version__} with BGMN {_bgmn_version(refinement)}"
    if meta.get("weight_percent") is not None:
        block.data["_pd_phase_mass_%"] = format_esd(
            float(meta["weight_percent"]), meta.get("weight_esd_percent"), 2, digits=2
        )
    if rwp is not None:
        block.data["_pd_proc_ls_prof_wR_factor"] = f"{float(rwp) / 100:.4f}"
    if rexp is not None:
        block.data["_pd_proc_ls_prof_wR_expected"] = f"{float(rexp) / 100:.4f}"
    angstrom = wavelength_angstrom(meta.get("wavelength"))
    if angstrom:
        block.data["_diffrn_radiation_wavelength"] = f"{angstrom:.5f}"
    database = _database_id(name, meta.get("database"))
    comment = ""
    if database and database[0] == "cod":
        block.data["_database_code_COD"] = database[1]
    elif database:
        comment = f"# Materials Project id: {database[1]}\n"

    # _refine_special_details
    extra = getattr(phase, "model_extra", None) or {}
    refined = ["scale"]
    if refined_cell:
        refined.insert(0, f"lattice parameters {', '.join(refined_cell)}")
    broadening = [k for k in ("B1", "k1", "k2") if _pair(extra.get(k))[1] is not None]
    if broadening:
        refined.append(f"peak broadening {', '.join(broadening)}")
    if _pair(getattr(lst, "EPS2", None))[1] is not None:
        refined.append("global sample displacement")
    held = []
    if limit_cell:
        noun = "parameter" if len(limit_cell) == 1 else "parameters"
        held.append(f"lattice {noun} {', '.join(limit_cell)} at the refinement limit (no e.s.d.)")
    if held_cell:
        held.append("lattice parameters")
    occupancy_refined = bool(spec and spec.get("occupancy"))
    thermal_refined = bool(spec and spec.get("thermal", "fixed") != "fixed")
    if coords_refined:
        refined.append("atomic coordinates")
    if occupancy_refined:
        refined.append("site occupancies")
    if thermal_refined:
        refined.append("B(iso)")
    unrefined = [
        text
        for text, done in (("atomic coordinates", coords_refined), ("site occupancies", occupancy_refined))
        if not done
    ]
    if unrefined:
        source = f"{database[0].upper()} {database[1]}" if database else "the reference structure"
        held.append(f"{' and '.join(unrefined)} of {source}")
    if not thermal_refined:
        held.append("B(iso) = 1.0 A^2 for all sites")

    def symbol(number):
        try:
            return spacegroup_symbol(number).replace("_", "")
        except ValueError:
            return "?"

    if refined_no != written_no:
        group = (
            f"Space group: refined in {symbol(refined_no)}, written in {symbol(written_no)} "
            "(database setting); lattice e.s.d. refer to the refined setting and are not given."
        )
    else:
        group = f"Space group: {symbol(written_no)} ({written_no})."
    origin = f"Refined with DARA (BGMN {_bgmn_version(refinement)})"
    if meta.get("task_id") is not None:
        origin += f", task {meta['task_id']}"
    if meta.get("selection"):
        origin += f", {_selection_text(meta['selection'])}"
    if rwp is not None:
        origin += f", Rwp {float(rwp):.2f} %"
    lines = [origin + ".", f"Refined: {'; '.join(refined)}.", f"Held at reference values: {'; '.join(held)}."]
    if result:
        lines.append("Structure parameters are indicative (laboratory X-ray data).")
    status = meta.get("refinement_status") or {}
    if status.get("level") in ("limited", "failed") and status.get("message"):
        lines.append(f"Refinement status: {status['level']}. {status['message']}")
    lines.append(group)
    # the multi-line text block is written by hand: CifBlock would quote it on one line
    return comment + str(writer).rstrip("\n") + "\n_refine_special_details\n;\n" + "\n".join(lines) + "\n;\n"


def phase_structure(name, refinement, reference_cif=None, meta=None):
    """``(structure, from_reference)`` of phase ``name``; ``ValueError`` carries the reason.

    The refined structure comes from the listing (the reference lattice fills the keys of a
    fixed lattice). When that fails and the lattice was held fixed, the reference structure
    is returned as is (``from_reference=True``).
    """
    phases = getattr(getattr(refinement, "lst_data", None), "phases_results", None) or {}
    phase = phases.get(name)
    reference = _reference_structure(reference_cif)
    fallback = None
    if reference is not None:
        fallback = {
            **dict(zip(("a", "b", "c"), reference.lattice.abc)),
            **dict(zip(("alpha", "beta", "gamma"), reference.lattice.angles)),
        }
    try:
        getter = getattr(phase, "get_structure", None)
        if getter:
            return getter(lattice_fallback=fallback), False
        return refinement.export_structure(name), False
    except Exception as exc:
        reason = f"refined structure unavailable ({type(exc).__name__}: {exc})".replace("\n", " ")
    if reference is not None and phase is not None and not _lattice_states(phase, dict(meta or {})):
        return reference, True
    raise ValueError(reason)


def refined_cif_text(name, refinement, *, meta=None, reference_cif=None) -> tuple[str, str]:
    """``(CIF text, folder)`` of phase ``name``; folder is ``refined_cifs`` or ``reference_cifs``.

    A phase that cannot be written as refined structure gets its unmodified reference CIF
    behind a ``# NOT REFINED`` header (folder ``reference_cifs``). Raises
    :class:`CifNotExportable` when not even a reference CIF exists.
    """
    meta = dict(meta or {})
    phases = getattr(getattr(refinement, "lst_data", None), "phases_results", None) or {}
    phase = phases.get(name)
    try:
        structure, from_reference = phase_structure(name, refinement, reference_cif, meta)
        text = _write_structure(name, structure, refinement, phase, meta, from_reference=from_reference)
        return text, "refined_cifs"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        if not isinstance(exc, ValueError):
            logger.warning("Refined CIF of %s could not be written", name, exc_info=True)
            reason = f"CIF export failed ({type(exc).__name__}: {exc})"
    reason = reason.replace("\n", " ")
    original = getattr(reference_cif, "orig_string", None)
    if not original:
        raise CifNotExportable(reason)
    header = f"# NOT REFINED by DARA: {reason}\n# Task {meta.get('task_id', '?')}, {meta.get('selection', '?')}\n"
    return header + original, "reference_cifs"
