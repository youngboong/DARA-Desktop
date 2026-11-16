"""Structure library packs: selection, search-equivalent filters, order, resume, cancel, progress."""

import io
import json
from pathlib import Path

import numpy as np
import pytest

from dara import structure_db
from dara import structure_library as lib
from dara.search import quick_rank

CIF = """data_test
_cell_length_a 4.0
_cell_length_b 4.0
_cell_length_c 4.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Li1 Li 0 0 0 1
O1 O 0.5 0.5 0.5 1
"""

INDEX = {
    "Li": [("Li", "1000001", 229, 0.0)],
    "O": [("O2", "1000002", 12, 0.0)],  # common gas
    "Li-O": [("Li2O", "1000003", 225, 0.0), ("Li2O2", "1000004", 194, 0.02), ("LiO3", "1000005", 1, 0.3)],
    "C-O": [("C3O2", "1000006", 14, None)],  # molecular organic
    "C-Li-O": [("Li2CO3", "1000007", 15, None)],
    "Fe-O": [("Fe2O3", "1000008", 167, 0.0)],  # Fe is outside the set
    "Li-O-P": [("Li3PO4", "1000009", 31, 0.0), ("LiPO3", "1000010", 7, 0.0)],
}


class FakeDB:
    preparsed_info = INDEX


DATA = {
    "format_version": 1,
    "e_hull_cutoff": 0.05,
    "measured": {"cod_mean_cif_kb": 20.0, "cod_disk_kb": 24.0, "cod_per_s": 4.0, "cod_theoretical_fraction": 0.0,
                 "mp_mean_cif_kb": 3.0, "mp_disk_kb": 4.0, "mp_per_s": 20.0, "mp_index_s": 60.0,
                 "stick_s_per_cif": 0.5, "stick_workers": 1, "stick_disk_kb": 4.0},
    "packs": [{"id": "recommended", "name": "Test", "description": "", "elements": ["C", "Li", "O", "P"],
               "counts": {"cod": 5, "mp": 10}}],
}


@pytest.fixture
def caches(tmp_path, monkeypatch):
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path / "cod"))
    monkeypatch.setenv("DARA_MP_CACHE_DIR", str(tmp_path / "mp"))
    monkeypatch.setenv("DARA_STICK_CACHE_DIR", str(tmp_path / "sticks"))
    monkeypatch.setattr(structure_db, "shared_cod_database", lambda: FakeDB())
    monkeypatch.setattr(structure_db.CODDatabase, "REQUEST_DELAY_S", 0)
    return tmp_path


def test_shipped_pack_file_has_one_recommended_pack_with_measurements():
    data = lib.load_packs()
    assert [p["id"] for p in data["packs"]] == ["recommended"]
    pack = data["packs"][0]
    assert {"Li", "Si", "Ga", "O", "Te", "W"} <= set(pack["elements"]) and len(set(pack["elements"])) == len(pack["elements"])
    for key in ("cod_mean_cif_kb", "cod_per_s", "mp_per_s", "stick_s_per_cif", "stick_workers"):
        assert data["measured"][key] > 0
    assert pack["counts"]["cod"] > 0 and pack["counts"]["mp"] > 0


def test_selection_is_the_element_subset_with_the_search_filters_in_useful_order():
    rows = lib.cod_selection(["Li", "O", "P", "C"], 0.05, database=FakeDB())
    ids = [r["id"] for r in rows]
    # Fe2O3 (outside), O2 (gas), LiO3 (e-hull), C3O2 (molecular organic) are left out
    assert set(ids) == {"1000001", "1000003", "1000004", "1000007", "1000009", "1000010"}
    assert ids[0] == "1000001"  # one element first
    assert ids.index("1000003") < ids.index("1000009")  # two elements before three
    assert ids.index("1000009") < ids.index("1000007")  # the more common 3-element system first
    # exactly what a search over the same elements could use
    search = structure_db.cod_candidate_rows(["Li", "O", "P", "C"], [], 0.05, database=FakeDB())
    assert {str(r[1]) for r in search} == set(ids)


def test_element_set_unions_packs_and_custom_elements_and_rejects_unknown():
    assert lib.element_set(["recommended"], ["fe"], DATA) == ["C", "Fe", "Li", "O", "P"]
    with pytest.raises(ValueError):
        lib.element_set(["nope"], [], DATA)
    with pytest.raises(ValueError):
        lib.element_set([], [], DATA)


def test_estimate_needs_no_network(caches, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(structure_db.requests, "get", no_network)
    monkeypatch.setattr(structure_db.MaterialsProjectDatabase, "rows_within", no_network)
    out = lib.estimate(["recommended"], [], DATA)
    assert out["counts"]["cod"] == 6 and out["counts"]["mp"] == 10 and not out["mp_estimated"]
    assert out["download_mb"]["total"] > 0 and out["minutes"]["total"] > 0
    custom = lib.estimate([], ["Li", "O"], DATA)
    assert custom["counts"]["cod"] == 3 and custom["mp_estimated"]


def _fake_download(calls, cancel_after=None, cancel_file=None):
    class Text:
        def __str__(self):
            return CIF

    def fetch(cod_id):
        calls.append(cod_id)
        if cancel_after is not None and len(calls) == cancel_after:
            cancel_file.write_text("stop")
        return Text()

    return fetch


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_download_resumes_skips_cached_and_reports_json_progress(caches, monkeypatch):
    folder = caches / "cod"
    folder.mkdir()
    (folder / "1000003.cif").write_text(CIF)
    calls = []
    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod", staticmethod(_fake_download(calls)))
    stream = io.StringIO()
    code = lib.download(["recommended"], [], ["cod"], sticks=False, progress=lib.Progress(True, stream), data=DATA)
    assert code == lib.EXIT_DONE
    assert "1000003" not in calls and len(calls) == 5
    lines = _lines(stream)
    assert all(set(line) == {"phase", "done", "total", "bytes", "eta_s", "message"} for line in lines)
    cod_lines = [line for line in lines if line["phase"] == "cod"]
    assert cod_lines[-1]["done"] == cod_lines[-1]["total"] == 6 and lines[-1]["phase"] == "done"
    calls.clear()
    assert lib.download(["recommended"], [], ["cod"], sticks=False, data=DATA) == lib.EXIT_DONE
    assert calls == []  # everything cached: nothing is fetched again


def test_cancel_stops_between_steps_and_leaves_only_complete_files(caches, monkeypatch):
    cancel = caches / "cancel.flag"
    calls = []
    monkeypatch.setattr(lib, "COD_CHUNK", 2)
    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod",
                        staticmethod(_fake_download(calls, cancel_after=2, cancel_file=cancel)))
    stream = io.StringIO()
    code = lib.download(["recommended"], [], ["cod"], sticks=False, cancel_file=str(cancel),
                        progress=lib.Progress(True, stream), data=DATA)
    assert code == lib.EXIT_PARTIAL and _lines(stream)[-1]["phase"] == "cancelled"
    files = list((caches / "cod").iterdir())
    assert len(calls) == 2 and len([f for f in files if f.suffix == ".cif"]) == 2
    assert all(structure_db.is_valid_cif_file(f) for f in files if f.suffix == ".cif")
    assert not [f for f in files if f.suffix == ".tmp"]
    cancel.unlink()
    assert lib.download(["recommended"], [], ["cod"], sticks=False, cancel_file=str(cancel), data=DATA) == 0
    assert len(calls) == 6  # resumed with the 4 missing structures only


def test_not_found_structures_are_remembered_and_do_not_fail_the_download(caches, monkeypatch):
    import requests

    def fetch(cod_id):
        if cod_id == "1000010":
            error = requests.HTTPError("404")
            error.response = type("R", (), {"status_code": 404})()
            raise error
        return CIF

    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod", staticmethod(fetch))
    assert lib.download(["recommended"], [], ["cod"], sticks=False, data=DATA) == lib.EXIT_DONE
    status = lib.status(["recommended"], [], DATA)
    assert status["cod"] == {"total": 6, "cached": 5, "unavailable": 1}


def test_sticks_phase_caches_every_downloaded_non_theoretical_structure(caches, monkeypatch):
    folder = caches / "cod"
    folder.mkdir()
    for code in ("1000001", "1000003", "1000004"):
        (folder / f"{code}.cif").write_text(CIF)
    (folder / "1000004.cif").write_text(CIF + "_cod_struct_determination_method theoretical\n")
    sims = []

    def fake(path, lam, rng):
        sims.append(path.name)
        return np.array([20.0]), np.array([100.0]), np.array([4.4])

    def not_found(cod_id):
        import requests

        error = requests.HTTPError("404")
        error.response = type("R", (), {"status_code": 404})()
        raise error

    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod", staticmethod(not_found))
    monkeypatch.setattr(quick_rank, "simulate_reflections", fake)
    code = lib.download(["recommended"], [], ["cod"], sticks=True, workers=1, data=DATA)
    assert code == lib.EXIT_DONE  # the other COD ids do not exist (404) and are remembered
    assert sorted(sims) == ["1000001.cif", "1000003.cif"]
    lam1, _ = quick_rank.wavelength_lines("Cu")
    assert quick_rank.cached_sticks("cod:1000001", lam1, (10.0, 80.0)) is not None
    # a common 10-90 deg scan (ranked over 4-96 deg) and scans up to 120 deg reuse the library sticks
    assert quick_rank.cached_sticks("cod:1000001", lam1, (4.0, 96.0)) is not None
    assert quick_rank.cached_sticks("cod:1000001", lam1, (4.0, 126.0)) is not None
    assert lib.status(["recommended"], [], DATA)["sticks"]["cached"] == 2


def test_mp_listing_failure_skips_mp_and_still_downloads_cod(caches, monkeypatch):
    from dara import known_phases

    def bad_key(*args, **kwargs):
        raise RuntimeError("401 invalid key sk-secret")

    calls = []
    monkeypatch.setattr(known_phases, "mp_configured", lambda: True)
    monkeypatch.setattr(structure_db.MaterialsProjectDatabase, "rows_within", bad_key)
    monkeypatch.setattr(structure_db.CODDatabase, "_download_cod", staticmethod(_fake_download(calls)))
    stream = io.StringIO()
    code = lib.download(["recommended"], [], ["cod", "mp"], sticks=False, progress=lib.Progress(True, stream), data=DATA)
    assert code == lib.EXIT_PARTIAL and len(calls) == 6  # COD done, MP to retry
    lines = _lines(stream)
    assert {"phase": "mp", "message": "Materials Project skipped: RuntimeError"}.items() <= next(
        line for line in lines if line["phase"] == "mp").items()
    assert "sk-secret" not in stream.getvalue() and lines[-1]["phase"] == "done"


def _li_o_doc(material_id):
    return {"material_id": material_id, "formula_pretty": "LiO", "symmetry": {"number": 1},
            "energy_above_hull": 0.0, "theoretical": False,
            "structure": structure_db.Cif.from_str(CIF).to_structure()}


def test_mp_cache_survives_a_locked_file_and_resume_indexes_unindexed_cifs(caches, monkeypatch):
    real = structure_db.atomic_write_text

    def locked_once(path, text):
        if Path(path).name == "mp-1.cif":
            raise PermissionError("locked")
        real(path, text)

    monkeypatch.setattr(structure_db, "atomic_write_text", locked_once)
    db = structure_db.MaterialsProjectDatabase()
    assert db.save_documents([_li_o_doc("mp-1"), _li_o_doc("mp-2")], "queries") == 1
    assert set(structure_db.mp_cache_rows()) == {"mp-2"}  # mp-1 failed, mp-2 still cached
    # a CIF without an index row (killed before the chunk's index write) is indexed on resume
    (caches / "mp" / "mp-3.cif").write_text(CIF)
    row = {"material_id": "mp-3", "formula": "LiO", "spacegroup": 1, "e_above_hull": 0.0,
           "theoretical": False, "chemsys": "Li-O", "n_elements": 2, "popularity": 1}
    assert lib._download_mp([row], lib.Progress(False), None) == 0
    assert structure_db.mp_cache_rows()["mp-3"]["chemsys"] == "Li-O"


def _fake_rester(error):
    class Rester:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @property
        def materials(self):
            raise error()

    return Rester


def test_mp_query_falls_back_to_the_cache_only_when_offline(caches, monkeypatch):
    import mp_api.client
    import requests
    from mp_api.client.core.exceptions import MPRestError

    monkeypatch.setenv("MP_API_KEY", "test-key")
    structure_db.MaterialsProjectDatabase().save_documents([_li_o_doc("mp-1")], "queries")

    def offline():  # how mp_api wraps a failed request during a search
        try:
            raise requests.exceptions.ConnectionError("no route to host")
        except requests.exceptions.RequestException as ex:
            raise MPRestError(str(ex))

    monkeypatch.setattr(mp_api.client, "MPRester", _fake_rester(offline))
    quick_rank._MP_ROWS_MEMO.clear()
    rows = quick_rank.mp_pool_rows(["Li", "O"], 0.05, 100)
    assert [r["material_id"] for r in rows] == ["mp-1"]
    assert not quick_rank._MP_ROWS_MEMO  # the offline stand-in is not memoised for an hour
    db = structure_db.MaterialsProjectDatabase()
    assert len(db._query(chemsys=["Li-O"])) == 1 and db.offline_cache_used == 1

    def expired_key():
        raise MPRestError("REST query returned with error status code 401")

    monkeypatch.setattr(mp_api.client, "MPRester", _fake_rester(expired_key))
    with pytest.raises(MPRestError):
        structure_db.MaterialsProjectDatabase()._query(chemsys=["Li-O"])


def test_server_runs_one_download_at_a_time_and_keeps_its_progress(tmp_path, monkeypatch):
    import asyncio
    import threading

    from fastapi import HTTPException

    from dara.server import structure_library_router as router

    monkeypatch.setenv("DARA_STICK_CACHE_DIR", str(tmp_path / "sticks"))
    release = threading.Event()
    started = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            started.append(command)
            self.code = None
            lines = [json.dumps({"phase": "cod", "done": 3, "total": 6, "bytes": 10, "eta_s": 1.0, "message": ""})]
            self.stdout = iter(lines)

        def poll(self):
            return self.code

        def wait(self, timeout=None):
            release.wait(5)
            self.code = 2
            return self.code

        def kill(self):
            release.set()

    monkeypatch.setattr(router.subprocess, "Popen", FakeProcess)
    router._JOB.clear()
    router._JOB["process"] = None

    async def scenario():
        first = await router.start_download(router.DownloadRequest(packs=["recommended"], sources=["cod"]))
        assert first["job"]["running"]
        command = started[0]
        assert command[1:4] == ["-m", "dara.structure_library", "download"] and "--progress-json" in command
        assert command[command.index("--packs") + 1] == "recommended"
        with pytest.raises(HTTPException) as info:
            await router.start_download(router.DownloadRequest(elements=["Li"]))
        assert info.value.status_code == 409
        for _ in range(50):
            job = (await router.download_progress())["job"]
            if job["last"]:
                break
            await asyncio.sleep(0.02)
        assert job["last"]["phase"] == "cod" and job["phases"]["cod"]["done"] == 3
        cancelled = (await router.cancel_download())["job"]
        assert cancelled["cancelling"] and Path(router._JOB["cancel_file"]).exists()
        release.set()
        for _ in range(100):
            job = (await router.download_progress())["job"]
            if not job["running"] and job["exit_code"] is not None:
                break
            await asyncio.sleep(0.02)
        assert job["exit_code"] == 2 and not job["running"]
        with pytest.raises(HTTPException) as info:
            await router.start_download(router.DownloadRequest(packs=["nope"]))
        assert info.value.status_code == 400

    asyncio.run(scenario())


def test_download_killed_after_cancel_is_reported_as_cancelled(tmp_path, monkeypatch):
    import asyncio
    import subprocess
    import threading

    from dara.server import structure_library_router as router

    monkeypatch.setenv("DARA_STICK_CACHE_DIR", str(tmp_path / "sticks"))
    killed = threading.Event()

    class StuckProcess:  # never reads the cancel file (a blocking Materials Project query)
        def __init__(self, command, **kwargs):
            self.stdout = iter([json.dumps({"phase": "select", "done": 0, "total": 1, "bytes": 0,
                                            "eta_s": None, "message": "Listing Materials Project entries"})])
            self.code = None

        def poll(self):
            return self.code

        def wait(self, timeout=None):
            if timeout is not None and not killed.is_set():
                raise subprocess.TimeoutExpired("download", timeout)
            killed.wait(5)
            self.code = 1  # TerminateProcess exit code on Windows
            return self.code

        def kill(self):
            killed.set()

    monkeypatch.setattr(router.subprocess, "Popen", StuckProcess)
    router._JOB.clear()
    router._JOB["process"] = None

    async def scenario():
        await router.start_download(router.DownloadRequest(elements=["Li", "O"], sources=["mp"]))
        await router.cancel_download()
        for _ in range(100):
            job = (await router.download_progress())["job"]
            if not job["running"] and job["exit_code"] is not None:
                break
            await asyncio.sleep(0.02)
        assert job["exit_code"] == 2 and job["last"]["phase"] == "cancelled"

    asyncio.run(scenario())
