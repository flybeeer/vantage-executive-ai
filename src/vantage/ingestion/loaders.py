"""Document loaders that preserve the page a piece of text came from.

Most RAG loaders concatenate a PDF's pages into one string. That throws away the
page number, which is exactly what a reader needs in order to go and check the
claim — so here every loader yields `(page_number, text)` and the page travels
with the chunk all the way into the citation.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pypdf import PdfReader

# Text formats that become retrievable chunks. Spreadsheets are deliberately not
# here: tabular files go to the SQL knowledge base (see ingestion.tabular) so
# numbers are computed by SQL, never read out of prose by the model.
SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
TABULAR_SUFFIXES = {".csv", ".xlsx"}


def load_pdf_pages(path: Path) -> Iterator[tuple[int, str]]:
    """Yield `(page_number, text)` per page, 1-based, skipping empty pages."""
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            yield i, text


def load_text(path: Path) -> Iterator[tuple[int, str]]:
    """Yield a plain-text/Markdown file as a single unpaged unit."""
    text = path.read_text(encoding="utf-8").strip()
    if text:
        yield 1, text


def page_count(path: Path) -> int | None:
    """Total pages in a PDF, or None for formats without pages."""
    if path.suffix.lower() == ".pdf":
        return len(PdfReader(str(path)).pages)
    return None


def load_pages(path: Path) -> Iterator[tuple[int, str]]:
    """Load a document as `(page_number, text)` units, dispatching on suffix."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        yield from load_pdf_pages(path)
    elif suffix in {".md", ".markdown", ".txt"}:
        yield from load_text(path)
    else:
        raise ValueError(f"Unsupported document type: {path.suffix} ({path})")
