import pytest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from dara.plot import peaks_in_fit_range, visualize


def test_practical_plot_layout_preserves_profiles_and_reflections():
    names = [f"phase_{i}" for i in range(12)]
    result = SimpleNamespace(
        plot_data=SimpleNamespace(
            x=[10., 20., 30.], y_obs=[5., 20., 8.],
            y_calc=[6., 18., 9.], y_bkg=[1., 1., 1.],
            structs={name: [1., 2., 1.] for name in names},
        ),
        lst_data=SimpleNamespace(pattern_name="test", rwp=8.),
        peak_data=pd.DataFrame([
            {"phase": name, "2theta": 20., "intensity": 2.} for name in names
        ]),
        get_phase_weights=lambda: {},
    )
    fig = visualize(result, diff_offset=True,
                    missing_peaks=[[20., 0.], [31., 1.]],
                    extra_peaks=[[9., 1.]],
                    intensity_mismatch_peaks=[[10., 0.], [40., 0.]])
    diff = next(trace for trace in fig.data if trace.name == "Difference")
    np.testing.assert_equal(diff.y, [-1., 2., -1.])
    assert diff.yaxis == "y2"
    reflections = [trace for trace in fig.data if trace.meta == "reflection"]
    assert len({trace.y[0] for trace in reflections}) == 12
    assert all(trace.visible == "legendonly" and trace.yaxis is None for trace in reflections)
    assert "hkl" in reflections[0].hovertemplate
    missing = next(trace for trace in fig.data if trace.name == "Unexplained peaks")
    assert missing.y[0] == 0.
    assert missing.visible is True
    assert list(missing.x) == [20.]
    assert len(next(t for t in fig.data if t.name == "Extra peaks").x) == 0
    assert list(next(t for t in fig.data if t.name == "Intensity mismatch").x) == [10.]
    assert fig.layout.xaxis2.matches == "x"
    assert fig.layout.xaxis2.title.text == "2θ (°)"
    assert fig.layout.yaxis2.title.text == "Obs − calc"
    assert next(t for t in fig.data if t.name == "Observed").marker.color == "rgba(0,0,0,.75)"
    assert next(t for t in fig.data if t.name == "Calculated").line.color == "#d4380d"
    assert not any("Missing" in str(t.name) for t in fig.data)
    assert "RebeccaPurple" not in fig.to_json()
    assert not {"blue", "green"} & {
        getattr(getattr(t, k), "color", None) for t in fig.data for k in ("marker", "line")}
    assert any(t.fill == "tonexty" and t.visible == "legendonly" and t.legendgroup in names for t in fig.data)


def test_peak_range_boundaries_empty_and_nonfinite():
    peaks = [[9., 1.], [10., 2.], [30., 3.], [31., 4.], [np.nan, np.nan]]
    np.testing.assert_equal(peaks_in_fit_range(peaks, [30., 10.]), [[10., 2.], [30., 3.]])
    assert peaks_in_fit_range(None, [10., 30.]) is None
    assert peaks_in_fit_range([], [10., 30.]).shape == (0, 2)
    assert peaks_in_fit_range(peaks, []).shape == (0, 2)


@pytest.mark.parametrize("symbol,html,mathtext", [
    ("P6_3mc", "<i>P</i>6<sub>3</sub><i>mc</i>", r"$\mathit{P}\mathrm{6}_{\mathrm{3}}\mathit{mc}$"),
    ("Fd-3m", '<i>Fd</i><span style="text-decoration:overline">3</span><i>m</i>', r"$\mathit{Fd}\overline{\mathrm{3}}\mathit{m}$"),
    ("P2_1/c", "<i>P</i>2<sub>1</sub>/<i>c</i>", None),
    ("P21/c", "<i>P</i>2<sub>1</sub>/<i>c</i>", None),
    ("Ia-3", '<i>Ia</i><span style="text-decoration:overline">3</span>', None),
    ("R-3m:H", '<i>R</i><span style="text-decoration:overline">3</span><i>m</i>', None),
    ("Pmn2_1", "<i>Pmn</i>2<sub>1</sub>", None),
    ("P63/mmc", "<i>P</i>6<sub>3</sub>/<i>mmc</i>", r"$\mathit{P}\mathrm{6}_{\mathrm{3}}\mathrm{/}\mathit{mmc}$"),
    ("C12/c1", "<i>C</i>2/<i>c</i>", None),
    ("P312", "<i>P</i>312", None),  # not P3₁2: the symbol table decides
    ("P-421m", '<i>P</i><span style="text-decoration:overline">4</span>2<sub>1</sub><i>m</i>', None),
])
def test_space_group_symbols_follow_international_tables(symbol, html, mathtext):
    from dara.plot import spacegroup_html, spacegroup_mathtext

    assert spacegroup_html(symbol) == html
    if mathtext:
        assert spacegroup_mathtext(symbol) == mathtext
