"""Tests for dashboard endpoints: system metrics service, system-health,
internal metrics, /v1/admin/stats, and /v1/admin/agents/seeds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.services.system_metrics as system_metrics
from app.services.system_metrics import record_system_metrics

pytestmark = pytest.mark.usefixtures("clean_db")

ADMIN = {"Authorization": "Admin dev-admin-key"}


# ─── system_metrics service ───────────────────────────────────────────────────

async def test_record_system_metrics_inserts_row(db_pool):
    await record_system_metrics(db_pool)
    row = await db_pool.fetchrow("SELECT * FROM system_metrics_hourly")
    assert row is not None
    assert 0.0 <= row["cpu_pct"] <= 100.0
    assert 0.0 <= row["memory_pct"] <= 100.0
    assert 0.0 <= row["disk_pct"] <= 100.0
    assert 0.0 <= row["db_pool_pct"] <= 100.0


async def test_record_system_metrics_upserts_same_hour(db_pool):
    await record_system_metrics(db_pool)
    await record_system_metrics(db_pool)
    count = await db_pool.fetchval("SELECT COUNT(*) FROM system_metrics_hourly")
    assert count == 1


async def test_record_system_metrics_updates_last_recorded_at(db_pool):
    before = datetime.now(timezone.utc)
    await record_system_metrics(db_pool)
    assert system_metrics.get_last_recorded_at() is not None
    assert system_metrics.get_last_recorded_at() >= before


# ─── GET /internal/admin/system-health ───────────────────────────────────────

async def test_system_health_shape(client):
    r = await client.get("/internal/admin/system-health", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["cpu_pct"], float)
    assert set(data["memory"].keys()) == {"pct", "used_gb", "total_gb"}
    assert set(data["disk"].keys()) == {"pct", "used_gb", "free_gb"}
    assert set(data["disk_io"].keys()) == {"read_mb_s", "write_mb_s"}
    assert set(data["db_pool"].keys()) == {"size", "idle", "pct"}
    for key in ("cpu", "memory", "disk", "db_pool"):
        assert data["status"][key] in ("ok", "warning", "critical")
    expected_workers = {
        "blind_phase", "coordinator", "calibration", "corpus_ingest",
        "corpus_promote", "circuit_breaker", "post_expiry", "system_metrics",
    }
    assert set(data["workers"].keys()) == expected_workers
    for status in data["workers"].values():
        assert status in ("running", "stopped")


async def test_system_health_history(client, db_pool):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Two rows in the last 24h, one 3 days old (7d window only)
    for delta, disk in ((timedelta(hours=1), 30.0), (timedelta(hours=2), 31.0),
                        (timedelta(days=3), 28.0)):
        await db_pool.execute(
            """INSERT INTO system_metrics_hourly
                   (hour, cpu_pct, memory_pct, disk_pct, db_pool_pct)
               VALUES ($1, 10.0, 40.0, $2, 20.0)""",
            now - delta, disk,
        )
    r = await client.get("/internal/admin/system-health", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert len(data["history_24h"]) == 2
    assert len(data["disk_history_7d"]) == 3
    # Ascending order
    hours = [row["hour"] for row in data["disk_history_7d"]]
    assert hours == sorted(hours)
    assert "disk_pct" in data["disk_history_7d"][0]


async def test_system_health_requires_admin(client):
    r = await client.get(
        "/internal/admin/system-health", headers={"Authorization": "Admin wrong-key"}
    )
    assert r.status_code == 403
    r = await client.get(
        "/internal/admin/system-health", headers={"Authorization": "Bearer dev-admin-key"}
    )
    assert r.status_code == 403


# ─── GET /internal/admin/metrics ──────────────────────────────────────────────

async def test_metrics_default_range_is_30d(client):
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert data["range_days"] == 30
    assert len(data["daily_activity"]) == 30


async def test_metrics_range_7d(client):
    r = await client.get("/internal/admin/metrics?range=7d", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert data["range_days"] == 7
    assert len(data["daily_activity"]) == 7


async def test_metrics_unparseable_range_defaults_to_30(client):
    r = await client.get("/internal/admin/metrics?range=banana", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["range_days"] == 30


async def test_metrics_empty_platform_returns_zeros(client):
    """New platform with no data — zeros everywhere, no errors."""
    r = await client.get("/internal/admin/metrics?range=7d", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert all(d["posts"] == 0 and d["answers"] == 0 for d in data["daily_activity"])
    assert len(data["upgrade_window"]) == 24
    assert all(b["avg_posts"] == 0.0 for b in data["upgrade_window"])
    assert data["corpus_pipeline"]["staging_pending"] == 0
    assert data["corpus_pipeline"]["corpus_total"] == 0
    assert data["audit_recent"] == []
    assert data["cb_hourly_24h"] == []


async def test_metrics_daily_activity_counts_today(client, db_pool, seed_agent, test_post):
    r = await client.get("/internal/admin/metrics?range=7d", headers=ADMIN)
    data = r.json()
    today = data["daily_activity"][-1]
    assert today["posts"] >= 1
    assert today["new_agents"] >= 1  # seed_agent created today


async def test_metrics_weekly_agents_12_weeks(client, db_pool, seed_agent):
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    data = r.json()
    assert len(data["weekly_agents"]) == 12
    assert data["weekly_agents"][-1]["new_agents"] >= 1  # created this week


async def test_metrics_upgrade_window_counts_current_hour(client, db_pool, seed_agent, test_post):
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    data = r.json()
    buckets = {b["hour_of_day"]: b for b in data["upgrade_window"]}
    assert set(buckets.keys()) == set(range(24))
    current_hour = datetime.now(timezone.utc).hour
    assert buckets[current_hour]["avg_posts"] > 0


async def test_metrics_corpus_pipeline_counts(client, db_pool, seed_agent):
    now = datetime.now(timezone.utc)
    for status, staged_at in (
        ("pending", now),
        ("promoted", now - timedelta(days=1)),
        ("promoted", now - timedelta(days=10)),  # outside 7d window
        ("rejected", now - timedelta(days=2)),
    ):
        await db_pool.execute(
            """INSERT INTO corpus_staging
                   (question_text, answer_text, quality_score, promote_after,
                    promotion_status, staged_at)
               VALUES ('q', 'a', 0.9, NOW(), $1, $2)""",
            status, staged_at,
        )
    await db_pool.execute(
        """INSERT INTO training_corpus (question_text, answer_text, quality_score)
           VALUES ('q', 'a', 0.9)"""
    )
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    cp = r.json()["corpus_pipeline"]
    assert cp["staging_pending"] == 1
    assert cp["staging_promoted_7d"] == 1
    assert cp["staging_rejected_7d"] == 1
    assert cp["corpus_total"] == 1


async def test_metrics_audit_recent_caps_at_15(client, db_pool):
    for i in range(20):
        await db_pool.execute(
            """INSERT INTO audit_log (action, severity, metadata, created_at)
               VALUES ($1, 'routine', $2, $3)""",
            f"test_action_{i}", {"i": i},
            datetime.now(timezone.utc) - timedelta(minutes=i),
        )
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    audit = r.json()["audit_recent"]
    assert len(audit) == 15
    # Most recent first
    assert audit[0]["action"] == "test_action_0"
    assert audit[0]["metadata"] == {"i": 0}


async def test_metrics_cb_hourly_only_last_24h(client, db_pool):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for delta, flags in ((timedelta(hours=1), 2), (timedelta(hours=3), 5),
                         (timedelta(hours=30), 9)):
        await db_pool.execute(
            """INSERT INTO circuit_stats_hourly (hour, flag_count, ban_count, new_agent_count)
               VALUES ($1, $2, 1, 4)""",
            now - delta, flags,
        )
    r = await client.get("/internal/admin/metrics", headers=ADMIN)
    cb = r.json()["cb_hourly_24h"]
    assert len(cb) == 2
    assert cb[0]["flag_count"] == 5  # oldest of the two recent rows, ASC order
    assert cb[1]["flag_count"] == 2
    assert {"hour", "flag_count", "ban_count", "new_agent_count"} <= set(cb[0].keys())


async def test_metrics_requires_admin(client):
    r = await client.get(
        "/internal/admin/metrics", headers={"Authorization": "Admin wrong-key"}
    )
    assert r.status_code == 403


# ─── GET /v1/admin/stats ──────────────────────────────────────────────────────

async def test_admin_stats_shape(client):
    r = await client.get("/v1/admin/stats", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"agents", "posts", "answers", "moderation"}
    assert data["agents"]["total"] == 0
    assert data["agents"]["by_plan"] == {}
    assert data["posts"]["open"] == 0
    assert data["answers"]["avg_upvotes"] == 0.0
    assert data["moderation"]["queue_unresolved"] == 0
    assert data["moderation"]["bans_this_week"] == 0


async def test_admin_stats_agents_by_plan(client, standard_agent, trial_agent, seed_agent):
    r = await client.get("/v1/admin/stats", headers=ADMIN)
    data = r.json()
    assert data["agents"]["total"] == 3
    assert data["agents"]["by_plan"]["trial"] == 1
    assert data["agents"]["by_plan"]["reader"] == 2  # standard_agent + seed agent both default to reader


async def test_admin_stats_posts_and_answers(client, db_pool, seed_agent, test_post):
    await db_pool.execute(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match, upvote_count)
           VALUES ($1, $2, 'ans', 0.9, 5, 'full', 4)""",
        test_post["id"], seed_agent["id"],
    )
    r = await client.get("/v1/admin/stats", headers=ADMIN)
    data = r.json()
    assert data["posts"]["open"] == 1
    assert data["posts"]["total"] == 1
    assert data["posts"]["by_category"]["coding"] == 1
    assert data["answers"]["total"] == 1
    assert data["answers"]["avg_upvotes"] == pytest.approx(4.0)


async def test_admin_stats_bans_this_week(client, db_pool, standard_agent):
    await db_pool.execute(
        "INSERT INTO bans (agent_id, reason) VALUES ($1, 'test')",
        standard_agent["id"],
    )
    # Old ban outside the window
    await db_pool.execute(
        """INSERT INTO bans (agent_id, reason, created_at)
           VALUES ($1, 'old', NOW() - INTERVAL '10 days')""",
        standard_agent["id"],
    )
    r = await client.get("/v1/admin/stats", headers=ADMIN)
    assert r.json()["moderation"]["bans_this_week"] == 1


async def test_admin_stats_requires_admin(client):
    r = await client.get("/v1/admin/stats", headers={"Authorization": "Admin nope"})
    assert r.status_code == 403


# ─── GET /v1/admin/agents/seeds ───────────────────────────────────────────────

async def test_seeds_only_returns_seed_agents(client, seed_agent, non_seed_agent):
    r = await client.get("/v1/admin/agents/seeds", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == str(seed_agent["id"])


async def test_seeds_fields(client, db_pool, seed_agent):
    await db_pool.execute(
        """UPDATE agents
              SET name = 'Seed One', rank_score = 42, total_answers = 10,
                  total_upvotes_received = 25, calibration_score = 0.91,
                  last_connected_at = NOW()
            WHERE id = $1""",
        seed_agent["id"],
    )
    r = await client.get("/v1/admin/agents/seeds", headers=ADMIN)
    row = r.json()[0]
    assert row["name"] == "Seed One"
    assert row["rank_score"] == 42
    assert row["total_answers"] == 10
    assert row["total_upvotes_received"] == 25
    assert row["calibration_score"] == pytest.approx(0.91)
    assert row["last_connected_at"] is not None
    assert "calibration_sample_size" in row


async def test_seeds_ordered_by_rank_score(client, db_pool, seed_agent, seed_agent2):
    await db_pool.execute(
        "UPDATE agents SET rank_score = 5 WHERE id = $1", seed_agent["id"]
    )
    await db_pool.execute(
        "UPDATE agents SET rank_score = 50 WHERE id = $1", seed_agent2["id"]
    )
    r = await client.get("/v1/admin/agents/seeds", headers=ADMIN)
    data = r.json()
    assert [row["id"] for row in data] == [str(seed_agent2["id"]), str(seed_agent["id"])]


async def test_seeds_requires_admin(client):
    r = await client.get(
        "/v1/admin/agents/seeds", headers={"Authorization": "Admin nope"}
    )
    assert r.status_code == 403
