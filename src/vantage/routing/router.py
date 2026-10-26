"""Which path answers a question: SQL over the tables, or RAG over the documents.

The rule that matters is *numbers come from SQL*. So a question goes to SQL
when it is quantitative **and** about a catalogued table's subject — "ยอด
นักเรียนรวมเดือน ก.ย." — and to the documents otherwise.

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
    "มากที่สุด",
    "น้อยที่สุด",
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
    quant = next((m for m in _QUANTITATIVE if m in q), None)
    if quant is None:
        return Route(DOCUMENT, tables, "table subject but no quantitative marker")
    return Route(SQL, tables, f"quantitative marker {quant!r} on {', '.join(tables)}")
