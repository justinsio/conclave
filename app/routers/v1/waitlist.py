from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import settings
from app.database import get_pool
from app.models import WaitlistCreate

router = APIRouter(prefix="/v1", tags=["waitlist"])

# Deliberately permissive — we validate shape, not deliverability (no MX checks).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IP_HOURLY_LIMIT = 5  # new sign-ups per IP per hour — a list-bombing guard


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    # Trust X-Forwarded-For ONLY when the direct peer is a configured proxy
    # (Cloudflare/Hetzner edge). Otherwise XFF is attacker-controlled and would
    # let a spoofed hop per request defeat the per-IP cap, so key off the peer.
    trusted = {ip.strip() for ip in settings.trusted_proxy_ips.split(",") if ip.strip()}
    if peer in trusted:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return peer


@router.post("/waitlist")
async def join_waitlist(
    body: WaitlistCreate,
    request: Request,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Public, unauthenticated waitlist sign-up for the pre-launch marketing site."""
    # Honeypot: a bot filled the hidden field. Look successful, store nothing.
    if body.hp.strip():
        return {"status": "subscribed"}

    email = body.email.strip()
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="a valid email address is required")
    email_norm = email.lower()

    ip_hash = hashlib.sha256(_client_ip(request).encode("utf-8")).hexdigest()

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = await pool.fetchval(
        "SELECT COUNT(*) FROM waitlist WHERE ip_hash = $1 AND created_at > $2",
        ip_hash, since,
    )
    if recent >= IP_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="too many sign-ups from this address — please try again later",
        )

    row = await pool.fetchrow(
        """INSERT INTO waitlist (email, email_norm, ip_hash, source)
                VALUES ($1, $2, $3, $4)
           ON CONFLICT (email_norm) DO NOTHING
             RETURNING id""",
        email, email_norm, ip_hash, body.source,
    )

    if row is None:
        response.status_code = 200
        return {"status": "already_subscribed"}
    response.status_code = 201
    return {"status": "subscribed"}
