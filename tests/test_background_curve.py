"""User-defined background curve: anchors, control file (UNT/RU=2), quick refine, report."""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.interpolate import PchipInterpolator

from dara import background_curve
from dara.generate_control_file import generate_control_file
from dara.server import quick_refine
from dara.server.quick_refine import QuickBackground, QuickRefineRequest

DATA = Path(__file__).parent / "test_data"
INSTRUMENT = "Aeris-fds-Pixcel1d-Medipix3"
ANCHORS = [[20.0, 300.0], [35.0, 250.0], [50.0, 260.0], [70.0, 200.0]]


# --------------------------------------------------------------------------- curve and validation
def test_validate_anchors_accepts_and_converts():
    assert background_curve.validate_anchors([[20, 1], [30, 0]], lo=20, hi=30) == [[20.0, 1.0], [30.0, 0.0]]


@pytest.mark.parametrize("anchors, kwargs, text", [
    ([[20, 1]], {}, "2 to 40"),
    ([[float(i), 1.0] for i in range(41)], {}, "2 to 40"),
    ([[20, 1], [20, 2]], {}, "sorted"),
    ([[30, 1], [20, 2]], {}, "sorted"),
    ([[20, -1], [30, 2]], {}, "negative"),
    ([[20, 1], [30, float("nan")]], {}, "finite"),
    ([[20, 1], [30]], {}, "pair"),
    ([[20, True], [30, 1]], {}, "pair"),
    ([[20, 1], [30, 1]], {"lo": 21}, "outside the refined range"),
    ([[20, 1], [30, 1]], {"hi": 29}, "outside the refined range"),
    ([[20, 1], [30, 1]], {"excluded_ranges": [[25, 35]]}, "inside the excluded range"),
    ("20,1", {}, "list"),
])
def test_validate_anchors_rejects(anchors, kwargs, text):
    kwargs = {"lo": 0, "hi": 100, **kwargs}
    with pytest.raises(ValueError, match=text):
        background_curve.validate_anchors(anchors, **kwargs)


def test_curve_is_scipy_pchip_held_flat_beyond_the_ends():
    x = np.linspace(10, 80, 500)
    y = background_curve.curve(ANCHORS, x)
    inner = (x >= 20) & (x <= 70)
    a = np.asarray(ANCHORS)
    assert np.allclose(y[inner], PchipInterpolator(a[:, 0], a[:, 1])(x[inner]))
    assert np.all(y[x < 20] == 300.0) and np.all(y[x > 70] == 200.0)
    assert background_curve.curve([[0, 0], [1, 0]], [0.5])[0] == 0.0


def test_js_fixture_matches_scipy():
    """The JavaScript port is tested against this fixture (tests/test_background_anchors.mjs)."""
    cases = json.loads((DATA / "pchip_fixture.json").read_text())["cases"]
    for case in cases:
        assert np.allclose(background_curve.curve(case["anchors"], case["x"]), case["y"], atol=1e-9)


def test_fitted_scale_reads_back_scale_and_constant():
    x = np.linspace(20, 70, 300)
    user = background_curve.curve(ANCHORS, x)
    assert background_curve.fitted_scale(x, 1.2 * user + 5.0, ANCHORS) == (1.2, 5.0)
    assert background_curve.fitted_scale(x, user, [[20, 1], [70, 1]]) == (None, None)  # flat curve


def test_record_label_caution_and_sentence():
    rec = background_curve.record(ANCHORS, 1.003, -0.2)
    assert rec == {"mode": "manual", "anchors": ANCHORS, "interpolation": "pchip", "ru": 2,
                   "scale": 1.003, "constant": -0.2}
    assert background_curve.label(ANCHORS * 2 + ANCHORS[:2]) == "Background: manual (10 points)"
    assert background_curve.shift_caution(0.08) is None and background_curve.shift_caution(None) is None
    assert "15%" in background_curve.shift_caution(0.15, 23.4) and "23.40°" in background_curve.shift_caution(0.15, 23.4)
    assert background_curve.methods_sentence(10, 0.0042) == (
        "The background was a user-defined curve (10 anchor points, PCHIP interpolation) refined "
        "by BGMN with a scale and an additive constant; the fitted background stayed within 0.4% "
        "of the drawn curve."
    )


def test_curve_shift_ignores_compensating_scale_and_unfitted_points():
    x = np.linspace(20, 70, 501)
    flat = [[20.0, 4000.0], [70.0, 3900.0]]
    user = background_curve.curve(flat, x)
    # scale 1.297 and the constant cancel on a nearly flat curve (0.4 % off): no caution
    fitted = 1.297 * user - 0.297 * 3950.0
    assert background_curve.fitted_scale(x, fitted, flat)[0] == 1.297
    shift, _ = background_curve.curve_shift(x, fitted, flat)
    assert shift < 0.005 and background_curve.shift_caution(shift) is None
    bump = user + np.where(np.abs(x - 30) < 1, 1000.0, 0.0)
    shift, at = background_curve.curve_shift(x, bump, flat)
    assert shift > 0.10 and 29 <= at <= 31
    # the bump lies in an excluded range / outside WMIN: not a fitted point
    assert background_curve.curve_shift(x, bump, flat, {"excluded_ranges": [[28, 32]]})[0] == 0.0
    assert background_curve.curve_shift(x, bump, flat, {"wmin": 35.0})[0] == 0.0


def test_version_summary_carries_the_curve_shift_caution():
    from dara.server import versions

    record = {"refinement_params": {"background_anchors": ANCHORS}}
    assert versions._curve_shift_caution(record, _result(ANCHORS, 1.0, 0.0)) is None
    assert "largest at" in versions._curve_shift_caution(record, _result(ANCHORS, 1.5, 0.0))
    assert versions._curve_shift_caution({"refinement_params": {}}, _result(ANCHORS)) is None


# --------------------------------------------------------------------------- control file
@pytest.fixture
def workdir(tmp_path):
    for name in ("BiFeO3.str", "BiFeO3.xy"):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path


def _control(workdir, **kwargs):
    return generate_control_file(
        workdir / "BiFeO3.xy", [workdir / "BiFeO3.str"], INSTRUMENT, workdir, n_threads=1, **kwargs
    ).read_text()


def test_anchors_write_dense_unt_file_and_ru2(workdir):
    data = np.loadtxt(workdir / "BiFeO3.xy")
    lo, hi = float(data[0, 0]), float(data[-1, 0])
    anchors = [[lo, 100.0], [(lo + hi) / 2, 80.0], [hi, 60.0]]
    text = _control(workdir, background_anchors=anchors, excluded_ranges=[[24, 31]])
    assert "CUT[1]=24:31\nUNT=user_bkg.xy\nRU=2\n% Phases" in text
    assert text.count("RU=") == 1
    curve = np.loadtxt(workdir / "user_bkg.xy")
    assert curve.shape == (len(data), 2)  # every data point, not just the anchors
    assert np.allclose(curve[:, 0], data[:, 0], atol=1e-6)
    assert np.allclose(curve[:, 1], background_curve.curve(anchors, data[:, 0]), atol=1e-5)


def test_anchors_and_degree_are_exclusive(workdir):
    with pytest.raises(ValueError, match="either"):
        _control(workdir, background_anchors=ANCHORS, background_degree=6)


def test_anchors_are_checked_against_range_and_cut(workdir):
    with pytest.raises(ValueError, match="outside the refined range"):
        _control(workdir, background_anchors=ANCHORS, wmin=25.0)
    with pytest.raises(ValueError, match="excluded range"):
        _control(workdir, background_anchors=ANCHORS, excluded_ranges=[[30, 40]])


def test_no_anchors_writes_no_curve(workdir):
    assert "UNT=" not in _control(workdir)
    assert not (workdir / "user_bkg.xy").exists()


# --------------------------------------------------------------------------- quick refine request
def test_request_accepts_anchors():
    background = QuickBackground(anchors=ANCHORS)
    assert background.anchors == ANCHORS and background.interpolation == "pchip"
    request = QuickRefineRequest(background=background, origin="background")
    assert quick_refine.default_label(request) == "Background: manual (4 points)"
    with pytest.raises(ValidationError, match="either"):
        QuickBackground(anchors=ANCHORS, degree=6)
    with pytest.raises(ValidationError, match="sorted"):
        QuickBackground(anchors=ANCHORS[::-1])
    with pytest.raises(ValidationError):
        QuickBackground(anchors=ANCHORS, interpolation="linear")
    assert QuickBackground(anchors=None, degree=None).anchors is None


def test_merge_background_anchor_semantics():
    base = {"background_degree": 6, "excluded_ranges": [[10.0, 11.0]], "wmin": 12.0}
    manual = quick_refine.merge_background(base, QuickBackground(anchors=ANCHORS))
    assert manual["background_anchors"] == ANCHORS and "background_degree" not in manual
    assert manual["wmin"] == 12.0 and manual["excluded_ranges"] == [[10.0, 11.0]]
    # kept by any later request that does not touch the background model
    assert quick_refine.merge_background(manual, None)["background_anchors"] == ANCHORS
    kept = quick_refine.merge_background(manual, QuickBackground(excluded_ranges=[[80, 81]]))
    assert kept["background_anchors"] == ANCHORS
    # "Reset to automatic" (degree null) and an explicit degree both drop the curve
    reset = quick_refine.merge_background(manual, QuickBackground(degree=None))
    assert "background_anchors" not in reset and "background_degree" not in reset
    assert quick_refine.merge_background(manual, QuickBackground(degree=5)) == {**base, "background_degree": 5}
    assert "background_anchors" not in quick_refine.merge_background(manual, QuickBackground(anchors=None))


def test_check_anchors_uses_refined_range():
    angles = np.linspace(10, 80, 100)
    quick_refine.check_anchors({"background_anchors": ANCHORS}, angles)
    quick_refine.check_anchors({}, angles)
    with pytest.raises(quick_refine.QuickRefineError, match="outside"):
        quick_refine.check_anchors({"background_anchors": ANCHORS, "wmax": 60.0}, angles)
    with pytest.raises(quick_refine.QuickRefineError, match="excluded"):
        quick_refine.check_anchors({"background_anchors": ANCHORS, "excluded_ranges": [[30, 40]]}, angles)


def _result(anchors, scale=1.1, constant=3.0):
    x = np.linspace(20, 70, 200)
    return SimpleNamespace(plot_data=SimpleNamespace(
        x=x.tolist(), y_bkg=(scale * background_curve.curve(anchors, x) + constant).tolist()))


def test_background_record_manual_inherited_and_reset():
    manual_request = QuickRefineRequest(background=QuickBackground(anchors=ANCHORS), origin="background")
    params = {"background_anchors": ANCHORS, "excluded_ranges": [[40.0, 41.0]]}
    rec = quick_refine.background_record(manual_request, None, params, _result(ANCHORS))
    assert rec == {"degree": None, "excluded_ranges": [[40.0, 41.0]], "mode": "manual", "anchors": ANCHORS,
                   "interpolation": "pchip", "ru": 2, "scale": 1.1, "constant": 3.0}
    # A version refined from the manual one without a background request keeps the curve and
    # records its own scale.
    other = QuickRefineRequest(phases=[quick_refine.QuickPhase(ref="x")])
    inherited = quick_refine.background_record(other, {"background": rec}, params, _result(ANCHORS, 0.95))
    assert inherited["mode"] == "manual" and inherited["scale"] == 0.95
    reset = QuickRefineRequest(background=QuickBackground(degree=None), origin="background")
    assert quick_refine.background_record(reset, {"background": rec}, {}, _result(ANCHORS)) == {
        "degree": None, "excluded_ranges": []}
    assert quick_refine.background_record(other, None, {}, _result(ANCHORS)) is None


# --------------------------------------------------------------------------- report
def test_report_sentence_reads_deviation_from_the_fit():
    from dara.server.report.builder import _background_curve_text

    text = _background_curve_text(ANCHORS, _result(ANCHORS, 1.0, 3.0), {"wmin": 20.0})
    assert text == ("The background was a user-defined curve (4 anchor points, PCHIP interpolation) "
                    "refined by BGMN with a scale and an additive constant; the fitted background "
                    "stayed within 1.2% of the drawn curve.")
    assert _background_curve_text(ANCHORS, None).endswith("with a scale and an additive constant.")


# --------------------------------------------------------------------------- real BGMN
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("DARA_RUN_SLOW") != "1", reason="real BGMN run (DARA_RUN_SLOW=1)")
def test_curve_equal_to_refined_background_keeps_rwp(tmp_path):
    from dara.refine import do_refinement

    for name in ("BiFeO3.str", "BiFeO3.xy"):
        shutil.copy(DATA / name, tmp_path / name)
    params = {"n_threads": 1}
    auto = do_refinement(tmp_path / "BiFeO3.xy", [tmp_path / "BiFeO3.str"], working_dir=tmp_path / "auto",
                         instrument_profile=INSTRUMENT, refinement_params=params)
    x, bkg = np.asarray(auto.plot_data.x), np.asarray(auto.plot_data.y_bkg)
    idx = np.linspace(0, len(x) - 1, background_curve.MAX_ANCHORS).round().astype(int)
    anchors = [[float(x[i]), float(bkg[i])] for i in idx]
    manual = do_refinement(tmp_path / "BiFeO3.xy", [tmp_path / "BiFeO3.str"], working_dir=tmp_path / "manual",
                           instrument_profile=INSTRUMENT,
                           refinement_params={**params, "background_anchors": anchors})
    control = (tmp_path / "manual" / "BiFeO3.sav").read_text()
    assert "UNT=user_bkg.xy\nRU=2\n" in control
    assert abs(manual.lst_data.rwp - auto.lst_data.rwp) <= 0.05
    scale, _ = background_curve.fitted_scale(manual.plot_data.x, manual.plot_data.y_bkg, anchors)
    assert abs(scale - 1.0) < 0.05
