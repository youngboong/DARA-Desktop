"""Le Bail phases in the fit-compare payload and in the report's Methods text."""

import json
from types import SimpleNamespace

from dara.server import versions_router
from dara.server.report.builder import build_report_html
from test_report import A, _refinement

LE_BAIL = "Co3O4_227_(cod_9005896)"
RIETVELD = "ZnO_186_(cod_1)"


def test_fit_compare_side_lists_le_bail_phase_without_a_weight():
    summary = {
        "rwp": 1.6, "weight_percent": {RIETVELD: 100.0}, "phases": [RIETVELD, LE_BAIL], "weights_relative": True,
        "phase_details": {RIETVELD: {"weight_percent": 100.0}, LE_BAIL: {"le_bail": True, "weight_percent": None}},
    }
    side = versions_router._lite("best", {"label": "Le Bail: Co3O4"}, summary)
    assert side["weight_percent"] == {RIETVELD: 100.0}
    assert set(side["phase_identity"]) == {RIETVELD, LE_BAIL}
    assert side["le_bail_phases"] == [LE_BAIL] and side["weights_relative"] is True
    assert side["phase_symbols"][LE_BAIL] == "Fd-3m"
    plain = versions_router._lite("best", {}, {"weight_percent": {RIETVELD: 50.0}})
    assert plain["le_bail_phases"] == [] and plain["weights_relative"] is False
    assert json.dumps(side)


def _report(monkeypatch, le_bail):
    import dara.result

    monkeypatch.setattr(dara.result, "le_bail_phases", lambda lst_data: le_bail)
    doc = SimpleNamespace(final_result=_refinement(7.0), best_rwp=7.0, results=[], xrd_data=SimpleNamespace(),
                          search_kwargs={}, task_label="t")
    return build_report_html(3, {"job": {"name": "x.xy"}, "uuid": "u-le-bail"}, doc, "best",
                             {"selection": None, "notes": "", "updated_at": None})


def test_methods_text_names_the_le_bail_phase(monkeypatch):
    html = _report(monkeypatch, {A})
    assert "was fitted by the Le Bail method" in html
    assert "the weight fractions of the other phases are relative to each other" in html
    assert "Le Bail" not in _report(monkeypatch, set())
