"""User-defined background curve: anchor points interpolated with PCHIP.

BGMN 4.2.22 reads the curve with ``UNT=<file>``. With ``RU=2`` the curve's shape is fixed and
BGMN refines only a scale and an additive constant (measured: the refined automatic background
used as the curve gives the same Rwp, 9.54 on TiO2+ZnO). ``UNTC`` crashes BGMN and ``RU=0``
ignores the curve, so neither is used. BGMN does not interpolate a sparse ``UNT`` file
linearly, so the curve is written densely on the measured 2-theta grid. Points outside
WMIN/WMAX or inside CUT ranges do not change the fit (measured: identical Rwp and scale with
the curve written over the whole grid or only over the fitted points).

The UI draws the same curve with a JavaScript port (``ui/src/components/backgroundAnchors.mjs``);
``tests/test_data/pchip_fixture.json`` keeps the two in step.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MIN_ANCHORS = 2
MAX_ANCHORS = 40
INTERPOLATION = "pchip"
BGMN_RU = 2
CURVE_FILENAME = "user_bkg.xy"
# Warn when the fitted background leaves the drawn curve by more than this fraction of its median.
SHIFT_WARN = 0.10


def validate_anchors(anchors, *, lo: float, hi: float, excluded_ranges=None) -> list[list[float]]:
    """``[[two_theta, y], ...]`` checked and converted to floats; ``ValueError`` otherwise.

    2..40 points, x strictly increasing, inside ``[lo, hi]`` (the refined range), outside every
    excluded range, y finite and >= 0.
    """
    if not isinstance(anchors, (list, tuple)):
        raise ValueError("Background anchors must be a list of [2θ, intensity] pairs.")
    if not MIN_ANCHORS <= len(anchors) <= MAX_ANCHORS:
        raise ValueError(f"Use {MIN_ANCHORS} to {MAX_ANCHORS} background anchor points.")
    out: list[list[float]] = []
    for point in anchors:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) for v in point
        ):
            raise ValueError("Each background anchor must be a [2θ, intensity] pair of numbers.")
        x, y = float(point[0]), float(point[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Background anchors must be finite numbers.")
        if y < 0:
            raise ValueError(f"Background anchor at {x:g}° has a negative intensity.")
        # 1e-3 deg: the fit plot (.dia) rounds 2θ to 4 decimals, and anchors are seeded from it.
        if x < lo - 1e-3 or x > hi + 1e-3:
            raise ValueError(f"Background anchor at {x:g}° is outside the refined range {lo:g}–{hi:g}°.")
        for low, high in excluded_ranges or []:
            if low < x < high:
                raise ValueError(f"Background anchor at {x:g}° is inside the excluded range {low:g}–{high:g}°.")
        if out and x <= out[-1][0]:
            raise ValueError("Background anchors must be sorted by 2θ without repeats.")
        out.append([x, y])
    return out


def curve(anchors, x) -> np.ndarray:
    """PCHIP curve through ``anchors`` at ``x``; constant beyond the end anchors, clipped to >= 0."""
    from scipy.interpolate import PchipInterpolator

    points = np.asarray(anchors, dtype=float)
    x = np.clip(np.asarray(x, dtype=float), points[0, 0], points[-1, 0])
    return np.clip(PchipInterpolator(points[:, 0], points[:, 1])(x), 0.0, None)


def write_curve(path: Path, x, anchors) -> Path:
    """Dense ``2θ intensity`` file of the curve on the data grid ``x`` (read by BGMN ``UNT=``)."""
    x = np.asarray(x, dtype=float)
    np.savetxt(path, np.column_stack([x, curve(anchors, x)]), fmt="%.6f")
    return path


def fitted_scale(x, y_bkg, anchors) -> tuple[float | None, float | None]:
    """``(scale, constant)`` BGMN applied: the refined background is ``scale * curve + constant``.

    A least-squares line, not median(background / curve): with the constant the ratio is not
    flat (measured on a 20-70 deg window: median ratio 1.011, true scale 0.969, constant 14.8).
    """
    user = curve(anchors, x)
    y_bkg = np.asarray(y_bkg, dtype=float)
    ok = np.isfinite(y_bkg) & np.isfinite(user)
    if ok.sum() < 2 or np.ptp(user[ok]) <= 0:
        return None, None
    scale, constant = np.polyfit(user[ok], y_bkg[ok], 1)
    return round(float(scale), 4), round(float(constant), 3)


def record(anchors, scale=None, constant=None) -> dict:
    """``background`` block of a version refined with a manual curve."""
    return {
        "mode": "manual",
        "anchors": [list(map(float, point)) for point in anchors],
        "interpolation": INTERPOLATION,
        "ru": BGMN_RU,
        "scale": scale,
        "constant": constant,
    }


def label(anchors) -> str:
    return f"Background: manual ({len(anchors)} points)"


def curve_shift(x, y_bkg, anchors, refinement_params=None) -> tuple[float | None, float | None]:
    """``(shift, two_theta)``: largest |fitted background - curve| over the fitted points,
    relative to max(median(curve), 1), and the 2θ where it occurs; ``(None, None)`` without data.

    Not the bare scale: on a nearly flat curve scale and constant compensate (measured 1.297 and
    -1070 with a real deviation of 0.4 %).
    """
    params = refinement_params or {}
    x = np.asarray(x, dtype=float)
    y_bkg = np.asarray(y_bkg, dtype=float)
    user = curve(anchors, x)
    ok = np.isfinite(x) & np.isfinite(y_bkg)
    if params.get("wmin") is not None:
        ok &= x >= float(params["wmin"])
    if params.get("wmax") is not None:
        ok &= x <= float(params["wmax"])
    for low, high in params.get("excluded_ranges") or []:
        ok &= ~((x > low) & (x < high))
    if not ok.any():
        return None, None
    dev = np.abs(y_bkg[ok] - user[ok])
    i = int(np.argmax(dev))
    shift = float(dev[i]) / max(float(np.median(user[ok])), 1.0)
    return round(shift, 4), round(float(x[ok][i]), 3)


def shift_caution(shift, two_theta=None) -> str | None:
    """Text of the "fitted background left the drawn curve" warning, or ``None``."""
    if shift is None or float(shift) <= SHIFT_WARN:
        return None
    where = f" (largest at {float(two_theta):.2f}°)" if two_theta is not None else ""
    return (
        f"The fitted background differs from the user-defined curve by up to {float(shift):.0%}"
        f"{where}; the curve is probably too high or too low there. Check it against the data."
    )


def methods_sentence(n_anchors: int, shift=None) -> str:
    shift_text = (
        f"; the fitted background stayed within {float(shift):.1%} of the drawn curve"
        if shift is not None else ""
    )
    return (
        f"The background was a user-defined curve ({n_anchors} anchor points, PCHIP "
        f"interpolation) refined by BGMN with a scale and an additive constant{shift_text}."
    )
