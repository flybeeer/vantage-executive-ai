"""The Streamlit UI, driven headlessly with AppTest. No database, no model.

`service.ask` is replaced by a recorder, so these test the UI's own behaviour:
what gets asked, and whose chat is on screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from vantage.observability import audit  # noqa: E402
from vantage.service import Outcome  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "src" / "vantage" / "ui" / "app.py"


@pytest.fixture
def asked(monkeypatch):
    """Record every question the UI sends; answer each with a refusal."""
    calls: list[tuple[str, str]] = []

    def fake_ask(user_id, question, settings=None):
        calls.append((user_id, question))
        return Outcome(audit.REFUSED, "test", "document", message=f"ไม่พบข้อมูล — {question}")

    monkeypatch.setattr("vantage.service.ask", fake_ask)
    return calls


def _app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def test_example_questions_can_be_asked_one_after_another(asked):
    at = _app()
    at.button_group(key="example").set_value("ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร").run()
    at.button_group(key="example").set_value("ยอดนักเรียนเดือน ธ.ค. 2026").run()

    assert [q for _, q in asked] == [
        "ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร",
        "ยอดนักเรียนเดือน ธ.ค. 2026",
    ]
    # A plain rerun must not ask the last example again.
    at.run()
    assert len(asked) == 2


def test_english_examples_are_offered_and_asked_as_written(asked):
    at = _app()
    question = "How many students were enrolled in September 2026?"
    assert question in at.button_group(key="example").options
    at.button_group(key="example").set_value(question).run()
    assert asked == [("ceo", question)]


def test_chat_input_is_asked_as_the_signed_in_user(asked):
    at = _app()
    at.chat_input[0].set_value("คู่แข่งรายใหญ่ของเราตั้งราคาเท่าไร").run()
    assert asked == [("ceo", "คู่แข่งรายใหญ่ของเราตั้งราคาเท่าไร")]
    assert any("ไม่พบข้อมูล" in m.value for m in at.markdown)


def test_the_ui_chrome_is_english(asked):
    # Thai belongs in questions and answers only, never in the UI's own labels.
    at = _app()
    at.chat_input[0].set_value("What do our main competitors charge?").run()
    chrome = [at.title[0].value, *(c.value for c in at.caption), at.chat_input[0].placeholder]
    chrome += [at.selectbox(key="user_id").label, at.button_group(key="example").label]
    chrome += [b.value for b in at.get("badge")]
    chrome += [b.label for b in at.sidebar.button]
    thai = [s for s in chrome if any("฀" <= ch <= "๿" for ch in str(s))]
    assert thai == []


def test_switching_user_does_not_show_the_previous_users_chat(asked):
    at = _app()
    at.chat_input[0].set_value("ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร").run()
    at.selectbox(key="user_id").set_value("academic_director").run()

    assert not any("ยอดนักเรียนรวมเดือน ก.ย. 2026" in m.value for m in at.markdown)
    at.chat_input[0].set_value("อัตราสอบผ่านวิชาไหนต่ำสุดเทอม 1").run()
    assert asked[-1] == ("academic_director", "อัตราสอบผ่านวิชาไหนต่ำสุดเทอม 1")
