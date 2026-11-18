"""Phase identification without refinement: the result of a search-match, as HighScore, Match! and
Jade give it (accepted phases with their reference lines on the pattern, peak list with phase/hkl
assignment, semi-quantification by RIR, crystallite size by Scherrer).

The accepted reference structures keep the lattice scale, zero shift and intensity scale the
search-match scoring found for them (:func:`dara.search.quick_rank._score`, scored on the whole
net pattern as their ranking row). Their calculated reflections (2θ Kα1, d, hkl, intensity,
multiplicity) come from the RIR module's line calculation (:func:`dara.rir.reflection_lines`)
at those positions and heights. :class:`IdentificationFit` presents the measured pattern, the
search-match's background (:func:`dara.search.prefilter.observed_profile`) and these reflections
the way the classical result tools read a refinement, without a calculated curve or weight fractions.
"""

from __future__ import annotations

from functools import cached_property
from types import SimpleNamespace

import numpy as np
import pandas as pd
from monty.json import MSONable

MODE = "identification"
NOT_AVAILABLE = "Not available for an identification result: refine the phases with Rietveld first."
REFLECTION_MERGE_DEG = 1e-4  # one reflection per d (symmetry equivalents), not merged card lines
COLORS = ("#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#9c755f", "#76b7b2", "#edc948", "#7f7f7f")  # as dara.plot


class IdentifiedPhase:
    """``lst_data.phases_results`` entry: the reference structure at the matched lattice scale.

    ``eps2`` is the search-match's zero shift ``z·cos θ`` of this phase in BGMN's EPS2 form."""

    def __init__(self, structure, spacegroup_no, eps2):
        self.structure, self.spacegroup_no, self.eps2 = structure, spacegroup_no, eps2

    def get_structure(self, lattice_fallback=None):
        return self.structure


class IdentificationFit:
    """The fit-like view the peak list, observed peaks, RIR, Williamson-Hall, cell refinement and
    pattern downloads read (``plot_data``, ``peak_data``, ``lst_data.phases_results``)."""

    identification = True

    def __init__(self, x, y_obs, y_bkg, peak_data, phases):
        # shortcut: no calculated peak widths (structs), so an observed peak is assigned within 0.1° of a
        # line (peak_list.ASSIGN_TOLERANCE_DEG) where a refinement widens it to the calculated FWHM.
        self.plot_data = SimpleNamespace(x=x, y_obs=y_obs, y_bkg=y_bkg, y_calc=None, structs={})
        self.peak_data = peak_data
        self.lst_data = SimpleNamespace(phases_results=phases, raw_lst="", rwp=None)

    def get_phase_weights(self):
        raise ValueError("An identification result has no weight fractions.")


def _sorted_pattern(xrd_data):
    x = np.asarray(xrd_data.angles, dtype=float)
    y = np.asarray(xrd_data.intensities, dtype=float)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    ok = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[ok], kind="stable")
    return x[ok][order], y[ok][order]


def _spacegroup_of(name: str) -> int | None:
    parts = str(name).split("_")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


class IdentificationDocument(MSONable):
    """Stored result of an identification task (the ``output`` in the result store)."""

    analysis_mode = MODE
    results = ()
    final_refinement_params = None

    def __init__(self, xrd_data, search_kwargs, input_cifs, phases, explained=None, elements=None,
                 precursors=None):
        self.xrd_data = xrd_data
        self.search_kwargs = search_kwargs
        self.input_cifs = list(input_cifs)
        self.phases = phases
        self.explained = explained
        self.elements = elements or []
        self.precursors = precursors or []

    def _range(self) -> dict:
        return (self.search_kwargs or {}).get("refinement_params") or {}

    @cached_property
    def final_result(self) -> IdentificationFit:
        from dara.search.prefilter import background_settings, observed_profile
        from dara.search.quick_rank import CONFIG

        x, y = _sorted_pattern(self.xrd_data)
        rng = self._range()
        # the background of the search-match (stored with the task; older tasks: the default)
        profile = observed_profile(x, y, wmin=rng.get("wmin"), wmax=rng.get("wmax"),
                                   excluded_ranges=rng.get("excluded_ranges"), config=CONFIG,
                                   background=background_settings(
                                       (self.search_kwargs or {}).get("background") or LEGACY_BACKGROUND))
        keep = (x >= profile.lo) & (x <= profile.hi)
        rows, phases = [], {}
        structures = {cif.filename: cif for cif in self.input_cifs}
        for phase in self.phases:
            r = phase["reflections"]
            for i, (h, k, l) in enumerate(r["hkl"]):
                rows.append({"phase": phase["name"], "2theta": r["two_theta"][i], "h": h, "k": k, "l": l,
                             "intensity": r["intensity"][i], "multiplicity": r["multiplicity"][i]})
            structure = structures[phase["name"]].to_structure()
            structure.scale_lattice(structure.volume * phase["lattice_scale"] ** 3)
            # z·cos θ (deg) = -2·EPS2·cos θ (rad), as dara.utils.angular_correction
            phases[phase["name"]] = IdentifiedPhase(structure, phase.get("spacegroup"), -np.radians(phase["shift"]) / 2)
        frame = pd.DataFrame(rows, columns=["phase", "2theta", "h", "k", "l", "intensity", "multiplicity"])
        return IdentificationFit(x[keep], y[keep], profile.background[keep], frame, phases)

    def figure(self):
        """Plotly figure: observed, background and each phase's reflections as sticks on the
        background at their matched positions and heights (no calculated curve, no difference)."""
        import plotly.graph_objects as go

        from dara.plot import AXIS, FONT, GRID, phase_display_name

        fit = self.final_result
        x, bkg = fit.plot_data.x, fit.plot_data.y_bkg
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=fit.plot_data.y_obs, mode="markers", name="Observed",
                                 marker=dict(color="rgba(0,0,0,.75)", size=3, symbol="circle")))
        fig.add_trace(go.Scatter(x=x, y=bkg, mode="lines", name="Background", opacity=0.8,
                                 line=dict(color="#8c8c8c", width=1, dash="dash")))
        for i, phase in enumerate(self.phases):
            r = phase["reflections"]
            tt, height = np.asarray(r["two_theta"], float), np.asarray(r["intensity"], float)
            base = np.interp(tt, x, bkg)
            gap = np.full(tt.shape, np.nan)
            label = phase_display_name(phase["name"])
            data = [[h, k, l, value] for (h, k, l), value in zip(r["hkl"], height)]
            fig.add_trace(go.Scatter(
                x=np.repeat(tt, 3), y=np.column_stack([base, base + height, gap]).ravel(), mode="lines",
                line=dict(color=COLORS[i % len(COLORS)], width=1.5), name=label, legendgroup=phase["name"],
                meta="reference-sticks", customdata=[row for row in data for _ in range(3)],
                hovertemplate=("2θ %{x:.4f}°<br>hkl (%{customdata[0]} %{customdata[1]} %{customdata[2]})"
                               "<br>Net height: %{customdata[3]:.4g}<extra>" + label + "</extra>"),
            ))
        fig.update_layout(
            autosize=True, height=640, margin=dict(l=80, r=24, t=40, b=60), hovermode="closest",
            dragmode="zoom", plot_bgcolor="white", legend_title="",
            font=dict(family=FONT, size=12, color="rgba(0,0,0,.88)"),
            xaxis=dict(range=[float(min(x)), float(max(x))], showline=True, linewidth=1, linecolor=AXIS,
                       mirror=True, showgrid=False, title=dict(text="2θ (°)", standoff=8)),
            yaxis=dict(showline=True, linewidth=1, linecolor=AXIS, mirror=True, title="Intensity", gridcolor=GRID),
            legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom", groupclick="togglegroup",
                        font=dict(size=12), borderwidth=0, bgcolor="rgba(0,0,0,0)"),
        )
        fig.update_xaxes(ticks="outside", tickwidth=1, tickcolor=AXIS, ticklen=5)
        fig.update_yaxes(ticks="outside", tickwidth=1, tickcolor=AXIS, ticklen=5)
        return fig


# Background of identification tasks stored before the settings were recorded (the default then).
LEGACY_BACKGROUND = {"method": "rolling_minimum"}


def identify(angles, intensities, cifs, sources, *, wavelength="Cu", wmin=None, wmax=None,
             excluded_ranges=None, background=None) -> tuple[list[dict], float]:
    """``(phases, explained)`` of the accepted reference structures ``cifs`` (``sources``: their
    ``cod:``/``mp:`` ids, cached, in the same order).

    Each phase is scored on the whole net pattern as in the ranking (lattice scale, shift,
    amplitude, matched/expected strong peaks); ``gain`` and ``explained`` follow the greedy
    combination's subtraction in the given order (:func:`quick_rank.residual_step`). Raises
    ``ValueError`` when the pattern or a phase cannot be matched in the analysis range.
    """
    from dara.rir import reflection_lines
    from dara.search import quick_rank as qr
    from dara.search.prefilter import _merge_reflections

    from dara.search.prefilter import background_settings

    ctx, caveat = qr._context(angles, intensities, wavelength=wavelength, wmin=wmin, wmax=wmax,
                              excluded_ranges=excluded_ranges, background=background_settings(background))
    if ctx is None:
        raise ValueError(caveat)
    lam1, lo, hi = ctx.lam1, ctx.profile.lo, ctx.profile.hi
    edge = qr.CONFIG.edge_margin_deg
    stick_range = (max(1.0, lo - 6.0), min(178.0, hi + 6.0))  # as quick_ranking
    candidates, phases = [], []
    for cif, source in zip(cifs, sources):
        name = cif.filename
        sticks = qr.simulate_sticks(source, qr.reference_path(source), lam1, stick_range)
        if sticks is None:
            raise ValueError(f"The reflections of {name} could not be calculated.")
        two_theta, intensity, d = (np.asarray(v, float) for v in sticks)
        ok = np.isfinite(two_theta) & np.isfinite(intensity) & np.isfinite(d) & (d > 0) & (intensity > 0)
        structure = cif.to_structure()
        cand = qr.Candidate(source=source, formula=structure.composition.reduced_formula,
                            spacegroup=_spacegroup_of(name), path=qr.reference_path(source),
                            sticks=_merge_reflections(two_theta[ok], intensity[ok], d[ok], qr.CONFIG.merge_deg))
        result = qr._score(ctx, cand)
        if result is None or result["model"] is None:
            raise ValueError(f"{name} has too few strong reflections in the analysis range to be matched.")
        candidates.append(cand)
        scale, shift, amplitude = result["scale"], result["shift"], result["amplitude"]
        # shortcut: multiplicity counts the reciprocal points of a d with the strongest |F|² (rir.reflection_lines);
        # an accidental equal-|F|² coincidence counts with it. Use the space-group operations if exact m matters.
        lines = reflection_lines(structure, lam1, two_theta_max=min(179.0, hi + 5.0), merge_deg=REFLECTION_MERGE_DEG)
        pos, reachable = qr._positions(lines["d"], scale, shift, lam1)
        # heights on the scoring's scale: the strongest 0.1-degree line inside the range = amplitude
        inside = (lines["two_theta"] > lo + edge) & (lines["two_theta"] < hi - edge)
        _, merged, _ = _merge_reflections(lines["two_theta"][inside], lines["intensity"][inside],
                                          lines["d"][inside], qr.CONFIG.merge_deg)
        top = float(merged.max()) if len(merged) else float(lines["intensity"].max())
        keep = np.flatnonzero(reachable & (pos >= lo) & (pos <= hi))
        phases.append({
            "name": name, "source": source, "formula": cand.formula, "spacegroup": cand.spacegroup,
            "score": round(result["score"], 4), "explained": round(result["explained"], 4),
            "matched": result["matched"], "expected": result["expected"], "lattice_scale": round(scale, 4),
            "shift": round(shift, 3), "amplitude": round(amplitude, 3),
            "reflections": {
                "two_theta": [round(float(pos[i]), 4) for i in keep],
                "d": [round(float(scale * lines["d"][i]), 5) for i in keep],
                "hkl": [list(lines["hkl"][i]) for i in keep],
                "intensity": [round(float(amplitude * lines["intensity"][i] / top), 3) for i in keep],
                "multiplicity": [int(lines["multiplicity"][i]) for i in keep],
            },
        })
    combined = qr.residual_step(angles, intensities, candidates, list(sources), wavelength=wavelength,
                                wmin=wmin, wmax=wmax, excluded_ranges=excluded_ranges, max_rows=0)
    for phase, step in zip(phases, combined["steps"]):
        phase["gain"] = step["gain"]
    return phases, combined["explained"]
