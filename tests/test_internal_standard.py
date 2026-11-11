import pytest

from dara.server.internal_standard import quantify


def _phase(name, formula, weight, esd=0.0, **extra):
    return {
        "name": name,
        "formula": formula,
        "weight_percent": weight,
        "weight_esd_percent": esd,
        "weight_flag": None,
        "refinement_level": "high",
        **extra,
    }


SPEC = {"formula": "Si", "wt_percent": 10.0, "phase": None}


def test_ok_example_from_design():
    phases = [_phase("Si_1", "Si", 12.5, 0.3), _phase("A", "Fe2O3", 40.0, 1.0), _phase("B", "Y2O3", 47.5, 1.0)]
    out = quantify(phases, SPEC)
    assert out["status"] == "ok"
    assert out["standard_phase"] == "Si_1"
    assert out["scale"] == pytest.approx(0.8)
    assert out["amorphous"]["mixture_wt_percent"] == 20.0
    assert out["amorphous"]["sample_wt_percent"] == 22.2
    # standard row is left out; A: 40 * 0.8 = 32 in the mixture, 35.6 without the standard
    assert [p["name"] for p in out["phases"]] == ["A", "B"]
    assert out["phases"][0]["absolute_wt_percent"] == 32.0
    assert out["phases"][0]["sample_wt_percent"] == 35.6
    # sigma_k = 0.8 * 0.3 / 12.5; sigma_A = hypot(0.8, 40*sigma_k)
    assert out["phases"][0]["absolute_esd_percent"] == pytest.approx(1.11, abs=0.01)


def test_standard_not_found():
    out = quantify([_phase("A", "Fe2O3", 50.0)], SPEC)
    assert out["status"] == "standard_not_found"
    assert out["message"].startswith("The internal standard Si is not among the fitted phases")


def test_formula_is_reduced_before_matching():
    out = quantify([_phase("A", "Si2", 12.5)], SPEC)
    assert out["status"] == "ok"


def test_negative_amorphous():
    out = quantify([_phase("Si_1", "Si", 8.0, 0.2)], SPEC)
    assert out["status"] == "negative_amorphous"
    assert out["amorphous"]["mixture_wt_percent"] == -25.0
    assert "higher than added" in out["message"]


def test_small_negative_is_not_detected():
    out = quantify([_phase("Si_1", "Si", 9.8, 0.5)], SPEC)
    assert out["status"] == "ok"
    assert out["amorphous"]["not_detected"] is True
    assert out["amorphous"]["mixture_wt_percent"] < 0


def test_ambiguous_then_resolved_by_phase():
    phases = [_phase("Si_a", "Si", 12.0), _phase("Si_b", "Si", 3.0)]
    out = quantify(phases, SPEC)
    assert out["status"] == "ambiguous_standard"
    assert out["candidates"] == ["Si_a", "Si_b"]
    out = quantify(phases, {**SPEC, "phase": "Si_a"})
    assert out["status"] == "ok"
    assert out["standard_phase"] == "Si_a"
    assert [p["name"] for p in out["phases"]] == ["Si_b"]


def test_unreliable_standard_still_computed():
    out = quantify([_phase("Si_1", "Si", 12.5, 0.3, weight_flag="at_limit")], SPEC)
    assert out["status"] == "standard_unreliable"
    assert out["scale"] == pytest.approx(0.8)


def test_invalid_amount_raises():
    with pytest.raises(ValueError):
        quantify([], {**SPEC, "wt_percent": 100})
