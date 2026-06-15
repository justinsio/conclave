import logging
from observability import setup_logging, alert_crash


def test_setup_logging_writes_to_stdout_with_seed_name(capsys):
    setup_logging("coding")
    logging.getLogger("seed").info("hello world")
    out = capsys.readouterr().out
    assert "hello world" in out
    assert "coding" in out


async def test_alert_crash_noop_without_webhook():
    # Must not raise when no webhook is configured.
    await alert_crash(None, "coding", "boom")
