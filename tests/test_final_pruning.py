from types import SimpleNamespace

from dara.final_pruning import phases_to_prune, refit_is_acceptable


def _phase(weight, a=0.5, group=14):
    return SimpleNamespace(gewicht=weight, a=a, spacegroup_no=group)


def test_task43_like_fit_drops_failed_and_duplicate_phases():
    # Li2CO3-ZrO2 benchmark: LiO8 collapsed (lattice undefined), ZrO2 P21/c twice.
    phases = {
        "ZrO2_14_(cod_1010912)-0": _phase(0.02, group=14),
        "ZrO2_14_(cod_2300296)-0": _phase(0.17, group=14),
        "Li2CO3_15_(cod_2310703)-0": _phase(1.47, group=15),
        "ZrO2_57_(cod_9005834)-None": _phase(0.007, group=57),
        "LiO8_8_(mp_mp-1235059)-0": _phase(0.0, a=None, group=8),
    }
    weights = {"ZrO2_14_(cod_1010912)-0": 0.0124, "ZrO2_14_(cod_2300296)-0": 0.1016,
               "Li2CO3_15_(cod_2310703)-0": 0.882, "ZrO2_57_(cod_9005834)-None": 0.0041,
               "LiO8_8_(mp_mp-1235059)-0": 0.0}
    drop = phases_to_prune(phases, weights)
    assert drop == {
        "LiO8_8_(mp_mp-1235059)-0": "failed",
        "ZrO2_57_(cod_9005834)-None": "negligible",
        "ZrO2_14_(cod_1010912)-0": "duplicate",
    }
    # Tried in this order: failed first, then the lightest.
    assert list(drop) == ["LiO8_8_(mp_mp-1235059)-0", "ZrO2_57_(cod_9005834)-None", "ZrO2_14_(cod_1010912)-0"]


def test_negligible_protected_and_last_phase():
    phases = {"A2O3_167_(cod_1)-0": _phase(1.0, group=167), "BO_225_(cod_2)-0": _phase(0.0001, group=225)}
    weights = {"A2O3_167_(cod_1)-0": 0.997, "BO_225_(cod_2)-0": 0.003}
    assert phases_to_prune(phases, weights) == {"BO_225_(cod_2)-0": "negligible"}
    assert phases_to_prune(phases, weights, protected={"BO_225_(cod_2)-0"}) == {}
    # Polymorphs (other space group) of one formula both stay.
    poly = {"TiO2_141_(cod_1)-0": _phase(1.0, group=141), "TiO2_136_(cod_2)-0": _phase(1.0, group=136)}
    assert phases_to_prune(poly, {"TiO2_141_(cod_1)-0": 0.5, "TiO2_136_(cod_2)-0": 0.5}) == {}
    # Never everything: the heaviest phase remains.
    lone = {"X_1_(cod_1)-0": _phase(0.0, a=None, group=1)}
    assert phases_to_prune(lone, {"X_1_(cod_1)-0": 1.0}) == {}
    assert phases_to_prune({}, {}) == {}


def test_refit_acceptance():
    assert refit_is_acceptable(4.49, 4.53)
    assert refit_is_acceptable(10.0, 10.09)
    assert not refit_is_acceptable(4.49, 4.60)
