"""Name helpers for reference CIFs (phase names such as ``Fe3O4_227_(cod_9002316)-0``)."""

from __future__ import annotations


def cif_stem(cif) -> str:
    """File name of a CIF (``Cif`` object or string) without ``.cif``.

    Only a trailing ``.cif`` is removed. ``Path.stem`` would also cut a dot inside the
    phase name, e.g. ``Na12.67P4O64.67_165_(cod_9007527)-0`` -> ``Na12.67P4O64``.
    """
    name = str(getattr(cif, "filename", cif) or "")
    return name[:-4] if name.lower().endswith(".cif") else name


def strip_none(name) -> str:
    """Drop the ``-None`` suffix of names made for a missing energy above hull."""
    text = str(name or "")
    return text[:-5] if text.endswith("-None") else text
