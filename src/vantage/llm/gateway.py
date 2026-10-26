"""The single gateway for every model call. No other module talks to a provider.

Two kinds of call, with deliberately different privacy properties:

- **Embeddings** are always local (Ollama + bge-m3). Embedding is the step that
  touches *every* document, so sending it to an API would mean shipping the
  client's whole corpus off-site. There is no API backend here on purpose.
- **Generation** is swappable (`GENERATION_BACKEND=anthropic|vertex|ollama`).
  Only the question plus the retrieved chunks are sent, and switching to
  `ollama` keeps even that on-premise. `vertex` is the same Claude model served
  from the client's own Google Cloud project, authenticated with Application
  Default Credentials instead of an API key.

Keeping both behind one module is what makes "swap the provider" a config change
rather than a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from vantage.config import Settings, get_settings


class LLMError(RuntimeError):
    """A model call failed or was not configured."""


@dataclass(frozen=True)
class Completion:
    """A generated answer plus the accounting the audit log needs."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


# ---------- Embeddings (always local) ----------


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with the local embedding model."""
    settings = get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/embed"
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, json={"model": settings.embedding_model, "input": texts})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(
            f"embedding call to {url} failed ({exc}). Is Ollama running and "
            f"`{settings.embedding_model}` pulled?"
        ) from exc

    vectors = data.get("embeddings")
    if not vectors or len(vectors) != len(texts):
        raise LLMError(
            f"embedding response had {len(vectors or [])} vectors for {len(texts)} texts"
        )
    if len(vectors[0]) != settings.embedding_dim:
        raise LLMError(
            f"embedding dim mismatch: model returned {len(vectors[0])}, "
            f"EMBEDDING_DIM is {settings.embedding_dim}"
        )
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]


# ---------- Generation (swappable) ----------


def _complete_ollama(messages: list[dict], settings: Settings) -> Completion:
    """Generate with the local model via Ollama's chat endpoint."""
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                url,
                json={"model": settings.local_model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"generation call to {url} failed ({exc})") from exc

    return Completion(
        text=(data.get("message") or {}).get("content", "").strip(),
        model=settings.local_model,
        input_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
    )


def _anthropic_client(settings: Settings):
    """Build the Claude client for the configured backend (anthropic | vertex)."""
    try:
        from anthropic import Anthropic, AnthropicVertex
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise LLMError('the anthropic package is missing — pip install -e ".[api]"') from exc

    if settings.generation_backend.lower() == "vertex":
        if not settings.vertex_project_id:
            raise LLMError("GENERATION_BACKEND=vertex but VERTEX_PROJECT_ID is empty")
        # Credentials come from ADC (`gcloud auth application-default login`
        # locally, the service account in production) — no secret in .env.
        return AnthropicVertex(project_id=settings.vertex_project_id, region=settings.vertex_region)

    if not settings.anthropic_api_key:
        raise LLMError("GENERATION_BACKEND=anthropic but ANTHROPIC_API_KEY is empty")
    kwargs = {"api_key": settings.anthropic_api_key}
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url
    return Anthropic(**kwargs)


def _complete_claude(messages: list[dict], settings: Settings) -> Completion:
    """Generate with Claude, via the public API or Vertex AI.

    The system prompt is a top-level parameter in this API rather than a message,
    so it is lifted out of the message list here — callers build one uniform
    message list and do not need to know which provider they are talking to.
    """
    client = _anthropic_client(settings)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]

    try:
        resp = client.messages.create(
            # Thinking tokens count against max_tokens on current models, so a
            # tight cap truncates the JSON the answer path parses.
            model=settings.api_model,
            max_tokens=16000,
            system=system,
            messages=turns,
        )
    except Exception as exc:
        raise LLMError(f"{settings.generation_backend} generation call failed ({exc})") from exc
    # A truncated or declined reply is not a partial answer to show.
    if resp.stop_reason in ("max_tokens", "refusal"):
        raise LLMError(f"generation stopped early (stop_reason={resp.stop_reason})")
    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    return Completion(
        text=text,
        model=resp.model,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
    )


def complete(messages: list[dict], settings: Settings | None = None) -> Completion:
    """Generate an answer with the configured backend."""
    settings = settings or get_settings()
    backend = settings.generation_backend.lower()
    if backend in ("anthropic", "vertex"):
        return _complete_claude(messages, settings)
    if backend == "ollama":
        return _complete_ollama(messages, settings)
    raise LLMError(f"unknown GENERATION_BACKEND {backend!r} (expected anthropic | vertex | ollama)")
