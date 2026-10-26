"""Grade one golden-set outcome against its expectations. Rules, not a judge.

Every check is a plain comparison a reviewer can re-do by hand from the audit
log: the decision, the route, strings the answer must contain, the sources it
cites, and — for the isolation cases — strings and files that must not reach
the user at all. No model grades another model's answer, so a pass means the
rule held, not that a second model was persuaded.

Checks fall into two families, reported separately in EVAL.md:

- **answer** — decision, route, contains, leaks, reason_contains
- **citation** — must_cite, may_cite, rows (only when an answer was given)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vantage.service import Outcome

ANSWER = "answer"
CITATION = "citation"

_STRIP_RE = re.compile(r"[\s,]+")


def _norm(text: str) -> str:
    """Whitespace and thousands separators removed, so "1,343 คน" ⊇ "1343"."""
    return _STRIP_RE.sub("", text)


@dataclass(frozen=True)
class Check:
    name: str
    family: str  # ANSWER | CITATION
    ok: bool
    detail: str = ""


@dataclass
class Graded:
    case_id: int
    metric: str
    user: str
    question: str
    outcome: Outcome
    checks: list[Check] = field(default_factory=list)
    followup: Graded | None = None

    def family_ok(self, family: str) -> bool:
        own = all(c.ok for c in self.checks if c.family == family)
        return own and (self.followup is None or self.followup.family_ok(family))

    @property
    def passed(self) -> bool:
        own = all(c.ok for c in self.checks)
        return own and (self.followup is None or self.followup.passed)

    @property
    def failures(self) -> list[str]:
        out = [f"{c.name}: {c.detail}" for c in self.checks if not c.ok]
        if self.followup is not None:
            out += [f"then → {f}" for f in self.followup.failures]
        return out


def _cited(outcome: Outcome) -> list[tuple[str, str]]:
    if outcome.answer is None:
        return []
    return [(c.source, c.doc_version) for c in outcome.answer.cited]


def _matches(ref: str, source: str, version: str) -> bool:
    """`ref` is "file" or "file@v2"."""
    file, _, want = ref.partition("@")
    return file == source and (not want or want == version)


def grade(case: dict, outcome: Outcome) -> list[Check]:
    """All checks for `case["expect"]` against `outcome`."""
    expect = case.get("expect") or {}
    checks: list[Check] = []

    # What the user reads as the answer. Sources and SQL are excluded from
    # `contains` (a figure must be stated, not merely present in a query) but
    # included for `leaks` (a leak anywhere on screen is a leak).
    body = outcome.answer.render(sources=False) if outcome.answer else (outcome.message or "")
    screen = outcome.message or ""

    want = expect.get("decision")
    if want is not None:
        allowed = want if isinstance(want, list) else [want]
        checks.append(
            Check("decision", ANSWER, outcome.decision in allowed,
                  f"expected {'/'.join(allowed)}, got {outcome.decision}")
        )  # fmt: skip

    if "route" in expect:
        checks.append(
            Check("route", ANSWER, outcome.route == expect["route"],
                  f"expected {expect['route']}, got {outcome.route}")
        )  # fmt: skip

    missing = [s for s in expect.get("contains", []) if _norm(s) not in _norm(body)]
    if expect.get("contains"):
        checks.append(Check("contains", ANSWER, not missing, f"missing {missing}"))

    leaked = [s for s in expect.get("leaks", []) if _norm(s) in _norm(screen)]
    if expect.get("leaks"):
        checks.append(Check("leaks", ANSWER, not leaked, f"leaked {leaked}"))

    if expect.get("forbidden_sources"):
        seen = {c.source for c in outcome.chunks} | {s for s, _ in _cited(outcome)}
        bad = sorted(seen & set(expect["forbidden_sources"]))
        checks.append(Check("forbidden_sources", ANSWER, not bad, f"retrieved or cited {bad}"))

    absent = [s for s in expect.get("reason_contains", []) if s not in outcome.reason]
    if expect.get("reason_contains"):
        checks.append(Check("reason_contains", ANSWER, not absent, f"audit reason lacks {absent}"))

    # Citations are graded only for questions that expect an answer.
    cited = _cited(outcome)
    if expect.get("must_cite"):
        lacking = [r for r in expect["must_cite"] if not any(_matches(r, *c) for c in cited)]
        checks.append(Check("must_cite", CITATION, not lacking, f"not cited {lacking}"))
        allowed_refs = expect["must_cite"] + expect.get("may_cite", [])
        stray = [f"{s}@{v}" for s, v in cited if not any(_matches(r, s, v) for r in allowed_refs)]
        checks.append(Check("may_cite", CITATION, not stray, f"cited unexpected {stray}"))

    if "rows" in expect:
        rows = [c.rows for c in outcome.answer.cited] if outcome.answer else []
        checks.append(
            Check("rows", CITATION, expect["rows"] in rows,
                  f"expected rows {expect['rows']}, got {rows}")
        )  # fmt: skip
    return checks
