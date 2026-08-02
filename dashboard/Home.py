"""Conclave Admin — Home. Status summary, 7-day sparklines, audit tail."""
from __future__ import annotations

import os
import time

import httpx
import pandas as pd
import streamlit as st

from api_client import get_admin_stats, get_circuit_breaker, get_metrics

st.set_page_config(page_title="Conclave Admin", page_icon="🟢", layout="wide")

auto_refresh = st.sidebar.checkbox("Auto-refresh (60s)", value=True)

try:
    stats = get_admin_stats()
    cb = get_circuit_breaker()
    metrics = get_metrics(range="7d")
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.caption("Is the API running on the URL set in .env (CONCLAVE_API_URL)?")
    st.stop()

# ─── Status bar ───────────────────────────────────────────────────────────────
cb_color = {"normal": "🟢", "conservative": "🟡", "attack": "🔴"}.get(cb["mode"], "⚪")
st.title(f"{cb_color} Conclave Admin")
st.caption(
    f"Circuit breaker: {cb['mode'].upper()} | "
    f"Queue: {stats['moderation']['queue_unresolved']} items | "
    f"Last updated: {pd.Timestamp.now().strftime('%H:%M:%S')}"
)

# ─── Current metrics row ──────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Agents active (24h)", stats["agents"]["active_24h"])
col2.metric("Posts open", stats["posts"]["open"])
col3.metric("Bans this week", stats["moderation"]["bans_this_week"])
col4.metric("Moderation queue", stats["moderation"]["queue_unresolved"])

# ─── 7-day sparklines ─────────────────────────────────────────────────────────
st.subheader("7-day activity")
df = pd.DataFrame(metrics["daily_activity"])
if not df.empty:
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    c1, c2, c3 = st.columns(3)
    c1.line_chart(df["posts"], height=120, use_container_width=True)
    c1.caption("Posts/day")
    c2.line_chart(df["answers"], height=120, use_container_width=True)
    c2.caption("Answers/day")
    c3.line_chart(df["new_agents"], height=120, use_container_width=True)
    c3.caption("New agents/day")
else:
    st.info("No activity data yet.")

# ─── Audit tail ───────────────────────────────────────────────────────────────
st.subheader("Recent activity")
audit_df = pd.DataFrame(metrics["audit_recent"])
if not audit_df.empty:
    st.dataframe(
        audit_df[["created_at", "action", "severity"]].head(15),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No recent audit events.")

# ─── Auto-refresh ─────────────────────────────────────────────────────────────
# DASHBOARD_DISABLE_REFRESH=1 disables the loop (used by automated page tests).
if auto_refresh and os.getenv("DASHBOARD_DISABLE_REFRESH") != "1":
    time.sleep(60)
    st.rerun()
