"""Authorization — the only policy surface the rest of the app may use.

Retrieval, generation and the API import from here and nowhere deeper, so every
access decision is reviewable in `policy/engine.py` and the policy engine can be
replaced (Cerbos is the obvious production swap) without touching the answer
path.

Two functions do the work:

- `allowed_filter(principal)` — the predicate pushed *into* the retrieval query,
  called before retrieval so forbidden chunks are never candidates.
- `authorize(principal, action, resource)` — a point decision for a named asset
  (a tabular-KB table, a document being cited).
"""

from __future__ import annotations

from vantage.policy.engine import (
    DOCUMENT,
    TABLE,
    AccessFilter,
    Decision,
    PolicyError,
    Principal,
    Resource,
    allowed_filter,
    authorize,
    describe_table,
    list_principals,
    resolve_principal,
)

__all__ = [
    "DOCUMENT",
    "TABLE",
    "AccessFilter",
    "Decision",
    "PolicyError",
    "Principal",
    "Resource",
    "allowed_filter",
    "authorize",
    "describe_table",
    "list_principals",
    "resolve_principal",
]
