"""audit_log must accept a row at any date.

Regression guard. Migration 002 RANGE-partitions audit_log on created_at but
only ever created the 2026-06 and 2026-07 partitions, and nothing creates more
at runtime. From 2026-08-01 every insert raised

    no partition of relation "audit_log" found for row

breaking log_admin_action (bans, restores, key minting, cost-cap overrides) and
the circuit_breaker worker's audit writes. Migration 016 adds a DEFAULT
partition; these tests fail without it.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")


async def _insert_at(pool, when: datetime):
    return await pool.fetchval(
        "INSERT INTO audit_log (action, created_at) VALUES ($1, $2) RETURNING id",
        "test_partition_coverage",
        when,
    )


async def test_accepts_a_row_dated_two_years_out(db_pool):
    """The actual failure: any date past the last hardcoded partition."""
    assert await _insert_at(db_pool, datetime.now(timezone.utc) + timedelta(days=730))


async def test_accepts_a_row_dated_before_the_first_partition(db_pool):
    assert await _insert_at(db_pool, datetime(2020, 1, 1, tzinfo=timezone.utc))


async def test_accepts_a_row_at_the_current_time(db_pool):
    """Guards the boundary this bug was always going to cross unnoticed."""
    assert await _insert_at(db_pool, datetime.now(timezone.utc))


async def test_rows_outside_the_named_partitions_are_cleaned_between_tests(db_pool):
    """conftest previously deleted from audit_log_2026_06/_07 by name, so a row
    in any other partition would survive cleanup and leak into later tests."""
    count = await db_pool.fetchval("SELECT COUNT(*) FROM audit_log")
    assert count == 0
