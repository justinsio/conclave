from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SeedConfig:
    api_url: str
    agent_key: str
    llm_api_key: str
    specialty: str
    subscriptions: list[str]
    llm_provider: str
    poll_interval: int
    solo_threshold: float
    open_thread_threshold: float
    draft_after_minutes: int
    answer_after_minutes: int
    llm_base_url: str
    llm_model: str
    ollama_base_url: str
    ollama_model: str
    telegram_webhook: str | None


def load_config(env: dict | None = None) -> SeedConfig:
    e = env if env is not None else os.environ
    specialty = e.get("SEED_SPECIALTY", "general")
    subs = [specialty] if specialty == "general" else [specialty, "general"]

    # Default to ollama: a $0 local-first default that boots with no API key.
    provider = e.get("LLM_PROVIDER", "ollama")
    llm_api_key = e.get("LLM_API_KEY", "")
    llm_base_url = e.get("LLM_BASE_URL", "")

    # Reject unknown values explicitly. Without this, the legacy
    # LLM_PROVIDER=deepseek (still in the old .env.example) skips validation,
    # then make_provider falls through to the hosted client with an empty
    # base_url — every completion POSTs to "/chat/completions" with no host.
    if provider not in ("ollama", "openai_compatible"):
        raise ValueError(
            f"LLM_PROVIDER={provider!r} is not recognised — use 'ollama' (local, "
            "no API key) or 'openai_compatible' (any hosted OpenAI-compatible "
            "endpoint, including DeepSeek: set LLM_BASE_URL=https://api.deepseek.com)"
        )

    # Only the hosted provider needs credentials. Requiring them unconditionally
    # meant an Ollama-only self-hoster could not boot a seed at all.
    if provider == "openai_compatible":
        if not llm_api_key:
            raise ValueError(
                "LLM_PROVIDER=openai_compatible requires LLM_API_KEY "
                "(use LLM_PROVIDER=ollama to run fully local with no key)"
            )
        if not llm_base_url:
            raise ValueError(
                "LLM_PROVIDER=openai_compatible requires LLM_BASE_URL, e.g. "
                "https://api.deepseek.com or https://api.groq.com/openai/v1"
            )

    # Reject EMPTY values, not just missing ones. compose passes these as
    # `${SEED_*_KEY:-}` — an unset variable would otherwise abort every compose
    # command, including a plain `docker compose up` with no seeds requested,
    # because interpolation happens before profile filtering. The cost of that
    # default is that a seed started without a key receives "" rather than
    # nothing, which used to pass validation here and then crash inside httpx
    # with `LocalProtocolError: Illegal header value b'Bearer '` — in a restart
    # loop, telling the operator nothing about what they actually forgot.
    for _var in ("CONCLAVE_API_URL", "CONCLAVE_AGENT_KEY"):
        if not e.get(_var, "").strip():
            raise ValueError(
                f"{_var} is empty — a seed cannot start without it. Mint an agent "
                "key with `docker compose run --rm api python scripts/mint_key.py "
                "--name <agent>` and set it in .env, or stop the seeds profile."
            )

    return SeedConfig(
        api_url=e["CONCLAVE_API_URL"],
        agent_key=e["CONCLAVE_AGENT_KEY"],
        llm_api_key=llm_api_key,
        specialty=specialty,
        subscriptions=subs,
        llm_provider=provider,
        poll_interval=int(e.get("POLL_INTERVAL_SECONDS", "10")),
        solo_threshold=float(e.get("SOLO_THRESHOLD", "0.85")),
        open_thread_threshold=float(e.get("OPEN_THREAD_THRESHOLD", "0.60")),
        draft_after_minutes=int(e.get("DRAFT_AFTER_MINUTES", "5")),
        answer_after_minutes=int(e.get("ANSWER_AFTER_MINUTES", "15")),
        llm_base_url=llm_base_url,
        llm_model=e.get("LLM_MODEL", "deepseek-chat"),
        ollama_base_url=e.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=e.get("OLLAMA_MODEL", "llama3.1:8b"),
        telegram_webhook=e.get("TELEGRAM_WEBHOOK") or None,
    )
