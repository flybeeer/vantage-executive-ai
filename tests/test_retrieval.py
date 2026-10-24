"""Retrieval logic that needs no database: fusion, the access predicate, citations.

The database-backed guarantees (a B user never receives an A-only chunk) are in
`test_retrieval_db.py`; these cover the pieces those guarantees are built from.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date

import pytest

from vantage.policy import AccessFilter
from vantage.retrieval.access import glob_to_regex, predicate
from vantage.retrieval.fusion import KEYWORD, VECTOR, reciprocal_rank_fusion
from vantage.retrieval.types import RetrievedChunk
from vantage.sqlkb.catalog import tables_for


def _chunk(cid: int, score: float = 0.0, **kw) -> RetrievedChunk:
    fields = {
        "id": cid,
        "document_id": 1,
        "source": "plan.pdf",
        "doc_version": "v1",
        "chunk_index": cid,
        "page": 1,
        "row_ref": None,
        "data_date": date(2026, 6, 30),
        "is_current": True,
        "content": f"c{cid}",
        "score": score,
    }
    return RetrievedChunk(**(fields | kw))


# ---------- Fusion ----------


def test_fusion_empty_input():
    assert reciprocal_rank_fusion({}, k=60) == []
    assert reciprocal_rank_fusion({VECTOR: [], KEYWORD: []}, k=60) == []


def test_fusion_dedupes_and_ranks_shared_hit_first():
    fused = reciprocal_rank_fusion(
        {VECTOR: [_chunk(1), _chunk(2)], KEYWORD: [_chunk(3), _chunk(2)]}, k=60
    )
    assert sorted(c.id for c in fused) == [1, 2, 3]
    assert fused[0].id == 2


def test_fusion_score_is_rrf_sum():
    fused = reciprocal_rank_fusion({VECTOR: [_chunk(1)], KEYWORD: [_chunk(1)]}, k=60)
    assert abs(fused[0].score - 2 / 61) < 1e-9


def test_fusion_keeps_each_legs_raw_score():
    """RRF is rank-only; the refusal decision needs the raw scores, so they survive."""
    fused = reciprocal_rank_fusion(
        {VECTOR: [_chunk(1, 0.83)], KEYWORD: [_chunk(1, 0.4), _chunk(2, 0.1)]}, k=60
    )
    by_id = {c.id: c for c in fused}
    assert by_id[1].vector_score == 0.83
    assert by_id[1].keyword_score == 0.4
    assert by_id[2].vector_score is None
    assert by_id[2].keyword_score == 0.1


def test_rrf_cannot_signal_irrelevance_on_its_own():
    """Why the raw scores matter: a weak lone hit still gets the top RRF score."""
    weak = reciprocal_rank_fusion({VECTOR: [_chunk(1, 0.02)]}, k=60)
    strong = reciprocal_rank_fusion({VECTOR: [_chunk(1, 0.95)]}, k=60)
    assert weak[0].score == strong[0].score


# ---------- Glob → regex ----------

_NAMES = [
    "board_meeting_2026Q2.pdf",
    "boardXmeeting_2026Q2.pdf",
    "strategy_plan_2026.pdf",
    "finance_monthly",
    "financeXmonthly",
    "academic_report_term1.pdf",
    "Board_meeting_2026Q2.pdf",
    "a.b",
    "aXb",
]
_PATTERNS = ["*", "board_*.pdf", "finance_*", "strategy_plan_????.pdf", "a.b", "*_report_*"]


@pytest.mark.parametrize("pattern", _PATTERNS)
def test_glob_translation_agrees_with_fnmatchcase(pattern):
    """The query filter and `authorize()` must make the same call on every name.

    `_` and `.` are the interesting cases: `LIKE` would treat `_` as a wildcard
    and an unescaped regex `.` matches anything, so `boardXmeeting` would leak
    through a rule written for `board_meeting`.
    """
    regex = re.compile(glob_to_regex(pattern))
    for name in _NAMES:
        assert bool(regex.match(name)) == fnmatch.fnmatchcase(name, pattern), (pattern, name)


def test_bracket_globs_are_rejected():
    with pytest.raises(ValueError, match="bracket"):
        glob_to_regex("board_[0-9].pdf")


def test_predicate_refuses_an_empty_filter():
    """An empty filter must short-circuit, never become an unfiltered query."""
    with pytest.raises(ValueError, match="empty access filter"):
        predicate(AccessFilter(reason="deny-by-default"))


def test_predicate_binds_values_rather_than_inlining_them():
    access = AccessFilter(
        workspaces=("B",), allow_patterns=("*",), deny_patterns=("finance_*",), reason="r"
    )
    sql, params = predicate(access)
    assert "finance" not in sql and "'B'" not in sql
    assert params["acl_workspaces"] == ["B"]
    assert params["acl_deny"] == ["^finance_.*$"]


# ---------- Citation ----------


def test_citation_names_file_version_page_and_data_date():
    c = _chunk(1, source="board_meeting_2026Q2.pdf", page=2)
    assert c.citation == "board_meeting_2026Q2.pdf · v1 · หน้า 2 · ข้อมูล ณ 2026-06-30"


def test_citation_flags_a_superseded_version():
    c = _chunk(1, source="strategy_plan_2026.pdf", is_current=False)
    assert c.citation.endswith("ฉบับเก่า")


def test_audit_ref_survives_chunk_id_rotation():
    """Re-ingest renumbers chunk ids; the audit row must still locate the chunk."""
    ref = _chunk(42, source="plan.pdf").audit_ref()
    assert {"chunk_id", "source", "doc_version", "chunk_index", "page"} <= ref.keys()


# ---------- Table catalog ----------


def test_q8_revenue_question_needs_the_finance_table():
    assert tables_for("รายได้สาขาบางนาเดือน ส.ค. เท่าไร") == ["finance_monthly"]


def test_q6_marketing_cost_per_student_needs_both_tables():
    assert tables_for("ค่าการตลาดต่อนักเรียนใหม่ของแต่ละสาขา") == ["finance_monthly", "enrollment"]


@pytest.mark.parametrize(
    "question",
    [
        "บอร์ดตัดสินใจอะไรเรื่องการขยายสาขา",
        "สรุปความเสี่ยง 3 ข้อจากรายงานบอร์ด",
        "อัตราสอบผ่านวิชาไหนต่ำสุดเทอม 1",
        "บอร์ดคุยเรื่องอะไรไตรมาส 2",
    ],
)
def test_document_questions_touch_no_table(question):
    assert tables_for(question) == []
