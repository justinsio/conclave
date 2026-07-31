import pytest

from config import load_config

_MIN = {"CONCLAVE_API_URL": "http://x", "CONCLAVE_AGENT_KEY": "key"}


def test_load_config_parses_env_and_derives_subscriptions():
    cfg = load_config({
        **_MIN,
        "LLM_API_KEY": "dk", "SEED_SPECIALTY": "research",
        "LLM_PROVIDER": "openai_compatible", "POLL_INTERVAL_SECONDS": "10",
        "SOLO_THRESHOLD": "0.85", "OPEN_THREAD_THRESHOLD": "0.60",
        "DRAFT_AFTER_MINUTES": "5", "ANSWER_AFTER_MINUTES": "15",
        "LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-chat",
        "OLLAMA_BASE_URL": "http://o", "OLLAMA_MODEL": "llama3.1:8b",
    })
    assert cfg.specialty == "research"
    assert cfg.subscriptions == ["research", "general"]
    assert cfg.solo_threshold == 0.85
    assert cfg.telegram_webhook is None
    assert cfg.llm_api_key == "dk"


def test_general_specialty_not_duplicated():
    cfg = load_config({**_MIN, "SEED_SPECIALTY": "general"})
    assert cfg.subscriptions == ["general"]


def test_provider_defaults_to_ollama():
    """A $0 local-first default: a self-hoster with no API key can boot."""
    cfg = load_config(_MIN)
    assert cfg.llm_provider == "ollama"


def test_ollama_provider_boots_with_no_api_key():
    """THE BUG: e['DEEPSEEK_API_KEY'] used to raise KeyError, so an
    Ollama-only self-hoster could not start a seed at all."""
    cfg = load_config({**_MIN, "LLM_PROVIDER": "ollama"})
    assert cfg.llm_api_key == ""
    assert cfg.ollama_base_url == "http://localhost:11434"


def test_openai_compatible_provider_requires_an_api_key():
    with pytest.raises(ValueError) as exc:
        load_config({**_MIN, "LLM_PROVIDER": "openai_compatible"})
    assert "LLM_API_KEY" in str(exc.value)


def test_openai_compatible_provider_requires_a_base_url():
    with pytest.raises(ValueError) as exc:
        load_config({**_MIN, "LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "k"})
    assert "LLM_BASE_URL" in str(exc.value)


def test_unknown_provider_is_rejected():
    """The legacy value 'deepseek' used to fall through to an unconfigured
    hosted client that POSTed to '/chat/completions' with no host."""
    with pytest.raises(ValueError) as exc:
        load_config({**_MIN, "LLM_PROVIDER": "deepseek", "LLM_API_KEY": "k"})
    message = str(exc.value)
    assert "deepseek" in message and "openai_compatible" in message


def test_missing_required_conclave_vars_still_raise():
    with pytest.raises(KeyError):
        load_config({"CONCLAVE_AGENT_KEY": "k"})
