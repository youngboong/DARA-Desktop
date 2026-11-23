import re
import struct

import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace

from dara.server import review_router
from dara.server.report.figures import fit_figure

from test_report import A, B, _context, _refinement


def _fit(with_peaks=True):
    x = np.linspace(10, 80, 800)
    obs = 20 + 800 * np.exp(-0.5 * ((x - 30) / 0.1) ** 2)
    peaks = pd.DataFrame({
        "2theta": [30.0, 45.0, 50.0, 60.0, 65.0],
        "phase": [A, A, B, B, B],
    })
    refinement = SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs * 0.98, y_bkg=np.full_like(x, 20.0)),
        lst_data=SimpleNamespace(phases_results={A: object(), B: object()}),
    )
    if with_peaks:
        refinement.peak_data = peaks
    return refinement


def _png_size(content):
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", content[16:24])


def test_png_size_follows_width_and_dpi():
    content = fit_figure(_fit(), fmt="png", dpi=200, width_mm=100)
    width, height = _png_size(content)
    assert width == pytest.approx(100 / 25.4 * 200, abs=2)
    assert height == pytest.approx(width * 0.68, abs=3)
    assert content.startswith(b"\x89PNG")


def test_dpi_and_width_are_clamped():
    width, _ = _png_size(fit_figure(_fit(), fmt="png", dpi=5, width_mm=10))
    assert width == pytest.approx(40 / 25.4 * 72, abs=2)
    width, _ = _png_size(fit_figure(_fit(), fmt="png", dpi=72, width_mm=5000))
    assert width == pytest.approx(250 / 25.4 * 72, abs=2)


def test_svg_has_text_legend_and_one_tick_row_per_phase():
    svg = fit_figure(_fit(), fmt="svg", excluded_ranges=[[20.0, 25.0]]).decode("utf-8")
    assert "<svg" in svg
    assert "Excluded from refinement" in svg
    assert 'id="ticks-0"' in svg and 'id="ticks-1"' in svg and 'id="ticks-2"' not in svg
    for label in ("Observed", "Calculated", "Background", "Difference"):
        assert label in svg
    assert "Intensity (counts)" in svg
    assert "2θ (°)" in svg
    assert "Excluded from refinement" not in fit_figure(_fit(), fmt="svg").decode("utf-8")
    assert "Intensity (a.u.)" in fit_figure(_fit(), fmt="svg", counts_like=False).decode("utf-8")


def test_svg_is_deterministic_and_handles_missing_peak_data():
    first = fit_figure(_fit(), fmt="svg")
    assert first == fit_figure(_fit(), fmt="svg")
    svg = fit_figure(_fit(with_peaks=False), fmt="svg").decode("utf-8")
    assert "ticks-0" not in svg


def test_invalid_inputs_raise_value_error():
    with pytest.raises(ValueError):
        fit_figure(_fit(), fmt="gif")
    broken = _fit()
    broken.plot_data.x = np.array([np.nan, np.nan])
    with pytest.raises(ValueError):
        fit_figure(broken, fmt="png")


def test_phase_labels_override():
    svg = fit_figure(_fit(), fmt="svg", phase_labels={A: "Phase one", B: "Phase two"}).decode()
    assert "Phase one" in svg and "Phase two" in svg


# --- endpoint -----------------------------------------------------------------------

def test_figure_endpoint(monkeypatch):
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0), _refinement(8.0)]
    )
    png = client.get("/api/task/9/figure.png?dpi=100&width_mm=80")
    assert png.status_code == 200 and png.headers["Content-Type"] == "image/png"
    assert "dara_task_9_Sample_figure.png" in png.headers["Content-Disposition"]
    width, _ = _png_size(png.content)
    assert width == pytest.approx(80 / 25.4 * 100, abs=2)

    svg = client.get("/api/task/9/figure.svg?idx=1")
    assert svg.status_code == 200 and svg.headers["Content-Type"] == "image/svg+xml"
    assert "dara_task_9_Sample_figure_candidate_2.svg" in svg.headers["Content-Disposition"]
    assert b"Excluded from refinement" in svg.content

    pdf = client.get("/api/task/9/figure.pdf?width_mm=85")
    assert pdf.status_code == 200 and pdf.headers["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert client.get("/api/task/9/figure.gif").status_code == 404
    assert client.get("/api/task/9/figure.svg?idx=0&best=true").status_code == 422
    assert client.get("/api/task/9/figure.svg?idx=99").status_code == 404
    assert client.get("/api/task/77/figure.svg").status_code == 404


def test_figure_endpoint_reports_render_failures(monkeypatch):
    client, _, _ = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])

    def boom(*args, **kwargs):
        raise RuntimeError("no font")

    monkeypatch.setattr("dara.server.report.figures.fit_figure", boom)
    response = client.get("/api/task/9/figure.png")
    assert response.status_code == 500
    assert "Figure rendering failed: no font" in response.text


def test_report_falls_back_to_builtin_svg_when_matplotlib_fails(monkeypatch):
    client, _, _ = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])

    def boom(*args, **kwargs):
        raise RuntimeError("no font")

    monkeypatch.setattr("dara.server.report.builder.fit_figure", boom)
    html = client.get("/api/task/9/report?best=true").text
    assert "<svg " in html and "Excluded from refinement" in html
    # the figure caption stays below the figure; table captions sit above their tables
    assert re.search(r"</svg><figcaption>Figure 1\. ", html)
    assert re.search(r"<table><caption><b>Table 1\.</b> ", html)
    assert review_router._fit_svg  # fallback remains available


def test_journal_style_axes_fonts_and_formats():
    from dara.server.report.figures import PHASE_COLORS, radiation_text

    assert radiation_text("Cu") == "Cu Kα" and radiation_text("0.07293") == "λ = 0.7293 Å"
    assert radiation_text(None) is None and radiation_text("unknown") is None
    svg = fit_figure(_fit(), fmt="svg", radiation="Cu Kα").decode("utf-8")
    assert "2θ (°, Cu Kα)" in svg
    assert "Arial" in svg or "DejaVu Sans" in svg  # real text with the journal font (or its fallback)
    assert PHASE_COLORS[0].lower() in svg.lower()  # Okabe-Ito tick rows
    assert fit_figure(_fit(), fmt="pdf").startswith(b"%PDF")


def test_hkl_labels_stay_inside_the_frame_and_off_the_legend():
    """A Rietveld fit: each label sits on the curve and inside the frame, and the strongest label
    wins a collision."""
    import matplotlib

    matplotlib.use("Agg")
    x = np.linspace(10, 80, 1400)
    obs = 20 + 800 * np.exp(-0.5 * ((x - 30) / 0.1) ** 2) + 300 * np.exp(-0.5 * ((x - 78) / 0.1) ** 2)
    peaks = pd.DataFrame({"2theta": [30.0, 30.05, 78.0], "phase": [A, A, B], "intensity": [950.0, 100.0, 280.0],
                          "h": [1, 2, 3], "k": [0, 0, 1], "l": [1, 0, 1]})
    refinement = SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs * 0.98, y_bkg=np.full_like(x, 20.0)),
        lst_data=SimpleNamespace(phases_results={A: object(), B: object()}), peak_data=peaks,
    )
    svg = fit_figure(refinement, fmt="svg", hkl_labels=True).decode("utf-8")
    assert "(101)" in svg  # the strongest line
    assert "(200)" not in svg  # 0.05° from the strongest: left out
    # (311) sits under the legend at the top right only if it fits; never drawn across it
    from dara.server.report import figures

    seen = {}
    original = figures._draw_hkl_labels

    def spy(figure, ax, legend, *args, **kwargs):
        original(figure, ax, legend, *args, **kwargs)
        renderer = figure.canvas.get_renderer()
        frame = ax.get_window_extent(renderer)
        boxes = [text.get_window_extent(renderer) for text in ax.texts]
        seen["inside"] = all(box.y1 <= frame.y1 + 0.5 for box in boxes)
        seen["off_legend"] = not any(box.overlaps(legend.get_window_extent(renderer)) for box in boxes)
        seen["count"] = len(boxes)

    figures._draw_hkl_labels = spy
    try:
        fit_figure(refinement, fmt="png", dpi=100, hkl_labels=True)
    finally:
        figures._draw_hkl_labels = original
    assert seen == {"inside": True, "off_legend": True, "count": seen["count"]} and seen["count"] >= 1


# --- phase-identification figure ------------------------------------------------------

def _identification(two_phases=True):
    x = np.linspace(10, 80, 1400)
    obs = 300 + 2 * x + 800 * np.exp(-0.5 * ((x - 30) / 0.1) ** 2) + 300 * np.exp(-0.5 * ((x - 50) / 0.1) ** 2)
    peaks = pd.DataFrame({"2theta": [30.0, 30.05, 44.0, 50.0], "phase": [A, A, A, B],
                          "intensity": [950.0, 100.0, 20.0, 280.0],
                          "h": [1, 2, 1, 3], "k": [0, 0, 1, 1], "l": [1, 0, 1, 1]})
    phases = {A: object(), B: object()} if two_phases else {A: object()}
    return SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=None, y_bkg=np.full_like(x, 300.0)),
        lst_data=SimpleNamespace(phases_results=phases), peak_data=peaks[peaks["phase"].isin(phases)],
    )


def test_identification_figure_is_the_paper_style_one():
    """Measured line (raw), no background, no y numbers, one strip per phase with its reference bars,
    the phases named in a legend, Miller indices of every phase in its colour."""
    import matplotlib

    from dara.server.report import figures

    matplotlib.use("Agg")
    references = {A: (np.array([29.9, 44.0]), np.array([100.0, 40.0]))}
    captured = {}
    original = figures.pattern_figure

    def spy(patterns, phases, **kwargs):
        captured.update(patterns=patterns, phases=phases, kwargs=kwargs)
        return original(patterns, phases, **kwargs)

    figures.pattern_figure = spy
    try:
        svg = fit_figure(_identification(), fmt="svg", references=references).decode("utf-8")
        default = dict(captured)
        fit_figure(_identification(), fmt="svg", references=references, peaks="symbols")
        symbols = dict(captured)
    finally:
        figures.pattern_figure = original
    assert "Intensity (a.u.)" in svg and "Background" not in svg and "Observed" not in svg
    assert "(101)" in svg and "(200)" not in svg  # 0.05° from the strongest: left out
    assert "(311)" in svg  # the second phase is labelled too, in its colour
    assert "COD" not in svg  # the database record is in the report, never in the figure
    pattern, (strip_a, strip_b) = default["patterns"][0], default["phases"]
    assert list(strip_a["two_theta"]) == [29.9, 44.0]  # the reference pattern, not the matched lines
    assert list(strip_b["two_theta"]) == [50.0] and list(strip_b["intensity"]) == [100.0]  # fallback: the result
    assert default["kwargs"]["names"] == "legend" and default["kwargs"]["bars"] is True
    assert not pattern["marks"] and not default["kwargs"]["symbols"]
    assert np.allclose(pattern["y"], _identification().plot_data.y_obs)  # raw: no background subtracted
    # symbols: lines of at least 10 % of the phase's strongest, one per 0.3°, and no Miller indices
    marks = symbols["patterns"][0]["marks"]
    assert list(marks[0]) == [30.0] and list(marks[1]) == [50.0]
    assert symbols["kwargs"]["symbols"] is True and symbols["kwargs"]["hkl"] is None


def _stacked():
    x = np.linspace(20, 60, 400)
    one = {"x": x, "y": 10 + np.exp(-0.5 * ((x - 30) / 0.2) ** 2), "label": "S1 $", "marks": {0: [30.0], 1: [30.0]}}
    two = {"x": x, "y": 50 + 9 * np.exp(-0.5 * ((x - 40) / 0.2) ** 2), "label": "S2", "marks": {1: [40.0]}}
    strips = [{"label": "P1", "two_theta": [30.0], "intensity": [100.0]},
              {"label": "P2", "two_theta": [40.0], "intensity": [60.0]}]
    return one, two, strips


def test_pattern_figure_styles_and_stays_deterministic():
    from dara.server.report.figures import PHASE_COLORS, pattern_figure

    one, two, strips = _stacked()
    first = pattern_figure([one, two], strips, fmt="svg", normalize=True)
    assert first == pattern_figure([one, two], strips, fmt="svg", normalize=True)
    svg = first.decode("utf-8")
    assert "S1 $" in svg and "S2" in svg and "P1" in svg and "P2" in svg
    assert PHASE_COLORS[0].lower() in svg.lower() and PHASE_COLORS[1].lower() in svg.lower()
    single = pattern_figure([one], strips[:1], fmt="svg").decode("utf-8")
    assert PHASE_COLORS[0].lower() in single.lower()  # the strip
    width, height = _png_size(pattern_figure([one, two], strips, fmt="png", dpi=100, width_mm=85))
    assert width == pytest.approx(85 / 25.4 * 100, abs=2) and height > width * 0.5
    # every style draws, and each changes the figure
    styles = [{}, {"symbols": True}, {"names": "strips"}, {"bars": False}, {"names": "strips", "bars": False},
              {"symbols": True, "names": "strips"}]
    drawn = [pattern_figure([one, two], strips, fmt="svg", **style) for style in styles]
    assert len(set(drawn[:4])) == 4 and drawn[3] == drawn[4]  # without strips the names stay in the legend
    _, no_bars = _png_size(pattern_figure([one, two], strips, fmt="png", dpi=100, bars=False))
    _, with_bars = _png_size(pattern_figure([one, two], strips, fmt="png", dpi=100))
    assert no_bars < with_bars
    with pytest.raises(ValueError):
        pattern_figure([{"x": [1.0], "y": [1.0]}], [], fmt="svg")
    with pytest.raises(ValueError):
        pattern_figure([one], [], fmt="gif")


def test_legend_clears_the_curves_and_labels_under_it():
    """A strong peak and its Miller index under the upper-right legend: the top is raised above them."""
    import matplotlib

    from dara.server.report import figures

    matplotlib.use("Agg")
    x = np.linspace(20, 60, 800)
    pattern = {"x": x, "y": 10 + 100 * np.exp(-0.5 * ((x - 58) / 0.1) ** 2), "label": None}
    strips = [{"label": "A long phase name · COD 1234567", "two_theta": [58.0], "intensity": [100.0]},
              {"label": "Second phase · COD 7654321", "two_theta": [30.0], "intensity": [100.0]}]
    seen = {}
    original = figures._clear_legend

    def spy(figure, ax, legend, texts, points, bottom, top):
        original(figure, ax, legend, texts, points, bottom, top)
        renderer = figure.canvas.get_renderer()
        figure.canvas.draw()
        box = legend.get_window_extent(renderer)
        peak = ax.transData.transform((58.0, float(np.max(pattern["y"]) - 10) / 100))
        seen["peak_below"] = peak[1] < box.y0
        seen["texts_off"] = not any(text.get_window_extent(renderer).overlaps(box) for text in texts)
        seen["texts"] = len(texts)

    figures._clear_legend = spy
    try:
        figures.pattern_figure([pattern], strips, fmt="png", dpi=100, hkl=[(58.0, "(311)", 0)])
    finally:
        figures._clear_legend = original
    assert seen == {"peak_below": True, "texts_off": True, "texts": 1}


def test_figure_phase_label_is_name_formula_and_space_group_without_the_record():
    from dara.server.report.figures import phase_strip_label

    assert phase_strip_label("ZnO_186_(cod_9004178)-0") == r"Zincite, ZnO ($\mathit{P}\mathrm{6}_{\mathrm{3}}\mathit{mc}$)"
    assert phase_strip_label("NiO_225_(cod_1010093)-0") == r"NiO ($\mathit{Fm}\overline{\mathrm{3}}\mathit{m}$)"
    assert "mp-" not in phase_strip_label("La(HO)3_176_(mp_mp-625394)-0")
    assert phase_strip_label("ZnO_186_(cod_9004178)-0", "My $label") == r"My \$label"
