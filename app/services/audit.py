"""Admin-action audit logging (OWASP A09).

Privileged admin mutations — key minting, bans/restore, cost-cap overrides —
append an audit_log row with severity 'admin_action' so a solo operator can scan
for admin abuse if the single static admin key is ever compromised. The dashboard
metrics endpoint already surfaces the recent-audit tail across all severities, so
these need no separate read surface.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

ADMIN_SEVERITY = "admin_action"


async def log_admin_action(
    pool: asyncpg.Pool,
    action: str,
    *,
    agent_id: UUID | None = None,
    metadata: dict | None = None,
) -> None:
    await pool.execute(
        """INSERT INTO audit_log (agent_id, action, severity, metadata)
           VALUES ($1, $2, $3, $4)""",
        agent_id, action, ADMIN_SEVERITY, metadata or {},
    )
