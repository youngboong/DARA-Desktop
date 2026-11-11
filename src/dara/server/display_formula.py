"""Conventional formulas of reference CIFs, for display only.

Phase names carry the formula of the database file stem, a reduced formula such as
``PH6NO4`` for ammonium dihydrogen phosphate. Practitioners read ``NH4H2PO4``, which most
CIFs state in ``_chemical_formula_structural``. The maps built here (reference name ->
display formula) only change what the UI shows; phase identities (formula + space group),
merging and every computation keep using the name.

A CIF value is used only when it is a parseable formula with the same composition as the
name's formula, so a wrong or unrelated tag can never relabel a phase. Mineral and common
names are not formulas; ``_chemical_name_common`` is used only when it is one.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("dara.server.display_formula")

FORMULA_TAGS = ("_chemical_formula_structural", "_chemical_name_common")
MAX_LENGTH = 40
_NAME_RE = re.compile(r"^(.+?)_(\d+)_\(")
_ALLOWED = re.compile(r"^[A-Za-z0-9().\[\]]+$")


def formula_of_name(name) -> str:
    """Formula token of a reference name such as ``Fe3O4_227_(cod_9002316)-0``."""
    text = str(name or "")
    match = _NAME_RE.match(text)
    return match.group(1) if match else text.split("_")[0]


def _clean(value) -> str | None:
    if value is None or isinstance(value, (list, tuple)):
        return None
    text = re.sub(r"\s+", "", str(value).strip().strip("'\""))
    if not text or text in {"?", "."} or len(text) > MAX_LENGTH or not _ALLOWED.match(text):
        return None
    text = re.sub(r"(?<!\d)\.(\d)", r"0.\1", text)  # H.572 -> H0.572
    # "La(MnO3)" / "La2O2(CO3)": a group without a multiplier needs no parentheses.
    unwrapped = re.sub(r"\(([A-Za-z0-9.]+)\)(?![\d.])", r"\1", text)
    # only an unwrapped group can leave the same element twice in a row (H4H2 is a real CIF form)
    return _merge_adjacent(unwrapped) if unwrapped != text else text


def _merge_adjacent(text: str) -> str:
    """``Fe2TiO3O2`` -> ``Fe2TiO5``: unwrapping a group can leave one element twice in a row."""
    if not re.fullmatch(r"(?:[A-Z][a-z]?\d*\.?\d*)+", text):
        return text
    merged: list[list] = []
    changed = False
    for element, count in re.findall(r"([A-Z][a-z]?)(\d*\.?\d*)", text):
        amount = float(count) if count else 1.0
        if merged and merged[-1][0] == element:
            merged[-1][1] += amount
            changed = True
        else:
            merged.append([element, amount])
    if not changed:
        return text
    return "".join(f"{el}{'' if amt == 1 else format(amt, 'g')}" for el, amt in merged)


def _same_composition(a: str, b: str) -> bool:
    try:
        from pymatgen.core import Composition

        first, second = Composition(a), Composition(b)
        if not first.num_atoms or not second.num_atoms:
            return False
        return first.fractional_composition.almost_equals(
            second.fractional_composition, rtol=0.0, atol=1e-3
        )
    except Exception:
        return False


def display_formula(fields, fallback: str) -> str | None:
    """Formula to show instead of ``fallback`` (the name's formula), or ``None`` to keep it."""
    if not fallback or not hasattr(fields, "get"):
        return None
    for tag in FORMULA_TAGS:
        value = _clean(fields.get(tag))
        if value and value != fallback and _same_composition(value, fallback):
            return value
    return None


def _cif_fields(cif) -> dict:
    """Tag -> value of the first data block of a parsed ``Cif``."""
    try:
        block = next(iter(cif.data.values()))
        return dict(block.data)
    except Exception:
        return {}


_TAG_LINE = re.compile(
    r"^[ \t]*(" + "|".join(re.escape(tag) for tag in FORMULA_TAGS) + r")[ \t]+(.+?)[ \t]*$",
    re.MULTILINE,
)


def _text_fields(text: str) -> dict:
    """The formula tags of a CIF text (single-line values; one data block)."""
    out: dict = {}
    for match in _TAG_LINE.finditer(str(text or "")):
        out.setdefault(match.group(1), match.group(2))
    return out


def cif_display_formulas(cifs) -> dict[str, str]:
    """``{filename: display formula}`` of parsed ``Cif`` objects (only the ones that differ)."""
    out: dict[str, str] = {}
    for cif in cifs or []:
        name = str(getattr(cif, "filename", "") or "")
        if not name or name in out:
            continue
        formula = display_formula(_cif_fields(cif), formula_of_name(name))
        if formula:
            out[name] = formula
    return out


def text_display_formulas(items) -> dict[str, str]:
    """Same for stored ``{"filename", "cif_text"}`` items (version records)."""
    out: dict[str, str] = {}
    for item in items or []:
        try:
            name = str(item.get("filename") or "")
            if not name or name in out:
                continue
            formula = display_formula(_text_fields(item.get("cif_text")), formula_of_name(name))
            if formula:
                out[name] = formula
        except Exception:
            continue
    return out


def document_display_formulas(document) -> dict[str, str]:
    """Display formulas of the task's own references (search candidates and input CIFs)."""
    try:
        from dara.server.selected_refinement_router import _references

        return cif_display_formulas(_references(document).values())
    except Exception:
        logger.debug("Display formulas of the document failed", exc_info=True)
        return {}


def version_display_formulas(task_uuid: str) -> dict[str, str]:
    """Display formulas of the references added by the task's versions."""
    try:
        from dara.server.versions import get_versions_store

        with get_versions_store() as store:
            records = list(store.query({"uuid": task_uuid}, properties=["cifs", "deleted"]))
        items = [item for record in records if not record.get("deleted")
                 for item in record.get("cifs") or []]
        return text_display_formulas(items)
    except Exception:
        logger.debug("Display formulas of the versions failed", exc_info=True)
        return {}


def task_display_formulas(document, task_uuid: str | None) -> dict[str, str]:
    out = dict(document_display_formulas(document)) if document is not None else {}
    if task_uuid:
        for name, formula in version_display_formulas(task_uuid).items():
            out.setdefault(name, formula)
    return out


_ID_KEYS = frozenset({"formula", "identity", "phase", "name", "ref", "reference", "key", "stem"})


def relabel_stored_text(value, mapping):
    """``value`` with each name formula in generated text replaced by its conventional one.

    For texts stored before display formulas existed (version summaries: cautions and
    verdict reasons). Reference names (``PH6NO4_19_(...)``) and identity fields are kept.
    """
    pairs = {}
    for name, formula in (mapping or {}).items():
        raw = formula_of_name(name)
        if raw and isinstance(formula, str) and formula and raw != formula:
            pairs.setdefault(raw, formula)
    if not pairs:
        return value
    alternatives = "|".join(re.escape(raw) for raw in sorted(pairs, key=len, reverse=True))
    pattern = re.compile(r"(?<![A-Za-z0-9_.])(" + alternatives + r")(?![A-Za-z0-9_(])")

    def walk(item, key=None):
        if isinstance(item, str):
            return item if key in _ID_KEYS else pattern.sub(lambda m: pairs[m.group(1)], item)
        if isinstance(item, dict):
            return {k: walk(v, k) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v, key) for v in item]
        return item

    return walk(value)
