"""The eval grader. If a rule passed or failed wrongly, EVAL.md would be wrong."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from vantage.eval.grading import CITATION, grade
from vantage.generation.answer import Answer, Citation, Fact
from vantage.observability import audit
from vantage.retrieval.types import RetrievedChunk
from vantage.service import Outcome

ROOT = Path(__file__).resolve().parent.parent


def _answer(text: str, cites: list[tuple[str, str]], rows: str | None = None) -> Answer:
    citations = [
        Citation(n, f"{s} · {v}", s, v, date(2026, 9, 30), rows=rows)
        for n, (s, v) in enumerate(cites, start=1)
    ]
    return Answer([Fact(text, tuple(c.n for c in citations))], "", [], citations)


def _outcome(decision=audit.ALLOWED, route="sql", answer=None, message=None, reason="", chunks=()):
    if answer is not None and message is None:
        message = answer.render() + "\n    SQL: SELECT 999999 FROM t"
    return Outcome(decision, reason, route, message=message, answer=answer, chunks=list(chunks))


def _failed(checks) -> set[str]:
    return {c.name for c in checks if not c.ok}


Q1 = {
    "expect": {
        "decision": "allowed",
        "route": "sql",
        "contains": ["1343"],
        "must_cite": ["enrollment_2025_2026.xlsx"],
        "rows": "402–421",
    }
}


def test_a_correct_cited_answer_passes():
    a = _answer("ยอดรวม 1,343 คน", [("enrollment_2025_2026.xlsx", "v1")], rows="402–421")
    assert _failed(grade(Q1, _outcome(answer=a))) == set()


def test_a_figure_only_in_the_sql_does_not_count_as_stated():
    a = _answer("ยอดรวมตามตาราง", [("enrollment_2025_2026.xlsx", "v1")], rows="402–421")
    out = _outcome(answer=a, message=a.render() + "\nSQL: ... = 1343")
    assert _failed(grade(Q1, out)) == {"contains"}


def test_wrong_rows_and_stray_citations_are_citation_failures():
    a = _answer("ยอดรวม 1,343 คน", [("enrollment_2025_2026.xlsx", "v1"), ("x.pdf", "v1")], "2–5")
    checks = grade(Q1, _outcome(answer=a))
    assert _failed(checks) == {"may_cite", "rows"}
    assert all(c.family == CITATION for c in checks if not c.ok)


def test_version_pinned_citation():
    case = {"expect": {"must_cite": ["plan.pdf@v2"], "may_cite": ["plan.pdf@v1"]}}
    v1_only = _answer("เป้า 4,800", [("plan.pdf", "v1")])
    assert _failed(grade(case, _outcome(answer=v1_only))) == {"must_cite"}
    both = _answer("เป้า 5,400", [("plan.pdf", "v2"), ("plan.pdf", "v1")])
    assert _failed(grade(case, _outcome(answer=both))) == set()


def test_a_leak_anywhere_on_screen_fails_isolation():
    case = {"expect": {"decision": ["refused", "allowed"], "leaks": ["35.9"]}}
    a = _answer("ข้อมูลทั่วไป", [("plan.pdf", "v2")])
    out = _outcome(answer=a, message=a.render() + "\n[1] board · สยาม 35.9%")
    assert _failed(grade(case, out)) == {"leaks"}


def test_a_forbidden_file_among_retrieval_candidates_fails_even_if_not_cited():
    chunk = RetrievedChunk(1, 1, "board.pdf", "v1", 0, 1, None, None, True, "x", 0.1)
    case = {"expect": {"forbidden_sources": ["board.pdf"]}}
    out = _outcome(decision=audit.REFUSED, message="ไม่พบข้อมูล", chunks=[chunk])
    assert _failed(grade(case, out)) == {"forbidden_sources"}


def test_denied_case_and_audit_reason():
    case = {"expect": {"decision": "denied", "reason_contains": ["finance_monthly"]}}
    ok = _outcome(decision=audit.DENIED, route="sql", message="ไม่มีสิทธิ์",
                  reason="explicit deny: table 'finance_monthly'")  # fmt: skip
    assert _failed(grade(case, ok)) == set()
    wrong = _outcome(decision=audit.ALLOWED, message="รายได้ 981,000", reason="")
    assert _failed(grade(case, wrong)) == {"decision", "reason_contains"}


def test_golden_file_is_well_formed():
    cases = yaml.safe_load((ROOT / "eval" / "golden.yaml").read_text(encoding="utf-8"))["cases"]
    assert [c["id"] for c in cases] == list(range(1, 13))
    assert {c["metric"] for c in cases} == {"correctness", "refusal", "isolation"}
    assert {c["user"] for c in cases} <= {"ceo", "academic_director"}
    for c in cases:
        assert c["expect"].get("decision"), c["id"]
