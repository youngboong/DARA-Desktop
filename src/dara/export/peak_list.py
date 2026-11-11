"""Peak list of a refinement: reflections with d, intensity and FWHM (design 2.3 of round-2026-11)."""

from __future__ import annotations

import csv
import io
import logging
import re

import numpy as np

logger = logging.getLogger("dara.export")

COLUMNS = (
    "phase", "phase_label", "h", "k", "l", "two_theta_deg", "d_angstrom", "intensity",
    "intensity_rel_phase_percent", "intensity_rel_all_percent", "fwhm_deg", "fwhm_note",
    "d_source", "hkl_database_setting",
)
GROUP_GAP_DEG = 0.02  # reflections of one phase closer than this form one peak
OVERLAP_DEG = 0.4  # a neighbouring peak this close makes the FWHM unmeasurable
_WINDOW_DEG = 0.15  # search window of the apex around the reflection angle
_WALK_LIMIT_DEG = 2.0


def wavelength_angstrom(value) -> float | None:
    """Wavelength in Å of a DARA setting: a lab anode name, or a number in nm (synchrotron)."""
    if value is None:
        return None
    try:
        return float(value) * 10
    except (TypeError, ValueError):
        pass
    from dara.server.api_router import LAB_WAVELENGTHS_ANGSTROM

    return LAB_WAVELENGTHS_ANGSTROM.get(str(value).strip().capitalize())


def _fwhm(x: np.ndarray, curve: np.ndarray, center: float) -> float | None:
    """Full width at half maximum (linear interpolation) of the peak of ``curve`` near ``center``."""
    idx = np.flatnonzero(np.abs(x - center) <= _WINDOW_DEG)
    if idx.size == 0:
        return None
    apex = idx[np.argmax(curve[idx])]
    half = curve[apex] / 2
    if not curve[apex] > 0:
        return None
    left = apex
    while left > 0 and curve[left] > half and x[apex] - x[left] < _WALK_LIMIT_DEG:
        left -= 1
    right = apex
    while right < len(x) - 1 and curve[right] > half and x[right] - x[apex] < _WALK_LIMIT_DEG:
        right += 1
    if curve[left] > half or curve[right] > half:
        return None
    x_left = np.interp(half, [curve[left], curve[left + 1]], [x[left], x[left + 1]])
    x_right = np.interp(half, [curve[right], curve[right - 1]], [x[right], x[right - 1]])
    return float(x_right - x_left)


def _label(name: str) -> str:
    from dara.plot import display_formula_of

    shown = display_formula_of(name)
    if shown:
        return shown
    found = re.match(r"^(.+?)_\d+_\(", name)
    return found.group(1) if found else name


def _lattice_of(refinement, phase: str):
    try:
        return refinement.lst_data.phases_results[phase].get_structure().lattice
    except Exception:
        return None


def _as_lattice(value):
    if value is None or hasattr(value, "d_hkl"):
        return value
    from pymatgen.core import Lattice

    return Lattice.from_parameters(*(float(value[k]) for k in ("a", "b", "c", "alpha", "beta", "gamma")))


def peak_rows(refinement, *, wavelength, lattice_by_phase: dict | None = None, setting_transforms=None) -> list[dict]:
    """One row per reflection of ``refinement.peak_data``, ordered by 2theta (see ``COLUMNS``).

    ``wavelength`` is a lab anode name or a number in nm. ``lattice_by_phase`` maps a phase name
    to its refined lattice (``Lattice`` or a dict with a, b, c in Å and alpha, beta, gamma in
    degrees); without it the lattice is read from the listing, and ``d`` comes from Bragg's law
    (``d_source = "bragg"``) for phases that have none. Reflections of one phase closer than
    0.02 degrees share one FWHM (first row; the others say "same peak as row above"). A peak with
    a neighbour within 0.4 degrees gets no FWHM ("overlapped"). Rows of one peak stay together,
    so the order is by 2theta of the first reflection of each peak. ``hkl_database_setting``
    gives the indices in the database setting for a phase refined in a subgroup setting (see
    ``dara.setting_cell``; computed here when ``setting_transforms`` is not given).
    """
    from dara.setting_cell import setting_transforms as find_transforms, transform_hkl

    transforms = find_transforms(refinement) if setting_transforms is None else setting_transforms
    peaks = getattr(refinement, "peak_data", None)
    if peaks is None or len(peaks) == 0:
        return []
    x = np.asarray(refinement.plot_data.x, dtype=float)
    structs = refinement.plot_data.structs
    lam = wavelength_angstrom(wavelength)
    maximum = float(peaks["intensity"].max())
    rows: list[dict] = []
    for phase, table in peaks.groupby("phase", sort=False):
        table = table.rename(columns={"2theta": "tt"}).sort_values("tt")
        lattice = _as_lattice((lattice_by_phase or {}).get(phase)) if lattice_by_phase is not None \
            else _lattice_of(refinement, phase)
        curve = np.asarray(structs.get(phase, []), dtype=float)
        phase_max = float(table["intensity"].max())
        label = _label(phase)
        groups: list[list] = []
        for record in table.itertuples():
            if groups and record.tt - groups[-1][-1].tt <= GROUP_GAP_DEG:
                groups[-1].append(record)
            else:
                groups.append([record])
        for number, group in enumerate(groups):
            first, last = group[0].tt, group[-1].tt
            near = (number > 0 and first - groups[number - 1][-1].tt < OVERLAP_DEG) or (
                number + 1 < len(groups) and groups[number + 1][0].tt - last < OVERLAP_DEG
            )
            width = note = None
            if near:
                note = "overlapped"
            elif len(curve) == len(x):
                width = _fwhm(x, curve, (first + last) / 2)
                note = None if width is not None else "not measurable"
            else:
                note = "not measurable"
            for member, record in enumerate(group):
                two_theta = float(record.tt)
                if lattice is not None:
                    d_value, source = float(lattice.d_hkl((record.h, record.k, record.l))), "lattice"
                elif lam:
                    d_value, source = lam / (2 * np.sin(np.radians(two_theta / 2))), "bragg"
                else:
                    d_value, source = None, "bragg"
                intensity = float(record.intensity)
                rows.append(
                    {
                        "phase": phase,
                        "phase_label": label,
                        "h": int(record.h), "k": int(record.k), "l": int(record.l),
                        "two_theta_deg": round(two_theta, 4),
                        "d_angstrom": None if d_value is None else round(float(d_value), 5),
                        "intensity": round(intensity, 3),
                        "intensity_rel_phase_percent": round(100 * intensity / phase_max, 2) if phase_max else None,
                        "intensity_rel_all_percent": round(100 * intensity / maximum, 2) if maximum else None,
                        "fwhm_deg": None if member or width is None else round(width, 4),
                        "fwhm_note": "same peak as row above" if member else note,
                        "d_source": source,
                        "hkl_database_setting": _hkl_text(transform_hkl((record.h, record.k, record.l), transforms[phase]))
                        if phase in transforms else None,
                        "_group": (first, phase),
                    }
                )
    rows.sort(key=lambda row: (row["_group"], row["two_theta_deg"]))
    for row in rows:
        del row["_group"]
    return rows


def _hkl_text(hkl) -> str | None:
    return None if hkl is None else " ".join(str(value) for value in hkl)


def peak_csv_bytes(rows: list[dict]) -> bytes:
    """CSV of ``rows`` (UTF-8 with BOM, header = ``COLUMNS``)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(["" if row.get(key) is None else row[key] for key in COLUMNS])
    return buffer.getvalue().encode("utf-8-sig")
