"""Hybrid retrieval, scoped to one principal.

    principal ─► allowed_filter ─┬─► vector.search (top_k_vector) ─┐
                                 └─► bm25.search   (top_k_bm25)   ─┴─► RRF ─► rerank ─► top_k_final

`retrieve()` takes a *principal*, not a filter, and computes the filter itself.
That is deliberate: there is no way to call it without an access decision, and
no way to pass a filter belonging to someone else. Both legs receive the same
filter and bind it into their own `WHERE` clause.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vantage.config import Settings, get_settings
from vantage.policy import AccessFilter, Principal, allowed_filter
from vantage.retrieval import bm25, vector
from vantage.retrieval.fusion import KEYWORD, VECTOR, reciprocal_rank_fusion
from vantage.retrieval.rerank import get_reranker
from vantage.retrieval.types import RetrievedChunk


@dataclass(frozen=True)
class RetrievalResult:
    """The chunks for one question, plus enough context to audit how they were found."""

    chunks: list[RetrievedChunk]
    access: AccessFilter
    vector_hits: int = 0
    keyword_hits: int = 0
    candidates: int = 0
    notes: list[str] = field(default_factory=list)


def retrieve(
    question: str, principal: Principal | None, settings: Settings | None = None
) -> RetrievalResult:
    """Run the hybrid pipeline for `question` over what `principal` may read."""
    settings = settings or get_settings()
    access = allowed_filter(principal)
    if access.is_empty:
        # No query runs at all: an empty filter is not "match nothing", it is
        # "do not touch the corpus".
        return RetrievalResult(chunks=[], access=access, notes=[access.reason])

    vector_hits = vector.search(question, access, settings.top_k_vector)
    keyword_hits = bm25.search(question, access, settings.top_k_bm25)
    fused = reciprocal_rank_fusion({VECTOR: vector_hits, KEYWORD: keyword_hits}, k=settings.rrf_k)
    final = get_reranker(settings).rerank(question, fused, settings.top_k_final)

    notes = []
    if not keyword_hits:
        notes.append("keyword leg matched no chunk")
    return RetrievalResult(
        chunks=final,
        access=access,
        vector_hits=len(vector_hits),
        keyword_hits=len(keyword_hits),
        candidates=len(fused),
        notes=notes,
    )
