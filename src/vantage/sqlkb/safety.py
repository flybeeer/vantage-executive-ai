"""First layer on model-written SQL: one read-only SELECT, bounded.

Carried over from Sourcerer. It is a text check, so it is not the access
control — `executor` enforces which tables may be read inside SQLite itself.
This layer exists so a hallucinated `DELETE` or a smuggled second statement is
rejected with a readable reason before anything is prepared, and so every query
carries a row cap.
"""

from __future__ import annotations

import re

_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
)

_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_WORD_RE = re.compile(r"[a-z_]+")
_SELECT_START_RE = re.compile(r"\b(select|with)\b", re.IGNORECASE)
_TRAILING_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """Generated SQL is not a single read-only SELECT."""


def _strip(raw: str) -> str:
    """Reduce a chatty reply to one candidate statement.

    Drops markdown fences, slices from the first SELECT/WITH, keeps only up to
    the first `;`. What remains still goes through every check below.
    """
    text = _FENCE_RE.sub("", raw).strip()
    match = _SELECT_START_RE.search(text)
    if match:
        text = text[match.start() :]
    return text.split(";", 1)[0].strip()


def safe_select(raw: str, max_rows: int) -> str:
    """Return a validated SELECT bounded by `max_rows`, or raise UnsafeSQLError."""
    sql = _strip(raw)
    if not sql:
        raise UnsafeSQLError("empty SQL")

    lowered = sql.lower()
    first_word = _WORD_RE.search(lowered)
    first = first_word.group(0) if first_word else None
    if first not in ("select", "with"):
        raise UnsafeSQLError(f"not a SELECT (starts with {first!r})")

    # Word boundaries so a column like `created_at` does not trip "create".
    forbidden = [kw for kw in _FORBIDDEN if re.search(rf"\b{kw}\b", lowered)]
    if forbidden:
        raise UnsafeSQLError(f"forbidden keyword(s): {', '.join(forbidden)}")

    limit = _TRAILING_LIMIT_RE.search(sql)
    if limit is None:
        return f"{sql} LIMIT {max_rows}"
    if int(limit.group(1)) > max_rows:
        return _TRAILING_LIMIT_RE.sub(f"LIMIT {max_rows}", sql)
    return sql
