"""Portable copies of the HTML report: a PDF printed by a local Edge/Chrome and an editable Word file.

The HTML report stays the source. The PDF is the browser's print of it (the print stylesheet in
``html.py`` applies); the .docx is built from the same HTML, so both carry the same text, tables
and figure. The inline SVG figure is replaced in Word by a PNG of the same fit.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    """Edge or Chrome for printing (``DARA_PDF_BROWSER`` wins), or ``None``."""
    configured = os.environ.get("DARA_PDF_BROWSER")
    if configured and Path(configured).is_file():
        return configured
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return next((path for path in _BROWSER_CANDIDATES if Path(path).is_file()), None)


def html_to_pdf(html: str, timeout: float = 120) -> bytes | None:
    """The report printed to PDF by a headless browser; ``None`` when no browser can print it."""
    browser = find_browser()
    if not browser:
        return None
    # The browser may still hold its profile for a moment (WinError 145 on cleanup): ignore that.
    with tempfile.TemporaryDirectory(prefix="dara-report-", ignore_cleanup_errors=True) as tmp:
        source = Path(tmp) / "report.html"
        target = Path(tmp) / "report.pdf"
        source.write_text(html, encoding="utf-8")
        command = [
            browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            "--no-first-run", "--disable-extensions",
            # A profile of its own: never attaches to (or locks) the user's open browser.
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--print-to-pdf={target}", source.as_uri(),
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return target.read_bytes() if target.is_file() and target.stat().st_size > 0 else None


# ---- HTML -> Word ----------------------------------------------------------------------------

_VOID = {"br", "col", "meta", "img", "wbr", "hr", "link", "input", "source"}
_SKIP = {"head", "style", "script", "title", "colgroup"}


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict):
        self.tag, self.attrs, self.children = tag, attrs, []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class _TreeBuilder(HTMLParser):
    """Element tree of the report HTML; ``<svg>`` subtrees are kept as empty markers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in _VOID:
                self.skip += 1
            return
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag in _SKIP or tag == "svg":
            self.skip = 1
        elif tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if not self.skip:
            self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in _VOID:
                self.skip -= 1
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].children.append(data)


def _text_of(node) -> str:
    if isinstance(node, str):
        return node
    return "".join(_text_of(child) for child in node.children)


def html_to_docx(html: str, figure_png: bytes | None = None) -> bytes:
    """Word document with the report's headings, paragraphs, lists, tables and figure."""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    builder = _TreeBuilder()
    builder.feed(html)
    body = next((node for node in builder.root.children if isinstance(node, _Node) and node.tag == "html"), builder.root)
    body = next((node for node in body.children if isinstance(node, _Node) and node.tag == "body"), body)

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Mm(18))
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    usable_width = section.page_width - section.left_margin - section.right_margin
    # Headings as in the PDF: serif, black, bold; no Word theme colour, font or title rule.
    for name, size in (("Title", 20), ("Heading 1", 13), ("Heading 2", 11.5), ("Heading 3", 10.5)):
        heading_style = doc.styles[name]
        heading_style.font.name = "Times New Roman"
        for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            heading_style.element.rPr.rFonts.attrib.pop(qn(attribute), None)
        heading_style.font.size = Pt(size)
        heading_style.font.bold = True
        heading_style.font.color.rgb = RGBColor(0, 0, 0)
    for border in doc.styles["Title"].element.findall(".//" + qn("w:pBdr")):
        border.getparent().remove(border)
    caption_style = doc.styles["Caption"]  # black, upright captions as in the PDF
    caption_style.font.color.rgb = RGBColor(0, 0, 0)
    caption_style.font.italic = False
    caption_style.font.bold = False  # only the "Table 1." label is bold, as in the PDF
    caption_style.font.size = Pt(9.5)

    def add_inline(paragraph, node, style=None, preserve=False):
        """Runs of ``node`` into ``paragraph``; ``style`` = {italic, bold, sub, sup, size}."""
        style = dict(style or {})
        if isinstance(node, str):
            # Word breaks lines at U+202F, so values keep together with the plain no-break space.
            text = (node if preserve else re.sub(r"[ \t\r\n]+", " ", node)).replace("\u202f", "\u00a0")
            if style.get("nobreak"):  # what never wraps in the PDF (lattice lines, .nw) never wraps here
                text = text.replace(" ", "\u00a0").replace("-", "\u2011")  # also no break at R-3c
            if not text:
                return
            if preserve and "\n" in text:
                for index, line in enumerate(text.split("\n")):
                    if index:
                        paragraph.add_run().add_break()
                    add_inline(paragraph, line, style, preserve)
                return
            run = paragraph.add_run(text)
            run.italic = style.get("italic") or None
            run.bold = style.get("bold") or None
            # Set only when on: assigning None to one clears the other (both are vertAlign).
            if style.get("sub"):
                run.font.subscript = True
            elif style.get("sup"):
                run.font.superscript = True
            if style.get("size"):
                run.font.size = Pt(style["size"])
            return
        if node.tag == "br":
            paragraph.add_run().add_break()
            return
        if node.tag in ("i", "em"):
            style["italic"] = True
        elif node.tag in ("b", "strong", "th"):
            style["bold"] = True
        elif node.tag == "sub":
            style["sub"] = True
        elif node.tag == "sup":
            style["sup"] = True
        if node.classes & {"nw", "fn"}:
            style["nobreak"] = True
        if "sub" in node.classes or "note" in node.classes:
            style["size"] = 9
        preserve = preserve or "notes" in node.classes
        for child in node.children:
            add_inline(paragraph, child, style, preserve)

    def tidy(paragraph):
        # Drop the space left by collapsed whitespace at the start and end of a paragraph.
        runs = paragraph.runs
        if runs and runs[0].text:
            runs[0].text = runs[0].text.lstrip()
        if runs and runs[-1].text:
            runs[-1].text = runs[-1].text.rstrip()

    def add_table(node):
        caption = next((child for child in node.children if isinstance(child, _Node) and child.tag == "caption"), None)
        if caption is not None:
            paragraph = doc.add_paragraph(style="Caption")
            add_inline(paragraph, caption)
            tidy(paragraph)
        rows = []

        def collect(parent):
            for child in parent.children:
                if isinstance(child, _Node):
                    if child.tag == "tr":
                        rows.append(child)
                    elif child.tag in ("thead", "tbody", "tfoot"):
                        collect(child)

        collect(node)
        cells = [[cell for cell in row.children if isinstance(cell, _Node) and cell.tag in ("td", "th")] for row in rows]
        width = max((len(row) for row in cells), default=0)
        if not width:
            return
        table = doc.add_table(rows=len(cells), cols=width)
        # Compact tables sit at the left margin, as in the PDF.
        table.alignment = WD_TABLE_ALIGNMENT.LEFT if "compact" in node.classes else WD_TABLE_ALIGNMENT.CENTER
        # Word lays the columns out from their content (as the browser does for the PDF) within
        # the full text width: long values get the room, words never break inside.
        table.autofit = True
        properties = table._tbl.tblPr
        for old in properties.findall(qn("w:tblW")):
            properties.remove(old)
        table_width = OxmlElement("w:tblW")
        # Compact tables (fit statistics, mole fractions) take their content width, as in the PDF.
        compact = "compact" in node.classes
        table_width.set(qn("w:w"), "0" if compact else "5000")
        table_width.set(qn("w:type"), "auto" if compact else "pct")
        style_element = properties.find(qn("w:tblStyle"))  # schema order: tblStyle, then tblW
        properties.insert(0 if style_element is None else list(properties).index(style_element) + 1, table_width)
        for cell_xml in table._tbl.iter(qn("w:tcW")):  # no equal preset widths for autofit
            cell_xml.getparent().remove(cell_xml)

        def rules(parent, tag, sides):  # the PDF's three rules: heavy top and bottom, thin under the header
            borders = OxmlElement(tag)
            for side, size in sides:
                line = OxmlElement(f"w:{side}")
                line.set(qn("w:val"), "single" if size else "nil")
                line.set(qn("w:sz"), str(size))
                line.set(qn("w:space"), "0")
                line.set(qn("w:color"), "000000")
                borders.append(line)
            parent.append(borders)
            return borders

        later = [properties.find(qn(f"w:{name}")) for name in ("shd", "tblLayout", "tblCellMar", "tblLook")]
        borders = rules(properties, "w:tblBorders", [
            ("top", 12), ("left", 0), ("bottom", 12), ("right", 0), ("insideH", 0), ("insideV", 0)])
        anchor = next((element for element in later if element is not None), None)
        if anchor is not None:  # schema order inside tblPr
            anchor.addprevious(borders)
        header_rows = sum(1 for row in cells if row and all(cell.tag == "th" for cell in row))
        if header_rows:
            for cell_xml in table.rows[header_rows - 1]._tr.iter(qn("w:tc")):
                rules(cell_xml.get_or_add_tcPr(), "w:tcBorders", [("bottom", 6)])
        for r, row in enumerate(cells):
            for c, cell in enumerate(row):
                target = table.cell(r, c)
                paragraph = target.paragraphs[0]
                paragraph.paragraph_format.space_before = Pt(1)
                paragraph.paragraph_format.space_after = Pt(1)
                if "c" in cell.classes:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                lines = [child for child in cell.children if isinstance(child, _Node) and child.tag == "span"] \
                    if "lp" in cell.classes else None
                if lines:  # lattice cells: one parameter per line
                    for index, line in enumerate(lines):
                        if index:
                            paragraph.add_run().add_break()
                        add_inline(paragraph, line, {"size": 9, "nobreak": True})
                else:
                    add_inline(paragraph, cell, {"size": 9, "bold": cell.tag == "th"})
                tidy(paragraph)
        doc.add_paragraph()

    def add_list(node, ordered):
        for item in node.children:
            if isinstance(item, _Node) and item.tag == "li":
                paragraph = doc.add_paragraph(style="List Number" if ordered else "List Bullet")
                add_inline(paragraph, item)
                tidy(paragraph)

    def add_figure(node):
        if figure_png:
            doc.add_picture(io.BytesIO(figure_png), width=usable_width)
        caption = next((child for child in node.children if isinstance(child, _Node) and child.tag == "figcaption"), None)
        if caption is not None:
            paragraph = doc.add_paragraph(style="Caption")
            add_inline(paragraph, caption)
            tidy(paragraph)

    headings = {"h1": 0, "h2": 1, "h3": 2, "h4": 3}

    def walk(parent):
        for node in parent.children:
            if isinstance(node, str):
                if node.strip():
                    paragraph = doc.add_paragraph()
                    add_inline(paragraph, node)
                    tidy(paragraph)
                continue
            if node.tag in headings:
                heading = doc.add_heading(level=headings[node.tag])
                add_inline(heading, node)
                tidy(heading)
            elif node.tag == "p":
                paragraph = doc.add_paragraph()
                add_inline(paragraph, node, {"size": 9} if node.classes & {"note", "byline"} else None)
                tidy(paragraph)
            elif node.tag in ("ul", "ol"):
                add_list(node, node.tag == "ol")
            elif node.tag == "table":
                add_table(node)
            elif node.tag == "figure":
                add_figure(node)
            elif node.tag == "svg":
                if figure_png:
                    doc.add_picture(io.BytesIO(figure_png), width=usable_width)
            else:
                walk(node)

    walk(body)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
