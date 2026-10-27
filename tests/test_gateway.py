"""The model gateway's client setup. No network: only the client is built."""

from __future__ import annotations

from vantage.config import Settings
from vantage.llm.gateway import _anthropic_client


def test_a_blank_base_url_in_the_environment_means_the_public_api(monkeypatch):
    # docker compose passes `ANTHROPIC_BASE_URL=` from .env as an empty string;
    # the SDK must not treat it as the URL to call.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    settings = Settings(
        generation_backend="anthropic", anthropic_api_key="test", anthropic_base_url=""
    )
    assert str(_anthropic_client(settings).base_url).startswith("https://api.anthropic.com")


def test_a_configured_gateway_is_used(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    settings = Settings(
        generation_backend="anthropic",
        anthropic_api_key="test",
        anthropic_base_url="https://llm-gateway.internal",
    )
    assert str(_anthropic_client(settings).base_url).startswith("https://llm-gateway.internal")
