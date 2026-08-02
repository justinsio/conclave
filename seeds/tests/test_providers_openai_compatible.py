import httpx
from providers.openai_compatible import OpenAICompatibleProvider


async def test_deepseek_returns_completion_with_usage():
    captured = {}
    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "the answer"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 40}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider(api_key="dk", base_url="https://api.deepseek.com",
                         model="deepseek-chat", http=http)
    c = await p.complete("system text", "user text")
    assert c.text == "the answer"
    assert c.prompt_tokens == 200 and c.completion_tokens == 40 and c.model == "deepseek-chat"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer dk"


async def test_deepseek_missing_usage_defaults_zero():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider(api_key="dk", base_url="https://api.deepseek.com",
                         model="deepseek-chat", http=http)
    c = await p.complete("s", "u")
    assert c.prompt_tokens == 0 and c.completion_tokens == 0
