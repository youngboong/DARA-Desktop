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


# --------------------------------------------------------------------------- #
# fetch on demand, progress, cancel (offline: fake downloaders)
# --------------------------------------------------------------------------- #
FETCH_CIF = "data_x\n_cell_length_a 4.0\n_cell_length_b 4.0\n_cell_length_c 4.0\n"


@pytest.fixture
def fetch_caches(tmp_path, monkeypatch):
    from dara import known_phases, structure_db

    for name in ("COD", "MP", "STICK"):
        monkeypatch.setenv(f"DARA_{name}_CACHE_DIR", str(tmp_path / name.lower()))
    monkeypatch.setattr(structure_db.CODDatabase, "REQUEST_DELAY_S", 0)
    monkeypatch.setattr(known_phases, "mp_configured", lambda: False)
    monkeypatch.setattr(qr, "simulate_reflections",
                        lambda path, lam, rng: (np.array([20.0, 30.0]), np.array([100.0, 50.0]), np.array([4.4, 3.0])))

    def use_index(codes):
        class FakeDB:
            preparsed_info = {"Li-O": [("Li2O", code, 225, 0.0) for code in codes]}

        monkeypatch.setattr(structure_db, "shared_cod_database", lambda: FakeDB())

    return tmp_path, use_index


def _fake_cod(monkeypatch, calls, failing=()):
    from dara import structure_db

    lock = threading.Lock()

    def download(cod_id, max_attempts=3):
        with lock:
            calls.append(cod_id)
        if cod_id in failing:
            raise ConnectionError("connection reset")
        return FETCH_CIF

    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod", staticmethod(download))


def test_missing_cifs_are_fetched_on_demand_and_failures_counted(fetch_caches, monkeypatch):
    from dara import known_phases, structure_db

    tmp, use_index = fetch_caches
    use_index(["1000001", "1000002", "1000003"])
    (tmp / "cod").mkdir()
    (tmp / "cod" / "1000001.cif").write_text(FETCH_CIF)
    calls, steps = [], []
    _fake_cod(monkeypatch, calls, failing={"1000003"})
    monkeypatch.setattr(known_phases, "mp_configured", lambda: True)
    monkeypatch.setattr(qr, "mp_pool_rows", lambda *a: [
        {"material_id": "mp-1", "formula": "Li2O", "spacegroup": 225, "e_above_hull": 0.0, "theoretical": False}])

    def mp_download(self, ids, index_name, timeout_s=120):
        assert ids == ["mp-1"] and index_name == "queries"  # the server's own MP index file
        (tmp / "mp").mkdir(exist_ok=True)
        (tmp / "mp" / "mp-1.cif").write_text(FETCH_CIF)

    monkeypatch.setattr(structure_db.MaterialsProjectDatabase, "download_to_cache", mp_download)
    pool = qr.build_pool(["Li", "O"], [], L1, (10.0, 86.0), fetch=True,
                         progress=lambda *step: steps.append(step))
    assert sorted(calls) == ["1000002", "1000003"]  # the cached CIF is not fetched again
    summary = pool.summary()
    assert summary["downloaded"] == 2 and summary["unavailable"] == summary["missing_cif"] == 1
    assert summary["candidates"] == 3 and summary["sources"] == {"cod": 2, "mp": 1}
    assert any("1 of 4 reference structures could not be downloaded" in c for c in pool.caveats)
    assert {s[0] for s in steps} == {"listing", "downloading", "simulating"}
    assert all(isinstance(s[1], int) and isinstance(s[2], int) for s in steps)
    assert ("downloading", 3, 3) in steps
    # without fetch (library callers, the benchmark) nothing is downloaded
    calls.clear()
    pool = qr.build_pool(["Li", "O"], [], L1, (10.0, 86.0), use_mp=False)
    assert calls == [] and pool.summary()["downloaded"] == 0 and pool.missing_cif == 1


def test_unreachable_cod_ranks_the_cache_and_says_so(fetch_caches, monkeypatch):
    tmp, use_index = fetch_caches
    codes = [f"10000{i:02d}" for i in range(10)]
    use_index(codes)
    (tmp / "cod").mkdir()
    (tmp / "cod" / f"{codes[0]}.cif").write_text(FETCH_CIF)
    calls = []
    _fake_cod(monkeypatch, calls, failing=set(codes))
    pool = qr.build_pool(["Li", "O"], [], L1, (10.0, 86.0), use_mp=False, fetch=True)
    assert len(calls) == qr.FETCH_CHUNK  # gave up after one failed step, not 9 retries
    assert len(pool.candidates) == 1 and pool.unavailable == 9 and pool.downloaded == 0
    assert any("not reachable" in c for c in pool.caveats)


def test_stop_between_download_steps_keeps_finished_files(fetch_caches, monkeypatch):
    from dara.structure_library import Cancelled

    tmp, use_index = fetch_caches
    use_index([f"10000{i:02d}" for i in range(12)])
    calls = []
    _fake_cod(monkeypatch, calls)
    stop = lambda: len(calls) >= 1  # noqa: E731
    pool = qr.build_pool(["Li", "O"], [], L1, (10.0, 86.0), use_mp=False, fetch=True, should_stop=stop)
    assert len(calls) == qr.FETCH_CHUNK and pool.candidates == []
    assert len(list((tmp / "cod").glob("*.cif"))) == qr.FETCH_CHUNK and not list((tmp / "cod").glob("*.tmp"))
    x, y = _pattern([(A_D, A_I, 2000, 1.0)])
    with pytest.raises(Cancelled):
        qr.quick_ranking(x, y, elements=["Li", "O"], use_mp=False, should_stop=stop)


def test_router_progress_and_cancel(monkeypatch):
    import io
    import time

    from fastapi import HTTPException, UploadFile

    from dara.server import quick_rank_router as router_module
    from dara.structure_library import Cancelled

    runs = []

    def downloading(params):
        runs.append(1)
        while not params["should_stop"]():
            params["progress"]("downloading", 3, 9)
            time.sleep(0.01)
        raise Cancelled()

    monkeypatch.setattr(router_module, "_run", downloading)
    monkeypatch.setattr(router_module, "WAIT_S", 0.2)
    router_module._JOBS.clear()
    content = (DATA / "BiFeO3.xy").read_bytes()

    def upload():
        return router_module.quick_ranking_upload(
            UploadFile(io.BytesIO(content), filename="BiFeO3.xy"), elements='["Fe","Bi","O"]',
            precursor_formulas="[]", wavelength="Cu", two_theta_min=None, two_theta_max=None,
            excluded_ranges="[]", include_theoretical=False, use_cod=True, use_mp=False,
            mp_e_hull_cutoff=0.05, max_mp_structures=100,
        )

    async def scenario():
        first = await upload()
        assert first["status"] == "pending"
        assert (first["phase"], first["done"], first["total"]) == ("downloading", 3, 9)
        job = router_module._JOBS[first["job_id"]]
        assert await router_module.quick_ranking_cancel(first["job_id"]) == {
            "status": "cancelled", "job_id": first["job_id"]}
        await asyncio.wait({job["task"]}, timeout=5)
        assert job["status"] == "cancelled"  # the worker stopped at its next check
        for call in (router_module.quick_ranking_status, router_module.quick_ranking_cancel):
            with pytest.raises(HTTPException) as info:
                await call(first["job_id"])
            assert info.value.status_code == 404
        again = await upload()  # the same request starts afresh
        assert again["status"] == "pending" and again["job_id"] == first["job_id"] and len(runs) == 2
        job = router_module._JOBS[again["job_id"]]
        await router_module.quick_ranking_cancel(again["job_id"])
        await asyncio.wait({job["task"]}, timeout=5)

    asyncio.run(scenario())


def test_suggested_structure_with_unphysical_density_is_left_out(monkeypatch):
    pool = qr.Pool()
    pool.candidates = [_cand("cod:1", "A", A_D, A_I), _cand("cod:2", "B", B_D, B_I, sg=2)]
    pool.candidates[0].path = Path("bad.cif")
    monkeypatch.setattr(qr, "build_pool", lambda *a, **k: pool)
    monkeypatch.setattr(qr, "_unphysical_density", lambda path: path.name == "bad.cif")
    x, y = _pattern([(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])  # peaks above the noise floor
    result = qr.quick_ranking(x, y, elements=["A"])
    assert [row["source"] for row in result["ranking"]] == ["cod:2"]
    assert [step["source"] for step in result["combination"]] == ["cod:2"]
    assert any("unphysical density (cod:1)" in text for text in result["caveats"])


def test_combination_stops_when_no_significant_peak_is_left_unexplained():
    # a dense decoy that only fits the background noise would still pass the gain stop (>= STOP_GAIN)
    tt = np.arange(12.5, 79, 1.37)
    decoy = _cand("cod:5", "Ee", list(L1 / (2 * np.sin(np.radians(tt / 2)))), [1.0] * len(tt))
    cands = [_cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I), _cand("cod:3", "Cc", C_D, C_I), decoy]
    x, y = _pattern([(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    out = qr.rank(x, y, cands)
    assert [c["formula"] for c in out["combination"]] == ["Aa", "Bb"]
    left = qr.residual_step(x, y, cands, ["cod:1", "cod:2"])  # the same rule, shared
    assert left["unexplained_peaks"] == [] and left["next"][0]["formula"] == "Ee"
    assert left["next"][0]["explained"] >= qr.STOP_GAIN  # the gain alone would have picked it
    assert "picked" not in next(r for r in out["ranking"] if r["formula"] == "Ee")["flags"]
    # a real minor phase leaves its peaks unexplained, so the search goes on to it
    x, y = _pattern([(A_D, A_I, 20000, 1.0), (B_D, B_I, 12000, 1.0), (C_D, C_I, 3000, 1.0)])
    assert [c["formula"] for c in qr.rank(x, y, cands)["combination"]] == ["Aa", "Bb", "Cc"]


def test_ranking_reports_a_wavelength_mismatch_without_blocking(monkeypatch):
    import io

    from fastapi import UploadFile

    from dara.server import api_router
    from dara.server import quick_rank_router as router_module

    seen = []
    monkeypatch.setattr(router_module, "_run", lambda params: {
        "ranking": [], "combination": [], "caveats": ["COD only."]})
    monkeypatch.setattr(api_router, "_wavelength_mismatch", lambda pattern, path, wavelength: seen.append(
        (path.name, wavelength)) or {"code": "wavelength_mismatch", "message": "Recorded 1.5406 A, chosen 1.7890 A.",
                                     "recorded_angstrom": 1.5406, "selected_angstrom": 1.789})
    router_module._JOBS.clear()

    async def scenario():
        view = await router_module.quick_ranking_upload(
            UploadFile(io.BytesIO((DATA / "BiFeO3.xy").read_bytes()), filename="BiFeO3.xy"),
            elements='["Bi","Fe","O"]', precursor_formulas="[]", wavelength="Co", two_theta_min=None,
            two_theta_max=None, excluded_ranges="[]", include_theoretical=False, use_cod=True, use_mp=False,
            mp_e_hull_cutoff=0.05, max_mp_structures=100)
        for _ in range(50):
            if view["status"] == "done":
                break
            view = await router_module.quick_ranking_status(view["job_id"])
        return view

    view = asyncio.run(scenario())
    assert seen == [("BiFeO3.xy", "Co")]
    assert view["status"] == "done" and view["wavelength_mismatch"]["selected_angstrom"] == 1.789
    assert view["caveats"] == ["Recorded 1.5406 A, chosen 1.7890 A.", "COD only."]


def test_refinement_takes_every_anode_with_a_bgmn_profile(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from dara.server import api_router
    from dara.xrd import load_pattern

    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path))
    (tmp_path / "1234567.cif").write_text((DATA / "BiFeO3.cif").read_text())
    pattern = load_pattern(DATA / "BiFeO3.xy")
    lam = Path(api_router.__file__).parents[1] / "bgmn" / "BGMNwin"
    for anode in api_router.REFINEMENT_ANODES:
        assert (lam / f"{anode.upper()}.LAM").exists() or not lam.exists()
        job = api_router.build_submission_job(pattern, "BiFeO3.xy", use_mp=False, wavelength=anode,
                                              reference_ids='["cod:1234567"]')
        assert job.function_kwargs["search_kwargs"]["wavelength"] == anode
    with pytest.raises(HTTPException) as info:
        api_router.build_submission_job(pattern, "BiFeO3.xy", use_mp=False, wavelength="Ag",
                                        reference_ids='["cod:1234567"]')
    assert info.value.status_code == 400 and "Ag" in info.value.detail
    assert qr.wavelength_lines("Ag")[0] == pytest.approx(0.5594075)  # the search-match takes Ag


def test_reference_library_cifs_are_ranked_and_handed_on(tmp_path, monkeypatch):
    import io

    from fastapi import HTTPException, UploadFile

    from dara.server import quick_rank_router as router_module

    monkeypatch.setenv("DARA_SERVER_MONTYDB_PATH", str(tmp_path / "montydb"))
    library = tmp_path / "reference-cifs"
    library.mkdir()
    item = "ab" * 16
    (library / f"{item}.cif").write_text((DATA / "BiFeO3.cif").read_text())
    (library / "registry.json").write_text(json.dumps({"items": [
        {"id": item, "filename": "mine.cif", "formula": "BiFeO3", "spacegroup_number": 161}]}))
    monkeypatch.setattr(router_module, "WAIT_S", 0.1)
    router_module._JOBS.clear()

    def upload(ids):
        return router_module.quick_ranking_upload(
            UploadFile(io.BytesIO((DATA / "BiFeO3.xy").read_bytes()), filename="BiFeO3.xy"),
            elements='["Bi","Fe","O"]', precursor_formulas="[]", wavelength="Cu", two_theta_min=None,
            two_theta_max=None, excluded_ranges="[]", include_theoretical=False, use_cod=False, use_mp=False,
            mp_e_hull_cutoff=0.05, max_mp_structures=100, library_cif_ids=json.dumps(ids))

    async def scenario():
        view = await upload([item])
        for _ in range(600):
            if view["status"] != "pending":
                break
            view = await router_module.quick_ranking_status(view["job_id"])
        with pytest.raises(HTTPException) as info:
            await upload(["cd" * 16])
        assert info.value.status_code == 400 and "cd" * 16 in info.value.detail
        return view

    view = asyncio.run(scenario())
    assert view["status"] == "done", view
    top = view["ranking"][0]
    assert top["source"] == f"lib:{item}" and top["source_kind"] == "lib" and top["spacegroup"] == 161
    cif = qr.reference_cifs([f"lib:{item}"])[0]
    assert cif.filename.endswith(f"(lib_{item})")


def test_sonneveld_visser_background_follows_a_curved_base_under_peaks():
    from dara.search.prefilter import background_settings, observed_profile, sonneveld_visser

    x = np.arange(10.0, 80.0, 0.02)
    base = 200.0 + 3000.0 * np.exp(-(x - 10.0) / 3.0) + 0.5 * (x - 10.0)  # low-angle rise
    peaks = sum(5000.0 * np.exp(-0.5 * ((x - c) / 0.06) ** 2) for c in (14.0, 31.7, 36.2, 47.5, 56.6, 62.9))
    y = base + peaks
    curve = sonneveld_visser(y, 20, 1.0, 5.0)
    under = np.zeros_like(x, bool)
    for c in (31.7, 36.2, 47.5, 56.6, 62.9):
        under |= np.abs(x - c) < 0.05
    assert np.max(np.abs(curve[under] - base[under])) < 0.05 * 5000  # does not ride up the peaks
    assert abs(curve[np.searchsorted(x, 11.0)] - base[np.searchsorted(x, 11.0)]) < 150  # follows the rise
    assert np.all(sonneveld_visser(base, 20, 0.0, 1.0) <= base.max())
    settings = background_settings({"method": "auto"})
    assert settings == {"method": "sonneveld_visser", "granularity": 20, "bending_factor": 0.0, "anchors": None}
    for bad in ({"method": "magic"}, {"granularity": 1}, {"bending_factor": -1}, {"colour": 1},
                {"anchors": [[20, 1]]}):
        with pytest.raises(ValueError):
            background_settings(bad)
    flat = background_settings({"anchors": [[10.0, 100.0], [80.0, 100.0]]})
    profile = observed_profile(x, y, background=flat)
    assert np.allclose(profile.background, 100.0)  # the analyst's base points replace the automatic curve
    sv = observed_profile(x, y, background=background_settings({"method": "sonneveld_visser"}))
    assert sv.usable and sv.background.shape == x.shape


def test_flat_pattern_is_rejected_before_the_pool_is_listed(monkeypatch):
    def no_pool(*args, **kwargs):
        raise AssertionError("the pool must not be built for a flat pattern")

    monkeypatch.setattr(qr, "build_pool", no_pool)
    x = np.arange(10, 80, 0.01)
    out = qr.quick_ranking(x, np.full_like(x, 100.0), elements=["Fe", "O"], use_mp=False)
    assert out["status"] == "done" and out["ranking"] == [] and "too flat" in out["caveats"][0]
    assert out["_candidates"] == [] and out["pool"]["candidates"] == 0


def test_background_preview_of_a_job_without_ranking(monkeypatch):
    import io

    from fastapi import HTTPException, UploadFile

    from dara.server import quick_rank_router as router_module

    monkeypatch.setattr(router_module, "_run", lambda params: {"ranking": [], "combination": [], "caveats": []})
    router_module._JOBS.clear()

    async def scenario():
        view = await router_module.quick_ranking_upload(
            UploadFile(io.BytesIO((DATA / "BiFeO3.xy").read_bytes()), filename="BiFeO3.xy"),
            elements='["Bi","Fe","O"]', precursor_formulas="[]", wavelength="Cu", two_theta_min=None,
            two_theta_max=None, excluded_ranges="[]", include_theoretical=False, use_cod=True, use_mp=False,
            mp_e_hull_cutoff=0.05, max_mp_structures=100)
        curve = await router_module.quick_ranking_background(
            view["job_id"], {"method": "sonneveld_visser", "granularity": 10, "bending_factor": 2})
        assert curve["settings"]["granularity"] == 10 and len(curve["two_theta"]) == len(curve["intensity"]) > 100
        assert set(curve) == {"two_theta", "observed", "intensity", "settings"}
        with pytest.raises(HTTPException) as info:
            await router_module.quick_ranking_background(view["job_id"], {"method": "spline"})
        assert info.value.status_code == 400
        with pytest.raises(HTTPException) as info:
            await router_module.quick_ranking_background("nope", None)
        assert info.value.status_code == 404

    asyncio.run(scenario())


def test_alternatives_are_related_rows_with_the_same_strong_peaks():
    """Peak matching cannot tell apart a variant (or polymorph) whose strong lines coincide."""
    tio2 = {"source": "cod:1", "formula": "TiO2", "flags": [], "sticks": [[25.3, 100], [37.8, 20], [48.0, 30], [70.0, 5]]}
    same = {"source": "cod:2", "formula": "Ti0.99O2", "flags": ["variant of TiO2"],
            "sticks": [[25.33, 90], [37.85, 22], [48.05, 28]]}
    rutile = {"source": "cod:3", "formula": "TiO2", "flags": ["polymorph"], "sticks": [[27.4, 100], [36.1, 50], [54.3, 60]]}
    other = {"source": "cod:4", "formula": "ZnO", "flags": [], "sticks": [[25.3, 100], [37.8, 20], [48.0, 30]]}
    assert qr.same_peaks(tio2["sticks"], same["sticks"])
    assert not qr.same_peaks(tio2["sticks"], rutile["sticks"])
    assert not qr.same_peaks(tio2["sticks"], [[25.3, 100], [37.8, 20], [48.0, 30], [62.0, 40]]), "an extra strong line"
    rows = [tio2, same, rutile, other]
    qr.mark_alternatives(rows)
    assert tio2["alternatives"] == ["cod:2"] and same["alternatives"] == ["cod:1"]
    assert rutile["alternatives"] == [] and other["alternatives"] == []  # same peaks but unrelated: not flagged


def test_mp_pool_queries_named_formulas_on_their_own(monkeypatch):
    """A named precursor (an oxalate hydrate) the chemical-system query did not return (its structure
    cap cuts) is queried by formula, as the search does; a named organic is kept; a named formula
    already returned is not queried again."""
    from types import SimpleNamespace

    from pymatgen.core import Composition

    from dara import structure_db

    def doc(material_id, formula):
        return {"material_id": material_id, "formula_pretty": formula, "symmetry": {"number": 15},
                "energy_above_hull": 0.0, "theoretical": False,
                "structure": SimpleNamespace(composition=Composition(formula))}

    calls = []

    def query(self, *, formulas=None, chemsys=None, max_structures=None, **kwargs):
        calls.append((formulas, max_structures))
        if formulas:
            return [doc("mp-2", "FeC2O4(H2O)2"), doc("mp-4", "C2H2O4"), doc("mp-1", "Fe2O3")]
        return [doc("mp-1", "Fe2O3"), doc("mp-3", "C6H6")]

    monkeypatch.setattr(structure_db.MaterialsProjectDatabase, "_query", query)
    qr._MP_ROWS_MEMO.clear()
    rows = qr.mp_pool_rows(["C", "Fe", "H", "O"], 0.05, 100, ["FeC2O4(H2O)2", "H2C2O4", "Fe2O3"])
    assert [r["material_id"] for r in rows] == ["mp-1", "mp-2", "mp-4"]  # no duplicate, C6H6 left out
    assert calls[1] == (["FeH4(CO3)2", "HCO2"], 100)  # reduced formulas
    qr._MP_ROWS_MEMO.clear()


def test_a_pick_needs_correlated_lines_above_the_noise_floor(monkeypatch):
    """Lines predicted above the noise floor and present in the pattern are counted (Match!'s
    "correlated peaks"); a row with fewer than MIN_CORRELATED is passed over by the combination."""
    cands = [_cand("cod:3", "Cc", C_D, C_I), _cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I)]
    x, y = _pattern([(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    ctx, _ = qr._context(x, y, wavelength="Cu", wmin=None, wmax=None, excluded_ranges=None)
    assert qr._score(ctx, cands[1])["correlated"] == len(A_D)  # every line of A is there
    assert qr._score(ctx, cands[0])["correlated"] < qr.MIN_CORRELATED  # nothing of C is measured
    assert [c["formula"] for c in qr.rank(x, y, cands)["combination"]][:2] == ["Aa", "Bb"]
    monkeypatch.setattr(qr, "MIN_CORRELATED", len(B_D) + 1)  # B (7 lines) can no longer pass, A (9) can
    assert [c["formula"] for c in qr.rank(x, y, cands)["combination"]] == ["Aa"]


def test_doped_entry_gives_way_to_the_entered_formula_of_the_same_structure():
    assert qr._doped_form("Zn1.7Co0.3O2", 186, "ZnO", 186)
    assert qr._doped_form("Ti4H0.36O8", 141, "TiO2", 141)  # interstitial H in anatase
    assert not qr._doped_form("ZnFe2O4", 227, "Fe3O4", 227), "a stoichiometric compound is no dopant"
    assert not qr._doped_form("Zn1.7Co0.3O2", 186, "ZnO", 225), "another structure"
    # the doped entry fits a little better (intensities closer), the entered formula almost as well
    doped = _cand("cod:5", "Zn1.7Co0.3O2", A_D, A_I, sg=186)
    base = _cand("cod:6", "ZnO", A_D, [v * (0.9 if i % 2 else 1.0) for i, v in enumerate(A_I)], sg=186)
    cands = [doped, base, _cand("cod:2", "Bb", B_D, B_I)]
    x, y = _pattern([(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    plain = qr.rank(x, y, cands)
    assert plain["combination"][0]["formula"] == "Zn1.7Co0.3O2"
    named = qr.rank(x, y, cands, named=["ZnO", "Bb"])
    assert [c["formula"] for c in named["combination"]] == ["ZnO", "Bb"]  # the doped entry is not picked later
    row = next(r for r in named["ranking"] if r["formula"] == "ZnO")
    assert row["alternatives"] == ["cod:5"]
