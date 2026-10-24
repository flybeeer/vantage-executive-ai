"""A small Thai-aware PDF writer, used only to generate the synthetic corpus.

Why hand-rolled instead of ReportLab's Platypus flowables: Thai does not put
spaces between words, so any layout engine that breaks lines on whitespace sees
a paragraph of Thai as one enormous word and lets it run off the page. Here the
line breaking is done explicitly — PyThaiNLP segments the text, and words are
packed onto lines measured with the real font metrics.

Keeping it to `canvas` also keeps the extracted text predictable, which matters:
`scripts/gen_dataset.py` reads every PDF back with pypdf and fails if the Thai
does not survive the round trip. A corpus that cannot be re-extracted would make
the whole retrieval demo quietly wrong.
"""

from __future__ import annotations

import os
from pathlib import Path

from pythainlp.tokenize import word_tokenize
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# Thai-capable TTF pairs to look for, in preference order. Override with
# VANTAGE_THAI_FONT / VANTAGE_THAI_FONT_BOLD to point at your own.
_FONT_CANDIDATES = [
    # Windows
    (r"C:\Windows\Fonts\leelawad.ttf", r"C:\Windows\Fonts\leelawdb.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    # Linux (Debian/Ubuntu: fonts-thai-tlwg)
    (
        "/usr/share/fonts/truetype/tlwg/Sarabun.ttf",
        "/usr/share/fonts/truetype/tlwg/Sarabun-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/tlwg/Loma.ttf",
        "/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf",
    ),
    # macOS
    ("/Library/Fonts/Thonburi.ttc", "/Library/Fonts/Thonburi.ttc"),
]

REGULAR = "ThaiSerif"
BOLD = "ThaiSerif-Bold"

PAGE_W, PAGE_H = A4
MARGIN = 56.0
LEADING = 19.0

# Cell separator for tables. Part of the extracted text on purpose — see
# ThaiPdf.table.
SEP = "  |  "


class FontNotFoundError(RuntimeError):
    """No Thai-capable font was found on this machine."""


def _resolve_fonts() -> tuple[str, str]:
    """Find a (regular, bold) Thai TTF pair, or explain how to supply one."""
    override = os.environ.get("VANTAGE_THAI_FONT")
    if override:
        bold = os.environ.get("VANTAGE_THAI_FONT_BOLD", override)
        if not Path(override).is_file():
            raise FontNotFoundError(f"VANTAGE_THAI_FONT does not exist: {override}")
        return override, bold if Path(bold).is_file() else override

    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file():
            return regular, bold if Path(bold).is_file() else regular

    raise FontNotFoundError(
        "No Thai font found. Install one (Debian/Ubuntu: `apt-get install "
        "fonts-thai-tlwg`) or set VANTAGE_THAI_FONT to a Thai .ttf path. "
        "The generated corpus is committed to the repo, so this is only needed "
        "to regenerate it."
    )


_registered = False


def register_fonts() -> None:
    """Register the Thai fonts with ReportLab once per process."""
    global _registered
    if _registered:
        return
    regular, bold = _resolve_fonts()
    pdfmetrics.registerFont(TTFont(REGULAR, regular))
    pdfmetrics.registerFont(TTFont(BOLD, bold))
    _registered = True


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Break Thai (or mixed) text into lines that fit `width`.

    Words come from PyThaiNLP rather than `str.split`, which is the whole point:
    whitespace splitting cannot find a break point in Thai.
    """
    words = word_tokenize(text, engine="newmm", keep_whitespace=True)
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = current + word
        if pdfmetrics.stringWidth(candidate, font, size) <= width or not current:
            current = candidate
        else:
            lines.append(current.rstrip())
            current = word.lstrip() if word.strip() == "" else word
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


class ThaiPdf:
    """Flowing single-column document with automatic page breaks."""

    def __init__(self, path: Path, title: str, subtitle: str = "") -> None:
        register_fonts()
        self.path = path
        self.title = title
        path.parent.mkdir(parents=True, exist_ok=True)
        self.c = canvas.Canvas(str(path), pagesize=A4)
        self.c.setTitle(title)
        self.y = PAGE_H - MARGIN
        self._page = 1
        self._header(title, subtitle)

    # ---------- internals ----------

    @property
    def _text_width(self) -> float:
        return PAGE_W - 2 * MARGIN

    def _space(self, needed: float) -> None:
        """Start a new page when `needed` points will not fit."""
        if self.y - needed < MARGIN + 24:
            self._footer()
            self.c.showPage()
            self._page += 1
            self.y = PAGE_H - MARGIN

    def _footer(self) -> None:
        self.c.setFont(REGULAR, 8.5)
        self.c.drawRightString(PAGE_W - MARGIN, MARGIN - 18, f"หน้า {self._page}")
        self.c.drawString(MARGIN, MARGIN - 18, "เอกสารสังเคราะห์เพื่อการสาธิต · ข้อมูลสมมติ")

    def _header(self, title: str, subtitle: str) -> None:
        self.c.setFont(BOLD, 17)
        for line in _wrap(title, BOLD, 17, self._text_width):
            self.c.drawString(MARGIN, self.y, line)
            self.y -= 23
        if subtitle:
            self.c.setFont(REGULAR, 10.5)
            for line in _wrap(subtitle, REGULAR, 10.5, self._text_width):
                self.c.drawString(MARGIN, self.y, line)
                self.y -= 15
        self.y -= 8
        self.c.setLineWidth(0.6)
        self.c.line(MARGIN, self.y, PAGE_W - MARGIN, self.y)
        self.y -= 22

    # ---------- public API ----------

    def heading(self, text: str, size: float = 13.0) -> None:
        self._space(size + LEADING)
        self.y -= 6
        self.c.setFont(BOLD, size)
        for line in _wrap(text, BOLD, size, self._text_width):
            self.c.drawString(MARGIN, self.y, line)
            self.y -= size + 6
        self.y -= 4

    def para(self, text: str, size: float = 11.0, indent: float = 0.0) -> None:
        self.c.setFont(REGULAR, size)
        for line in _wrap(text, REGULAR, size, self._text_width - indent):
            self._space(LEADING)
            self.c.setFont(REGULAR, size)
            self.c.drawString(MARGIN + indent, self.y, line)
            self.y -= LEADING
        self.y -= 5

    def bullet(self, text: str, marker: str = "•", size: float = 11.0) -> None:
        self._space(LEADING)
        self.c.setFont(REGULAR, size)
        self.c.drawString(MARGIN + 6, self.y, marker)
        lines = _wrap(text, REGULAR, size, self._text_width - 26)
        for i, line in enumerate(lines):
            if i:
                self._space(LEADING)
                self.c.setFont(REGULAR, size)
            self.c.drawString(MARGIN + 26, self.y, line)
            self.y -= LEADING
        self.y -= 3

    def table(self, headers: list[str], rows: list[list[str]], size: float = 10.0) -> None:
        """A plain table, drawn one row per `drawString`.

        Drawing each cell at its own x would look tidier, but pypdf then treats
        the cells as separate text runs and extracts each one onto its own line —
        which destroys the row association that makes a table worth citing
        ("ภาษาจีน 68.4%" becomes "ภาษาจีน" and "68.4%" three lines apart). One
        string per row keeps the row intact through extraction, so the cell
        separator is deliberately part of the text.
        """
        self._space(LEADING * 2)
        self.c.setFont(BOLD, size)
        self.c.drawString(MARGIN, self.y, SEP.join(headers))
        self.y -= LEADING * 0.8
        self.c.setLineWidth(0.4)
        self.c.line(MARGIN, self.y + 4, PAGE_W - MARGIN, self.y + 4)
        self.y -= 4

        for row in rows:
            self._space(LEADING)
            self.c.setFont(REGULAR, size)
            self.c.drawString(MARGIN, self.y, SEP.join(str(cell) for cell in row))
            self.y -= LEADING * 0.92
        self.y -= 8

    def new_page(self) -> None:
        self._footer()
        self.c.showPage()
        self._page += 1
        self.y = PAGE_H - MARGIN

    def save(self) -> int:
        """Write the file and return its page count."""
        self._footer()
        self.c.save()
        return self._page
