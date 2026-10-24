"""Shared retrieval types.

`RetrievedChunk` is produced by every retrieval leg and carries the full
provenance a citation needs — file, version, page or row, data date — so nothing
downstream has to go back to the database to say where a fact came from.

`score` is always "higher is better", but its meaning depends on the producer
(cosine similarity, ts_rank_cd, RRF, or a cross-encoder). The per-leg scores are
kept separately because RRF is rank-only: it says which chunk is *best*, never
whether the best chunk is any *good*, and the refusal decision needs the latter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Selected by both legs, in this order, and turned into a chunk by `from_row`.
# Joining `documents` is what makes the access predicate possible at all: the
# workspace tags live on the document, not denormalised onto every chunk.
CHUNK_COLUMNS = """
    c.id, c.document_id, d.source, d.doc_version, c.chunk_index, c.page, c.row_ref,
    d.data_date, d.is_current, c.content
"""


@dataclass(frozen=True)
class RetrievedChunk:
    id: int
    document_id: int
    source: str
    doc_version: str
    chunk_index: int
    page: int | None
    row_ref: str | None
    data_date: date | None
    is_current: bool
    content: str
    score: float  # higher = more relevant (semantics depend on the producer)
    vector_score: float | None = None  # cosine similarity, if the vector leg found it
    keyword_score: float | None = None  # ts_rank_cd, if the keyword leg found it

    @property
    def citation(self) -> str:
        """`board_meeting_2026Q2.pdf · v1 · หน้า 1 · ข้อมูล ณ 2026-06-30`."""
        parts = [self.source, self.doc_version]
        if self.page is not None:
            parts.append(f"หน้า {self.page}")
        if self.row_ref:
            parts.append(f"แถว {self.row_ref}")
        parts.append(
            f"ข้อมูล ณ {self.data_date.isoformat()}" if self.data_date else "ไม่ระบุวันที่ข้อมูล"
        )
        if not self.is_current:
            parts.append("ฉบับเก่า")
        return " · ".join(parts)

    def audit_ref(self) -> dict:
        """What the audit log keeps about this chunk.

        Chunk ids are regenerated on re-ingest, so the row also records
        `(source, doc_version, chunk_index)` — the id alone would dangle after
        the next `make ingest`.
        """
        return {
            "chunk_id": self.id,
            "source": self.source,
            "doc_version": self.doc_version,
            "chunk_index": self.chunk_index,
            "page": self.page,
            "row_ref": self.row_ref,
            "score": round(self.score, 6),
        }


def from_row(row: tuple, *, vector_score=None, keyword_score=None) -> RetrievedChunk:
    """Build a chunk from a `CHUNK_COLUMNS` row followed by one score column."""
    return RetrievedChunk(
        id=row[0],
        document_id=row[1],
        source=row[2],
        doc_version=row[3],
        chunk_index=row[4],
        page=row[5],
        row_ref=row[6],
        data_date=row[7],
        is_current=row[8],
        content=row[9],
        score=float(row[10]),
        vector_score=vector_score,
        keyword_score=keyword_score,
    )
