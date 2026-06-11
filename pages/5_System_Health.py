"""Page 5 — System Health: current psutil gauges + 24h/7d trends."""
from __future__ import annotations

import os
import time

import httpx
import pandas as pd
import streamlit as st

from api_client import get_system_health

st.set_page_config(page_title="System Health — Conclave", layout="wide")
st.title("System Health")

auto_refresh = st.sidebar.checkbox("Auto-refresh (60s)", value=True)


def _status_color(status: str) -> str:
    return {"ok": "🟢", "warning": "🟡", "critical": "🔴"}.get(status, "⚪")


try:
    h = get_system_health()
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.stop()

# ─── Section A — Current state ────────────────────────────────────────────────
st.header("Current state")
status = h["status"]
col1, col2, col3, col4 = st.columns(4)
col1.metric("CPU", f"{_status_color(status['cpu'])} {h['cpu_pct']:.1f}%")
col2.metric("Memory", f"{_status_color(status['memory'])} {h['memory']['pct']:.1f}%")
col3.metric("Disk", f"{_status_color(status['disk'])} {h['disk']['pct']:.1f}%")
col4.metric("DB pool", f"{_status_color(status['db_pool'])} {h['db_pool']['pct']:.1f}%")

g1, g2, g3 = st.columns(3)
with g1:
    st.progress(min(h["cpu_pct"] / 100, 1.0))
    st.caption(f"CPU {h['cpu_pct']:.1f}%")
with g2:
    st.progress(min(h["memory"]["pct"] / 100, 1.0))
    st.caption(f"Memory {h['memory']['used_gb']:.1f} / {h['memory']['total_gb']:.1f} GB")
with g3:
    st.progress(min(h["disk"]["pct"] / 100, 1.0))
    st.caption(f"Disk {h['disk']['used_gb']:.1f} GB used, {h['disk']['free_gb']:.1f} GB free")

io1, io2, db1 = st.columns(3)
io1.metric("Disk read", f"{h['disk_io']['read_mb_s']} MB/s")
io2.metric("Disk write", f"{h['disk_io']['write_mb_s']} MB/s")
db1.metric("DB pool idle/size", f"{h['db_pool']['idle']} / {h['db_pool']['size']}")

st.subheader("Worker liveness")
workers_df = pd.DataFrame(
    [
        {"worker": name, "status": ("✓ running" if s == "running" else "✗ stopped")}
        for name, s in h["workers"].items()
    ]
)
st.dataframe(workers_df, use_container_width=True, hide_index=True)
if h.get("metrics_last_recorded_at"):
    st.caption(f"Last metrics snapshot: {h['metrics_last_recorded_at']}")

# ─── Section B — 24h resource trends ─────────────────────────────────────────
st.header("Resource trends — last 24h")
hdf = pd.DataFrame(h["history_24h"])
if not hdf.empty:
    hdf["hour"] = pd.to_datetime(hdf["hour"])
    hdf = hdf.set_index("hour")

    st.subheader("CPU & memory — last 24h")
    st.line_chart(hdf[["cpu_pct", "memory_pct"]])

    st.subheader("DB pool usage — last 24h")
    st.line_chart(hdf["db_pool_pct"])
else:
    st.info(
        "No hourly snapshots yet — the system_metrics worker writes one row per "
        "hour. Check back after the API has been running for a while."
    )

# ─── Section C — Disk trend (7 days) ─────────────────────────────────────────
st.header("Disk usage — last 7 days")
ddf = pd.DataFrame(h["disk_history_7d"])
if not ddf.empty:
    ddf["hour"] = pd.to_datetime(ddf["hour"])
    ddf = ddf.set_index("hour")
    st.line_chart(ddf["disk_pct"])
    st.caption("Steady upward slope = normal log growth. Sharp jump = investigate.")
else:
    st.info("No disk history yet.")

# DASHBOARD_DISABLE_REFRESH=1 disables the loop (used by automated page tests).
if auto_refresh and os.getenv("DASHBOARD_DISABLE_REFRESH") != "1":
    time.sleep(60)
    st.rerun()
