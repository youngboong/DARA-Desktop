"""Single-peak profile fits of the measured pattern: Kα1 FWHM of each observed peak.

Each peak is a pseudo-Voigt (position, height, FWHM, η); for a lab anode it is a Kα1+Kα2
doublet (Kα2 at the Bragg-shifted angle, half the height, same FWHM and η). Peaks closer than
3 FWHM are fitted together in one window with a linear residual background.
"""

from __future__ import annotations

import numpy as np

from dara.export.pattern_treatment import KALPHA2_RATIO, kalpha_pair

START_FWHM_DEG = 0.15
CLUSTER_FWHM = 3  # peaks closer than this many FWHM are fitted together
WINDOW_DEG = 0.6
MAX_CLUSTER = 4
FWHM_BOUNDS_DEG = (0.02, 2.0)
POSITION_RANGE_DEG = 0.1
MAX_RELATIVE_ESD = 0.3
MERGED = "merged"  # a found peak closer than one FWHM to a stronger one: fitted as that peak


def pseudo_voigt(x, center, fwhm, eta):
    """Unit-height pseudo-Voigt: η·Lorentzian + (1 − η)·Gaussian of the same FWHM."""
    d = (np.asarray(x, dtype=float) - center) / fwhm
    return eta / (1 + 4 * d**2) + (1 - eta) * np.exp(-4 * np.log(2) * d**2)


def doublet(x, center, height, fwhm, eta, pair):
    """Kα1 peak at ``center`` plus, for a lab anode ``pair`` (λ1, λ2), its Kα2 companion."""
    y = height * pseudo_voigt(x, center, fwhm, eta)
    if pair is not None:
        shifted = np.degrees(2 * np.arcsin(min(np.sin(np.radians(center / 2)) * pair[1] / pair[0], 1.0)))
        y = y + KALPHA2_RATIO * height * pseudo_voigt(x, shifted, fwhm, eta)
    return y


def doublet_area(height, fwhm, eta, pair):
    """Integrated area of ``doublet``: the pseudo-Voigt area ``H·w·(η·π/2 + (1 − η)·sqrt(π/(4 ln 2)))``,
    times 1 + I(Kα2)/I(Kα1) for a lab anode."""
    area = height * fwhm * (eta * np.pi / 2 + (1 - eta) * np.sqrt(np.pi / (4 * np.log(2))))
    return area * (1 + KALPHA2_RATIO) if pair is not None else area


def _empty(note):
    return {"two_theta_fit": None, "two_theta_esd_deg": None, "fwhm_deg": None, "fwhm_esd_deg": None, "eta": None,
            "area": None, "fit_note": note}


def _fit_cluster(x, net, sigma, peaks, pair, start_fwhm):
    """Least-squares fit of one window; one result dict per peak of ``peaks``."""
    from scipy.optimize import least_squares

    m = len(peaks)
    x0, lower, upper = [], [], []
    for center in peaks:
        near = np.abs(x - center) <= start_fwhm / 2
        height = max(float(net[near].max()) if near.any() else 0.0, 1e-6)
        width = float(np.clip(start_fwhm, *FWHM_BOUNDS_DEG))
        x0 += [center, height, width, 0.5]
        lower += [center - POSITION_RANGE_DEG, 0.0, FWHM_BOUNDS_DEG[0], 0.0]
        upper += [center + POSITION_RANGE_DEG, np.inf, FWHM_BOUNDS_DEG[1], 1.0]
    x0 += [0.0, 0.0]
    lower += [-np.inf, -np.inf]
    upper += [np.inf, np.inf]
    middle = float(x.mean())

    def residual(params):
        model = params[-2] + params[-1] * (x - middle)
        for i in range(m):
            center, height, width, eta = params[4 * i:4 * i + 4]
            model = model + doublet(x, center, height, width, eta, pair)
        return (model - net) / sigma

    try:
        fit = least_squares(residual,np.array(x0), bounds=(lower, upper), x_scale="jac", max_nfev=4000)
    except Exception:  # noqa: BLE001 - a failed window must not fail the peak list
        return [_empty("fit failed")] * m
    if not fit.success:
        return [_empty("fit failed")] * m
    dof = len(x) - len(x0)
    try:
        covariance = (2 * fit.cost / dof) * np.linalg.pinv(fit.jac.T @ fit.jac)
        errors = np.sqrt(np.clip(np.diag(covariance), 0, None))
    except (np.linalg.LinAlgError, ValueError):
        errors = np.full(len(x0), np.inf)
    results = []
    for i in range(m):
        center, height, width, eta = fit.x[4 * i:4 * i + 4]
        esd = errors[4 * i + 2]
        note = None
        if width <= FWHM_BOUNDS_DEG[0] * 1.001 or width >= FWHM_BOUNDS_DEG[1] * 0.999:
            note = "fit failed"
        elif height <= 0 or not np.isfinite(esd) or esd > MAX_RELATIVE_ESD * width:
            note = "overlapped" if m > 1 else "too weak"
        results.append(_empty(note) if note else {
            "two_theta_fit": float(center), "two_theta_esd_deg": float(errors[4 * i]),
            "fwhm_deg": float(width), "fwhm_esd_deg": float(esd),
            "eta": float(eta), "area": float(doublet_area(height, width, eta, pair)), "fit_note": None,
        })
    return results


def _fit_all(x, net, sigma, keep, positions, pair, start_fwhm):
    order = np.argsort(positions)
    ordered = np.asarray(positions, dtype=float)[order]
    results: list[dict | None] = [None] * len(ordered)
    # Two found peaks closer than one FWHM cannot be told apart by a free fit (the peak search
    # often splits one peak, e.g. at its Kα2 side): the stronger one is fitted, the other is "merged".
    height = np.interp(ordered, x, net) if len(x) else np.zeros(len(ordered))
    fitted: list[int] = []
    for i, center in enumerate(ordered):
        if fitted and center - ordered[fitted[-1]] < start_fwhm:
            weaker = i if height[i] <= height[fitted[-1]] else fitted.pop()
            results[weaker] = _empty(MERGED)
            if weaker == i:
                continue
        fitted.append(i)
    kept = ordered[fitted]  # positions of the fitted peaks; clusters index into it
    clusters: list[list[int]] = []
    for j, center in enumerate(kept):
        if clusters and center - kept[clusters[-1][-1]] < CLUSTER_FWHM * start_fwhm:
            clusters[-1].append(j)
        else:
            clusters.append([j])
    for cluster in clusters:
        first, last = cluster[0], cluster[-1]
        peaks = kept[cluster]
        if len(cluster) > MAX_CLUSTER:
            values = [_empty("overlapped")] * len(cluster)
        else:
            lo, hi = peaks[0] - WINDOW_DEG, peaks[-1] + WINDOW_DEG
            if first > 0:
                lo = max(lo, (kept[first - 1] + peaks[0]) / 2)
            if last + 1 < len(kept):
                hi = min(hi, (peaks[-1] + kept[last + 1]) / 2)
            mask = keep & (x >= lo) & (x <= hi)
            if mask.sum() < 4 * len(cluster) + 2 + 5 or peaks.min() < x[0] or peaks.max() > x[-1]:
                values = [_empty("fit failed")] * len(cluster)
            else:
                values = _fit_cluster(x[mask], net[mask], sigma[mask], peaks, pair, start_fwhm)
        for j, result in zip(cluster, values):
            results[fitted[j]] = result
    unordered: list[dict | None] = [None] * len(ordered)
    for rank, index in enumerate(order):
        unordered[index] = results[rank]
    return unordered


def fit_peak_profiles(two_theta, observed, background, positions, wavelength, excluded_ranges=None) -> list[dict]:
    """Kα1 FWHM of each peak at ``positions`` (same order) from pseudo-Voigt fits of
    ``observed − background``, weighted by 1/sqrt(max(observed, 1)).

    Each result: ``two_theta_fit`` (Kα1 position) with ``two_theta_esd_deg``, ``fwhm_deg``,
    ``fwhm_esd_deg`` (e.s.d. from the Jacobian), ``eta``,
    ``area`` (integrated net intensity of the fitted Kα1+Kα2 profile, counts·degree), ``fit_note`` (``None``, "overlapped", "too weak", "fit failed" or "merged"; the values are
    then None). Positions closer than one FWHM are one peak: the stronger is fitted, the other
    is "merged".
    The clustering distance starts at 3 × 0.15° and is redone once with the median fitted FWHM
    when that differs by more than 20 %.
    """
    x = np.asarray(two_theta, dtype=float)
    y = np.asarray(observed, dtype=float)
    net = y - np.asarray(background, dtype=float)
    sigma = np.sqrt(np.maximum(y, 1.0))
    keep = np.ones(x.shape, dtype=bool)
    for start, end in excluded_ranges or []:
        keep &= ~((x >= float(start)) & (x <= float(end)))
    pair = kalpha_pair(wavelength)
    if not len(positions):
        return []
    estimate = START_FWHM_DEG
    results = _fit_all(x, net, sigma, keep, positions, pair, estimate)
    widths = [result["fwhm_deg"] for result in results if result["fwhm_deg"] is not None]
    if widths and abs(np.median(widths) - estimate) > 0.2 * estimate:
        results = _fit_all(x, net, sigma, keep, positions, pair, float(np.median(widths)))
    return results
