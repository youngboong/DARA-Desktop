import asyncio
import json
import re
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import numpy as np
import pytest
from fastapi import FastAPI

from dara.server import quick_refine, review_router
from dara.server.report.builder import build_report_html
from dara.server.report.references import REFERENCES, Citations

A = "Fe2O3_167_(cod_1011163)-0"
B = "Al2O3_167_(cod_2300449)-0"


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def get(self, url):
        async def run():
            parts = urlsplit(url)
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": parts.path, "root_path": "",
                "query_string": parts.query.encode(), "server": ("test", 80),
                "client": ("test", 123), "headers": [],
            }, receive, send)
            start = next(item for item in messages if item["type"] == "http.response.start")
            raw = b"".join(
                item.get("body", b"") for item in messages if item["type"] == "http.response.body"
            )
            headers = {key.decode().title(): value.decode() for key, value in start["headers"]}
            return SimpleNamespace(
                status_code=start["status"], content=raw, headers=headers,
                text=raw.decode("utf-8", "replace"),
            )

        return asyncio.run(run())


def _refinement(rwp, names=(A,), parameters=20, eps2=(-0.001657, 3.3e-05)):
    def phase(weight):
        return SimpleNamespace(
            spacegroup_no=167, hermann_mauguin="R-3c", gewicht=(weight, 0.4),
            a=(0.5035, 0.0001), b=None, c=(1.375, 0.0002), alpha=None, beta=None, gamma=None,
            xray_density=3.2, rphase=4.0, model_extra={"GrainSize": (55.0, 3.0)}, unit="NM",
        )

    x = np.linspace(10, 80, 400)
    obs = 10 + 300 * np.exp(-0.5 * ((x - 33) / 0.15) ** 2)
    raw = (
        "Rietveld refinement to file(s) sample.xy\n"
        f"BGMN version 4.2.23, 4000 measured points, 78 peaks, {parameters} parameters\n"
    )
    weights = {name: 1.0 / len(names) for name in names}
    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
            phases_results={name: phase(w) for name, w in weights.items()},
            raw_lst=raw, EPS2=eps2,
        ),
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs + 1, y_bkg=np.full_like(x, 10.0)),
        get_phase_weights=lambda: weights,
    )


def _context(monkeypatch, *, final, results, wavelength="Cu"):
    quick_refine._DOCUMENTS.clear()  # a test may build a second context for the same task
    task = {
        "index": 9, "uuid": "task-report", "status": "COMPLETED", "user": "Analyst",
        "job": {"name": "Sample.xy"},
    }
    doc = SimpleNamespace(
        final_result=final, best_rwp=final.lst_data.rwp,
        results=[([], refinement) for refinement in results],
        xrd_data=SimpleNamespace(), task_label="sample",
        candidate_sources=["cod", "mp"],
        search_kwargs={
            "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "wavelength": wavelength,
            "refinement_params": {"excluded_ranges": [[20.5, 21.5]]},
        },
    )

    class Store:
        def __init__(self, kind):
            self.kind = kind

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            if self.kind == "worker":
                return deepcopy(task) if criteria.get("index") == task["index"] else None
            return {"output": doc}

    monkeypatch.setattr(review_router, "get_worker_store", lambda: Store("worker"))
    monkeypatch.setattr(quick_refine, "get_worker_store", lambda: Store("worker"))
    monkeypatch.setattr(quick_refine, "get_result_store", lambda: Store("result"))
    monkeypatch.setattr(
        quick_refine, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda output: output)
    )
    app = FastAPI()
    app.include_router(review_router.router)
    return ASGIClient(app), task, doc


@pytest.fixture
def plain(monkeypatch):
    return _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0), _refinement(8.0)])


def _headings(html, level):
    return re.findall(rf"<h{level}>(.*?)</h{level}>", html)


def test_headings_present_in_order(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert re.search(r'<h1>XRD phase analysis — <span class="fn">Sample\.xy</span></h1>', html)
    assert re.search(r"Task 9 · \d{4}-\d{2}-\d{2} · DARA ", html)
    # numbered section headings; the notes section is omitted without notes
    assert _headings(html, 2) == [
        "1. Summary", "2. Experimental and methods", "3. Results", "4. Assessment",
        "5. Limitations", "6. References",
    ]
    assert "4.1 Phase verdicts" in _headings(html, 3)
    captions = re.findall(r"<caption><b>(Table \d+)\.</b> (.*?)</caption>", html)
    assert [number for number, _ in captions][:3] == ["Table 1", "Table 2", "Table 3"]
    assert [text for _, text in captions][:3] == [
        "Analysis conditions.", "Fit statistics.", "Phase quantification.",
    ]
    assert "<figure>" in html and "Figure 1. Observed (red circles), calculated (black line)" in html
    # no adopted/non-default selection: no "Result shown" line, no old status wording
    assert "Result shown" not in html and "Minimum-Rwp" not in html
    assert "Recorded settings" not in html and "<pre>" not in html


def test_methods_paragraph_and_references(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    methods = html.split("<h2>2. Experimental and methods</h2>")[1].split("<table")[0]
    for token in ("BGMN 4.2.23", "DARA", "Aeris-fds-Pixcel1d-Medipix3", "λ = 1.540598 Å", "Cu Kα"):
        assert token in methods
    assert "zero-point error was fixed at zero" in methods
    references = html.split("<h2>6. References</h2>")[1]
    assert references.count("<li>") >= 5
    assert "10.1021/acs.chemmater.5c02820" in references
    assert "<ol" in references
    # citation numbers in the text match the list length
    cited = set(re.findall(r"\[(\d+)\]", html.split("<h2>6. References</h2>")[0]))
    assert cited == {str(i) for i in range(1, references.count("<li>") + 1)}
    # DARA is cited first, Toby last (Limitations)
    assert references.index("Dara: Automated") < references.index("R factors in Rietveld")


def test_report_is_script_free_english_and_small(plain):
    client, _, _ = plain
    response = client.get("/api/task/9/report?best=true")
    html = response.text
    assert "plotly" not in html.lower()
    assert "<script" not in html.lower()
    assert not re.search("[가-힣]", html)
    assert len(response.content) < 3_000_000
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "<svg " in html and "Excluded from refinement" in html


def test_hamilton_reference_only_when_a_result_exists(monkeypatch, plain):
    client, _, _ = plain
    assert "Significance tests on the crystallographic R factor" not in client.get(
        "/api/task/9/report?best=true"
    ).text

    final = _refinement(10.0, names=(A, B), parameters=20)
    without_b = _refinement(10.005, names=(A,), parameters=10)
    client, _, _ = _context(monkeypatch, final=final, results=[final, without_b])
    html = client.get("/api/task/9/report?best=true").text
    assert "Significance tests on the crystallographic R factor" in html
    assert "Hamilton" in html and "not significant" in html
    # the one-line verdict note for the weakly supported phase names the alternative candidate
    verdicts = html.split("<h3>4.1 Phase verdicts</h3>")[1].split("<h3>")[0].split("<h2>")[0]
    assert "<li>" in verdicts and "fits almost as well" in verdicts
    assert "All phases are supported by every criterion" not in verdicts
    assert not re.search("[가-힣]", html)
    # the references remain in citation order: Hamilton comes before Toby
    references = html.split("References</h2>")[1]
    assert references.index("Significance tests") < references.index("R factors in Rietveld")


def test_candidate_report_uses_selected_refinement(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?idx=1").text
    # stored result 2 has the same phases as result 1, so it is merged into candidate 1
    assert "Result shown: candidate 1 (stored #2)" in html
    assert "Rwp = 8.00%" in html


def test_summary_sentence_and_confidence_section(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    summary = html.split("<h2>1. Summary</h2>")[1].split("<h2>")[0]
    assert re.search(r"Quantified phases: .*100\.0\u202f±\u202f0\.\d\xa0wt%", summary)
    assert "Overall assessment:" in summary and "GOF = " in summary
    # Fit-statistics table: two-decimal values and the refined displacement
    fit = html.split("Fit statistics.</caption>")[1].split("</table>")[0]
    assert '<td class="c nw">7.00</td>' in fit and "−0.24" in fit
    assert re.findall(r"<th(?: [^>]*)?>(.*?)</th>", fit) == [
        "Rwp (%)", "Rp (%)", "Rexp (%)", "GOF", "Displacement (mm)",
    ]
    assert "χ²" not in fit and "Durbin" not in fit
    # the Limitations sentence still quotes d
    assert "(Durbin–Watson d = " in html.split("<h2>5. Limitations</h2>")[1]
    # one-sentence verdict line instead of the old confidence table
    # overall "Check recommended": no "all phases are supported" sentence (design 2.1 I)
    assert "All phases are supported by every criterion" not in html
    assert "Each phase passes the per-phase criteria. The overall &#x27;Check recommended&#x27; comes from the pattern: GOF is" in html
    assert "Hamilton test" not in html and "Weight / σ" not in html


def test_synchrotron_wavelength_and_unknown_radiation(monkeypatch):
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)], wavelength="0.0826"
    )
    html = client.get("/api/task/9/report?best=true").text
    assert "synchrotron radiation (λ = 0.826000 Å)" in html
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)], wavelength="Unobtainium"
    )
    assert "radiation and wavelength not recorded" in client.get("/api/task/9/report?best=true").text


def test_citations_registry_numbers_in_first_citation_order():
    citations = Citations()
    assert citations.cite("toby") == "[1]"
    assert citations.cite("dara") == "[2]"
    assert citations.cite("toby") == "[1]"
    html = citations.render_html()
    assert html.index("R factors") < html.index("Dara: Automated")
    with pytest.raises(KeyError):
        citations.cite("unknown")
    assert all(entry["text"] for entry in REFERENCES.values())
    assert REFERENCES["dara"]["doi"] == "10.1021/acs.chemmater.5c02820"
    assert REFERENCES["hamilton"]["doi"] == "10.1107/S0365110X65001081"
    assert REFERENCES["toby"]["doi"] == "10.1154/1.2179804"


def test_report_builder_can_be_called_directly():
    final = _refinement(7.0)
    doc = SimpleNamespace(
        final_result=final, best_rwp=7.0, results=[], xrd_data=SimpleNamespace(),
        search_kwargs={}, task_label="t",
    )
    html = build_report_html(
        3, {"job": {"name": "x.xy"}, "uuid": "u-direct"}, doc, "best",
        {"selection": None, "notes": "", "updated_at": None},
    )
    assert "<h2>5. Limitations</h2>" in html
    assert "recorded (radiation and wavelength not recorded)" in html
    assert json.dumps(html)  # serialisable text


def test_weight_text_rounds_to_the_precision_of_the_esd():
    from dara.server.report.builder import weight_text

    assert weight_text(25.8731, 0.6021) == "25.9 ± 0.6"
    assert weight_text(25.8731, 0.0421) == "25.87 ± 0.04"
    assert weight_text(25.8731, None) == "25.9"
    assert weight_text(None, 0.5) == "—"


def test_report_phase_table_uses_esd_precision(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert not re.search(r"\d\.\d{5,} ±", html)  # no 6-decimal weight percentages


def test_reported_mp_window_is_the_effective_one():
    from dara.server.report.builder import _effective_mp_window, _refinement_text

    window = [-0.045, 0.02]
    assert _effective_mp_window(0.02, window) == (-0.045, 0.02)  # default global range
    assert _effective_mp_window(0.05, window) == (-0.05, 0.05)  # wider global range wins
    assert _effective_mp_window("fixed", window) is None  # no widening happened
    summary = {"mp_lattice_window": {"range": window, "applied_to": ["Fe2O3_167_(mp_mp-1)-0"]}}
    assert "widened to -4.5% to +2.0%" in _refinement_text({"lattice_range": 0.02}, summary)
    assert "widened to -5.0% to +5.0%" in _refinement_text({"lattice_range": 0.05}, summary)
    assert "widened" not in _refinement_text({"lattice_range": "fixed"}, summary)


# --- unexplained observed peaks -------------------------------------------------------------

def _with_profile(refinement, injected, missing):
    x = np.arange(10.0, 80.0, 0.01)
    rng = np.random.default_rng(0)
    calc = 1000 + 10000 * np.exp(-0.5 * ((x - 33) / 0.065) ** 2)
    obs = calc + rng.normal(0, 31.6, x.size)
    for centre, height in injected:
        obs = obs + height * np.exp(-0.5 * ((x - centre) / 0.065) ** 2)
    refinement.plot_data = SimpleNamespace(x=x, y_obs=obs, y_calc=calc, y_bkg=np.full_like(x, 1000.0))
    refinement.refinement_metrics = SimpleNamespace(
        missing_peaks=missing, extra_peaks=[], intensity_mismatch_peaks=[]
    )
    return refinement


def test_report_lists_classified_unexplained_peaks(monkeypatch):
    final = _with_profile(_refinement(7.0), [(45.0, 150.0), (60.0, 20.0)], [[45.0, 3.0], [60.0, 1.0]])
    client, _, _ = _context(monkeypatch, final=final, results=[final])
    html = client.get("/api/task/9/report?best=true").text
    assert "Unexplained peaks</h3>" in html
    assert "at least 2% of the strongest peak with S/N ≥ 8" in html
    assert "% of strongest" in html and "Interpretation" in html
    assert '<td class="c nw">45.00</td>' in html and '<td class="c nw">minor</td>' in html
    assert '<td class="r nw">60.00</td>' not in html  # a noise-level feature is not listed
    assert "further weak feature" not in html
    # the peak rows are not repeated as a caution bullet
    assert "minor unexplained peak(s)" not in html


def test_report_without_profiles_has_no_unexplained_table(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert "Unexplained peaks" not in html
    assert "4. Assessment" in html


# --- booktabs layout, conditions table and phase table --------------------------------------

def _plain_doc():
    return SimpleNamespace(
        final_result=_refinement(7.0), best_rwp=7.0, results=[], xrd_data=SimpleNamespace(),
        search_kwargs={}, task_label="t",
    )


def test_report_style_is_black_and_white_booktabs(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "overflow-wrap:anywhere" not in style
    assert "Latin Modern Roman" in style and "tabular-nums" in style
    assert "1.5px solid #000" in style and ".75px solid #000" in style
    assert not re.findall(r"#(?!000\b|fff\b)[0-9a-fA-F]{3,6}\b", style)  # only black and white
    assert "<colgroup>" in html and 'style="width:' in html


def test_analysis_conditions_table_is_human_readable(monkeypatch):
    client, task, doc = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])
    doc.analysis_mode = "reaction"
    doc.elements = ["Fe", "O"]
    doc.precursors = ["Fe2O3"]
    doc.final_refinement_params = {"gewicht": "SPHAR4", "lattice_range": 0.02}
    doc.predict_kwargs = {"temp": 1273}
    task["end_time"] = "2026-09-09 04:32:02.661000"
    html = client.get("/api/task/9/report?best=true").text
    conditions = html.split("Analysis conditions.</caption>")[1].split("</table>")[0]
    rows = dict(re.findall(r'<tr><td class="c">(.*?)</td><td class="c">(.*?)</td></tr>', conditions))
    assert rows["Analysis mode"].startswith("Reaction-guided")
    assert rows["Elements"] == "Fe, O" and rows["Precursor formulas"] == "Fe<sub>2</sub>O<sub>3</sub>"
    assert rows["Instrument profile"] == "Aeris-fds-Pixcel1d-Medipix3"
    assert rows["Reaction prediction"] == "1000 °C (1273 K)"
    assert "Instrument" not in rows  # no acquisition metadata recorded
    # the five rows that repeat the methods paragraph (or are not needed) are gone ...
    for removed in ("Completed", "Databases", "Radiation", "2θ range analysed", "Final refinement"):
        assert removed not in rows
    assert "2026-09-09" not in html
    # ... and the paragraph above the table still carries those facts
    methods = html.split("<h2>2. Experimental and methods</h2>")[1].split("<table")[0]
    for fact in (
        "Cu Kα radiation (λ = 1.540598 Å)", "excluding 1 interval (20.50–21.50° 2θ)",
        "Crystallography Open Database", "Materials Project", "spherical harmonics of order 4",
        "lattice parameters (bounded to ±2.0%",
    ):
        assert fact in methods
    assert re.search(r"Data from 10\.00° to 80\.00° 2θ \(400 points\)", methods)
    assert "Analyst" not in html and "Submitted" not in html


def test_analysis_conditions_keeps_instrument_row(monkeypatch):
    import dara.xrd

    client, _, doc = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])
    doc.xrd_data = SimpleNamespace(xrdml_dict={"x": 1})
    monkeypatch.setattr(dara.xrd, "get_xrdml_metadata", lambda _: {
        "application_software": "Data Collector", "xray_tube": "Cu (LFF)", "detector": "Pixcel1d",
    })
    html = client.get("/api/task/9/report?best=true").text
    conditions = html.split("Analysis conditions.</caption>")[1].split("</table>")[0]
    rows = dict(re.findall(r'<tr><td class="c">(.*?)</td><td class="c">(.*?)</td></tr>', conditions))
    assert rows["Instrument"] == "Data Collector; Cu; Pixcel1d"


def test_phase_table_columns_and_reference_labels(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    headers = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", table)
    assert [h.replace("\u00a0", " ") for h in headers] == [
        "Phase", "Space group", "wt% ± esd", "Lattice parameters (Å, °)",
        "Crystallite size (nm)", "Verdict", "Reference",
    ]  # no microstrain value for any phase: the column is dropped
    assert "COD&nbsp;1011163" in table and "Fe2O3_167_(cod_1011163)-0" not in html
    assert "GrainSize" not in html
    assert "55.0\u202f±\u202f3.0" in table  # refined crystallite size: value ± esd only
    assert "<span><i>a</i> = " in table and "<span><i>c</i> = " in table


def test_unrefined_grain_size_is_not_reported_for_every_phase(monkeypatch):
    final = _refinement(10.0, names=(A, B))
    for phase in final.lst_data.phases_results.values():
        # BGMN prints the same start-value GrainSize, without esd, for unrefined phases
        phase.model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": 0.005}
    client, _, _ = _context(monkeypatch, final=final, results=[final])
    html = client.get("/api/task/9/report?best=true").text
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    assert "84.18" not in table
    assert len(re.findall('<td(?: class="[^"]*")?>—</td>', table)) >= 2


def test_lattice_cell_uses_greek_letters_and_esd_precision():
    from dara.server.report import builder

    cell = builder._lattice_cell({"lattice_parameters": {
        "a": {"value": 8.73139, "esd": 0.00099, "unit": "Å"},
        "beta": {"value": 122.44, "esd": 0.013, "unit": "deg"},
    }})
    assert "<i>β</i> = 122.440\u202f±\u202f0.013" in cell and "beta" not in cell
    assert "<i>a</i> = 8.73139\u202f±\u202f0.00099" in cell
    assert cell.count("<span>") == 2  # one parameter per line


def test_user_notes_only_when_recorded_and_escaped(plain):
    client, _, _ = plain
    assert "User review notes" not in client.get("/api/task/9/report?best=true").text
    html = build_report_html(
        3, {"job": {"name": "x.xy"}, "uuid": "u"}, _plain_doc(), "best",
        {"selection": None, "notes": "<b>check</b> peaks", "updated_at": None},
    )
    assert "6. User review notes" in html and "&lt;b&gt;check&lt;/b&gt; peaks" in html


def _annotated(monkeypatch, *, high=False):
    """Report context whose two phases carry the WP5 fields (failed / limited, at-limit lattice)."""
    from dara.server import api_router, confidence

    original = api_router._phase_refinement_details

    def details(*args, **kwargs):
        out = original(*args, **kwargs)
        failed, limited = out[A], out[B]
        failed["refinement_status"] = {
            "level": "failed", "codes": [], "undefined": {"k1": "UNDEF"}, "message": "failed",
        }
        failed["crystallite_size_status"] = "not_resolvable"
        failed["grain_sizes"] = []
        failed["structure_refinement"] = {"sites": [{
            "index": 0, "label": "Fe1", "xyz": [
                {"value": 0.1, "esd": 0.001, "state": "refined"},
                {"value": 0.2, "esd": None, "state": "at_limit"},
                {"value": 0.3, "esd": None, "state": "fixed"},
            ],
            "species": [{"element": "Fe", "occupancy": {"value": 0.95, "esd": 0.02}}],
            "b_iso": {"value": 1.0, "esd": None, "state": "fixed"},
        }], "indicative": True}
        limited["refinement_status"] = {
            "level": "limited", "codes": ["lattice_fixed"], "undefined": {},
            "message": "Lattice held at the database values.",
        }
        limited["crystallite_size_status"] = "not_refined"
        limited["grain_sizes"] = []
        limited["size_strain"] = {
            "strain_state": "refined", "microstrain_percent": {"value": 0.149, "esd": 0.024},
            "note": "All excess broadening was assigned to microstrain; crystallite size is "
                    "beyond the resolvable range.",
        }
        limited["lattice_parameters"]["a"]["state"] = "at_limit"
        return out

    real = confidence.confidence_for_selection

    def with_flags(*args, **kwargs):
        result = real(*args, **kwargs)
        result["phases"][A]["weight_flag"] = "unreliable"
        result["phases"][B]["weight_flag"] = "uncertain"
        if high:
            result["overall"]["level"] = "high"
        return result

    monkeypatch.setattr(api_router, "_phase_refinement_details", details)
    monkeypatch.setattr(confidence, "confidence_for_selection", with_flags)
    final = _refinement(10.0, names=(A, B))
    return _context(monkeypatch, final=final, results=[final])


def test_status_footnotes_failed_size_and_at_limit(monkeypatch):
    client, _, _ = _annotated(monkeypatch)
    html = client.get("/api/task/9/report?best=true").text
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    assert "Refinement failed" in table and "0.149" in table
    assert "Microstrain (%)" in table  # one phase has a value: the column stays
    assert "(at limit)" in table
    assert "Uncertain amount. " in html and "its amount is not reliable" in html
    assert "(k1 undefined)" in html and "Lattice held at the database values" in html
    assert "'Not resolvable' means" not in html  # no cell shows it: failed phase and no size
    assert "All excess broadening was assigned to microstrain" in html
    assert "Microstrain is the BGMN strain-broadening parameter" in html


def test_all_phases_sentence_only_when_overall_is_high(monkeypatch):
    from dara.server.report import builder

    monkeypatch.setattr(builder, "_phase_notes", lambda *args, **kwargs: [])
    client, _, _ = _annotated(monkeypatch, high=True)
    assert "All phases are supported by every criterion." in client.get(
        "/api/task/9/report?best=true"
    ).text


def test_internal_standard_and_structure_sections(monkeypatch):
    from dara.server import api_router

    client, _, _ = _annotated(monkeypatch)
    monkeypatch.setattr(api_router, "internal_standard_block", lambda *a, **k: {
        "spec": {"formula": "Si", "wt_percent": 10.0}, "status": "ok",
        "fitted_wt_percent": 12.5, "fitted_esd_percent": 0.3, "scale": 0.8,
        "phases": [{"name": A, "absolute_wt_percent": 30.1, "absolute_esd_percent": 0.9,
                    "sample_wt_percent": 33.4, "sample_esd_percent": 1.0}],
        "amorphous": {"mixture_wt_percent": -1.0, "sample_wt_percent": -1.1,
                      "esd_percent": 0.8, "not_detected": True},
        "message": None,
    })
    html = client.get("/api/task/9/report?best=true").text
    assert "Absolute quantification (internal standard)" in html
    assert "Internal standard Si, 10 wt% added" in html and "0 (not detected; fitted" in html
    assert "Structure refinement (indicative)" in html and "Fe1" in html
    assert "0.2000 (at limit)" in html and "0.3000 (fixed)" in html


def test_spacegroup_cell_names_the_refined_subgroup():
    from dara.server.report import builder

    cell = builder._spacegroup_cell({
        "spacegroup_number": 15, "spacegroup_symbol": "C2/c",
        "database_spacegroup_number": 167, "database_spacegroup_symbol": "R-3c",
    })
    assert "R-3c" in cell and "No. 167" in cell and "(refined as C2/c)" in cell


def test_lattice_cell_leads_with_the_database_setting():
    from dara.server.report import builder

    cell = builder._lattice_cell({
        "lattice_parameters": {"a": {"value": 8.7294, "esd": 0.0023, "unit": "Å"},
                               "beta": {"value": 122.437, "esd": 0.026, "unit": "deg"}},
        "database_lattice": {"spacegroup_number": 167,
                             "a": {"value": 5.0397, "esd": 0.001}, "b": {"value": 5.0397, "esd": 0.001},
                             "c": {"value": 13.7505, "esd": 0.0052},
                             "alpha": {"value": 89.97}, "beta": {"value": 90.03}, "gamma": {"value": 120.01}},
    })
    assert cell.index("setting:") < cell.index("13.75") < cell.index("refined cell:") < cell.index("8.729")
    assert "R-3c" in cell
    assert cell.count("5.03") == 1  # b equal to a is not repeated; angles at 90/120 are left out


# --- merged notes, microstrain column, mass-balance table ------------------------------------

C = "Cr2O3_167_(cod_9009093)-0"
NO_STRAIN = (
    "No strain broadening detected; all excess broadening was assigned to crystallite size. "
    "Size assumes the upper limit of the size-distribution parameter (k1 = 1)."
)


def _strain_context(monkeypatch, states, *, names=(A, B, C)):
    """Report context whose phases carry ``(strain_state, microstrain value, note)`` in order."""
    from dara.server import api_router

    original = api_router._phase_refinement_details

    def details(*args, **kwargs):
        out = original(*args, **kwargs)
        for phase, (state, value, note) in zip(names, states):
            out[phase]["size_strain"] = {
                "strain_state": state, "note": note,
                "microstrain_percent": {"value": value, "esd": 0.02} if value is not None else {},
            }
        return out

    monkeypatch.setattr(api_router, "_phase_refinement_details", details)
    final = _refinement(10.0, names=names)
    return _context(monkeypatch, final=final, results=[final])[0]


def _note_paragraphs(html):
    return [
        re.sub(r"<[^>]+>", "", text).replace(" ", " ")
        for text in re.findall(r'<p class="note">(.*?)</p>', html)
    ]


def _phase_headers(html):
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    return [
        re.sub(r"<[^>]+>", "", h).replace(" ", " ")
        for h in re.findall(r"<th(?: [^>]*)?>(.*?)</th>", table)
    ]


def test_identical_notes_of_all_phases_print_once_with_all_phases(monkeypatch):
    client = _strain_context(monkeypatch, [("not_detected", None, NO_STRAIN)] * 3)
    html = client.get("/api/task/9/report?best=true").text
    notes = _note_paragraphs(html)
    assert notes.count(f"All phases: {NO_STRAIN}") == 1
    assert sum(NO_STRAIN in note for note in notes) == 1  # not repeated per phase


def test_identical_notes_of_some_phases_list_those_phases(monkeypatch):
    other = "Size and strain cannot be separated from this pattern."
    client = _strain_context(monkeypatch, [
        ("not_detected", None, NO_STRAIN), ("not_detected", None, NO_STRAIN),
        ("not_detected", None, other),
    ])
    notes = _note_paragraphs(client.get("/api/task/9/report?best=true").text)
    merged = [note for note in notes if note.endswith(f": {NO_STRAIN}")]
    assert len(merged) == 1 and not merged[0].startswith("All phases")
    listed = merged[0][: -len(f": {NO_STRAIN}")].split(", ")
    assert len(listed) == 2 and all("Cr" not in name for name in listed)
    differing = [note for note in notes if note.endswith(f": {other}")]
    assert len(differing) == 1 and "Cr" in differing[0] and ", " not in differing[0].split(":")[0]


def test_microstrain_column_dropped_without_a_detected_value(monkeypatch):
    client = _strain_context(monkeypatch, [("not_detected", None, NO_STRAIN)] * 2 + [("not_refined", None, None)])
    html = client.get("/api/task/9/report?best=true").text
    assert "Microstrain" not in html
    assert _phase_headers(html) == [
        "Phase", "Space group", "wt% ± esd", "Lattice parameters (Å, °)", "Crystallite size (nm)",
        "Verdict", "Reference",
    ]
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    assert all(len(re.findall(r"<td", row)) == 7 for row in table.split("<tr>")[2:])


def test_microstrain_column_kept_with_one_value(monkeypatch):
    client = _strain_context(monkeypatch, [
        ("refined", 0.149, None), ("not_detected", None, NO_STRAIN), ("not_detected", None, NO_STRAIN),
    ])
    html = client.get("/api/task/9/report?best=true").text
    assert "Microstrain (%)" in _phase_headers(html) and len(_phase_headers(html)) == 8
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    assert "0.149" in table and "Not detected" in table
    assert "Microstrain is the BGMN strain-broadening parameter" in html


def _mass_balance_client(monkeypatch, level):
    from dara.server import api_router

    block = {
        "status": "evaluated", "source": "filename", "level": level,
        "observed_ratio": "Fe:O = 2:3", "expected_ratio": "Fe:O = 2:3.2", "max_deviation": 0.04,
        "excluded_elements": [],
        "elements": [
            {"element": "Fe", "expected": 0.4, "observed": 0.4, "deviation": 0.0},
            {"element": "O", "expected": 0.6, "observed": 0.56, "deviation": -0.04},
        ],
    }
    monkeypatch.setattr(api_router, "_mass_balance_amounts", lambda task, document: ({}, "filename"))
    monkeypatch.setattr(api_router, "_mass_balance_block", lambda *args, **kwargs: block)
    final = _with_profile(_refinement(7.0), [(45.0, 150.0)], [[45.0, 3.0]])
    return _context(monkeypatch, final=final, results=[final])[0]


def _captions(html):
    return re.findall(r"<caption><b>Table (\d+)\.</b> (.*?)</caption>", html)


def test_mole_fraction_table_omitted_when_mass_balance_agrees(monkeypatch):
    html = _mass_balance_client(monkeypatch, "ok").get("/api/task/9/report?best=true").text
    assert "agree within the tolerance (largest deviation 0.04)" in html
    assert "Element mole fractions" not in html and "<th>Precursors</th>" not in html
    captions = _captions(html)
    assert [number for number, _ in captions] == ["1", "2", "3", "4"]  # no gap
    assert captions[-1][1] == "Unexplained observed peaks."


def test_mole_fraction_table_kept_with_deviation_and_numbering_follows(monkeypatch):
    html = _mass_balance_client(monkeypatch, "caution").get("/api/task/9/report?best=true").text
    assert "differ moderately" in html
    captions = _captions(html)
    assert [number for number, _ in captions] == ["1", "2", "3", "4", "5"]
    assert captions[3][1] == "Element mole fractions." and captions[4][1] == "Unexplained observed peaks."


def test_docx_export_of_a_report_with_trimmed_tables():
    import io

    from docx import Document

    from dara.server.report.export import html_to_docx

    html = build_report_html(
        3, {"job": {"name": "x.xy"}, "uuid": "u-docx"}, _plain_doc(), "best",
        {"selection": None, "notes": "", "updated_at": None},
    )
    document = Document(io.BytesIO(html_to_docx(html)))
    headers = [[cell.text for cell in table.rows[0].cells] for table in document.tables]
    assert headers[0] == ["Condition", "Value"]
    assert headers[1] == ["Rwp (%)", "Rp (%)", "Rexp (%)", "GOF", "Displacement (mm)"]
    assert len(headers[2]) == 7 and "Microstrain (%)" not in headers[2]
