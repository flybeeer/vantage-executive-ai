"""Postgres connections + schema management.

Three tables carry the whole demo:

- `documents` — one row per *file version*. Holds the provenance a citation needs
  (sha256, version, upload date, data date) and the asset metadata the policy
  reads (`workspaces`). This is the single source of truth for "who may see
  this", so retrieval filters by joining to it rather than trusting a
  denormalised copy.
- `chunks` — retrievable text, pointing at its document. Carries `page` /
  `row_ref` so a citation can say *which page* or *which row*, not just which
  file.
- `audit_log` — append-only record of every question asked, including the ones
  that were denied or refused.

`init_schema()` is idempotent, so it is safe to call on every ingest.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from vantage.config import get_settings


def to_vector_literal(vec: list[float]) -> str:
    """Format an embedding as a pgvector text literal, e.g. "[0.1,0.2]".

    Bound as a `%s::vector` parameter so Postgres parses it as a `vector`; a
    plain Python list would be sent as `double precision[]`, which the `<=>`
    operator rejects.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a pgvector-aware connection, committing on success."""
    settings = get_settings()
    with psycopg.connect(settings.dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        yield conn


def init_schema() -> None:
    """Create the tables, indexes and audit-log guard if they don't exist."""
    settings = get_settings()
    dim = settings.embedding_dim
    with connect() as conn:
        # ---------- documents: provenance + access metadata ----------
        # A file and a version together identify a document, so strategy_plan v1
        # and v2 coexist (the demo needs to answer from v2 while reporting that
        # v1 said something different).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id             BIGSERIAL   PRIMARY KEY,
                source         TEXT        NOT NULL,
                doc_version    TEXT        NOT NULL,
                sha256         CHAR(64)    NOT NULL,
                workspaces     TEXT[]      NOT NULL,
                data_date      DATE,
                upload_date    TIMESTAMPTZ NOT NULL DEFAULT now(),
                page_count     INTEGER,
                classification TEXT        NOT NULL DEFAULT 'internal',
                -- The newest version of a source. Maintained by the ingest
                -- pipeline (not a generated column: "newest" is a decision
                -- about version ordering, not a fact about the row).
                is_current     BOOLEAN     NOT NULL DEFAULT TRUE,
                UNIQUE (source, doc_version)
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS documents_workspaces_gin
            ON documents USING gin (workspaces)
            """)

        # ---------- chunks: retrievable text ----------
        # content_tokens holds PyThaiNLP-segmented text (space-joined). Postgres
        # FTS cannot segment Thai — the 'simple' config would make one chunk a
        # single enormous token and the keyword leg of hybrid retrieval would
        # silently contribute nothing. We index the segmented copy instead.
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id             BIGSERIAL PRIMARY KEY,
                document_id    BIGINT    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index    INTEGER   NOT NULL,
                page           INTEGER,
                row_ref        TEXT,
                content        TEXT      NOT NULL,
                content_tokens TEXT      NOT NULL,
                embedding      vector({dim}) NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS chunks_document_id
            ON chunks (document_id)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
            ON chunks USING hnsw (embedding vector_cosine_ops)
            """)
        conn.execute("""
            ALTER TABLE chunks
            ADD COLUMN IF NOT EXISTS tokens_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('simple', content_tokens)) STORED
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS chunks_tokens_tsv_gin
            ON chunks USING gin (tokens_tsv)
            """)

        # ---------- audit_log: append-only ----------
        # Every question lands here, including denied and refused ones — the
        # denials are the point, so they cannot be the rows that go missing.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id              BIGSERIAL   PRIMARY KEY,
                ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
                principal       TEXT        NOT NULL,
                workspace       TEXT        NOT NULL,
                question        TEXT        NOT NULL,
                -- allowed | denied | refused  (see policy + guardrails)
                decision        TEXT        NOT NULL,
                decision_reason TEXT,
                route           TEXT,
                model           TEXT,
                -- [{chunk_id, source, doc_version, page, row_ref, score}, ...]
                retrieved       JSONB       NOT NULL DEFAULT '[]'::jsonb,
                sql_executed    TEXT,
                answer          TEXT,
                input_tokens    INTEGER     NOT NULL DEFAULT 0,
                output_tokens   INTEGER     NOT NULL DEFAULT 0,
                cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0,
                latency_ms      INTEGER
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log (ts DESC)
            """)
        # Enforce append-only in the database, not by convention. A trigger (not
        # just a REVOKE) so the guarantee holds even when the app connects as the
        # owning role, which it does in this demo.
        conn.execute("""
            CREATE OR REPLACE FUNCTION audit_log_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_log is append-only (attempted %)', TG_OP;
            END;
            $$ LANGUAGE plpgsql
            """)
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
        conn.execute("""
            CREATE TRIGGER audit_log_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()
            """)
