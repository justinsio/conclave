import pytest
from providers.base import LLMProvider, FakeProvider

async def test_fake_provider_returns_queued():
    p = FakeProvider(["hello"])
    assert await p.complete("sys", "user") == "hello"

def test_llmprovider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()
