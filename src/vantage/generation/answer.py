"""The answer: facts (each cited) / analysis / limitations, checked before it is shown.

The model's JSON is not trusted as-is. Before an answer reaches the user:

- a fact must cite at least one source that was actually given to the model;
- every number in a fact must appear in the text of what it cites (the SQL
  result rows, or the cited chunks). A model that "helpfully" totals a column
  or converts 12 ล้าน into 12,000,000 has produced a number nobody can trace,
  and that fact is dropped — or, on the SQL route, the phrasing is discarded
  and the result rows are shown as they are.

Whatever a check removes is recorded in `notes`, which goes to the audit log.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

_BE_OFFSET = 543
_SNIPPET_CHARS = 280

NOT_FOUND = "ไม่พบข้อมูล"
_MARKER_RE = re.compile(r"\[(\d+)\]")


class AnswerFormatError(ValueError):
    """The model's reply was not the JSON object the prompt asked for."""


@dataclass(frozen=True)
class Citation:
    """One source an answer may cite: a document chunk or a SQL table read."""

    n: int
    label: str  # file · version · page/rows · data date
    source: str
    doc_version: str
    data_date: date | None
    is_current: bool = True
    page: int | None = None
    rows: str | None = None  # compressed spreadsheet rows, e.g. "402–421"
    sql: str | None = None
    snippet: str = ""
    chunk_id: int | None = None


@dataclass(frozen=True)
class Fact:
    text: str
    sources: tuple[int, ...]


@dataclass
class Answer:
    facts: list[Fact]
    analysis: str
    limitations: list[str]
    citations: list[Citation]
    notes: list[str] = field(default_factory=list)

    @property
    def cited(self) -> list[Citation]:
        """Citations a fact relies on or the analysis marks as `[n]`, in number order."""
        used = {n for f in self.facts for n in f.sources}
        used |= {int(n) for n in _MARKER_RE.findall(self.analysis)}
        return [c for c in self.citations if c.n in used]

    def render(self, *, sources: bool = True) -> str:
        """The user-facing Thai answer (Markdown).

        `sources=False` leaves out the reference list, for a UI that shows the
        citations in a panel of its own.
        """
        parts = ["**ข้อเท็จจริง**"]
        for fact in self.facts:
            marks = "".join(f"[{n}]" for n in fact.sources)
            parts.append(f"- {fact.text} {marks}".rstrip())
        if self.analysis:
            parts += ["", "**บทวิเคราะห์**", self.analysis]
        if self.limitations:
            parts += ["", "**ข้อจำกัด**", *(f"- {item}" for item in self.limitations)]
        if not sources:
            return "\n".join(parts)
        parts += ["", "**แหล่งอ้างอิง**"]
        shown_sql: set[str] = set()
        for c in self.cited:
            parts.append(f"[{c.n}] {c.label}")
        # One query reading two tables is one query: print it once, after its tables.
        for c in self.cited:
            if c.sql and c.sql not in shown_sql:
                shown_sql.add(c.sql)
                parts.append(f"    SQL: {c.sql}")
        return "\n".join(parts)


def not_found(missing: str) -> str:
    """The refusal text: says *that* nothing was found and *what* is missing."""
    missing = missing.strip()
    return f"{NOT_FOUND} — {missing}" if missing else NOT_FOUND


def snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _SNIPPET_CHARS else flat[:_SNIPPET_CHARS] + "…"


def compress_rows(rows: list[int], max_ranges: int = 8) -> str:
    """[402, 403, …, 421, 430] → "402–421, 430"."""
    ordered = sorted(set(rows))
    if not ordered:
        return ""
    ranges: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for r in ordered[1:]:
        if r != prev + 1:
            ranges.append((start, prev))
            start = r
        prev = r
    ranges.append((start, prev))
    text = ", ".join(f"{a}–{b}" if a != b else str(a) for a, b in ranges[:max_ranges])
    if len(ranges) > max_ranges:
        text += f" … (รวม {len(ordered)} แถว)"
    return text


# ---------- Parsing ----------


def parse_json(text: str) -> dict:
    """The first JSON object in a reply, tolerating fences and stray prose."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AnswerFormatError("no JSON object in the model reply")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnswerFormatError(f"model reply is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise AnswerFormatError("model reply is not a JSON object")
    return data


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [v.strip() for v in value or [] if isinstance(v, str) and v.strip()]


# ---------- Numeric grounding ----------

_NUMBER_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Numbers as written (commas removed): "1,343 คน และ 68.4%" → ["1343", "68.4"]."""
    return [m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text)]


def _decimal(s: str) -> Decimal | None:
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _values(sources: list) -> set[Decimal]:
    """Every number available in `sources` (strings are scanned, numbers taken as-is)."""
    out: set[Decimal] = set()
    for item in sources:
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, int | float):
            out.add(abs(Decimal(str(item))))
        else:
            out.update(d for d in map(_decimal, numbers_in(str(item))) if d is not None)
    return out


def ungrounded(text: str, available: set[Decimal]) -> list[str]:
    """Numbers in `text` that no available value accounts for.

    A written number is grounded if some available value rounds to it at the
    precision it was written with (so 358.1 is grounded by 358.1123), or it is
    the same year in the other calendar (2569 ↔ 2026).
    """
    bad = []
    # "[2]" is a citation marker, not the figure 2.
    for written in numbers_in(_MARKER_RE.sub(" ", text)):
        value = _decimal(written)
        if value is None:
            continue
        places = -value.as_tuple().exponent if value.as_tuple().exponent < 0 else 0
        quantum = Decimal(1).scaleb(-places)
        ok = any(a.quantize(quantum) == value for a in available if a.is_finite())
        if not ok and value == value.to_integral_value():
            year = int(value)
            ok = Decimal(year - _BE_OFFSET) in available or Decimal(year + _BE_OFFSET) in available
        if not ok:
            bad.append(written)
    return bad


# ---------- Building answers from model JSON ----------


def from_document_reply(
    data: dict, citations: list[Citation], source_texts: dict[int, str], question: str
) -> tuple[Answer | None, str]:
    """Build a document answer. Returns `(answer, missing)`; answer is None on refusal."""
    missing = _str(data.get("missing"))
    if data.get("answerable") is False:
        return None, missing or "เอกสารที่มีสิทธิ์เข้าถึงไม่มีข้อมูลที่ตอบคำถามนี้"

    valid = {c.n for c in citations}
    q_values = _values([question])
    facts: list[Fact] = []
    notes: list[str] = []
    for raw in data.get("facts") or []:
        if not isinstance(raw, dict) or not _str(raw.get("text")):
            continue
        text = _str(raw["text"])
        sources = tuple(
            sorted({n for n in raw.get("sources") or [] if isinstance(n, int) and n in valid})
        )
        if not sources:
            notes.append(f"dropped uncited fact: {text[:80]!r}")
            continue
        available = _values([source_texts[n] for n in sources]) | q_values
        bad = ungrounded(text, available)
        if bad:
            notes.append(f"dropped fact with number(s) {bad} not in sources {list(sources)}")
            continue
        facts.append(Fact(text, sources))

    if not facts:
        return None, missing or "ไม่มีข้อเท็จจริงที่ตรวจสอบกับเอกสารต้นทางได้"

    analysis = _str(data.get("analysis"))
    bad = ungrounded(analysis, _values(list(source_texts.values())) | q_values)
    if bad:
        notes.append(f"dropped analysis with number(s) {bad} not in any source")
        analysis = ""
    answer = Answer(facts, analysis, _str_list(data.get("limitations")), citations, notes)
    return answer, ""


def _row_text(columns: list[str], row: tuple) -> str:
    def fmt(v) -> str:
        if isinstance(v, int) and not isinstance(v, bool):
            return f"{v:,}"
        return "" if v is None else str(v)

    return " · ".join(f"{c} {fmt(v)}" for c, v in zip(columns, row, strict=True))


def from_sql_reply(
    data: dict | None,
    citations: list[Citation],
    columns: list[str],
    rows: list[tuple],
    question: str,
    limitations: list[str],
) -> Answer:
    """Build a SQL answer. Numbers must come from `rows`; otherwise show the rows."""
    every = tuple(c.n for c in citations)
    available = _values([v for row in rows for v in row]) | _values([question])
    available |= _values([len(rows)])
    # The data date may be quoted ("ข้อมูล ณ 30 ก.ย. 2026"); row numbers may not
    # be passed off as figures, so the citation label as a whole is not allowed.
    available |= _values([c.data_date.isoformat() for c in citations if c.data_date])
    notes: list[str] = []

    facts: list[Fact] = []
    analysis = ""
    extra: list[str] = []
    if data is not None:
        texts = [_str(f.get("text")) for f in data.get("facts") or [] if isinstance(f, dict)]
        texts = [t for t in texts if t]
        bad = [n for t in texts for n in ungrounded(t, available)]
        if texts and not bad:
            facts = [Fact(t, every) for t in texts]
            analysis = _str(data.get("analysis"))
            bad_analysis = ungrounded(analysis, available)
            if bad_analysis:
                notes.append(f"dropped analysis with number(s) {bad_analysis} not in the result")
                analysis = ""
            extra = _str_list(data.get("limitations"))
        elif bad:
            notes.append(f"model phrasing had number(s) {bad} not in the result; showing rows")

    if not facts:
        # The rows themselves are the answer — every number is SQL's.
        facts = [Fact(_row_text(columns, row), every) for row in rows]
    return Answer(facts, analysis, limitations + extra, citations, notes)
