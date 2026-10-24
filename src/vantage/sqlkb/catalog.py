"""Which tabular-KB tables a question is about.

Two jobs, one list:

1. **Deny up front.** A question that needs a table the principal cannot read is
   refused as *denied* — and logged as such — before anything runs. Without
   this, the Academic Director asking for Bang Na's August revenue would get a
   vague "not found", which hides from the audit log that someone asked for
   finance data from the wrong workspace.
2. **Narrow the schema** handed to text-to-SQL (the SQL route), so the model is
   only ever shown tables it is allowed to query.

Subjects are matched as substrings of the lower-cased question. That is coarse
on purpose: it errs towards *denying* a borderline question, which is the safe
direction, and every denial carries its reason into the audit log.
"""

from __future__ import annotations

from vantage.policy import Decision, Principal, authorize, describe_table

# table → words that mean a question needs it. Thai first; these documents are Thai.
TABLE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "finance_monthly": (
        "รายได้",
        "ต้นทุน",
        "ค่าใช้จ่าย",
        "ค่าการตลาด",
        "งบการตลาด",
        "กำไร",
        "การเงิน",
        "revenue",
        "cost",
        "marketing spend",
        "profit",
        "finance",
    ),
    "enrollment": (
        "นักเรียน",
        "ลงทะเบียน",
        "ผู้เรียน",
        "enrollment",
        "enrolment",
        "students",
    ),
}


def tables_for(question: str) -> list[str]:
    """Tables whose subject words appear in `question`, in catalog order."""
    q = question.lower()
    return [table for table, words in TABLE_SUBJECTS.items() if any(w in q for w in words)]


def check_tables(principal: Principal | None, question: str) -> dict[str, Decision]:
    """Authorize every table `question` refers to. Returns `{table: decision}`."""
    return {t: authorize(principal, "read", describe_table(t)) for t in tables_for(question)}
