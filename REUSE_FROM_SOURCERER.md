# Reuse inventory — Sourcerer → Executive AI Demo

> Produced before writing code, per `DEMO_PLAN.md` §8.
> Source read: `https://github.com/flybeeer/Sourcerer` (cloned to `C:\Users\Admin\src\Sourcerer`,
> 117 files / 0.5 MB, Phases 1–7 built, Phases 8–10 design-only).
> Date: 2026-10-24 (Day 1)

## Headline

Sourcerer already covers **~70% of the demo's "Must have" list**. The three demo
differentiators map onto the repo like this:

| Demo claim | Sourcerer status |
|---|---|
| Grounded answers w/ citations | **Built** (Phase 1–3) — but citations carry `source + chunk_index` only, no page/version/data-date |
| Honest refusal | **Built** (Phase 5 `guardrails.py` + `NO_ANSWER`) — reusable almost as-is |
| Governed access (2 workspaces, RBAC, audit) | **Not built.** Designed in detail as Phase 10 (`docs/governance-phase-10.md`) — Cerbos + SQLGlot. This is the whole net-new build. |

Numbers-from-SQL-never-the-LLM is **built**: Phase 7 `sqlkb/` (Text-to-SQL +
`safe_select` + read-only conn + cost guards) and `router.is_analytical_query()`
already has **Thai markers**.

---

## A. Reuse as-is (copy, no logic change)

| Module | What it gives the demo |
|---|---|
| `config.py` | Pydantic `BaseSettings` + `@lru_cache get_settings()`; rule "no module reads os.environ". Add workspace/principal keys here. |
| `llm/client.py`, `api_client.py`, `ollama_client.py`, `vllm_client.py` | The plan's required single swappable `llm/gateway.py` — **already exists** (`client_for(route, settings)`). |
| `routing/pricing.py` | Plan's "all pricing/cost constants in one module" — already satisfied. |
| `retrieval/fusion.py` | RRF implementation + `tests/test_fusion.py`. |
| `retrieval/rerank.py` | 4 backends (none/local bge/cohere/llm) behind `get_reranker(settings)`. |
| `retrieval/hybrid.py`, `retriever.py`, `types.py` | Pipeline wiring: vector ∥ bm25 → RRF → rerank. |
| `ingestion/chunking.py` | fixed + semantic strategies, swappable by config. |
| `ingestion/embeddings.py` | 2-function façade over the gateway. **bge-m3 = already multilingual/Thai-capable** — no model change needed. |
| `generation/guardrails.py` | Injection denylist (**already has 2 Thai patterns**) + relevance floor. Covers golden Q12. |
| `generation/generator.py`, `prompts.py` | Cited-answer prompt + `NO_ANSWER` refusal contract. Covers Q10/Q11 mechanics. |
| `sqlkb/safety.py` | `safe_select()`: single read-only SELECT, forbidden-keyword scan, forced LIMIT. |
| `sqlkb/backends.py`, `connection.py`, `text_to_sql.py`, `answer.py`, `schema_retrieval.py` | Complete Text-to-SQL path incl. read-only conn, wall-clock timeout, scan-op budget, schema narrowing. SQL + rows **are** the citation. |
| `routing/router.py` | `is_analytical_query()` (EN+Thai) = the plan's numeric-vs-document router, free. |
| `eval/runner.py`, `report.py`, `retrieval_metrics.py`, `generation_metrics.py` | Harness + **Markdown table writer** → repoint at `EVAL.md`. |
| `observability/trace.py`, `logging.py` | Per-query structured trace + `query_log` table = audit-log foundation. |
| `docker-compose.yml`, `Dockerfile`, `Makefile`, `pyproject.toml`, `.env.example`, `.gitignore` | **Day-1 repo skeleton is effectively done.** pgvector/pg16 + ollama + api, healthchecks, ruff/black/pytest config, `[extras]` pattern. |
| `tests/` (8 files) | Conventions to copy for the two mandatory safety tests. |

**Day-1 implication:** "repo skeleton + docker compose" is a port, not a build.

---

## B. Needs modification (reuse the shape, change the code)

### B1. `db/session.py` — schema is too thin for provenance
Current `chunks` = `(id, source, chunk_index, content, embedding, created_at)`.
Demo needs per-chunk: `workspace`, `sha256`, `doc_version`, `upload_date`,
`data_date`, `page`. Add as columns + the `init_schema()` `ALTER TABLE ... IF NOT
EXISTS` idempotent pattern already used for Phase-4 columns.

### B2. `ingestion/loaders.py` — **page numbers are destroyed**
`load_pdf()` does `"\n\n".join(pages)` → the page a chunk came from is
unrecoverable. Golden Q3/Q5 require "cite board PDF **page**". Must yield
`(page_no, text)` and carry `page` through `chunk()` → `_store_document()`.
Also: **no XLSX/CSV loader** (`SUPPORTED_SUFFIXES` = pdf/md/txt only).

### B3. `retrieval/bm25.py` — **does not work for Thai**
Uses `to_tsvector('simple', content)`. The file's own docstring admits it:
*"'simple' does not stem or segment languages without whitespace (e.g. Thai)
well"*. Thai has no spaces → one chunk ≈ one giant token → keyword leg is dead.
Fix: PyThaiNLP `word_tokenize` at ingest → store space-joined tokens in a
`content_tokens` column, index `to_tsvector('simple', content_tokens)`, tokenize
the query the same way. Contained change, ~40 lines.

### B4. `retrieval/vector.py` + `bm25.py` — no principal, no WHERE filter
Both are `search(query, top_k)` with no access predicate. The plan requires the
workspace filter **inside** the query. Change signature to accept an
`allowed_filter()` result and splice `WHERE workspace = ANY(%s)` before
`ORDER BY`. Phase-10 doc calls this "push the predicate down" — the design is
written, the code is not.

### B5. `ingestion/pipeline.py` — versioning conflicts with idempotency
`_store_document()` does `DELETE FROM chunks WHERE source = %s` then reinserts.
That makes re-ingest idempotent but means **v1 and v2 of the strategy plan can't
coexist** unless they're distinct `source` values. Golden Q4 needs both present,
v2 preferred, v1 flagged as superseded. Need an explicit
`(source, doc_version)` key + a `supersedes` pointer, and the delete scoped to
that pair.

### B6. `observability/logging.py` — `query_log` ≠ audit log
`query_log` has query/answer/latency/route/model/tokens/cost. Missing:
`user`, `workspace`, `retrieved_chunk_ids`, `sql_executed`, `decision`
(allowed/denied/refused), `denied_assets`. And it's a normal table — the plan
says **append-only** (needs a revoke of UPDATE/DELETE, or a trigger).

### B7. `generation/prompts.py` — answer format differs
Current: concise + inline `[n]`. Demo needs **facts (cited) / analysis /
limitations** sections. New prompt template; keep `NO_ANSWER` contract.

---

## C. Build new (nothing to reuse)

1. **`policy/` module** — `authorize(principal, action, resource)` +
   `allowed_filter(principal)`. Per `DEMO_PLAN.md` §3 the answer path must call
   **only these two functions**. Two mandatory tests: deny-by-default for unknown
   roles, explicit deny beats wildcard allow. Plus the leakage test (a B user can
   never receive an A-only chunk id).
   → **Deliberately NOT Cerbos.** Phase 10 specs a Cerbos PDP sidecar + SQLGlot;
   that's the right production answer but too heavy for a 5-day window, and
   `DEMO_PLAN.md` already asks for a plain Python policy function. Keep the Cerbos
   seam documented in the README as the production swap (same move Sourcerer makes
   for Ollama→vLLM, SQLite→DuckDB, JSON→Postgres graph store).
2. **Bright Academy synthetic dataset** — generator script for 2 xlsx/csv + 4 Thai
   PDFs, with v1/v2 differing on one number. `eval/corpus/` is 12 English HR-policy
   markdown files — no reuse, but the *shape* (tiny corpus, one fact per file) is
   worth copying.
3. **`asset_catalog`** — source/table → workspace, classification. Phase 10
   designs it; nothing built.
4. **Golden set + governance eval** — `eval/eval_set.jsonl` format is reusable;
   the 12 Thai questions and the `(principal, query, expected_visibility)` matrix
   are new. Access isolation must be 12/12 on Q8/Q9/Q12.
5. **Thai PDF generation** — needs a Thai-font-capable writer (reportlab +
   Sarabun/Noto Thai). `pypdf` only reads.

---

## D. Decisions this surfaces

### D1. UI — Streamlit vs the UI that already exists
`DEMO_PLAN.md` says Streamlit. Sourcerer ships `api/static/index.html` (17 KB
single-page: query box, citations, history, sources, route display) behind
FastAPI. Extending it with a user switcher + audit page is **~2 hours**; a
Streamlit app from scratch is most of Day 4. Recommend dropping Streamlit.

### D2. SQL store — SQLite vs Postgres
`DEMO_PLAN.md` §3 says "PostgreSQL + pgvector + full-text". `sqlkb/` is built on
SQLite/DuckDB (`connect_ro`, `information_schema` introspection, SQLite
progress-handler timeout). Rewriting it for Postgres loses the cost guards.
Recommend keeping SQLite for the tabular side (docs in pgvector, numbers in
SQLite) — reuses Phase 7 wholesale.

### D3. Generation backend — local vs API
Everything defaults to Ollama + `qwen2.5:32b` (~20 GB pull, slow on CPU).
Embeddings must stay on Ollama (`bge-m3`, ~2 GB) regardless. For a 5-day build
with Thai text, recommend `ANTHROPIC_API_KEY` for generation and keeping the
local route available but off — `client_for()` makes this a config flag.
Cost is trivial at 12 questions.

### D4. Repo strategy
New standalone repo that vendors the reusable modules, vs a branch of Sourcerer.
Standalone reads better in a proposal (purpose-built for the client's problem)
and avoids dragging in graphrag/. Recommend standalone, crediting Sourcerer in
the README.

---

## E. Risks found while reading

- **Thai BM25 is a silent failure** (B3), not an error. Without the PyThaiNLP fix
  the "hybrid" claim is half-true and the keyword leg contributes nothing.
- **Chunk ids rotate** on re-ingest (`BIGSERIAL` + delete/reinsert). Audit rows
  that store `retrieved_chunk_ids` will dangle after a re-ingest. Either log
  `(source, version, chunk_index)` instead, or freeze ingest before recording.
- `graphrag/` index was **never built** ("needs the expensive run") — out of demo
  scope anyway, don't port it.
- `min_relevance_score` floor only applies when a **cross-encoder** reranker ran
  (`local`/`cohere`); with `reranker_type=none` or `llm` the floor is silently
  skipped. Q10/Q11 refusals depend on this — must set a real reranker or add a
  separate threshold.
