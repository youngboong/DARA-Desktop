import asyncio
import csv
import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from dara.server import api_router
from dara.server.table_export import (
    MULTI_TASK_EXTRA_COLUMNS,
    TABLE_COLUMNS,
    multi_task_rows,
    rows_to_csv,
    task_table_rows,
)


def _result(rwp, phase="CaF2_225_(cod_2300449)-0", with_a_only=True, cautions=()):
    lattice = {"a": {"value": 5.463821, "esd": 0.000078, "unit": "Å", "state": "refined"}}
    return {
        "rwp": rwp,
        "fit_quality": {
            "rwp": rwp, "rexp": 2.0, "gof": rwp / 2.0, "chi_squared": (rwp / 2.0) ** 2,
            "durbin_watson": 1.8, "two_theta_min": 10.0, "two_theta_max": 80.0,
            "rp": 3.456, "sample_displacement_eps2": -0.001957,
        },
        "phase_metadata": {phase: {"spacegroup_number": 225, "spacegroup_symbol": "Fm-3m"}},
        "phase_details": {
            phase: {
                "weight_percent": 100.0, "weight_esd_percent": 0.5,
                "lattice_parameters": lattice,
                "grain_sizes": [{"direction": "GrainSize(1,1,1)", "value": 52.0, "esd": 3.0, "unit": "nm"}],
                "refinement_status": {"level": "limited", "codes": ["lattice_at_limit", "size_not_resolvable"]},
                "size_strain": {
                    "microstrain_percent": {"value": 0.149, "esd": 0.0235}, "strain_state": "refined",
                },
            }
        },
        "peak_diagnostics": {"counts": {"missing": 1, "extra": 0, "intensity_mismatch": 0}},
        "cautions": [{"code": code} for code in cautions],
        "highlighted_phases": [phase],
    }


def _summary():
    candidates = [_result(5.0, cautions=("poor_fit", "correlated_residuals")), _result(6.0)]
    candidates[0]["source_index"] = 3
    candidates[1]["source_index"] = 7
    return {
        "task_label": "sample.xrdml", "final_result": _result(4.0, cautions=("poor_fit",)),
        "all_results": candidates,
    }


def test_rows_fill_symmetry_and_follow_review_selection():
    rows = task_table_rows(5, {"review": {"selection": 7}}, _summary())
    assert [row["candidate"] for row in rows] == ["final", "1", "2"]
    assert [row["source_index"] for row in rows] == [None, 3, 7]
    assert [row["adopted"] for row in rows] == ["", "", "yes"]
    row = rows[0]
    assert (row["a_angstrom"], row["b_angstrom"], row["c_angstrom"]) == (5.463821, 5.463821, 5.463821)
    assert row["a_esd"] == 0.000078 and row["b_esd"] is None and row["c_esd"] is None
    assert (row["alpha_deg"], row["beta_deg"], row["gamma_deg"]) == (90.0, 90.0, 90.0)
    assert row["crystallite_size_nm"] == 52.0 and row["crystallite_size_esd_nm"] == 3.0
    assert row["missing_peaks"] == 1 and row["cautions"] == "poor_fit"
    assert rows[1]["cautions"] == "poor_fit;correlated_residuals"
    assert "COD 2300449" in row["provenance"]
    assert row["phase_label"].startswith("Fluorite")
    assert "<" not in row["phase_label"]

    best_rows = task_table_rows(5, {"review": {"selection": "best"}}, _summary())
    assert [row["adopted"] for row in best_rows] == ["yes", "", ""]
    assert all(row["adopted"] == "" for row in task_table_rows(5, {}, _summary()))


def test_hexagonal_and_monoclinic_and_rhombohedral_fill():
    from dara.server.table_export import lattice_with_symmetry

    hexagonal = lattice_with_symmetry(
        {"a": {"value": 4.9, "esd": 0.1}, "c": {"value": 17.0, "esd": 0.2}}, 194
    )
    assert hexagonal["b"] == (4.9, None) and hexagonal["gamma"] == (120.0, None)
    assert hexagonal["c"] == (17.0, 0.2)
    mono = lattice_with_symmetry(
        {k: {"value": v, "esd": 0.01} for k, v in dict(a=1, b=2, c=3, beta=101).items()}, 14
    )
    assert mono["alpha"] == (90.0, None) and mono["beta"] == (101, 0.01)
    rhombo = lattice_with_symmetry(
        {"a": {"value": 5.0, "esd": 0.1}, "alpha": {"value": 55.0, "esd": 0.2}}, 166
    )
    assert rhombo["c"] == (5.0, None) and rhombo["gamma"] == (55.0, None)


def test_csv_bytes_bom_header_and_precision():
    rows = task_table_rows(5, {}, _summary())
    data = rows_to_csv(rows, TABLE_COLUMNS)
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed[0] == TABLE_COLUMNS
    assert len(parsed) == 1 + len(rows)
    record = dict(zip(parsed[0], parsed[1]))
    assert record["a_angstrom"] == "5.46382" and record["b_esd"] == ""
    assert record["a_esd"] == "0.00008" and record["weight_percent"] == "100.00"
    assert record["task_name"] == "sample.xrdml"


def test_new_columns_follow_the_status_fields():
    summary = _summary()
    summary["additional_search_options"] = {"wavelength": "Cu"}
    summary["final_result"]["confidence"] = {"phases": {"CaF2_225_(cod_2300449)-0": {"weight_flag": "uncertain"}}}
    summary["internal_standard"] = {
        "phases": [{"name": "CaF2_225_(cod_2300449)-0", "absolute_wt_percent": 28.4, "absolute_esd_percent": 0.45}],
        "amorphous": {"mixture_wt_percent": 8.8},
    }
    rows = task_table_rows(5, {}, summary)
    parsed = list(csv.DictReader(io.StringIO(rows_to_csv(rows, TABLE_COLUMNS).decode("utf-8-sig"))))
    final = parsed[0]
    assert final["a_state"] == "refined" and final["b_state"] == "dependent"
    assert final["alpha_deg"] == "90.000" and final["alpha_state"] == "dependent"
    assert final["weight_percent"] == "100.00" and final["crystallite_size_nm"] == "52.0"
    assert final["microstrain_percent"] == "0.149" and final["microstrain_esd_percent"] == "0.024"
    assert final["strain_state"] == "refined" and final["refinement_status"] == "limited"
    assert final["refinement_codes"] == "lattice_at_limit;size_not_resolvable"
    assert final["weight_flag"] == "uncertain"
    assert final["rwp"] == "4.00" and final["rp_percent"] == "3.46" and final["rexp"] == "2.00"
    assert final["sample_displacement_eps2"] == "-0.001957"
    assert final["wavelength"] == "1.54060" and final["anode"] == "Cu"
    assert final["absolute_wt_percent"] == "28.40" and final["amorphous_wt_percent"] == "8.80"
    assert parsed[1]["absolute_wt_percent"] == "" and parsed[1]["amorphous_wt_percent"] == ""  # candidate rows


def test_csv_neutralises_spreadsheet_formulas():
    rows = [{"task_id": 1, "task_name": "=HYPERLINK(1)", "candidate": "final"}]
    parsed = list(csv.reader(io.StringIO(rows_to_csv(rows, TABLE_COLUMNS).decode("utf-8-sig"))))
    assert parsed[1][1].startswith("'=")


def test_multi_task_rows_include_status_rows():
    entries = [
        (1, {"user": "me", "status": "COMPLETED", "job": {"name": "a.xy"}, "review": {"selection": "best"}}, _summary()),
        (2, {"user": "you", "status": "RUNNING", "job": {"name": "b.xy"}}, None),
        (3, None, None),
    ]
    rows = multi_task_rows(entries)
    assert [row["candidate"] for row in rows] == ["final", "status: RUNNING", "status: NOT_FOUND"]
    assert rows[0]["adopted"] == "yes" and rows[0]["user"] == "me"
    assert rows[1]["task_name"] == "b.xy" and rows[1].get("phase") is None
    data = rows_to_csv(rows, TABLE_COLUMNS + MULTI_TASK_EXTRA_COLUMNS)
    assert data.decode("utf-8-sig").splitlines()[0].endswith("user,submitted_on")


def test_task_id_parser():
    assert api_router._parse_task_ids("3, 1,3,2") == [3, 1, 2]
    for bad in ("1,x", "", "1,,-2", "1.5"):
        with pytest.raises(HTTPException) as caught:
            api_router._parse_task_ids(bad)
        assert caught.value.status_code == 400
    with pytest.raises(HTTPException):
        api_router._parse_task_ids(",".join(str(i) for i in range(1, 52)))
    assert len(api_router._parse_task_ids(",".join(str(i) for i in range(1, 51)))) == 50


class _Store:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        return next((r for r in self.records if r.get("index") == criteria.get("index")), None)


def test_csv_endpoints(monkeypatch):
    records = [
        {"index": 1, "status": "COMPLETED", "uuid": "u1", "job": {"name": "a b.xy"}, "user": "me"},
        {"index": 2, "status": "RUNNING", "uuid": "u2", "job": {"name": "b.xy"}},
    ]
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _Store(records))

    async def fake_result(task_id):
        return _summary()

    monkeypatch.setattr(api_router, "result", fake_result)
    response = asyncio.run(api_router.download_task_table(1))
    assert response.media_type.startswith("text/csv")
    assert 'filename="dara_task_1_a_b_table.csv"' in response.headers["content-disposition"]
    assert response.body.startswith(b"\xef\xbb\xbf")
    for task_id, status in ((2, 409), (99, 404)):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(api_router.download_task_table(task_id))
        assert caught.value.status_code == status

    multi = asyncio.run(api_router.download_tasks_table(ids="1,2,99"))
    lines = multi.body.decode("utf-8-sig").splitlines()
    assert len(lines) == 1 + 1 + 1 + 1
    assert lines[0].endswith("user,submitted_on")
    assert "status: RUNNING" in lines[2] and "status: NOT_FOUND" in lines[3]
    assert multi.headers["content-disposition"].endswith('dara_tasks_table.csv"')
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.download_tasks_table(ids="1,x"))
    assert caught.value.status_code == 400


def _fake_refinement(raw_lst="BGMN listing"):
    phase = SimpleNamespace(model_dump=lambda **kwargs: {"a": 0.5})
    return SimpleNamespace(
        lst_data=SimpleNamespace(raw_lst=raw_lst, phases_results={"CaF2_225_(cod_2300449)-0": phase}),
        plot_data=SimpleNamespace(
            x=np.array([10.0, 20.0]), y_obs=np.array([1.0, 2.0]), y_calc=np.array([1.0, 2.0]),
            y_bkg=np.array([0.0, 0.0]), structs={"CaF2_225_(cod_2300449)-0": np.array([1.0, 1.0])},
        ),
        visualize=lambda: SimpleNamespace(to_html=lambda **kwargs: "<html></html>"),
        refinement_metrics=object(),
        export_structure=lambda name: SimpleNamespace(to=lambda fmt: "data_x"),
    )


def test_archive_contains_lst_and_results_table():
    document = SimpleNamespace(
        xrd_data=SimpleNamespace(
            angles=np.array([10.0, 20.0]), intensities=np.array([1.0, 2.0]), errors=None
        ),
        final_result=_fake_refinement(),
        input_cifs=[],
        results=[([], _fake_refinement("candidate listing"))],
        missing_peaks=None,
        extra_peaks=None,
    )
    summary = _summary()
    summary["all_results"] = summary["all_results"][:1]
    summary["all_results"][0]["source_index"] = 0
    archive = api_router._make_result_archive(5, document, summary, review_selection=0)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"final/refinement.lst", "search_results/result_1/refinement.lst", "results_table.csv"} <= names
        assert bundle.read("final/refinement.lst") == b"BGMN listing"
        table = bundle.read("results_table.csv")
        assert table.startswith(b"\xef\xbb\xbf")
        rows = list(csv.DictReader(io.StringIO(table.decode("utf-8-sig"))))
        assert [row["candidate"] for row in rows] == ["final", "1"]
        assert [row["adopted"] for row in rows] == ["", "yes"]
        readme = bundle.read("README.txt").decode()
        assert "refinement.lst" in readme and "results_table.csv" in readme

    single = api_router._make_result_archive(5, document, summary, result_index=0)
    with zipfile.ZipFile(single) as bundle:
        assert "refinement.lst" in bundle.namelist()
        assert "results_table.csv" not in bundle.namelist()


def _archive_inputs():
    document = SimpleNamespace(
        xrd_data=SimpleNamespace(
            angles=np.array([10.0, 20.0]), intensities=np.array([1.0, 2.0]), errors=None
        ),
        final_result=_fake_refinement(),
        input_cifs=[],
        results=[([], _fake_refinement("candidate listing"))],
        missing_peaks=None,
        extra_peaks=None,
        search_kwargs={"wavelength": "Cu"},
    )
    summary = _summary()
    summary["all_results"] = summary["all_results"][:1]
    summary["all_results"][0]["source_index"] = 0
    return document, summary


def _adopted(rwp=3.0):
    adopted_fit = _fake_refinement("adopted listing")
    adopted_fit.lst_data.rwp = rwp
    return ("version:3", adopted_fit, {"cifs": [], "record": {}}, {"version": 3, "summary": _result(rwp)})


def test_archive_holds_the_adopted_version():
    document, summary = _archive_inputs()
    job = {"index": 5, "job": {"name": "s.xrdml"}, "review": {"selection": "version:3"}}
    archive = api_router._make_result_archive(
        5, document, summary, review_selection="version:3", job=job, adopted=_adopted()
    )
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert bundle.read("adopted_version_3/refinement.lst") == b"adopted listing"
        assert "final/refinement.lst" in names and "adopted_version_3/plot.html" in names
        exported = json.loads(bundle.read("summary.json"))
        assert exported["adopted"] == "version:3" and exported["adopted_rwp"] == 3.0
        readme = bundle.read("README.txt").decode()
        assert readme.splitlines()[2] == (
            "Adopted fit: version 3 (Rwp 3.00%). Files of the adopted fit are in adopted_version_3/."
        )
        rows = list(csv.DictReader(io.StringIO(bundle.read("results_table.csv").decode("utf-8-sig"))))
        assert sorted({(row["candidate"], row["adopted"]) for row in rows}) == [
            ("1", ""), ("final", ""), ("version 3", "yes"),
        ]
    plain = api_router._make_result_archive(5, document, summary, job=job)
    with zipfile.ZipFile(plain) as bundle:
        assert json.loads(bundle.read("summary.json"))["adopted"] is None
        assert not any(name.startswith("adopted_version") for name in bundle.namelist())


class _OneStore:
    def __init__(self, job, stored=None):
        self.job, self.stored = job, stored

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        return self.job if "index" in criteria else self.stored


def test_download_result_writes_the_adopted_version(monkeypatch):
    from dara.server import versions

    document, summary = _archive_inputs()
    job = {"index": 5, "uuid": "u", "status": "COMPLETED", "job": {"name": "s.xrdml"},
           "review": {"selection": "version:3"}}
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _OneStore(job))
    monkeypatch.setattr(api_router, "get_result_store", lambda: _OneStore(job, {"output": document}))
    monkeypatch.setattr(api_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda o: o))

    async def fake_result(task_id):
        return summary

    monkeypatch.setattr(api_router, "result", fake_result)
    adopted = _adopted()
    monkeypatch.setattr(api_router, "_adopted_version_record", lambda _job: adopted[3])
    monkeypatch.setattr(versions, "resolve_refinement", lambda *args: (adopted[1], adopted[2]))

    async def read():
        response = await api_router.download_result(5, None)
        return response, b"".join([chunk async for chunk in response.body_iterator])

    response, body = asyncio.run(read())
    assert "dara_task_5_s_all_results.zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(body)) as bundle:
        assert "adopted_version_3/refinement.lst" in bundle.namelist()
        assert json.loads(bundle.read("summary.json"))["adopted"] == "version:3"


@pytest.mark.parametrize(
    ("state", "word"), [("FIZZLED", "failed"), ("CANCELLED", "cancelled"), ("RUNNING", "Wait")]
)
def test_downloads_say_why_there_is_nothing_to_export(monkeypatch, state, word):
    job = {"index": 5, "uuid": "u", "status": state, "job": {"name": "a.xy"}}
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _OneStore(job))
    for call in (api_router.download_task_table(5), api_router.download_result(5, None)):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(call)
        assert caught.value.status_code == 409 and word in caught.value.detail


def test_classified_missing_counts_fill_the_new_columns():
    assert TABLE_COLUMNS.index("missing_peaks_significant") == TABLE_COLUMNS.index("missing_peaks") + 1
    assert TABLE_COLUMNS.index("missing_peaks_minor") == TABLE_COLUMNS.index("missing_peaks") + 2
    summary = _summary()
    summary["final_result"]["peak_diagnostics"]["counts_by_class"] = {
        "noise": 2, "minor": 3, "significant": 1, "instrument": 0, "total": 6,
    }
    rows = task_table_rows(5, {}, summary)
    assert (rows[0]["missing_peaks_significant"], rows[0]["missing_peaks_minor"]) == (1, 3)
    assert rows[0]["missing_peaks"] == 1  # the legacy count is untouched
    # old payloads have no classes: the cells stay empty
    assert rows[1]["missing_peaks_significant"] is None and rows[1]["missing_peaks_minor"] is None
    parsed = list(csv.reader(io.StringIO(rows_to_csv(rows, TABLE_COLUMNS).decode("utf-8-sig"))))
    first = dict(zip(parsed[0], parsed[1]))
    second = dict(zip(parsed[0], parsed[2]))
    assert (first["missing_peaks_significant"], first["missing_peaks_minor"]) == ("1", "3")
    assert (second["missing_peaks_significant"], second["missing_peaks_minor"]) == ("", "")
