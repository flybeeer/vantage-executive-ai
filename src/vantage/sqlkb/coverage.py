"""Does the data cover the period the question asks about?

Checked *before* any SQL is generated. "ยอดนักเรียนเดือน ธ.ค. 2026" against a
table that ends in September has one honest answer — the data stops in
September — and a model handed that question and a schema will often produce a
query that returns the latest month instead, or an empty result it then
phrases as zero. A deterministic check removes both failure modes and names
the gap in the refusal.

Recognised: Thai month names and abbreviations (ม.ค. … ธ.ค.), English month
names, quarters (Q3 2026, ไตรมาส 3/2569), and bare years (ปี 2026). Years may
be CE or Buddhist Era (BE = CE + 543). A month with no year is not checked —
guessing the year would be the very thing this module exists to prevent.

"ปีนี้" / "this year" is not a guess, but it is relative to *when*: the system's
"now" is the date of its data, not the server clock, so `pin_this_year`
rewrites it to the year of the latest data before anything else reads the
question — this check, and the model writing the SQL (which would otherwise
reach for `date('now')`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BE_OFFSET = 543

# Longest first, so "มี.ค." is not read as "ม.ค." and "มิถุนายน" before "มิ.ย.".
_THAI_MONTHS: list[tuple[str, int]] = sorted(
    [
        ("มกราคม", 1), ("กุมภาพันธ์", 2), ("มีนาคม", 3), ("เมษายน", 4),
        ("พฤษภาคม", 5), ("มิถุนายน", 6), ("กรกฎาคม", 7), ("สิงหาคม", 8),
        ("กันยายน", 9), ("ตุลาคม", 10), ("พฤศจิกายน", 11), ("ธันวาคม", 12),
        ("ม.ค.", 1), ("ก.พ.", 2), ("มี.ค.", 3), ("เม.ย.", 4), ("พ.ค.", 5), ("มิ.ย.", 6),
        ("ก.ค.", 7), ("ส.ค.", 8), ("ก.ย.", 9), ("ต.ค.", 10), ("พ.ย.", 11), ("ธ.ค.", 12),
    ],
    key=lambda item: -len(item[0]),
)  # fmt: skip
_EN_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip

_YEAR = r"(?P<year>(?:20|25)\d{2})"
_MONTH_ALT = "|".join(re.escape(name) for name, _ in _THAI_MONTHS)
_THAI_MONTH_RE = re.compile(rf"(?P<month>{_MONTH_ALT})\s*(?:ปี\s*|พ\.ศ\.\s*|ค\.ศ\.\s*)?{_YEAR}?")
_EN_MONTH_RE = re.compile(
    rf"\b(?P<month>{'|'.join(sorted(_EN_MONTHS, key=len, reverse=True))})\.?\s*,?\s*{_YEAR}?\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    rf"(?:\bQ\s*(?P<q1>[1-4])|ไตรมาส(?:ที่)?\s*(?P<q2>[1-4]))\s*(?:/|ปี|of)?\s*{_YEAR}?",
    re.IGNORECASE,
)
_YEAR_ONLY_RE = re.compile(rf"(?:ปี|year)\s*(?:พ\.ศ\.\s*|ค\.ศ\.\s*)?{_YEAR}", re.IGNORECASE)
_THIS_YEAR_RE = re.compile(r"(?P<th>ปีนี้)|(?P<en>\bthis year\b)", re.IGNORECASE)


def pin_this_year(question: str, year: int) -> str:
    """'ปีนี้' / 'this year' → an explicit year: the year of the latest data."""
    return _THIS_YEAR_RE.sub(lambda m: f"ปี {year}" if m["th"] else f"year {year}", question)


def _ce(year: str) -> int:
    y = int(year)
    return y - _BE_OFFSET if y >= 2400 else y


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def requested_months(question: str) -> list[str]:
    """Every 'YYYY-MM' the question names explicitly, in order, de-duplicated."""
    months: list[str] = []
    spans: list[tuple[int, int]] = []

    def add(year: int, ms: range | list[int], span: tuple[int, int]) -> None:
        spans.append(span)
        for m in ms:
            ym = _ym(year, m)
            if ym not in months:
                months.append(ym)

    for match in _THAI_MONTH_RE.finditer(question):
        if match.group("year"):
            month = dict(_THAI_MONTHS)[match.group("month")]
            add(_ce(match.group("year")), [month], match.span())
    for match in _EN_MONTH_RE.finditer(question):
        if match.group("year"):
            month = _EN_MONTHS[match.group("month").lower()]
            add(_ce(match.group("year")), [month], match.span())
    for match in _QUARTER_RE.finditer(question):
        if match.group("year"):
            q = int(match.group("q1") or match.group("q2"))
            add(_ce(match.group("year")), range(3 * q - 2, 3 * q + 1), match.span())
    # A bare year counts only when no month or quarter already pinned that year
    # down: "ก.ย. ปี 2026" is about September, not the whole of 2026.
    for match in _YEAR_ONLY_RE.finditer(question):
        start, end = match.span()
        if any(s <= start < e or s < end <= e for s, e in spans):
            continue
        year = _ce(match.group("year"))
        if any(m.startswith(f"{year:04d}-") for m in months):
            continue
        add(year, range(1, 13), match.span())
    return months


@dataclass(frozen=True)
class Coverage:
    """How a question's requested period relates to what a table holds."""

    requested: tuple[str, ...]
    first: str
    last: str

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(m for m in self.requested if not self.first <= m <= self.last)

    @property
    def none_covered(self) -> bool:
        """Every requested month is outside the data — refuse."""
        return bool(self.requested) and len(self.missing) == len(self.requested)

    @property
    def partly_covered(self) -> bool:
        """Some months exist, some don't — answer, and say which are missing."""
        return bool(self.missing) and not self.none_covered


def check(question: str, first: str, last: str) -> Coverage:
    return Coverage(tuple(requested_months(question)), first, last)
