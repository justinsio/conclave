import httpx
from providers.ollama import OllamaProvider

async def test_ollama_posts_chat_and_returns_content():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "local answer"}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OllamaProvider(base_url="http://o", model="llama3.1:8b", http=http)
    assert await p.complete("s", "u") == "local answer"
