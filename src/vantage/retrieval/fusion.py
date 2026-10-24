"""Reciprocal Rank Fusion (RRF).

Combines the ranked lists from each leg into one. A chunk's fused score is the
sum over the lists of 1 / (k + rank), rank 1-based within a list. Only ranks
matter, so legs with incomparable scales (cosine vs ts_rank_cd) fuse cleanly.

Carried over from Sourcerer, with one change: the per-leg scores survive fusion
on `vector_score` / `keyword_score`. RRF alone cannot tell "the best of several
good matches" from "the best of nothing relevant" — the top chunk of any
non-empty list scores 1/(k+1) — so the refusal decision needs the raw scores.

Reference: Cormack et al., "Reciprocal Rank Fusion outperforms Condorcet..."
"""

from __future__ import annotations

from dataclasses import replace

from vantage.retrieval.types import RetrievedChunk

VECTOR = "vector"
KEYWORD = "keyword"
_SCORE_FIELD = {VECTOR: "vector_score", KEYWORD: "keyword_score"}


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[RetrievedChunk]], k: int
) -> list[RetrievedChunk]:
    """Fuse `{leg_name: ranked_list}` into one list ordered by descending RRF score."""
    scores: dict[int, float] = {}
    merged: dict[int, RetrievedChunk] = {}

    for leg, ranked in ranked_lists.items():
        field = _SCORE_FIELD.get(leg)
        for rank, chunk in enumerate(ranked, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            base = merged.get(chunk.id, chunk)
            merged[chunk.id] = replace(base, **{field: chunk.score}) if field else base

    ordered_ids = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return [replace(merged[cid], score=scores[cid]) for cid in ordered_ids]
