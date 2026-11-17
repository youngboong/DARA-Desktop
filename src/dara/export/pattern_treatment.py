"""Display treatments of a measured pattern: Kα2 stripping and smoothing.

These never feed a fit, a search, the peak detection or the residual; they change only the
observed trace drawn on the fit plot and the dedicated pattern download.
"""

from __future__ import annotations

import numpy as np

KALPHA2_RATIO = 0.5  # I(Kα2) / I(Kα1)
SMOOTH_WINDOWS = range(5, 26, 2)  # odd Savitzky-Golay windows, in points


def kalpha_pair(wavelength) -> tuple[float, float] | None:
    """``(Kα1, Kα2)`` in Å of a lab anode name; ``None`` for a numeric (synchrotron/custom) wavelength."""
    from dara.utils import ANODE_LINES_A

    try:
        float(wavelength)
        return None
    except (TypeError, ValueError):
        pass
    lines = ANODE_LINES_A.get(str(wavelength).strip().lower())
    return (lines[0], lines[1]) if lines else None


def strip_kalpha2(two_theta, intensity, wavelength, *, nonnegative: bool = True) -> np.ndarray:
    """Rachinger correction: Kα1-only intensity of a Kα1+Kα2 pattern (2θ ascending).

    From low to high angle, ``I1(2θ) = I(2θ) − 0.5 · I1(2θ₁)`` where ``2θ₁`` is the Kα1 angle whose
    Kα2 line falls at ``2θ`` (Bragg's law, ``sin θ₁ = sin θ · λ1/λ2``); ``I1(2θ₁)`` is linearly
    interpolated between corrected points (when ``2θ₁`` lies within the last step, the equation is
    solved for this point), and a point whose ``2θ₁`` lies before the first point is not
    corrected. The result is never negative unless ``nonnegative`` is False (net signals).
    """
    pair = kalpha_pair(wavelength)
    if pair is None:
        raise ValueError(f"Kα2 stripping needs a lab anode, not {wavelength!r}")
    x = np.asarray(two_theta, dtype=float)
    y = np.asarray(intensity, dtype=float)
    shifted = np.degrees(2 * np.arcsin(np.clip(np.sin(np.radians(x / 2)) * pair[0] / pair[1], -1, 1)))
    starts = np.minimum(np.searchsorted(x, shifted, side="right") - 1, np.arange(len(x)) - 1)
    floor = 0.0 if nonnegative else -np.inf
    out = np.zeros_like(y)
    for i, j in enumerate(starts):
        if j < 0:  # 2θ₁ before the first point
            out[i] = max(y[i], floor)
            continue
        step = x[j + 1] - x[j]
        t = (shifted[i] - x[j]) / step if step > 0 else 0.0
        if j + 1 == i:  # I1 = y − r((1 − t) I1[j] + t I1)
            value = (y[i] - KALPHA2_RATIO * (1 - t) * out[j]) / (1 + KALPHA2_RATIO * t)
        else:
            value = y[i] - KALPHA2_RATIO * ((1 - t) * out[j] + t * out[j + 1])
        out[i] = max(value, floor)
    return out


def smooth(intensity, window: int) -> np.ndarray:
    """Savitzky-Golay smoothing, polynomial order 2, odd ``window`` of 5 to 25 points."""
    from scipy.signal import savgol_filter

    if window not in SMOOTH_WINDOWS:
        raise ValueError("The smoothing window is an odd number of 5 to 25 points")
    y = np.asarray(intensity, dtype=float)
    return savgol_filter(y, window, 2) if len(y) >= window else y.copy()


def treat(two_theta, intensity, wavelength, *, kalpha2: bool = False, window: int | None = None,
          background=None) -> np.ndarray:
    """Stripping first, then smoothing. With ``background`` the Kα2 stripping acts on the net
    signal and the background is added back unchanged (the refined background has no doublet)."""
    y = np.asarray(intensity, dtype=float)
    if kalpha2 and background is None:
        y = strip_kalpha2(two_theta, y, wavelength)
    elif kalpha2:
        base = np.asarray(background, dtype=float)
        y = np.maximum(strip_kalpha2(two_theta, y - base, wavelength, nonnegative=False) + base, 0.0)
    if window:
        y = smooth(y, window)
    return y


def treatment_text(kalpha2: bool, window: int | None) -> str:
    """``Kα2 stripped, smoothed (11 points)``; empty without a treatment."""
    parts = (["Kα2 stripped"] if kalpha2 else []) + ([f"smoothed ({window} points)"] if window else [])
    return ", ".join(parts)


def treatment_suffix(kalpha2: bool, window: int | None) -> str:
    """File-name suffix: ``_ka2stripped_smooth11``."""
    return ("_ka2stripped" if kalpha2 else "") + (f"_smooth{window}" if window else "")
