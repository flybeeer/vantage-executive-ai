"""The document route's last step: retrieved chunks in, a checked answer or a refusal out.

    evidence floor ─► redact injection text ─► model (JSON) ─► citation + number checks

Each step can refuse, and each refusal says what is missing. The model is not
called at all when retrieval found nothing worth reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vantage.config import Settings
from vantage.generation import guardrails, prompts
from vantage.generation.answer import (
    Answer,
    AnswerFormatError,
    Citation,
    from_document_reply,
    parse_json,
    snippet,
)
from vantage.generation.language import TH, text
from vantage.llm import gateway
from vantage.llm.gateway import Completion
from vantage.retrieval.types import RetrievedChunk


@dataclass
class DocumentOutcome:
    answer: Answer | None = None
    missing: str = ""
    notes: list[str] = field(default_factory=list)
    calls: list[Completion] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.answer is None


def _citation(n: int, chunk: RetrievedChunk, lang: str) -> Citation:
    return Citation(
        n=n,
        label=chunk.label(lang),
        source=chunk.source,
        doc_version=chunk.doc_version,
        data_date=chunk.data_date,
        is_current=chunk.is_current,
        page=chunk.page,
        rows=chunk.row_ref,
        snippet=snippet(chunk.content),
        chunk_id=chunk.id,
    )


def answer_documents(
    question: str, chunks: list[RetrievedChunk], settings: Settings, lang: str = TH
) -> DocumentOutcome:
    outcome = DocumentOutcome()

    ok, why = guardrails.has_evidence(chunks, settings)
    outcome.notes.append(f"evidence: {why}")
    if not ok:
        outcome.missing = text("doc_no_evidence", lang)
        return outcome

    redaction = guardrails.redact(chunks) if settings.enable_injection_check else None
    shown = redaction.chunks if redaction else chunks
    if redaction:
        outcome.notes += redaction.notes

    citations = [_citation(n, c, lang) for n, c in enumerate(shown, start=1)]
    # What each source number may be checked against: the header the model saw
    # (file, version, page, data date) and the redacted text.
    source_texts = {n: f"{c.citation}\n{c.content}" for n, c in enumerate(shown, start=1)}

    reply = gateway.complete(prompts.document_messages(question, shown, lang), settings)
    outcome.calls.append(reply)
    try:
        data = parse_json(reply.text)
    except AnswerFormatError as exc:
        outcome.notes.append(f"generation: {exc}")
        outcome.missing = text("doc_unparsed", lang)
        return outcome

    answer, missing = from_document_reply(data, citations, source_texts, question, lang)
    if answer is None:
        outcome.missing = missing
        outcome.notes.append("model reported the sources do not answer the question")
        return outcome
    outcome.answer = answer
    outcome.notes += answer.notes
    return outcome
