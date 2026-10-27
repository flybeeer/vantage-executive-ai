"""Which path answers a question: SQL over the tables, or RAG over the documents.

The rule that matters is *numbers come from SQL*. So a question about a
catalogued table's subject goes to SQL when it asks for a quantity — "ยอด
นักเรียนรวมเดือน ก.ย. เท่าไร" — **or** names a slice of the table: a month,
quarter, year or branch — "กำไรขั้นต้นสาขาขอนแก่นไตรมาส 3". People ask for a
figure without saying "how much"; a metric for a branch or a period is a row
in a table either way. Everything else goes to the documents.

Two document markers override that: targets, plans, decisions and reports are
things written in documents, not rows in a table. "เป้าหมายนักเรียนใหม่ปี 2026
คือเท่าไร" mentions students and asks "how many", but the number is a *target*
in the strategy plan; there is no target column to sum. Sending it to SQL would
answer a different question (actual intake) with total confidence.

Keyword rules, deliberately: the routing decision is logged with the rule that
fired, and a reviewer can read this file and predict every route.
"""

from __future__ import annotations

from dataclasses import dataclass

from vantage.sqlkb.catalog import tables_for
from vantage.sqlkb.coverage import requested_months

DOCUMENT = "document"
SQL = "sql"

_QUANTITATIVE = (
    "เท่าไร",
    "เท่าไหร่",
    "กี่",
    "ยอด",
    "รวม",
    "จำนวน",
    "เฉลี่ย",
    "สูงสุด",
    "ต่ำสุด",
    "ที่สุด",
    "มากสุด",
    "น้อยสุด",
    "เยอะ",
    "โต",
    "เทียบ",
    "ต่อ",
    "แต่ละ",
    "สัดส่วน",
    "เปอร์เซ็นต์",
    "%",
    "how many",
    "how much",
    "total",
    "average",
    "per ",
    "growth",
    "highest",
    "lowest",
    "most",
    "least",
)

# The dimensions the tables are sliced by. Naming one alongside a table subject
# ("รายได้สาขาบางนา", "รายได้เดือนธันวาคม") asks for rows, not prose.
_SLICES = (
    "เดือน",
    "ไตรมาส",
    "ปี",
    "สาขา",
    "month",
    "quarter",
    "year",
    "branch",
)

# Things that live in documents even when they carry a number.
_DOCUMENT_MARKERS = (
    "เป้าหมาย",
    "เป้า",
    "แผน",
    "บอร์ด",
    "คณะกรรมการ",
    "มติ",
    "ประชุม",
    "รายงาน",
    "ความเสี่ยง",
    "นโยบาย",
    "ตัดสินใจ",
    "target",
    "plan",
    "board",
    "report",
    "risk",
)


@dataclass(frozen=True)
class Route:
    path: str  # SQL | DOCUMENT
    tables: tuple[str, ...]
    reason: str


def route(question: str) -> Route:
    q = question.lower()
    tables = tuple(tables_for(question))
    if not tables:
        return Route(DOCUMENT, (), "no table subject in the question")
    marker = next((m for m in _DOCUMENT_MARKERS if m in q), None)
    if marker:
        return Route(DOCUMENT, tables, f"document marker {marker!r}")
    on = ", ".join(tables)
    quant = next((m for m in _QUANTITATIVE if m in q), None)
    if quant:
        return Route(SQL, tables, f"quantitative marker {quant!r} on {on}")
    piece = next((s for s in _SLICES if s in q), None)
    if piece:
        return Route(SQL, tables, f"names a slice {piece!r} of {on}")
    # "December 2026", "Q3 2026": a dated period, in either language.
    months = requested_months(question)
    if months:
        return Route(SQL, tables, f"names a period {months[0]}… of {on}")
    return Route(DOCUMENT, tables, "table subject but no quantity or slice asked for")
