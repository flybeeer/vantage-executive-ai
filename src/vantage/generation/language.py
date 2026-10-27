"""The answer is written in the language of the question: Thai or English.

Decided once per question (`of`) and passed down, so the model's prose, the
headings around it, the citation labels and every refusal agree. Any Thai
character makes a question Thai — a Thai question naming "Q3" or "SQL" is
still Thai. The UI chrome stays Thai; only what answers the question follows
the question.

Every fixed user-facing string lives in `_TEXT`, in both languages, so a new
message cannot be added in one language only without a test noticing.
"""

from __future__ import annotations

import re

TH = "th"
EN = "en"

_THAI_RE = re.compile(r"[฀-๿]")

_TEXT: dict[str, dict[str, str]] = {
    # The answer's frame
    "not_found": {TH: "ไม่พบข้อมูล", EN: "No data found"},
    "facts": {TH: "ข้อเท็จจริง", EN: "Facts"},
    "analysis": {TH: "บทวิเคราะห์", EN: "Analysis"},
    "limitations": {TH: "ข้อจำกัด", EN: "Limitations"},
    "sources": {TH: "แหล่งอ้างอิง", EN: "Sources"},
    # Citation labels
    "page": {TH: "หน้า {page}", EN: "p. {page}"},
    "rows": {TH: "แถว {rows}", EN: "rows {rows}"},
    "rows_unknown": {TH: "แถว: ไม่ระบุ", EN: "rows: not given"},
    "rows_total": {TH: " … (รวม {n} แถว)", EN: " … ({n} rows in all)"},
    "data_as_of": {TH: "ข้อมูล ณ {date}", EN: "data as of {date}"},
    "no_data_date": {TH: "ไม่ระบุวันที่ข้อมูล", EN: "no data date"},
    "superseded": {TH: "ฉบับเก่า", EN: "superseded"},
    # Denials
    "denied": {
        TH: "ไม่มีสิทธิ์เข้าถึงข้อมูลที่คำถามนี้ต้องใช้ ({assets}) — คำขอนี้ถูกบันทึกไว้แล้ว",
        EN: "You do not have access to the data this question needs ({assets}) — "
        "this request has been logged",
    },
    "unknown_user": {
        TH: "ไม่พบผู้ใช้นี้ในระบบ — คำขอนี้ถูกบันทึกไว้แล้ว",
        EN: "Unknown user — this request has been logged",
    },
    "injection": {
        TH: "คำถามนี้มีข้อความที่สั่งให้ระบบเปลี่ยนกฎหรือสิทธิ์การเข้าถึง จึงไม่ได้ดำเนินการ — "
        "คำขอนี้ถูกบันทึกไว้แล้ว",
        EN: "This question tells the system to change its rules or access, so it was not "
        "run — this request has been logged",
    },
    # What is missing (refusals)
    "coverage_none": {
        TH: "ข้อมูลในตาราง {table} มีตั้งแต่ {first} ถึง {last} เท่านั้น "
        "แต่คำถามต้องการข้อมูลช่วง {missing}",
        EN: "the {table} table only covers {first} to {last}, but the question asks "
        "about {missing}",
    },
    "coverage_partial": {
        TH: "ข้อมูลใน {table} มีถึง {last} — ไม่รวมช่วง {missing}",
        EN: "{table} ends at {last} — {missing} is not included",
    },
    "max_rows": {TH: "แสดงผลลัพธ์สูงสุด {n} แถว", EN: "showing at most {n} result rows"},
    "sql_unwritable": {
        TH: "ระบบไม่สามารถสร้างคำสั่ง SQL สำหรับคำถามนี้ได้",
        EN: "the system could not write a query for this question",
    },
    "sql_unanswerable": {
        TH: "ตารางที่มีอยู่ไม่มีข้อมูลนี้",
        EN: "the available tables do not hold this data",
    },
    "sql_rejected": {
        TH: "คำสั่ง SQL ที่สร้างขึ้นไม่ผ่านการตรวจสอบความปลอดภัยหรือสิทธิ์การเข้าถึง",
        EN: "the generated query failed the safety or access check",
    },
    "sql_failed": {
        TH: "คำสั่ง SQL ที่สร้างขึ้นทำงานไม่สำเร็จ",
        EN: "the generated query did not run",
    },
    "sql_no_rows": {
        TH: "ไม่พบแถวข้อมูลที่ตรงกับเงื่อนไขของคำถาม",
        EN: "no rows match what the question asks for",
    },
    "doc_no_evidence": {
        TH: "ไม่พบเอกสารที่เกี่ยวข้องกับคำถามนี้ในขอบเขตที่มีสิทธิ์เข้าถึง",
        EN: "no document you have access to is relevant to this question",
    },
    "doc_unparsed": {
        TH: "ระบบไม่สามารถสรุปคำตอบที่ตรวจสอบได้จากเอกสาร",
        EN: "the system could not produce a checkable answer from the documents",
    },
    "doc_unanswerable": {
        TH: "เอกสารที่มีสิทธิ์เข้าถึงไม่มีข้อมูลที่ตอบคำถามนี้",
        EN: "the documents you have access to do not answer this question",
    },
    "doc_no_facts": {
        TH: "ไม่มีข้อเท็จจริงที่ตรวจสอบกับเอกสารต้นทางได้",
        EN: "no fact could be checked against the source documents",
    },
}

# Said to the model: which language every text field of its JSON is in.
MODEL_LANGUAGE = {TH: "Thai", EN: "English"}


def of(question: str) -> str:
    """TH if the question contains any Thai, else EN."""
    return TH if _THAI_RE.search(question) else EN


def text(key: str, lang: str, **values) -> str:
    """The fixed string `key` in `lang` (Thai if `lang` is unknown)."""
    entry = _TEXT[key]
    return entry.get(lang, entry[TH]).format(**values)
