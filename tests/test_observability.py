import json
import logging
from observability import setup_logging, alert_crash, log_llm_usage


def test_setup_logging_writes_to_stdout_with_seed_name(capsys):
    setup_logging("coding")
    logging.getLogger("seed").info("hello world")
    out = capsys.readouterr().out
    assert "hello world" in out
    assert "coding" in out


async def test_alert_crash_noop_without_webhook():
    # Must not raise when no webhook is configured.
    await alert_crash(None, "coding", "boom")


def test_log_llm_usage_emits_json_line(capsys):
    setup_logging("coding")
    log_llm_usage("answer", "qwen2.5:3b", 820, 140)
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if "llm_usage" in ln][-1]
    payload = json.loads(line[line.index("{"):])
    assert payload["event"] == "llm_usage"
    assert payload["purpose"] == "answer"
    assert payload["model"] == "qwen2.5:3b"
    assert payload["prompt_tokens"] == 820 and payload["completion_tokens"] == 140
    assert payload["total_tokens"] == 960
