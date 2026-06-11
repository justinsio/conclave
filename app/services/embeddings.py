from __future__ import annotations

import logging
import math

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def vector_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """
    Batch-fetch embeddings from Ollama nomic-embed-text.
    Returns None when ollama_base_url is unset or the call fails.
    """
    if not settings.ollama_base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": settings.embedding_model, "input": texts},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]
    except Exception as exc:
        logger.warning("embeddings: call failed (%s)", exc)
        return None
