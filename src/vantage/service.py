"""The answer path: one question in, one audited outcome out.

    user_id ─► principal ─► injection screen ─► table check ─► route ─┬─► SQL route
                                                                     └─► document route
                                                                            ─► audit row

    SQL:      coverage ─► text-to-SQL ─► authorizer ─► phrase ─► grounding check
    document: retrieval ─► evidence floor ─► redact ─► generate ─► citation check

Every exit writes exactly one audit row — unknown user, injection, denied
table, refusal, answer, or a crash part-way through. The denials and refusals
are the rows most worth keeping, so they are not a special case.

Decisions:
- `denied`  — the policy (or the injection screen) refused; nothing was read.
- `refused` — access was fine but the evidence was not there; the user is told
  "ไม่พบข้อมูล" and what is missing.
- `allowed` — answered, every fact cited.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from vantage.config import Settings, get_settings
from vantage.generation import guardrails
from vantage.generation.answer import Answer, not_found
from vantage.generation.generator import answer_documents
from vantage.llm.gateway import Completion
from vantage.observability import audit
from vantage.policy import resolve_principal
from vantage.retrieval import RetrievedChunk, retrieve
from vantage.routing import router
from vantage.routing.pricing import estimate_cost
from vantage.sqlkb.answer import answer_sql
from vantage.sqlkb.catalog import check_tables

logger = logging.getLogger(__name__)

DOCUMENT_ROUTE = router.DOCUMENT
SQL_ROUTE = router.SQL

# What a denied user is told. Says *that* they were refused and that it was
# logged, never what the refused asset contains.
_DENIED_MESSAGE = "ไม่มีสิทธิ์เข้าถึงข้อมูลที่คำถามนี้ต้องใช้ ({assets}) — คำขอนี้ถูกบันทึกไว้แล้ว"
_UNKNOWN_USER_MESSAGE = "ไม่พบผู้ใช้นี้ในระบบ — คำขอนี้ถูกบันทึกไว้แล้ว"
_INJECTION_MESSAGE = (
    "คำถามนี้มีข้อความที่สั่งให้ระบบเปลี่ยนกฎหรือสิทธิ์การเข้าถึง จึงไม่ได้ดำเนินการ — "
    "คำขอนี้ถูกบันทึกไว้แล้ว"
)


@dataclass(frozen=True)
class Outcome:
    decision: str  # audit.ALLOWED | DENIED | REFUSED
    reason: str
    route: str | None
    message: str | None = None  # what the user is shown
    answer: Answer | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)
    sql: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    audit_id: int | None = None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _usage(calls: list[Completion]) -> dict:
    return {
        "model": calls[-1].model if calls else None,
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "cost_usd": round(
            sum(estimate_cost(c.model, c.input_tokens, c.output_tokens) for c in calls), 6
        ),
    }


def _finish(start: float, entry: audit.AuditEntry, **outcome) -> Outcome:
    """Write the audit row for `entry` and return the matching Outcome."""
    entry.latency_ms = _elapsed_ms(start)
    audit_id = audit.record(entry)
    return Outcome(
        decision=entry.decision,
        reason=entry.decision_reason or "",
        route=entry.route,
        message=entry.answer,
        sql=entry.sql_executed,
        model=entry.model,
        input_tokens=entry.input_tokens,
        output_tokens=entry.output_tokens,
        cost_usd=entry.cost_usd,
        latency_ms=entry.latency_ms,
        audit_id=audit_id,
        **outcome,
    )


def ask(user_id: str, question: str, settings: Settings | None = None) -> Outcome:
    """Answer `question` as `user_id`, writing one audit row whatever happens."""
    settings = settings or get_settings()
    start = time.perf_counter()

    principal = resolve_principal(user_id)
    if principal is None:
        entry = audit.AuditEntry(
            principal=user_id,
            workspace="-",
            question=question,
            decision=audit.DENIED,
            decision_reason=f"deny-by-default: unknown principal {user_id!r}",
            answer=_UNKNOWN_USER_MESSAGE,
        )
        return _finish(start, entry)

    def entry(decision: str, reason: str, route: str | None = None, **kw) -> audit.AuditEntry:
        return audit.AuditEntry(
            principal=principal.id,
            workspace=principal.workspace_label,
            question=question,
            decision=decision,
            decision_reason=reason,
            route=route,
            **kw,
        )

    if settings.enable_injection_check:
        injected = guardrails.injection_reason(question)
        if injected:
            return _finish(
                start,
                entry(audit.DENIED, f"injection screen: {injected}", answer=_INJECTION_MESSAGE),
            )

    # A question about data this principal cannot read is denied before any
    # retrieval runs, and the denial names the asset in the audit log.
    denied = {t: d for t, d in check_tables(principal, question).items() if not d.allowed}
    if denied:
        message = _DENIED_MESSAGE.format(assets=", ".join(denied))
        reason = "; ".join(d.reason for d in denied.values())
        return _finish(start, entry(audit.DENIED, reason, SQL_ROUTE, answer=message))

    chosen = router.route(question)
    calls: list[Completion] = []
    try:
        if chosen.path == SQL_ROUTE:
            result = answer_sql(question, principal, chosen.tables, settings)
            calls = result.calls
            reason = "; ".join([f"route: {chosen.reason}", *result.notes])
            usage = _usage(calls)
            if result.refused:
                message = not_found(result.missing)
                return _finish(
                    start,
                    entry(
                        audit.REFUSED,
                        reason,
                        SQL_ROUTE,
                        sql_executed=result.sql,
                        answer=message,
                        **usage,
                    ),
                )
            return _finish(
                start,
                entry(
                    audit.ALLOWED,
                    reason,
                    SQL_ROUTE,
                    sql_executed=result.sql,
                    answer=result.answer.render(),
                    **usage,
                ),
                answer=result.answer,
            )

        retrieval = retrieve(question, principal, settings)
        notes = [f"route: {chosen.reason}", retrieval.access.reason]
        notes.append(f"{retrieval.candidates} candidates")
        notes += retrieval.notes
        retrieved = [c.audit_ref() for c in retrieval.chunks]

        result = answer_documents(question, retrieval.chunks, settings)
        calls = result.calls
        reason = "; ".join(notes + result.notes)
        usage = _usage(calls)
        if result.refused:
            return _finish(
                start,
                entry(
                    audit.REFUSED,
                    reason,
                    DOCUMENT_ROUTE,
                    retrieved=retrieved,
                    answer=not_found(result.missing),
                    **usage,
                ),
                chunks=retrieval.chunks,
            )
        return _finish(
            start,
            entry(
                audit.ALLOWED,
                reason,
                DOCUMENT_ROUTE,
                retrieved=retrieved,
                answer=result.answer.render(),
                **usage,
            ),
            answer=result.answer,
            chunks=retrieval.chunks,
        )
    except Exception as exc:
        audit.record(
            entry(
                audit.ERROR,
                f"{type(exc).__name__}: {exc}",
                chosen.path,
                latency_ms=_elapsed_ms(start),
                **_usage(calls),
            )
        )
        raise
