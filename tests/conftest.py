"""Shared fixtures for the database-backed tests.

`pg` starts a throwaway Postgres with pgvector (via `pgserver`, an embedded
build — no Docker needed) and points the settings at it. `corpus` ingests the
real synthetic corpus into it.

Embeddings come from a deterministic stand-in rather than bge-m3, so these tests
run without Ollama. That makes them tests of the *plumbing* — access filtering,
provenance, audit — not of retrieval quality: the stand-in is a hashed bag of
Thai tokens, i.e. a second keyword leg. Retrieval quality is measured by the
eval harness against the real model.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    """Hashed bag-of-tokens embedding, unit length, at the configured dimension."""
    from vantage.config import get_settings
    from vantage.ingestion.thai import tokens

    dim = get_settings().embedding_dim
    out = []
    for text in texts:
        vec = [0.0] * dim
        vec[0] = 1e-3  # never the zero vector: cosine distance to it is NaN
        for tok in tokens(text):
            vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        out.append([x / norm for x in vec])
    return out


@pytest.fixture(scope="session")
def pg(tmp_path_factory):
    """A running Postgres + pgvector, with POSTGRES_* pointed at it."""
    pgserver = pytest.importorskip("pgserver", reason="pip install pgserver for DB tests")
    from vantage.config import get_settings

    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    uri = urlparse(server.get_uri())
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("POSTGRES_HOST", uri.hostname or "127.0.0.1")
        mp.setenv("POSTGRES_PORT", str(uri.port))
        mp.setenv("POSTGRES_USER", uri.username or "postgres")
        mp.setenv("POSTGRES_PASSWORD", uri.password or "")
        mp.setenv("POSTGRES_DB", uri.path.lstrip("/") or "postgres")
        mp.setenv("SQL_KB_PATH", str(tmp_path_factory.mktemp("kb") / "kb.sqlite"))
        get_settings.cache_clear()
        yield server
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def corpus(pg):
    """The synthetic corpus, ingested. Returns the ingest summary."""
    from vantage.ingestion.pipeline import ingest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("vantage.ingestion.pipeline.embed_texts", fake_embed_texts)
        mp.setattr("vantage.retrieval.vector.embed_query", lambda q: fake_embed_texts([q])[0])
        summary = ingest(ROOT / "data" / "manifest.yaml", ROOT / "data" / "raw")
        yield summary
