"""Semi-quantitative phase analysis by the Reference Intensity Ratio (RIR, I/Ic) method.

I/Ic of a phase is the ratio of the integrated intensity of its strongest line to that of the
corundum 113 line in a 1:1 mixture by weight, for Cu Kα (Hubbard & Snyder 1988, Powder Diffr.
3, 74, "Measurement of I/Ic"). It is calculated from the crystal structure as calculated
patterns do (Hubbard, Evans & Smith 1976, J. Appl. Cryst. 9, 169). With all phases crystalline
and identified, Chung's normalised RIR ("matrix-flushing") method gives the weight fraction
``w_i = (I_i / RIR_i) / Σ_j (I_j / RIR_j)`` (Chung 1975, J. Appl. Cryst. 8, 17; Hubbard &
Snyder 1988 eq. 12).
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

CORUNDUM_CIF = Path(__file__).parent / "data" / "standards" / "Al2O3_1000032.cif"  # COD 1000032
CORUNDUM_LINE = (1, 1, 3)  # the I/Ic reference line (hexagonal setting)
LINE_MERGE_DEG = 0.1  # reflections closer than about one lab Kα1 FWHM are one line, as on calculated cards
TWO_THETA_MAX = 90.0
# International Tables f', f'' per anode, as shipped with BGMN ("cited from IT 1995")
ANOMALOUS_FILES = {"cu": "CU.ANO", "co": "CO.ANO", "cr": "CR.ANO", "fe": "FE.ANO", "mo": "MO.ANO"}
RIR_SOURCE = "calculated from structure"
CONTAMINATION = 0.05  # another phase's share of a peak's calculated intensity above which the peak is not clean
ASSIGN_WINDOW_DEG = 0.1
ASSUMPTIONS = [
    "All phases are crystalline and identified (Chung's normalised RIR method); amorphous or unidentified "
    "phases make every fraction too high.",
    "I/Ic is calculated from the refined structure: kinematic integrated intensity |F|²·m·LP/(V²·ρ) of the "
    "strongest line relative to corundum 113 (Hubbard & Snyder 1988), Bragg-Brentano LP without monochromator, "
    "no thermal motion (B = 0), anomalous dispersion from International Tables.",
    "The measured line intensity is the area of the pseudo-Voigt Kα1+Kα2 profile fit; a phase whose strongest "
    "line is not clean is scaled from its next strongest clean line by the calculated relative intensities.",
    "No preferred-orientation, microabsorption or extinction correction: the result is semi-quantitative.",
]


@lru_cache(maxsize=8)
def anomalous_factors(anode: str) -> dict[str, complex]:
    """``{element: f' + i·f''}`` of a lab anode from BGMN's tables; empty when unavailable."""
    name = ANOMALOUS_FILES.get(str(anode).strip().lower())
    path = Path(__file__).parent / "bgmn" / "BGMNwin" / name if name else None
    if path is None or not path.is_file():
        return {}
    factors = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith("%"):
            try:
                factors[parts[0].capitalize()] = complex(float(parts[1]), float(parts[2]))
            except ValueError:
                continue
    return factors


def radiation(wavelength) -> tuple[float, dict[str, complex]]:
    """``(λ in Å, anomalous factors)``: a lab anode name uses its Kα1; a number is in nm (DARA setting)."""
    from dara.utils import ANODE_LINES_A

    try:
        return float(wavelength) * 10, {}
    except (TypeError, ValueError):
        pass
    name = str(wavelength).strip().lower()
    if name not in ANODE_LINES_A:
        raise ValueError(f"Unknown wavelength {wavelength!r}")
    return ANODE_LINES_A[name][0], anomalous_factors(name)


def reflection_lines(structure, wavelength_a: float, *, anomalous=None, two_theta_max: float = TWO_THETA_MAX,
                     merge_deg: float = LINE_MERGE_DEG) -> dict:
    """Calculated powder lines of ``structure``: arrays ``two_theta``, ``intensity``, ``d`` and ``hkl``
    (strongest reflection of each line), ordered by 2θ.

    Integrated intensity per unit cell of a flat Bragg-Brentano specimen without monochromator,
    ``|F|²·m·(1 + cos²2θ)/(sin²θ·cosθ)`` (Hubbard & Snyder 1988 eq. 2 without the constant factors);
    every reciprocal-lattice point is summed, so the multiplicity is implicit. ``f = f0 + f' + i·f''``
    with ``f0`` from pymatgen's parameters (as its XRDCalculator) and ``anomalous`` = ``{element: f'+if''}``.
    Reflections closer than ``merge_deg`` are summed as one line.
    """
    from pymatgen.analysis.diffraction.xrd import ATOMIC_SCATTERING_PARAMS

    anomalous = anomalous or {}
    g_max = 2 * math.sin(math.radians(two_theta_max / 2)) / wavelength_a
    points = structure.lattice.reciprocal_lattice_crystallographic.get_points_in_sphere(
        [[0, 0, 0]], [0, 0, 0], g_max)
    hkl = np.array([np.round(point[0]) for point in points if point[1] > 1e-9], dtype=float).reshape(-1, 3)
    g = np.array([point[1] for point in points if point[1] > 1e-9], dtype=float)
    zs, coeffs, extra, coords, occupancies = [], [], [], [], []
    for site in structure:
        for species, occupancy in site.species.items():
            symbol = species.symbol
            zs.append(species.Z)
            coeffs.append(ATOMIC_SCATTERING_PARAMS[symbol])
            extra.append(anomalous.get(symbol, 0j))
            coords.append(site.frac_coords)
            occupancies.append(occupancy)
    zs, coeffs = np.array(zs, dtype=float), np.array(coeffs, dtype=float)
    extra, coords, occupancies = np.array(extra), np.array(coords, dtype=float), np.array(occupancies, dtype=float)
    intensity = np.empty(len(g))
    for start in range(0, len(g), 2000):  # bounded memory for large cells
        part = slice(start, start + 2000)
        s2 = (g[part] / 2) ** 2
        f0 = zs - 41.78214 * s2[:, None] * np.sum(coeffs[None, :, :, 0] * np.exp(-coeffs[None, :, :, 1] * s2[:, None, None]), axis=2)
        structure_factor = np.sum((f0 + extra) * occupancies * np.exp(2j * np.pi * hkl[part] @ coords.T), axis=1)
        intensity[part] = np.abs(structure_factor) ** 2
    theta = np.arcsin(np.clip(wavelength_a * g / 2, 0, 1))
    intensity *= (1 + np.cos(2 * theta) ** 2) / (np.sin(theta) ** 2 * np.cos(theta))
    two_theta = np.degrees(2 * theta)
    order = np.argsort(two_theta, kind="stable")
    two_theta, intensity, g, hkl = two_theta[order], intensity[order], g[order], hkl[order]
    keep = intensity > 1e-9 * intensity.max() if len(intensity) else intensity > 0  # drop extinct reflections
    two_theta, intensity, g, hkl = two_theta[keep], intensity[keep], g[keep], hkl[keep]
    if not len(two_theta):
        return {"two_theta": two_theta, "intensity": intensity, "d": g, "hkl": []}
    group = np.cumsum(np.r_[True, np.diff(two_theta) > merge_deg]) - 1
    total = np.bincount(group, intensity)
    names, d_values = [], []
    for number in range(len(total)):
        members = np.flatnonzero(group == number)
        top = members[intensity[members] >= 0.999 * intensity[members].max()]
        index = max(top, key=lambda i: tuple(hkl[i]))  # (1 1 1) rather than (-1 -1 1) among equivalents
        names.append(tuple(int(v) for v in hkl[index]))
        d_values.append(1 / g[index])
    return {
        "two_theta": np.bincount(group, intensity * two_theta) / total,
        "intensity": total,
        "d": np.array(d_values),
        "hkl": names,
    }


def diffracting_power(structure, intensity: float) -> float:
    """Line intensity per unit mass of phase: ``I/(V²·ρ)`` (Hubbard & Snyder 1988 eq. 1-2: intensity
    ∝ X/ρ · |F|²·m·LP/V²)."""
    return float(intensity) / (float(structure.volume) ** 2 * float(structure.density))


@lru_cache(maxsize=8)
def corundum_power(wavelength="Cu") -> float:
    """Diffracting power of the corundum 113 line (shipped COD 1000032 structure)."""
    from dara.cif import Cif

    corundum = Cif.from_file(CORUNDUM_CIF).to_structure()
    lam, anomalous = radiation(wavelength)
    lines = reflection_lines(corundum, lam, anomalous=anomalous)
    target = math.degrees(2 * math.asin(lam / (2 * corundum.lattice.d_hkl(CORUNDUM_LINE))))
    index = int(np.argmin(np.abs(lines["two_theta"] - target)))
    return diffracting_power(corundum, lines["intensity"][index])


def calculated_rir(structure, wavelength="Cu", two_theta_max: float = TWO_THETA_MAX) -> dict:
    """``{"rir", "lines", "strongest"}``: I/Ic of ``structure`` for ``wavelength`` (anode name or nm),
    its calculated lines (``reflection_lines``) and the index of the strongest one."""
    lam, anomalous = radiation(wavelength)
    lines = reflection_lines(structure, lam, anomalous=anomalous, two_theta_max=max(two_theta_max, TWO_THETA_MAX))
    if not len(lines["intensity"]):
        raise ValueError("no reflection in range")
    in_range = np.flatnonzero(lines["two_theta"] <= TWO_THETA_MAX)  # the card range defines "strongest"
    strongest = int(in_range[np.argmax(lines["intensity"][in_range])]) if len(in_range) else int(np.argmax(lines["intensity"]))
    rir = diffracting_power(structure, lines["intensity"][strongest]) / corundum_power(wavelength)
    return {"rir": rir, "lines": lines, "strongest": strongest}


def _bragg(d, lam):
    ratio = lam / (2 * d) if d else None
    return math.degrees(2 * math.asin(ratio)) if ratio is not None and ratio < 1 else None


def phase_line_intensity(phase: str, calc: dict, observed: list[dict], reflections: list[dict], lam: float,
                         label=lambda name: name) -> dict:
    """Measured intensity of the strongest line of ``phase`` from its observed peaks.

    ``observed``: ``peak_list.observed_peak_rows`` (with ``area`` of the profile fit); ``reflections``:
    ``peak_list.peak_rows`` of the fit. A peak is usable when its profile fit is clean and other phases
    add less than 5 % of its calculated intensity. Its calculated intensity is the sum of the phase's
    lines within half its FWHM (positions corrected by the fit's instrument shift of the assigned
    reflection); the usable peak with the largest calculated intensity is used and its area scaled by
    I(strongest)/I(peak) (Hubbard & Snyder 1988 eq. 6: relative intensities × RIR).
    """
    lines, strongest = calc["lines"], calc["strongest"]
    rejected = []
    best = None
    for row in observed:
        if row.get("phase") != phase:
            continue
        two_theta, fwhm = row["two_theta_deg"], row.get("fwhm_deg")
        reason = row.get("fit_note") or (None if row.get("area") else "no profile fit")
        window = max(ASSIGN_WINDOW_DEG, fwhm or 0)
        own = sum(r["intensity"] for r in reflections if r["phase"] == phase and abs(r["two_theta_deg"] - two_theta) <= window)
        others = {}
        for r in reflections:
            if r["phase"] != phase and abs(r["two_theta_deg"] - two_theta) <= window:
                others[r["phase"]] = others.get(r["phase"], 0) + r["intensity"]
        if not reason and sum(others.values()) > CONTAMINATION * (own + sum(others.values())):
            reason = "overlapped with " + ", ".join(sorted(label(name) for name in others))
        assigned = two_theta - (row.get("delta_two_theta_deg") or 0)
        mine = [r for r in reflections if r["phase"] == phase]
        shift = 0.0
        if mine:  # the fit's instrument shift at this reflection (its angle minus the lattice's Bragg angle)
            nearest = min(mine, key=lambda r: abs(r["two_theta_deg"] - assigned))
            bragg = _bragg(nearest.get("d_angstrom"), lam)
            shift = nearest["two_theta_deg"] - bragg if bragg is not None else 0.0
        inside = np.abs(lines["two_theta"] - (two_theta - shift)) <= max((fwhm or 0) / 2, LINE_MERGE_DEG / 2)
        calculated = float(lines["intensity"][inside].sum())
        if not reason and calculated <= 0:
            reason = "no calculated line"
        entry = {"row": row, "calculated": calculated, "has_strongest": bool(inside[strongest])}
        if reason:
            rejected.append(f"{row.get('hkl') or two_theta}: {reason}")
        elif best is None or calculated > best["calculated"]:
            best = entry
    if best is None:
        return {"intensity": None, "note": "no usable line" + (f" ({'; '.join(rejected[:4])})" if rejected else "")}
    row = best["row"]
    return {
        "intensity": row["area"] * lines["intensity"][strongest] / best["calculated"],
        "line_hkl": row.get("hkl"), "line_two_theta": row["two_theta_deg"], "line_intensity": row["area"],
        "scaled_from_strongest": not best["has_strongest"],
        "note": None if best["has_strongest"] else
        f"strongest line {' '.join(map(str, lines['hkl'][strongest]))} not usable; scaled by calculated relative intensity",
    }


RIR_COLUMNS = (
    "phase", "phase_label", "rir", "rir_source", "line_hkl", "line_two_theta", "line_intensity",
    "scaled_from_strongest", "weight_percent_rir", "weight_percent_rietveld", "note",
)


def rir_quantification(structures: dict, observed: list[dict], reflections: list[dict], *, wavelength,
                       rietveld: dict | None = None, labels: dict | None = None) -> dict:
    """RIR weight fractions of the phases of ``structures`` (``{phase: Structure or error text}``).

    Returns ``{"phases": [rows of RIR_COLUMNS + strongest_line_intensity], "complete", "assumptions"}``;
    a phase without I/Ic or usable line gets no fraction and makes the result incomplete (the
    others are then normalised among themselves).
    """
    labels = labels or {}
    label = lambda name: labels.get(name, name)  # noqa: E731
    lam, _ = radiation(wavelength)
    two_theta_max = max([row["two_theta_deg"] for row in observed] + [TWO_THETA_MAX]) + 1
    rows = []
    for phase, structure in structures.items():
        row = {"phase": phase, "phase_label": label(phase), "rir": None, "rir_source": RIR_SOURCE,
               "line_hkl": None, "line_two_theta": None, "line_intensity": None, "scaled_from_strongest": None,
               "strongest_line_intensity": None, "weight_percent_rir": None,
               "weight_percent_rietveld": (rietveld or {}).get(phase), "note": None}
        rows.append(row)
        if isinstance(structure, str) or structure is None:
            row["note"] = f"no structure: {structure}" if structure else "no structure"
            continue
        try:
            calc = calculated_rir(structure, wavelength, two_theta_max)
        except Exception as exc:  # noqa: BLE001 - one phase must not fail the table
            row["note"] = f"I/Ic not calculated: {exc}"
            continue
        row["rir"] = round(calc["rir"], 3)
        measured = phase_line_intensity(phase, calc, observed, reflections, lam, label)
        row["note"] = measured.pop("note")
        if measured["intensity"] is None:
            continue
        row.update(line_hkl=measured["line_hkl"], line_two_theta=measured["line_two_theta"],
                   line_intensity=round(measured["line_intensity"], 3),
                   scaled_from_strongest=measured["scaled_from_strongest"],
                   strongest_line_intensity=measured["intensity"])
    total = sum(row["strongest_line_intensity"] / row["rir"] for row in rows if row["strongest_line_intensity"])
    for row in rows:
        if row["strongest_line_intensity"] and total > 0:
            row["weight_percent_rir"] = round(float(100 * row["strongest_line_intensity"] / row["rir"] / total), 2)
            row["strongest_line_intensity"] = round(float(row["strongest_line_intensity"]), 3)
    return {
        "phases": rows,
        "complete": bool(rows) and all(row["weight_percent_rir"] is not None for row in rows),
        "assumptions": list(ASSUMPTIONS),
    }
