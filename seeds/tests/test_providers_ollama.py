import httpx
from providers.ollama import OllamaProvider


async def test_ollama_returns_completion_with_token_counts():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "local answer"},
                                         "prompt_eval_count": 50, "eval_count": 12})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OllamaProvider(base_url="http://o", model="llama3.1:8b", http=http)
    c = await p.complete("s", "u")
    assert c.text == "local answer"
    assert c.prompt_tokens == 50 and c.completion_tokens == 12 and c.model == "llama3.1:8b"


async def test_ollama_missing_counts_default_zero():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "x"}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OllamaProvider(base_url="http://o", model="m", http=http)
    c = await p.complete("s", "u")
    assert c.prompt_tokens == 0 and c.completion_tokens == 0
