from functools import lru_cache
from datetime import datetime as _dt_datetime
from zoneinfo import ZoneInfo as _ZoneInfo
import json
import urllib.parse
import urllib.request
from math import inf
from pathlib import Path
from datetime import date, datetime, timedelta

import streamlit as st
from datetime import date, timedelta
import pandas as pd
import os
import re

from views.shared_refresh import render_refresh_sidebar

from services.db import get_connection
from services.batter_multiday import build_batter_multiday_projection
from services.rotowire_lineups import lineup_status_with_rotowire
from services.scoring import ranking_band
from services.queries import (
    fetch_available_batter_rows,
    fetch_batter_roster_rows,
    fetch_remaining_starts_by_slot,
    fetch_hitter_slot_order,
    get_default_context,
    resolve_as_of_date,
)

APP_DISPLAY_NAME = os.getenv("APP_DISPLAY_NAME", "MLF Roster Manager")

st.set_page_config(page_title=APP_DISPLAY_NAME, layout="wide")


def _read_env_file(path: str = "/app/.env") -> dict[str, str]:
    vals: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return vals
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def get_runtime_context() -> dict[str, str]:
    file_vals = _read_env_file("/app/.env")
    return {
        "league_key": os.getenv("DEFAULT_LEAGUE_KEY") or file_vals.get("DEFAULT_LEAGUE_KEY", ""),
        "team_key": os.getenv("DEFAULT_TEAM_KEY") or file_vals.get("DEFAULT_TEAM_KEY", ""),
        "as_of_date": resolve_as_of_date(
            os.getenv("DEFAULT_AS_OF_DATE") or file_vals.get("DEFAULT_AS_OF_DATE", ""),
            os.getenv("DEFAULT_DATE_OFFSET_DAYS") or file_vals.get("DEFAULT_DATE_OFFSET_DAYS", "0"),
        ),
    }




BATTER_LINEUP_COLUMN_CONFIG = {
    "Slot": st.column_config.TextColumn("Slot"),
    "Threshold": st.column_config.TextColumn("Threshold"),
    "Player": st.column_config.TextColumn("Player"),
    "Eligible Pos.": st.column_config.TextColumn("Eligible Pos."),
    "% Ros": st.column_config.TextColumn("% Ros"),
    "Rank": st.column_config.TextColumn("Rank"),
    "Band": st.column_config.TextColumn("Band"),
    "Game": st.column_config.TextColumn("Game"),
    "Lineup": st.column_config.TextColumn("Lineup"),
    "Status": st.column_config.TextColumn("Status"),
    "B": st.column_config.NumberColumn("B", format="%.1f"),
    "P": st.column_config.NumberColumn("P", format="%.1f"),
    "Hand": st.column_config.NumberColumn("Hand", format="%.1f"),
    "H/A": st.column_config.NumberColumn("H/A", format="%.1f"),
    "D/N": st.column_config.NumberColumn("D/N", format="%.1f"),
    "Recent": st.column_config.NumberColumn("Recent", format="%.1f"),
    "Start": st.column_config.NumberColumn("Start", format="%.1f"),
    "H2H": st.column_config.NumberColumn("H2H", format="%.1f"),
    "LineupMod": st.column_config.NumberColumn("LineupMod", format="%.1f"),
    "StatusMod": st.column_config.NumberColumn("StatusMod", format="%.1f"),
}

BATTER_SLOT_COLUMN_CONFIG = {
    "Player": st.column_config.TextColumn("Player"),
    "Eligible Pos.": st.column_config.TextColumn("Eligible Pos."),
    "Eligible": st.column_config.TextColumn("Eligible"),
    "% Ros": st.column_config.TextColumn("% Ros"),
    "Rank": st.column_config.TextColumn("Rank"),
    "Band": st.column_config.TextColumn("Band"),
    "Game": st.column_config.TextColumn("Game"),
    "Lineup": st.column_config.TextColumn("Lineup"),
    "Status": st.column_config.TextColumn("Status"),
    "B": st.column_config.NumberColumn("B", format="%.1f"),
    "P": st.column_config.NumberColumn("P", format="%.1f"),
    "Hand": st.column_config.NumberColumn("Hand", format="%.1f"),
    "H/A": st.column_config.NumberColumn("H/A", format="%.1f"),
    "D/N": st.column_config.NumberColumn("D/N", format="%.1f"),
    "Recent": st.column_config.NumberColumn("Recent", format="%.1f"),
    "Start": st.column_config.NumberColumn("Start", format="%.1f"),
    "H2H": st.column_config.NumberColumn("H2H", format="%.1f"),
    "LineupMod": st.column_config.NumberColumn("LineupMod", format="%.1f"),
    "StatusMod": st.column_config.NumberColumn("StatusMod", format="%.1f"),
}

BATTER_FA_COLUMN_CONFIG = {
    "Player": st.column_config.TextColumn("Player"),
    "Eligible": st.column_config.TextColumn("Eligible"),
    "% Ros": st.column_config.TextColumn("% Ros"),
    "Rank": st.column_config.TextColumn("Rank"),
    "Game": st.column_config.TextColumn("Game"),
    "Lineup": st.column_config.TextColumn("Lineup"),
    "Status": st.column_config.TextColumn("Status"),
    "B": st.column_config.NumberColumn("B", format="%.1f"),
    "P": st.column_config.NumberColumn("P", format="%.1f"),
    "Hand": st.column_config.NumberColumn("Hand", format="%.1f"),
    "H/A": st.column_config.NumberColumn("H/A", format="%.1f"),
    "D/N": st.column_config.NumberColumn("D/N", format="%.1f"),
    "Recent": st.column_config.NumberColumn("Recent", format="%.1f"),
    "Start": st.column_config.NumberColumn("Start", format="%.1f"),
    "H2H": st.column_config.NumberColumn("H2H", format="%.1f"),
    "LineupMod": st.column_config.NumberColumn("LineupMod", format="%.1f"),
    "StatusMod": st.column_config.NumberColumn("StatusMod", format="%.1f"),
}


SLOT_PRESSURE_FAMILY_ORDER = ["C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"]

DEFAULT_SLOT_REMAINING_STARTS = {
    "C": 131,
    "1B": 130,
    "2B": 130,
    "3B": 131,
    "SS": 130,
    "IF": 133,
    "OF": 389,
    "UTIL": 131,
}

SLOT_PRESSURE_LIMITS = {
    "C": 1,
    "1B": 1,
    "2B": 1,
    "3B": 1,
    "SS": 1,
    "IF": 1,
    "OF": 3,
    "UTIL": 1,
}

TEAM_ID_ALIASES = {
    "AZ": ["ARI"],
    "ATH": ["ATH", "OAK"],
    "CWS": ["CWS", "CHW"],
    "KC": ["KC", "KCR"],
    "SD": ["SD", "SDP"],
    "SF": ["SF", "SFG"],
    "TB": ["TB", "TBR"],
    "WSH": ["WSH", "WSN"],
}

_CURRENT_SLOT_FLOORS = {k: 50.0 for k in SLOT_PRESSURE_FAMILY_ORDER}
_CURRENT_SLOT_FLOOR_META = {}


def get_remaining_starts(league_key: str, team_key: str, as_of_date: str) -> dict[str, int]:
    auto = {}
    try:
        auto = fetch_remaining_starts_by_slot(league_key, team_key, as_of_date) or {}
    except Exception:
        auto = {}

    out = dict(DEFAULT_SLOT_REMAINING_STARTS)
    for family, value in auto.items():
        family_key = str(family).upper()
        if family_key in out:
            try:
                out[family_key] = max(0, int(value))
            except Exception:
                pass

    if st.session_state.get("use_manual_slot_override", False):
        for family in SLOT_PRESSURE_FAMILY_ORDER:
            key = f"remaining_starts_{family}"
            val = st.session_state.get(key, out[family])
            try:
                out[family] = max(0, int(val))
            except Exception:
                pass

    return out


def format_remaining_starts_caption(remaining_starts: dict[str, int]) -> str:
    return "Remaining starts — " + " | ".join(
        f"{family} {int(remaining_starts.get(family, DEFAULT_SLOT_REMAINING_STARTS[family]))}"
        for family in SLOT_PRESSURE_FAMILY_ORDER
    )
def get_mlb_team_id_map():
    req = urllib.request.Request(
        "https://statsapi.mlb.com/api/v1/teams?sportId=1",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    out = {}
    for t in data.get("teams", []):
        for key in {t.get("abbreviation"), t.get("teamCode"), t.get("fileCode")}:
            if key:
                out[str(key).upper()] = int(t["id"])
    return out


def _team_id_for_abbr(abbr: str, team_id_map: dict[str, int]):
    abbr = str(abbr or "").upper()
    if abbr in team_id_map:
        return team_id_map[abbr]
    for alt in TEAM_ID_ALIASES.get(abbr, []):
        if alt in team_id_map:
            return team_id_map[alt]
    return None


@st.cache_data(ttl=21600)
def get_team_schedule_dates(team_abbr: str, start_iso: str, end_iso: str):
    team_id_map = get_mlb_team_id_map()
    team_id = _team_id_for_abbr(team_abbr, team_id_map)
    if team_id is None:
        return []

    params = urllib.parse.urlencode(
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": start_iso,
            "endDate": end_iso,
        }
    )
    url = f"https://statsapi.mlb.com/api/v1/schedule?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    out = []
    for d in data.get("dates", []):
        if d.get("date"):
            out.append(d["date"])
    return out


def _daterange(start_iso: str, end_iso: str):
    cur = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _schedule_floor_from_pressure(pressure):
    if pressure is None:
        return 50.0
    premium = round((1.0 - pressure) * 50.0)
    premium = max(0, min(10, premium))
    return 50.0 + premium


def compute_schedule_pressure_meta(roster_rows: list[dict], as_of_date: str, remaining_starts: dict[str, int]):
    try:
        season_end = f"{str(as_of_date)[:4]}-09-27"
        future_start = (date.fromisoformat(as_of_date) + timedelta(days=1)).isoformat()
    except Exception:
        return {
            "floors": {k: 50.0 for k in SLOT_PRESSURE_FAMILY_ORDER},
            "pressure": {},
            "future_opportunities": {},
            "skip_budget": {},
            "future_start": None,
            "season_end": None,
            "roster_teams": [],
            "missing_team_maps": [],
        }

    active_rows = [r for r in roster_rows if not is_unavailable(r)]

    roster_team_abbrs = sorted(
        {
            str(r.get("mlb_team_abbr") or "").upper()
            for r in active_rows
            if str(r.get("mlb_team_abbr") or "").strip()
        }
    )

    team_future_dates = {}
    missing_teams = []

    for abbr in roster_team_abbrs:
        try:
            team_future_dates[abbr] = set(get_team_schedule_dates(abbr, future_start, season_end))
        except Exception:
            missing_teams.append(abbr)

    future_opportunities = {k: 0 for k in SLOT_PRESSURE_FAMILY_ORDER}

    for day in _daterange(future_start, season_end):
        counts = {k: 0 for k in SLOT_PRESSURE_FAMILY_ORDER}

        for r in active_rows:
            abbr = str(r.get("mlb_team_abbr") or "").upper()
            if day not in team_future_dates.get(abbr, set()):
                continue

            elig = eligible_set(r)

            if "C" in elig:
                counts["C"] += 1
            if "1B" in elig:
                counts["1B"] += 1
            if "2B" in elig:
                counts["2B"] += 1
            if "3B" in elig:
                counts["3B"] += 1
            if "SS" in elig:
                counts["SS"] += 1
            if "IF" in elig:
                counts["IF"] += 1
            if "OF" in elig:
                counts["OF"] += 1

            counts["UTIL"] += 1

        for family in SLOT_PRESSURE_FAMILY_ORDER:
            future_opportunities[family] += min(SLOT_PRESSURE_LIMITS[family], counts[family])

    pressure = {}
    floors = {}
    skip_budget = {}

    for family in SLOT_PRESSURE_FAMILY_ORDER:
        starts = int(remaining_starts.get(family, 0))
        opps = int(future_opportunities.get(family, 0))

        if opps <= 0:
            p = None
            floor = 50.0
            skip = None
        else:
            p = starts / opps
            floor = _schedule_floor_from_pressure(p)
            skip = opps - starts

        pressure[family] = p
        floors[family] = floor
        skip_budget[family] = skip

    return {
        "floors": floors,
        "pressure": pressure,
        "future_opportunities": future_opportunities,
        "skip_budget": skip_budget,
        "future_start": future_start,
        "season_end": season_end,
        "roster_teams": roster_team_abbrs,
        "missing_team_maps": missing_teams,
    }


def format_slot_floors_caption(meta: dict) -> str:
    floors = meta.get("floors") or {}
    return "Slot floors — " + " | ".join(
        f"{family} {int(round(float(floors.get(family, 50.0))))}"
        for family in SLOT_PRESSURE_FAMILY_ORDER
    )


def format_slot_skip_budget_caption(meta: dict) -> str:
    skip_budget = meta.get("skip_budget") or {}
    parts = []
    for family in SLOT_PRESSURE_FAMILY_ORDER:
        val = skip_budget.get(family)
        parts.append(f"{family} {'n/a' if val is None else int(val)}")
    return "Skip budget — " + " | ".join(parts)


DEFAULT_HITTER_SLOT_ORDER = [
    ("C", "C"),
    ("1B", "1B"),
    ("2B", "2B"),
    ("3B", "3B"),
    ("SS", "SS"),
    ("IF", "IF"),
    ("OF1", "OF"),
    ("OF2", "OF"),
    ("OF3", "OF"),
    ("UTIL", "UTIL"),
]

SLOT_ORDER = list(DEFAULT_HITTER_SLOT_ORDER)

UNAVAILABLE_PREFIXES = ("IL", "NA")
SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV", "V"}


BASE_START_THRESHOLD = 50.0
SLOT_MIN_RANKING_OVERRIDES: dict[str, float] = {}

TEAM_NAME_TO_ABBR = {
    "ARIZONA DIAMONDBACKS": "AZ",
    "ATHLETICS": "ATH",
    "ATLANTA BRAVES": "ATL",
    "BALTIMORE ORIOLES": "BAL",
    "BOSTON RED SOX": "BOS",
    "CHICAGO CUBS": "CHC",
    "CHICAGO WHITE SOX": "CWS",
    "CINCINNATI REDS": "CIN",
    "CLEVELAND GUARDIANS": "CLE",
    "COLORADO ROCKIES": "COL",
    "DETROIT TIGERS": "DET",
    "HOUSTON ASTROS": "HOU",
    "KANSAS CITY ROYALS": "KC",
    "LOS ANGELES ANGELS": "LAA",
    "LOS ANGELES DODGERS": "LAD",
    "MIAMI MARLINS": "MIA",
    "MILWAUKEE BREWERS": "MIL",
    "MINNESOTA TWINS": "MIN",
    "NEW YORK METS": "NYM",
    "NEW YORK YANKEES": "NYY",
    "PHILADELPHIA PHILLIES": "PHI",
    "PITTSBURGH PIRATES": "PIT",
    "SAN DIEGO PADRES": "SD",
    "SAN FRANCISCO GIANTS": "SF",
    "SEATTLE MARINERS": "SEA",
    "ST. LOUIS CARDINALS": "STL",
    "TAMPA BAY RAYS": "TB",
    "TEXAS RANGERS": "TEX",
    "TORONTO BLUE JAYS": "TOR",
    "WASHINGTON NATIONALS": "WSH",
}


def is_unavailable(row: dict) -> bool:
    status = str(row.get("status_display") or "").strip().upper()
    return any(status.startswith(prefix) for prefix in UNAVAILABLE_PREFIXES)


def eligible_set(row: dict) -> set[str]:
    raw = str(row.get("eligible_display") or "").strip()
    out = set()
    if raw:
        for part in raw.split(","):
            part = part.strip().upper()
            if part:
                out.add(part)

    out.add("UTIL")

    if out.intersection({"1B", "2B", "3B", "SS", "IF"}):
        out.add("IF")

    return out


def eligible_for_slot(row: dict, slot_type: str) -> bool:
    if is_unavailable(row):
        return False

    elig = eligible_set(row)

    if slot_type == "UTIL":
        return True
    if slot_type == "OF":
        return "OF" in elig
    if slot_type == "IF":
        return "IF" in elig
    return slot_type in elig


def has_game_today(row: dict) -> bool:
    return str(row.get("game_status") or "").strip().upper() == "GAME_FOUND"


def _boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "t", "1", "yes", "y"}


def _parse_game_time_today_et(as_of_date: str, game_time_et: str):
    raw = str(game_time_et or "").strip().replace(" ET", "").strip()
    if not raw:
        return None

    try:
        parsed_time = _dt_datetime.strptime(raw, "%I:%M %p").time()
    except Exception:
        return None

    try:
        base_date = _dt_datetime.fromisoformat(str(as_of_date)).date()
    except Exception:
        return None

    return _dt_datetime.combine(
        base_date,
        parsed_time,
        tzinfo=_ZoneInfo("America/New_York"),
    )


def _game_has_started_for_slot_lock(row: dict, ctx_obj: dict) -> bool:
    if _boolish(row.get("game_started")):
        return True

    game_dt = _parse_game_time_today_et(
        str(ctx_obj.get("as_of_date") or ""),
        str(row.get("game_time_et") or ""),
    )
    if not game_dt:
        return False

    return _dt_datetime.now(_ZoneInfo("America/New_York")) >= game_dt


def build_auto_locked_assignments_from_started_games(rows: list[dict], ctx_obj: dict) -> dict[str, str]:
    active_slot_types = {"C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"}
    valid_slot_ids = {slot_id for slot_id, _slot_type in SLOT_ORDER}

    locks: dict[str, str] = {}
    of_seen = 0

    for row in rows:
        current_slot = str(row.get("slot_display") or row.get("current_slot") or "").strip().upper()
        if current_slot not in active_slot_types:
            continue

        if current_slot == "OF":
            of_seen += 1
            slot_id = f"OF{of_seen}"
        else:
            slot_id = current_slot

        if slot_id not in valid_slot_ids:
            continue

        if not _game_has_started_for_slot_lock(row, ctx_obj):
            continue

        player_key = make_player_key(row)
        if player_key:
            locks[slot_id] = player_key

    return locks


def _format_auto_locked_assignments(locks: dict[str, str]) -> str:
    if not locks:
        return ""
    parts = [f"{slot}: {player}" for slot, player in locks.items()]
    return "Locked today: " + "; ".join(parts)


def use_h2h_start_every_active_mode() -> bool:
    app_alias = os.getenv("APP_ALIAS", "").strip().lower()
    if app_alias in {"mlf-rmt", "milf-rmt"}:
        return True

    league_key = str((globals().get("ctx") or {}).get("league_key") or "").strip()
    return league_key in {"469.l.41640", "469.l.60688"}


def slot_min_ranking(slot_id: str, slot_type: str) -> float:
    if use_h2h_start_every_active_mode():
        return 1.0

    try:
        base_threshold = float(_CURRENT_SLOT_FLOORS.get(slot_type, 50.0))
    except Exception:
        base_threshold = 50.0

    ctx_obj = globals().get("ctx") or {}
    if str(ctx_obj.get("league_key") or "").strip() == USUAL_CAP_USAGE_LEAGUE_KEY:
        slot = str(slot_type or "").upper()
        try:
            diff = float(_CURRENT_SLOT_ASSIGNMENT_DIFFS.get(slot, 0.0) or 0.0)
        except Exception:
            diff = 0.0

        # Usual-RMT rule:
        # Only enforce the start threshold when the slot is projected ahead by at least +1.
        # If the slot is even or behind pace, maximize total lineup rank instead.
        if diff < 1.0:
            return 0.0

    return base_threshold


def startable_for_slot(row: dict, slot_id: str, slot_type: str) -> bool:
    if not eligible_for_slot(row, slot_type):
        return False
    if not has_game_today(row):
        return False

    if use_h2h_start_every_active_mode():
        lineup_status = str(row.get("lineup_status") or "").strip().upper()
        if lineup_status == "POSTED_BUT_NOT_FOUND":
            return False

    try:
        ranking = float(row.get("ranking") or 0.0)
    except Exception:
        ranking = 0.0

    return ranking >= slot_min_ranking(slot_id, slot_type)


def candidate_rows_for_slot(rows: list[dict], slot_id: str, slot_type: str) -> list[dict]:
    out = [r for r in rows if startable_for_slot(r, slot_id, slot_type)]
    out.sort(key=lambda r: (-int(r.get("ranking", 0)), str(r.get("player_display", ""))))
    return out


def make_player_key(row: dict) -> str:
    return str(row.get("player_display") or row.get("player_name") or "")


def build_player_index(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    active_rows = [r for r in rows if not is_unavailable(r)]
    active_rows.sort(key=lambda r: (-int(r.get("ranking", 0)), make_player_key(r)))
    idx = {make_player_key(r): i for i, r in enumerate(active_rows)}
    return active_rows, idx


def slot_label(slot_id: str, slot_type: str) -> str:
    return slot_id if slot_id.startswith("OF") else slot_type


def last_name(name: str) -> str:
    parts = [p for p in str(name or "").strip().replace(",", "").split() if p]
    if not parts:
        return ""
    if parts[-1].upper() in SUFFIXES and len(parts) >= 2:
        return parts[-2]
    return parts[-1]




def compress_rank_reason(text: str) -> str:
    s = str(text or "")
    replacements = [
        ("Bat ", "B: "),
        ("Pitcher ", "P: "),
        ("Hand ", "H: "),
        ("Home/Away ", "H/A: "),
        ("Day/Night ", "D/N: "),
        ("Recent ", "R: "),
        ("Status ", "S: "),
        ("Lineup ", "L: "),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    return s

def _short_game_line(line: str) -> str:
    s = str(line or "").strip()
    if not s:
        return s
    s = s.replace(" ET", "")
    for full_name, abbr in TEAM_NAME_TO_ABBR.items():
        s = s.replace(f"@ {full_name.title()}", f"@ {abbr}")
        s = s.replace(f"vs {full_name.title()}", f"vs {abbr}")
    s = s.replace(" — ", " ")
    return s


def game_with_pitcher(row: dict) -> str:
    base = str(row.get("game_display") or "").strip()
    if not base or base in {"No game today", "Game data missing"}:
        return base

    base_lines = [_short_game_line(x) for x in base.splitlines() if str(x).strip()]
    raw_pitchers = str(row.get("opposing_probable_pitcher") or "").strip()
    pitcher_lines = [last_name(x) for x in raw_pitchers.splitlines() if str(x).strip()]

    if not pitcher_lines:
        return " | ".join(base_lines)

    out = []
    for i, line in enumerate(base_lines):
        pitcher = pitcher_lines[i] if i < len(pitcher_lines) else pitcher_lines[-1]
        out.append(f"{line} - SP: {pitcher}" if pitcher else line)
    return " | ".join(out)



_CURRENT_SLOT_ASSIGNMENT_DIFFS: dict[str, float] = {}


def _slot_assignment_start_penalty(row: dict) -> float:
    match = re.search(r"Start%\s+(-?\d+(?:\.\d+)?)", str(row.get("note_short") or ""))
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except Exception:
        return 0.0


def _slot_assignment_risk_score(row: dict) -> float:
    penalty = _slot_assignment_start_penalty(row)
    if penalty >= 0:
        return 0.0
    return min(1.0, abs(penalty) / 10.0)


def _slot_assignment_cap_bonus(slot_type: str) -> float:
    slot = str(slot_type or "").upper()
    try:
        diff = float(_CURRENT_SLOT_ASSIGNMENT_DIFFS.get(slot, 0.0) or 0.0)
    except Exception:
        diff = 0.0
    return min(3.0, max(0.0, -diff) * 0.75)


def _slot_assignment_flex_bonus(slot_type: str, row: dict) -> float:
    slot = str(slot_type or "").upper()
    risk = _slot_assignment_risk_score(row)

    weights = {
        "UTIL": 1.00,
        "IF": 0.70,
        "OF": 0.40,
        "1B": 0.10,
        "2B": 0.10,
        "3B": 0.10,
        "SS": 0.10,
        "C": 0.00,
    }
    return risk * weights.get(slot, 0.0)


def _slot_assignment_reliable_constrained_bonus(slot_type: str, row: dict) -> float:
    slot = str(slot_type or "").upper()
    reliability = 1.0 - _slot_assignment_risk_score(row)

    weights = {
        "C": 0.75,
        "1B": 0.75,
        "2B": 0.75,
        "3B": 0.75,
        "SS": 0.75,
        "OF": 0.35,
        "IF": 0.10,
        "UTIL": 0.00,
    }
    return reliability * weights.get(slot, 0.0)


def slot_assignment_bonus(slot_type: str, row: dict) -> float:
    ctx_obj = globals().get("ctx") or {}
    if str(ctx_obj.get("league_key") or "").strip() != "469.l.22528":
        return 0.0

    return (
        _slot_assignment_cap_bonus(slot_type)
        + _slot_assignment_flex_bonus(slot_type, row)
        + _slot_assignment_reliable_constrained_bonus(slot_type, row)
    )


def _current_usual_assignment_slot_diffs(ctx_obj: dict) -> dict[str, float]:
    if str(ctx_obj.get("league_key") or "").strip() != "469.l.22528":
        return {}

    try:
        summary = _fetch_usual_cap_usage_summary(ctx_obj)
        projections = _usual_cap_projection_values(ctx_obj, summary)
    except Exception:
        return {}

    return {
        slot: float((projections.get(slot) or {}).get("diff", 0.0) or 0.0)
        for slot in ["C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"]
    }


def slot_pace_priority_bonus(slot_type: str) -> float:
    ctx_obj = globals().get("ctx") or {}
    if str(ctx_obj.get("league_key") or "").strip() != "469.l.22528":
        return 0.0

    slot = str(slot_type or "").upper()
    diff_map = globals().get("_CURRENT_SLOT_ASSIGNMENT_DIFFS") or {}

    try:
        diff = float(diff_map.get(slot, 0.0) or 0.0)
    except Exception:
        diff = 0.0

    if diff < 0:
        return 1000.0 + (abs(diff) * 100.0)
    if diff == 0:
        return 100.0
    return -10.0 * diff


def slot_optimizer_value(slot_id: str, slot_type: str, row: dict) -> float:
    return (
        float(row["ranking"])
        + slot_assignment_bonus(slot_type, row)
        + slot_pace_priority_bonus(slot_type)
    )


def optimize_lineup(rows: list[dict], locked_assignments: dict[str, str | None]) -> dict[str, dict | None]:
    players, player_index = build_player_index(rows)

    slot_candidates: list[list[int]] = []
    locked_indices: dict[int, int] = {}

    for slot_pos, (slot_id, slot_type) in enumerate(SLOT_ORDER):
        eligible_idxs = [
            i for i, row in enumerate(players)
            if startable_for_slot(row, slot_id, slot_type)
        ]
        slot_candidates.append(eligible_idxs)

        locked_name = locked_assignments.get(slot_id)
        if locked_name:
            locked_idx = player_index.get(locked_name)
            if locked_idx is not None and locked_idx in eligible_idxs:
                locked_indices[slot_pos] = locked_idx

    seen_locked = set()
    cleaned_locked: dict[int, int] = {}
    for slot_pos in range(len(SLOT_ORDER)):
        if slot_pos in locked_indices:
            idx = locked_indices[slot_pos]
            if idx not in seen_locked:
                cleaned_locked[slot_pos] = idx
                seen_locked.add(idx)
    locked_indices = cleaned_locked

    @lru_cache(maxsize=None)
    def solve(slot_pos: int, used_mask: int):
        if slot_pos >= len(SLOT_ORDER):
            return 0.0, ()

        slot_id, slot_type = SLOT_ORDER[slot_pos]

        if slot_pos in locked_indices:
            idx = locked_indices[slot_pos]
            bit = 1 << idx
            if used_mask & bit:
                return -inf, ()
            next_score, next_assign = solve(slot_pos + 1, used_mask | bit)
            if next_score == -inf:
                return -inf, ()
            total = slot_optimizer_value(slot_id, slot_type, players[idx]) + next_score
            return total, (idx,) + next_assign

        best_score = -inf
        best_assign = None

        empty_score, empty_assign = solve(slot_pos + 1, used_mask)
        if empty_score > best_score:
            best_score = empty_score
            best_assign = (None,) + empty_assign

        for idx in slot_candidates[slot_pos]:
            bit = 1 << idx
            if used_mask & bit:
                continue
            next_score, next_assign = solve(slot_pos + 1, used_mask | bit)
            if next_score == -inf:
                continue
            total = slot_optimizer_value(slot_id, slot_type, players[idx]) + next_score
            if total > best_score:
                best_score = total
                best_assign = (idx,) + next_assign

        if best_assign is None:
            return -inf, ()
        return best_score, best_assign

    _, assignment = solve(0, 0)

    result: dict[str, dict | None] = {}
    for pos, (slot_id, _slot_type) in enumerate(SLOT_ORDER):
        idx = assignment[pos] if pos < len(assignment) else None
        result[slot_id] = None if idx is None else players[idx]
    return result


def build_starting_lineup_table(assignment: dict[str, dict | None]) -> list[dict]:
    out = []
    for slot_id, slot_type in SLOT_ORDER:
        chosen = assignment.get(slot_id)
        threshold = int(round(float(slot_min_ranking(slot_id, slot_type))))
        out.append(
            {
                "Slot": slot_label(slot_id, slot_type),
                "Threshold": str(threshold),
                "Player": chosen.get("player_display", "") if chosen else "",
                "Eligible Pos.": chosen.get("eligible_display", "") if chosen else "",
                "% Ros": _format_percent_owned(chosen.get("percent_owned")) if chosen else "",
                "Rank": chosen.get("ranking", "") if chosen else "",
                "Band": chosen.get("ranking_band", "") if chosen else "",
                "Game": game_with_pitcher(chosen) if chosen else "",
                "Lineup": _lineup_display(chosen) if chosen else "",
                "Status": chosen.get("status_display", "") if chosen else "",
                **(_modifier_cells(chosen) if chosen else _empty_modifier_cells()),
            }
        )
    return out


def build_slot_table(slot_id: str, slot_type: str, rows: list[dict], selected_name: str | None) -> list[dict]:
    out = []
    for r in candidate_rows_for_slot(rows, slot_id, slot_type):
        out.append(
            {
                "Selected": "✅" if make_player_key(r) == selected_name else "",
                "Player": r.get("player_display", ""),
                "% Ros": _format_percent_owned(r.get("percent_owned")),
                "Rank": r.get("ranking", ""),
                "Band": r.get("ranking_band", ""),
                "Game": game_with_pitcher(r),
                "Lineup": _lineup_display(r),
                "Status": r.get("status_display", ""),
                **_modifier_cells(r),
            }
        )
    return out


def build_bench_table(all_rows: list[dict], assignment: dict[str, dict | None]) -> list[dict]:
    chosen_names = {make_player_key(r) for r in assignment.values() if r}
    out = []
    for r in all_rows:
        if make_player_key(r) not in chosen_names:
            current_slot = str(r.get("slot_display", "") or "").upper()
            raw_slot = current_slot if current_slot in {"IL", "NA"} else "BN"
            display_slot = "⬜ BN" if raw_slot == "BN" else ("🟨 IL" if raw_slot == "IL" else "🟦 NA")
            out.append(
                {
                    "Slot": display_slot,
                    "Player": r.get("player_display", ""),
                    "Eligible Pos.": r.get("eligible_display", ""),
                    "% Ros": _format_percent_owned(r.get("percent_owned")),
                    "Threshold": "",
                    "Rank": r.get("ranking", ""),
                    "Band": r.get("ranking_band", ""),
                    "Game": game_with_pitcher(r),
                    "Lineup": _lineup_display(r),
                    "Status": r.get("status_display", ""),
                    **_modifier_cells(r),
                }
            )
    order = {"⬜ BN": 0, "🟨 IL": 1, "🟦 NA": 2}
    out.sort(key=lambda r: (order.get(str(r.get("Slot") or ""), 99), str(r.get("Player") or "")))
    return out



def _long_dataframe_height(row_count: int, min_height: int = 520) -> int:
    """Large enough to prefer page scrolling over inner dataframe scrolling."""
    try:
        n = int(row_count)
    except Exception:
        n = 0
    return max(min_height, 38 * (n + 1) + 80)





def _round_modifier(value):
    if value in (None, ""):
        return None
    try:
        return round(float(value), 1)
    except Exception:
        return None


def _start_modifier_value(row: dict):
    """Only expose Start modifier when Start% was semantically evaluated."""
    try:
        ranking = float(row.get("ranking") or 0)
    except Exception:
        ranking = 0.0

    lineup_status = str(row.get("lineup_status") or "").strip().upper()
    if lineup_status != "LINEUP_NOT_CONFIRMED" or ranking <= 0:
        return None

    return _round_modifier(row.get("start_frequency_points"))


def _modifier_cells(row: dict) -> dict:
    row = row or {}
    return {
        "B": _round_modifier(row.get("baseline_points")),
        "P": _round_modifier(row.get("pitcher_points")),
        "Hand": _round_modifier(row.get("handedness_points")),
        "H/A": _round_modifier(row.get("home_away_points")),
        "D/N": _round_modifier(row.get("day_night_points")),
        "Recent": _round_modifier(row.get("recent_form_points")),
        "Start": _start_modifier_value(row),
        "H2H": _round_modifier(row.get("h2h_matchup_points")),
        "LineupMod": _round_modifier(row.get("lineup_points")),
        "StatusMod": _round_modifier(row.get("status_risk_points")),
    }


def _empty_modifier_cells() -> dict:
    return {
        "B": None,
        "P": None,
        "Hand": None,
        "H/A": None,
        "D/N": None,
        "Recent": None,
        "Start": None,
        "H2H": None,
        "LineupMod": None,
        "StatusMod": None,
    }


def _lineup_display(row: dict | None) -> str:
    try:
        return lineup_status_with_rotowire(row)
    except Exception:
        return str((row or {}).get("lineup_status") or "")


def _format_percent_owned(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.0f}%"
    except Exception:
        return str(value)


BATTER_PROJECTION_VIEW_OPTIONS = ["Today", "Tomorrow", "Day After Tomorrow"]
BATTER_PROJECTION_FIELD_MAP = {
    "Today": ("Today", "TodayGame", "TodayLineup", "TodayNote"),
    "Tomorrow": ("Tomorrow", "TomorrowGame", "", "TomorrowNote"),
    "Day After Tomorrow": ("Day2", "Day2Game", "", "Day2Note"),
}


def _projection_lookup(multiday_result: dict | None, pool: str) -> dict[str, dict]:
    if not multiday_result:
        return {}

    wanted = str(pool or "").upper()
    return {
        str(row.get("YahooKey") or ""): row
        for row in multiday_result.get("rows", [])
        if str(row.get("Pool") or "").upper() == wanted
    }


def _project_batter_row(row: dict, projection_row: dict | None, projection_view: str) -> dict:
    if projection_view == "Today" or not projection_row:
        return row

    rank_field, game_field, _lineup_field, note_field = BATTER_PROJECTION_FIELD_MAP[projection_view]
    projected = dict(row)

    projected_rank = projection_row.get(rank_field)
    try:
        projected_rank_value = int(round(float(projected_rank or 0)))
    except Exception:
        projected_rank_value = ""
    projected["ranking"] = projected_rank_value
    try:
        projected["ranking_band"] = ranking_band(float(projected_rank_value or 0))
    except Exception:
        projected["ranking_band"] = ""

    projected_game = projection_row.get(game_field) or ""
    projected["game_display"] = projected_game
    projected["opposing_probable_pitcher"] = ""
    projected["lineup_status"] = "PROJECTED"
    projected["game_status"] = "GAME_FOUND" if projected_game and projected_game != "No game" else "NO_GAME_TODAY"
    projected["note_short"] = projection_row.get(note_field) or ""
    projected["projection_view"] = projection_view

    return projected


def _project_batter_rows(rows: list[dict], lookup: dict[str, dict], projection_view: str) -> list[dict]:
    return [
        _project_batter_row(row, lookup.get(str(row.get("yahoo_player_key") or "")), projection_view)
        for row in rows
    ]


def _projection_caption(projection_view: str) -> str:
    if projection_view == "Today":
        return "Today uses current roster/free-agent rows, game context, and posted lineup status when available."
    return f"{projection_view} is projected. Lineups are not confirmed and Yahoo transactions are not implied."


def _render_projection_explainer(projection_view: str) -> None:
    if projection_view == "Today":
        return

    with st.expander("What goes into this projected rank?", expanded=False):
        st.markdown(
            """
Future batter ranks are calculated by the RMT model, not pulled from Yahoo as future rankings.

Inputs used:
- today's real owned roster or today's true free-agent pool
- future game date
- opponent
- home/away
- game time
- opposing probable pitcher
- probable pitcher handedness
- batter vs RHP / vs LHP splits
- home/away splits
- day/night splits
- batter and pitcher Savant inputs
- recent 7-day form
- Start% / recent-start reliability
- H2H matchup adjustment when available

Future views are planning projections. Lineups are not confirmed, probable pitchers can change, and Yahoo add/drop actions are not implied.
"""
        )


ctx = get_runtime_context()

try:
    SLOT_ORDER = fetch_hitter_slot_order(ctx["league_key"], int(str(ctx["as_of_date"])[:4]))
except Exception as exc:
    st.warning(f"Using default hitter slot order because league profile slots could not be loaded: {exc}")
    SLOT_ORDER = list(DEFAULT_HITTER_SLOT_ORDER)

st.title(APP_DISPLAY_NAME)

if not (ctx.get("league_key") and ctx.get("team_key") and ctx.get("as_of_date")):
    st.error("Missing DEFAULT_LEAGUE_KEY / DEFAULT_TEAM_KEY in .env")
    st.stop()

rows = fetch_batter_roster_rows(
    league_key=ctx["league_key"],
    team_key=ctx["team_key"],
    as_of_date=ctx["as_of_date"],
)

slot_floor_meta = compute_schedule_pressure_meta(
    rows,
    ctx["as_of_date"],
    get_remaining_starts(ctx["league_key"], ctx["team_key"], ctx["as_of_date"]),
)
_CURRENT_SLOT_FLOORS = slot_floor_meta["floors"]
_CURRENT_SLOT_FLOOR_META = slot_floor_meta

available_batters = fetch_available_batter_rows(
    league_key=ctx["league_key"],
    team_key=ctx["team_key"],
    as_of_date=ctx["as_of_date"],
)

st.caption(
    f'League={ctx["league_key"]} | Team={ctx["team_key"]} | Date={ctx["as_of_date"]} | Batters={len(rows)}'
)

if any(float(r.get("lineup_points", 0.0)) <= -30.0 for r in rows):
    st.caption(
        "Lineup note: the Lineup column is the source of truth. "
        "A -30.0 lineup modifier is expected when a posted lineup omits the player; "
        "only treat it cautiously if the Lineup status looks inconsistent."
    )

for slot_id, _slot_type in SLOT_ORDER:
    key = f"override_{slot_id}"
    if key not in st.session_state:
        st.session_state[key] = "AUTO"

active_rows = [r for r in rows if not is_unavailable(r)]


USUAL_CAP_USAGE_LEAGUE_KEY = "469.l.22528"
USUAL_CAP_USAGE_SLOT_ORDER = ["C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL", "P"]


def _cap_usage_sort_key(slot: str) -> int:
    try:
        return USUAL_CAP_USAGE_SLOT_ORDER.index(str(slot).upper())
    except ValueError:
        return 99


def _format_baseball_ip_from_decimal(value) -> str:
    try:
        numeric = float(value or 0)
    except Exception:
        numeric = 0.0

    sign = "-" if numeric < 0 else ""
    total_thirds = int(round(abs(numeric) * 3))
    whole = total_thirds // 3
    thirds = total_thirds % 3
    return f"{sign}{whole}.{thirds}"


def _format_cap_usage_value(slot: str, value) -> str:
    slot = str(slot or "").upper()
    if slot == "P":
        return f"{_format_baseball_ip_from_decimal(value)} IP"

    try:
        return str(int(round(float(value or 0))))
    except Exception:
        return "0"


def _fetch_usual_latest_cap_usage(ctx: dict) -> list[dict]:
    if str(ctx.get("league_key") or "").strip() != USUAL_CAP_USAGE_LEAGUE_KEY:
        return []

    sql = """
    WITH latest AS (
        SELECT max(usage_date) AS usage_date
        FROM rmt.usual_daily_cap_usage
        WHERE league_key = %s
          AND team_key = %s
    )
    SELECT
        u.usage_date,
        u.slot_family,
        u.used_value,
        u.source,
        u.loaded_at_utc
    FROM rmt.usual_daily_cap_usage u
    JOIN latest l
      ON l.usage_date = u.usage_date
    WHERE u.league_key = %s
      AND u.team_key = %s
    ORDER BY
      CASE u.slot_family
        WHEN 'C' THEN 1
        WHEN '1B' THEN 2
        WHEN '2B' THEN 3
        WHEN '3B' THEN 4
        WHEN 'SS' THEN 5
        WHEN 'IF' THEN 6
        WHEN 'OF' THEN 7
        WHEN 'UTIL' THEN 8
        WHEN 'P' THEN 9
        ELSE 99
      END
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        ctx.get("league_key"),
                        ctx.get("team_key"),
                        ctx.get("league_key"),
                        ctx.get("team_key"),
                    ),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    return [
        {
            "usage_date": row[0],
            "slot_family": str(row[1]).upper(),
            "used_value": row[2],
            "source": row[3],
            "loaded_at_utc": row[4],
        }
        for row in rows
    ]


def _fetch_usual_recent_cap_usage(ctx: dict, limit_dates: int = 7) -> list[dict]:
    if str(ctx.get("league_key") or "").strip() != USUAL_CAP_USAGE_LEAGUE_KEY:
        return []

    sql = """
    WITH recent_dates AS (
        SELECT DISTINCT usage_date
        FROM rmt.usual_daily_cap_usage
        WHERE league_key = %s
          AND team_key = %s
        ORDER BY usage_date DESC
        LIMIT %s
    )
    SELECT
        u.usage_date,
        u.slot_family,
        u.used_value
    FROM rmt.usual_daily_cap_usage u
    JOIN recent_dates d
      ON d.usage_date = u.usage_date
    WHERE u.league_key = %s
      AND u.team_key = %s
    ORDER BY u.usage_date DESC
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        ctx.get("league_key"),
                        ctx.get("team_key"),
                        limit_dates,
                        ctx.get("league_key"),
                        ctx.get("team_key"),
                    ),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    by_date: dict[str, dict] = {}

    for usage_date, slot, value in rows:
        dkey = str(usage_date)
        if dkey not in by_date:
            by_date[dkey] = {"Date": dkey}

        slot_key = str(slot).upper()
        by_date[dkey][slot_key] = _format_cap_usage_value(slot_key, value)

    out = []
    for dkey in sorted(by_date.keys(), reverse=True):
        row = by_date[dkey]
        ordered = {"Date": row.get("Date", dkey)}
        for slot in USUAL_CAP_USAGE_SLOT_ORDER:
            ordered[slot] = row.get(slot, "")
        out.append(ordered)

    return out


def _fetch_usual_cap_usage_summary(ctx: dict) -> list[dict]:
    if str(ctx.get("league_key") or "").strip() != USUAL_CAP_USAGE_LEAGUE_KEY:
        return []

    sql = """
    WITH seed AS (
        SELECT
            league_key,
            team_key,
            season_year,
            slot_family,
            max_allowed,
            seed_used,
            seed_as_of_date
        FROM rmt.usual_cap_usage_seed
        WHERE league_key = %s
          AND team_key = %s
          AND season_year = %s
    ),
    usage AS (
        SELECT
            u.slot_family,
            sum(u.used_value) AS used_since_seed
        FROM rmt.usual_daily_cap_usage u
        JOIN seed s
          ON s.league_key = u.league_key
         AND s.team_key = u.team_key
         AND s.slot_family = u.slot_family
        WHERE u.usage_date > s.seed_as_of_date
        GROUP BY u.slot_family
    )
    SELECT
        s.slot_family,
        s.seed_used + COALESCE(u.used_since_seed, 0) AS used_now,
        s.max_allowed,
        s.max_allowed - (s.seed_used + COALESCE(u.used_since_seed, 0)) AS remaining_now,
        s.seed_as_of_date
    FROM seed s
    LEFT JOIN usage u
      ON u.slot_family = s.slot_family
    ORDER BY
      CASE s.slot_family
        WHEN 'C' THEN 1
        WHEN '1B' THEN 2
        WHEN '2B' THEN 3
        WHEN '3B' THEN 4
        WHEN 'SS' THEN 5
        WHEN 'IF' THEN 6
        WHEN 'OF' THEN 7
        WHEN 'UTIL' THEN 8
        WHEN 'P' THEN 9
        ELSE 99
      END
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        ctx.get("league_key"),
                        ctx.get("team_key"),
                        int(str(ctx.get("as_of_date"))[:4]),
                    ),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    return [
        {
            "slot_family": str(row[0]).upper(),
            "used_now": row[1],
            "max_allowed": row[2],
            "remaining_now": row[3],
            "seed_as_of_date": row[4],
        }
        for row in rows
    ]


def _format_cap_sidebar_number(slot: str, value) -> str:
    slot = str(slot or "").upper()

    if slot == "P":
        return _format_baseball_ip_from_decimal(value)

    try:
        return str(int(round(float(value or 0))))
    except Exception:
        return "0"



USUAL_CAP_PROJECTION_SEASON_START = date(2026, 3, 27)
USUAL_CAP_PROJECTION_SEASON_END = date(2026, 9, 27)
USUAL_CAP_TEAM_ALIASES = {
    "AZ": ["ARI"],
    "ATH": ["ATH", "OAK"],
    "CWS": ["CWS", "CHW"],
    "KC": ["KC", "KCR"],
    "SD": ["SD", "SDP"],
    "SF": ["SF", "SFG"],
    "TB": ["TB", "TBR"],
    "WSH": ["WSH", "WSN"],
}


@st.cache_data(ttl=21600)
def _usual_cap_team_id_map() -> dict[str, int]:
    req = urllib.request.Request(
        "https://statsapi.mlb.com/api/v1/teams?sportId=1",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    out: dict[str, int] = {}
    for team in data.get("teams", []):
        for key in {team.get("abbreviation"), team.get("teamCode"), team.get("fileCode")}:
            if key:
                out[str(key).upper()] = int(team["id"])
    return out


def _usual_cap_team_id_for(team_abbr: str, team_map: dict[str, int]) -> int | None:
    abbr = str(team_abbr or "").upper()
    if abbr in team_map:
        return team_map[abbr]

    for alias in USUAL_CAP_TEAM_ALIASES.get(abbr, []):
        if alias in team_map:
            return team_map[alias]

    return None


@st.cache_data(ttl=21600)
def _usual_cap_remaining_team_games(team_abbr: str, start_iso: str, end_iso: str) -> int:
    team_map = _usual_cap_team_id_map()
    team_id = _usual_cap_team_id_for(team_abbr, team_map)
    if team_id is None:
        return 0

    params = urllib.parse.urlencode(
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": start_iso,
            "endDate": end_iso,
        }
    )
    req = urllib.request.Request(
        f"https://statsapi.mlb.com/api/v1/schedule?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    return sum(len(d.get("games", [])) for d in data.get("dates", []))


def _usual_cap_row_eligible_set(row: dict) -> set[str]:
    raw = row.get("eligible_display") or row.get("eligible_positions") or ""
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw).replace("[", "").replace("]", "").replace("'", "").split(",")

    return {str(v).strip().upper() for v in values if str(v).strip()}


def _usual_cap_slot_ok(slot: str, eligible: set[str]) -> bool:
    slot = str(slot or "").upper()
    if slot == "UTIL":
        return True
    if slot == "IF":
        return bool(eligible & {"1B", "2B", "3B", "SS", "IF"})
    if slot == "OF":
        return "OF" in eligible
    return slot in eligible


def _fetch_usual_current_cap_slots(ctx: dict) -> list[dict]:
    sql = """
    SELECT selected_position, full_name, yahoo_player_key, mlb_team_abbr
    FROM lineup_tool.roster_snapshot
    WHERE league_key = %s
      AND team_key = %s
      AND as_of_date = %s
      AND upper(selected_position) IN ('C','1B','2B','3B','SS','IF','OF','UTIL')
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (ctx.get("league_key"), ctx.get("team_key"), ctx.get("as_of_date")),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    return [
        {
            "slot": "UTIL" if str(row[0]).upper() == "UTIL" else str(row[0]).upper(),
            "player": row[1],
            "yahoo_player_key": row[2],
            "team": row[3],
        }
        for row in rows
    ]


def _format_cap_diff_number(slot: str, value) -> str:
    slot = str(slot or "").upper()
    if value is None:
        return ""

    if slot == "P":
        return _format_baseball_ip_from_decimal(value)

    try:
        return str(int(round(float(value or 0))))
    except Exception:
        return "0"


def _usual_cap_projection_values(ctx: dict, summary: list[dict]) -> dict[str, dict]:
    active_day = date.fromisoformat(str(ctx.get("as_of_date")))
    today = active_day.isoformat()
    tomorrow = (active_day + timedelta(days=1)).isoformat()
    season_end = USUAL_CAP_PROJECTION_SEASON_END.isoformat()

    current_slots = _fetch_usual_current_cap_slots(ctx)
    summary_by_slot = {row["slot_family"]: row for row in summary}

    def games_for_team(team_abbr: str, start_iso: str) -> int:
        return _usual_cap_remaining_team_games(str(team_abbr or "").upper(), start_iso, season_end)

    def occupant_future(slot: str, start_iso: str) -> int:
        return sum(
            games_for_team(row["team"], start_iso)
            for row in current_slots
            if row["slot"] == slot
        )

    def best_eligible_future(slot: str, start_iso: str) -> int:
        values = []
        for row in active_rows:
            if _usual_cap_slot_ok(slot, _usual_cap_row_eligible_set(row)):
                values.append(games_for_team(row.get("mlb_team_abbr"), start_iso))
        return max(values) if values else 0

    def top_n_eligible_future(slot: str, n: int, start_iso: str) -> int:
        values = []
        for row in active_rows:
            if _usual_cap_slot_ok(slot, _usual_cap_row_eligible_set(row)):
                values.append(games_for_team(row.get("mlb_team_abbr"), start_iso))
        return sum(sorted(values, reverse=True)[:n])

    out: dict[str, dict] = {}

    for slot, row in summary_by_slot.items():
        used = float(row["used_now"] or 0)
        max_allowed = float(row["max_allowed"] or 0)

        if slot == "P":
            elapsed = max(1, (active_day - USUAL_CAP_PROJECTION_SEASON_START).days)
            total = max(1, (USUAL_CAP_PROJECTION_SEASON_END - USUAL_CAP_PROJECTION_SEASON_START).days)
            # Best observed Yahoo-style pitcher cap model:
            # raw pace capped to max, then conservative -2 IP adjustment.
            projected = max(0.0, min(max_allowed, used / elapsed * total) - 2.0)
        elif slot in {"C", "1B", "2B", "3B", "SS", "IF", "UTIL", "OF"}:
            # Best observed Yahoo-style hitter cap model:
            # Use one shared future-games baseline derived from the active hitter occupants.
            # Yahoo appears to allow hitter projections to exceed Max.
            active_hitter_games = [
                games_for_team(row["team"], today)
                for row in current_slots
                if row["slot"] in {"C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"}
            ]
            future_game_sum = sum(active_hitter_games)
            future_game_count = len(active_hitter_games)
            single_future = int((future_game_sum + future_game_count - 1) // future_game_count) if active_hitter_games else 0
            of_future = int(((future_game_sum * 3) + future_game_count - 1) // future_game_count) if active_hitter_games else 0
            future = of_future if slot == "OF" else single_future
            projected = used + future
        else:
            future = max(occupant_future(slot, tomorrow), best_eligible_future(slot, tomorrow))
            projected = min(max_allowed, used + future)

        out[slot] = {
            "projected": projected,
            "diff": projected - max_allowed,
        }

    return out


def render_usual_cap_usage_sidebar(ctx: dict) -> None:
    if str(ctx.get("league_key") or "").strip() != USUAL_CAP_USAGE_LEAGUE_KEY:
        return

    st.divider()
    st.subheader("Max Games & IP")

    summary = _fetch_usual_cap_usage_summary(ctx)

    if not summary:
        st.caption("No cap usage seed found yet.")
        return

    seed_date = summary[0]["seed_as_of_date"]
    projections = _usual_cap_projection_values(ctx, summary)

    display_rows = []
    for row in summary:
        slot = row["slot_family"]
        projection = projections.get(slot, {})

        display_rows.append(
            {
                "Pos": slot,
                "Used": _format_cap_sidebar_number(slot, row["used_now"]),
                "Remain": _format_cap_sidebar_number(slot, row["remaining_now"]),
                "Proj": _format_cap_sidebar_number(slot, projection.get("projected")),
                "Diff": _format_cap_diff_number(slot, projection.get("diff")),
            }
        )

    st.caption(f"Calculated projection. Seed: {seed_date}")
    st.dataframe(
        display_rows,
        hide_index=True,
        width="content",
        key=f"usual_cap_projection_{ctx['as_of_date']}_{len(display_rows)}",
    )

with st.sidebar:
    render_refresh_sidebar(ctx)
    render_usual_cap_usage_sidebar(ctx)

manual_choices: dict[str, str | None] = {}
for slot_id, slot_type in SLOT_ORDER:
    candidates = candidate_rows_for_slot(active_rows, slot_id, slot_type)
    options = ["AUTO"] + [make_player_key(r) for r in candidates]
    current = st.session_state.get(f"override_{slot_id}", "AUTO")
    if current not in options:
        current = "AUTO"
        st.session_state[f"override_{slot_id}"] = "AUTO"

    manual_choices[slot_id] = None if current == "AUTO" else current


_CURRENT_SLOT_ASSIGNMENT_DIFFS = _current_usual_assignment_slot_diffs(ctx)
auto_locked_assignments = build_auto_locked_assignments_from_started_games(rows, ctx)
today_locked_assignments = dict(manual_choices)
today_locked_assignments.update(auto_locked_assignments)
assignment = optimize_lineup(active_rows, today_locked_assignments)
starting_lineup_rows = build_starting_lineup_table(assignment)
bench_rows = build_bench_table(rows, assignment)
combined_roster_rows = starting_lineup_rows + bench_rows
combined_roster_df = pd.DataFrame(combined_roster_rows)

def _style_combined_roster_row(row):
    styles = [""] * len(row)
    cols = list(row.index)

    slot = str(row.get("Slot") or "").strip().upper()
    active_slots = {"C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"}
    is_active_slot = slot in active_slots or slot.startswith("OF") or slot.startswith("UTIL")

    # Only color selected starting-lineup rows. Bench rows remain neutral.
    if not is_active_slot:
        if "Rank" in cols:
            styles[cols.index("Rank")] += " font-weight: 600;"
        return styles

    lineup = str(row.get("Lineup") or "")

    if lineup == "IN_POSTED_LINEUP":
        row_style = "background-color: #17351f; color: #d7f5df;"
    elif lineup == "POSTED_BUT_NOT_FOUND" or "RW Expected Out" in lineup or "RW Posted Out" in lineup:
        row_style = "background-color: #4a232b; color: #ffd9df;"
    elif "RW Expected In" in lineup or "RW Posted In" in lineup:
        row_style = "background-color: #3a3217; color: #f7efc6;"
    else:
        row_style = ""

    if row_style:
        for i, col in enumerate(cols):
            if col != "Slot":
                styles[i] = row_style

    if "Rank" in cols:
        styles[cols.index("Rank")] += " font-weight: 600;"

    return styles

combined_roster_styler = combined_roster_df.style.apply(_style_combined_roster_row, axis=1)

try:
    batter_multiday_projection = build_batter_multiday_projection(ctx, days=3, include_fa=True)
except Exception as exc:
    batter_multiday_projection = None
    st.warning(f"3-day batter projection unavailable: {exc}")


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
    # Avoid double scrolling by making the editor tall enough for all rows.
    # Streamlit still caps based on browser/window constraints, but this removes the default small grid.
    return max(220, min(1400, 42 * (int(row_count or 0) + 2)))


def _render_policy_editor(ctx: dict, policy_rows: list[dict], roster_type: str) -> None:
    rows = [r for r in policy_rows if r.get("Type") == roster_type]

    st.markdown(f"#### {roster_type}s")

    if not rows:
        st.info(f"No {roster_type.lower()} policy rows found for the current roster/date.")
        return

    policy_df = pd.DataFrame(rows)

    with st.form(key=f"roster_policy_form_{roster_type.lower()}_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}"):
        submit = st.form_submit_button(f"Save {roster_type} Policy")

        edited_policy_df = st.data_editor(
            policy_df,
            hide_index=True,
            disabled=["Type", "Slot", "Player", "Yahoo Key", "Eligible", "Status", "Policy Cue"],
            column_order=["Slot", "Player", "Eligible", "Status", "Policy Cue", "Policy", "Notes"],
            column_config={
                "Yahoo Key": None,
                "Type": None,
                "Policy": st.column_config.SelectboxColumn(
                    "Policy",
                    options=ROSTER_POLICY_STATUSES,
                    required=True,
                ),
                "Policy Cue": st.column_config.TextColumn("Policy Cue"),
                "Notes": st.column_config.TextColumn("Notes"),
            },
            key=f"roster_policy_editor_{roster_type.lower()}_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}_{len(rows)}",
            use_container_width=True,
            height=_policy_editor_height(len(rows)),
        )

        if submit:
            saved = save_roster_policy_rows(ctx, edited_policy_df.to_dict("records"))
            st.success(f"Saved {roster_type.lower()} policy for {saved} players.")
            st.rerun()

def fetch_roster_policy_rows(ctx: dict) -> list[dict]:
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
    ORDER BY
        CASE upper(r.selected_position)
            WHEN 'C' THEN 1
            WHEN '1B' THEN 2
            WHEN '2B' THEN 3
            WHEN '3B' THEN 4
            WHEN 'SS' THEN 5
            WHEN 'IF' THEN 6
            WHEN 'OF' THEN 7
            WHEN 'UTIL' THEN 8
            WHEN 'BN' THEN 9
            WHEN 'IL' THEN 10
            WHEN 'NA' THEN 11
            WHEN 'P' THEN 12
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
        slot = str(row[0] or "").upper()
        is_pitcher = slot == "P" or "P" in tokens

        out.append(
            {
                "Type": "Pitcher" if is_pitcher else "Batter",
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


def save_roster_policy_rows(ctx: dict, edited_rows) -> int:
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



def _recommendation_rank_score(assignment: dict[str, dict | None]) -> float:
    return sum(float(r.get("ranking") or 0) for r in assignment.values() if r)


def _recommendation_objective_score(assignment: dict[str, dict | None]) -> float:
    total = 0.0
    for slot_id, slot_type in SLOT_ORDER:
        row = assignment.get(slot_id)
        if row:
            total += float(slot_optimizer_value(slot_id, slot_type, row))
    return total


def build_batter_recommendation_preview(ctx_obj: dict, projection_view: str, min_rank_gain: float = 5.0) -> tuple[list[dict], list[dict], list[dict], dict]:
    owned_rows = fetch_batter_roster_rows(ctx_obj["league_key"], ctx_obj["team_key"], ctx_obj["as_of_date"])
    fa_rows = fetch_available_batter_rows(ctx_obj["league_key"], ctx_obj["team_key"], ctx_obj["as_of_date"])

    globals()["ctx"] = ctx_obj
    globals()["_CURRENT_SLOT_ASSIGNMENT_DIFFS"] = _current_usual_assignment_slot_diffs(ctx_obj)
    try:
        globals()["_CURRENT_SLOT_FLOORS"] = _current_slot_floors(ctx_obj)
    except Exception:
        pass

    if projection_view == "Today":
        projected_owned = owned_rows
        projected_fa = fa_rows
        auto_locks = build_auto_locked_assignments_from_started_games(owned_rows, ctx_obj)
        locks = dict(manual_choices)
        locks.update(auto_locks)
    else:
        projection = build_batter_multiday_projection(ctx_obj, days=3, include_fa=True)
        owned_lookup = _projection_lookup(projection, "OWNED")
        fa_lookup = _projection_lookup(projection, "FA")
        projected_owned = _project_batter_rows(owned_rows, owned_lookup, projection_view)
        projected_fa = _project_batter_rows(fa_rows, fa_lookup, projection_view)
        locks = {}

    active_owned = [r for r in projected_owned if not is_unavailable(r)]

    def player_key(row: dict) -> str:
        return make_player_key(row)

    def boolish(v) -> bool:
        return str(v or "").strip().lower() in {"true", "t", "1", "yes", "y"}

    baseline_assignment = optimize_lineup(active_owned, locks)
    baseline_rank_score = _recommendation_rank_score(baseline_assignment)
    baseline_objective_score = _recommendation_objective_score(baseline_assignment)
    baseline_names = {player_key(r) for r in baseline_assignment.values() if r}
    locked_players = set(locks.values())

    baseline_rows: list[dict] = []
    for slot_id, slot_type in SLOT_ORDER:
        r = baseline_assignment.get(slot_id)
        baseline_rows.append(
            {
                "Slot": slot_id,
                "Player": player_key(r) if r else "EMPTY",
                "Rank": int(float(r.get("ranking") or 0)) if r else "",
                "Policy": r.get("policy_status", "") if r else "",
                "Game": r.get("game_display", "") if r else "",
                "Lineup": lineup_status_with_rotowire(r) if r else "",
            }
        )

    drop_candidates = []
    for r in active_owned:
        name = player_key(r)
        policy = str(r.get("policy_status") or "").upper()
        slot = str(r.get("slot_display") or "").upper()

        if policy != "DROPPABLE_LOW":
            continue
        if slot in {"IL", "NA"}:
            continue
        if projection_view == "Today" and name in locked_players:
            continue
        if projection_view == "Today" and boolish(r.get("game_started")):
            continue

        roster_without = [x for x in active_owned if player_key(x) != name]
        assignment_without = optimize_lineup(roster_without, locks)

        rank_drop_cost = baseline_rank_score - _recommendation_rank_score(assignment_without)
        objective_drop_cost = baseline_objective_score - _recommendation_objective_score(assignment_without)

        drop_candidates.append(
            {
                "row": r,
                "name": name,
                "rank": float(r.get("ranking") or 0),
                "slot": slot,
                "in_baseline": name in baseline_names,
                "rank_drop_cost": rank_drop_cost,
                "objective_drop_cost": objective_drop_cost,
            }
        )

    fa_candidates = []
    for r in projected_fa:
        if is_unavailable(r):
            continue
        if not has_game_today(r):
            continue
        if projection_view == "Today" and boolish(r.get("game_started")):
            continue
        if float(r.get("ranking") or 0) <= 0:
            continue
        fa_candidates.append(r)

    raw_recs = []
    for drop in drop_candidates:
        roster_without = [r for r in active_owned if player_key(r) != drop["name"]]

        for add in fa_candidates:
            add_name = player_key(add)
            test_roster = roster_without + [add]
            assignment = optimize_lineup(test_roster, locks)

            new_rank_score = _recommendation_rank_score(assignment)
            new_objective_score = _recommendation_objective_score(assignment)

            rank_gain = new_rank_score - baseline_rank_score
            objective_gain = new_objective_score - baseline_objective_score

            if rank_gain < min_rank_gain:
                continue

            added_slot = ""
            for slot, row in assignment.items():
                if row and player_key(row) == add_name:
                    added_slot = slot
                    break

            if not added_slot:
                continue

            new_names = {player_key(r) for r in assignment.values() if r}
            displaced = sorted(baseline_names - new_names)

            raw_recs.append(
                {
                    "rank_gain": rank_gain,
                    "objective_gain": objective_gain,
                    "slot": added_slot,
                    "drop": drop,
                    "add": add,
                    "add_name": add_name,
                    "add_rank": float(add.get("ranking") or 0),
                    "new_rank_score": new_rank_score,
                    "displaced": ", ".join(displaced),
                }
            )

    best_by_add = {}
    for rec in raw_recs:
        key = rec["add_name"]
        current = best_by_add.get(key)

        rec_sort = (
            rec["rank_gain"],
            rec["objective_gain"],
            -rec["drop"]["rank_drop_cost"],
            -rec["drop"]["rank"],
            rec["add_rank"],
        )
        cur_sort = None
        if current:
            cur_sort = (
                current["rank_gain"],
                current["objective_gain"],
                -current["drop"]["rank_drop_cost"],
                -current["drop"]["rank"],
                current["add_rank"],
            )

        if current is None or rec_sort > cur_sort:
            best_by_add[key] = rec

    deduped = sorted(
        best_by_add.values(),
        key=lambda r: (-r["rank_gain"], -r["objective_gain"], r["drop"]["rank_drop_cost"], -r["add_rank"], r["add_name"]),
    )

    recommendation_rows = []
    for rec in deduped[:20]:
        drop = rec["drop"]
        add = rec["add"]
        drop_note = "bench / zero-cost drop" if drop["rank_drop_cost"] == 0 else f"starter drop cost {round(drop['rank_drop_cost'], 1)}"

        recommendation_rows.append(
            {
                "Rank Gain": round(rec["rank_gain"], 1),
                "Objective Gain": round(rec["objective_gain"], 3),
                "Add": rec["add_name"],
                "Add Rank": int(float(add.get("ranking") or 0)),
                "Start Slot": rec["slot"],
                "Drop": drop["name"],
                "Drop Rank": int(float(drop["rank"] or 0)),
                "Drop Cost": round(drop["rank_drop_cost"], 1),
                "Displaced": rec["displaced"],
                "Add Game": add.get("game_display", ""),
                "Add Lineup": lineup_status_with_rotowire(add),
                "Reason": f"+{round(rec['rank_gain'], 1)} rank; {drop_note}",
            }
        )

    drop_rows = []
    for drop in sorted(drop_candidates, key=lambda x: (x["rank_drop_cost"], x["rank"], x["name"])):
        r = drop["row"]
        drop_rows.append(
            {
                "Player": drop["name"],
                "Policy": r.get("policy_status", ""),
                "Slot": drop["slot"],
                "Rank": int(float(drop["rank"] or 0)),
                "In Baseline": drop["in_baseline"],
                "Drop Cost": round(drop["rank_drop_cost"], 1),
                "Game": r.get("game_display", ""),
                "Lineup": lineup_status_with_rotowire(r),
            }
        )

    summary = {
        "projection_view": projection_view,
        "baseline_rank_score": baseline_rank_score,
        "baseline_objective_score": baseline_objective_score,
        "drop_candidate_count": len(drop_candidates),
        "fa_candidate_count": len(fa_candidates),
        "raw_recommendation_count": len(raw_recs),
        "deduped_recommendation_count": len(deduped),
    }

    return recommendation_rows, baseline_rows, drop_rows, summary




tab_lineup, tab_recommendations, tab_slots, tab_fa, tab_policy = st.tabs(
    ["Starting Lineup", "Recommendations", "Slots", "Batter Free Agents", "Roster Policy"]
)

with tab_lineup:
    lineup_projection_view = st.radio(
        "Projection View",
        options=BATTER_PROJECTION_VIEW_OPTIONS,
        horizontal=True,
        key=f"lineup_projection_view_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}",
    )
    st.caption(_projection_caption(lineup_projection_view))
    _render_projection_explainer(lineup_projection_view)
    if lineup_projection_view == "Today" and auto_locked_assignments:
        st.caption(_format_auto_locked_assignments(auto_locked_assignments))

    if lineup_projection_view == "Today":
        display_assignment = assignment
        display_rows = rows
        display_combined_roster_rows = combined_roster_rows
        display_styler = combined_roster_styler
    else:
        owned_projection_lookup = _projection_lookup(batter_multiday_projection, "OWNED")
        projected_rows = _project_batter_rows(rows, owned_projection_lookup, lineup_projection_view)
        projected_active_rows = [r for r in projected_rows if not is_unavailable(r)]
        display_assignment = optimize_lineup(projected_active_rows, manual_choices)
        display_combined_roster_rows = (
            build_starting_lineup_table(display_assignment)
            + build_bench_table(projected_rows, display_assignment)
        )
        display_df = pd.DataFrame(display_combined_roster_rows)
        display_styler = display_df.style.apply(_style_combined_roster_row, axis=1)

    total_score = sum(int(r["ranking"]) for r in display_assignment.values() if r)
    st.subheader(f"Optimized starting lineup score: {total_score}")
    st.caption("All game times Eastern.")
    roster_table_height = max(420, 35 * (len(display_combined_roster_rows) + 1) + 3)
    st.dataframe(
        display_styler,
        width="content",
        height=roster_table_height,
        hide_index=True,
        column_config=BATTER_LINEUP_COLUMN_CONFIG,
        key=f"combined_roster_{ctx['as_of_date']}_{lineup_projection_view}_{len(display_combined_roster_rows)}",
    )

with tab_recommendations:
    st.subheader("Batter Recommendations")
    st.caption("Read-only planning. No Yahoo moves are made from this screen.")

    recommendation_view = st.radio(
        "Recommendation View",
        options=["Today", "Tomorrow"],
        horizontal=True,
        key=f"recommendation_view_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}",
    )

    min_rank_gain = st.number_input(
        "Minimum rank gain",
        min_value=1.0,
        max_value=25.0,
        value=5.0,
        step=1.0,
        key=f"recommendation_min_gain_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}",
    )

    rec_rows, rec_baseline_rows, rec_drop_rows, rec_summary = build_batter_recommendation_preview(
        ctx,
        recommendation_view,
        float(min_rank_gain),
    )

    st.caption(
        " | ".join(
            [
                f"View: {rec_summary['projection_view']}",
                f"Baseline score: {int(rec_summary['baseline_rank_score'])}",
                f"Drop candidates: {rec_summary['drop_candidate_count']}",
                f"FA candidates: {rec_summary['fa_candidate_count']}",
                f"Recommendations: {rec_summary['deduped_recommendation_count']}",
            ]
        )
    )

    if rec_rows:
        st.dataframe(
            pd.DataFrame(rec_rows),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No add/drop recommendations meet the current threshold.")

    with st.expander("Baseline lineup used for recommendation scoring", expanded=False):
        st.dataframe(
            pd.DataFrame(rec_baseline_rows),
            width="stretch",
            hide_index=True,
        )

    with st.expander("Eligible drop candidates", expanded=False):
        st.dataframe(
            pd.DataFrame(rec_drop_rows),
            width="stretch",
            hide_index=True,
        )



with tab_slots:
    st.subheader("Slot controls")

    auto_remaining_preview = get_remaining_starts(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])
    with st.expander("Slot cap source", expanded=False):
        st.caption("Auto from cap usage tracker. Usual uses rmt.usual_cap_usage_seed + rmt.usual_daily_cap_usage; other leagues use legacy slot_usage_seed.")
        st.caption(format_remaining_starts_caption(auto_remaining_preview))
        st.checkbox("Use manual slot override", value=False, key="use_manual_slot_override")
        if st.session_state.get("use_manual_slot_override", False):
            for family in SLOT_PRESSURE_FAMILY_ORDER:
                st.number_input(
                    f"{family} remaining starts",
                    min_value=0,
                    max_value=(486 if family == "OF" else 162),
                    step=1,
                    key=f"remaining_starts_{family}",
                    value=auto_remaining_preview.get(family, DEFAULT_SLOT_REMAINING_STARTS[family]),
                )

    st.subheader("Slot overrides")
    st.write("Leave on AUTO to keep the optimized default lineup.")

    for slot_id, slot_type in SLOT_ORDER:
        candidates = candidate_rows_for_slot(active_rows, slot_id, slot_type)
        options = ["AUTO"] + [make_player_key(r) for r in candidates]
        current = st.session_state.get(f"override_{slot_id}", "AUTO")
        if current not in options:
            current = "AUTO"
            st.session_state[f"override_{slot_id}"] = "AUTO"

        st.selectbox(
            slot_label(slot_id, slot_type),
            options=options,
            index=options.index(current),
            key=f"override_{slot_id}",
        )

    st.divider()
    st.subheader("Slot candidates")

    for slot_id, slot_type in SLOT_ORDER:
        chosen = assignment.get(slot_id)
        chosen_name = make_player_key(chosen) if chosen else None
        st.subheader(slot_label(slot_id, slot_type))
        slot_table_rows = build_slot_table(slot_id, slot_type, active_rows, chosen_name)
        st.dataframe(
            slot_table_rows,
            width="content",
            hide_index=True,
            column_config=BATTER_SLOT_COLUMN_CONFIG,
            key=f"slot_table_{slot_id}_{ctx['as_of_date']}_{len(slot_table_rows)}",
        )

with tab_fa:
    st.subheader("Batter Free Agents")
    if not available_batters:
        st.info("Batter free-agent backend is not wired yet, so this tab is a placeholder for now.")
    else:
        fa_projection_view = st.radio(
            "Projection View",
            options=BATTER_PROJECTION_VIEW_OPTIONS,
            horizontal=True,
            key=f"fa_projection_view_{ctx['league_key']}_{ctx['team_key']}_{ctx['as_of_date']}",
        )
        st.caption(_projection_caption(fa_projection_view))
        _render_projection_explainer(fa_projection_view)

        if "fa_slot_filter" not in st.session_state:
            st.session_state["fa_slot_filter"] = "All"

        fa_filters = ["All", "C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"]
        current_filter = st.radio(
            "FA Position Filter",
            options=fa_filters,
            index=fa_filters.index(st.session_state["fa_slot_filter"]),
            horizontal=True,
            label_visibility="collapsed",
            key="fa_slot_filter",
        )

        def fa_matches_filter(row, slot):
            if slot == "All":
                return True
            if slot == "IF":
                return any(
                    eligible_for_slot(row, s)
                    for s in ("1B", "2B", "3B", "SS", "IF")
                )
            return eligible_for_slot(row, slot)

        fa_projection_lookup = _projection_lookup(batter_multiday_projection, "FA")
        projected_available_batters = _project_batter_rows(
            available_batters,
            fa_projection_lookup,
            fa_projection_view,
        )
        filtered_batters = [
            r for r in projected_available_batters
            if fa_matches_filter(r, current_filter)
        ]
        filtered_batters.sort(key=lambda r: -float(r.get("ranking") or 0))

        st.caption(f"Filter: {current_filter} | Free Agents: {len(filtered_batters)}")

        fa_rows = []
        for r in filtered_batters:
            fa_rows.append(
                {
                    "Player": r.get("player_display", ""),
                    "Eligible": r.get("eligible_display", ""),
                    "% Ros": _format_percent_owned(r.get("percent_owned")),
                    "Rank": r.get("ranking", ""),
                    "Game": game_with_pitcher(r),
                    "Lineup": _lineup_display(r),
                    "Status": r.get("status_display", ""),
                    **_modifier_cells(r),
                }
            )
        fa_table_height = _long_dataframe_height(len(fa_rows), min_height=620)

        st.dataframe(
            fa_rows,
            width="content",
            height=fa_table_height,
            hide_index=True,
            column_config=BATTER_FA_COLUMN_CONFIG,
            key=f"fa_batters_{ctx['as_of_date']}_{fa_projection_view}_{current_filter}_{len(fa_rows)}",
        )

st.divider()
with tab_policy:
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

    policy_rows = fetch_roster_policy_rows(ctx)

    if not policy_rows:
        st.info("No roster policy rows found for the current roster/date.")
    else:
        _render_policy_editor(ctx, policy_rows, "Batter")

