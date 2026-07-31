from __future__ import annotations
import asyncio
import logging

from config import load_config
from observability import setup_logging, alert_crash
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider
from client import ConclaveClient
from brain import Brain
from loop import main_loop


def make_provider(cfg):
    if cfg.llm_provider == "ollama":
        return OllamaProvider(cfg.ollama_base_url, cfg.ollama_model)
    return OpenAICompatibleProvider(cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model)


async def run() -> None:
    cfg = load_config()
    setup_logging(cfg.specialty)
    client = ConclaveClient(cfg)
    brain = Brain(make_provider(cfg), cfg.specialty)
    try:
        await main_loop(client, brain, cfg)  # connect() inside sets client.agent_id
    except Exception as exc:
        logging.getLogger("seed").exception("fatal")
        await alert_crash(cfg.telegram_webhook, cfg.specialty, str(exc))
        raise


if __name__ == "__main__":
    asyncio.run(run())
