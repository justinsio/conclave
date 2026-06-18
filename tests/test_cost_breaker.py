"""Tests for the daily cost circuit breaker (Part 3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_spend_table_exists_and_empty(db_pool):
    total = await db_pool.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM moderation_spend_daily WHERE day = CURRENT_DATE"
    )
    assert float(total) == 0.0
