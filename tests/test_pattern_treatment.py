"""Kα2 stripping and smoothing of the displayed observed pattern (display treatments)."""

import asyncio

import numpy as np
import pytest
from fastapi import HTTPException

from dara.export.pattern_treatment import kalpha_pair, smooth, strip_kalpha2, treat, treatment_text
from dara.server import api_router
from test_observed_peaks_and_pattern_data import loaded  # noqa: F401 - fixture

CU1, CU2 = kalpha_pair("Cu")


def _pseudo_voigt(x, center, fwhm=0.08, eta=0.5):
    gauss = np.exp(-4 * np.log(2) * ((x - center) / fwhm) ** 2)
    lorentz = 1 / (1 + 4 * ((x - center) / fwhm) ** 2)
    return eta * lorentz + (1 - eta) * gauss


def _kalpha2_angle(two_theta):
    return np.degrees(2 * np.arcsin(np.sin(np.radians(two_theta / 2)) * CU2 / CU1))


@pytest.mark.parametrize("center", [30.0, 75.0])
def test_strip_kalpha2_removes_the_doublet(center):
    x = np.arange(center - 3, center + 3, 0.01)
    alpha1 = 1000 * _pseudo_voigt(x, center)
    alpha2 = 500 * _pseudo_voigt(x, _kalpha2_angle(center))
    stripped = strip_kalpha2(x, alpha1 + alpha2, "Cu")
    area = lambda y: y.sum() * 0.01  # noqa: E731
    assert area(np.abs(stripped - alpha1)) < 0.05 * area(alpha2)  # what is left of Kα2
    assert abs(area(stripped) - area(alpha1)) < 0.02 * area(alpha1)
    assert stripped.min() >= 0


def test_strip_is_offered_only_for_lab_anodes():
    assert kalpha_pair("cu") == (1.540598, 1.544426) and kalpha_pair("Mo")[1] == 0.713609
    assert kalpha_pair(0.0826) is None and kalpha_pair("0.0826") is None
    with pytest.raises(ValueError):
        strip_kalpha2([10.0, 10.1], [1.0, 1.0], 0.0826)


def test_strip_with_background_keeps_the_background():
    x = np.arange(20.0, 40.0, 0.02)
    background = 50 + 0.5 * x
    measured = background + 1000 * _pseudo_voigt(x, 30) + 500 * _pseudo_voigt(x, _kalpha2_angle(30))
    treated = treat(x, measured, "Cu", kalpha2=True, background=background)
    far = np.abs(x - 30) > 2
    assert np.abs(treated[far] - background[far]).max() < 1  # peak tails only; plain Rachinger would drop it by 1/3
    assert treat(x, measured, "Cu", kalpha2=True)[far].mean() < 0.7 * background[far].mean()
    assert treat(x, measured, "Cu").tolist() == measured.tolist()  # no treatment


def test_smooth_keeps_a_quadratic_and_reduces_noise():
    x = np.linspace(0, 1, 400)
    quadratic = 3 + 2 * x - 5 * x**2
    assert np.allclose(smooth(quadratic, 11), quadratic)
    noisy = quadratic + np.random.default_rng(0).normal(0, 0.1, x.size)
    assert np.std(smooth(noisy, 11) - quadratic) < 0.6 * np.std(noisy - quadratic)
    for window in (4, 3, 27):
        with pytest.raises(ValueError):
            smooth(quadratic, window)
    assert treatment_text(True, 11) == "Kα2 stripped, smoothed (11 points)"
    assert treatment_text(False, None) == ""


def test_treated_pattern_endpoints(loaded):  # noqa: F811
    body = asyncio.run(api_router.task_pattern_treated(5, None, True, 11))
    assert body["treatment"] == "Kα2 stripped, smoothed (11 points)"
    assert len(body["two_theta"]) == len(loaded.plot_data.x) == len(body["intensity"])
    assert min(body["intensity"]) >= 0
    response = asyncio.run(api_router.task_pattern_xy(5, "measured", None, True, 11))
    lines = response.body.decode().splitlines()
    assert "Ka2 stripped" in lines[0] and "Savitzky-Golay, order 2, 11 points" in lines[0]
    assert 'filename="dara_task_5_a_b_measured_ka2stripped_smooth11.xy"' in response.headers["content-disposition"]
    response = asyncio.run(api_router.task_pattern_xy(5, "background_subtracted", None, False, 7))
    assert 'filename="dara_task_5_a_b_bgsub_smooth7.xy"' in response.headers["content-disposition"]
    data = np.loadtxt(response.body.decode().splitlines()[1:])
    net = np.asarray(loaded.plot_data.y_obs) - np.asarray(loaded.plot_data.y_bkg)
    assert np.allclose(data[:, 1], smooth(net, 7), atol=1e-4)
    for args in ((True, 4), (False, 27)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(api_router.task_pattern_treated(5, None, *args))
        assert error.value.status_code == 422


def test_strip_is_refused_for_a_numeric_wavelength(loaded, monkeypatch):  # noqa: F811
    from dara.server import review_router

    task, document = asyncio.run(review_router._load_completed_task(5))
    document.search_kwargs = {"wavelength": "0.0826"}
    with pytest.raises(HTTPException) as error:
        asyncio.run(api_router.task_pattern_treated(5, None, True, None))
    assert error.value.status_code == 422
    assert asyncio.run(api_router.task_pattern_treated(5, None, False, 5))["treatment"] == "smoothed (5 points)"
