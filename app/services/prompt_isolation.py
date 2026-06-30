"""Prompt-injection isolation for untrusted text (R1).

SYNCED COPY — duplicated in conclave (app/services/prompt_isolation.py) and
conclave-seeds (prompt_isolation.py). Keep both in sync; both test suites assert
identical properties. See the R1 design spec §3.1 and §9 (TD-1) for why it is
duplicated rather than packaged.
"""
from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass

# Any delimiter-shaped marker: [WORD_START] / [WORD_END], optional _<nonce> suffix.
# Generic by SHAPE (not an enumerated label list) so a new label can never open an
# un-stripped breakout token.
# Case-sensitive is intentional: this module always emits markers uppercase, so
# matching only uppercase START/END does not reduce strip coverage.
_MARKER_RE = re.compile(r"\[[A-Za-z][A-Za-z0-9_]*_(?:START|END)(?:_[0-9A-Za-z]+)?\]")


@dataclass
class Isolated:
    block: str        # delimited block safe to embed in a prompt
    tampered: bool    # a marker-shaped token was found and stripped (hostile signal)


def _normalize(text: str) -> str:
    # NFKC folds fullwidth/compatibility brackets to ASCII; then drop Unicode
    # "format" (Cf) characters (zero-width spaces, BOM, bidi marks, soft hyphen)
    # that attackers stuff inside marker names to evade the strip.
    folded = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


def isolate(content: str, *, label: str = "AGENT_CONTENT") -> Isolated:
    """Wrap untrusted `content` in unguessable nonce delimiters after stripping any
    delimiter-shaped markers from it.

    The nonce stops an attacker forging the close marker (they can't guess it); the
    strip removes any literal marker and is the tamper signal. Both run every call.
    """
    cleaned, n = _MARKER_RE.subn("", _normalize(content))
    nonce = secrets.token_hex(8)  # 16 hex chars
    block = f"[{label}_START_{nonce}]\n{cleaned}\n[{label}_END_{nonce}]"
    return Isolated(block=block, tampered=n > 0)


def contains_marker(text: str) -> bool:
    """True if `text` contains a delimiter-shaped marker token (hostile signal)."""
    return _MARKER_RE.search(_normalize(text)) is not None
