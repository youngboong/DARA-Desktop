"""Publication-style Rietveld fit figure (observed, calculated, background, ticks, difference).

One closed frame as in GSAS-II, FullProf and TOPAS plots: the observed points (black), the
calculated curve (red) and the background, one row of Bragg-position ticks per phase below the
pattern and the difference curve offset below the rows (grey); a legend names the curves and the
phases. Arial (Helvetica, DejaVu Sans as fallbacks) at 7-8 pt, inward ticks on all four sides, no
title or grid, colour-blind-safe phase colours (Okabe-Ito).

A fit without a calculated curve (an identification result) and a series of patterns use the
phase-identification figure of materials papers instead (:func:`pattern_figure`): the measured
pattern(s) as thin black lines, raw (background neither drawn nor subtracted), stacked with a
constant offset and labelled at the right end; under them one strip per phase with its reference
pattern as vertical bars in the phase's colour; a legend at the upper right names each phase with
formula and space group (the database record is in the report's phase table); the Miller indices of the strongest lines are written over the top
pattern in the colour of their phase (materials papers label peaks by hkl far more often than by symbol).
"""

from __future__ import annotations

import io
import re
import threading
from typing import Literal

import numpy as np

from dara.miller import space_group_number, usual_hkl as _usual_hkl, usual_hkl_lookup

_RENDER_LOCK = threading.Lock()

DPI_RANGE = (72, 1200)
WIDTH_MM_RANGE = (40.0, 250.0)
HEIGHT_RATIO = 0.68
FONT_SIZE = 8
EXCLUDED_LABEL = "Excluded from refinement"


def _default_label(name: str) -> str:
    """Figure label of a phase: name, formula and space group ("Zincite, ZnO (*P*6₃*mc*)"; the name
    left out when it is the formula), in mathtext with the text font (Arial has no Unicode subscript
    digits). Never the database record: that is in the report's phase table."""
    try:
        from dara.plot import COMMON_PHASE_NAMES, display_formula_of, hydroxide, spacegroup_mathtext, spacegroup_symbol

        match = re.match(r"^(.+?)_(\d+)_\(", str(name))
        if not match:
            return str(name).replace("$", r"\$")
        formula, group = match.groups()
        common = COMMON_PHASE_NAMES.get((formula, group))
        shown = hydroxide(display_formula_of(name) or formula).replace("$", r"\$")
        text = re.sub(r"(\d+(?:\.\d+)?)", lambda found: r"$\mathregular{_{%s}}$" % found.group(1), shown)
        if common and common != shown:
            text = f"{common}, {text}"
        try:
            text += f" ({spacegroup_mathtext(spacegroup_symbol(group))})"
        except ValueError:
            pass
        return text
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
    parts = [rf"\overline{{{-value}}}" if value < 0 else str(value) for value in indices]
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
        usual = usual_hkl_lookup(refinement)
        phases = frame["phase"] if "phase" in frame else [None] * len(frame)
        for position, phase, *indices in zip(frame["2theta"], phases, frame["h"], frame["k"], frame["l"]):
            position = float(position)
            if any(abs(position - other) < min_gap for other, _ in picked):
                continue
            picked.append((position, _hkl_text(list(usual(phase, indices)))))
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


def _rc() -> dict:
    """Matplotlib settings of every figure: Arial 8 pt, inward ticks on all sides, no grid."""
    return {
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


def _save(figure, fmt: str, dpi: int) -> bytes:
    buffer = io.BytesIO()
    if fmt == "png":
        figure.savefig(buffer, format="png", dpi=dpi, metadata={"Software": None})
    elif fmt == "pdf":
        figure.savefig(buffer, format="pdf", metadata={"Creator": "DARA", "Producer": None, "CreationDate": None})
    else:
        figure.savefig(buffer, format="svg", metadata={"Date": None, "Creator": "DARA"})
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# phase-identification figure (measured patterns, reference strips, hkl in phase colours)
# ---------------------------------------------------------------------------

# Filled ●, ▼, ◆, ■, ▲ in the phase colours: shape and colour both name the phase.
PHASE_MARKERS = ["o", "v", "D", "s", "^"]
MARKS_MAX = 8  # the strongest lines only: a phase's peaks are recognised by a few marks
MARK_MIN_RELATIVE = 10.0  # a phase's reflections marked on a pattern: at least 10 % of its strongest
REFERENCE_MIN_RELATIVE = 0.5  # reference bars under 0.5 % are invisible at strip height


def phase_card(name: str) -> str | None:
    """Database record of a reference structure as written on its strip ("COD 9004178", "mp-625394")."""
    try:
        from dara.structure_quality import reference_provenance

        provenance = reference_provenance(name)
    except Exception:
        return None
    database_id, source = provenance.get("database_id"), provenance.get("source")
    if not database_id:
        return None
    return str(database_id) if source == "mp" else f"{str(source).upper()} {database_id}"


def phase_strip_label(name: str, label: str | None = None) -> str:
    """"Zincite, ZnO (P6₃mc)": the phase's label in the identification figure (no database record)."""
    return str(label).replace("$", r"\$") if label else _default_label(name)


def reference_lines(structure, wavelength_a: float, low: float, high: float) -> tuple[np.ndarray, np.ndarray]:
    """``(2θ, relative intensity in %)`` of the reference structure's calculated powder pattern
    (database lattice, no fitted shift) inside ``low``–``high``."""
    from dara.rir import reflection_lines

    lines = reflection_lines(structure, float(wavelength_a), two_theta_max=min(179.0, high + 1.0))
    tt, intensity = np.asarray(lines["two_theta"], float), np.asarray(lines["intensity"], float)
    inside = (tt >= low) & (tt <= high) & np.isfinite(intensity)
    tt, intensity = tt[inside], intensity[inside]
    if not tt.size or not np.nanmax(intensity) > 0:
        return np.array([]), np.array([])
    relative = 100.0 * intensity / np.nanmax(intensity)
    keep = relative >= REFERENCE_MIN_RELATIVE
    return tt[keep], relative[keep]


def _phase_rows(refinement, name: str, low: float, high: float):
    """``(2θ, intensity, h, k, l)`` rows of one phase's reflections in the result, strongest first."""
    try:
        import pandas as pd

        frame = pd.DataFrame(refinement.peak_data)
        if not {"2theta", "intensity", "phase"}.issubset(frame.columns):
            return frame.iloc[0:0]
        frame = frame[(frame["phase"] == name) & (frame["2theta"] >= low) & (frame["2theta"] <= high)]
        return frame.sort_values("intensity", ascending=False)
    except Exception:
        import pandas as pd

        return pd.DataFrame()


def result_lines(refinement, name: str, low: float, high: float) -> tuple[np.ndarray, np.ndarray]:
    """``(2θ, relative intensity in %)`` of a phase's reflections as matched or refined in the result
    (the strip's fallback when the reference structure is not at hand)."""
    rows = _phase_rows(refinement, name, low, high)
    if rows.empty:
        return np.array([]), np.array([])
    tt, intensity = rows["2theta"].to_numpy(float), rows["intensity"].to_numpy(float)
    top = float(np.nanmax(intensity)) if intensity.size else 0.0
    if not top > 0:
        return np.array([]), np.array([])
    relative = 100.0 * intensity / top
    keep = relative >= REFERENCE_MIN_RELATIVE
    return tt[keep], relative[keep]


def peak_marks(refinement, name: str, low: float, high: float, merge_deg: float = 0.3) -> np.ndarray:
    """2θ of a phase's peaks marked on its pattern: reflections at its matched or refined positions of
    at least ``MARK_MIN_RELATIVE`` % of its strongest, one mark per ``merge_deg`` (the strongest), at
    most ``MARKS_MAX``."""
    rows = _phase_rows(refinement, name, low, high)
    if rows.empty:
        return np.array([])
    tt, intensity = rows["2theta"].to_numpy(float), rows["intensity"].to_numpy(float)
    top = float(np.nanmax(intensity)) if intensity.size else 0.0
    picked: list[float] = []
    for position, value in zip(tt, intensity):  # strongest first
        if not top > 0 or value < top * MARK_MIN_RELATIVE / 100:
            break
        if all(abs(position - other) >= merge_deg for other in picked):
            picked.append(float(position))
            if len(picked) == MARKS_MAX:
                break
    return np.sort(np.array(picked))


def hkl_candidates(refinement, name: str, low: float, high: float) -> list[tuple[float, str]]:
    """``(2θ, "(hkl)")`` of a phase's marked reflections (``MARK_MIN_RELATIVE``), strongest first."""
    rows = _phase_rows(refinement, name, low, high)
    if rows.empty or not {"h", "k", "l"}.issubset(rows.columns):
        return []
    rows = rows[rows["intensity"] >= float(rows["intensity"].max()) * MARK_MIN_RELATIVE / 100]
    try:
        lattice = refinement.lst_data.phases_results[name].get_structure().lattice
    except Exception:  # noqa: BLE001 - an identification has no refined structure: indices as listed
        lattice = None
    number = space_group_number(name)
    return [(float(position), _hkl_text(list(_usual_hkl((int(h), int(k), int(l)), number, lattice))))
            for position, h, k, l in zip(rows["2theta"], rows["h"], rows["k"], rows["l"])]


STACK_STEP = 1.0  # vertical offset between stacked patterns, in units of one pattern's height
STRIP_MM = 8.0  # one reference strip (its bars); the phase is named in the legend
STRIP_LABEL_MM = 3.2  # the strip's own name row when the phases are named on the strips
GAP_MM = 2.0  # between the lowest pattern and the first strip
HEAD_MM = 6.0  # above the top pattern, besides the legend rows
LEGEND_ROW_MM = 3.4  # one legend row at 7 pt
MARGIN_MM = 13.0  # axis labels, tick labels and the frame outside the plotting area


def interleaved_hkl(per_phase: list[list[tuple[float, str]]]) -> list[tuple[float, str, int]]:
    """``(2θ, "(hkl)", phase index)`` taking each phase's next strongest line in turn (main phase first),
    so a minor phase still gets its strongest lines labelled."""
    out, depth = [], max((len(lines) for lines in per_phase), default=0)
    for rank in range(depth):
        for index, lines in enumerate(per_phase):
            if rank < len(lines):
                out.append((lines[rank][0], lines[rank][1], index))
    return out


def pattern_figure(
    patterns: list[dict],
    phases: list[dict],
    *,
    fmt: Literal["png", "svg", "pdf"],
    dpi: int = 600,
    width_mm: float = 170.0,
    normalize: bool = False,
    radiation: str | None = None,
    hkl: list[tuple[float, str, int]] | None = None,
    excluded_ranges=None,
    annotations=None,
    symbols: bool = False,
    names: str = "legend",
    bars: bool = True,
) -> bytes:
    """Phase-identification figure as PNG, SVG or PDF bytes (deterministic for equal input).

    ``patterns``: in series order, the first at the bottom; each ``{x, y, label, marks}`` with the
    measured 2θ and intensities, the name written at its right end (``None``: none) and ``marks``
    ``{phase index: 2θ array}`` of the peaks marked with that phase's symbol when ``symbols``.
    ``phases``: one reference strip each, top to bottom, ``{label, two_theta, intensity}`` (relative
    intensities), drawn when ``bars``; ``names`` ``legend`` names them in a legend at the upper right,
    ``strips`` on their strips (without strips: in the legend).
    Without ``normalize`` all patterns share one intensity scale; with it each spans its own
    minimum to maximum. Every pattern is shifted by a constant only, never background-subtracted.
    ``hkl``: ``(2θ, "(hkl)", phase index)`` candidates, strongest first, written over the top pattern
    in the colour of their phase where they do not collide. ``annotations``: the analyst's labels
    (``{two_theta, intensity, text}``) on a single pattern, as plain text.
    """
    if fmt not in {"png", "svg", "pdf"}:
        raise ValueError(f"Unsupported figure format: {fmt}")
    dpi = int(min(max(int(dpi), DPI_RANGE[0]), DPI_RANGE[1]))
    width_mm = float(min(max(float(width_mm), WIDTH_MM_RANGE[0]), WIDTH_MM_RANGE[1]))

    curves = []
    for pattern in patterns:
        x, y = np.asarray(pattern["x"], float), np.asarray(pattern["y"], float)
        n = min(x.size, y.size)
        ok = np.isfinite(x[:n]) & np.isfinite(y[:n])
        if ok.sum() >= 2:
            order = np.argsort(x[:n][ok], kind="stable")
            curves.append((x[:n][ok][order], y[:n][ok][order], pattern))
    if not curves:
        raise ValueError("No pattern has a usable 2θ range to plot")
    low = min(float(x[0]) for x, _, _ in curves)
    high = max(float(x[-1]) for x, _, _ in curves)
    if not high > low:
        raise ValueError("No pattern has a usable 2θ range to plot")
    common = max(float(y.max() - y.min()) for _, y, _ in curves) or 1.0
    shown = []
    for index, (x, y, pattern) in enumerate(curves):
        scale = float(y.max() - y.min()) if normalize else common
        shown.append((x, (y - y.min()) / (scale or 1.0) + index * STACK_STEP, float(y.min()), scale or 1.0, pattern))

    n_phases = len(phases)
    n_strips = n_phases if bars else 0
    legend_rows = n_phases if n_phases and (names != "strips" or not bars) else 0  # strip names need the strips
    strip_mm = STRIP_MM + (0.0 if legend_rows else STRIP_LABEL_MM)
    head_mm = HEAD_MM + LEGEND_ROW_MM * legend_rows  # the legend sits above the top pattern
    stack_mm = width_mm * 0.55 * min(1 + 0.35 * (len(shown) - 1), 3.0) + LEGEND_ROW_MM * legend_rows
    stack_units = (len(shown) - 1) * STACK_STEP + 1
    unit_mm = (stack_mm - head_mm) / stack_units  # mm per unit of intensity
    axes_mm = stack_mm + (GAP_MM + n_strips * strip_mm if n_strips else 0)
    height_mm = axes_mm + MARGIN_MM
    units = lambda mm: mm / unit_mm  # noqa: E731
    deg_per_mm = (high - low) / max(width_mm - 14.0, 10.0)
    bottom = -units(GAP_MM + n_strips * strip_mm + 0.8) if n_strips else -units(1.0)
    top = stack_units + units(head_mm)

    import matplotlib

    try:
        matplotlib.use("Agg")
    except Exception:
        pass
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.ticker import AutoMinorLocator

    colors = [PHASE_COLORS[i % len(PHASE_COLORS)] for i in range(max(n_phases, 1))]
    markers = [PHASE_MARKERS[i % len(PHASE_MARKERS)] for i in range(max(n_phases, 1))]
    with _RENDER_LOCK, matplotlib.rc_context(_rc()):
        figure = Figure(figsize=(width_mm / 25.4, height_mm / 25.4), layout="constrained")
        FigureCanvasAgg(figure)
        ax = figure.add_subplot()

        texts = []  # labels the legend must clear
        points = []  # curves and symbols the legend must clear: (x, y) arrays
        for x, y, _, _, pattern in shown:
            ax.plot(x, y, color=OBSERVED_COLOR, lw=0.6, zorder=3)
            points.append((x, y))
            placed: list[tuple[float, float]] = []
            if symbols:
                for phase_index, positions in sorted((pattern.get("marks") or {}).items()):
                    if not 0 <= phase_index < n_phases:
                        continue
                    xs, ys = [], []
                    for position in np.asarray(positions, float):
                        near = np.abs(x - position) <= 0.15
                        if not near.any() or not low <= position <= high:
                            continue
                        level = float(y[near].max()) + units(1.3)
                        for other_x, other_y in placed:  # a mark of another phase at the same peak: above it
                            if abs(other_x - position) < 1.6 * deg_per_mm and level < other_y + units(1.6):
                                level = other_y + units(1.6)
                        placed.append((float(position), level))
                        xs.append(position)
                        ys.append(level)
                    ax.plot(xs, ys, linestyle="none", marker=markers[phase_index], markersize=3.2,
                            markeredgewidth=0, color=colors[phase_index], zorder=4)
                    points.append((np.asarray(xs, float), np.asarray(ys, float) + units(0.6)))
            if pattern.get("label"):
                # above the curve and the marks under the whole label (7 pt Arial: about 0.55 em per character)
                start = high - (len(str(pattern["label"])) * 0.55 * (FONT_SIZE - 1) / 72 * 25.4 + 2.0) * deg_per_mm
                tail = x >= start
                level = float(y[tail].max()) if tail.any() else float(y[-1])
                level = max([level] + [height + units(1.0) for px, height in placed if px >= start])
                texts.append(ax.annotate(
                    str(pattern["label"]).replace("$", r"\$"), (high, level), xytext=(-3, 2),
                    textcoords="offset points", ha="right", va="bottom", fontsize=FONT_SIZE - 1, zorder=5))

        for index, phase in enumerate(phases[:n_strips]):
            strip_top = -units(GAP_MM + index * strip_mm)
            base = strip_top - units(strip_mm - 0.4)
            bar = units(STRIP_MM - 1.2)
            tt, relative = np.asarray(phase["two_theta"], float), np.asarray(phase["intensity"], float)
            inside = (tt >= low) & (tt <= high) & np.isfinite(relative)
            ax.hlines(base, low, high, color=colors[index], lw=0.4, zorder=2)
            ax.vlines(tt[inside], base, base + bar * np.clip(relative[inside], 0, 100) / 100,
                      color=colors[index], lw=0.7, zorder=3)
            if not legend_rows:
                label_y = strip_top - units(0.3)
                ax.annotate(phase["label"], (high, label_y), xytext=(-3 - (6 if symbols else 0), 0),
                            textcoords="offset points", ha="right", va="top", fontsize=FONT_SIZE - 1, zorder=5)
                if symbols:
                    ax.plot([high - 5.5 / 72 * 25.4 * deg_per_mm], [label_y - units(1.25)], linestyle="none",
                            marker=markers[index], markersize=3.2, markeredgewidth=0, color=colors[index],
                            clip_on=False, zorder=5)
        legend = None
        if legend_rows:
            handles = [Line2D([], [], linestyle="none", marker=markers[index], markersize=3.2, markeredgewidth=0,
                              color=colors[index]) if symbols else
                       Line2D([], [], linestyle="none", marker="|", markersize=7, markeredgewidth=1.2,
                              color=colors[index]) for index in range(n_phases)]
            legend = ax.legend(handles, [phase["label"] for phase in phases], loc="upper right", frameon=False,
                               fontsize=FONT_SIZE - 1, handlelength=0.8, handletextpad=0.5, borderaxespad=0.4,
                               labelspacing=0.3)

        for start, end in _clean_ranges(excluded_ranges, low, high):
            ax.axvspan(start, end, color="#d9d9d9", alpha=0.6, lw=0, zorder=0)

        if len(shown) == 1:
            x, y, minimum, scale, _ = shown[0]
            for note in annotations or ():
                try:  # stored task data: a damaged entry is left out, never fails the figure
                    text = str(note["text"])[:120]
                    position = (float(note["two_theta"]), (float(note["intensity"]) - minimum) / scale)
                except (KeyError, TypeError, ValueError):
                    continue
                texts.append(ax.annotate(
                    text.replace("$", r"\$"), position, xytext=(0, 3), textcoords="offset points", ha="center",
                    va="bottom", fontsize=FONT_SIZE - 1, color="black", annotation_clip=True,
                    bbox={"boxstyle": "square,pad=0.2", "fc": "white", "ec": "#c9cdc6", "lw": 0.4, "alpha": 0.85},
                    zorder=6,
                ))

        ax.set_xlim(low, high)
        ax.set_ylim(bottom, top)
        ax.set_yticks([])  # arbitrary units: no intensity numbers
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.set_ylabel("Intensity (a.u.)")
        ax.set_xlabel(f"2θ (°, {radiation})" if radiation else "2θ (°)")
        if hkl:
            top, written = _pattern_hkl(figure, ax, shown[-1], hkl, colors, (low, high), deg_per_mm, bottom, top,
                                        HKL_PATTERN_MAX if width_mm >= 120 else HKL_PATTERN_MAX // 2,
                                        obstacles=list(texts), curves=points)
            texts.extend(written)
        if legend is not None:
            _clear_legend(figure, ax, legend, texts, points, bottom, top)
        return _save(figure, fmt, dpi)


HKL_PATTERN_MAX = 14


def _pattern_hkl(figure, ax, curve, candidates, colors, window, deg_per_mm, bottom, top, most,
                 obstacles=(), curves=()):
    """Miller indices over the top pattern's peaks, strongest first, at most ``most``: a label that
    would cross another label, an ``obstacles`` text or a curve of ``curves`` (``(x, y)`` arrays) is
    left out; the axis top is raised until every label fits inside the frame. Returns the new top
    and the labels."""
    x, y = curve[0], curve[1]
    low, high = window
    same = 0.8 * deg_per_mm  # lines this close sit on one peak: its first (strongest) line only
    picked: list[tuple[float, float, str, int]] = []
    for position, text, phase in candidates:
        if not low <= position <= high or any(abs(position - other) < same for other, _, _, _ in picked):
            continue
        near = np.abs(x - position) <= 0.15
        height = float(y[near].max()) if near.any() else float(np.interp(position, x, y))
        picked.append((position, height, text, phase))
        if len(picked) == 3 * most:
            break
    artists = [ax.annotate(text, (position, height), xytext=(0, 3), textcoords="offset points", rotation=90,
                           ha="center", va="bottom", fontsize=FONT_SIZE - 1, zorder=5,
                           color=colors[phase % len(colors)])
               for position, height, text, phase in picked]
    renderer = figure.canvas.get_renderer()

    def settle(top, labels):
        for _ in range(3):  # the axes height settles with the layout: measure, raise the top, measure again
            figure.canvas.draw()
            inverse = ax.transData.inverted()
            needed = max([top] + [inverse.transform((0, label.get_window_extent(renderer).y1))[1] for label in labels])
            if needed <= top * 1.0001:
                break
            top = needed + (needed - bottom) * 0.01
            ax.set_ylim(bottom, top)
        return top

    def prune(labels):
        figure.canvas.draw()
        taken = [text.get_window_extent(renderer) for text in obstacles]
        points = [ax.transData.transform(np.column_stack([cx, cy])) for cx, cy in curves if len(cx)]
        points = np.vstack(points) if points else np.empty((0, 2))
        kept = []
        for label in labels:
            box = label.get_window_extent(renderer).padded(1.0)
            crossed = ((points[:, 0] > box.x0) & (points[:, 0] < box.x1)
                       & (points[:, 1] > box.y0) & (points[:, 1] < box.y1)).any()
            if len(kept) < most and not crossed and not any(box.overlaps(other) for other in taken):
                kept.append(label)
                taken.append(box)
            else:
                label.remove()
        return kept

    start = top
    top = settle(top, artists)
    artists = prune(artists)
    ax.set_ylim(bottom, start)  # room for the labels kept only
    top = settle(start, artists)
    return top, prune(artists)


def _clear_legend(figure, ax, legend, texts, points, bottom, top) -> None:
    """Raise the axis top until the legend at the upper right sits above every curve, symbol and
    label under it (it never covers a peak)."""
    renderer = figure.canvas.get_renderer()
    pad = 1.5 * figure.dpi / 72
    for _ in range(5):  # the layout settles with each new top: measure again
        figure.canvas.draw()
        box, frame = legend.get_window_extent(renderer), ax.get_window_extent(renderer)
        highest = frame.y0
        for xs, ys in points:
            if not len(xs):
                continue
            px = ax.transData.transform(np.column_stack([xs, ys]))
            under = (px[:, 0] >= box.x0) & (px[:, 0] <= box.x1)
            if under.any():
                highest = max(highest, float(px[under, 1].max()))
        for text in texts:
            extent = text.get_window_extent(renderer)
            if extent.x1 >= box.x0 and extent.x0 <= box.x1:
                highest = max(highest, float(extent.y1))
        if highest + pad <= box.y0:
            return
        # data scale with the span, labels do not: a slight overshoot, settled by the next pass
        top = bottom + (top - bottom) * (highest + pad - frame.y0) / max(box.y0 - frame.y0, 1.0)
        ax.set_ylim(bottom, top)


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
    references: dict | None = None,
    peaks: str = "hkl",
    names: str = "legend",
    bars: bool = True,
) -> bytes:
    """Render the refinement fit as PNG, SVG or PDF bytes (deterministic for equal input).

    ``width_mm``: about 85 for a single journal column, 170 for a double one (the text stays
    7-8 pt). ``hkl_labels`` writes the Miller indices on the strongest reflections; a label that
    would cross the legend or a stronger label is left out. ``radiation`` ("Cu Kα") is named in
    the 2θ axis title. ``annotations``: the analyst's plot labels (``{two_theta, intensity,
    text}``), written as plain text. An identification (no calculated curve) is drawn as a
    phase-identification figure (:func:`identification_figure`); ``references`` ``{phase: (2θ,
    relative intensity)}`` are its reference strips and ``peaks``, ``names``, ``bars`` its style
    (unused for a fit).
    """
    if getattr(refinement.plot_data, "y_calc", None) is None:
        return identification_figure(
            refinement, fmt=fmt, dpi=dpi, width_mm=width_mm, excluded_ranges=excluded_ranges,
            phase_labels=phase_labels, annotations=annotations, radiation=radiation,
            references=references, peaks=peaks, names=names, bars=bars,
        )
    if fmt not in {"png", "svg", "pdf"}:
        raise ValueError(f"Unsupported figure format: {fmt}")
    dpi = int(min(max(int(dpi), DPI_RANGE[0]), DPI_RANGE[1]))
    width_mm = float(min(max(float(width_mm), WIDTH_MM_RANGE[0]), WIDTH_MM_RANGE[1]))

    data = refinement.plot_data
    x = np.asarray(data.x, dtype=float)
    observed = np.asarray(data.y_obs, dtype=float)
    calculated = np.asarray(data.y_calc, dtype=float)
    background = np.asarray(data.y_bkg, dtype=float)
    finite_x = x[np.isfinite(x)]
    if finite_x.size < 2 or float(finite_x.max()) <= float(finite_x.min()):
        raise ValueError("The refinement has no usable 2θ range to plot")
    low, high = float(finite_x.min()), float(finite_x.max())

    names = list(refinement.lst_data.phases_results)
    labels = {name: str((phase_labels or {}).get(name) or '').replace('$', r'\$') or _default_label(name) for name in names}
    reflections = _reflection_positions(refinement, names)
    spans = _clean_ranges(excluded_ranges, low, high)

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
    with _RENDER_LOCK, matplotlib.rc_context(_rc()):
        figure = Figure(figsize=(width_in, width_in * HEIGHT_RATIO), layout="constrained")
        FigureCanvasAgg(figure)
        ax = figure.add_subplot()

        # The pattern, one tick row per phase under it, then the difference offset below the rows.
        tops = [observed, calculated]
        data_top = float(np.nanmax(np.concatenate([np.ravel(values) for values in tops])))
        data_low = float(np.nanmin(np.concatenate([observed, background])))
        span = max(data_top - min(data_low, 0.0), 1e-9)
        step = 0.055 * span
        rows = [data_low - (index + 1) * step for index in range(len(names))]
        floor = (rows[-1] if rows else data_low) - step

        ax.plot(x, observed, linestyle="none", marker="o", markersize=1.5, markeredgewidth=0.3,
                markerfacecolor="none", markeredgecolor=OBSERVED_COLOR, zorder=3)
        ax.plot(x, calculated, color=CALCULATED_COLOR, lw=0.7, zorder=4)
        ax.plot(x, background, color=BACKGROUND_COLOR, lw=0.6, zorder=2)
        difference = observed - calculated
        offset = floor - float(np.nanmax(difference))
        ax.axhline(offset, color="#c8c8c8", lw=0.4, zorder=1)
        ax.plot(x, difference + offset, color=DIFFERENCE_COLOR, lw=0.6, zorder=3)
        bottom = offset + float(np.nanmin(difference)) - 0.4 * step

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
                          markerfacecolor="none", markeredgecolor=OBSERVED_COLOR),
                   Line2D([], [], color=CALCULATED_COLOR, lw=0.7),
                   Line2D([], [], color=BACKGROUND_COLOR, lw=0.6),
                   Line2D([], [], color=DIFFERENCE_COLOR, lw=0.6)]
        legend_labels = ["Observed", "Calculated", "Background", "Difference"]
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
            _draw_hkl_labels(figure, ax, legend, refinement, names, x, observed, calculated,
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

        return _save(figure, fmt, dpi)


def _draw_hkl_labels(figure, ax, legend, refinement, names, x, observed, calculated,
                     window, width_in, bottom, top_limit) -> None:
    """Miller indices above the strongest reflections. Each label stands on the highest curve
    under it, the axis top is raised until every label fits inside the frame, and
    a label that would cross the legend or a stronger label is left out (strongest first)."""
    low, high = window
    label_size = FONT_SIZE - 1
    # A vertical label is about one line wide: keep label centres that far apart.
    min_gap = (high - low) * label_size * 1.3 / (width_in * 72 * 0.86)
    top = np.fmax(observed, calculated)
    picked = []
    for position, text in _hkl_labels(refinement, names, low, high, min_gap):
        near = np.abs(x - position) <= min_gap / 2
        height = float(np.nanmax(top[near])) if near.any() else float(np.interp(position, x, top))
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


def identification_figure(
    refinement,
    *,
    fmt: Literal["png", "svg", "pdf"],
    dpi: int = 600,
    width_mm: float = 170.0,
    excluded_ranges=None,
    phase_labels: dict[str, str] | None = None,
    annotations=None,
    radiation: str | None = None,
    references: dict | None = None,
    peaks: str = "hkl",
    names: str = "legend",
    bars: bool = True,
) -> bytes:
    """Phase-identification figure of one result: the measured pattern (raw), one reference strip per
    phase (``references`` ``{phase: (2θ, relative intensity)}``; without one, the phase's reflections
    in the result) and its peaks labelled. ``peaks``: ``hkl`` (Miller indices of the strongest lines of
    every phase, in its colour), ``symbols`` (one filled symbol per phase) or ``none``; ``names``:
    ``legend`` or ``strips``; ``bars``: the reference strips drawn (see :func:`pattern_figure`)."""
    data = refinement.plot_data
    x = np.asarray(data.x, dtype=float)
    finite_x = x[np.isfinite(x)]
    if finite_x.size < 2 or float(finite_x.max()) <= float(finite_x.min()):
        raise ValueError("The refinement has no usable 2θ range to plot")
    low, high = float(finite_x.min()), float(finite_x.max())
    phase_names = list(refinement.lst_data.phases_results)
    phases = []
    for name in phase_names:
        tt, relative = (references or {}).get(name) or result_lines(refinement, name, low, high)
        phases.append({"label": phase_strip_label(name, (phase_labels or {}).get(name)),
                       "two_theta": tt, "intensity": relative})
    hkl = interleaved_hkl([hkl_candidates(refinement, name, low, high) for name in phase_names]) if peaks == "hkl" else None
    marks = ({index: peak_marks(refinement, name, low, high) for index, name in enumerate(phase_names)}
             if peaks == "symbols" else {})
    return pattern_figure(
        [{"x": x, "y": data.y_obs, "label": None, "marks": marks}], phases, fmt=fmt, dpi=dpi,
        width_mm=width_mm, radiation=radiation, hkl=hkl, excluded_ranges=excluded_ranges,
        annotations=annotations, symbols=peaks == "symbols", names=names, bars=bars,
    )
