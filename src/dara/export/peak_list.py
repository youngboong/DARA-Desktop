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
    a neighbour within 0.4 degrees gets no FWHM ("overlapped"). For a lab anode the FWHM is
    measured on the Kα2-stripped phase curve: the Kα1 FWHM. Rows of one peak stay together,
    so the order is by 2theta of the first reflection of each peak. ``hkl_database_setting``
    gives the indices in the database setting for a phase refined in a subgroup setting (see
    ``dara.setting_cell``; computed here when ``setting_transforms`` is not given).
    """
    from dara.export.pattern_treatment import kalpha_pair, strip_kalpha2
    from dara.setting_cell import setting_transforms as find_transforms, transform_hkl

    transforms = find_transforms(refinement) if setting_transforms is None else setting_transforms
    peaks = getattr(refinement, "peak_data", None)
    if peaks is None or len(peaks) == 0:
        return []
    x = np.asarray(refinement.plot_data.x, dtype=float)
    structs = refinement.plot_data.structs
    lam = wavelength_angstrom(wavelength)
    kalpha2 = kalpha_pair(wavelength)
    maximum = float(peaks["intensity"].max())
    rows: list[dict] = []
    for phase, table in peaks.groupby("phase", sort=False):
        table = table.rename(columns={"2theta": "tt"}).sort_values("tt")
        lattice = _as_lattice((lattice_by_phase or {}).get(phase)) if lattice_by_phase is not None \
            else _lattice_of(refinement, phase)
        curve = np.asarray(structs.get(phase, []), dtype=float)
        if kalpha2 is not None and len(curve) == len(x):  # FWHM of the Kα1 profile, not of the doublet
            curve = strip_kalpha2(x, curve, wavelength)
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


def peak_csv_bytes(rows: list[dict], columns=COLUMNS) -> bytes:
    """CSV of ``rows`` (UTF-8 with BOM, header = ``columns``)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(key) is None else row[key] for key in columns])
    return buffer.getvalue().encode("utf-8-sig")


OBSERVED_COLUMNS = (
    "two_theta_deg", "d_angstrom", "intensity", "intensity_rel_percent",
    "fwhm_deg", "fwhm_esd_deg", "eta", "fit_note", "instrument_fwhm_deg",
    "phase", "phase_label", "hkl", "delta_two_theta_deg", "size_nm", "size_note",
)
ASSIGN_TOLERANCE_DEG = 0.1  # smallest assignment window; a broad calculated peak widens it to its FWHM
BELOW_RESOLUTION = "below instrument resolution"


def corrected_fwhm(fwhm, eta, instrument_fwhm=None):
    """Sample FWHM (°) after removing the instrument FWHM ``b``: ``sqrt(B² − b²)`` for a
    Gaussian-like peak (η < 0.5), ``B − b`` otherwise; ``None`` when ``B ≤ 1.1·b``."""
    if fwhm is None:
        return None
    if not instrument_fwhm:
        return fwhm
    if fwhm <= 1.1 * instrument_fwhm:
        return None
    return float(np.sqrt(fwhm**2 - instrument_fwhm**2)) if (eta or 0) < 0.5 else fwhm - instrument_fwhm


def observed_peak_rows(peaks, reflections: list[dict], *, wavelength, profiles=None, k: float = 0.89,
                       instrument_fwhm=None) -> list[dict]:
    """One row per observed peak (EFLECH ``2theta``/``intensity`` frame), ordered by 2theta.

    ``intensity`` is EFLECH's integrated peak intensity; ``d`` comes from Bragg's law with the
    Kα1 wavelength. Each peak is assigned to the nearest reflection of ``reflections`` (rows of
    ``peak_rows``) when it lies within max(0.1°, FWHM of that reflection's calculated peak):
    phase, hkl and ``delta_two_theta_deg`` (observed − calculated). Otherwise those stay empty
    (unassigned). ``profiles`` (``peak_profile.fit_peak_profiles`` results in the frame's row
    order) give the Kα1 FWHM of each peak; ``size_nm`` is the Scherrer estimate
    ``k·λ / (β·cos θ)`` with β the FWHM minus ``instrument_fwhm`` (``corrected_fwhm``): a
    constant in degrees or a function of 2θ (the instrument's FWHM curve).
    """
    lam = wavelength_angstrom(wavelength)
    widths, width = [], None
    for row in reflections:  # rows of one peak carry its FWHM on the first row only
        if row.get("fwhm_note") != "same peak as row above":
            width = row.get("fwhm_deg")
        widths.append(width or 0.0)
    angles = np.array([row["two_theta_deg"] for row in reflections], dtype=float)
    fits = list(profiles) if profiles is not None else [None] * len(peaks)
    observed = sorted(zip(map(float, peaks["2theta"]), map(float, peaks["intensity"]), fits), key=lambda item: item[0])
    maximum = max((value for _, value, _ in observed), default=0.0)
    rows = []
    for two_theta, intensity, fit in observed:
        fit = fit or {}
        fwhm, eta = fit.get("fwhm_deg"), fit.get("eta")
        b = instrument_fwhm(two_theta) if callable(instrument_fwhm) else instrument_fwhm
        beta = corrected_fwhm(fwhm, eta, b)
        row = {
            "two_theta_deg": round(two_theta, 4),
            "d_angstrom": round(lam / (2 * np.sin(np.radians(two_theta / 2))), 5) if lam else None,
            "intensity": round(intensity, 3),
            "intensity_rel_percent": round(100 * intensity / maximum, 2) if maximum > 0 else None,
            "fwhm_deg": None if fwhm is None else round(fwhm, 4),
            "fwhm_esd_deg": None if fit.get("fwhm_esd_deg") is None else round(fit["fwhm_esd_deg"], 4),
            "eta": None if eta is None else round(eta, 3),
            "fit_note": fit.get("fit_note") if profiles is not None else "not fitted",
            "instrument_fwhm_deg": round(b, 4) if b else None,
            "phase": None, "phase_label": None, "hkl": None, "delta_two_theta_deg": None,
            "size_nm": round(scherrer_nm(beta, two_theta, lam, k), 1) if beta and lam else None,
            "size_note": BELOW_RESOLUTION if fwhm is not None and beta is None else None,
        }
        if angles.size:
            nearest = int(np.argmin(np.abs(angles - two_theta)))
            delta = two_theta - angles[nearest]
            if abs(delta) <= max(ASSIGN_TOLERANCE_DEG, widths[nearest]):
                match = reflections[nearest]
                row.update(
                    phase=match["phase"], phase_label=match["phase_label"],
                    hkl=match.get("hkl_database_setting") or f"{match['h']} {match['k']} {match['l']}",
                    delta_two_theta_deg=round(float(delta), 4),
                )
        rows.append(row)
    return rows


def scherrer_nm(beta_deg, two_theta, lam_angstrom, k):
    """Scherrer size (nm): ``k·λ / (β·cos θ)``, β in degrees 2θ converted to radians."""
    return k * lam_angstrom / 10 / (np.radians(beta_deg) * np.cos(np.radians(two_theta / 2)))


WH_MIN_PEAKS = 4
WH_MIN_SPAN_DEG = 30.0
WH_MIN_R2 = 0.5
# Sample broadening never narrows with angle: a falling line is instrument broadening left in.
NEGATIVE_SLOPE = "Width falls with angle: instrument broadening is not removed (enter the instrument FWHM or use the Rietveld value)"


def williamson_hall(rows: list[dict], *, wavelength, k: float = 0.89, instrument_fwhm=None) -> list[dict]:
    """Williamson-Hall fit per phase of ``observed_peak_rows`` output (classical FWHM form).

    Points are the assigned peaks with a fitted FWHM: ``x = 4·sin θ`` and ``y = β·cos θ`` (β the
    instrument-corrected FWHM in radians, ``instrument_fwhm`` as in ``observed_peak_rows``;
    ``y_err`` from the FWHM esd). A least-squares line
    ``y = k·λ/D + ε·x`` is fitted when a phase has at least 4 such peaks spread over at least
    30° 2θ: ``size_nm = D``, ``strain_percent = 100·ε`` (0 when the slope is negative), R² and
    the standard errors of both. ``reliable`` is False without a fit, with R² below 0.5, an
    intercept that is not positive, a negative slope or a size uncertainty not smaller than the size; ``note``
    then says why.
    """
    lam_nm = (wavelength_angstrom(wavelength) or 0) / 10
    phases: dict[str, dict] = {}
    for row in rows:
        if not row.get("phase"):
            continue
        entry = phases.setdefault(row["phase"], {
            "phase": row["phase"], "phase_label": row.get("phase_label"), "points": [], "unresolved": 0,
        })
        fwhm, eta = row.get("fwhm_deg"), row.get("eta")
        b = instrument_fwhm(row["two_theta_deg"]) if callable(instrument_fwhm) else instrument_fwhm
        beta = corrected_fwhm(fwhm, eta, b)
        if not beta or not lam_nm:
            entry["unresolved"] += 1
            continue
        theta = np.radians(row["two_theta_deg"] / 2)
        slope_of_correction = fwhm / beta if b and (eta or 0) < 0.5 else 1.0
        entry["points"].append({
            "hkl": row.get("hkl"), "two_theta": row["two_theta_deg"],
            "x": float(4 * np.sin(theta)), "y": float(np.radians(beta) * np.cos(theta)),
            "y_err": float(np.radians(row.get("fwhm_esd_deg") or 0) * slope_of_correction * np.cos(theta)),
        })
    output = []
    for entry in phases.values():
        points = entry["points"]
        entry.update(peaks_used=len(points), size_nm=None, size_esd_nm=None, strain_percent=None,
                     strain_esd_percent=None, r_squared=None, intercept=None, slope=None, note=None, reliable=False)
        output.append(entry)
        angles = [point["two_theta"] for point in points]
        if len(points) < WH_MIN_PEAKS:
            entry["note"] = f"{len(points)} usable peak{'' if len(points) == 1 else 's'}; at least {WH_MIN_PEAKS} are needed"
            continue
        if max(angles) - min(angles) < WH_MIN_SPAN_DEG:
            entry["note"] = f"usable peaks span {max(angles) - min(angles):.1f}° 2θ; at least {WH_MIN_SPAN_DEG:.0f}° is needed"
            continue
        xs = np.array([point["x"] for point in points])
        ys = np.array([point["y"] for point in points])
        design = np.column_stack((np.ones_like(xs), xs))
        (intercept, slope), *_ = np.linalg.lstsq(design, ys, rcond=None)
        residual = ys - design @ (intercept, slope)
        covariance = (residual @ residual / (len(xs) - 2)) * np.linalg.inv(design.T @ design)
        se_intercept, se_slope = np.sqrt(np.clip(np.diag(covariance), 0, None))
        total = np.sum((ys - ys.mean()) ** 2)
        r_squared = float(1 - residual @ residual / total) if total > 0 else None
        entry.update(intercept=float(intercept), slope=float(slope), r_squared=r_squared)
        gate = []
        if r_squared is not None and r_squared < WH_MIN_R2:
            gate.append(f"Scatter too large for a Williamson–Hall estimate (R² {r_squared:.2f})")
        if intercept > 0:
            entry["size_nm"] = float(k * lam_nm / intercept)
            entry["size_esd_nm"] = float(k * lam_nm * se_intercept / intercept**2)
            if entry["size_esd_nm"] >= entry["size_nm"]:
                gate.append("Size uncertainty exceeds the value")
        else:
            gate.append("no size broadening resolved")
        if slope >= 0:
            entry["strain_percent"] = float(100 * slope)
            entry["strain_esd_percent"] = float(100 * se_slope)
        else:
            entry["strain_percent"] = 0.0
            gate.append(NEGATIVE_SLOPE)
        entry["reliable"] = not gate
        entry["note"] = "; ".join(gate) or None
    return output
