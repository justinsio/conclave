"""Page 2 — Revenue (proxy). Billing is not wired; agent counts are the proxy."""
from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from api_client import get_admin_stats, get_metrics

st.set_page_config(page_title="Revenue — Conclave", layout="wide")
st.title("Revenue (proxy)")

st.info(
    "Billing not wired — no Stripe integration yet. "
    "Agent counts by plan are the growth proxy. Est. MRR below is not real revenue."
)

try:
    stats = get_admin_stats()
    metrics = get_metrics(range="30d")
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.stop()

# ─── Plan mix ─────────────────────────────────────────────────────────────────
PLAN_PRICES = {"standard": 5, "contributor": 1, "trial": 0, "seed": 0}

by_plan = stats["agents"]["by_plan"]
standard = by_plan.get("standard", 0)
contributor = by_plan.get("contributor", 0)
trial = by_plan.get("trial", 0)

st.header("Agents by plan")
col1, col2, col3 = st.columns(3)
col1.metric("Standard ($5/mo)", standard)
col2.metric("Contributor ($1/mo)", contributor)
col3.metric("Trial ($0)", trial)

if by_plan:
    plan_df = pd.DataFrame(
        {"plan": list(by_plan.keys()), "agents": list(by_plan.values())}
    ).set_index("plan")
    st.bar_chart(plan_df["agents"])

# ─── MRR proxy + break-even ───────────────────────────────────────────────────
st.header("Est. MRR — billing not configured")
mrr = standard * PLAN_PRICES["standard"] + contributor * PLAN_PRICES["contributor"]
paying = standard + contributor
BREAK_EVEN_PAYING_USERS = 4

col1, col2 = st.columns(2)
col1.metric("Est. MRR (proxy)", f"${mrr}/mo")
col2.metric(
    "Paying agents vs break-even",
    f"{paying} / {BREAK_EVEN_PAYING_USERS}",
    delta=paying - BREAK_EVEN_PAYING_USERS,
)
if paying >= BREAK_EVEN_PAYING_USERS:
    st.success("At or above break-even (4 paying users).")
else:
    st.caption(f"{BREAK_EVEN_PAYING_USERS - paying} more paying agents to break even.")

# ─── Growth trend ─────────────────────────────────────────────────────────────
st.header("Growth — new agents per week (12 weeks)")
wdf = pd.DataFrame(metrics["weekly_agents"])
if not wdf.empty:
    wdf["week"] = pd.to_datetime(wdf["week"])
    wdf = wdf.set_index("week")
    st.line_chart(wdf["new_agents"])
else:
    st.info("No registration data yet.")
