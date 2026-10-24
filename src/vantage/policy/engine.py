"""Policy engine internals. Do not import this module from the answer path.

The answer path calls `authorize()` and `allowed_filter()` from
`vantage.policy` and nothing else, so authorization can be reviewed in one
place and swapped (for Cerbos, say) without touching retrieval or generation.

Decision order — the two rules that have tests:

1. **Deny-by-default.** A principal with no known role is granted nothing. An
   unknown role contributes no grants rather than being ignored.
2. **Explicit deny beats wildcard allow.** A `deny` glob match is final; it is
   evaluated before any allow and cannot be overridden.

Then, to be allowed, an asset must *both* share a workspace with the principal
*and* match an allow glob.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from vantage.config import get_settings

# Asset kinds the policy knows about. "document" = an ingested file (workspace
# tags live on the documents row); "table" = a tabular KB table (tagged in the
# policy's own catalog).
DOCUMENT = "document"
TABLE = "table"
_KINDS = {DOCUMENT: "documents", TABLE: "tables"}


class PolicyError(RuntimeError):
    """Raised when the policy file is missing or malformed."""


@dataclass(frozen=True)
class Principal:
    """Who is asking. Produced by `resolve_principal`, never built by callers."""

    id: str
    display_name: str
    roles: tuple[str, ...]
    workspaces: tuple[str, ...]

    @property
    def workspace_label(self) -> str:
        """Human-readable workspace for logs and the UI."""
        return ",".join(self.workspaces) if self.workspaces else "-"


@dataclass(frozen=True)
class Resource:
    """What is being reached for."""

    kind: str  # DOCUMENT | TABLE
    name: str
    workspaces: tuple[str, ...] = ()
    classification: str = "internal"


@dataclass(frozen=True)
class Decision:
    """The outcome, with a reason fit to write straight into the audit log."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class AccessFilter:
    """The predicate to push *into* the retrieval query.

    Not a post-filter: a forbidden chunk must never enter the candidate set, so
    these values are bound into the SQL `WHERE` clause of both retrieval legs.
    An empty `workspaces` means "nothing is visible" and callers must short-
    circuit rather than run an unfiltered query.
    """

    workspaces: tuple[str, ...] = ()
    allow_patterns: tuple[str, ...] = ()
    deny_patterns: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_empty(self) -> bool:
        """True when this principal can reach nothing of this kind."""
        return not self.workspaces or not self.allow_patterns


@dataclass
class _Policy:
    """Parsed policy file."""

    version: int
    principals: dict[str, dict] = field(default_factory=dict)
    roles: dict[str, dict] = field(default_factory=dict)
    tables: dict[str, dict] = field(default_factory=dict)


@lru_cache
def _load() -> _Policy:
    """Read and cache the policy file."""
    path = Path(get_settings().policy_path)
    if not path.is_file():
        raise PolicyError(f"policy file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "roles" not in raw:
        raise PolicyError(f"policy file has no 'roles' section: {path}")
    return _Policy(
        version=int(raw.get("version", 0)),
        principals=raw.get("principals") or {},
        roles=raw.get("roles") or {},
        tables=raw.get("tables") or {},
    )


def _grants(policy: _Policy, roles: tuple[str, ...], kind: str) -> tuple[list[str], list[str]]:
    """Collect (allow, deny) globs for `kind` across the principal's roles.

    Unknown roles contribute nothing — that is deny-by-default in one line.
    """
    section = _KINDS[kind]
    allow: list[str] = []
    deny: list[str] = []
    for role in roles:
        rule = policy.roles.get(role)
        if rule is None:
            continue  # unknown role grants nothing
        kind_rule = rule.get(section) or {}
        allow.extend(kind_rule.get("allow") or [])
        deny.extend(kind_rule.get("deny") or [])
    return allow, deny


def _role_workspaces(policy: _Policy, roles: tuple[str, ...]) -> tuple[str, ...]:
    """Union of workspaces granted by the principal's known roles."""
    out: list[str] = []
    for role in roles:
        for ws in (policy.roles.get(role) or {}).get("workspaces") or []:
            if ws not in out:
                out.append(ws)
    return tuple(out)


def _matches(name: str, patterns: list[str]) -> str | None:
    """Return the first glob in `patterns` that matches `name`, else None."""
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return pattern
    return None


def resolve_principal(user_id: str) -> Principal | None:
    """Look up a principal by id. Returns None for an unknown user.

    Stands in for an identity provider: the demo logs in as a known id, a real
    deployment would build this from verified JWT claims.
    """
    policy = _load()
    entry = policy.principals.get(user_id)
    if entry is None:
        return None
    roles = tuple(entry.get("roles") or ())
    return Principal(
        id=user_id,
        display_name=entry.get("display_name") or user_id,
        roles=roles,
        workspaces=_role_workspaces(policy, roles),
    )


def list_principals() -> list[Principal]:
    """Every configured principal — used by the UI's user switcher."""
    return [p for p in (resolve_principal(uid) for uid in _load().principals) if p is not None]


def describe_table(name: str) -> Resource:
    """Build a TABLE resource from the policy's asset catalog.

    An uncatalogued table gets no workspaces, so `authorize` denies it — a table
    nobody tagged is not quietly world-readable.
    """
    entry = _load().tables.get(name) or {}
    return Resource(
        kind=TABLE,
        name=name,
        workspaces=tuple(entry.get("workspaces") or ()),
        classification=entry.get("classification") or "internal",
    )


def authorize(principal: Principal | None, action: str, resource: Resource) -> Decision:
    """Decide whether `principal` may `action` `resource`.

    `action` is "read" throughout the demo; it is part of the signature because
    it is part of the decision model, and widening it later should not change
    every call site.
    """
    if principal is None:
        return Decision(False, "deny-by-default: unauthenticated principal")
    if not principal.roles:
        return Decision(False, f"deny-by-default: principal {principal.id!r} has no roles")
    if resource.kind not in _KINDS:
        return Decision(False, f"deny-by-default: unknown resource kind {resource.kind!r}")

    policy = _load()
    known = [r for r in principal.roles if r in policy.roles]
    if not known:
        return Decision(
            False,
            f"deny-by-default: no known role among {list(principal.roles)}",
        )

    allow, deny = _grants(policy, principal.roles, resource.kind)

    # Explicit deny is final — checked before any allow so a wildcard allow can
    # never override it.
    denied_by = _matches(resource.name, deny)
    if denied_by is not None:
        return Decision(
            False,
            f"explicit deny: {resource.kind} {resource.name!r} matches deny rule {denied_by!r}",
        )

    shared = sorted(set(principal.workspaces) & set(resource.workspaces))
    if not shared:
        return Decision(
            False,
            f"no shared workspace: principal has {list(principal.workspaces)}, "
            f"{resource.kind} {resource.name!r} is in {list(resource.workspaces)}",
        )

    allowed_by = _matches(resource.name, allow)
    if allowed_by is None:
        return Decision(
            False,
            f"deny-by-default: {resource.kind} {resource.name!r} matches no allow rule",
        )

    if action != "read":
        return Decision(False, f"deny-by-default: action {action!r} is not granted")

    return Decision(
        True,
        f"allow: workspace {shared[0]} via role(s) {known}, allow rule {allowed_by!r}",
    )


def allowed_filter(principal: Principal | None, kind: str = DOCUMENT) -> AccessFilter:
    """Return the access predicate to bind into the retrieval query.

    Called once per question, *before* retrieval. The result is pushed into the
    `WHERE` clause of both the vector and the keyword leg so forbidden chunks
    are never candidates — filtering after generation would be too late, the
    model would already have read them.
    """
    if principal is None:
        return AccessFilter(reason="deny-by-default: unauthenticated principal")
    if kind not in _KINDS:
        return AccessFilter(reason=f"deny-by-default: unknown resource kind {kind!r}")

    policy = _load()
    known = [r for r in principal.roles if r in policy.roles]
    if not known:
        return AccessFilter(reason=f"deny-by-default: no known role among {list(principal.roles)}")

    allow, deny = _grants(policy, principal.roles, kind)
    if not principal.workspaces or not allow:
        return AccessFilter(
            reason=f"deny-by-default: role(s) {known} grant no {_KINDS[kind]} access"
        )

    return AccessFilter(
        workspaces=principal.workspaces,
        allow_patterns=tuple(allow),
        deny_patterns=tuple(deny),
        reason=f"workspaces {list(principal.workspaces)} via role(s) {known}",
    )
