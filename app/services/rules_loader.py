"""Load the published rules list from an operator-supplied file.

The rules served at GET /v1/rules are documentation - nothing enforces them.
Several built-in rules (coordinated upvoting, fake accounts) are meaningless on
a small private team network, so this is the most likely thing an operator
customises.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def load_rules(path: str, defaults: list[str]) -> list[str]:
    """One rule per line; '#' comments and blank lines skipped.

    Falls back to `defaults` when the path is unset, unreadable, or yields no
    rules. Never raises - an unreadable rules file must not stop the app.
    """
    if not path:
        return list(defaults)

    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("rules file %s could not be read (%s) - using built-in rules", path, exc)
        return list(defaults)

    rules = [
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith("#")
    ]
    if not rules:
        logger.warning("rules file %s contained no rules - using built-in rules", path)
        return list(defaults)
    return rules
