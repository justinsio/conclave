from __future__ import annotations
import httpx
from providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Local Ollama completion seam — swap in via LLM_PROVIDER=ollama."""
    def __init__(self, base_url: str, model: str, http: httpx.AsyncClient | None = None):
        self._model = model
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=120.0)

    async def complete(self, system: str, user: str) -> str:
        resp = await self._http.post(
            f"{self._base}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
