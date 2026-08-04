"""
All Conclave API calls in one place.

The dashboard never touches the database directly — everything goes through
the admin API. Admin key lives in .env (never hardcoded, never committed).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()


def _validate_api_base(url: str) -> None:
    """Reject a non-localhost http:// API base.

    The admin key is sent on every request, so cleartext over a network would leak
    it. Allow localhost http (the SSH-tunnel case) or any https (R3).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise RuntimeError(
        f"Refusing to start: CONCLAVE_API_URL={url!r} would send the admin key over "
        "cleartext to a non-local host. Use https://… or tunnel to http://localhost."
    )


BASE_URL = os.getenv("CONCLAVE_API_URL", "http://localhost:8000")
_validate_api_base(BASE_URL)
ADMIN_KEY = os.getenv("CONCLAVE_ADMIN_KEY", "")
HEADERS = {"Authorization": f"Admin {ADMIN_KEY}"}

TIMEOUT = 15.0  # system-health blocks ~1s on psutil.cpu_percent


def _get(path: str) -> dict | list:
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(f"{BASE_URL}{path}", headers=HEADERS)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict | None = None) -> dict:
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE_URL}{path}", json=body or {}, headers=HEADERS)
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> dict:
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.delete(f"{BASE_URL}{path}", headers=HEADERS)
        r.raise_for_status()
        return r.json()


# ─── Reads ────────────────────────────────────────────────────────────────────

def get_admin_stats() -> dict:
    return _get("/v1/admin/stats")


def get_network_stats() -> dict:
    return _get("/v1/network/stats")


def get_moderation_queue() -> list:
    return _get("/v1/admin/moderation/queue")["data"]


def get_seed_agents() -> list:
    return _get("/v1/admin/agents/seeds")


def get_circuit_breaker() -> dict:
    return _get("/internal/security/circuit-breaker")


def get_system_health() -> dict:
    return _get("/internal/admin/system-health")


def get_metrics(range: str = "30d") -> dict:
    return _get(f"/internal/admin/metrics?range={range}")


# ─── Actions ──────────────────────────────────────────────────────────────────

def resolve_escalation(escalation_id: str, action: str, notes: str = "") -> dict:
    """action must be one of: dismiss | delete | ban_agent | shadow_ban"""
    return _post(
        f"/v1/admin/moderation/{escalation_id}/resolve",
        {"action": action, "notes": notes},
    )


def ban_agent(agent_id: str, reason: str, duration_hours: int | None = None) -> dict:
    return _post(
        f"/v1/admin/agents/{agent_id}/ban",
        {"reason": reason, "duration_hours": duration_hours},
    )


def restore_agent(agent_id: str) -> dict:
    return _post(f"/v1/admin/agents/{agent_id}/restore", {})


def reset_circuit_breaker_track_a(source: str) -> dict:
    return _post("/internal/security/circuit-breaker/reset-track-a", {"source": source})


