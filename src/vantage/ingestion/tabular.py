"""Spreadsheets become SQL tables, not prose.

A model asked to total a column of numbers it read as text will produce a
plausible number. The demo's promise is the opposite: every figure is computed
by SQL over typed columns, and the query is shown as part of the citation. So
`.xlsx` and `.csv` are loaded into SQLite rather than chunked and embedded.

Each file still gets a `documents` row, so a SQL answer can cite the same four
things a document answer does — file, version, row, data date.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from pathlib import Path

from openpyxl import load_workbook

logger = logging.getLogger(__name__)

# Column names are used directly in generated SQL, so they must be plain
# identifiers. Anything else is normalised rather than quoted, which keeps the
# schema shown to the model simple and the generated SQL easy to read.
_IDENT_RE = re.compile(r"[^0-9a-z_]+")

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _ident(name: str) -> str:
    """Normalise a header cell into a safe snake_case SQL identifier."""
    cleaned = _IDENT_RE.sub("_", str(name).strip().lower()).strip("_")
    if not cleaned:
        raise ValueError(f"header cell {name!r} normalises to an empty identifier")
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"{path.name} is empty")
    return rows[0], rows[1:]


def _read_xlsx(path: Path) -> tuple[list[str], list[list[str]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    rows = [["" if c is None else str(c) for c in row] for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError(f"{path.name} has no rows")
    return rows[0], rows[1:]


def _column_types(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Infer INTEGER / REAL / TEXT per column.

    Types matter: a REAL stored as TEXT makes `SUM()` return nonsense, and the
    whole point of this path is that the arithmetic is trustworthy.
    """
    types: list[str] = []
    for i in range(len(headers)):
        values = [r[i].strip() for r in rows if i < len(r) and r[i].strip()]
        if values and all(_NUMERIC_RE.match(v) for v in values):
            types.append("REAL" if any("." in v for v in values) else "INTEGER")
        else:
            types.append("TEXT")
    return types


def _coerce(value: str, sql_type: str):
    value = value.strip()
    if value == "":
        return None
    if sql_type == "INTEGER":
        return int(value)
    if sql_type == "REAL":
        return float(value)
    return value


def load_into_sqlite(path: Path, db_path: Path, table: str) -> dict[str, int]:
    """Replace `table` in `db_path` with the contents of `path`.

    Adds a `row_ref` column holding the file's own 1-based row number (header =
    row 1), so a SQL answer can cite the exact spreadsheet row a figure came
    from. Returns `{columns, rows}`.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        headers, rows = _read_csv(path)
    elif suffix == ".xlsx":
        headers, rows = _read_xlsx(path)
    else:
        raise ValueError(f"Unsupported tabular type: {path.suffix} ({path})")

    columns = [_ident(h) for h in headers]
    types = _column_types(columns, rows)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        col_ddl = ", ".join(f"{c} {t}" for c, t in zip(columns, types, strict=True))
        # Re-ingest is idempotent: drop and rebuild rather than append, so
        # running `make ingest` twice does not double every number.
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(f"CREATE TABLE {table} ({col_ddl}, row_ref INTEGER)")
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        payload = []
        for n, row in enumerate(rows, start=2):  # row 1 is the header
            padded = list(row) + [""] * (len(columns) - len(row))
            payload.append([_coerce(padded[i], types[i]) for i in range(len(columns))] + [n])
        conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", payload)
        conn.commit()
    finally:
        conn.close()

    logger.info("loaded %s → table %s (%d rows)", path.name, table, len(rows))
    return {"columns": len(columns), "rows": len(rows)}
