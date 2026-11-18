"""Pattern data downloads: measured or background-subtracted .xy, and the fit curves as CSV."""

from __future__ import annotations

import csv
import io

import numpy as np

from dara.export.peak_list import _label, wavelength_angstrom

FIT_COLUMNS = ("two_theta_deg", "d_angstrom", "observed", "calculated", "background", "difference")


def wavelength_text(wavelength) -> str:
    """``Cu (Ka1 1.540598 A)`` or the raw setting when it is not a known wavelength."""
    lam = wavelength_angstrom(wavelength)
    return f"{wavelength} (Ka1 {lam:.6f} A)" if lam else str(wavelength)


def xy_bytes(angles, intensities, header: str) -> bytes:
    """Two-column text (2theta, intensity) after one ``# header`` line."""
    lines = [f"# {header}"]
    lines += [f"{float(x):.6f} {float(y):.6f}" for x, y in zip(angles, intensities)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def fit_csv_bytes(plot_data, wavelength) -> bytes:
    """CSV of the fit curves (``FIT_COLUMNS`` plus one column per phase contribution, without
    background; the header names a phase by its formula, or by its full name when two phases
    share a formula). UTF-8 with BOM like the other CSV downloads. Without a calculated curve
    (an identification result) the calculated and difference columns are left out."""
    x = np.asarray(plot_data.x, dtype=float)
    observed = np.asarray(plot_data.y_obs, dtype=float)
    lam = wavelength_angstrom(wavelength)
    d = lam / (2 * np.sin(np.radians(x / 2))) if lam else np.full(x.shape, np.nan)
    background = np.asarray(plot_data.y_bkg, dtype=float)
    if plot_data.y_calc is None:
        names, columns = ["two_theta_deg", "d_angstrom", "observed", "background"], [x, d, observed, background]
    else:
        calculated = np.asarray(plot_data.y_calc, dtype=float)
        names, columns = list(FIT_COLUMNS), [x, d, observed, calculated, background, observed - calculated]
    structs = plot_data.structs or {}
    labels = [_label(name) for name in structs]
    header = names + [
        label if labels.count(label) == 1 else name for name, label in zip(structs, labels)
    ]
    columns += [np.asarray(curve, dtype=float) for curve in structs.values()]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for values in zip(*columns):
        writer.writerow(["" if not np.isfinite(value) else f"{value:.8g}" for value in values])
    return buffer.getvalue().encode("utf-8-sig")
