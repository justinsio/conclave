from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import close_pool, init_pool
from app.services.preflight import assert_production_safety, warn_self_host_posture
from app.services.rate_limit import get_rate_limits, parse_rate_limit_tiers
from app.services.url_policy import build_policy
from app.routers.internal.threads import router as threads_router
from app.routers.internal.security import router as security_router
from app.routers.internal.corpus import router as corpus_router
from app.routers.internal.admin_metrics import router as admin_metrics_router
from app.routers.internal.admin_corpus import router as admin_corpus_router
from app.routers.internal.admin_flag_events import router as admin_flag_events_router
from app.routers.internal.admin_flags import router as admin_flags_router
from app.routers.internal.admin_cost import router as admin_cost_router
from app.routers.internal.admin_beta_users import router as admin_beta_users_router
from app.routers.v1.rules import router as rules_router
from app.routers.v1.agents import router as agents_router
from app.routers.v1.posts import router as posts_router
from app.routers.v1.answers import router as answers_router
from app.routers.v1.clarifications import router as clarifications_router
from app.routers.v1.votes import router as votes_router
from app.routers.v1.network import router as network_router
from app.routers.v1.admin import router as admin_router
from app.routers.v1.waitlist import router as waitlist_router
from app.routers.v1.knowledge import router as knowledge_router
from app.services.blind_phase import start_blind_phase_worker, stop_blind_phase_worker
from app.services.calibration import start_calibration_worker, stop_calibration_worker
from app.services.coordinator import start_coordinator_worker, stop_coordinator_worker
from app.services.corpus_pipeline import (
    start_corpus_ingest_worker,
    start_corpus_promote_worker,
    stop_corpus_ingest_worker,
    stop_corpus_promote_worker,
)
from app.services.circuit_breaker import (
    start_circuit_breaker_worker,
    stop_circuit_breaker_worker,
)
from app.services.moderation_timeout import (
    start_moderation_timeout_worker,
    stop_moderation_timeout_worker,
)
from app.services.post_expiry import (
    parse_ttl_overrides,
    start_post_expiry_worker,
    stop_post_expiry_worker,
)
from app.services.system_metrics import (
    start_system_metrics_worker,
    stop_system_metrics_worker,
)

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_safety(settings)  # R2/R3: refuse to boot unsafe in production
    warn_self_host_posture(settings)    # fires in any environment, unlike the above
    # Parse the URL lists now so a malformed entry fails the boot loudly
    # instead of being discovered on the first post.
    build_policy(settings)
    parse_rate_limit_tiers(settings.rate_limit_tiers)
    parse_ttl_overrides(settings.post_expiry_ttl_overrides)
    pool = await init_pool()

    # Sync runtime flags from DB so restarts honour any flags set while the app was running
    row = await pool.fetchrow(
        "SELECT trial_posting_blocked FROM circuit_breaker_state WHERE id = 1"
    )
    if row and row["trial_posting_blocked"]:
        settings.trial_block_posting = True

    await start_blind_phase_worker(pool, interval=settings.blind_phase_check_interval)
    await start_coordinator_worker(pool, interval=settings.coordinator_fallback_interval)
    await start_calibration_worker(pool, interval=settings.calibration_interval)
    await start_corpus_ingest_worker(pool, interval=settings.corpus_ingest_interval)
    await start_corpus_promote_worker(pool, interval=settings.corpus_promote_interval)
    await start_circuit_breaker_worker(pool, interval=settings.circuit_breaker_check_interval)
    await start_post_expiry_worker(
        pool,
        interval=settings.post_expiry_interval,
        ttl_days=settings.post_expiry_ttl_days,
        enabled=settings.post_expiry_enabled,
        overrides=parse_ttl_overrides(settings.post_expiry_ttl_overrides),
    )
    await start_moderation_timeout_worker(
        pool,
        interval=settings.moderation_timeout_check_interval,
        timeout_hours=settings.moderation_timeout_hours,
    )
    await start_system_metrics_worker(pool, interval=settings.system_metrics_interval)
    yield
    await stop_blind_phase_worker()
    await stop_coordinator_worker()
    await stop_calibration_worker()
    await stop_corpus_ingest_worker()
    await stop_corpus_promote_worker()
    await stop_circuit_breaker_worker()
    await stop_post_expiry_worker()
    await stop_moderation_timeout_worker()
    await stop_system_metrics_worker()
    await close_pool()


app = FastAPI(
    title="Conclave",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def rate_limit_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    plan = getattr(request.state, "agent_plan", "reader")
    limit = get_rate_limits().get(plan, 60)
    remaining = getattr(request.state, "rate_limit_remaining", limit)
    reset_ts = int(time.time()) + 60
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_ts)
    response.headers["X-RateLimit-Window"] = "60"
    return response


# CORS for the browser-facing waitlist form (marketing site → API is cross-origin).
# Added after the rate-limit middleware so it sits outermost and answers preflights.
_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )


app.include_router(threads_router)
app.include_router(security_router)
app.include_router(corpus_router)
app.include_router(admin_metrics_router)
app.include_router(admin_flags_router)
app.include_router(admin_corpus_router)
app.include_router(admin_flag_events_router)
app.include_router(admin_cost_router)
app.include_router(admin_beta_users_router)
app.include_router(rules_router)
app.include_router(agents_router)
app.include_router(posts_router)
app.include_router(answers_router)
app.include_router(clarifications_router)
app.include_router(votes_router)
app.include_router(network_router)
app.include_router(admin_router)
app.include_router(waitlist_router)
app.include_router(knowledge_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
