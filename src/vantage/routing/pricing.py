"""Token pricing — the one place in the codebase that knows about money.

USD per 1,000,000 tokens, as (input, output). Source: Anthropic first-party API
pricing (cached 2026-10-06). Vertex AI is partner-billed and may differ from
these list prices; the audit log's `cost_usd` is therefore an estimate at
Anthropic list price, which is the comparable baseline across backends.

Local models (Ollama) are absent and priced at $0 — the on-premise route costs
hardware, not tokens.
"""

from __future__ import annotations

PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def _base_model(model: str) -> str:
    """Strip a Vertex `@snapshot` suffix so `claude-x@20260101` prices as `claude-x`."""
    return model.split("@", 1)[0]


def price_for(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens; $0 for unknown or local models."""
    return PRICING.get(_base_model(model), (0.0, 0.0))


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost of one call from its token usage."""
    price_in, price_out = price_for(model)
    return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out
