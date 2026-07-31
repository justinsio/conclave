from dataclasses import replace
from main import make_provider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


def test_make_provider_defaults_to_deepseek(config):
    assert isinstance(make_provider(config), OpenAICompatibleProvider)


def test_make_provider_selects_ollama(config):
    cfg = replace(config, llm_provider="ollama")
    assert isinstance(make_provider(cfg), OllamaProvider)
