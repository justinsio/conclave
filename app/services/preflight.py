"""Production safety preflight (R2/R3).

In production, refuse to boot unless the trust-and-safety controls are correctly
configured — turns a silent misconfig (the audit's "controls ship OFF by default")
into a loud, fail-fast RuntimeError before the app serves a single request.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_ADMIN_KEY = "dev-admin-key"

# Rejected as a SET, not one literal. .env.example ships a placeholder that is
# NOT "dev-admin-key", so a single-string check passed a value published in the
# repository. Any placeholder anyone might plausibly ship belongs here.
_PLACEHOLDER_ADMIN_KEYS = frozenset({
    _DEFAULT_ADMIN_KEY,
    "change-me-to-a-strong-secret",
    "change-me",
    "changeme",
})


def assert_production_safety(settings) -> None:
    """No-op unless settings.environment == 'production'.

    In production: collect ALL hard-control failures and raise a single RuntimeError
    listing each; emit loud warnings (but still boot) for soft-control gaps.
    """
    if settings.environment != "production":
        return

    failures: list[str] = []
    if not settings.admin_api_key or settings.admin_api_key in _PLACEHOLDER_ADMIN_KEYS:
        failures.append(
            "admin_api_key is unset or still a shipped placeholder — set a strong ADMIN_API_KEY"
        )
    if not settings.rate_limit_enabled:
        failures.append("rate_limit_enabled is False — set RATE_LIMIT_ENABLED=true")
    # Required only when the gate is on. Requiring it unconditionally made
    # ENVIRONMENT=production unbootable for a bring-your-own-LLM self-hoster:
    # the gate needs a paid provider, so "no gate" and "no key" travel together.
    if settings.moderation_gate_enabled and not settings.anthropic_api_key:
        failures.append(
            "anthropic_api_key is empty — the moderation gate needs ANTHROPIC_API_KEY "
            "(or set MODERATION_GATE_ENABLED=false to run without it)"
        )

    if failures:
        raise RuntimeError(
            "Refusing to start in production — unsafe configuration:\n  - "
            + "\n  - ".join(failures)
        )

    # Soft controls: recommended, not a safety floor. Warn loudly, still boot.
    if settings.notify_target == "none":
        logger.warning(
            "preflight: notify_target is 'none' — running blind to moderation "
            "escalations and cost-breaker trips (set NOTIFY_TARGET=telegram or webhook)"
        )
    if not settings.ollama_base_url:
        logger.warning(
            "preflight: ollama_base_url is empty — secondary consensus gate disabled "
            "(recommended set)"
        )


def warn_self_host_posture(settings) -> None:
    """Posture warnings that must reach an operator in ANY environment.

    Deliberately NOT part of assert_production_safety: that function returns
    immediately unless environment == 'production', while these must be heard
    everywhere. main.py calls both, in that order.

    Two of these were HARD production failures until 2026-08-02. Both were
    demoted here because both made ENVIRONMENT=production unbootable for the
    self-hoster the free release targets — the moderation gate needs a paid
    LLM, and a LAN deployment has no reverse proxy to declare. Demoting a
    control must not mean deleting it, so both still warn, in every
    environment, which is strictly more reachable than the hard check was.
    """
    if not settings.trusted_proxy_ips:
        logger.warning(
            "preflight: trusted_proxy_ips is empty — X-Forwarded-For is ignored, so the "
            "public waitlist form's per-IP throttle sees your proxy's address instead of "
            "the caller's. Agent rate limiting is unaffected (it is keyed on agent_id, "
            "not IP). Set TRUSTED_PROXY_IPS to the edge IP(s) if you front this with a proxy"
        )
    if not settings.moderation_gate_enabled:
        logger.warning(
            "preflight: moderation_gate_enabled is False — the structural pre-checks "
            "are the ONLY moderation. Correct for a trusted private network; make sure "
            "that is what you intend"
        )
    if not settings.ollama_base_url:
        logger.warning(
            "preflight: ollama_base_url is empty — knowledge retrieval will return "
            "nothing. Agents cannot search what the network has already learned "
            "(GET /v1/knowledge needs embeddings), and corpus ingest is skipped "
            "entirely"
        )
