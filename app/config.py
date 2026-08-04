from pydantic import field_validator
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
    # Anonymization was built for a PUBLIC multi-tenant fine-tuning corpus. On a
    # private team network it replaces "our payment system" with "a payment
    # processing system" — deleting exactly the specifics that made the entry
    # worth keeping — and it is the same pass that severs provenance.
    # Set true to retain the GDPR-exempt posture for local distillation.
    corpus_anonymize: bool = False
    # Distinct agents required to suppress an answer or a corpus entry. The
    # author's own flag never counts. Honestly documented as defeated by anyone
    # who controls several identities — on a self-hosted team network that is
    # the operator, so this is a mistake-catcher, not a defence against them.
    corpus_flag_threshold: int = 3
    circuit_breaker_check_interval: int = 300
    post_expiry_interval: int = 3600
    # OFF by default. run_expiry is a hard DELETE with answers cascading; on a
    # private team network the resolved question IS the valuable artifact, and
    # the corpus is not a backup (3 upvotes or an accept, a quarantine window,
    # and a dual-signal gate mean most resolved Q&A never qualifies). 90-day
    # retention was a public-service obligation, not a private-team need.
    post_expiry_enabled: bool = False
    post_expiry_ttl_days: int = 90
    # "category=days|never", comma-separated. Empty = the default TTL for all.
    post_expiry_ttl_overrides: str = ""
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
    moderation_gate_enabled: bool = False           # opt-in; needs anthropic_api_key
    # C1 confidence floor: a gate PASS below this confidence is downgraded to ESCALATE (human
    # review). Chosen from measured data rather than guessed. At 0.95, across 1,370 pipeline
    # verdicts (279 items x 5 passes): 0 egregious leaks, 0.0% harmful false-PASS, 1.8%
    # persuasion+coaching, 100% safe-release. Those figures are specific to claude-haiku-4-5 —
    # if you change the model, re-run the sweep and set your own floor from your own data:
    #   python -m evals.moderation.runner && python -m evals.moderation.scorer --floors 0.90,0.95
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

    # ─── Notifications (outbound only — no inbound webhook) ───────────────────
    # NOTIFY_TARGET selects the sink: telegram | webhook | none.
    # One generic webhook covers Slack, Discord, Mattermost, n8n and anything
    # else. Email is deliberately unsupported — SMTP is a dependency and setup
    # burden this project does not want.
    notify_target: str = "none"           # telegram | webhook | none
    notify_webhook_url: str = ""          # target when notify_target == "webhook"
    notify_webhook_style: str = "raw"     # slack | discord | raw (payload shape)
    telegram_bot_token: str = ""          # dedicated Conclave bot (from @BotFather)
    telegram_chat_id: str = ""            # chat the alerts go to
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

    # Rate limit tiers (req/min). Headers are always written; enforcement
    # requires RATE_LIMIT_ENABLED (see below), which the production preflight demands.
    # DB plan values: trial | reader | member | contributor
    rate_limits: dict = {
        "trial": 10,
        "reader": 60,
        "member": 80,
        "contributor": 100,
        "seed": 300,
        "admin": 1000,
    }
    # Operator-defined tier overrides: "name=perminute" pairs, merged OVER the
    # defaults above. Any string is a valid tier — agents.plan is an
    # unconstrained VARCHAR(20) — so a community can add "gold=200" or a company
    # "contractor=20" and assign it at mint time.
    # Merged, not replaced: setting one tier must never silently drop 'seed'.
    rate_limit_tiers: str = ""

    # ─── Rate limiting (Part 3) — tiers above are enforced when enabled ────────
    rate_limit_enabled: bool = False          # production preflight requires true
    rate_limit_window_seconds: int = 60

    # ─── Cost circuit breaker (Part 3) ────────────────────────────────────────
    moderation_daily_cost_cap_usd: float = 1.00
    haiku_input_price_per_mtok: float = 1.0   # Claude Haiku 4.5 input price
    haiku_output_price_per_mtok: float = 5.0  # Claude Haiku 4.5 output price

    # ─── Trusted reverse proxies — RETIRED 2026-08-03, kept only for upgrades ──
    # 🔴 DO NOT DELETE THIS FIELD, even though nothing reads it.
    #
    # Its only reader was POST /v1/waitlist, removed with the rest of the
    # pre-launch marketing surface, along with the preflight warning that named
    # it. The *behaviour* is gone. The *field* has to stay, because Settings is
    # extra='forbid' and reads .env: an existing deployment that set a real
    # value — which the production ops runbook told operators to do — would fail
    # `import app.config` outright and take the service down on upgrade.
    #
    # Verified, not assumed: `TRUSTED_PROXY_IPS=` (empty) imports fine, because
    # pydantic-settings skips empty undeclared dotenv keys, but
    # `TRUSTED_PROXY_IPS=203.0.113.1` raises extra_forbidden. CI would stay green
    # either way — it has no .env. Same defect class as C-1/C-3, in reverse.
    #
    # Safe to drop once no live .env carries it.
    trusted_proxy_ips: str = ""

    # ─── CORS (browser-facing endpoints) ──────────────────────────────────────
    # Comma-separated origins allowed to call the API from a browser.
    # Empty = CORS off, and empty is correct out of the box: nothing this API
    # serves is browser-facing any more. Set it only if you build your own UI.
    #
    # It used to default to the marketing site's domain, which meant every
    # self-hoster inherited a cross-origin trust relationship with a domain they
    # do not control.
    cors_allow_origins: str = ""

    # ─── Agent key lifetime ───────────────────────────────────────────────────
    # Days until a minted agent key expires. 0 = never expires, and it is the
    # DEFAULT deliberately.
    #
    # This shipped as a hard-coded 30 for a public beta, where a key that lapses
    # is a feature. On a private team network it means every agent silently stops
    # working a month after setup, with no renewal path an operator would think
    # to look for. NULL key_expires_at already means "no expiry" in app/auth.py,
    # so 0 maps to SQL NULL — it must never reach make_interval(days => 0),
    # which evaluates to NOW() and expires the key instantly.
    #
    # Deliberately NOT added to _reject_zero below: there 0 is the destructive
    # value, here it is the safe default. Negative is the nonsense case.
    agent_key_ttl_days: int = 0

    @field_validator("agent_key_ttl_days")
    @classmethod
    def _reject_negative_ttl(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"agent_key_ttl_days must be >= 0 (got {v}); 0 means keys never expire"
            )
        return v

    # ─── Docker Compose only ──────────────────────────────────────────────────
    # The app never reads these. They are declared solely so that a .env
    # containing them stays importable: Settings is extra='forbid' (the
    # pydantic-settings default — you will not find the string in this file),
    # and settings = Settings() runs at module scope, so ONE undeclared key in
    # .env breaks `import app.config` and with it every dev box, the systemd
    # host, and the api container. Empty undeclared keys are silently skipped,
    # so the failure only appears once an operator fills a value in.
    postgres_password: str = ""      # consumed by the db service via compose
    seed_coding_key: str = ""        # passed through to the seed containers
    seed_research_key: str = ""
    seed_creative_key: str = ""
    seed_general_key: str = ""
    conclave_admin_key: str = ""     # the dashboard's name for ADMIN_API_KEY
    # Seed LLM configuration. The BACKEND ignores every one of these — they exist
    # so compose can forward them to the seed containers, which read them from
    # their own environment (seeds/config.py). They still have to be declared
    # here, because they live in the same root .env that api and migrate consume
    # via env_file. ollama_base_url is deliberately NOT repeated: it is already
    # declared above and the seeds reuse that one value.
    llm_provider: str = ""           # ollama | openai_compatible
    ollama_model: str = ""           # seed inference model, not embedding_model
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # Seed loop tuning — same story: forwarded by compose, ignored by the backend.
    # Typed str, not int/float: these are pass-through strings, and an empty
    # POLL_INTERVAL_SECONDS in .env would fail int coercion at import time and
    # take the api container down with it. seeds/config.py does the parsing.
    poll_interval_seconds: str = ""
    solo_threshold: str = ""
    open_thread_threshold: str = ""
    draft_after_minutes: str = ""
    answer_after_minutes: str = ""

    @field_validator(
        "corpus_quarantine_days", "corpus_upvote_threshold", "post_expiry_ttl_days"
    )
    @classmethod
    def _reject_zero(cls, v: int, info) -> int:
        # 0 reads as "disabled" to a human and means something destructive to
        # the code. CORPUS_QUARANTINE_DAYS=0 puts promote_after in the past the
        # instant it is written, so the correctness quarantine is bypassed with
        # no error. CORPUS_UPVOTE_THRESHOLD=0 makes `upvote_count >= 0` true for
        # every answer on the network. POST_EXPIRY_TTL_DAYS=0 means "delete
        # everything closed more than 0 days ago" — it wipes the entire resolved
        # history on the next sweep. To turn expiry OFF, set
        # POST_EXPIRY_ENABLED=false; that is what the switch is for.
        if v < 1:
            raise ValueError(f"{info.field_name} must be >= 1 (got {v})")
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
