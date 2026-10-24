"""Render a policy `AccessFilter` as a SQL predicate on `documents`.

This is the piece that makes "the filter is applied inside the query" true. Both
retrieval legs splice `predicate()` into their `WHERE` clause, so a chunk the
principal may not read is never a candidate — it is not fetched, not ranked, and
never handed to the model.

The predicate has to make the same decision `policy.authorize()` makes for a
document, or the two gates would disagree about what is visible:

- the document shares a workspace with the principal (`&&` = array overlap)
- its source matches an allow glob
- its source matches no deny glob

Globs are translated to anchored regular expressions here rather than to `LIKE`,
because `LIKE` treats `_` as a wildcard and every file name in the corpus has
one. `tests/test_retrieval_db.py` checks the translation against `authorize()`.
"""

from __future__ import annotations

from vantage.policy import AccessFilter

# Characters with a meaning in a Postgres ARE (and in Python's `re`). Everything
# else is matched literally, Thai included.
_REGEX_SPECIAL = set(".^$*+?()[]{}|\\")


def glob_to_regex(pattern: str) -> str:
    """Translate a policy glob (`*`, `?`) into an anchored regex.

    Bracket classes are rejected rather than translated: `fnmatch` and a
    Postgres regex disagree on edge cases (`[!a]` vs `[^a]`), and an access rule
    is the wrong place for two engines to disagree. Failing loudly here means a
    policy that uses one cannot be loaded into a query at all.
    """
    if "[" in pattern or "]" in pattern:
        raise ValueError(f"bracket globs are not supported in access rules: {pattern!r}")
    out: list[str] = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        elif ch in _REGEX_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "^" + "".join(out) + "$"


def predicate(access: AccessFilter, *, alias: str = "d") -> tuple[str, dict]:
    """Return `(sql, params)` restricting `alias` (a `documents` row) to `access`.

    Raises on an empty filter instead of returning a predicate that matches
    nothing: callers must short-circuit before querying, and a call that gets
    this far with an empty filter is a bug worth hearing about.
    """
    if access.is_empty:
        raise ValueError(f"refusing to build a query for an empty access filter ({access.reason})")
    sql = (
        f"{alias}.workspaces && %(acl_workspaces)s::text[]"
        f" AND {alias}.source ~ ANY(%(acl_allow)s::text[])"
        f" AND NOT ({alias}.source ~ ANY(%(acl_deny)s::text[]))"
    )
    params = {
        "acl_workspaces": list(access.workspaces),
        "acl_allow": [glob_to_regex(p) for p in access.allow_patterns],
        "acl_deny": [glob_to_regex(p) for p in access.deny_patterns],
    }
    return sql, params
