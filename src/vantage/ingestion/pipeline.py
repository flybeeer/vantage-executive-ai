"""Ingestion: manifest → provenance row → (chunks | SQL table).

One pass over the manifest. Every file gets a `documents` row carrying its hash,
version, workspace tags and data date; then it goes down one of two paths:

- **document** — load per page, chunk within the page, segment for Thai keyword
  search, embed, store in pgvector.
- **tabular** — load into the SQLite knowledge base as a typed table. No chunks:
  numbers are answered by SQL.

Re-ingesting is idempotent per `(source, version)`. After all files are in,
`_mark_current` decides which version of each source is the one to answer from —
the demo must answer from the revised plan while still knowing v1 exists.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from vantage.config import Settings, get_settings
from vantage.db.session import connect, init_schema, to_vector_literal
from vantage.ingestion import loaders, tabular
from vantage.ingestion.chunking import Chunk, chunk_page
from vantage.ingestion.provenance import FileSpec, load_manifest, sha256_file
from vantage.ingestion.thai import segment
from vantage.llm.gateway import embed_texts

logger = logging.getLogger(__name__)


def _version_key(version: str) -> tuple[int, str]:
    """Sort key for versions: `v2` beats `v1`, unknown forms sort last by text."""
    digits = "".join(ch for ch in version if ch.isdigit())
    return (int(digits) if digits else -1, version)


def _upsert_document(conn: psycopg.Connection, spec: FileSpec, pages: int | None) -> int:
    """Insert or refresh the provenance row for one file version. Returns its id."""
    row = conn.execute(
        """
        INSERT INTO documents
            (source, doc_version, sha256, workspaces, data_date, page_count, classification,
             sql_table)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, doc_version) DO UPDATE SET
            sha256         = EXCLUDED.sha256,
            workspaces     = EXCLUDED.workspaces,
            data_date      = EXCLUDED.data_date,
            page_count     = EXCLUDED.page_count,
            classification = EXCLUDED.classification,
            sql_table      = EXCLUDED.sql_table,
            upload_date    = now()
        RETURNING id
        """,
        (
            spec.source,
            spec.doc_version,
            sha256_file(spec.path),
            list(spec.workspaces),
            spec.data_date,
            pages,
            spec.classification,
            spec.table if spec.kind == "tabular" else None,
        ),
    ).fetchone()
    return row[0]


def _store_chunks(conn: psycopg.Connection, document_id: int, chunks: list[Chunk]) -> int:
    """Embed and store a document's chunks, replacing any previous ones."""
    if not chunks:
        logger.warning("document %d produced no chunks", document_id)
        return 0

    embeddings = embed_texts([c.content for c in chunks])
    conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (document_id, chunk_index, page, row_ref, content, content_tokens, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            """,
            [
                (
                    document_id,
                    i,
                    c.page,
                    c.row_ref,
                    c.content,
                    segment(c.content),
                    to_vector_literal(emb),
                )
                for i, (c, emb) in enumerate(zip(chunks, embeddings, strict=True))
            ],
        )
    return len(chunks)


def _mark_current(conn: psycopg.Connection) -> None:
    """Flag the newest version of every source as the one to answer from.

    Superseded versions stay in the corpus and stay retrievable — the demo has
    to be able to say "v1 said 1,800; the current plan says 2,000" — but
    `is_current` is what lets an answer prefer the live figure.
    """
    rows = conn.execute("SELECT id, source, doc_version FROM documents").fetchall()
    by_source: dict[str, list[tuple[int, str]]] = {}
    for doc_id, source, version in rows:
        by_source.setdefault(source, []).append((doc_id, version))

    current_ids = [
        max(versions, key=lambda item: _version_key(item[1]))[0] for versions in by_source.values()
    ]
    conn.execute("UPDATE documents SET is_current = (id = ANY(%s))", (current_ids,))


def ingest(manifest_path: Path, data_root: Path, settings: Settings | None = None) -> dict:
    """Ingest every file in the manifest. Returns a per-file summary."""
    settings = settings or get_settings()
    specs = load_manifest(manifest_path, data_root)
    init_schema()

    sqlite_path = Path(settings.sql_kb_path)
    summary: list[dict] = []

    with connect() as conn:
        for spec in specs:
            pages = loaders.page_count(spec.path) if spec.kind == "document" else None
            document_id = _upsert_document(conn, spec, pages)

            if spec.kind == "tabular":
                stats = tabular.load_into_sqlite(spec.path, sqlite_path, spec.table)
                # A tabular file has no chunks; clear any left from a previous
                # run that classified it differently.
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
                summary.append(
                    {
                        "file": spec.path.name,
                        "source": spec.source,
                        "version": spec.doc_version,
                        "kind": "tabular",
                        "table": spec.table,
                        "rows": stats["rows"],
                        "chunks": 0,
                        "workspaces": list(spec.workspaces),
                        "data_date": str(spec.data_date),
                    }
                )
                continue

            chunks: list[Chunk] = []
            for page_no, text in loaders.load_pages(spec.path):
                chunks.extend(chunk_page(text, settings, page=page_no))
            n = _store_chunks(conn, document_id, chunks)
            summary.append(
                {
                    "file": spec.path.name,
                    "source": spec.source,
                    "version": spec.doc_version,
                    "kind": "document",
                    "pages": pages,
                    "chunks": n,
                    "workspaces": list(spec.workspaces),
                    "data_date": str(spec.data_date),
                }
            )
            logger.info("ingested %s (%d chunks, %s pages)", spec.label, n, pages)

        _mark_current(conn)

    return {
        "files": len(summary),
        "chunks": sum(item["chunks"] for item in summary),
        "detail": summary,
    }
