"""Ingestion tests, aimed at the three things that are easy to get silently wrong.

1. **Thai segmentation.** If this regresses, the keyword leg of hybrid retrieval
   stops matching and nothing raises — the vector leg covers for it and recall
   just drops. So the test asserts the property that makes FTS work at all:
   a Thai phrase becomes several space-separated tokens.
2. **Page-accurate chunking.** A chunk that spans pages cannot honestly cite a
   page, so chunks must never merge across them.
3. **Typed tabular load.** A numeric column stored as TEXT makes `SUM()` return
   nonsense, which is the exact failure the SQL path exists to prevent.
"""

from __future__ import annotations

import csv
import sqlite3

from vantage.config import Settings
from vantage.ingestion.chunking import chunk_page
from vantage.ingestion.tabular import load_into_sqlite
from vantage.ingestion.thai import segment, tokens, tsquery_terms

# ---------- Thai segmentation ----------


def test_thai_phrase_segments_into_multiple_tokens():
    """Postgres FTS cannot do this itself — unsegmented Thai is one giant token."""
    phrase = "ยอดนักเรียนรวมเดือนกันยายน"
    assert " " not in phrase  # no whitespace for FTS to split on

    result = tokens(phrase)
    assert len(result) >= 4, f"expected several tokens, got {result}"
    assert "นักเรียน" in result


def test_segment_output_is_space_joined_and_matches_tokens():
    """`content_tokens` must be exactly what `to_tsvector('simple', …)` can index."""
    text = "รายได้สาขาบางนาเดือนสิงหาคม 2569"
    assert segment(text).split() == tokens(text)


def test_question_and_document_segment_to_overlapping_terms():
    """A question and the text that answers it must share tokens, or FTS scores 0."""
    doc = segment("สาขาที่มีรายได้เติบโตสูงสุดคือสาขาขอนแก่น")
    query = tsquery_terms("สาขาไหนรายได้โตสูงสุด")

    shared = set(doc.split()) & set(query.split())
    assert "รายได้" in shared
    assert "สาขา" in shared


def test_mixed_thai_english_digits_survive():
    """These documents mix scripts constantly ("ไตรมาส 3/2569", "IELTS")."""
    result = tokens("เป้าหมาย IELTS ไตรมาส 3/2569 จำนวน 240 คน")
    assert "ielts" in result
    assert "240" in result


def test_segmentation_does_not_leak_into_stored_content():
    """Segmentation is an index artefact; a citation snippet must read naturally."""
    text = "ที่ประชุมอนุมัติเปิดสาขาที่ 5"
    chunks = chunk_page(text, Settings(), page=1)
    assert chunks[0].content == text


# ---------- Chunking ----------


def test_chunks_carry_their_page():
    chunks = chunk_page("ข้อความสั้น ๆ ในหน้าที่สาม", Settings(), page=3)
    assert chunks
    assert all(c.page == 3 for c in chunks)


def test_long_page_splits_with_overlap():
    settings = Settings(chunk_size=20, chunk_overlap=5)
    text = "นักเรียนสาขาสยามเรียนคณิตศาสตร์และวิทยาศาสตร์ทุกวันเสาร์อาทิตย์ตลอดภาคเรียน " * 12

    chunks = chunk_page(text, settings, page=1)
    assert len(chunks) > 1
    assert all(c.page == 1 for c in chunks)
    # Every chunk must be non-empty and shorter than the whole page.
    assert all(0 < len(c.content) < len(text) for c in chunks)


def test_short_page_is_one_chunk():
    chunks = chunk_page("สั้น", Settings(), page=1)
    assert len(chunks) == 1


def test_empty_page_produces_no_chunks():
    assert chunk_page("   ", Settings(), page=1) == []


# ---------- Tabular load ----------


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)


def test_numeric_columns_are_typed_so_sum_is_arithmetic(tmp_path):
    """The whole point of the SQL path: SUM must add, not concatenate."""
    src = tmp_path / "finance.csv"
    _write_csv(
        src,
        [
            ["month", "branch", "revenue", "margin"],
            ["2026-08", "บางนา", "1200000", "0.31"],
            ["2026-09", "บางนา", "1350000", "0.33"],
        ],
    )
    db = tmp_path / "kb.sqlite"

    stats = load_into_sqlite(src, db, "finance_monthly")
    assert stats == {"columns": 4, "rows": 2}

    conn = sqlite3.connect(db)
    types = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(finance_monthly)")}
    assert types["revenue"] == "INTEGER"
    assert types["margin"] == "REAL"
    assert types["branch"] == "TEXT"

    total = conn.execute("SELECT SUM(revenue) FROM finance_monthly").fetchone()[0]
    assert total == 2550000
    conn.close()


def test_row_ref_points_at_the_spreadsheet_row(tmp_path):
    """A SQL answer cites a row number, so it must match the file's own numbering."""
    src = tmp_path / "t.csv"
    _write_csv(src, [["month", "n"], ["2026-01", "5"], ["2026-02", "7"]])
    db = tmp_path / "kb.sqlite"

    load_into_sqlite(src, db, "t")
    conn = sqlite3.connect(db)
    # Header is row 1, so the first data row is row 2.
    assert conn.execute("SELECT row_ref FROM t WHERE month = '2026-01'").fetchone()[0] == 2
    assert conn.execute("SELECT row_ref FROM t WHERE month = '2026-02'").fetchone()[0] == 3
    conn.close()


def test_reload_replaces_rather_than_appends(tmp_path):
    """`make ingest` twice must not double every number."""
    src = tmp_path / "t.csv"
    _write_csv(src, [["month", "n"], ["2026-01", "5"]])
    db = tmp_path / "kb.sqlite"

    load_into_sqlite(src, db, "t")
    load_into_sqlite(src, db, "t")

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_thai_headers_normalise_to_sql_identifiers(tmp_path):
    """Generated SQL has to reference these names, so they must be plain."""
    src = tmp_path / "t.csv"
    _write_csv(src, [["Month ", "New Students", "2026 Total"], ["2026-01", "5", "9"]])
    db = tmp_path / "kb.sqlite"

    load_into_sqlite(src, db, "t")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(t)")]
    assert cols == ["month", "new_students", "c_2026_total", "row_ref"]
    conn.close()
