"""The answer path: one question in, one audited outcome out.

    user_id ─► resolve_principal ─► table check ─► hybrid retrieval ─► audit row

Every exit writes exactly one audit row — unknown user, denied table, retrieval
result, or a crash part-way through. The denials are the rows most worth
keeping, so they are not a special case.

Generation, the SQL route and the refusal threshold slot in between retrieval
and the audit write; until they do, an allowed question returns its cited
chunks and no prose answer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from vantage.config import Settings, get_settings
from vantage.observability import audit
from vantage.policy import resolve_principal
from vantage.retrieval import RetrievedChunk, retrieve
from vantage.sqlkb.catalog import check_tables

logger = logging.getLogger(__name__)

DOCUMENT_ROUTE = "document"
SQL_ROUTE = "sql"

# What a denied user is told. Says *that* they were refused and that it was
# logged, never what the refused asset contains.
_DENIED_MESSAGE = "ไม่มีสิทธิ์เข้าถึงข้อมูลที่คำถามนี้ต้องใช้ ({assets}) — คำขอนี้ถูกบันทึกไว้แล้ว"
_UNKNOWN_USER_MESSAGE = "ไม่พบผู้ใช้นี้ในระบบ — คำขอนี้ถูกบันทึกไว้แล้ว"


@dataclass(frozen=True)
class Outcome:
    decision: str  # audit.ALLOWED | DENIED | REFUSED
    reason: str
    route: str | None
    message: str | None = None  # what the user is shown, when there is no answer
    chunks: list[RetrievedChunk] = field(default_factory=list)
    audit_id: int | None = None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def ask(user_id: str, question: str, settings: Settings | None = None) -> Outcome:
    """Answer `question` as `user_id`, writing one audit row whatever happens."""
    settings = settings or get_settings()
    start = time.perf_counter()

    principal = resolve_principal(user_id)
    if principal is None:
        reason = f"deny-by-default: unknown principal {user_id!r}"
        audit_id = audit.record(
            audit.AuditEntry(
                principal=user_id,
                workspace="-",
                question=question,
                decision=audit.DENIED,
                decision_reason=reason,
                answer=_UNKNOWN_USER_MESSAGE,
                latency_ms=_elapsed_ms(start),
            )
        )
        return Outcome(audit.DENIED, reason, None, _UNKNOWN_USER_MESSAGE, audit_id=audit_id)

    base = {
        "principal": principal.id,
        "workspace": principal.workspace_label,
        "question": question,
    }

    # A question about data this principal cannot read is denied before any
    # retrieval runs, and the denial names the asset in the audit log.
    denied = {t: d for t, d in check_tables(principal, question).items() if not d.allowed}
    if denied:
        reason = "; ".join(d.reason for d in denied.values())
        message = _DENIED_MESSAGE.format(assets=", ".join(denied))
        audit_id = audit.record(
            audit.AuditEntry(
                **base,
                decision=audit.DENIED,
                decision_reason=reason,
                route=SQL_ROUTE,
                answer=message,
                latency_ms=_elapsed_ms(start),
            )
        )
        return Outcome(audit.DENIED, reason, SQL_ROUTE, message, audit_id=audit_id)

    try:
        result = retrieve(question, principal, settings)
    except Exception as exc:
        audit.record(
            audit.AuditEntry(
                **base,
                decision=audit.ERROR,
                decision_reason=f"{type(exc).__name__}: {exc}",
                route=DOCUMENT_ROUTE,
                latency_ms=_elapsed_ms(start),
            )
        )
        raise

    reason = f"{result.access.reason}; {result.candidates} candidates"
    if result.notes:
        reason += "; " + "; ".join(result.notes)
    audit_id = audit.record(
        audit.AuditEntry(
            **base,
            decision=audit.ALLOWED,
            decision_reason=reason,
            route=DOCUMENT_ROUTE,
            retrieved=[c.audit_ref() for c in result.chunks],
            latency_ms=_elapsed_ms(start),
        )
    )
    return Outcome(audit.ALLOWED, reason, DOCUMENT_ROUTE, chunks=result.chunks, audit_id=audit_id)
