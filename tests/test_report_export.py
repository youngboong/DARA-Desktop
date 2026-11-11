import io

from dara.server.report import export
from dara.server.report.html import page, table


def test_docx_keeps_headings_tables_and_formulas():
    from docx import Document

    body = (
        "<h1>DARA report</h1><h2>3. Results</h2><p>Rwp = 2.77%, Y<sub>2</sub>O<sub>3</sub> found.</p>"
        + table(
            ["Phase", "Lattice parameters (Å, °)"],
            [["Y<sub>2</sub>O<sub>3</sub>", "<span><i>a</i> = 10.6</span><span><i>c</i> = 5.2</span>"]],
            caption="Table 3. Phase quantification.", cell_classes={1: "lp"},
        )
        + "<ul><li>first</li><li>second</li></ul>"
        + "<figure><svg></svg><figcaption>Figure 1. Fit.</figcaption></figure>"
    )
    document = Document(io.BytesIO(export.html_to_docx(page("DARA report", body))))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert "DARA report" in texts and "3. Results" in texts
    assert "Rwp = 2.77%, Y2O3 found." in texts
    assert "Table 3. Phase quantification." in texts and "Figure 1. Fit." in texts
    assert "first" in texts and "second" in texts
    cells = document.tables[0].rows
    assert cells[0].cells[0].text == "Phase"
    # one lattice parameter per line, each kept on one line (no-break spaces)
    assert cells[1].cells[1].text.replace("\u00a0", " ") == "a = 10.6\nc = 5.2"
    sub = [run for run in document.paragraphs[2].runs if run.text == "2"]
    assert sub and sub[0].font.subscript


def test_pdf_is_none_without_a_browser(monkeypatch):
    monkeypatch.setattr(export, "find_browser", lambda: None)
    assert export.html_to_pdf(page("x", "<p>x</p>")) is None


def test_formulas_in_report_text():
    from dara.server.report.html import formulas_in_text, tidy_formula

    assert tidy_formula("Y31.99952O47.99904") == "Y2O3"
    assert tidy_formula("Fe7.7841O12") == "Fe7.784O12"
    html = formulas_in_text(
        '<p>Y2O3 · Ia-3 and Fe7.784O12 · C12/c1 (EPS2), file 2Fe3O4-3Y2O3_1000C.xrdml.</p>'
        '<a href="cod_Fe2O3">Fe2O3</a><svg><text>Y2O3</text></svg>'
    )
    assert "Y<sub>2</sub>O<sub>3</sub> · Ia-3" in html and "C2/c" in html
    assert "(EPS2)" in html and "2Fe3O4-3Y2O3_1000C.xrdml" in html  # not formulas
    assert 'href="cod_Fe2O3"' in html and "<text>Y2O3</text>" in html  # attributes and SVG untouched
