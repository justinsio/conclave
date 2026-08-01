"""Production safety preflight (R2/R3).

In production, refuse to boot unless the trust-and-safety controls are correctly
configured — turns a silent misconfig (the audit's "controls ship OFF by default")
into a loud, fail-fast RuntimeError before the app serves a single request.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_ADMIN_KEY = "dev-admin-key"


def assert_production_safety(settings) -> None:
    """No-op unless settings.environment == 'production'.

    In production: collect ALL hard-control failures and raise a single RuntimeError
    listing each; emit loud warnings (but still boot) for soft-control gaps.
    """
    if settings.environment != "production":
        return

    failures: list[str] = []
    if not settings.admin_api_key or settings.admin_api_key == _DEFAULT_ADMIN_KEY:
        failures.append(
            "admin_api_key is unset or still the dev default — set a strong ADMIN_API_KEY"
        )
    if not settings.moderation_gate_enabled:
        failures.append("moderation_gate_enabled is False — set MODERATION_GATE_ENABLED=true")
    if not settings.rate_limit_enabled:
        failures.append("rate_limit_enabled is False — set RATE_LIMIT_ENABLED=true")
    if not settings.anthropic_api_key:
        failures.append(
            "anthropic_api_key is empty — the moderation gate needs ANTHROPIC_API_KEY"
        )
    if not settings.trusted_proxy_ips:
        failures.append(
            "trusted_proxy_ips is empty — behind a proxy this collapses the rate limiter "
            "to one shared bucket (all clients share the proxy's IP); set TRUSTED_PROXY_IPS "
            "to the edge/proxy IP(s)"
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

    Deliberately NOT part of assert_production_safety. That function returns
    immediately unless environment == 'production', and inside production a
    disabled moderation gate is already a HARD failure that raises before the
    soft-warning section runs — so this warning placed there would be
    unreachable in both directions.

    The operator who needs to hear it is exactly the self-hoster running
    without ENVIRONMENT=production, who has deliberately or accidentally
    turned the paid gate off and should know what that leaves them with.
    """
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
