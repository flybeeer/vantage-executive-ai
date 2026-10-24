"""Chunking, measured in Thai tokens rather than characters or whitespace words.

Splitting Thai on whitespace would produce one chunk per *paragraph* at best and
one chunk per document at worst, so chunk boundaries are counted in PyThaiNLP
tokens. Chunks never cross a page boundary: a chunk that spanned pages could not
honestly cite a page number, and page-accurate citation is the point of the demo.
"""

from __future__ import annotations

from dataclasses import dataclass

from vantage.config import Settings
from vantage.ingestion.thai import tokens


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit of text, with the provenance it was found at."""

    content: str
    page: int | None = None
    row_ref: str | None = None


def _split_tokens(text: str, size: int, overlap: int) -> list[str]:
    """Split `text` into overlapping windows of `size` tokens."""
    toks = tokens(text)
    if not toks:
        return []
    if len(toks) <= size:
        return [text]

    # Reconstruct windows from the original text by locating token boundaries,
    # so the stored content keeps its original spacing and punctuation instead
    # of the segmented form.
    spans: list[tuple[int, int]] = []
    cursor = 0
    for tok in toks:
        found = text.find(tok, cursor) if tok else -1
        if found == -1:
            # Token was lower-cased or normalised away from the raw text; fall
            # back to advancing by its length so offsets stay roughly aligned.
            spans.append((cursor, cursor + len(tok)))
            cursor += len(tok)
        else:
            spans.append((found, found + len(tok)))
            cursor = found + len(tok)

    step = max(1, size - overlap)
    out: list[str] = []
    for start in range(0, len(toks), step):
        window = spans[start : start + size]
        if not window:
            break
        piece = text[window[0][0] : window[-1][1]].strip()
        if piece:
            out.append(piece)
        if start + size >= len(toks):
            break
    return out


def chunk_page(text: str, settings: Settings, *, page: int | None = None) -> list[Chunk]:
    """Chunk one page (or one whole text) into `Chunk`s carrying that page."""
    pieces = _split_tokens(text.strip(), settings.chunk_size, settings.chunk_overlap)
    return [Chunk(content=piece, page=page) for piece in pieces]
