import httpx
from providers.deepseek import DeepSeekProvider

async def test_deepseek_posts_chat_and_returns_content():
    captured = {}
    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "the answer"}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = DeepSeekProvider(api_key="dk", base_url="https://api.deepseek.com",
                         model="deepseek-chat", http=http)
    out = await p.complete("system text", "user text")
    assert out == "the answer"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer dk"
