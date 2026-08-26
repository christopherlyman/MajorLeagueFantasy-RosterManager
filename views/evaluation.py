from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from services.evaluation_league_scoring import (
    summarize_league_aware_hitter_evaluation,
)
from services.evaluation_results import (
    fetch_hitter_evaluation_scorecards,
)


LEAGUE_LABELS = {
    "usual-rmt": "Usual Suspects",
    "mlf-rmt": "MLF",
    "milf-rmt": "MiLF",
}


st.title("Hitter Evaluation")
st.caption(
    "Automated hitter-only comparison of Yahoo/YGMA, the RMT same-roster "
    "baseline, and the final Yahoo lineup. Primary results use each league's "
    "live Yahoo scoring categories and competitive format."
)


@st.cache_data(ttl=120, show_spinner=False)
def _load_scorecards() -> list[dict]:
    return fetch_hitter_evaluation_scorecards(limit=5000)


def _friendly_league(alias: str) -> str:
    return LEAGUE_LABELS.get(str(alias), str(alias))


def _record_text(record: dict) -> str:
    return (
        f"{record['record']} "
        f"({record['result']})"
    )


rows = _load_scorecards()

if not rows:
    st.info("No hitter evaluation scorecards found yet.")
    st.stop()

df = pd.DataFrame(rows)

numeric_columns = [
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
]

for col in numeric_columns:
    if col in df.columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

df["_stat_date_obj"] = pd.to_datetime(
    df["stat_date"],
    errors="coerce",
).dt.date

valid_dates = sorted(
    value
    for value in df["_stat_date_obj"].dropna().unique()
    if isinstance(value, date)
)

if not valid_dates:
    st.warning(
        "Evaluation scorecards do not contain usable stat dates."
    )
    st.stop()

aliases = sorted(
    df["instance_alias"]
    .dropna()
    .astype(str)
    .unique()
)

with st.sidebar:
    st.header("Evaluation Filters")

    selected_dates = st.date_input(
        "Date range",
        value=(valid_dates[0], valid_dates[-1]),
        min_value=valid_dates[0],
        max_value=valid_dates[-1],
    )

    selected_aliases = st.multiselect(
        "Leagues",
        aliases,
        default=aliases,
        format_func=_friendly_league,
    )

if isinstance(selected_dates, (tuple, list)):
    if len(selected_dates) >= 2:
        start_date = selected_dates[0]
        end_date = selected_dates[1]
    elif len(selected_dates) == 1:
        start_date = selected_dates[0]
        end_date = selected_dates[0]
    else:
        start_date = valid_dates[0]
        end_date = valid_dates[-1]
else:
    start_date = selected_dates
    end_date = selected_dates

filtered = df[
    (df["_stat_date_obj"] >= start_date)
    & (df["_stat_date_obj"] <= end_date)
].copy()

if selected_aliases:
    filtered = filtered[
        filtered["instance_alias"].isin(
            selected_aliases
        )
    ]
else:
    filtered = filtered.iloc[0:0]

if filtered.empty:
    st.warning(
        "No scorecards match the current date and league filters."
    )
    st.stop()

summary = summarize_league_aware_hitter_evaluation(
    filtered.drop(
        columns=["_stat_date_obj"]
    ).to_dict("records")
)

st.subheader("League-Aware Model Evaluation")

st.caption(
    "RMT vs YGMA is the clean model test. Final vs YGMA measures the "
    "real-world RMT-assisted workflow. Roto is compared cumulatively over "
    "the selected evaluation span; H2H Categories is compared by Yahoo "
    "fantasy week."
)

for error in summary["errors"]:
    st.warning(error)

rmt_h2h = summary["completed_h2h"]["rmt"]
final_h2h = summary["completed_h2h"]["final"]

ygma_vs_opp = summary["completed_vs_opponent"]["ygma"]
rmt_vs_opp = summary["completed_vs_opponent"]["rmt"]
final_vs_opp = summary["completed_vs_opponent"]["final"]

metrics = st.columns(4)

metrics[0].metric(
    "Eligible league-days",
    int(summary["eligible_league_days"]),
)

metrics[1].metric(
    "Complete H2H weeks",
    int(summary["completed_h2h_weeks"]),
)

metrics[2].metric(
    "RMT vs YGMA H2H",
    (
        f"{rmt_h2h['wins']}-"
        f"{rmt_h2h['losses']}-"
        f"{rmt_h2h['ties']}"
    ),
)

metrics[3].metric(
    "Final vs YGMA H2H",
    (
        f"{final_h2h['wins']}-"
        f"{final_h2h['losses']}-"
        f"{final_h2h['ties']}"
    ),
)

st.caption(
    "Completed-week model records above compare RMT and Final directly "
    "against YGMA. The records below compare each hypothetical lineup "
    "against the real Yahoo opponent."
)

opponent_metrics = st.columns(3)

opponent_metrics[0].metric(
    "YGMA vs Opponent",
    (
        f"{ygma_vs_opp['wins']}-"
        f"{ygma_vs_opp['losses']}-"
        f"{ygma_vs_opp['ties']}"
    ),
)

opponent_metrics[1].metric(
    "RMT vs Opponent",
    (
        f"{rmt_vs_opp['wins']}-"
        f"{rmt_vs_opp['losses']}-"
        f"{rmt_vs_opp['ties']}"
    ),
)

opponent_metrics[2].metric(
    "Final vs Opponent",
    (
        f"{final_vs_opp['wins']}-"
        f"{final_vs_opp['losses']}-"
        f"{final_vs_opp['ties']}"
    ),
)

if int(summary["partial_h2h_weeks"]) > 0:
    st.caption(
        f"{int(summary['partial_h2h_weeks'])} H2H week(s) are currently "
        "partial. They are shown below for diagnosis but are excluded from "
        "the completed H2H record."
    )

result_rows = []

for result in summary["league_results"]:
    opponent_name = result.get("opponent_name")

    ygma_vs_opponent = result.get(
        "ygma_vs_opponent"
    )
    rmt_vs_opponent = result.get(
        "rmt_vs_opponent"
    )
    final_vs_opponent = result.get(
        "final_vs_opponent"
    )
    final_integrity = result.get(
        "final_integrity"
    )

    result_rows.append(
        {
            "League": _friendly_league(
                result["instance_alias"]
            ),
            "Format": result["format"],
            "Evaluation Unit": result["unit"],
            "Period": result["period"],
            "Status": result["status"],
            "Days": int(result["eligible_days"]),
            "Categories": result["categories"],
            "RMT vs YGMA": _record_text(
                result["rmt_vs_ygma"]
            ),
            "RMT Category Detail": result[
                "rmt_vs_ygma"
            ]["detail"],
            "Final vs YGMA": _record_text(
                result["final_vs_ygma"]
            ),
            "Final Category Detail": result[
                "final_vs_ygma"
            ]["detail"],
            "Opponent": (
                opponent_name
                if opponent_name
                else "—"
            ),
            "YGMA vs Opponent": (
                _record_text(ygma_vs_opponent)
                if ygma_vs_opponent
                else "—"
            ),
            "RMT vs Opponent": (
                _record_text(rmt_vs_opponent)
                if rmt_vs_opponent
                else "—"
            ),
            "Final vs Opponent": (
                _record_text(final_vs_opponent)
                if final_vs_opponent
                else "—"
            ),
            "Final Integrity": (
                final_integrity["status"]
                if final_integrity
                else "—"
            ),
        }
    )

if result_rows:
    results_df = pd.DataFrame(result_rows)

    st.dataframe(
        results_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Days": st.column_config.NumberColumn(
                "Days",
                format="%d",
            ),
        },
    )
else:
    st.info(
        "No league-aware comparison units are available "
        "for the selected filters."
    )


st.subheader("Daily Raw Results")

st.caption(
    "Daily rows are diagnostic evidence only. They are not used as "
    "standalone winners for H2H leagues."
)

detail = filtered.copy()

detail = detail.sort_values(
    [
        "_stat_date_obj",
        "instance_alias",
        "track_label",
    ],
    ascending=[False, True, True],
)

detail["League"] = detail[
    "instance_alias"
].map(_friendly_league)

detail["H/AB"] = (
    detail["total_hits"]
    .fillna(0)
    .astype(int)
    .astype(str)
    + "/"
    + detail["total_ab"]
    .fillna(0)
    .astype(int)
    .astype(str)
)

detail_display = detail[
    [
        "stat_date",
        "League",
        "track_label",
        "starting_hitter_rows",
        "H/AB",
        "batting_avg",
        "total_r",
        "total_hr",
        "total_rbi",
        "total_sb",
        "total_bb",
        "total_k",
    ]
].rename(
    columns={
        "stat_date": "Date",
        "track_label": "Source",
        "starting_hitter_rows": "Starters",
        "batting_avg": "AVG",
        "total_r": "R",
        "total_hr": "HR",
        "total_rbi": "RBI",
        "total_sb": "SB",
        "total_bb": "BB",
        "total_k": "K",
    }
)

st.dataframe(
    detail_display,
    width="stretch",
    hide_index=True,
    column_config={
        "Starters": st.column_config.NumberColumn(
            "Starters",
            format="%d",
        ),
        "AVG": st.column_config.NumberColumn(
            "AVG",
            format="%.3f",
        ),
        "R": st.column_config.NumberColumn(
            "R",
            format="%d",
        ),
        "HR": st.column_config.NumberColumn(
            "HR",
            format="%d",
        ),
        "RBI": st.column_config.NumberColumn(
            "RBI",
            format="%d",
        ),
        "SB": st.column_config.NumberColumn(
            "SB",
            format="%d",
        ),
        "BB": st.column_config.NumberColumn(
            "BB",
            format="%d",
        ),
        "K": st.column_config.NumberColumn(
            "K",
            format="%d",
        ),
    },
)

with st.expander(
    "Metric definitions",
    expanded=False,
):
    st.markdown(
        """
- **YGMA**: Yahoo/YGMA lineup captured before the RMT refresh.
- **RMT Baseline**: RMT optimized lineup using the same roster available at analysis time.
- **Final Lineup**: actual final Yahoo lineup, reconstructed automatically from Yahoo's historical dated roster after the day is complete.
- **RMT vs YGMA**: the primary model-performance comparison.
- **Final vs YGMA**: the real-world RMT-assisted workflow comparison.
- **Roto leagues**: scoring categories are accumulated across the selected evaluation span.
- **H2H Categories leagues**: scoring categories are accumulated inside Yahoo's authoritative fantasy-week boundaries.
- **Complete H2H week**: every calendar date in Yahoo's returned week range has a complete YGMA, RMT Baseline, and Final Lineup scorecard. Partial weeks remain diagnostic and do not count in any completed-week record.
- **RMT vs Opponent / YGMA vs Opponent / Final vs Opponent**: for complete H2H weeks only, each hypothetical lineup is scored against the actual Yahoo opponent's completed weekly hitter totals.
- **Final Integrity**: verifies that the Evaluation system's reconstructed Final Lineup weekly H, AB, R, HR, RBI, SB, BB, and K totals exactly match Yahoo's official weekly team totals. A failure is surfaced as an Evaluation error.
- **AVG**: recalculated as cumulative hits divided by cumulative at-bats; daily averages are never averaged together.
- **Category direction**: read from Yahoo settings. For the current hitter leagues, strikeouts are lower-is-better and the remaining scoring categories are higher-is-better.
- **H/AB** is display/support data for AVG, not a separate Yahoo scoring category.
- Pitcher outcomes are not included yet.
        """
    )
