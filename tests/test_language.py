"""The answer follows the language of the question: Thai or English."""

from __future__ import annotations

from datetime import date
from string import Formatter

import pytest

from vantage.generation import language, prompts
from vantage.generation.answer import Answer, Citation, Fact, not_found
from vantage.generation.language import EN, TH
from vantage.retrieval.types import RetrievedChunk


@pytest.mark.parametrize(
    ("question", "lang"),
    [
        ("ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร", TH),
        ("สาขาไหนรายได้โตสูงสุด Q3 2026 เทียบ Q2", TH),  # Latin inside a Thai question
        ("How many students were enrolled in September 2026?", EN),
        ("What was Bang Na's revenue in August?", EN),
    ],
)
def test_the_question_decides_the_language(question, lang):
    assert language.of(question) == lang


def test_every_message_exists_in_both_languages():
    for key, entry in language._TEXT.items():
        assert set(entry) == {TH, EN}, key
        # Same placeholders, so neither language can drop a value the other shows.
        fields = {
            lang: {f for _, f, _, _ in Formatter().parse(s) if f} for lang, s in entry.items()
        }
        assert fields[TH] == fields[EN], key


def test_an_english_answer_has_english_headings_and_labels():
    chunk = RetrievedChunk(
        1, 1, "strategy_plan_2026.pdf", "v1", 0, 1, None, date(2026, 1, 15), False, "x", 0.1
    )
    assert (
        chunk.label(EN) == "strategy_plan_2026.pdf · v1 · p. 1 · data as of 2026-01-15 · superseded"
    )
    assert chunk.citation == "strategy_plan_2026.pdf · v1 · หน้า 1 · ข้อมูล ณ 2026-01-15 · ฉบับเก่า"

    cite = Citation(1, chunk.label(EN), chunk.source, chunk.doc_version, chunk.data_date)
    answer = Answer(
        [Fact("The target was 4,800.", (1,))], "Raised later.", ["Synthetic."], [cite], lang=EN
    )
    rendered = answer.render()
    for heading in ("**Facts**", "**Analysis**", "**Limitations**", "**Sources**"):
        assert heading in rendered
    assert "ข้อ" not in rendered and "แหล่ง" not in rendered


def test_refusals_lead_with_the_question_language():
    assert not_found("x", EN) == "No data found — x"
    assert not_found("x") == "ไม่พบข้อมูล — x"


def test_the_model_is_told_the_answer_language_without_changing_the_system_prompt():
    th = prompts.sql_messages("ยอด ก.ย. 2026", "schema", TH)
    en = prompts.sql_messages("students in Sep 2026", "schema", EN)
    assert th[0] == en[0]  # one fixed system prompt
    assert "Answer language: Thai" in th[1]["content"]
    assert "Answer language: English" in en[1]["content"]
