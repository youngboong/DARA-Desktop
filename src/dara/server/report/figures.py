"""Publication-style Rietveld fit figure (observed, calculated, background, ticks, difference).

One closed frame as in GSAS-II, FullProf and TOPAS plots: the observed points (black), the
calculated curve (red) and the background, one row of Bragg-position ticks per phase below the
pattern and the difference curve offset below the rows (grey); a legend names the curves and the
phases. Arial (Helvetica, DejaVu Sans as fallbacks) at 7-8 pt, inward ticks on all four sides, no
title or grid, colour-blind-safe phase colours (Okabe-Ito). A fit without a calculated curve (an
identification result) shows the measured pattern with its background (not subtracted), each
phase's reference lines as sticks on the background and the tick rows, with no difference curve.
"""

from __future__ import annotations

import io
import threading
from typing import Literal

import numpy as np

_RENDER_LOCK = threading.Lock()

DPI_RANGE = (72, 1200)
WIDTH_MM_RANGE = (40.0, 250.0)
HEIGHT_RATIO = 0.68
FONT_SIZE = 8
EXCLUDED_LABEL = "Excluded from refinement"


def _default_label(name: str) -> str:
    """Readable phase label; formula subscripts as mathtext subscripts in the text font (Arial has
    no Unicode subscript digits)."""
    try:
        import re
        from html import unescape

        from dara.plot import phase_display_name
        html = phase_display_name(name).replace("$", "")
        html = re.sub(r"<sub>([^<]*)</sub>", lambda match: r"$\mathregular{_{%s}}$" % match.group(1), html)
        return unescape(re.sub(r"<[^>]+>", "", html))
    except Exception:
        return str(name).replace("$", r"\$")


def _reflection_positions(refinement, names: list[str]) -> dict[str, np.ndarray]:
    """2θ of every reflection per refined phase (empty when peak data are unavailable)."""
    positions: dict[str, np.ndarray] = {name: np.array([]) for name in names}
    try:
        import pandas as pd

        frame = pd.DataFrame(refinement.peak_data)
        if "2theta" not in frame:
            return positions
        for index, name in enumerate(names):
            if "phase" in frame:
                chosen = frame.loc[frame["phase"] == name, "2theta"]
            else:
                chosen = frame.loc[frame.get("phase_idx") == index, "2theta"]
            values = np.asarray(chosen, dtype=float)
            positions[name] = values[np.isfinite(values)]
    except Exception:
        pass
    return positions


HKL_MAX_LABELS = 14
# Okabe-Ito colours, ordered so that neighbouring phases also differ in grey (relative luminance
# 0.15, 0.42, 0.26, 0.41, 0.29); yellow is too light on white and vermillion too close to the red
# calculated curve.
PHASE_COLORS = ["#0072B2", "#E69F00", "#009E73", "#56B4E9", "#CC79A7"]
OBSERVED_COLOR = "black"
CALCULATED_COLOR = "#D7191C"
BACKGROUND_COLOR = "#8c8c8c"
DIFFERENCE_COLOR = "#5a5a5a"
FONT_FAMILY = ["Arial", "Helvetica", "DejaVu Sans"]


def _hkl_text(indices: list[int]) -> str:
    """Miller indices as printed in papers: (110); a negative index gets an overbar."""
    parts = [rf"\bar{{{-value}}}" if value < 0 else str(value) for value in indices]
    joiner = r"\," if any(abs(value) > 9 for value in indices) else ""
    body = joiner.join(parts)
    return f"$({body})$" if "\\" in body else f"({body})"


def _hkl_labels(refinement, names: list[str], low: float, high: float, min_gap: float) -> list[tuple[float, str]]:
    """(2θ, "(hkl)") of the strongest reflections of all phases, strongest first, at least
    ``min_gap`` degrees apart, so that no two labels overlap."""
    try:
        import pandas as pd

        frame = pd.DataFrame(refinement.peak_data)
        if not {"2theta", "intensity", "h", "k", "l"}.issubset(frame.columns):
            return []
        if "phase" in frame:
            frame = frame[frame["phase"].isin(names)]
        frame = frame[(frame["2theta"] >= low) & (frame["2theta"] <= high)]
        frame = frame.sort_values("intensity", ascending=False)
        picked: list[tuple[float, str]] = []
        for position, *indices in zip(frame["2theta"], frame["h"], frame["k"], frame["l"]):
            position = float(position)
            if any(abs(position - other) < min_gap for other, _ in picked):
                continue
            picked.append((position, _hkl_text([int(value) for value in indices])))
            if len(picked) == HKL_MAX_LABELS:
                break
        return picked
    except Exception:
        return []


def _clean_ranges(ranges, low: float, high: float) -> list[tuple[float, float]]:
    cleaned = []
    for interval in ranges or []:
        try:
            start, end = float(interval[0]), float(interval[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (np.isfinite(start) and np.isfinite(end)) or end <= low or start >= high:
            continue
        cleaned.append((max(start, low), min(end, high)))
    return cleaned


def radiation_text(wavelength) -> str | None:
    """"Cu Kα" for a lab anode, "λ = 0.7293 Å" for a synchrotron wavelength in nm (DARA's
    setting), ``None`` when unknown."""
    if wavelength is None:
        return None
    try:
        return f"λ = {float(wavelength) * 10:.4f} Å"
    except (TypeError, ValueError):
        name = str(wavelength).strip().capitalize()
        return f"{name} Kα" if name in {"Cu", "Co", "Cr", "Fe", "Mo", "Ag"} else None


def _stick_lines(refinement, names, x, background) -> dict:
    """Reference lines of each phase of an identification: ``{name: (2θ, base, top)}``."""
    try:
        import pandas as pd

        frame = pd.DataFrame(refinement.peak_data)
        if not {"2theta", "intensity", "phase"}.issubset(frame.columns):
            return {}
        lines = {}
        for name in names:
            chosen = frame[frame["phase"] == name]
            tt = chosen["2theta"].to_numpy(dtype=float)
            base = np.interp(tt, x, background)
            lines[name] = (tt, base, base + chosen["intensity"].to_numpy(dtype=float))
        return lines
    except Exception:
        return {}


def fit_figure(
    refinement,
    *,
    fmt: Literal["png", "svg", "pdf"],
    dpi: int = 600,
    width_mm: float = 170.0,
    excluded_ranges=None,
    counts_like: bool | None = None,
    phase_labels: dict[str, str] | None = None,
    hkl_labels: bool = False,
    annotations=None,
    radiation: str | None = None,
) -> bytes:
    """Render the refinement fit as PNG, SVG or PDF bytes (deterministic for equal input).

    ``width_mm``: about 85 for a single journal column, 170 for a double one (the text stays
    7-8 pt). ``hkl_labels`` writes the Miller indices on the strongest reflections; a label that
    would cross the legend or a stronger label is left out. ``radiation`` ("Cu Kα") is named in
    the 2θ axis title. ``annotations``: the analyst's plot labels (``{two_theta, intensity,
    text}``), written as plain text.
    """
    if fmt not in {"png", "svg", "pdf"}:
        raise ValueError(f"Unsupported figure format: {fmt}")
    dpi = int(min(max(int(dpi), DPI_RANGE[0]), DPI_RANGE[1]))
    width_mm = float(min(max(float(width_mm), WIDTH_MM_RANGE[0]), WIDTH_MM_RANGE[1]))

    data = refinement.plot_data
    x = np.asarray(data.x, dtype=float)
    observed = np.asarray(data.y_obs, dtype=float)
    calculated = None if data.y_calc is None else np.asarray(data.y_calc, dtype=float)
    background = np.asarray(data.y_bkg, dtype=float)
    finite_x = x[np.isfinite(x)]
    if finite_x.size < 2 or float(finite_x.max()) <= float(finite_x.min()):
        raise ValueError("The refinement has no usable 2θ range to plot")
    low, high = float(finite_x.min()), float(finite_x.max())

    names = list(refinement.lst_data.phases_results)
    labels = {name: str((phase_labels or {}).get(name) or '').replace('$', r'\$') or _default_label(name) for name in names}
    reflections = _reflection_positions(refinement, names)
    spans = _clean_ranges(excluded_ranges, low, high)
    sticks = _stick_lines(refinement, names, x, background) if calculated is None else {}

    import matplotlib

    try:
        matplotlib.use("Agg")
    except Exception:
        pass
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import AutoMinorLocator, MaxNLocator

    width_in = width_mm / 25.4
    colors = [PHASE_COLORS[i % len(PHASE_COLORS)] for i in range(max(len(names), 1))]
    rc = {
        "font.size": FONT_SIZE,
        "font.family": "sans-serif",
        "font.sans-serif": FONT_FAMILY,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.minor.width": 0.5,
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "xtick.minor.size": 1.5,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "axes.grid": False,
        "svg.fonttype": "none",
        "svg.hashsalt": "dara-report",
        "pdf.fonttype": 42,
        # Overbars and subscripts in the text font as well
        "mathtext.fontset": "custom",
        "mathtext.rm": "Arial",
        "mathtext.it": "Arial:italic",
        "mathtext.bf": "Arial:bold",
        "axes.unicode_minus": True,
    }
    buffer = io.BytesIO()
    with _RENDER_LOCK, matplotlib.rc_context(rc):
        figure = Figure(figsize=(width_in, width_in * HEIGHT_RATIO), layout="constrained")
        FigureCanvasAgg(figure)
        ax = figure.add_subplot()

        # The pattern, one tick row per phase under it, then the difference offset below the rows.
        tops = [observed] + ([calculated] if calculated is not None else [])
        tops += [top for _, _, top in sticks.values() if top.size]
        data_top = float(np.nanmax(np.concatenate([np.ravel(values) for values in tops])))
        data_low = float(np.nanmin(np.concatenate([observed, background])))
        span = max(data_top - min(data_low, 0.0), 1e-9)
        step = 0.055 * span
        rows = [data_low - (index + 1) * step for index in range(len(names))]
        floor = (rows[-1] if rows else data_low) - step

        if calculated is not None:
            ax.plot(x, observed, linestyle="none", marker="o", markersize=1.5, markeredgewidth=0.3,
                    markerfacecolor="none", markeredgecolor=OBSERVED_COLOR, zorder=3)
            ax.plot(x, calculated, color=CALCULATED_COLOR, lw=0.7, zorder=4)
            ax.plot(x, background, color=BACKGROUND_COLOR, lw=0.6, zorder=2)
            difference = observed - calculated
            offset = floor - float(np.nanmax(difference))
            ax.axhline(offset, color="#c8c8c8", lw=0.4, zorder=1)
            ax.plot(x, difference + offset, color=DIFFERENCE_COLOR, lw=0.6, zorder=3)
            bottom = offset + float(np.nanmin(difference)) - 0.4 * step
        else:  # the measured pattern with its background and each phase's reference lines on it
            ax.plot(x, observed, color=OBSERVED_COLOR, lw=0.6, zorder=3)
            ax.plot(x, background, color=BACKGROUND_COLOR, lw=0.6, zorder=2)
            for index, name in enumerate(names):
                if name in sticks:
                    tt, base, top = sticks[name]
                    ax.vlines(tt, base, top, color=colors[index], lw=0.7, zorder=2.5)  # under the measured line
            bottom = floor + 0.4 * step

        for index, name in enumerate(names):
            positions = reflections.get(name)
            if positions is not None and positions.size:
                ax.plot(
                    positions, np.full(positions.shape, rows[index]),
                    linestyle="none", marker="|", markersize=5, markeredgewidth=0.6,
                    color=colors[index], gid=f"ticks-{index}", zorder=3,
                )

        for start, end in spans:
            ax.axvspan(start, end, color="#d9d9d9", alpha=0.6, lw=0, zorder=0)

        ax.set_xlim(low, high)
        top_limit = data_top + 0.06 * span
        ax.set_ylim(bottom, top_limit)
        # Intensity ticks on the pattern only, not on the tick rows or the offset difference.
        base = max(min(data_low, 0.0), 0.0) if data_low >= 0 else data_low
        ax.set_yticks([tick for tick in MaxNLocator(nbins=5).tick_values(base, data_top)
                       if base - 1e-9 <= tick <= data_top])
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.set_ylabel("Intensity (counts)" if counts_like is not False else "Intensity (a.u.)")
        ax.set_xlabel(f"2θ (°, {radiation})" if radiation else "2θ (°)")

        handles = [Line2D([], [], linestyle="none", marker="o", markersize=3, markeredgewidth=0.5,
                          markerfacecolor="none", markeredgecolor=OBSERVED_COLOR)
                   if calculated is not None else Line2D([], [], color=OBSERVED_COLOR, lw=0.6)]
        legend_labels = ["Observed"]
        if calculated is not None:
            handles.append(Line2D([], [], color=CALCULATED_COLOR, lw=0.7))
            legend_labels.append("Calculated")
        handles.append(Line2D([], [], color=BACKGROUND_COLOR, lw=0.6))
        legend_labels.append("Background")
        if calculated is not None:
            handles.append(Line2D([], [], color=DIFFERENCE_COLOR, lw=0.6))
            legend_labels.append("Difference")
        for index, name in enumerate(names):
            handles.append(Line2D([], [], linestyle="none", marker="|", markersize=6, markeredgewidth=0.8,
                                  color=colors[index]))
            legend_labels.append(labels[name])
        if spans:
            handles.append(Patch(facecolor="#d9d9d9", alpha=0.6, lw=0))
            legend_labels.append(EXCLUDED_LABEL)
        legend = ax.legend(
            handles, legend_labels, loc="upper right", frameon=False, fontsize=FONT_SIZE - 1,
            handlelength=1.4, borderaxespad=0.5, labelspacing=0.3,
            ncols=2 if width_mm < 120 and len(handles) > 5 else 1,
        )

        if hkl_labels:
            _draw_hkl_labels(figure, ax, legend, refinement, names, x, observed, calculated, sticks,
                             (low, high), width_in, bottom, top_limit)

        for note in annotations or ():
            try:  # stored task data: a damaged entry is left out, never fails the figure
                text, position = str(note["text"])[:120], (float(note["two_theta"]), float(note["intensity"]))
            except (KeyError, TypeError, ValueError):
                continue
            # Plain text: a "$" would start matplotlib mathtext.
            ax.annotate(
                text.replace("$", r"\$"), position,
                xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                fontsize=FONT_SIZE - 1, color="black", annotation_clip=True,
                # as on the web plot: a light label box keeps the text readable over the curves
                bbox={"boxstyle": "square,pad=0.2", "fc": "white", "ec": "#c9cdc6", "lw": 0.4, "alpha": 0.85}, zorder=6,
            )

        if fmt == "png":
            figure.savefig(buffer, format="png", dpi=dpi, metadata={"Software": None})
        elif fmt == "pdf":
            figure.savefig(buffer, format="pdf", metadata={"Creator": "DARA", "Producer": None, "CreationDate": None})
        else:
            figure.savefig(buffer, format="svg", metadata={"Date": None, "Creator": "DARA"})
    return buffer.getvalue()


def _draw_hkl_labels(figure, ax, legend, refinement, names, x, observed, calculated, sticks,
                     window, width_in, bottom, top_limit) -> None:
    """Miller indices above the strongest reflections. Each label stands on the highest curve or
    reference line under it, the axis top is raised until every label fits inside the frame, and
    a label that would cross the legend or a stronger label is left out (strongest first)."""
    low, high = window
    label_size = FONT_SIZE - 1
    # A vertical label is about one line wide: keep label centres that far apart.
    min_gap = (high - low) * label_size * 1.3 / (width_in * 72 * 0.86)
    top = observed if calculated is None else np.fmax(observed, calculated)
    picked = []
    for position, text in _hkl_labels(refinement, names, low, high, min_gap):
        near = np.abs(x - position) <= min_gap / 2
        height = float(np.nanmax(top[near])) if near.any() else float(np.interp(position, x, top))
        for tt, _, peak in sticks.values():
            close = np.abs(tt - position) <= min_gap / 2
            if close.any():
                height = max(height, float(np.nanmax(peak[close])))
        picked.append((position, height, text))
    if not picked:
        return
    artists = [ax.annotate(text, (position, height), xytext=(0, 2), textcoords="offset points", rotation=90,
                           ha="center", va="bottom", fontsize=label_size, color="black", zorder=5)
               for position, height, text in picked]
    renderer = figure.canvas.get_renderer()
    for _ in range(2):  # the axes height settles with the layout: measure, raise the top, measure again
        figure.canvas.draw()
        area = ax.get_window_extent(renderer).height
        needed = top_limit
        for artist, (_, height, _) in zip(artists, picked):
            px = artist.get_window_extent(renderer).height + 3 * figure.dpi / 72
            if area > 2 * px:
                needed = max(needed, (height * area - px * bottom) / (area - px))
        ax.set_ylim(bottom, needed)
        top_limit = needed
    figure.canvas.draw()
    frame = ax.get_window_extent(renderer)
    taken = [legend.get_window_extent(renderer)]
    for artist in artists:  # strongest first
        box = artist.get_window_extent(renderer)
        if box.y1 > frame.y1 + 0.5 or any(box.overlaps(other) for other in taken):
            artist.remove()
            continue
        taken.append(box)
