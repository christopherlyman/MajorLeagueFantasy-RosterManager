import os

import pandas as pd
from services.db import get_connection
import streamlit as st

from views.shared_refresh import render_refresh_sidebar

from services.pitcher_queries import fetch_available_pitcher_rows, fetch_owned_pitcher_rows
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
        "% Ros": st.column_config.TextColumn("% Ros"),
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



def _format_percent_owned(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.0f}%"
    except Exception:
        return ""


def build_pitcher_table(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        display_row = {
            "Slot": row.get("selected_position", ""),
            "Pitcher": _pitcher_display(row),
            "% Ros": _format_percent_owned(row.get("percent_owned")),
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


def _is_available_pitcher(row: dict) -> bool:
    slot = str(row.get("selected_position") or "").strip().upper()
    status = str(row.get("status") or "").strip().upper()
    return slot not in {"IL", "NA"} and not status.startswith(("IL", "NA"))


def _pitcher_eligible_for_slot(row: dict, slot_type: str) -> bool:
    slot_type = str(slot_type or "").strip().upper()
    eligible = row.get("eligible_positions") or []

    if isinstance(eligible, str):
        values = {eligible.upper()}
    else:
        values = {str(x).upper() for x in eligible if x}

    role = str(row.get("role") or "").strip().upper()

    if slot_type == "P":
        return bool(values.intersection({"P", "SP", "RP"})) or role in {"SP", "RP"}

    if slot_type in {"SP", "RP"}:
        return slot_type in values or role == slot_type

    return False


def _slot_candidate_rows(rows: list[dict], slot_type: str) -> list[dict]:
    candidates = [
        row for row in rows
        if _is_available_pitcher(row) and _pitcher_eligible_for_slot(row, slot_type)
    ]
    candidates.sort(
        key=lambda row: (
            -int(row.get("ranking") or 0),
            str(row.get("full_name") or ""),
        )
    )
    return candidates


def _current_pitcher_for_slot(rows: list[dict], slot_type: str, slot_index: int) -> str:
    matching = [
        row for row in rows
        if str(row.get("selected_position") or "").strip().upper() == slot_type
        and _is_available_pitcher(row)
    ]
    matching.sort(key=lambda row: str(row.get("full_name") or ""))
    if slot_index - 1 >= len(matching):
        return ""
    return _pitcher_display(matching[slot_index - 1])


def _active_slot_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _is_available_pitcher(row):
            continue
        slot = str(row.get("selected_position") or "").strip().upper()
        if slot in {"SP", "RP", "P"}:
            counts[slot] = counts.get(slot, 0) + 1
    return counts


def _pitcher_slot_plan(rows: list[dict]) -> list[tuple[str, str, int]]:
    counts = _active_slot_counts(rows)

    if APP_ALIAS == "usual-rmt":
        p_count = max(1, counts.get("P", 0))
        return [(f"P{i}", "P", i) for i in range(1, p_count + 1)]

    out: list[tuple[str, str, int]] = []
    for slot_type in ("SP", "RP", "P"):
        for i in range(1, counts.get(slot_type, 0) + 1):
            out.append((f"{slot_type}{i}", slot_type, i))
    return out


def build_pitcher_slot_table(rows: list[dict], slot_type: str, current_pitcher: str) -> list[dict]:
    out = []
    for row in _slot_candidate_rows(rows, slot_type):
        pitcher = _pitcher_display(row)
        display_row = {
            "Selected": "✅" if pitcher == current_pitcher else "",
            "Pitcher": pitcher,
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


PITCHER_SLOT_COLUMN_CONFIG = {
    "Selected": st.column_config.TextColumn("Selected"),
    "Pitcher": st.column_config.TextColumn("Pitcher"),
}

if APP_ALIAS != "usual-rmt":
    PITCHER_SLOT_COLUMN_CONFIG["Eligible Pos."] = st.column_config.TextColumn("Eligible Pos.")

PITCHER_SLOT_COLUMN_CONFIG.update(
    {
        "Game / Usage": st.column_config.TextColumn("Game / Usage"),
        "Status": st.column_config.TextColumn("Status"),
        "Rank": st.column_config.TextColumn("Rank"),
        "Band": st.column_config.TextColumn("Band"),
        "Rank Reason": st.column_config.TextColumn("Rank Reason"),
    }
)


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

with st.sidebar:
    render_refresh_sidebar(ctx)

st.caption(
    f"League: {ctx['league_key']} | Team: {ctx['team_key']} | Active date: {ctx['as_of_date']}"
)

owned_pitchers = fetch_owned_pitcher_rows(
    ctx["league_key"],
    ctx["team_key"],
    ctx["as_of_date"],
)

available_pitchers = fetch_available_pitcher_rows(
    ctx["league_key"],
    ctx["team_key"],
    ctx["as_of_date"],
)


ROSTER_POLICY_STATUSES = ["KEEPER", "DROPPABLE_HIGH", "DROPPABLE_LOW"]



def _eligible_policy_tokens(value) -> set[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw = str(value or "")
        for ch in "[]'\"":
            raw = raw.replace(ch, "")
        raw_values = raw.split(",")

    return {str(v).strip().upper() for v in raw_values if str(v).strip()}

def _policy_cue(policy_status: str) -> str:
    policy = str(policy_status or "").strip().upper()
    if policy == "KEEPER":
        return "🔵 Keeper"
    if policy == "DROPPABLE_HIGH":
        return "🟠 Droppable High"
    return "🟢 Droppable Low"


def _policy_editor_height(row_count: int) -> int:
    return max(220, min(1400, 42 * (int(row_count or 0) + 2)))


def fetch_pitcher_policy_rows(ctx: dict) -> list[dict]:
    sql = """
    SELECT
        r.selected_position,
        r.full_name,
        r.yahoo_player_key,
        r.eligible_positions,
        COALESCE(r.status, '') AS player_status,
        COALESCE(p.policy_status, 'DROPPABLE_LOW') AS policy_status,
        COALESCE(p.notes, '') AS notes
    FROM lineup_tool.roster_snapshot r
    LEFT JOIN rmt.roster_player_policy p
      ON p.league_key = r.league_key
     AND p.team_key = r.team_key
     AND p.yahoo_player_key = r.yahoo_player_key
    WHERE r.league_key = %s
      AND r.team_key = %s
      AND r.as_of_date = %s
      AND r.yahoo_player_key IS NOT NULL
      AND (
            upper(r.selected_position) = 'P'
         OR upper(r.eligible_positions::text) LIKE '%%P%%'
      )
    ORDER BY
        CASE upper(r.selected_position)
            WHEN 'P' THEN 1
            WHEN 'BN' THEN 2
            WHEN 'IL' THEN 3
            WHEN 'NA' THEN 4
            ELSE 99
        END,
        r.full_name
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ctx["league_key"], ctx["team_key"], ctx["as_of_date"]))
            rows = cur.fetchall()

    out = []
    for row in rows:
        tokens = _eligible_policy_tokens(row[3])
        eligible = ", ".join(sorted(tokens)) if tokens else str(row[3] or "")
        out.append(
            {
                "Slot": row[0],
                "Player": row[1],
                "Yahoo Key": row[2],
                "Eligible": eligible,
                "Status": row[4],
                "Policy": row[5],
                "Policy Cue": _policy_cue(row[5]),
                "Notes": row[6],
            }
        )

    return out


def save_pitcher_policy_rows(ctx: dict, edited_rows) -> int:
    changed = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in edited_rows:
                yahoo_key = str(row.get("Yahoo Key") or "").strip()
                if not yahoo_key:
                    continue

                policy = str(row.get("Policy") or "DROPPABLE_LOW").strip()
                if policy not in ROSTER_POLICY_STATUSES:
                    policy = "DROPPABLE_LOW"

                notes = str(row.get("Notes") or "").strip()

                cur.execute(
                    """
                    INSERT INTO rmt.roster_player_policy (
                        league_key,
                        team_key,
                        yahoo_player_key,
                        policy_status,
                        notes,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (league_key, team_key, yahoo_player_key)
                    DO UPDATE SET
                        policy_status = EXCLUDED.policy_status,
                        notes = EXCLUDED.notes,
                        updated_at = now()
                    """,
                    (
                        ctx["league_key"],
                        ctx["team_key"],
                        yahoo_key,
                        policy,
                        notes,
                    ),
                )
                changed += 1

        conn.commit()

    return changed


def render_pitcher_policy_tab(ctx: dict) -> None:
    st.subheader("Roster Policy")
    st.caption(
        "Manual safety layer for future add/drop automation. "
        "KEEPER = never consider dropping. "
        "DROPPABLE_HIGH = only consider with strong evidence. "
        "DROPPABLE_LOW = actively evaluate against free agents."
    )
    st.markdown(
        "**Color guide:** 🔵 Keeper &nbsp;&nbsp; 🟠 Droppable High &nbsp;&nbsp; 🟢 Droppable Low"
    )

    policy_rows = fetch_pitcher_policy_rows(ctx)

    if not policy_rows:
        st.info("No pitcher policy rows found for the current roster/date.")
        return

    policy_df = pd.DataFrame(policy_rows)

    with st.form(key=f"pitcher_policy_form_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}"):
        submit = st.form_submit_button("Save Pitcher Policy")

        edited_policy_df = st.data_editor(
            policy_df,
            hide_index=True,
            disabled=["Slot", "Player", "Yahoo Key", "Eligible", "Status", "Policy Cue"],
            column_order=["Slot", "Player", "Eligible", "Status", "Policy Cue", "Policy", "Notes"],
            column_config={
                "Yahoo Key": None,
                "Policy": st.column_config.SelectboxColumn(
                    "Policy",
                    options=ROSTER_POLICY_STATUSES,
                    required=True,
                ),
                "Policy Cue": st.column_config.TextColumn("Policy Cue"),
                "Notes": st.column_config.TextColumn("Notes"),
            },
            key=f"pitcher_policy_editor_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}_{len(policy_rows)}",
            use_container_width=True,
            height=_policy_editor_height(len(policy_rows)),
        )

        if submit:
            saved = save_pitcher_policy_rows(ctx, edited_policy_df.to_dict("records"))
            st.success(f"Saved pitcher policy for {saved} players.")
            st.rerun()

tab_lineup, tab_slots, tab_fa, tab_policy = st.tabs(
    ["Starting Lineup", "Slots", "Pitcher Free Agents", "Roster Policy"]
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
        width="content",
        height=table_height,
        hide_index=True,
        column_config=PITCHER_LINEUP_COLUMN_CONFIG,
    )

with tab_slots:
    st.subheader("Pitcher slot controls")
    st.caption(
        "Experimental slot controls. Leave on AUTO to keep the current Yahoo slot assignment."
    )

    slot_plan = _pitcher_slot_plan(owned_pitchers)

    if not slot_plan:
        st.info("No active pitcher slots found.")
    else:
        for slot_label, slot_type, slot_index in slot_plan:
            current_pitcher = _current_pitcher_for_slot(owned_pitchers, slot_type, slot_index)
            candidate_rows = _slot_candidate_rows(owned_pitchers, slot_type)
            candidate_names = [_pitcher_display(row) for row in candidate_rows]

            options = ["AUTO"] + candidate_names
            key = f"pitcher_override_{slot_label}"

            current_choice = st.session_state.get(key, "AUTO")
            if current_choice not in options:
                current_choice = "AUTO"
                st.session_state[key] = "AUTO"

            st.subheader(slot_label)
            if current_pitcher:
                st.caption(f"Current Yahoo slot: {current_pitcher}")

            choice = st.selectbox(
                f"{slot_label} override",
                options=options,
                index=options.index(current_choice),
                key=key,
            )

            selected_pitcher = current_pitcher if choice == "AUTO" else choice

            if selected_pitcher:
                st.caption(f"Experimental selection: {selected_pitcher}")

            slot_rows = build_pitcher_slot_table(owned_pitchers, slot_type, selected_pitcher)

            if not slot_rows:
                st.info(f"No eligible owned pitchers found for {slot_label}.")
                continue

            slot_df = pd.DataFrame(slot_rows)
            slot_styler = slot_df.style.apply(_style_pitcher_row, axis=1)
            slot_height = max(260, 35 * (len(slot_rows) + 1) + 3)

            st.dataframe(
                slot_styler,
                width="content",
                height=slot_height,
                hide_index=True,
                column_config=PITCHER_SLOT_COLUMN_CONFIG,
            )

with tab_fa:
    st.subheader("Pitcher Free Agents")
    st.caption(
        "Ranks active Yahoo-available pitcher candidates from the Daily Refresh candidate pool."
    )

    fa_rows = build_pitcher_table(available_pitchers)
    if not fa_rows:
        st.info("No pitcher free-agent candidate file found yet. Run Daily Refresh.")
    else:
        fa_df = pd.DataFrame(fa_rows)
        fa_styler = fa_df.style.apply(_style_pitcher_row, axis=1)
        fa_height = max(420, 35 * (len(fa_rows) + 1) + 3)

        st.dataframe(
            fa_styler,
            width="content",
            height=fa_height,
            hide_index=True,
            column_config=PITCHER_LINEUP_COLUMN_CONFIG,
        )

st.divider()

if APP_ALIAS == "usual-rmt":
    st.caption("Pitcher key: SP/RP inferred from roster role | Usual categories: W, SV, K, HLD, ERA, WHIP")
else:
    st.caption("Pitcher key: SP/RP inferred from roster role | MLF/MiLF categories: W, K, TB, ERA, WHIP, QS, SV+H")

with tab_policy:
    render_pitcher_policy_tab(ctx)

