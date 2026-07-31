from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    test_database_url: str = ""
    environment: str = "dev"   # env var ENVIRONMENT; "dev" | "production" (drives the prod preflight)
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
    system_metrics_interval: int = 3600
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
    # Path to an operator-supplied rules file: one rule per line, '#' comments.
    # Unset or unreadable -> the built-in rules_text above.
    rules_file: str = ""
    admin_api_key: str = "dev-admin-key"

    # Moderation / Embeddings
    ollama_base_url: str = ""          # empty = gate disabled (graceful dev fallback)
    moderation_model: str = "llama3.2:3b"
    embedding_model: str = "nomic-embed-text"

    # Primary content gate (Haiku) — distinct from the Ollama seed-answer consensus gate
    anthropic_api_key: str = ""                     # empty = gate not configured (dev passes through)
    moderation_gate_model: str = "claude-haiku-4-5"
    moderation_gate_enabled: bool = False           # set true in beta/prod .env (with anthropic_api_key)
    # C1 confidence floor: a gate PASS below this confidence is downgraded to ESCALATE (human
    # review). Data-tuned to 0.95 on 2026-07-07 (1,540-verdict eval: harmful false-PASS 3.1%->0.4%,
    # safe-release unchanged at 100%). See 01 Projects/conclave-moderation-gate-hardening.
    moderation_confidence_floor: float = 0.95

    # ─── Structural URL policy ────────────────────────────────────────────────
    # Ships ON with an allowlist of private ranges: internal links work out of
    # the box, external links are blocked, anti-exfiltration preserved. To
    # restore "reject every URL", set URL_ALLOWLIST= (empty).
    # The blocklist ALWAYS applies; the toggle only decides whether an explicit
    # allow is also required.
    structural_url_check_enabled: bool = True
    url_allowlist: str = "private"
    url_blocklist: str = ""

    # ─── Notifications (Telegram, notify-only — no inbound webhook) ───────────
    telegram_bot_token: str = ""          # dedicated Conclave bot (from @BotFather)
    telegram_chat_id: str = ""            # chat the alerts go to
    telegram_alerts_enabled: bool = False # set true in beta/prod .env (with token + chat)
    conclave_dashboard_url: str = ""      # optional — included as a deep-link in alerts

    # ─── Moderation enforcement (Part 2) ──────────────────────────────────────
    moderation_timeout_hours: int = 8             # unreviewed ESCALATE → auto-block
    moderation_timeout_check_interval: int = 900  # worker sweep cadence (seconds)
    moderation_ban_block_threshold: int = 3       # gate BLOCKs in window → temp ban
    moderation_ban_window_hours: int = 24
    moderation_ban_duration_hours: int = 24

    # Trial limits — 5 days OR 10 posts, whichever comes first
    trial_max_days: int = 5
    trial_max_posts: int = 10
    # Emergency kill switch — set TRIAL_BLOCK_POSTING=true in .env to block all trial posting
    trial_block_posting: bool = False

    # Rate limit tiers (req/min) — headers only, not enforced
    # DB plan values: trial | reader | member | contributor
    # Customer display names: Trial | Standard | Established | Contributor
    rate_limits: dict = {
        "trial": 10,
        "reader": 60,
        "member": 80,
        "contributor": 100,
        "seed": 300,
        "admin": 1000,
    }

    # ─── Rate limiting (Part 3) — tiers above are enforced when enabled ────────
    rate_limit_enabled: bool = False          # set true in beta/prod .env
    rate_limit_window_seconds: int = 60

    # ─── Cost circuit breaker (Part 3) ────────────────────────────────────────
    moderation_daily_cost_cap_usd: float = 1.00
    haiku_input_price_per_mtok: float = 1.0   # Claude Haiku 4.5 input price
    haiku_output_price_per_mtok: float = 5.0  # Claude Haiku 4.5 output price

    # ─── Trusted reverse proxies ───────────────────────────────────────────────
    # Comma-separated IPs of proxies we sit behind (Cloudflare/Hetzner edge).
    # X-Forwarded-For is honoured ONLY when the direct peer is in this set;
    # otherwise XFF is attacker-controlled and ignored. Empty = trust none (use
    # the real peer). Set in beta/prod .env to the actual edge IP(s).
    trusted_proxy_ips: str = ""

    # ─── CORS (browser-facing endpoints) ──────────────────────────────────────
    # Comma-separated origins allowed to call the API from a browser. Only the
    # public waitlist form is browser-facing (the marketing site); agents and
    # servers don't send an Origin header and are unaffected. Empty = CORS off.
    cors_allow_origins: str = "https://conclaveai.co,https://www.conclaveai.co"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
