"""Unit-cell refinement from indexed peak positions, and 2θ calibration with an internal standard.

Cell refinement follows the classical least-squares procedure on peak positions of UnitCell
(T. J. B. Holland & S. A. T. Redfern, Mineral. Mag. 61 (1997) 65-77), HighScore "Refine Unit
Cell" and Jade "Cell Refinement": the observed 2θ of indexed peaks of one phase are fitted by
non-linear least squares with the free lattice parameters of its crystal system, optionally
with a 2θ zero shift and a specimen-displacement term. Peak-position model (degrees):

    2θ_calc = 2·asin(λ / 2d_hkl) + zero − (360/π)·(s/R)·cos θ

``zero`` is observed − true (a constant offset). The displacement term is the Bragg–Brentano
specimen-displacement aberration Δ2θ = −2·s·cos θ / R (radians) (H. P. Klug & L. E. Alexander,
X-ray Diffraction Procedures, 2nd ed. 1974, ch. 5; the same term as TOPAS
``Specimen_Displacement`` and BGMN ``EPS2`` = s/R, so ``s`` compares with the Rietveld
displacement DARA reports, ``s = EPS2·R``). 1/d² = hᵀ G* h with G* the inverse metric tensor.
e.s.d. are the square roots of the diagonal of (JᵀWJ)⁻¹ scaled by the reduced χ² (Σ w·Δ² /
(n − p)), as in UnitCell.

Internal-standard 2θ calibration (HighScore / Jade "theta calibration" with e.g. Si SRM 640):
Δ2θ(2θ) = observed − true of the standard's peaks, with the true positions from its certified
cell, is fitted with the same zero + displacement model and removed from every peak position.
"""

from __future__ import annotations

import numpy as np

KEYS = ("a", "b", "c", "alpha", "beta", "gamma")
OUTLIER_FACTOR = 3.0  # one pass: |Δ2θ| > 3 × r.m.s. ...
OUTLIER_MIN_DEG = 0.05  # ... and > 0.05° is removed
SAME_D_RELATIVE = 1e-5  # reflections whose d differ less than this share one position
CORRECTION_DISPLACEMENT_MIN_PEAKS = 4  # displacement is fitted to a standard only with
CORRECTION_DISPLACEMENT_MIN_SPAN_DEG = 40.0  # at least 4 peaks spanning at least 40° 2θ

# Free parameters and ties of each crystal system (a value: fixed angle; a key: equal to it).
SYSTEMS = {
    "cubic": (("a",), {"b": "a", "c": "a", "alpha": 90.0, "beta": 90.0, "gamma": 90.0}),
    "tetragonal": (("a", "c"), {"b": "a", "alpha": 90.0, "beta": 90.0, "gamma": 90.0}),
    "hexagonal": (("a", "c"), {"b": "a", "alpha": 90.0, "beta": 90.0, "gamma": 120.0}),
    "rhombohedral": (("a", "alpha"), {"b": "a", "c": "a", "beta": "alpha", "gamma": "alpha"}),
    "orthorhombic": (("a", "b", "c"), {"alpha": 90.0, "beta": 90.0, "gamma": 90.0}),
    "triclinic": (KEYS, {}),
}

# Certified lattice parameters of NIST line-position standards (22.5 °C).
CERTIFIED_CELLS = {
    # NIST SRM 640f certificate (2019); D. R. Black et al., Powder Diffr. 35 (2020) 156.
    "Si": ({"a": 5.431144}, "cubic", "NIST SRM 640f certified a = 5.431144 Å (22.5 °C)"),
    # NIST SRM 660c certificate; D. R. Black et al., Powder Diffr. 35 (2020) 17.
    "LaB6": ({"a": 4.156826}, "cubic", "NIST SRM 660c certified a = 4.156826 Å (22.5 °C)"),
}


class CellRefinementError(ValueError):
    """The peaks cannot determine the requested parameters; the message says why."""


def crystal_system(spacegroup_number: int | None, start_cell: dict) -> str:
    """Crystal system for the refinement of a cell in space group ``spacegroup_number``;
    trigonal groups use hexagonal axes unless ``start_cell`` is the rhombohedral setting.
    Without a number the system is read from the cell's metric."""
    n = int(spacegroup_number or 0)
    if n >= 195:
        return "cubic"
    if n >= 168:
        return "hexagonal"
    if n >= 143:
        return "hexagonal" if abs(start_cell["gamma"] - 120) < 0.01 else "rhombohedral"
    if n >= 75:
        return "tetragonal"
    if n >= 16:
        return "orthorhombic"
    if n >= 3:
        return "monoclinic"
    if n >= 1:
        return "triclinic"
    a, b, c, al, be, ga = (start_cell[k] for k in KEYS)
    right = [abs(v - 90) < 1e-3 for v in (al, be, ga)]
    if all(right):
        if abs(a - b) < 1e-4 and abs(b - c) < 1e-4:
            return "cubic"
        return "tetragonal" if abs(a - b) < 1e-4 else "orthorhombic"
    if right[0] and right[1] and abs(ga - 120) < 1e-3 and abs(a - b) < 1e-4:
        return "hexagonal"
    if sum(right) == 2:
        return "monoclinic"
    return "triclinic"


def _system(system: str, start_cell: dict):
    if system == "monoclinic":  # the unique axis is the one whose angle is not 90°
        angle = max(("alpha", "beta", "gamma"), key=lambda k: abs(start_cell[k] - 90))
        return ("a", "b", "c", angle), {k: 90.0 for k in ("alpha", "beta", "gamma") if k != angle}
    if system == "trigonal":
        system = "hexagonal" if abs(start_cell["gamma"] - 120) < 0.01 else "rhombohedral"
    if system not in SYSTEMS:
        raise CellRefinementError(f"Unknown crystal system {system!r}")
    return SYSTEMS[system]


def full_cell(free: dict, ties: dict, start_cell: dict) -> dict:
    cell = {**start_cell, **free}
    for key, tie in ties.items():
        cell[key] = float(tie) if isinstance(tie, float) else cell[tie]
    return cell


def inverse_d_squared(hkls, cell: dict) -> np.ndarray:
    """1/d² = hᵀ G* h for Miller indices ``hkls`` (n × 3) of ``cell`` (Å, degrees)."""
    a, b, c = cell["a"], cell["b"], cell["c"]
    ca, cb, cg = (np.cos(np.radians(cell[k])) for k in ("alpha", "beta", "gamma"))
    metric = np.array([[a * a, a * b * cg, a * c * cb], [a * b * cg, b * b, b * c * ca], [a * c * cb, b * c * ca, c * c]])
    h = np.asarray(hkls, dtype=float).reshape(-1, 3)
    return np.einsum("ij,jk,ik->i", h, np.linalg.inv(metric), h)


def bragg_two_theta(hkls, cell: dict, wavelength: float) -> np.ndarray:
    """2θ (degrees) of ``hkls`` without aberrations; NaN when the reflection is beyond 2θ = 180°."""
    sine = wavelength * np.sqrt(inverse_d_squared(hkls, cell)) / 2
    with np.errstate(invalid="ignore"):
        return np.degrees(2 * np.arcsin(np.where(sine <= 1, sine, np.nan)))


def volume(cell: dict) -> float:
    ca, cb, cg = (np.cos(np.radians(cell[k])) for k in ("alpha", "beta", "gamma"))
    return float(cell["a"] * cell["b"] * cell["c"] * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg))


def displacement_shift(two_theta, value, radius_mm):
    """Δ2θ (degrees) of a specimen displacement ``value`` (mm with ``radius_mm``, else s/R)."""
    ratio = value / radius_mm if radius_mm else value
    return -np.degrees(2 * ratio * np.cos(np.radians(np.asarray(two_theta, dtype=float) / 2)))


def _least_squares(names, x0, model, observed, sigma, extra=2):
    """Weighted non-linear least squares; ``(values, covariance, calculated)``.

    Refused when there are fewer observations than parameters + ``extra`` or a parameter is
    not determined (design matrix singular)."""
    from scipy.optimize import least_squares

    n, p = len(observed), len(names)
    if n < p + extra:
        raise CellRefinementError(
            f"{n} usable peak{'s' if n != 1 else ''} for {p} parameter{'s' if p != 1 else ''} "
            f"({', '.join(names)}); at least {p + extra} are needed")
    fit = least_squares(lambda x: (observed - model(x)) / sigma, np.asarray(x0, float), method="lm", x_scale="jac")
    jac = fit.jac
    norms = np.linalg.norm(jac, axis=0)
    missing = [name for name, norm in zip(names, norms) if not norm > 1e-9 * max(norms.max(), 1e-300)]
    if missing:
        raise CellRefinementError(
            f"The peaks do not determine {', '.join(missing)} (for example only hk0 reflections for c); "
            "the design matrix is singular")
    scaled = jac / norms
    if np.linalg.cond(scaled.T @ scaled) > 1e12:
        raise CellRefinementError(f"The parameters {', '.join(names)} are not independent for these peaks "
                                  "(the design matrix is singular)")
    chi2 = float(np.sum(fit.fun**2)) / (n - p)
    covariance = chi2 * np.linalg.inv(jac.T @ jac)
    return fit.x, covariance, model(fit.x)


def _value(value, variance):
    return {"value": float(value), "esd": float(np.sqrt(max(variance, 0.0)))}


def refine_cell(hkls, two_theta_obs, sigma, wavelength, crystal_system, start_cell, refine_zero=False,
                refine_displacement=False, radius_mm=None, reject_outliers=True, use=None) -> dict:
    """Least-squares lattice parameters from indexed Kα1 peak positions (see the module docstring).

    ``hkls``: Miller indices in the setting of ``start_cell`` ({a, b, c, alpha, beta, gamma}, Å
    and degrees: the start values and the values of parameters fixed by symmetry);
    ``two_theta_obs`` in degrees; ``sigma``: position e.s.d. (weights 1/σ²) or None for unit
    weights; ``crystal_system``: cubic, tetragonal, hexagonal, trigonal, rhombohedral,
    orthorhombic, monoclinic or triclinic. The displacement is in mm with ``radius_mm`` (the
    goniometer radius), else the dimensionless s/R. ``use`` (booleans) leaves peaks out of the
    fit; they are still listed with their calculated 2θ. With ``reject_outliers`` one pass removes
    peaks with |Δ2θ| > 3 × r.m.s. and > 0.05° and refits. Raises ``CellRefinementError``.

    Returns ``crystal_system``, ``parameters`` (free ones: {value, esd}), ``cell`` (all six),
    ``volume``, ``zero_deg``, ``displacement`` ({value, esd, unit: "mm" | "s/R"}) or None,
    ``peaks`` ([{hkl, two_theta_obs, two_theta_calc, delta, used}]), ``rms_deg``,
    ``peaks_used``, ``dof``, ``excluded`` (outliers removed), ``correlation_zero_displacement``.
    """
    hkls = np.asarray(hkls, dtype=float).reshape(-1, 3)
    observed = np.asarray(two_theta_obs, dtype=float)
    sigma = np.ones_like(observed) if sigma is None else np.asarray(sigma, dtype=float)
    if not np.all(sigma > 0):
        sigma = np.ones_like(observed)  # a missing or zero e.s.d. on any peak: unit weights for all
    start = {k: float(start_cell[k]) for k in KEYS}
    free, ties = _system(crystal_system, start)
    names = list(free) + (["zero"] if refine_zero else []) + (["displacement"] if refine_displacement else [])
    x0 = [start[k] for k in free] + [0.0] * (len(names) - len(free))

    def unpack(x):
        values = dict(zip(names, x))
        return full_cell({k: values[k] for k in free}, ties, start), values.get("zero", 0.0), values.get("displacement", 0.0)

    def model_for(rows):
        def model(x):
            cell, zero, shift = unpack(x)
            tt = bragg_two_theta(hkls[rows], cell, wavelength)
            return tt + zero + displacement_shift(tt, shift, radius_mm)
        return model

    used = np.ones(len(observed), dtype=bool) if use is None else np.asarray(use, dtype=bool).copy()
    x, covariance, _ = _least_squares(names, x0, model_for(used), observed[used], sigma[used])
    excluded = []
    if reject_outliers:
        delta = observed - model_for(slice(None))(x)
        rms = float(np.sqrt(np.mean(delta[used] ** 2)))
        outliers = used & (np.abs(delta) > OUTLIER_FACTOR * rms) & (np.abs(delta) > OUTLIER_MIN_DEG)
        if outliers.any() and (used & ~outliers).sum() >= len(names) + 2:
            used &= ~outliers
            excluded = [int(i) for i in np.flatnonzero(outliers)]
            x, covariance, _ = _least_squares(names, x, model_for(used), observed[used], sigma[used])
    cell, zero, shift = unpack(x)
    calculated = model_for(slice(None))(x)
    delta = observed - calculated
    index = {name: i for i, name in enumerate(names)}
    # volume e.s.d. from its numerical gradient with respect to the cell parameters
    gradient = np.zeros(len(names))
    for name in free:
        step = 1e-6 * max(abs(x[index[name]]), 1.0)
        bumped = x.copy()
        bumped[index[name]] += step
        gradient[index[name]] = (volume(unpack(bumped)[0]) - volume(cell)) / step
    return {
        "crystal_system": crystal_system,
        "parameters": {k: _value(x[index[k]], covariance[index[k], index[k]]) for k in free},
        "cell": cell,
        "volume": _value(volume(cell), gradient @ covariance @ gradient),
        "zero_deg": _value(zero, covariance[index["zero"], index["zero"]]) if refine_zero else None,
        "displacement": {**_value(shift, covariance[index["displacement"], index["displacement"]]),
                         "unit": "mm" if radius_mm else "s/R"} if refine_displacement else None,
        "peaks": [
            {"hkl": [int(v) for v in hkls[i]], "two_theta_obs": float(observed[i]),
             "two_theta_calc": float(calculated[i]), "delta": float(delta[i]), "used": bool(used[i])}
            for i in range(len(observed))
        ],
        "rms_deg": float(np.sqrt(np.mean(delta[used] ** 2))),
        "peaks_used": int(used.sum()),
        "dof": int(used.sum() - len(names)),
        "excluded": excluded,
        "correlation_zero_displacement": float(
            covariance[index["zero"], index["displacement"]]
            / np.sqrt(covariance[index["zero"], index["zero"]] * covariance[index["displacement"], index["displacement"]]))
        if refine_zero and refine_displacement else None,
    }


def fit_two_theta_correction(hkls, two_theta_obs, sigma, wavelength, true_cell, radius_mm=None,
                             refine_displacement=None) -> dict:
    """Zero shift (+ specimen displacement) from a standard's peaks at their true positions.

    ``true_cell``: the standard's certified/reference cell in the setting of ``hkls``.
    ``refine_displacement`` None: fitted when there are at least 4 peaks spanning at least 40°.
    At least 2 peaks are needed. Returns ``zero_deg``, ``displacement`` (as in ``refine_cell``)
    or None, ``peaks`` ([{hkl, two_theta_obs, two_theta_true, delta}], delta = observed −
    true), ``rms_deg`` (of the fit), ``dof``.
    """
    hkls = np.asarray(hkls, dtype=float).reshape(-1, 3)
    observed = np.asarray(two_theta_obs, dtype=float)
    if len(observed) < 2:
        raise CellRefinementError(f"{len(observed)} usable standard peak{'' if len(observed) == 1 else 's'}; "
                                  "at least 2 are needed")
    sigma = np.ones_like(observed) if sigma is None else np.asarray(sigma, dtype=float)
    if not np.all(sigma > 0):
        sigma = np.ones_like(observed)
    true = bragg_two_theta(hkls, {k: float(true_cell[k]) for k in KEYS}, wavelength)
    if refine_displacement is None:
        refine_displacement = (len(observed) >= CORRECTION_DISPLACEMENT_MIN_PEAKS
                               and np.ptp(observed) >= CORRECTION_DISPLACEMENT_MIN_SPAN_DEG)
    names = ["zero"] + (["displacement"] if refine_displacement else [])

    def model(x):
        return true + x[0] + (displacement_shift(true, x[1], radius_mm) if refine_displacement else 0.0)

    # a zero shift alone may come from 2 peaks (one degree of freedom)
    x, covariance, calculated = _least_squares(names, [0.0] * len(names), model, observed, sigma, extra=1)
    residual = observed - calculated
    return {
        "zero_deg": _value(x[0], covariance[0, 0]),
        "displacement": {**_value(x[1], covariance[1, 1]), "unit": "mm" if radius_mm else "s/R"}
        if refine_displacement else None,
        "peaks": [
            {"hkl": [int(v) for v in hkls[i]], "two_theta_obs": float(observed[i]), "two_theta_true": float(true[i]),
             "delta": float(observed[i] - true[i])}
            for i in range(len(observed))
        ],
        "rms_deg": float(np.sqrt(np.mean(residual**2))),
        "dof": len(observed) - len(names),
    }


def correct_two_theta(two_theta, model: dict, radius_mm=None) -> np.ndarray:
    """Observed 2θ with the fitted zero and displacement removed (the displacement term is
    evaluated at the observed angle; the error of that is second order in the shift)."""
    two_theta = np.asarray(two_theta, dtype=float)
    corrected = two_theta - model["zero_deg"]["value"]
    if model.get("displacement"):
        corrected = corrected - displacement_shift(two_theta, model["displacement"]["value"], radius_mm)
    return corrected


def select_peaks(rows: list[dict], reflections: list[dict], phase: str) -> list[dict]:
    """Observed peaks of ``phase`` for the cell refinement, each with ``used`` and ``reason``.

    ``rows``: ``peak_list.observed_peak_rows`` (with ``two_theta_fit`` / ``two_theta_esd_deg``);
    ``reflections``: ``peak_list.peak_rows`` (h, k, l in the refinement setting). A peak is used
    when its profile fit is clean, it is the only peak assigned to its reflection (else the
    closest one is used), and no reflection of another phase, nor one of the same phase with a
    different d, lies within half its FWHM (at least 0.02°) of that reflection. Reflections of
    equal d (cubic 333/511) are one position.
    """
    mine = [r for r in reflections if r["phase"] == phase]
    selected = []
    for row in rows:
        if row.get("phase") != phase or not mine:
            continue
        nearest = min(mine, key=lambda r: abs(r["two_theta_deg"] - row["two_theta_deg"]))
        entry = {"hkl": [nearest["h"], nearest["k"], nearest["l"]], "reflection": nearest,
                 "hkl_database_setting": nearest.get("hkl_database_setting"),
                 "two_theta_obs": row.get("two_theta_fit"), "sigma": row.get("two_theta_esd_deg"),
                 "used": True, "reason": None}
        selected.append(entry)
        if row.get("fit_note") or row.get("two_theta_fit") is None:
            entry.update(used=False, reason=f"profile fit: {row.get('fit_note') or 'not fitted'}")
            continue
        window = max(0.02, (row.get("fwhm_deg") or 0) / 2)
        others = [r for r in reflections if r is not nearest and abs(r["two_theta_deg"] - nearest["two_theta_deg"]) <= window]
        foreign = [r for r in others if r["phase"] != phase]
        d = nearest.get("d_angstrom")
        split = [r for r in others if r["phase"] == phase and (d is None or r.get("d_angstrom") is None
                                                                 or abs(r["d_angstrom"] - d) > SAME_D_RELATIVE * d)]
        if foreign:
            entry.update(used=False, reason=f"overlaps {foreign[0].get('phase_label') or foreign[0]['phase']} "
                                            f"{foreign[0]['h']} {foreign[0]['k']} {foreign[0]['l']}")
        elif split:
            entry.update(used=False, reason="overlapping reflections of this phase with different d: "
                         + ", ".join(f"{r['h']} {r['k']} {r['l']}" for r in [nearest, *split]))
    for entry in selected:  # one observed peak per reflection: the closest
        if not entry["used"]:
            continue
        rivals = [e for e in selected if e["used"] and e["reflection"] is entry["reflection"]]
        best = min(rivals, key=lambda e: abs(e["two_theta_obs"] - entry["reflection"]["two_theta_deg"]))
        if best is not entry:
            entry.update(used=False, reason="another peak is closer to this reflection")
    for entry in selected:
        del entry["reflection"]
    return selected
