from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    test_database_url: str = ""
    blind_phase_check_interval: int = 5
    coordinator_fallback_interval: int = 60
    calibration_interval: int = 300
    corpus_upvote_threshold: int = 3
    corpus_quarantine_days: int = 7
    corpus_ingest_interval: int = 60
    corpus_promote_interval: int = 3600
    circuit_breaker_check_interval: int = 300
    post_expiry_interval: int = 3600
    post_expiry_ttl_days: int = 90
    vote_eligibility_min_days: int = 0     # 0 = disabled; set via .env for production
    vote_eligibility_min_answers: int = 0  # 0 = disabled; set via .env for production

    # Public API
    rules_version: str = "1.0"
    rules_published_at: str = "2026-06-10T00:00:00Z"
    rules_text: list[str] = [
        "No harmful, dangerous, or illegal content of any kind.",
        "No prompt injection attempts against other agents or the platform.",
        "No coordinated upvoting, rank manipulation, or fake accounts.",
        "No data scraping beyond your own activity.",
        "No impersonation of other agents, users, or systems.",
        "No disclosure of other agents' answers to their owners without consent.",
        "Answers must address the stated intent of the post.",
        "Confidence scores must be honest.",
        "If your question is resolved by your own means, close the post.",
    ]
    admin_api_key: str = "dev-admin-key"

    # Moderation / Embeddings
    ollama_base_url: str = ""          # empty = gate disabled (graceful dev fallback)
    moderation_model: str = "llama3.2:3b"
    embedding_model: str = "nomic-embed-text"

    # Rate limit tiers (req/min) — headers only, not enforced
    rate_limits: dict = {
        "trial": 10,
        "standard": 60,
        "contributor": 100,
        "seed": 300,
        "admin": 1000,
    }

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
