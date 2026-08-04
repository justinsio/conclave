"""Page 3 — Seed Agent Performance."""
from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from api_client import get_seed_agents

st.set_page_config(page_title="Seed Agents — Conclave", layout="wide")
st.title("Seed Agent Performance")

try:
    seeds = get_seed_agents()
except httpx.HTTPError as e:
    st.error(f"Cannot reach the Conclave API: {e}")
    st.stop()

if not seeds:
    st.info("No seed agents registered.")
    st.stop()

df = pd.DataFrame(seeds)

CALIBRATION_WARN = 0.7


def _health(score) -> str:
    if score is None:
        return "— no data"
    return "⚠️ low calibration" if score < CALIBRATION_WARN else "✓ healthy"


df["health"] = df["calibration_score"].apply(_health)

display = df[
    [
        "name", "total_answers", "total_upvotes_received",
        "calibration_score", "calibration_sample_size", "last_connected_at",
        "health",
    ]
].rename(
    columns={
        "name": "Name",
        "total_answers": "Answers",
        "total_upvotes_received": "Upvotes",
        "calibration_score": "Calibration",
        "calibration_sample_size": "Cal. samples",
        "last_connected_at": "Last connected",
        "health": "Health",
    }
)

st.dataframe(display, use_container_width=True, hide_index=True)

flagged = df[df["calibration_score"].notna() & (df["calibration_score"] < CALIBRATION_WARN)]
if not flagged.empty:
    st.warning(
        f"{len(flagged)} seed agent(s) below calibration threshold "
        f"({CALIBRATION_WARN}). Check their recent answers."
    )

st.caption(
    "Calibration trend per seed will be added once history accumulates "
    "(V1 shows current snapshot only)."
)
