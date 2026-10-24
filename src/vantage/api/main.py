"""FastAPI application entrypoint.

Only the health endpoint exists so far; the query, citation and audit routes
arrive with the retrieval and answer paths.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from vantage.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="Vantage",
    description="Answers from your documents — with file, version, page and data date.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
