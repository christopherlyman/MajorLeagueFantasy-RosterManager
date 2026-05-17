import os

import pandas as pd
import streamlit as st

from services.pitcher_queries import fetch_owned_pitcher_rows
from services.queries import get_default_context


APP_DISPLAY_NAME = os.getenv("APP_DISPLAY_NAME", "MLF Roster Manager")
APP_ALIAS = os.getenv("APP_ALIAS", "").strip().lower()

st.set_page_config(page_title=f"{APP_DISPLAY_NAME} - Pitchers", layout="wide")

st.title("Pitchers")


PITCHER_LINEUP_COLUMN_CONFIG = {
    "Slot": st.column_config.TextColumn("Slot"),
    "Pitcher": st.column_config.TextColumn("Pitcher"),
}

if APP_ALIAS != "usual-rmt":
    PITCHER_LINEUP_COLUMN_CONFIG["Eligible Pos."] = st.column_config.TextColumn("Eligible Pos.")

PITCHER_LINEUP_COLUMN_CONFIG.update(
    {
        "Game / Usage": st.column_config.TextColumn("Game / Usage"),
        "Status": st.column_config.TextColumn("Status"),
        "Rank": st.column_config.TextColumn("Rank"),
        "Band": st.column_config.TextColumn("Band"),
        "Rank Reason": st.column_config.TextColumn("Rank Reason"),
    }
)


def _display_status(row: dict) -> str:
    status = str(row.get("status") or "").strip()
    status_full = str(row.get("status_full") or "").strip()
    if status_full:
        return status_full
    if status:
        return status
    return "Active"


def _eligible_display(row: dict) -> str:
    eligible = row.get("eligible_positions") or []
    if isinstance(eligible, str):
        values = [eligible]
    else:
        values = [str(x) for x in eligible if x]

    # In pitcher slots, P is redundant because every SP/RP is P-eligible.
    values = [v for v in values if v in {"SP", "RP"}]
    return ", ".join(values)


def _pitcher_display(row: dict) -> str:
    name = str(row.get("full_name") or "").strip()
    team = str(row.get("mlb_team_abbr") or "").strip()
    if team:
        return f"{name} ({team})"
    return name


def _game_usage_text(row: dict) -> str:
    role = str(row.get("role") or "").upper()

    if role == "SP":
        return "Next start: not loaded"

    if role == "RP":
        return "Today usage: not loaded"

    return "Game context: not loaded"


def build_pitcher_table(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        display_row = {
            "Slot": row.get("selected_position", ""),
            "Pitcher": _pitcher_display(row),
        }

        if APP_ALIAS != "usual-rmt":
            display_row["Eligible Pos."] = _eligible_display(row)

        display_row.update(
            {
                "Game / Usage": _game_usage_text(row),
                "Status": _display_status(row),
                "Rank": row.get("ranking", ""),
                "Band": row.get("band", ""),
                "Rank Reason": row.get("note_short", ""),
            }
        )

        out.append(display_row)
    return out


def _style_pitcher_row(row):
    cols = list(row.index)
    styles = [""] * len(cols)

    band = str(row.get("Band") or "")
    status = str(row.get("Status") or "")
    rank = row.get("Rank")

    try:
        rank_val = int(rank)
    except Exception:
        rank_val = 0

    if rank_val <= 0 or status not in {"", "Active"}:
        row_style = "background-color: #2f3136; color: #c9d1d9;"
    elif band == "Start":
        row_style = "background-color: #17351f; color: #d7f5df;"
    elif band == "Conditional Start":
        row_style = "background-color: #3a3217; color: #f7efc6;"
    else:
        row_style = "background-color: #4a232b; color: #ffd9df;"

    for i, col in enumerate(cols):
        if col != "Slot":
            styles[i] = row_style

    if "Rank" in cols:
        styles[cols.index("Rank")] += " font-weight: 600;"

    return styles


ctx = get_default_context()

st.caption(
    f"League: {ctx['league_key']} | Team: {ctx['team_key']} | Active date: {ctx['as_of_date']}"
)

owned_pitchers = fetch_owned_pitcher_rows(
    ctx["league_key"],
    ctx["team_key"],
    ctx["as_of_date"],
)

tab_lineup, tab_slots, tab_fa = st.tabs(
    ["Starting Lineup", "Slots", "Pitcher Free Agents"]
)

with tab_lineup:
    st.subheader("Pitcher recommendations")
    st.caption(
        "V1 ranks owned pitchers only. SP/RP use separate ranking logic. "
        "Next-start and RP usage context will be added after those data sources are loaded."
    )

    pitcher_rows = build_pitcher_table(owned_pitchers)
    pitcher_df = pd.DataFrame(pitcher_rows)
    pitcher_styler = pitcher_df.style.apply(_style_pitcher_row, axis=1)

    table_height = max(420, 35 * (len(pitcher_rows) + 1) + 3)

    st.dataframe(
        pitcher_styler,
        width="stretch",
        height=table_height,
        hide_index=True,
        column_config=PITCHER_LINEUP_COLUMN_CONFIG,
    )

with tab_slots:
    st.subheader("Pitcher slots")
    st.info("Slot-by-slot pitcher detail will be added after the owned pitcher table is proven.")

with tab_fa:
    st.subheader("Pitcher Free Agents")
    st.info(
        "Pitcher free-agent recommendations will compare available SP spot starts "
        "and RP saves/holds candidates after the owned pitcher ranking model is accepted."
    )

st.divider()

if APP_ALIAS == "usual-rmt":
    st.caption("Pitcher key: SP/RP inferred from roster role | Usual categories: W, SV, K, HLD, ERA, WHIP")
else:
    st.caption("Pitcher key: SP/RP inferred from roster role | MLF/MiLF categories: W, K, TB, ERA, WHIP, QS, SV+H")
