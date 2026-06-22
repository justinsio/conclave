import pytest
from providers.base import LLMProvider, FakeProvider, Completion


async def test_fake_provider_returns_queued_completion():
    p = FakeProvider(["hello"], prompt_tokens=12, completion_tokens=3, model="m")
    c = await p.complete("sys", "user")
    assert isinstance(c, Completion)
    assert c.text == "hello"
    assert c.prompt_tokens == 12 and c.completion_tokens == 3 and c.model == "m"
    assert p.calls == [("sys", "user")]


def test_llmprovider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()
