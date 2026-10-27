"""The demo UI: sign in as user A or B, ask, see where every fact came from.

    streamlit run src/vantage/ui/app.py

The UI is a thin shell over `service.ask()` — it makes no access decision of
its own. Choosing a user here stands in for a login; the policy, the SQL
authorizer and the audit log behave exactly as they do from the CLI. Each
user's chat is kept separately, so switching to the Academic Director does not
put the CEO's answers on screen.

The UI chrome is in English; answers follow the language of the question.

Two pages:
- **Chat** — the answer (facts / analysis / limitations) above a citation panel
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

from vantage.generation import guardrails, language  # noqa: E402
from vantage.generation.answer import Citation, snippet  # noqa: E402
from vantage.observability import audit  # noqa: E402
from vantage.policy import list_principals  # noqa: E402
from vantage.service import Outcome, ask  # noqa: E402

st.set_page_config(page_title="Vantage", page_icon="🔎", layout="wide")

# decision → (badge text, badge colour)
_BADGES = {
    audit.ALLOWED: ("Answered · cited", "green"),
    audit.REFUSED: ("No data found", "orange"),
    audit.DENIED: ("Access denied", "red"),
    audit.ERROR: ("System error", "gray"),
}

# One-click demo questions, four per user: three from the golden set (Thai, like
# the documents) and one English question that was run end to end before going
# in — the answer comes back in the language of the question.
_EXAMPLES = {
    "ceo": [
        "ยอดนักเรียนรวมเดือน ก.ย. 2026 เท่าไร",
        "เป้าหมายนักเรียนใหม่ปี 2026 คือเท่าไร",
        "คู่แข่งรายใหญ่ของเราตั้งราคาเท่าไร",
        "What did the board decide about branch expansion?",
    ],
    "academic_director": [
        "อัตราสอบผ่านวิชาไหนต่ำสุดเทอม 1",
        "รายได้สาขาบางนาเดือน ส.ค. เท่าไร",
        "บอร์ดคุยเรื่องอะไรไตรมาส 2",
        "What was Bang Na's revenue in August?",
    ],
}

_ROUTES = {"sql": "SQL", "document": "documents"}

# The audit log stores UTC; the people reading it are in Thailand (no DST).
_DISPLAY_TZ = timezone(timedelta(hours=7), "ICT")


# ---------- Sign-in (stub identity provider) ----------


def _sidebar_user():
    principals = {p.id: p for p in list_principals()}
    user_id = st.sidebar.selectbox(
        "Signed in as",
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
        st.warning("Superseded — a newer version exists", icon="🕘")
    if c.sql:
        st.caption(f"Rows in the source file: {c.rows or 'not given'}")
    elif c.snippet:
        st.markdown(f"> {c.snippet}")


def _citation_panel(outcome: Outcome, lang: str) -> None:
    if outcome.answer is not None:
        st.markdown("**Sources**")
        for c in outcome.answer.cited:
            with st.expander(f"[{c.n}] {c.label}"):
                _citation_body(c)
        if outcome.sql:
            with st.expander("SQL used — every figure comes from this query's result"):
                st.code(outcome.sql, language="sql", wrap_lines=True)
        return

    # No answer: still show what was looked at, so a refusal is checkable too.
    if outcome.sql:
        with st.expander("SQL generated (not used for an answer)"):
            st.code(outcome.sql, language="sql", wrap_lines=True)
    if outcome.chunks:
        st.markdown("**Documents retrieved — none of them answers this question**")
        for i, chunk in enumerate(outcome.chunks, start=1):
            # Shown as the model saw it: injection lines replaced by the marker.
            with st.expander(f"[{i}] {chunk.label(lang)}"):
                st.markdown(f"> {snippet(guardrails.redact_text(chunk.content)[0])}")


def _meta_line(outcome: Outcome) -> str:
    parts = [f"Route: {_ROUTES.get(outcome.route, outcome.route or '-')}"]
    if outcome.model:
        parts.append(f"{outcome.model} · {outcome.input_tokens:,}+{outcome.output_tokens:,} tokens")
        parts.append(f"${outcome.cost_usd:.4f}")
    else:
        parts.append("no model call")
    parts.append(f"{outcome.latency_ms:,} ms")
    parts.append(f"audit #{outcome.audit_id}")
    return " · ".join(parts)


def _show_outcome(question: str, outcome: Outcome) -> None:
    label, colour = _BADGES.get(outcome.decision, (outcome.decision, "gray"))
    st.badge(label, color=colour)
    # Answer first, sources under it: side-by-side columns squeeze both on a
    # laptop screen, and a stacked layout reads the same at every width.
    if outcome.answer is not None:
        st.markdown(outcome.answer.render(sources=False))
    else:
        st.markdown(outcome.message or "")
    _citation_panel(outcome, language.of(question))
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
        "Answers only from the company's documents and tables — every fact names its file · "
        "version · page or rows · data date. All data is synthetic. Ask in Thai or English."
    )

    pending = st.chat_input(
        "Ask a question, e.g. How many students were enrolled in September 2026?"
    )
    examples = _EXAMPLES.get(principal.id, [])
    if examples:
        st.pills("Example questions", examples, key="example", on_change=_queue_example)
        pending = pending or st.session_state.pop("queued_question", None)

    for question, result in history:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            if isinstance(result, str):
                st.error(result)
            else:
                _show_outcome(question, result)

    if pending:
        with st.chat_message("user"):
            st.markdown(pending)
        with st.chat_message("assistant"):
            with st.spinner("Searching and checking sources…"):
                try:
                    result: Outcome | str = ask(principal.id, pending)
                except Exception as exc:  # noqa: BLE001 - shown to the user; already audited
                    result = (
                        f"System error ({type(exc).__name__}) — this request has been logged: {exc}"
                    )
            if isinstance(result, str):
                st.error(result)
            else:
                _show_outcome(pending, result)
        history.append((pending, result))


def audit_page() -> None:
    st.title("Audit log")
    st.caption(
        "Every question is logged, including the denied ones. Rows can only be added — never "
        "edited or deleted (enforced by a database trigger). Administrator's view."
    )
    rows = audit.recent(limit=500)
    if not rows:
        st.info("No questions yet")
        return

    users = sorted({r["principal"] for r in rows})
    decisions = [d for d in _BADGES if any(r["decision"] == d for r in rows)]
    c1, c2 = st.columns(2)
    pick_users = c1.multiselect("User", users, default=users)
    pick_decisions = c2.multiselect(
        "Decision", decisions, default=decisions, format_func=lambda d: _BADGES[d][0]
    )
    rows = [r for r in rows if r["principal"] in pick_users and r["decision"] in pick_decisions]

    st.dataframe(
        [
            {
                "#": r["id"],
                "Time (ICT)": r["ts"].astimezone(_DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "User": r["principal"],
                "ws": r["workspace"],
                "Decision": r["decision"],
                "Route": r["route"] or "",
                "Question": r["question"],
                "Reason": r["decision_reason"] or "",
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
        "Details",
        list(by_id),
        format_func=lambda i: f"#{i} · {by_id[i]['principal']} · {by_id[i]['question'][:60]}",
    )
    row = by_id[chosen]
    st.markdown(f"**Question:** {row['question']}")
    st.markdown(f"**Decision:** `{row['decision']}` — {row['decision_reason'] or ''}")
    if row["sql_executed"]:
        st.code(row["sql_executed"], language="sql", wrap_lines=True)
    if row["answer"]:
        with st.expander("Answer the user saw", expanded=True):
            st.markdown(row["answer"])
    if row["retrieved"]:
        with st.expander(f"Chunks retrieved ({len(row['retrieved'])})"):
            st.code(json.dumps(row["retrieved"], ensure_ascii=False, indent=1), language="json")


# ---------- Shell ----------

st.session_state["principal"] = _sidebar_user()
st.sidebar.divider()
navigation = st.navigation(
    [
        st.Page(chat_page, title="Chat", icon="💬", default=True),
        st.Page(audit_page, title="Audit log", icon="📜", url_path="audit"),
    ]
)
if st.sidebar.button("Clear this user's chat", width="stretch"):
    _history(st.session_state["principal"].id).clear()
    st.rerun()
navigation.run()
