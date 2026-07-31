from dataclasses import replace
from main import make_provider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


def test_make_provider_returns_the_openai_compatible_client(config):
    """The fixture sets llm_provider='openai_compatible' explicitly — this is
    not a default. The actual default is ollama; test_config.py pins that."""
    assert isinstance(make_provider(config), OpenAICompatibleProvider)


def test_make_provider_selects_ollama(config):
    cfg = replace(config, llm_provider="ollama")
    assert isinstance(make_provider(cfg), OllamaProvider)
