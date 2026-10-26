"""Run a SELECT against the tabular KB with the access decision inside SQLite.

Three guards, all on the connection rather than on the SQL text:

1. **Read-only.** The file is opened with `mode=ro`; a write cannot happen even
   if every check above this one were wrong.
2. **Table authorizer.** SQLite calls `set_authorizer` for every table and
   column a statement touches *while compiling it*. Reads of tables outside the
   principal's allowed set are refused there, so a model that writes
   `FROM finance_monthly` for the Academic Director gets a compile error, not
   rows. A regex over the SQL could be fooled by aliases, CTEs or quoting; the
   compiler cannot.
3. **Wall-clock budget.** A progress handler aborts a runaway query.

The authorizer also records which tables were actually read. Those — not the
tables the model claims it used — are what the answer cites.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Actions a read-only SELECT legitimately needs. Everything else is denied.
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

# How often (in VM ops) the progress handler checks the deadline.
_PROGRESS_INTERVAL = 10_000


class QueryRejected(RuntimeError):
    """The query was refused by a guard (forbidden table, action, or budget)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    tables_read: list[str] = field(default_factory=list)


def _open_ro(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQL knowledge base not found: {path} (run `make ingest`)")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def run_select(
    sql: str, db_path: str | Path, allowed_tables: Iterable[str], timeout_s: float
) -> QueryResult:
    """Execute `sql`, reading only `allowed_tables`. Raises QueryRejected on a guard."""
    allowed = set(allowed_tables)
    read: list[str] = []
    refused: list[str] = []

    def authorizer(action, arg1, _arg2, _db, _trigger):
        if action == sqlite3.SQLITE_READ:
            if arg1 not in allowed:
                if arg1 not in refused:
                    refused.append(arg1)
                return sqlite3.SQLITE_DENY
            if arg1 not in read:
                read.append(arg1)
            return sqlite3.SQLITE_OK
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        refused.append(f"action {action}")
        return sqlite3.SQLITE_DENY

    deadline = time.monotonic() + timeout_s if timeout_s and timeout_s > 0 else None

    def progress() -> int:
        return 1 if deadline is not None and time.monotonic() > deadline else 0

    conn = _open_ro(db_path)
    try:
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(progress, _PROGRESS_INTERVAL)
        try:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description or ()]
            rows = [tuple(r) for r in cursor.fetchall()]
        except sqlite3.DatabaseError as exc:
            if refused:
                raise QueryRejected(f"not authorized to read: {', '.join(refused)}") from exc
            if deadline is not None and time.monotonic() > deadline:
                raise QueryRejected(f"query exceeded the {timeout_s}s time limit") from exc
            raise
    finally:
        conn.close()
    return QueryResult(columns=columns, rows=rows, tables_read=read)


def describe(db_path: str | Path, tables: Iterable[str], max_values: int = 30) -> str:
    """Schema text for the text-to-SQL prompt, limited to `tables`.

    Lists the exact distinct values of small TEXT columns (branch and course
    names are Thai strings; the model cannot guess them) and the range of
    `month`, so the model writes `WHERE branch = 'บางนา'` rather than a
    transliteration that matches nothing.
    """
    conn = _open_ro(db_path)
    try:
        blocks = []
        for table in tables:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if not cols:
                continue
            lines = [f"TABLE {table}"]
            for _cid, name, col_type, *_ in cols:
                note = ""
                if name == "row_ref":
                    note = "  -- spreadsheet row number of this record (for citation)"
                elif col_type == "TEXT":
                    values = [
                        r[0]
                        for r in conn.execute(
                            f'SELECT DISTINCT "{name}" FROM "{table}" ORDER BY 1 LIMIT ?',
                            (max_values + 1,),
                        )
                    ]
                    if name == "month" and values:
                        lo, hi = conn.execute(
                            f'SELECT MIN("{name}"), MAX("{name}") FROM "{table}"'
                        ).fetchone()
                        note = f"  -- 'YYYY-MM', from {lo} to {hi}"
                    elif len(values) <= max_values:
                        note = "  -- one of: " + ", ".join(f"'{v}'" for v in values)
                lines.append(f"  {name} {col_type}{note}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)
    finally:
        conn.close()


def list_tables(db_path: str | Path) -> list[str]:
    """User tables in the knowledge base."""
    conn = _open_ro(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def month_range(db_path: str | Path, table: str) -> tuple[str, str] | None:
    """(first, last) value of `table.month`, or None if it has no month column."""
    conn = _open_ro(db_path)
    try:
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')}
        if "month" not in cols:
            return None
        lo, hi = conn.execute(f'SELECT MIN(month), MAX(month) FROM "{table}"').fetchone()
        return (lo, hi) if lo and hi else None
    finally:
        conn.close()
