"""Central configuration, loaded from `.env`.

Every module reads settings from here — no module reads `os.environ` directly.
Cost constants are the one exception and live in `routing/pricing.py`, so there
is exactly one place to look for money.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- App ----------
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ---------- Database (Postgres + pgvector) ----------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "vantage"
    postgres_user: str = "vantage"
    postgres_password: str = "change_me"

    # ---------- Embeddings ----------
    # Always local. Document text is the client's confidential data; the pitch is
    # that it never leaves the box, so there is deliberately no embedding-API
    # backend to switch to.
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # ---------- Generation ----------
    generation_backend: str = "anthropic"  # anthropic | ollama
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""  # blank = public API; set to use a gateway
    api_model: str = "claude-sonnet-5"
    local_model: str = "qwen2.5:7b"

    # ---------- Tabular knowledge base ----------
    # Numeric questions are answered by SQL over this file, never by the LLM
    # reading a spreadsheet as prose. Opened read-only.
    sql_kb_path: str = "./data/kb.sqlite"
    sql_kb_max_rows: int = 50
    sql_kb_timeout_s: float = 5.0

    # ---------- Chunking ----------
    # Smaller than a typical English default: Thai is denser per character and
    # the corpus is small, so tighter chunks make citations more precise.
    chunk_size: int = 320  # approx tokens per chunk (PyThaiNLP tokens)
    chunk_overlap: int = 48

    # ---------- Retrieval ----------
    top_k_vector: int = 20
    top_k_bm25: int = 20
    top_k_final: int = 5
    rrf_k: int = 60

    # ---------- Reranker ----------
    reranker_type: str = "none"  # none | local | llm
    reranker_model: str = "bge-reranker-v2-m3"

    # ---------- Guardrails / refusal ----------
    enable_injection_check: bool = True
    # Cross-encoder relevance floor. Only comparable across queries when a
    # cross-encoder actually ran, so it is ignored unless reranker_type="local".
    min_relevance_score: float | None = None
    # Floor on the fused RRF score, applied when no cross-encoder ran. Without
    # this, RERANKER_TYPE=none would silently disable the refusal path.
    min_fused_score: float | None = 0.016

    # ---------- Data governance ----------
    policy_path: str = "policy/policy.yaml"

    @field_validator("min_relevance_score", "min_fused_score", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        """Treat a blank env var (e.g. `MIN_FUSED_SCORE=`) as unset (None)."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def dsn(self) -> str:
        """Postgres connection string, composed from the individual parts.

        Built from POSTGRES_* rather than read from a DATABASE_URL so a
        docker-compose override (POSTGRES_HOST=db) works without editing .env.
        """
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read .env once per process)."""
    return Settings()
