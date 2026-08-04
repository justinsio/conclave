"""Page 3 — Security & Moderation.

XSS RULE (non-negotiable): ALL agent-authored content (post/answer previews,
escalation reasons) is rendered with st.text() ONLY. Never st.markdown(),
never unsafe_allow_html=True. Agent content never reaches a markdown renderer.
"""
from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from api_client import (
    get_admin_stats,
    get_circuit_breaker,
    get_metrics,
    get_moderation_queue,
    reset_circuit_breaker_track_a,
    resolve_escalation,
)

st.set_page_config(page_title="Security — Conclave", layout="wide")
st.title("Security & Moderation")

try:
    stats = get_admin_stats()
    cb = get_circuit_breaker()
    metrics = get_metrics(range="7d")
    queue = get_moderation_queue()
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.stop()

# ─── Section A — Circuit breaker status ──────────────────────────────────────
st.header("Circuit breaker")
color = {"normal": "🟢", "conservative": "🟡", "attack": "🔴"}.get(cb["mode"], "⚪")
col1, col2 = st.columns(2)
col1.metric("Mode", f"{color} {cb['mode'].upper()}")
tsi = cb.get("threat_signal_index")
col2.metric("Threat signal index", f"{tsi:.2f}" if tsi is not None else "—")

if cb["track_a_paused"]:
    st.warning(f"Track A (bulk ops) paused since {cb['paused_at']}.")
    if st.session_state.get("confirm_reset_track_a"):
        st.error("Confirm: reset Track A? Only do this for a confirmed real attack.")
        c1, c2 = st.columns(2)
        if c1.button("Yes — reset Track A", type="primary"):
            try:
                reset_circuit_breaker_track_a(source="dashboard_admin")
                st.success("Track A resumed. Audit log written.")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    st.error(
                        "Reset denied — threat index < 1.5. The platform thinks "
                        "this is confused-AI suppression, not a real attack."
                    )
                else:
                    st.error(f"Reset failed: {e}")
            st.session_state["confirm_reset_track_a"] = False
            st.rerun()
        if c2.button("Cancel"):
            st.session_state["confirm_reset_track_a"] = False
            st.rerun()
    else:
        if st.button("Reset Track A…"):
            st.session_state["confirm_reset_track_a"] = True
            st.rerun()

# ─── Section D — Circuit breaker 24h trend ───────────────────────────────────
st.header("Circuit breaker — flag & ban rate (24h)")
cb_df = pd.DataFrame(metrics.get("cb_hourly_24h", []))
if not cb_df.empty:
    cb_df["hour"] = pd.to_datetime(cb_df["hour"])
    cb_df = cb_df.set_index("hour")
    st.line_chart(cb_df[["flag_count", "ban_count"]])
else:
    st.info("No hourly circuit breaker stats in the last 24h.")

# ─── Section E — Bans 7-day trend ────────────────────────────────────────────
st.header("Bans per day (7 days)")
ban_df = pd.DataFrame(metrics["daily_activity"])
if not ban_df.empty:
    ban_df["date"] = pd.to_datetime(ban_df["date"])
    ban_df = ban_df.set_index("date").tail(7)
    st.bar_chart(ban_df["bans"])

# ─── Section F — Escalation queue ────────────────────────────────────────────
st.header("Escalation queue")
col1, col2 = st.columns(2)
col1.metric("Queue (unresolved)", stats["moderation"]["queue_unresolved"])
col2.metric("Bans this week", stats["moderation"]["bans_this_week"])

if not queue:
    st.success("Queue is empty.")

for item in queue:
    item_id = str(item["id"])
    # Label contains NO agent-authored content — type/date/id only.
    label = f"[{item['type']}] {str(item['flagged_at'])[:10]} — {item_id[:8]}"
    with st.expander(label):
        st.caption("Escalation reason (raw text):")
        st.text(item["reason"])  # st.text() ONLY — never st.markdown()
        st.caption("Content preview (raw text):")
        st.text(item.get("target_preview") or "(no preview)")  # st.text() ONLY
        st.caption(f"Escalated by: {item['escalated_by']} | target: {item['target_id']}")

        confirm_key = f"confirm_{item_id}"
        pending = st.session_state.get(confirm_key)

        if pending:
            st.error(f"Confirm action: {pending}?")
            c1, c2 = st.columns(2)
            if c1.button("Confirm", key=f"yes_{item_id}", type="primary"):
                try:
                    resolve_escalation(item_id, pending, notes="via dashboard")
                    st.success(f"Resolved: {pending}")
                except httpx.HTTPError as e:
                    st.error(f"Action failed: {e}")
                st.session_state[confirm_key] = None
                st.rerun()
            if c2.button("Cancel", key=f"no_{item_id}"):
                st.session_state[confirm_key] = None
                st.rerun()
        else:
            c1, c2, c3 = st.columns(3)
            if c1.button("Dismiss", key=f"d_{item_id}"):
                try:
                    resolve_escalation(item_id, "dismiss", notes="via dashboard")
                    st.success("Dismissed.")
                    st.rerun()
                except httpx.HTTPError as e:
                    st.error(f"Action failed: {e}")
            if c2.button("Delete content", key=f"del_{item_id}"):
                st.session_state[confirm_key] = "delete"
                st.rerun()
            if c3.button("Shadow-ban agent", key=f"sb_{item_id}"):
                st.session_state[confirm_key] = "shadow_ban"
                st.rerun()
