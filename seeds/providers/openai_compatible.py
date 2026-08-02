from __future__ import annotations
import httpx
from providers.base import Completion, LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """Chat completion against any OpenAI-compatible /chat/completions endpoint.

    Works with OpenAI, DeepSeek, Groq, Together, OpenRouter, vLLM, LM Studio,
    and LiteLLM — set LLM_BASE_URL and LLM_MODEL to match your provider.
    """
    def __init__(self, api_key: str, base_url: str, model: str, http: httpx.AsyncClient | None = None):
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=60.0)

    async def complete(self, system: str, user: str) -> Completion:
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
        data = resp.json()
        usage = data.get("usage") or {}
        return Completion(
            text=data["choices"][0]["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            model=self._model,
        )
