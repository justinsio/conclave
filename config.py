from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SeedConfig:
    api_url: str
    agent_key: str
    deepseek_api_key: str
    specialty: str
    subscriptions: list[str]
    llm_provider: str
    poll_interval: int
    solo_threshold: float
    open_thread_threshold: float
    draft_after_minutes: int
    answer_after_minutes: int
    deepseek_base_url: str
    deepseek_model: str
    ollama_base_url: str
    ollama_model: str
    telegram_webhook: str | None


def load_config(env: dict | None = None) -> SeedConfig:
    e = env if env is not None else os.environ
    specialty = e.get("SEED_SPECIALTY", "general")
    subs = [specialty] if specialty == "general" else [specialty, "general"]
    return SeedConfig(
        api_url=e["CONCLAVE_API_URL"],
        agent_key=e["CONCLAVE_AGENT_KEY"],
        deepseek_api_key=e["DEEPSEEK_API_KEY"],
        specialty=specialty,
        subscriptions=subs,
        llm_provider=e.get("LLM_PROVIDER", "deepseek"),
        poll_interval=int(e.get("POLL_INTERVAL_SECONDS", "10")),
        solo_threshold=float(e.get("SOLO_THRESHOLD", "0.85")),
        open_thread_threshold=float(e.get("OPEN_THREAD_THRESHOLD", "0.60")),
        draft_after_minutes=int(e.get("DRAFT_AFTER_MINUTES", "5")),
        answer_after_minutes=int(e.get("ANSWER_AFTER_MINUTES", "15")),
        deepseek_base_url=e.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=e.get("DEEPSEEK_MODEL", "deepseek-chat"),
        ollama_base_url=e.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=e.get("OLLAMA_MODEL", "llama3.1:8b"),
        telegram_webhook=e.get("TELEGRAM_WEBHOOK") or None,
    )
