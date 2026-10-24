"""Thai word segmentation for the keyword leg of hybrid retrieval.

Postgres full-text search cannot tokenise Thai. Its `'simple'` configuration
splits on whitespace, and Thai does not use spaces between words — so a whole
chunk of Thai becomes *one* token, `@@` almost never matches, and the keyword
half of "hybrid retrieval" silently contributes nothing. The vector leg still
returns results, so nothing looks broken; recall just quietly drops.

The fix is to segment the text ourselves and let Postgres index the segmented
copy: `chunks.content_tokens` holds space-joined PyThaiNLP tokens, and
`tokens_tsv` is generated from that column. Queries are segmented the same way,
so both sides of the match see the same token stream.

`content` keeps the original text — segmentation is an index-time artefact and
must never reach a citation snippet.
"""

from __future__ import annotations

import re

from pythainlp.tokenize import word_tokenize

# newmm = dictionary-based maximal matching. Fast, deterministic, and good
# enough for search tokens; we are not doing linguistics, just making the
# keyword index addressable.
_ENGINE = "newmm"

# Tokens that carry no search signal: whitespace runs and lone punctuation.
_NOISE_RE = re.compile(r"^[\s​]*$|^[^\w฀-๿]+$")


def tokens(text: str) -> list[str]:
    """Segment `text` into search tokens, dropping whitespace and punctuation.

    Mixed Thai/English/digits is the normal case in these documents (e.g.
    "ยอดนักเรียน Q3 2026"); `word_tokenize` keeps non-Thai runs intact.
    """
    out: list[str] = []
    for tok in word_tokenize(text, engine=_ENGINE, keep_whitespace=False):
        tok = tok.strip()
        if not tok or _NOISE_RE.match(tok):
            continue
        out.append(tok.lower())
    return out


def segment(text: str) -> str:
    """Return `text` as space-joined tokens, ready for `to_tsvector('simple', …)`."""
    return " ".join(tokens(text))


def tsquery_terms(query: str) -> str:
    """Segment a user question into a space-joined term list for the FTS query.

    The caller feeds this to `plainto_tsquery('simple', …)`, whose ANDs are then
    rewritten to ORs so a chunk matching *any* term stays a candidate — the
    right recall behaviour for a keyword leg that will be fused by RRF anyway.
    """
    return segment(query)
