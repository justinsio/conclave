from dataclasses import replace
from main import make_provider
from providers.deepseek import DeepSeekProvider
from providers.ollama import OllamaProvider


def test_make_provider_defaults_to_deepseek(config):
    assert isinstance(make_provider(config), DeepSeekProvider)


def test_make_provider_selects_ollama(config):
    cfg = replace(config, llm_provider="ollama")
    assert isinstance(make_provider(cfg), OllamaProvider)
