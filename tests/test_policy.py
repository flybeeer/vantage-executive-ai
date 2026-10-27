"""The two safety rules that must never regress.

The project plan names these explicitly: **deny-by-default for unknown roles** and
**explicit deny beats wildcard allow**. They are tested against purpose-built
policy fixtures rather than the shipped `policy/policy.yaml`, because a rule
should be provable on its own terms — if the demo policy later stops happening
to exercise a branch, the guarantee must not quietly stop being tested.

`test_shipped_policy_isolates_workspaces` then checks the real policy, so the
demo's own two users are covered too.
"""

from __future__ import annotations

import textwrap

import pytest

from vantage import policy
from vantage.policy import engine


@pytest.fixture
def load_policy(tmp_path, monkeypatch):
    """Write a policy file, point the settings at it, and clear the caches."""

    def _load(yaml_text: str):
        path = tmp_path / "policy.yaml"
        path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        from vantage.config import get_settings

        get_settings.cache_clear()
        engine._load.cache_clear()
        monkeypatch.setenv("POLICY_PATH", str(path))
        return get_settings()

    yield _load
    from vantage.config import get_settings

    get_settings.cache_clear()
    engine._load.cache_clear()


def _doc(name: str, *workspaces: str) -> policy.Resource:
    return policy.Resource(kind=policy.DOCUMENT, name=name, workspaces=workspaces)


# ---------- Rule 1: deny by default ----------


def test_unknown_role_is_denied(load_policy):
    """A role that is not in the policy grants nothing — it is not ignored."""
    load_policy("""
        version: 1
        principals:
          ghost:
            display_name: Ghost
            roles: [not_a_real_role]
        roles:
          executive:
            workspaces: [A]
            documents:
              allow: ["*"]
        """)
    ghost = policy.resolve_principal("ghost")

    assert ghost is not None
    assert ghost.workspaces == ()
    decision = policy.authorize(ghost, "read", _doc("anything.pdf", "A"))
    assert decision.allowed is False
    assert "deny-by-default" in decision.reason
    assert policy.allowed_filter(ghost).is_empty


def test_unknown_principal_is_denied(load_policy):
    """An unresolvable user is never a principal, and None is denied."""
    load_policy("""
        version: 1
        principals: {}
        roles:
          executive:
            workspaces: [A]
            documents:
              allow: ["*"]
        """)
    assert policy.resolve_principal("nobody") is None
    assert policy.authorize(None, "read", _doc("x.pdf", "A")).allowed is False
    assert policy.allowed_filter(None).is_empty


def test_asset_matching_no_allow_rule_is_denied(load_policy):
    """Sharing a workspace is not sufficient — an allow rule must also match."""
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [narrow]
        roles:
          narrow:
            workspaces: [A]
            documents:
              allow: ["board_*.pdf"]
        """)
    alice = policy.resolve_principal("alice")

    assert policy.authorize(alice, "read", _doc("board_q2.pdf", "A")).allowed is True
    denied = policy.authorize(alice, "read", _doc("salaries.pdf", "A"))
    assert denied.allowed is False
    assert "matches no allow rule" in denied.reason


def test_uncatalogued_table_is_denied(load_policy):
    """A table nobody tagged has no workspaces, so it is not world-readable."""
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [executive]
        roles:
          executive:
            workspaces: [A]
            tables:
              allow: ["*"]
        tables:
          known_table:
            workspaces: [A]
        """)
    alice = policy.resolve_principal("alice")

    assert policy.authorize(alice, "read", policy.describe_table("known_table")).allowed is True
    orphan = policy.authorize(alice, "read", policy.describe_table("forgotten_table"))
    assert orphan.allowed is False
    assert "no shared workspace" in orphan.reason


def test_non_read_action_is_denied(load_policy):
    """Only "read" is granted; a new action is denied until the policy says so."""
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [executive]
        roles:
          executive:
            workspaces: [A]
            documents:
              allow: ["*"]
        """)
    alice = policy.resolve_principal("alice")
    assert policy.authorize(alice, "delete", _doc("plan.pdf", "A")).allowed is False


# ---------- Rule 2: explicit deny beats wildcard allow ----------


def test_explicit_deny_beats_wildcard_allow(load_policy):
    """A deny glob wins over `allow: ["*"]`, in the same role."""
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [broad]
        roles:
          broad:
            workspaces: [A]
            documents:
              allow: ["*"]
              deny: ["salaries_*.pdf"]
        """)
    alice = policy.resolve_principal("alice")

    assert policy.authorize(alice, "read", _doc("plan.pdf", "A")).allowed is True
    decision = policy.authorize(alice, "read", _doc("salaries_2026.pdf", "A"))
    assert decision.allowed is False
    assert "explicit deny" in decision.reason


def test_deny_in_one_role_beats_allow_in_another(load_policy):
    """Denies are unioned across roles, so a second role cannot grant around one.

    This is the case that makes "most permissive role wins" dangerous: adding a
    broad role to a user must not silently re-open what a narrow role denied.
    """
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [restricted, broad]
        roles:
          restricted:
            workspaces: [A]
            documents:
              deny: ["salaries_*.pdf"]
          broad:
            workspaces: [A]
            documents:
              allow: ["*"]
        """)
    alice = policy.resolve_principal("alice")

    assert policy.authorize(alice, "read", _doc("plan.pdf", "A")).allowed is True
    assert policy.authorize(alice, "read", _doc("salaries_2026.pdf", "A")).allowed is False


def test_deny_is_carried_into_the_retrieval_filter(load_policy):
    """The filter pushed into the query carries the denies, not just the allows.

    If `allowed_filter` dropped them, the deny would only hold on the point
    checks and a denied document could still enter the candidate set.
    """
    load_policy("""
        version: 1
        principals:
          alice:
            display_name: Alice
            roles: [broad]
        roles:
          broad:
            workspaces: [A]
            documents:
              allow: ["*"]
              deny: ["salaries_*.pdf"]
        """)
    alice = policy.resolve_principal("alice")
    access = policy.allowed_filter(alice)

    assert access.is_empty is False
    assert access.workspaces == ("A",)
    assert "salaries_*.pdf" in access.deny_patterns


# ---------- The shipped policy ----------


def test_shipped_policy_isolates_the_two_workspaces():
    """The demo's own users must not overlap on the restricted assets."""
    engine._load.cache_clear()
    ceo = policy.resolve_principal("ceo")
    director = policy.resolve_principal("academic_director")

    assert ceo is not None and director is not None
    assert ceo.workspaces == ("A",)
    assert director.workspaces == ("B",)

    finance = policy.describe_table("finance_monthly")
    assert policy.authorize(ceo, "read", finance).allowed is True
    assert policy.authorize(director, "read", finance).allowed is False

    enrollment = policy.describe_table("enrollment")
    assert policy.authorize(ceo, "read", enrollment).allowed is True
    assert policy.authorize(director, "read", enrollment).allowed is True

    board = _doc("board_meeting_2026Q2.pdf", "A")
    assert policy.authorize(ceo, "read", board).allowed is True
    assert policy.authorize(director, "read", board).allowed is False
