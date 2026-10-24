"""Reranking stage: re-score fused candidates and keep the best `top_k`.

Two backends, selected by RERANKER_TYPE:

- `none`  — keep the fused order, truncate. The default; the plan cuts the
  reranker first if the schedule slips.
- `local` — a local cross-encoder (bge-reranker-v2-m3, multilingual) via
  sentence-transformers. Its scores are comparable across queries, which is
  what `MIN_RELEVANCE_SCORE` relies on.

Carried over from Sourcerer minus the Cohere and LLM backends: Cohere would send
document text off-box, and an LLM reranker is a second generation call per
question for a corpus of six files.

Reranking only reorders what the access filter already admitted — it never sees
a chunk the principal cannot read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from vantage.config import Settings
from vantage.retrieval.types import RetrievedChunk


class Reranker(Protocol):
    def rerank(
        self, query: str, chunks: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker:
    """Keep the incoming (fused) order; just truncate."""

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
        return chunks[:top_k]


class LocalReranker:
    """Local cross-encoder reranker (bge-reranker via sentence-transformers)."""

    def __init__(self, model: str) -> None:
        # Config stores a short name; map it to the HF repo id.
        self.model_name = model if "/" in model else f"BAAI/{model}"
        self._model = None  # loaded lazily on first use

    def _ensure_model(self):
        if self._model is None:
            try:
                import torch
                from sentence_transformers import CrossEncoder
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError(
                    'RERANKER_TYPE=local needs sentence-transformers: pip install -e ".[rerank]"'
                ) from exc
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = CrossEncoder(self.model_name, device=device)
        return self._model

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
        if not chunks:
            return []
        scores = self._ensure_model().predict([(query, c.content) for c in chunks])
        rescored = [replace(c, score=float(s)) for c, s in zip(chunks, scores, strict=True)]
        rescored.sort(key=lambda c: c.score, reverse=True)
        return rescored[:top_k]


# Cached so the cross-encoder loads once per process, not once per question.
_CACHE: dict[tuple[str, str], Reranker] = {}


def get_reranker(settings: Settings) -> Reranker:
    """Return the configured reranker."""
    kind = settings.reranker_type.lower()
    key = (kind, settings.reranker_model)
    if key not in _CACHE:
        if kind == "none":
            _CACHE[key] = NoopReranker()
        elif kind == "local":
            _CACHE[key] = LocalReranker(settings.reranker_model)
        else:
            raise ValueError(f"unknown RERANKER_TYPE {settings.reranker_type!r} (none | local)")
    return _CACHE[key]
