from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.config import settings
from app.database import close_pool, init_pool
from app.routers.internal.threads import router as threads_router
from app.services.blind_phase import start_blind_phase_worker, stop_blind_phase_worker
from app.services.coordinator import start_coordinator_worker, stop_coordinator_worker

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await init_pool()
    await start_blind_phase_worker(pool, interval=settings.blind_phase_check_interval)
    await start_coordinator_worker(pool, interval=settings.coordinator_fallback_interval)
    yield
    await stop_blind_phase_worker()
    await stop_coordinator_worker()
    await close_pool()


app = FastAPI(
    title="Conclave — Seed Discussion Protocol",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(threads_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
