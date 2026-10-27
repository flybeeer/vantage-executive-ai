"""The SQL route: a numeric question in, a cited answer (or an honest refusal) out.

    coverage check ─► text-to-SQL ─► safe_select ─► run (authorizer) ─► evidence rows
                                                                       └─► phrase ─► grounding check

The model writes the SQL and phrases the result; it never produces a figure.
Every number shown is either in the result rows or the answer falls back to
the rows themselves (`generation.answer.from_sql_reply`).

The access decision is made twice and independently: the catalog check in the
service denies a question about a forbidden table before this runs, and the
SQLite authorizer refuses to compile a query that reads one. The second is the
one that holds if the model ignores the schema it was shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vantage.config import Settings
from vantage.db.session import connect
from vantage.generation import prompts
from vantage.generation.answer import (
    Answer,
    AnswerFormatError,
    Citation,
    compress_rows,
    from_sql_reply,
    parse_json,
)
from vantage.generation.language import TH, text
from vantage.llm import gateway
from vantage.llm.gateway import Completion
from vantage.policy import Principal, authorize, describe_table
from vantage.sqlkb import coverage, executor
from vantage.sqlkb.safety import UnsafeSQLError, safe_select

_EVIDENCE_MAX_ROWS = 5000


@dataclass
class SqlOutcome:
    answer: Answer | None = None
    missing: str = ""  # set when refused: what the data does not contain
    sql: str | None = None
    notes: list[str] = field(default_factory=list)
    calls: list[Completion] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.answer is None


def readable_tables(principal: Principal, settings: Settings) -> list[str]:
    """KB tables `principal` may read — the authorizer's allow-list."""
    return [
        t
        for t in executor.list_tables(settings.sql_kb_path)
        if authorize(principal, "read", describe_table(t)).allowed
    ]


def _table_citations(
    tables: list[str], evidence: dict[str, list[int]], sql: str, lang: str
) -> list[Citation]:
    """One citation per table read, with its file, version, rows and data date."""
    with connect() as conn:
        found = conn.execute(
            """
            SELECT sql_table, source, doc_version, data_date
            FROM documents
            WHERE sql_table = ANY(%s) AND is_current
            """,
            (tables,),
        ).fetchall()
    by_table = {r[0]: r[1:] for r in found}

    citations = []
    for n, table in enumerate(tables, start=1):
        source, version, data_date = by_table.get(table, (f"table {table}", "?", None))
        rows = compress_rows(evidence.get(table, []), lang=lang)
        parts = [
            source,
            version,
            text("rows", lang, rows=rows) if rows else text("rows_unknown", lang),
        ]
        parts.append(
            text("data_as_of", lang, date=data_date.isoformat())
            if data_date
            else text("no_data_date", lang)
        )
        citations.append(
            Citation(
                n=n,
                label=" · ".join(parts),
                source=source,
                doc_version=version,
                data_date=data_date,
                rows=rows or None,
                sql=sql,
            )
        )
    return citations


def _evidence_rows(items, tables_read: list[str], settings: Settings, notes: list[str]):
    """Run each evidence query (row_ref only, one table each) and collect row numbers."""
    out: dict[str, list[int]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        table, raw = item.get("table"), item.get("sql")
        if table not in tables_read or not isinstance(raw, str):
            continue
        try:
            sql = safe_select(raw, _EVIDENCE_MAX_ROWS)
            result = executor.run_select(
                sql, settings.sql_kb_path, [table], settings.sql_kb_timeout_s
            )
        except Exception as exc:  # noqa: BLE001 - a failed evidence query costs the row list only
            notes.append(f"evidence query for {table} failed: {exc}")
            continue
        out[table] = [r[0] for r in result.rows if r and isinstance(r[0], int)]
    return out


def _has_figures(rows: list[tuple]) -> bool:
    """False for a result with nothing to report: no rows, or only labels beside
    empty aggregates — `('2026-12', None, 0)` is SUM over no rows and COUNT 0,
    which must become "ไม่พบข้อมูล", not a fact reading "total_revenue  · 0"."""
    if not rows:
        return False
    numbers = [v for row in rows for v in row if not isinstance(v, str)]
    return not (None in numbers and all(v in (None, 0) for v in numbers))


def answer_sql(
    question: str,
    principal: Principal,
    subject_tables: tuple[str, ...],
    settings: Settings,
    lang: str = TH,
) -> SqlOutcome:
    outcome = SqlOutcome()
    limitations: list[str] = []

    # 1. Coverage — before the model is asked anything. "ปีนี้" means the year
    #    of the latest data, pinned once so the check and the SQL agree.
    spans = {t: executor.month_range(settings.sql_kb_path, t) for t in subject_tables}
    latest = max((s[1] for s in spans.values() if s), default=None)
    if latest:
        pinned = coverage.pin_this_year(question, int(latest[:4]))
        if pinned != question:
            outcome.notes.append(f"'this year' read as {latest[:4]} (latest data {latest})")
            question = pinned
    for table, span in spans.items():
        if span is None:
            continue
        cov = coverage.check(question, *span)
        if cov.none_covered:
            outcome.missing = text(
                "coverage_none",
                lang,
                table=table,
                first=cov.first,
                last=cov.last,
                missing=", ".join(cov.missing),
            )
            outcome.notes.append(f"coverage: {table} ends {cov.last}, asked {list(cov.missing)}")
            return outcome
        if cov.partly_covered:
            limitations.append(
                text(
                    "coverage_partial",
                    lang,
                    table=table,
                    last=cov.last,
                    missing=", ".join(cov.missing),
                )
            )

    # 2. Text-to-SQL over the subject tables only (narrowed schema).
    allowed = readable_tables(principal, settings)
    schema_tables = [t for t in subject_tables if t in allowed] or allowed
    schema = executor.describe(settings.sql_kb_path, schema_tables)
    reply = gateway.complete(prompts.sql_messages(question, schema, lang), settings)
    outcome.calls.append(reply)
    try:
        plan = parse_json(reply.text)
    except AnswerFormatError as exc:
        outcome.missing = text("sql_unwritable", lang)
        outcome.notes.append(f"text-to-SQL: {exc}")
        return outcome
    if plan.get("answerable") is False or not isinstance(plan.get("sql"), str):
        outcome.missing = (plan.get("missing") or "").strip() or text("sql_unanswerable", lang)
        outcome.notes.append("text-to-SQL: model reported the schema cannot answer")
        return outcome

    # 3. Validate and run, with the authorizer limited to what this principal may read.
    try:
        sql = safe_select(plan["sql"], settings.sql_kb_max_rows)
        outcome.sql = sql
        result = executor.run_select(sql, settings.sql_kb_path, allowed, settings.sql_kb_timeout_s)
    except (UnsafeSQLError, executor.QueryRejected) as exc:
        outcome.sql = outcome.sql or plan["sql"]
        outcome.missing = text("sql_rejected", lang)
        outcome.notes.append(f"SQL rejected: {exc}")
        return outcome
    except Exception as exc:  # noqa: BLE001 - any engine error is a no-answer, not a crash
        outcome.missing = text("sql_failed", lang)
        outcome.notes.append(f"SQL failed: {type(exc).__name__}: {exc}")
        return outcome

    if not _has_figures(result.rows):
        outcome.missing = text("sql_no_rows", lang)
        outcome.notes.append("SQL returned no data")
        return outcome
    if len(result.rows) >= settings.sql_kb_max_rows:
        limitations.append(text("max_rows", lang, n=settings.sql_kb_max_rows))

    # 4. Cite the tables actually read, with the rows that fed the answer.
    evidence = _evidence_rows(plan.get("evidence"), result.tables_read, settings, outcome.notes)
    citations = _table_citations(result.tables_read, evidence, sql, lang)

    # 5. Phrase the rows; the grounding check decides whether the phrasing is kept.
    table_text = prompts.render_table(result.columns, result.rows)
    reply = gateway.complete(prompts.sql_answer_messages(question, sql, table_text, lang), settings)
    outcome.calls.append(reply)
    try:
        data = parse_json(reply.text)
    except AnswerFormatError as exc:
        outcome.notes.append(f"phrasing: {exc}; showing rows")
        data = None
    outcome.answer = from_sql_reply(
        data, citations, result.columns, result.rows, question, limitations, lang
    )
    outcome.notes += outcome.answer.notes
    return outcome
