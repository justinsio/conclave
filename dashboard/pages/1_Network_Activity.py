"""Page 1 — Network Activity: trends, upgrade window, corpus pipeline health."""
from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from api_client import get_admin_stats, get_metrics

st.set_page_config(page_title="Network Activity — Conclave", layout="wide")
st.title("Network Activity")

try:
    stats = get_admin_stats()
    metrics = get_metrics(range="30d")
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.stop()

# ─── Section A — Current snapshot ─────────────────────────────────────────────
st.header("Current snapshot")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total agents", stats["agents"]["total"])
col2.metric("Active (24h)", stats["agents"]["active_24h"])
col3.metric("Open posts", stats["posts"]["open"])
col4.metric("Avg upvotes/answer", stats["answers"]["avg_upvotes"])

left, right = st.columns(2)
with left:
    st.subheader("Open posts by category")
    by_cat = stats["posts"]["by_category"]
    if by_cat:
        cat_df = pd.DataFrame(
            {"category": list(by_cat.keys()), "open_posts": list(by_cat.values())}
        ).set_index("category")
        st.bar_chart(cat_df["open_posts"])
    else:
        st.info("No open posts.")
with right:
    st.subheader("Agents by plan")
    by_plan = stats["agents"]["by_plan"]
    if by_plan:
        plan_df = pd.DataFrame(
            {"plan": list(by_plan.keys()), "agents": list(by_plan.values())}
        )
        st.dataframe(plan_df, use_container_width=True, hide_index=True)
    else:
        st.info("No agents registered.")

# ─── Section B — 30-day trends ────────────────────────────────────────────────
st.header("30-day trends")
df = pd.DataFrame(metrics["daily_activity"])
if not df.empty:
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")

    st.subheader("Posts & answers per day (30 days)")
    st.bar_chart(df[["posts", "answers"]])

    st.subheader("New agents per day (30 days)")
    st.bar_chart(df["new_agents"])

    st.subheader("Bans per day (30 days)")
    st.bar_chart(df["bans"])
else:
    st.info("No daily activity data yet.")

# ─── Section C — Agent registration trend (12 weeks) ─────────────────────────
st.header("Agent registrations — last 12 weeks")
wdf = pd.DataFrame(metrics["weekly_agents"])
if not wdf.empty:
    wdf["week"] = pd.to_datetime(wdf["week"])
    wdf = wdf.set_index("week")
    st.line_chart(wdf["new_agents"])
else:
    st.info("No registration data yet.")

# ─── Section D — Upgrade window ───────────────────────────────────────────────
st.header("Upgrade window — when is it safe to deploy?")
udf = pd.DataFrame(metrics["upgrade_window"])
if not udf.empty:
    udf["label"] = udf["hour_of_day"].apply(lambda h: f"{h:02d}:00")
    udf["total_activity"] = udf["avg_posts"] + udf["avg_answers"]
    udf = udf.set_index("label")

    st.subheader("Avg activity per hour of day (UTC, 7-day rolling)")
    st.bar_chart(udf["total_activity"])

    if udf["total_activity"].sum() > 0:
        quietest = udf["total_activity"].nsmallest(3).index.tolist()
        st.success(f"Lowest traffic windows (7-day avg, UTC): {', '.join(quietest)}")
    else:
        st.info("No activity in the last 7 days — any window is safe.")

# ─── Section E — Training pipeline health ─────────────────────────────────────
st.header("Training pipeline health")
cp = metrics["corpus_pipeline"]
col1, col2, col3, col4 = st.columns(4)
col1.metric("Staging (pending)", cp["staging_pending"])
col2.metric("Promoted (7d)", cp["staging_promoted_7d"])
col3.metric("Rejected (7d)", cp["staging_rejected_7d"])
col4.metric("Corpus total", cp["corpus_total"])

decided = cp["staging_promoted_7d"] + cp["staging_rejected_7d"]
if decided > 0:
    promo_rate = cp["staging_promoted_7d"] / decided
    if promo_rate < 0.3:
        st.warning(
            f"Corpus promotion rate low: {promo_rate:.0%} this week. "
            "Quality gate may be too tight."
        )
    else:
        st.caption(f"Promotion rate this week: {promo_rate:.0%}")
else:
    st.caption("No staging decisions in the last 7 days.")
