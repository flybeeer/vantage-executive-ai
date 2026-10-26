"""The demo UI: sign in as user A or B, ask, see where every fact came from.

    streamlit run src/vantage/ui/app.py

The UI is a thin shell over `service.ask()` — it makes no access decision of
its own. Choosing a user here stands in for a login; the policy, the SQL
authorizer and the audit log behave exactly as they do from the CLI. Each
user's chat is kept separately, so switching to the Academic Director does not
put the CEO's answers on screen.

Two pages:
- **แชท** — the answer (facts / analysis / limitations) above a citation panel
  that shows each source: the chunk text, or the SQL and spreadsheet rows.
- **Audit log** — every question asked, including denials and refusals. An
  administrator's view; a real deployment would restrict it to that role.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta, timezone
from pathlib import Path

import streamlit as st

# `streamlit run` executes this file as a script, not as part of the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vantage.generation import guardrails  # noqa: E402
from vantage.generation.answer import Citation, snippet  # noqa: E402
from vantage.observability import audit  # noqa: E402
from vantage.policy import list_principals  # noqa: E402
from vantage.service import Outcome, ask  # noqa: E402

st.set_page_config(page_title="Vantage", page_icon="🔎", layout="wide")

# decision → (badge text, badge colour)
_BADGES = {
    audit.ALLOWED: ("ตอบได้ · มีแหล่งอ้างอิง", "green"),
    audit.REFUSED: ("ไม่พบข้อมูล", "orange"),
    audit.DENIED: ("ไม่มีสิทธิ์เข้าถึง", "red"),
    audit.ERROR: ("ระบบขัดข้อง", "gray"),
}

# Golden-set questions, so a demo can be driven with one click.
_EXAMPLES = {
    "ceo": [
        "ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร",
        "เป้าหมายนักเรียนใหม่ปี 2026 คือเท่าไร",
        "สาขาไหนรายได้โตสูงสุด Q3 2026 เทียบ Q2",
        "ยอดนักเรียนเดือน ธ.ค. 2026",
        "คู่แข่งรายใหญ่ของเราตั้งราคาเท่าไร",
    ],
    "academic_director": [
        "อัตราสอบผ่านวิชาไหนต่ำสุดเทอม 1",
        "รายได้สาขาบางนาเดือน ส.ค. เท่าไร",
        "บอร์ดคุยเรื่องอะไรไตรมาส 2",
    ],
}

_ROUTES = {"sql": "SQL", "document": "เอกสาร"}

# The audit log stores UTC; the people reading it are in Thailand (no DST).
_DISPLAY_TZ = timezone(timedelta(hours=7), "ICT")


# ---------- Sign-in (stub identity provider) ----------


def _sidebar_user():
    principals = {p.id: p for p in list_principals()}
    user_id = st.sidebar.selectbox(
        "เข้าสู่ระบบในฐานะ",
        list(principals),
        format_func=lambda uid: principals[uid].display_name,
        key="user_id",
    )
    principal = principals[user_id]
    st.sidebar.caption(
        f"Workspace **{principal.workspace_label}** · role {', '.join(principal.roles)}"
    )
    return principal


def _history(user_id: str) -> list[tuple[str, Outcome | str]]:
    """This user's chat. An entry is (question, Outcome) or (question, error text)."""
    return st.session_state.setdefault("history", {}).setdefault(user_id, [])


# ---------- Rendering one answer ----------


def _citation_body(c: Citation) -> None:
    if not c.is_current:
        st.warning("ฉบับเก่า — มีฉบับใหม่กว่าแทนที่แล้ว", icon="🕘")
    if c.sql:
        st.caption(f"แถวในไฟล์ต้นทาง: {c.rows or 'ไม่ระบุ'}")
    elif c.snippet:
        st.markdown(f"> {c.snippet}")


def _citation_panel(outcome: Outcome) -> None:
    if outcome.answer is not None:
        st.markdown("**แหล่งอ้างอิง**")
        for c in outcome.answer.cited:
            with st.expander(f"[{c.n}] {c.label}"):
                _citation_body(c)
        if outcome.sql:
            with st.expander("SQL ที่ใช้คำนวณ — ตัวเลขทุกตัวมาจากผลลัพธ์ของคำสั่งนี้"):
                st.code(outcome.sql, language="sql", wrap_lines=True)
        return

    # No answer: still show what was looked at, so a refusal is checkable too.
    if outcome.sql:
        with st.expander("SQL ที่ระบบสร้าง (ไม่ได้ใช้ตอบ)"):
            st.code(outcome.sql, language="sql", wrap_lines=True)
    if outcome.chunks:
        st.markdown("**เอกสารที่ค้นพบ — ไม่มีส่วนใดตอบคำถามนี้ได้**")
        for i, chunk in enumerate(outcome.chunks, start=1):
            # Shown as the model saw it: injection lines replaced by the marker.
            with st.expander(f"[{i}] {chunk.citation}"):
                st.markdown(f"> {snippet(guardrails.redact_text(chunk.content)[0])}")


def _meta_line(outcome: Outcome) -> str:
    parts = [f"เส้นทาง: {_ROUTES.get(outcome.route, outcome.route or '-')}"]
    if outcome.model:
        parts.append(f"{outcome.model} · {outcome.input_tokens:,}+{outcome.output_tokens:,} tokens")
        parts.append(f"${outcome.cost_usd:.4f}")
    else:
        parts.append("ไม่ได้เรียกโมเดล")
    parts.append(f"{outcome.latency_ms:,} ms")
    parts.append(f"audit #{outcome.audit_id}")
    return " · ".join(parts)


def _show_outcome(outcome: Outcome) -> None:
    label, colour = _BADGES.get(outcome.decision, (outcome.decision, "gray"))
    st.badge(label, color=colour)
    # Answer first, sources under it: side-by-side columns squeeze both on a
    # laptop screen, and a stacked layout reads the same at every width.
    if outcome.answer is not None:
        st.markdown(outcome.answer.render(sources=False))
    else:
        st.markdown(outcome.message or "")
    _citation_panel(outcome)
    st.caption(_meta_line(outcome))


# ---------- Pages ----------


def _queue_example() -> None:
    """Move a clicked example into the queue and clear the pills.

    Read in a callback rather than from the widget's return value: the pills
    must be empty again on the next run, or the same example would be asked
    on every rerun, and only a callback may reset a widget's state.
    """
    st.session_state["queued_question"] = st.session_state["example"]
    st.session_state["example"] = None


def chat_page() -> None:
    principal = st.session_state["principal"]
    history = _history(principal.id)

    st.title("Vantage")
    st.caption(
        "ตอบจากเอกสารและตารางของบริษัทเท่านั้น — ทุกข้อเท็จจริงระบุไฟล์ · ฉบับ · หน้าหรือแถว · "
        "วันที่ข้อมูล · ข้อมูลทั้งหมดเป็นข้อมูลสังเคราะห์"
    )

    pending = st.chat_input("ถามคำถาม เช่น ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร")
    examples = _EXAMPLES.get(principal.id, [])
    if examples:
        st.pills("ตัวอย่างคำถาม", examples, key="example", on_change=_queue_example)
        pending = pending or st.session_state.pop("queued_question", None)

    for question, result in history:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            if isinstance(result, str):
                st.error(result)
            else:
                _show_outcome(result)

    if pending:
        with st.chat_message("user"):
            st.markdown(pending)
        with st.chat_message("assistant"):
            with st.spinner("กำลังค้นหาและตรวจสอบแหล่งที่มา…"):
                try:
                    result: Outcome | str = ask(principal.id, pending)
                except Exception as exc:  # noqa: BLE001 - shown to the user; already audited
                    result = f"ระบบขัดข้อง ({type(exc).__name__}) — คำขอนี้ถูกบันทึกไว้แล้ว: {exc}"
            if isinstance(result, str):
                st.error(result)
            else:
                _show_outcome(result)
        history.append((pending, result))


def audit_page() -> None:
    st.title("Audit log")
    st.caption(
        "ทุกคำถามถูกบันทึก รวมถึงคำถามที่ถูกปฏิเสธ — ตารางนี้เพิ่มได้อย่างเดียว "
        "แก้ไขหรือลบไม่ได้ (บังคับด้วย trigger ในฐานข้อมูล) · มุมมองของผู้ดูแลระบบ"
    )
    rows = audit.recent(limit=500)
    if not rows:
        st.info("ยังไม่มีคำถามในระบบ")
        return

    users = sorted({r["principal"] for r in rows})
    decisions = [d for d in _BADGES if any(r["decision"] == d for r in rows)]
    c1, c2 = st.columns(2)
    pick_users = c1.multiselect("ผู้ใช้", users, default=users)
    pick_decisions = c2.multiselect(
        "ผลการตัดสิน", decisions, default=decisions, format_func=lambda d: _BADGES[d][0]
    )
    rows = [r for r in rows if r["principal"] in pick_users and r["decision"] in pick_decisions]

    st.dataframe(
        [
            {
                "#": r["id"],
                "เวลา (ICT)": r["ts"].astimezone(_DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "ผู้ใช้": r["principal"],
                "ws": r["workspace"],
                "ผล": r["decision"],
                "เส้นทาง": r["route"] or "",
                "คำถาม": r["question"],
                "เหตุผล": r["decision_reason"] or "",
                "chunks": len(r["retrieved"] or []),
                "SQL": "✓" if r["sql_executed"] else "",
                "USD": round(r["cost_usd"], 4),
                "ms": r["latency_ms"],
            }
            for r in rows
        ],
        hide_index=True,
        width="stretch",
    )

    if not rows:
        return
    by_id = {r["id"]: r for r in rows}
    chosen = st.selectbox(
        "ดูรายละเอียด",
        list(by_id),
        format_func=lambda i: f"#{i} · {by_id[i]['principal']} · {by_id[i]['question'][:60]}",
    )
    row = by_id[chosen]
    st.markdown(f"**คำถาม:** {row['question']}")
    st.markdown(f"**ผล:** `{row['decision']}` — {row['decision_reason'] or ''}")
    if row["sql_executed"]:
        st.code(row["sql_executed"], language="sql", wrap_lines=True)
    if row["answer"]:
        with st.expander("คำตอบที่ผู้ใช้เห็น", expanded=True):
            st.markdown(row["answer"])
    if row["retrieved"]:
        with st.expander(f"chunks ที่ค้นได้ ({len(row['retrieved'])})"):
            st.code(json.dumps(row["retrieved"], ensure_ascii=False, indent=1), language="json")


# ---------- Shell ----------

st.session_state["principal"] = _sidebar_user()
st.sidebar.divider()
navigation = st.navigation(
    [
        st.Page(chat_page, title="แชท", icon="💬", default=True),
        st.Page(audit_page, title="Audit log", icon="📜", url_path="audit"),
    ]
)
if st.sidebar.button("ล้างแชทของผู้ใช้นี้", width="stretch"):
    _history(st.session_state["principal"].id).clear()
    st.rerun()
navigation.run()
