"""Quick ranking (search-match): scoring, grouping, residual combination, stick cache, API."""

import asyncio
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from dara.search import quick_rank as qr
from dara.search.prefilter import _merge_reflections

DATA = Path(__file__).parent / "test_data"
L1, L2 = 1.540598, 1.544426


def _two_theta(d, scale=1.0, lam=L1):
    return 2 * np.degrees(np.arcsin(lam / (2 * scale * np.asarray(d, float))))


def _pattern(phases, fwhm=0.1, seed=0):
    """Synthetic Cu K-alpha pattern: ``phases`` is ``[(d, rel, amount, lattice scale)]``."""
    x = np.arange(10.0, 80.0, 0.01)
    y = 50.0 + 0.2 * (80 - x)
    sig2 = (fwhm / 2.3548) ** 2
    for d, rel, amount, scale in phases:
        for lam, weight in ((L1, 1.0), (L2, 0.5)):
            for t, h in zip(_two_theta(d, scale, lam), rel):
                y = y + weight * amount * h * np.exp(-((x - t) ** 2) / (2 * sig2))
    rng = np.random.default_rng(seed)
    return x, rng.poisson(y).astype(float)


def _cand(source, formula, d, rel, sg=1, theoretical=False):
    d = np.asarray(d, float)
    tt = _two_theta(d)
    return qr.Candidate(
        source=source, formula=formula, spacegroup=sg, path=Path("unused.cif"),
        theoretical=theoretical, sticks=_merge_reflections(tt, np.asarray(rel, float) * 100, d, 0.1),
    )


A_D = [4.10, 3.20, 2.71, 2.45, 2.10, 1.83, 1.64, 1.52, 1.40]
A_I = [1.0, 0.45, 0.8, 0.3, 0.6, 0.25, 0.4, 0.2, 0.3]
B_D = [5.30, 3.55, 2.95, 2.30, 1.95, 1.71, 1.48]
B_I = [0.7, 1.0, 0.5, 0.65, 0.35, 0.3, 0.45]
C_D = [6.10, 3.85, 2.58, 2.20, 1.77, 1.58]  # decoy: nothing of it is measured
C_I = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3]


def _rank(cands, phases, **kw):
    x, y = _pattern(phases)
    return qr.rank(x, y, cands, wavelength="Cu", **kw)


def test_two_phase_pattern_ranks_both_true_phases_on_top():
    cands = [_cand("cod:3", "Cc", C_D, C_I), _cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I)]
    out = _rank(cands, [(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    assert {row["formula"] for row in out["ranking"][:2]} == {"Aa", "Bb"}
    top = out["ranking"][0]
    assert top["matched"] == top["expected"] and top["sticks"]
    assert [c["formula"] for c in out["combination"]][:2] == ["Aa", "Bb"]


def test_one_percent_lattice_shift_is_tolerated():
    cands = [_cand("cod:3", "Cc", C_D, C_I), _cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I)]
    out = _rank(cands, [(A_D, A_I, 2000, 1.01), (B_D, B_I, 1200, 1.0)])
    assert {row["formula"] for row in out["ranking"][:2]} == {"Aa", "Bb"}
    a = next(row for row in out["ranking"] if row["formula"] == "Aa")
    assert a["lattice_scale"] == pytest.approx(1.01, abs=0.003)


def test_candidate_missing_a_strong_peak_ranks_low():
    # three of A's strong lines plus a strongest line at d=3.0 where the pattern is flat
    # (with 4 strong lines no outlier is forgiven; with 5+ the worst 20 % are)
    bad = _cand("cod:4", "Dd", [3.0, 4.10, 2.71, 2.10], [1.0, 1.0, 0.8, 0.6])
    cands = [bad, _cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I)]
    out = _rank(cands, [(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    order = [row["formula"] for row in out["ranking"]]
    assert order.index("Dd") > order.index("Aa") and order.index("Dd") > order.index("Bb")
    dd = out["ranking"][order.index("Dd")]
    assert dd["score"] < 0.05


def test_duplicates_group_into_one_row_with_cod_representing_mp():
    cands = [
        _cand("mp:mp-9", "Aa", A_D, A_I, theoretical=True),
        _cand("cod:1", "Aa", A_D, A_I), _cand("cod:5", "Aa", A_D, A_I),
        _cand("cod:6", "Aa", A_D, A_I, sg=2),
        _cand("cod:2", "Bb", B_D, B_I),
    ]
    out = _rank(cands, [(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    rows = [row for row in out["ranking"] if row["formula"] == "Aa"]
    assert len(rows) == 2  # space groups 1 and 2
    first = next(row for row in rows if row["spacegroup"] == 1)
    assert first["source"].startswith("cod:") and first["duplicates"] == 2
    assert "mp:mp-9" in first["sources"] and not first["theoretical"]
    assert all("polymorph" in row["flags"] for row in rows)


def test_variant_flag_for_close_compositions():
    rows = qr.group_rows([
        (_cand("cod:1", "Fe2O3", A_D, A_I), {"score": 0.5}),
        (_cand("cod:2", "Fe7.9O12", B_D, B_I), {"score": 0.3}),
        (_cand("cod:3", "FeO", C_D, C_I), {"score": 0.2}),
    ])
    flags = {row["key"][0]: row["flags"] for row in rows}
    assert flags["Fe7.9O12"] == ["variant of Fe2O3"] and flags["FeO"] == []


def test_residual_second_step_picks_the_minor_phase_not_a_duplicate():
    cands = [_cand("cod:1", "Aa", A_D, A_I), _cand("cod:7", "Aa", A_D, A_I, sg=2), _cand("cod:2", "Bb", B_D, B_I),
             _cand("cod:3", "Cc", C_D, C_I)]
    out = _rank(cands, [(A_D, A_I, 3000, 1.0), (B_D, B_I, 600, 1.0)])
    combo = out["combination"]
    assert combo[0]["formula"] == "Aa" and combo[1]["formula"] == "Bb"
    assert combo[1]["gain"] >= qr.STOP_GAIN
    assert all(c["formula"] != "Cc" for c in combo)
    assert combo[-1]["explained_total"] <= 1.0


def test_combination_step_reports_the_ranking_rows_representative():
    # cod:8 also claims A's two strongest lines: it represents the Bb row on the full pattern,
    # but on the residual (A removed) the plain cod:2 of the same group scores higher
    b_overlap = _cand("cod:8", "Bb", B_D + [4.10, 2.71], B_I + [1.0, 0.9])
    cands = [_cand("cod:1", "Aa", A_D, A_I), b_overlap, _cand("cod:2", "Bb", B_D, B_I)]
    out = _rank(cands, [(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    row = next(r for r in out["ranking"] if r["formula"] == "Bb")
    step = next(c for c in out["combination"] if c["formula"] == "Bb")
    assert step["source"] == row["source"] and "picked" in row["flags"]


def test_grid_points_with_neutral_peaks_do_not_hide_a_perfect_match():
    # at lattice scales < 1 both strong lines move into the excluded ranges (K = inf there)
    d = [_two_theta_inv(30.0), _two_theta_inv(60.0)]
    x, y = _pattern([(d, [1.0, 0.8], 2000, 1.0)])
    out = qr.rank(x, y, [_cand("cod:1", "Aa", d, [1.0, 0.8])], wavelength="Cu",
                  excluded_ranges=[[30.5, 32.0], [61.0, 64.0]])
    assert out["ranking"][0]["score"] > 0.3  # 0 while the inf grid points sorted first


def _two_theta_inv(two_theta, lam=L1):
    return lam / (2 * np.sin(np.radians(two_theta / 2)))


def test_flat_pattern_gives_a_caveat_not_a_ranking():
    x = np.arange(10, 80, 0.01)
    out = qr.rank(x, np.full_like(x, 100.0), [_cand("cod:1", "Aa", A_D, A_I)])
    assert out["ranking"] == [] and out["caveats"]


def test_stick_cache_roundtrip_and_failure_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("DARA_STICK_CACHE_DIR", str(tmp_path))
    calls = []

    def fake(path, lam, rng):
        calls.append(rng)
        return np.array([20.0, 30.0, 100.0]), np.array([100.0, 50.0, 10.0]), np.array([4.4, 3.0, 1.0])

    monkeypatch.setattr(qr, "simulate_reflections", fake)
    first = qr.simulate_sticks("cod:1", "x.cif", L1, (10.0, 86.0))
    assert calls == [(1.0, 90.0)] and list(first[0]) == [20.0, 30.0]
    again = qr.simulate_sticks("cod:1", "x.cif", L1, (12.0, 80.0))
    assert len(calls) == 1 and list(again[0]) == [20.0, 30.0]
    wider = qr.simulate_sticks("cod:1", "x.cif", L1, (10.0, 110.0))  # not covered: simulate again
    assert calls[-1] == (1.0, 130.0) and list(wider[0]) == [20.0, 30.0, 100.0]

    def broken(path, lam, rng):
        calls.append("broken")
        raise ValueError("no structure")

    monkeypatch.setattr(qr, "simulate_reflections", broken)
    assert qr.simulate_sticks("cod:2", "x.cif", L1, (10.0, 86.0)) is None
    assert qr.simulate_sticks("cod:2", "x.cif", L1, (10.0, 86.0)) is None
    assert calls.count("broken") == 1  # the failure is remembered


def test_reference_cifs_resolve_cached_cod_and_reject_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path))
    (tmp_path / "1234567.cif").write_text((DATA / "BiFeO3.cif").read_text())
    cifs = qr.reference_cifs(["cod:1234567"])
    formula, number, tag = cifs[0].filename.split("_", 2)
    assert sorted(formula) == sorted("BiFeO3") and number.isdigit() and tag == "(cod_1234567)"
    assert len(qr.reference_cifs(["cod:1234567", "cod:1234567 "])) == 1  # one phase, not two
    for bad in ("cod:7654321", "icsd:1", "mp:../x"):
        with pytest.raises(KeyError):
            qr.reference_cifs([bad])


def test_submit_reference_ids_become_a_known_phase_refinement(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from dara.server import api_router
    from dara.xrd import load_pattern

    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path))
    (tmp_path / "1234567.cif").write_text((DATA / "BiFeO3.cif").read_text())
    pattern = load_pattern(DATA / "BiFeO3.xy")
    job = api_router.build_submission_job(pattern, "BiFeO3.xy", use_mp=False, reference_ids='["cod:1234567"]')
    kwargs = job.function_kwargs
    assert kwargs["analysis_mode"] == "known_mixture" and kwargs["cif_dbs"] == []
    assert [c.filename for c in kwargs["fixed_phase_cifs"]][0].endswith("(cod_1234567)")
    assert job.metadata["submission_settings"]["reference_ids"] == ["cod:1234567"]
    with pytest.raises(HTTPException) as info:
        api_router.build_submission_job(pattern, "BiFeO3.xy", use_mp=False, reference_ids='["cod:999"]')
    assert info.value.status_code == 400


def test_router_pending_then_done(monkeypatch):
    import io

    from fastapi import HTTPException, UploadFile

    from dara.server import quick_rank_router as router_module

    release = threading.Event()
    runs = []

    def slow(params):
        runs.append(1)
        assert params["elements"] == ["Bi", "Fe", "O"] and params["use_mp"] is False
        release.wait(10)
        return {"status": "done", "pool": {"rows": 0}, "ranking": [], "combination": [], "elapsed_s": 0.1,
                "caveats": []}

    monkeypatch.setattr(router_module, "_run", slow)
    monkeypatch.setattr(router_module, "WAIT_S", 0.1)
    router_module._JOBS.clear()
    content = (DATA / "BiFeO3.xy").read_bytes()

    def upload(**fields):
        fields.setdefault("use_mp", False)
        return router_module.quick_ranking_upload(
            UploadFile(io.BytesIO(content), filename="BiFeO3.xy"),
            **{"precursor_formulas": "[]", "wavelength": "Cu", "two_theta_min": None, "two_theta_max": None,
               "excluded_ranges": "[]", "include_theoretical": False, "use_cod": True,
               "mp_e_hull_cutoff": 0.05, "max_mp_structures": 100, **fields},
        )

    async def scenario():
        first = await upload(elements='["Fe","Bi","O"]')
        assert first["status"] == "pending" and first["job_id"]
        release.set()
        for _ in range(50):
            polled = await router_module.quick_ranking_status(first["job_id"])
            if polled["status"] == "done":
                break
        assert polled["status"] == "done" and polled["ranking"] == []
        again = await upload(elements='["Fe","Bi","O"]')
        assert again["status"] == "done" and again["job_id"] == first["job_id"]  # same inputs reuse the job
        assert len(runs) == 1
        router_module._JOBS[first["job_id"]]["finished"] -= router_module.REUSE_DONE_S + 1
        third = await upload(elements='["Fe","Bi","O"]')
        for _ in range(50):
            if (await router_module.quick_ranking_status(third["job_id"]))["status"] == "done":
                break
        assert len(runs) == 2  # a finished ranking is not reused forever (stale caveats)
        with pytest.raises(HTTPException) as info:
            await upload(elements=json.dumps(["H", "Li", "Be", "B", "C", "N", "O", "F", "Na", "Mg", "Al", "Si", "P"]))
        assert info.value.status_code == 422 and "at most 12" in info.value.detail
        with pytest.raises(HTTPException) as info:
            await router_module.quick_ranking_status("unknown")
        assert info.value.status_code == 404
        with pytest.raises(HTTPException) as info:
            await upload(elements="[]")
        assert info.value.status_code == 400

    asyncio.run(scenario())


def test_prefilter_simulation_ignores_the_stick_cache(tmp_path, monkeypatch):
    from dara.search import prefilter

    monkeypatch.setenv("DARA_STICK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(qr, "simulate_reflections",
                        lambda path, lam, rng: (np.array([20.0, 30.0]), np.array([100.0, 50.0]), np.array([4.4, 3.0])))
    qr.simulate_sticks("cod:1234567", "x.cif", L1, (10.0, 86.0))
    # a search CIF may be repaired in place under the same tag: the normal search never reads
    # cached sticks, so the missing CIF fails instead of borrowing another structure's sticks
    with pytest.raises(Exception):
        prefilter.simulate_reflections(Path("Fe2O3_167_(cod_1234567)-0.cif"), L1, (14.0, 86.0))
