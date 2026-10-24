"""Keyword leg: Postgres full-text search over PyThaiNLP-segmented text.

Matches `chunks.tokens_tsv`, which is built from the segmented copy of each
chunk (see `ingestion.thai`). The question is segmented the same way, so both
sides of `@@` see the same token stream. Without that, Thai text is one token
per chunk and this leg matches nothing — silently.

Ranking is `ts_rank_cd`, which is not literally BM25 but gives the keyword
ordering RRF needs; only the order matters to fusion. Terms are OR-ed rather
than AND-ed (plainto_tsquery's default) so a chunk matching *any* term is a
candidate, ranked by how well it matches.
"""

from __future__ import annotations

from vantage.db.session import connect
from vantage.ingestion.thai import tsquery_terms
from vantage.policy import AccessFilter
from vantage.retrieval.access import predicate
from vantage.retrieval.types import CHUNK_COLUMNS, RetrievedChunk, from_row

# plainto_tsquery renders as "'a' & 'b' & 'c'"; turn the ANDs into ORs.
_OR_QUERY = "replace(plainto_tsquery('simple', %(terms)s)::text, ' & ', ' | ')::tsquery"


def search(query: str, access: AccessFilter, top_k: int) -> list[RetrievedChunk]:
    """Return the `top_k` chunks matching any query term that `access` permits."""
    if access.is_empty:
        return []
    terms = tsquery_terms(query)
    if not terms:
        return []
    where, params = predicate(access)
    params |= {"terms": terms, "top_k": top_k}

    with connect() as conn:
        rows = conn.execute(
            f"""
            WITH q AS (SELECT {_OR_QUERY} AS query)
            SELECT {CHUNK_COLUMNS},
                   ts_rank_cd(c.tokens_tsv, q.query) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            CROSS JOIN q
            WHERE c.tokens_tsv @@ q.query
              AND {where}
            ORDER BY score DESC, c.id
            LIMIT %(top_k)s
            """,
            params,
        ).fetchall()

    return [from_row(row) for row in rows]
