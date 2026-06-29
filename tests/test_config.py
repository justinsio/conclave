from config import load_config

def test_load_config_parses_env_and_derives_subscriptions():
    env = {
        "CONCLAVE_API_URL": "http://x", "CONCLAVE_AGENT_KEY": "key",
        "DEEPSEEK_API_KEY": "dk", "SEED_SPECIALTY": "research",
        "LLM_PROVIDER": "deepseek", "POLL_INTERVAL_SECONDS": "10",
        "SOLO_THRESHOLD": "0.85", "OPEN_THREAD_THRESHOLD": "0.60",
        "DRAFT_AFTER_MINUTES": "5", "ANSWER_AFTER_MINUTES": "15",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com", "DEEPSEEK_MODEL": "deepseek-chat",
        "OLLAMA_BASE_URL": "http://o", "OLLAMA_MODEL": "llama3.1:8b",
    }
    cfg = load_config(env)
    assert cfg.specialty == "research"
    assert cfg.subscriptions == ["research", "general"]
    assert cfg.solo_threshold == 0.85
    assert cfg.telegram_webhook is None

def test_general_specialty_not_duplicated():
    env_base = {"CONCLAVE_API_URL": "x", "CONCLAVE_AGENT_KEY": "k", "DEEPSEEK_API_KEY": "d",
                "SEED_SPECIALTY": "general"}
    cfg = load_config(env_base)
    assert cfg.subscriptions == ["general"]
