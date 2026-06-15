from __future__ import annotations
import httpx
from providers.base import LLMProvider


class DeepSeekProvider(LLMProvider):
    """OpenAI-compatible chat completion against DeepSeek."""
    def __init__(self, api_key: str, base_url: str, model: str, http: httpx.AsyncClient | None = None):
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=60.0)

    async def complete(self, system: str, user: str) -> str:
        resp = await self._http.post(
            f"{self._base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
