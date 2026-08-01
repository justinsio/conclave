from __future__ import annotations

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

_CLOSED_STATUSES = ("resolved", "deleted")

# The closed category set. Imported rather than re-declared so a fifth category
# cannot be added in one place and silently break override parsing here.
from app.models import VALID_CATEGORIES  # noqa: E402


def parse_ttl_overrides(raw: str) -> dict[str, int | str]:
    """Parse POST_EXPIRY_TTL_OVERRIDES: "category=days|never", comma-separated.

    Raises at boot rather than degrading. Three traps closed here:
      * `0` is rejected — it means "delete everything closed more than 0 days
        ago", i.e. wipe the category's whole history on the next sweep. To keep
        a category forever use `never`; to disable expiry use POST_EXPIRY_ENABLED.
      * Category keys are validated against the real closed set. A typo, or a
        capitalised `Coding`, would otherwise match zero rows and silently fail
        to protect the history it was written to protect.
      * Negative values are rejected for the same reason as 0.
    """
    overrides: dict[str, int | str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"POST_EXPIRY_TTL_OVERRIDES entry {chunk!r} must be 'category=days' or 'category=never'"
            )
        key, _, value = chunk.partition("=")
        key, value = key.strip(), value.strip()
        if key not in VALID_CATEGORIES:
            raise ValueError(
                f"POST_EXPIRY_TTL_OVERRIDES category {key!r} is not one of: "
                f"{', '.join(sorted(VALID_CATEGORIES))}"
            )
        if value == "never":
            overrides[key] = "never"
            continue
        try:
            days = int(value)
        except ValueError:
            raise ValueError(
                f"POST_EXPIRY_TTL_OVERRIDES value for {key!r} must be an integer or 'never', got {value!r}"
            )
        if days < 1:
            raise ValueError(
                f"POST_EXPIRY_TTL_OVERRIDES {key}={days} is rejected: 0 means "
                "'delete everything closed more than 0 days ago'. Use 'never' to "
                "keep a category, or POST_EXPIRY_ENABLED=false to disable expiry."
            )
        overrides[key] = days
    return overrides


async def run_expiry(
    pool: asyncpg.Pool,
    ttl_days: int = 90,
    overrides: dict[str, int | str] | None = None,
) -> int:
    """
    Hard-delete posts that have been closed for longer than their TTL.

    Measures age as COALESCE(closed_at, created_at) so admin-deleted posts
    (which have no closed_at) are measured from creation date.
    Answers cascade on DELETE. seed_threads and corpus_staging FKs are SET NULL.
    Returns the number of posts deleted.

    Posts that produced a corpus entry are EXEMPT — deleting them would strand
    the provenance that answers "what did this bad entry contaminate?".
    ⚠️ That exemption keys on training_corpus.source_post_id, which is NULL for
    every entry promoted before migration 019 and cannot be backfilled (see the
    NO BACKFILL note in that migration). Those source posts are NOT protected.
    """
    overrides = overrides or {}
    statuses = list(_CLOSED_STATUSES)

    # 'never' keys must stay in `overridden` so the default sweep below excludes
    # them. Dropping them here is the trap the spec names: every 'never'
    # category would fall into the default DELETE and be destroyed at the
    # default TTL — the exact inverse of what the operator asked for.
    overridden = list(overrides.keys())
    by_ttl: dict[int, list[str]] = {}
    for cat, value in overrides.items():
        if value == "never":
            continue
        by_ttl.setdefault(int(value), []).append(cat)

    deleted = 0

    for days, cats in by_ttl.items():
        rows = await pool.fetch(
            """DELETE FROM posts
                WHERE status = ANY($1)
                  AND category = ANY($2)
                  AND COALESCE(closed_at, created_at) < NOW() - ($3 || ' days')::INTERVAL
                  AND NOT EXISTS (
                      SELECT 1 FROM training_corpus tc WHERE tc.source_post_id = posts.id
                  )
                RETURNING id""",
            statuses, cats, str(days),
        )
        deleted += len(rows)

    # Everything not overridden, at the default TTL. category IS NULL is checked
    # explicitly: `category <> ALL(...)` is NULL for a NULL category, which would
    # silently exempt those posts.
    rows = await pool.fetch(
        """DELETE FROM posts
            WHERE status = ANY($1)
              AND (category IS NULL OR category <> ALL($2))
              AND COALESCE(closed_at, created_at) < NOW() - ($3 || ' days')::INTERVAL
              AND NOT EXISTS (
                  SELECT 1 FROM training_corpus tc WHERE tc.source_post_id = posts.id
              )
            RETURNING id""",
        statuses, overridden, str(ttl_days),
    )
    deleted += len(rows)

    if deleted:
        logger.info(
            "post_expiry: purged %d closed posts (default ttl=%d days, overrides=%s)",
            deleted, ttl_days, overrides or "none",
        )
    return deleted


# ─── Background worker ────────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None


async def _worker(
    pool: asyncpg.Pool, interval: int, ttl_days: int,
    overrides: dict[str, int | str] | None = None,
) -> None:
    while True:
        try:
            await run_expiry(pool, ttl_days=ttl_days, overrides=overrides)
        except Exception:
            logger.exception("post_expiry: worker error")
        await asyncio.sleep(interval)


async def start_post_expiry_worker(
    pool: asyncpg.Pool,
    interval: int = 3600,
    ttl_days: int = 90,
    enabled: bool = False,
    overrides: dict[str, int | str] | None = None,
) -> None:
    """Start the sweep, unless expiry is disabled.

    Disabled is the DEFAULT and the safe state: the worker simply never starts,
    so nothing can be deleted by accident. An operator who wants deletion opts
    in with POST_EXPIRY_ENABLED=true.
    """
    global _worker_task
    if not enabled:
        logger.info(
            "post_expiry: disabled (POST_EXPIRY_ENABLED=false) — no posts will be "
            "deleted. This is the default; resolved history is kept indefinitely."
        )
        _worker_task = None
        return
    logger.warning(
        "post_expiry: ENABLED — closed posts older than %d days will be HARD "
        "DELETED with their answers. Posts that produced a corpus entry are exempt.",
        ttl_days,
    )
    _worker_task = asyncio.create_task(_worker(pool, interval, ttl_days, overrides))


async def stop_post_expiry_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
