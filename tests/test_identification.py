"""Identification results (search-match without refinement) and the search-match residual step."""

import asyncio
import io
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dara.export.peak_profile import doublet, doublet_area
from dara.export.pattern_treatment import kalpha_pair
from dara.rir import calculated_rir, diffracting_power
from dara.search import quick_rank as qr
from test_quick_rank import A_D, A_I, B_D, B_I, C_D, C_I, _cand, _pattern
from test_rir import RUTILE, ZNO, _structure

IDS = ["cod:9004178", "cod:9007432"]  # ZnO, rutile
WEIGHTS = {"ZnO": 0.3, "TiO2": 0.7}


def _mixture_xy() -> tuple[bytes, list[float]]:
    """ZnO/rutile pattern (Kα1+Kα2 pseudo-Voigt lines, areas ∝ w·I/(V²ρ), Poisson noise) as .xy
    text, and the positions a peak search finds."""
    pair = kalpha_pair("Cu")
    x = np.arange(20.0, 90.0, 0.01)
    clean = np.full(x.shape, 100.0)
    unit, positions = doublet_area(1.0, 0.1, 0.5, pair), []
    for phase, path in (("ZnO", ZNO), ("TiO2", RUTILE)):
        structure = _structure(path)
        lines = calculated_rir(structure, "Cu")["lines"]
        for two_theta, intensity in zip(lines["two_theta"], lines["intensity"]):
            if two_theta < 89:
                height = 4.0e7 * WEIGHTS[phase] * diffracting_power(structure, intensity) / unit
                clean += doublet(x, two_theta, height, 0.1, 0.5, pair)
                if height > 30:
                    positions.append(float(two_theta))
    y = np.random.default_rng(1).poisson(clean).astype(float)
    text = "\n".join(f"{a:.4f} {b:.1f}" for a, b in zip(x, y)) + "\n"
    return text.encode(), sorted(positions)


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Private task stores and COD cache with the two reference CIFs; the peak search (EFLECH)
    returns the true line positions."""
    from dara.server import quick_refine

    monkeypatch.setenv("DARA_SERVER_MONTYDB_PATH", str(tmp_path / "montydb"))
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path / "cod"))
    (tmp_path / "cod").mkdir()
    shutil.copy(ZNO, tmp_path / "cod" / "9004178.cif")
    shutil.copy(RUTILE, tmp_path / "cod" / "9007432.cif")
    content, positions = _mixture_xy()
    monkeypatch.setattr(quick_refine, "_compute_peaks", lambda *a, **k: pd.DataFrame(
        {"2theta": positions, "intensity": [1.0] * len(positions)}))
    return content


def _create(content, **fields):
    from fastapi import UploadFile

    from dara.server import identification_router

    form = {"user": "amy", "reference_ids": json.dumps(IDS), "elements": '["Zn","Ti","O"]',
            "precursor_formulas": "[]", "wavelength": "Cu", "two_theta_min": None, "two_theta_max": None,
            "excluded_ranges": "[]", "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "sample": "batch 7",
            "confirm_wavelength": False, **fields}
    return identification_router.create_identification(UploadFile(io.BytesIO(content), filename="mix.xy"), **form)


def test_identification_task_and_its_result_tools(server):
    from fastapi import HTTPException

    from dara.server import api_router, cell_refinement_router, identification_router
    from dara.server.utils import get_worker_store

    async def scenario():
        created = await _create(server)
        assert created["status"] == "COMPLETED"
        task_id = created["wf_id"]
        payload = await api_router.result(task_id)
        assert payload["analysis_mode"] == "identification" and payload["final_result"] is None
        assert payload["best_rwp"] is None and payload["sample"] == "batch 7"
        zno, rutile = payload["phases"]
        assert zno["name"].endswith("(cod_9004178)") and rutile["name"].endswith("(cod_9007432)")
        for phase in payload["phases"]:
            assert phase["matched"] == phase["expected"] > 0 and phase["lattice_scale"] == pytest.approx(1.0, abs=0.003)
            assert phase["weight_percent"] is None and phase["observed_peaks"] > 0
        assert zno["rir_weight_percent"] == pytest.approx(30, abs=4)
        assert rutile["rir_weight_percent"] == pytest.approx(70, abs=4)
        assert payload["peak_counts"]["unexplained"] == 0 and payload["explained"] > 0.5
        assert payload["capabilities"]["report"] and payload["capabilities"]["figure"]
        assert payload["capabilities"]["table_csv"] is False

        plot = json.loads((await api_router.plot(task_id, None, None)).body)
        assert [trace["name"] for trace in plot["data"][:2]] == ["Observed", "Background"]
        assert [trace.get("meta") for trace in plot["data"][2:]] == ["reference-sticks"] * 2
        assert not any(trace["name"] in ("Calculated", "Difference") for trace in plot["data"])

        peaks = await api_router.task_peaks(task_id, None)
        row = next(r for r in peaks["rows"] if r["phase"] == zno["name"] and (r["h"], r["k"], r["l"]) == (1, 0, 1))
        assert row["d_angstrom"] == pytest.approx(2.476, abs=0.01) and row["intensity_rel_phase_percent"] == 100.0
        observed = await api_router.task_peaks_measured(task_id, None, 0.89, None, None)
        assert observed["unassigned"] == 0 and all(r["size_nm"] for r in observed["rows"] if r["fit_note"] is None)
        rir = await api_router.task_rir_quantification(task_id, None)
        assert rir["complete"] and all(r["weight_percent_rietveld"] is None for r in rir["phases"])
        wh = await api_router.task_williamson_hall(task_id, None, 0.89, None, None)
        assert {p["phase"] for p in wh["phases"]} == {zno["name"], rutile["name"]}
        cell = await cell_refinement_router.task_cell_refinement(task_id, None, None, 0, 0, None, "unit")
        assert cell["rietveld_shift"] is None
        a = next(p for p in cell["phases"] if p["phase"] == zno["name"])
        assert a["rietveld"] is None and a["note"] is None and a["parameters"]["a"]["value"] == pytest.approx(3.25, abs=0.01)
        fit_csv = (await api_router.task_pattern_csv(task_id, None)).body.decode("utf-8-sig").splitlines()[0]
        assert fit_csv.split(",")[:4] == ["two_theta_deg", "d_angstrom", "observed", "background"]
        assert "calculated" not in fit_csv
        bgsub = (await api_router.task_pattern_xy(task_id, "background_subtracted", None, False, None)).body
        assert len(bgsub.splitlines()) > 1000

        # refine hand-off: a linked Known-phase refinement queued as a new task
        handed = await identification_router.refine_identification(task_id)
        with get_worker_store() as store:
            new = store.query_one({"index": handed["wf_id"]})
            old = store.query_one({"index": task_id})
        assert new["status"] == "READY" and old["refined_as"] == [handed["wf_id"]]
        settings = new["job"]["metadata"]["submission_settings"]
        assert settings["analysis_mode"] == "known_mixture" and settings["reference_ids"] == IDS
        assert settings["identified_from"] == task_id
        assert (await api_router.result(handed["wf_id"]))["identified_from"] == task_id
        assert (await api_router.result(task_id))["refined_as"] == [handed["wf_id"]]
        with pytest.raises(HTTPException) as info:
            await identification_router.refine_identification(handed["wf_id"])
        assert info.value.status_code == 409

        # mixed task kinds in the Results list
        listed = {row["fw_id"]: row for row in (await api_router.get_all_tasks(1, 10, None, None, None))["tasks"]}
        summary = listed[task_id]["summary"]
        assert summary["analysis_mode"] == "identification" and summary["best_rwp"] is None
        assert summary["final_phases"] == [zno["name"], rutile["name"]] and summary["final_phase_labels"]
        assert listed[handed["wf_id"]]["state"] == "READY" and listed[handed["wf_id"]]["summary"] is None
        return task_id

    task_id = asyncio.run(scenario())

    from types import SimpleNamespace

    from dara.server.app import app

    assert identification_router.refuse_refinement_tools in [m.kwargs.get("dispatch") for m in app.user_middleware]

    async def passed(request):
        return "passed"

    def guard(path):
        request = SimpleNamespace(url=SimpleNamespace(path=f"/api/task/{task_id}/{path}"))
        return asyncio.run(identification_router.refuse_refinement_tools(request, passed))

    for path in ("versions", "versions/1", "fit-compare", "fit-curves", "structure-sites", "quick-refine",
                 "internal-standard", "refine-selected", "export/cif", "download", "table.csv"):
        response = guard(path)
        assert response.status_code == 409 and b"identification" in response.body, path
    for path in ("", "plot", "peaks", "observed-peaks.csv", "rir", "williamson-hall", "cell-refinement",
                 "pattern.xy", "annotations", "refine", "report", "report.zip", "figure.png", "figure.svg"):
        assert guard(path) == "passed", path
    assert asyncio.run(api_router.delete_result(task_id))["message"] == "deleted"
    with pytest.raises(HTTPException) as info:
        asyncio.run(api_router.result(task_id))
    assert info.value.status_code == 404


def test_identification_rejects_bad_input(server):
    from fastapi import HTTPException

    async def scenario():
        for fields, text in (({"reference_ids": "[]"}, "Accept 1 to 12"),
                             ({"reference_ids": '["cod:1234567"]'}, "not in the local cache"),
                             ({"instrument_profile": "nope"}, "Unknown instrument profile")):
            with pytest.raises(HTTPException) as info:
                await _create(server, **fields)
            assert info.value.status_code == 400 and text in str(info.value.detail)

    asyncio.run(scenario())


CANDIDATES = lambda: [_cand("cod:1", "Aa", A_D, A_I), _cand("cod:2", "Bb", B_D, B_I), _cand("cod:3", "Cc", C_D, C_I)]  # noqa: E731
B_LINES = [float(t) for t in 2 * np.degrees(np.arcsin(1.540598 / (2 * np.asarray(B_D))))]


def test_residual_step_after_accepting_one_and_both_phases():
    # strong lines: the net pattern also holds the background estimate's noise bias, never explained
    x, y = _pattern([(A_D, A_I, 20000, 1.0), (B_D, B_I, 12000, 1.0)])
    one = qr.residual_step(x, y, CANDIDATES(), ["cod:1"])
    assert one["next"][0]["source"] == "cod:2" and [s["source"] for s in one["steps"]] == ["cod:1"]
    assert all(row["formula"] != "Aa" for row in one["next"])
    found = [peak[0] for peak in one["unexplained_peaks"]]
    assert all(any(abs(f - t) < 0.15 for f in found) for t in B_LINES)  # B's lines are what is left
    assert len(one["profile"]["two_theta"]) == len(one["profile"]["net"])
    both = qr.residual_step(x, y, CANDIDATES(), ["cod:1", "cod:2"])
    assert both["explained"] > 0.85 and both["unexplained_peaks"] == []
    assert both["explained"] == pytest.approx(sum(s["gain"] for s in both["steps"]), abs=2e-3)
    with pytest.raises(KeyError):
        qr.residual_step(x, y, CANDIDATES(), ["cod:9"])


def test_residual_endpoint_reuses_the_finished_job(monkeypatch):
    import time

    from fastapi import HTTPException

    from dara.server import quick_rank_router as router_module

    x, y = _pattern([(A_D, A_I, 2000, 1.0), (B_D, B_I, 1200, 1.0)])
    params = {"angles": list(x), "intensities": list(y), "wavelength": "Cu", "wmin": None, "wmax": None,
              "excluded_ranges": None}
    router_module._JOBS["job1"] = {"status": "done", "result": {}, "cancel": __import__("threading").Event(),
                                   "params": params, "candidates": CANDIDATES(), "finished": time.monotonic()}
    request = router_module.ResidualRequest(selected=["cod:1"])
    started = time.perf_counter()
    body = asyncio.run(router_module.quick_ranking_residual("job1", request))
    assert time.perf_counter() - started < 2.0
    assert body["selected"] == ["cod:1"] and body["next"][0]["formula"] == "Bb" and "spacegroup_symbol" in body["next"][0]
    with pytest.raises(HTTPException) as info:
        asyncio.run(router_module.quick_ranking_residual("job1", router_module.ResidualRequest(selected=["cod:9"])))
    assert info.value.status_code == 400
    del router_module._JOBS["job1"]
    with pytest.raises(HTTPException) as info:
        asyncio.run(router_module.quick_ranking_residual("job1", request))
    assert info.value.status_code == 404


def test_identification_report_figure_and_bundle(server):
    import zipfile

    from dara.server import annotations_router, review_router

    async def scenario():
        task_id = (await _create(server))["wf_id"]
        await annotations_router.save_annotations(task_id, annotations_router.AnnotationsUpdate(
            annotations=[{"id": "a1", "two_theta": 36.25, "intensity": 900.0, "text": "ZnO 101 marker"}]))
        html = (await review_router.download_report(task_id, None, False, None, True)).body.decode()
        figure = (await review_router.download_figure(task_id, "svg", None, False, 600, 170, None, True)).body.decode()
        bundle = (await review_router.download_report_bundle(task_id, None, False, None, False)).body
        return html, figure, bundle

    html, figure, bundle = asyncio.run(scenario())
    headings = re.findall(r"<h2>\d+\. ([^<]+)</h2>", html)
    assert headings == ["Summary", "Experimental and methods", "Results", "Assessment", "Limitations", "References"]
    assert "XRD phase identification" in html and "search-match" in html and "no structure was refined" in html
    assert "Search-match statistics." in html and "Semi-quantitative phase analysis." in html
    assert "wt% (RIR)" in html and "I/Ic" in html and "Crystallite size, Scherrer (nm)" in html
    assert "COD&nbsp;9004178" in html and "COD&nbsp;9007432" in html
    assert "Hubbard" in html and "Chung" in html and "Patterson" in html and "Crystallography Open Database" in html
    assert "lower bounds" in html  # no instrument FWHM curve: the Scherrer sizes are not corrected
    assert "Every observed peak is assigned" in html
    assert html.index("Y. Fei") < html.index("Gražulis")  # DARA is reference 1, as in the Rietveld report
    assert "reference structure at its matched lattice" in html  # the RIR assumption fits this result
    for rietveld_only in ("Rwp", "GOF", "Fit statistics", "Phase verdicts", "Mass balance", "± esd", "Difference"):
        assert rietveld_only not in html, rietveld_only
    assert "ZnO 101 marker" in html and "<svg" in html  # the report figure carries the annotation
    assert "ZnO 101 marker" in figure and "Difference" not in figure and "Calculated" not in figure
    names = zipfile.ZipFile(io.BytesIO(bundle)).namelist()
    assert any(n.endswith(".docx") for n in names) and any(n.endswith((".pdf", ".html")) for n in names)


def test_le_bail_legend_marks_the_other_weights_relative():
    from types import SimpleNamespace

    from dara.plot import visualize

    x = np.linspace(20, 30, 50)
    result = SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=x * 0 + 5, y_calc=x * 0 + 5, y_bkg=x * 0 + 1,
                                  structs={"A_1_(cod_1)": x * 0 + 2, "B_2_(cod_2)": x * 0 + 2}),
        peak_data=pd.DataFrame({"phase": ["A_1_(cod_1)", "B_2_(cod_2)"], "2theta": [22.0, 25.0], "intensity": [1.0, 1.0],
                                "h": [1, 1], "k": [0, 1], "l": [0, 0]}),
        get_phase_weights=lambda: {"A_1_(cod_1)": 1.0},  # B is a Le Bail phase: no weight
    )
    names = [trace.name for trace in visualize(result=result).data if trace.name]
    assert any(name.endswith("(100.00 wt%, relative)") for name in names)
    result.get_phase_weights = lambda: {"A_1_(cod_1)": 0.25, "B_2_(cod_2)": 0.75}
    names = [trace.name for trace in visualize(result=result).data if trace.name]
    assert any(name.endswith("(25.00 wt%)") for name in names) and not any("relative" in name for name in names)


def test_worker_start_leaves_a_running_identification_to_the_server(server):
    from datetime import datetime, timezone

    from dara.server.utils import get_worker_store
    from dara.server.worker import add_task_record, mark_running_jobs_as_fizzled

    now = datetime.now(timezone.utc)
    ident = add_task_record({"uuid": "u-ident", "status": "RUNNING", "submitted_time": now, "job": {
        "name": "a.xy", "metadata": {"submission_settings": {"analysis_mode": "identification"}}}})
    search = add_task_record({"uuid": "u-search", "status": "RUNNING", "submitted_time": now, "job": {"name": "b.xy"}})

    def status(index):
        with get_worker_store() as store:
            return store.query_one({"index": index})["status"]

    mark_running_jobs_as_fizzled()  # the worker's start-up, while the server computes the identification
    assert status(ident) == "RUNNING" and status(search) == "FIZZLED"
    mark_running_jobs_as_fizzled(identification=True)  # the server's own start-up
    assert status(ident) == "FIZZLED"
