from __future__ import annotations

import pandas as pd
import streamlit as st

from services.evaluation_results import fetch_hitter_evaluation_scorecards


st.title("Evaluation Results")
st.caption(
    "Read-only hitter outcome scorecards comparing Yahoo/YGMA, RMT same-roster baseline, "
    "and the final locked roster after RMT-driven decisions."
)


@st.cache_data(ttl=120, show_spinner=False)
def _load_scorecards() -> list[dict]:
    return fetch_hitter_evaluation_scorecards(limit=1000)


def _display_df(df: pd.DataFrame, columns: list[str], rename: dict[str, str]) -> pd.DataFrame:
    available = [col for col in columns if col in df.columns]
    out = df.loc[:, available].copy()
    return out.rename(columns=rename)


def _column_config() -> dict:
    return {
        "Outcome Rank": st.column_config.NumberColumn("Outcome Rank", format="%d"),
        "Event Score": st.column_config.NumberColumn("Event Score", format="%d"),
        "Starters": st.column_config.NumberColumn("Starters", format="%d"),
        "Actual": st.column_config.NumberColumn("Actual", format="%d"),
        "Missing": st.column_config.NumberColumn("Missing", format="%d"),
        "H": st.column_config.NumberColumn("H", format="%d"),
        "AB": st.column_config.NumberColumn("AB", format="%d"),
        "AVG": st.column_config.NumberColumn("AVG", format="%.3f"),
        "R": st.column_config.NumberColumn("R", format="%d"),
        "HR": st.column_config.NumberColumn("HR", format="%d"),
        "RBI": st.column_config.NumberColumn("RBI", format="%d"),
        "SB": st.column_config.NumberColumn("SB", format="%d"),
        "BB": st.column_config.NumberColumn("BB", format="%d"),
        "K": st.column_config.NumberColumn("K", format="%d"),
    }


rows = _load_scorecards()

if not rows:
    st.info("No hitter evaluation scorecards found yet.")
    st.stop()

df = pd.DataFrame(rows)

for col in [
    "outcome_rank",
    "event_score",
    "starting_hitter_rows",
    "actual_rows",
    "missing_actual_rows",
    "total_hits",
    "total_ab",
    "batting_avg",
    "total_r",
    "total_hr",
    "total_rbi",
    "total_sb",
    "total_bb",
    "total_k",
]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

stat_dates = sorted(df["stat_date"].dropna().astype(str).unique(), reverse=True)
aliases = sorted(df["instance_alias"].dropna().astype(str).unique())

with st.sidebar:
    st.header("Evaluation Filters")
    selected_date = st.selectbox("Stat date", stat_dates, index=0)
    selected_aliases = st.multiselect("Leagues", aliases, default=aliases)
    complete_only = st.checkbox("Final complete scorecards only", value=True)

filtered = df[df["stat_date"].astype(str) == str(selected_date)].copy()
if selected_aliases:
    filtered = filtered[filtered["instance_alias"].isin(selected_aliases)]
if complete_only:
    filtered = filtered[(filtered["is_final"] == True) & (filtered["missing_actual_rows"] == 0)]

if filtered.empty:
    st.warning("No scorecards match the current filters.")
    st.stop()

rename = {
    "eval_date": "Eval Date",
    "stat_date": "Stat Date",
    "instance_alias": "League",
    "track_label": "Track",
    "snapshot_source": "Source",
    "completion": "Status",
    "outcome_rank": "Outcome Rank",
    "event_score": "Event Score",
    "starting_hitter_rows": "Starters",
    "actual_rows": "Actual",
    "missing_actual_rows": "Missing",
    "total_hits": "H",
    "total_ab": "AB",
    "batting_avg": "AVG",
    "total_r": "R",
    "total_hr": "HR",
    "total_rbi": "RBI",
    "total_sb": "SB",
    "total_bb": "BB",
    "total_k": "K",
}

summary_cols = [
    "stat_date",
    "instance_alias",
    "track_label",
    "event_score",
    "total_r",
    "total_hr",
    "total_rbi",
    "total_sb",
    "total_bb",
    "total_hits",
    "total_ab",
    "batting_avg",
    "total_k",
]

detail_cols = [
    "stat_date",
    "instance_alias",
    "track_label",
    "completion",
    "outcome_rank",
    "event_score",
    "starting_hitter_rows",
    "actual_rows",
    "missing_actual_rows",
    "total_r",
    "total_hr",
    "total_rbi",
    "total_sb",
    "total_bb",
    "total_hits",
    "total_ab",
    "batting_avg",
    "total_k",
]

st.subheader("Winners")
winner_df = filtered[filtered["outcome_rank"] == 1].sort_values(["instance_alias", "track_label"])
st.dataframe(
    _display_df(winner_df, summary_cols, rename),
    width="stretch",
    hide_index=True,
    column_config=_column_config(),
)

st.subheader("Full Comparison")
st.dataframe(
    _display_df(
        filtered.sort_values(
            ["instance_alias", "outcome_rank", "track_label"],
            ascending=[True, True, True],
        ),
        detail_cols,
        rename,
    ),
    width="stretch",
    hide_index=True,
    column_config=_column_config(),
)

with st.expander("Metric definitions", expanded=False):
    st.markdown(
        """
- **YGMA**: Yahoo/YGMA lineup captured before RMT refresh.
- **RMT Baseline**: RMT optimized lineup using the same roster.
- **Final Locked**: final Yahoo roster/lineup after your RMT-driven decisions.
- **Event Score**: `R + HR + RBI + SB + BB`.
- **Outcome Rank**: ranked within the same league/date by Event Score, then AVG, then hits, then fewer strikeouts.
- Pitcher outcomes are not included yet.
"""
    )
