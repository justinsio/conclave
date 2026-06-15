import httpx
import pytest
from config import SeedConfig


def mock_client(handler):
    """httpx.AsyncClient backed by a MockTransport routing handler(request)->Response."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.fixture
def config():
    return SeedConfig(
        api_url="http://test", agent_key="k", deepseek_api_key="dk",
        specialty="coding", subscriptions=["coding", "general"], llm_provider="deepseek",
        poll_interval=10, solo_threshold=0.85, open_thread_threshold=0.60,
        draft_after_minutes=5, answer_after_minutes=15,
        deepseek_base_url="https://api.deepseek.com", deepseek_model="deepseek-chat",
        ollama_base_url="http://o", ollama_model="llama3.1:8b", telegram_webhook=None,
    )
