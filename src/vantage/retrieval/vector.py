"""Vector leg: cosine similarity over pgvector, filtered by access in the query.

A note on the HNSW index and a `WHERE` clause: pgvector applies the filter to
the candidates the index scan produces (`hnsw.ef_search`, default 40), so on a
large corpus a narrowly-scoped user can get fewer than `top_k` results back. That
fails safe — fewer results, never someone else's — and at six documents it does
not arise. On real volume, raise `ef_search` or move to pgvector ≥ 0.8 iterative
scans.
"""

from __future__ import annotations

from vantage.db.session import connect, to_vector_literal
from vantage.llm.gateway import embed_query
from vantage.policy import AccessFilter
from vantage.retrieval.access import predicate
from vantage.retrieval.types import CHUNK_COLUMNS, RetrievedChunk, from_row


def search(query: str, access: AccessFilter, top_k: int) -> list[RetrievedChunk]:
    """Return the `top_k` chunks most similar to `query` that `access` permits."""
    if access.is_empty:
        return []
    where, params = predicate(access)
    params |= {"qvec": to_vector_literal(embed_query(query)), "top_k": top_k}

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {CHUNK_COLUMNS},
                   1 - (c.embedding <=> %(qvec)s::vector) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where}
            ORDER BY c.embedding <=> %(qvec)s::vector
            LIMIT %(top_k)s
            """,
            params,
        ).fetchall()

    return [from_row(row) for row in rows]
