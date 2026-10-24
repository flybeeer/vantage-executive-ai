"""The audit log writer — one row per question, whatever happened to it.

The table is append-only in the database (a trigger rejects UPDATE and DELETE;
see `db.session.init_schema`), so this module only ever inserts and reads.

Writes are not best-effort. If the row cannot be written the question fails: a
system whose pitch is "every question is logged" cannot answer one it did not
log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from psycopg.types.json import Jsonb

from vantage.db.session import connect

# `decision` values.
ALLOWED = "allowed"  # answered (or retrieval ran) within the principal's access
DENIED = "denied"  # the policy refused — unknown user, or a forbidden asset
REFUSED = "refused"  # access was fine but the evidence was not there
ERROR = "error"  # something failed after the question was accepted


@dataclass
class AuditEntry:
    principal: str
    workspace: str
    question: str
    decision: str
    decision_reason: str | None = None
    route: str | None = None
    model: str | None = None
    retrieved: list[dict] = field(default_factory=list)
    sql_executed: str | None = None
    answer: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None


_COLUMNS = tuple(AuditEntry.__dataclass_fields__)


def record(entry: AuditEntry) -> int:
    """Append `entry` and return its id. Raises if the write fails."""
    values = asdict(entry)
    values["retrieved"] = Jsonb(values["retrieved"])
    with connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO audit_log ({", ".join(_COLUMNS)})
            VALUES ({", ".join(f"%({c})s" for c in _COLUMNS)})
            RETURNING id
            """,
            values,
        ).fetchone()
    return row[0]


def recent(limit: int = 50, principal: str | None = None) -> list[dict]:
    """The newest audit rows, optionally for one principal — for the audit page."""
    with connect() as conn:
        cur = conn.execute(
            f"""
            SELECT id, ts, {", ".join(_COLUMNS)}
            FROM audit_log
            WHERE %(principal)s::text IS NULL OR principal = %(principal)s::text
            ORDER BY id DESC
            LIMIT %(limit)s
            """,
            {"principal": principal, "limit": limit},
        )
        names = [d.name for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
