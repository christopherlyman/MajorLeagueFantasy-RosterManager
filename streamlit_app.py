from functools import lru_cache
import json
import re
import subprocess
import urllib.parse
import urllib.request
from math import inf
from pathlib import Path
from statistics import mean
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd
import os

from services.queries import (
    fetch_available_batter_rows,
    fetch_batter_roster_rows,
    fetch_remaining_starts_by_slot,
    get_default_context,
)

st.set_page_config(page_title="MLF Roster Manager", layout="wide")


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
        "league_key": file_vals.get("DEFAULT_LEAGUE_KEY") or os.getenv("DEFAULT_LEAGUE_KEY", ""),
        "team_key": file_vals.get("DEFAULT_TEAM_KEY") or os.getenv("DEFAULT_TEAM_KEY", ""),
        "as_of_date": file_vals.get("DEFAULT_AS_OF_DATE") or os.getenv("DEFAULT_AS_OF_DATE", ""),
    }


STATUS_DIR = Path("/app/runtime/status")
LOG_DIR = Path("/app/runtime/logs")
REFRESH_LABELS = {
    "quick": "Quick Refresh",
    "daily": "Daily Refresh",
    "full": "Full Refresh",
    "deep": "Deep Refresh",
}


def _parse_utc(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _format_seconds(total_s: int | float | None) -> str:
    if total_s is None:
        return "n/a"
    total_s = int(round(float(total_s)))
    m, s = divmod(total_s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _status_elapsed_seconds(data: dict) -> int | None:
    started = _parse_utc(data.get("started_at_utc"))
    finished = _parse_utc(data.get("finished_at_utc"))
    if started and finished:
        return int((finished - started).total_seconds())
    return None


def _log_mode_and_elapsed(path: Path) -> tuple[str | None, int | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None, None

    total_match = re.search(r"RUN_END .* total_elapsed_s=(\d+)", text)
    elapsed = int(total_match.group(1)) if total_match else None

    if path.name.startswith("refresh_live_"):
        return "quick", elapsed

    mode_match = re.search(r"RUN_START .* run_mode=(\w+)", text)
    mode = mode_match.group(1) if mode_match else None
    return mode, elapsed


@st.cache_data(ttl=60)
def load_refresh_telemetry():
    status_rows = []
    for name in ("refresh_live_status.json", "refresh_all_status.json"):
        data = _load_json(STATUS_DIR / name)
        if not data:
            continue

        run_type = str(data.get("run_type") or "")
        run_mode = str(data.get("run_mode") or "")
        mode_key = "quick" if run_type == "live" else run_mode
        label = REFRESH_LABELS.get(mode_key, mode_key.title() if mode_key else "Unknown")

        finished = _parse_utc(data.get("finished_at_utc")) or _parse_utc(data.get("started_at_utc"))
        status_rows.append(
            {
                "finished": finished,
                "mode_key": mode_key,
                "label": label,
                "success": bool(data.get("success")),
                "message": str(data.get("message") or ""),
                "as_of_date": str(data.get("as_of_date") or ""),
                "elapsed_s": _status_elapsed_seconds(data),
            }
        )

    status_rows = [r for r in status_rows if r.get("finished") is not None]
    status_rows.sort(key=lambda r: r["finished"], reverse=True)
    last_refresh = status_rows[0] if status_rows else None

    buckets = {"quick": [], "daily": [], "full": [], "deep": []}
    log_paths = sorted(LOG_DIR.glob("refresh_*.log"), reverse=True)[:80]
    for path in log_paths:
        mode, elapsed = _log_mode_and_elapsed(path)
        if mode in buckets and elapsed is not None:
            buckets[mode].append(elapsed)

    averages = {}
    for mode, vals in buckets.items():
        averages[mode] = round(mean(vals[:8])) if vals else None

    return {"last_refresh": last_refresh, "averages": averages}


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


SLOT_ORDER = [
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


def slot_min_ranking(slot_id: str, slot_type: str) -> float:
    try:
        return float(_CURRENT_SLOT_FLOORS.get(slot_type, 50.0))
    except Exception:
        return 50.0
def startable_for_slot(row: dict, slot_id: str, slot_type: str) -> bool:
    if not eligible_for_slot(row, slot_type):
        return False
    if not has_game_today(row):
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

        if slot_pos in locked_indices:
            idx = locked_indices[slot_pos]
            bit = 1 << idx
            if used_mask & bit:
                return -inf, ()
            next_score, next_assign = solve(slot_pos + 1, used_mask | bit)
            if next_score == -inf:
                return -inf, ()
            total = float(players[idx]["ranking"]) + next_score
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
            total = float(players[idx]["ranking"]) + next_score
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
                "Threshold": threshold,
                "Player": chosen.get("player_display", "") if chosen else "",
                "Eligible Pos.": chosen.get("eligible_display", "") if chosen else "",
                "Rank": chosen.get("ranking", "") if chosen else "",
                "Band": chosen.get("ranking_band", "") if chosen else "",
                "Game": game_with_pitcher(chosen) if chosen else "",
                "Lineup": chosen.get("lineup_status", "") if chosen else "",
                "Status": chosen.get("status_display", "") if chosen else "",
                "Rank Reason": compress_rank_reason(chosen.get("note_short", "")) if chosen else "",
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
                "Rank": r.get("ranking", ""),
                "Band": r.get("ranking_band", ""),
                "Game": game_with_pitcher(r),
                "Lineup": r.get("lineup_status", ""),
                "Status": r.get("status_display", ""),
                "Rank Reason": compress_rank_reason(r.get("note_short", "")),
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
                    "Threshold": "",
                    "Rank": r.get("ranking", ""),
                    "Band": r.get("ranking_band", ""),
                    "Game": game_with_pitcher(r),
                    "Lineup": r.get("lineup_status", ""),
                    "Status": r.get("status_display", ""),
                    "Rank Reason": compress_rank_reason(r.get("note_short", "")),
                }
            )
    order = {"⬜ BN": 0, "🟨 IL": 1, "🟦 NA": 2}
    out.sort(key=lambda r: (order.get(str(r.get("Slot") or ""), 99), str(r.get("Player") or "")))
    return out


ctx = get_runtime_context()

st.title("MLF Roster Manager")

if not (ctx.get("league_key") and ctx.get("team_key") and ctx.get("as_of_date")):
    st.error("Missing DEFAULT_LEAGUE_KEY / DEFAULT_TEAM_KEY / DEFAULT_AS_OF_DATE in .env")
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

with st.sidebar:
    st.header("Refresh")

    telemetry = load_refresh_telemetry()
    st.caption(f"Active date: {ctx['as_of_date']}")

    last_refresh = telemetry.get("last_refresh")
    if last_refresh:
        icon = "✅" if last_refresh.get("success") else "❌"
        st.caption(
            f"Last: {last_refresh.get('label')} {icon} | "
            f"{_format_seconds(last_refresh.get('elapsed_s'))} | "
            f"{last_refresh.get('as_of_date')}"
        )

    averages = telemetry.get("averages") or {}
    avg_lines = []
    for mode in ("quick", "daily", "full", "deep"):
        avg = averages.get(mode)
        if avg is not None:
            avg_lines.append(f"{REFRESH_LABELS[mode]} avg: {_format_seconds(avg)}")
    if avg_lines:
        st.caption(" | ".join(avg_lines))

    auto_remaining_preview = get_remaining_starts(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])
    with st.expander("Slot cap source", expanded=False):
        st.caption("Auto from slot_usage_seed + roster_snapshot. Use manual override only if you need to reconcile.")
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

    lock_path = "/tmp/mlf_refresh_all.lock"
    refresh_running = os.path.exists(lock_path)

    st.caption(
        "Quick = roster, games, lineups. "
        "Daily = quick + league rosters + current scoring artifacts. "
        "Full = daily + Yahoo player-pool meta. "
        "Deep = full + Yahoo historical stats."
    )

    refresh_choice = None

    col1, col2 = st.columns(2)
    if col1.button(
        "Quick Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_quick_btn",
    ):
        refresh_choice = ("Quick Refresh", "/app/runtime/refresh_quick.sh")

    if col2.button(
        "Daily Refresh",
        type="primary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_daily_btn",
    ):
        refresh_choice = ("Daily Refresh", "/app/runtime/refresh_daily.sh")

    col3, col4 = st.columns(2)
    if col3.button(
        "Full Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_full_btn",
    ):
        refresh_choice = ("Full Refresh", "/app/runtime/refresh_full.sh")

    if col4.button(
        "Deep Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_deep_btn",
    ):
        refresh_choice = ("Deep Refresh", "/app/runtime/refresh_deep.sh")

    if refresh_choice:
        refresh_label, refresh_script = refresh_choice
        try:
            with open(lock_path, "w", encoding="utf-8") as _lock:
                _lock.write("running\n")

            with st.spinner(f"{refresh_label} running..."):
                proc = subprocess.run(
                    ["/bin/bash", refresh_script],
                    capture_output=True,
                    text=True,
                )

            st.session_state["last_refresh_mode"] = refresh_label
            st.session_state["last_refresh_returncode"] = proc.returncode
            st.session_state["last_refresh_stdout"] = proc.stdout[-20000:]
            st.session_state["last_refresh_stderr"] = proc.stderr[-8000:]

            if proc.returncode == 0:
                try:
                    st.cache_data.clear()
                    st.cache_resource.clear()
                except Exception:
                    pass
                st.success(f"{refresh_label} completed.")
                st.rerun()
            else:
                st.error(f"{refresh_label} failed.")
        finally:
            if os.path.exists(lock_path):
                os.remove(lock_path)

    if refresh_running:
        st.info("Refresh already running.")

    if "last_refresh_stdout" in st.session_state:
        with st.expander(
            f"Last refresh log ({st.session_state.get('last_refresh_mode', 'Unknown')})"
        ):
            st.code(st.session_state.get("last_refresh_stdout", ""))
            stderr = st.session_state.get("last_refresh_stderr", "")
            if stderr:
                st.code(stderr)

    st.header("Slot overrides")
    st.write("Leave on AUTO to keep the optimized default lineup.")
    manual_choices: dict[str, str | None] = {}
    for slot_id, slot_type in SLOT_ORDER:
        candidates = candidate_rows_for_slot(active_rows, slot_id, slot_type)
        options = ["AUTO"] + [make_player_key(r) for r in candidates]
        current = st.session_state.get(f"override_{slot_id}", "AUTO")
        if current not in options:
            current = "AUTO"
            st.session_state[f"override_{slot_id}"] = "AUTO"

        choice = st.selectbox(
            slot_label(slot_id, slot_type),
            options=options,
            index=options.index(current),
            key=f"override_{slot_id}",
        )
        manual_choices[slot_id] = None if choice == "AUTO" else choice

assignment = optimize_lineup(active_rows, manual_choices)
starting_lineup_rows = build_starting_lineup_table(assignment)
bench_rows = build_bench_table(rows, assignment)
combined_roster_rows = starting_lineup_rows + bench_rows
combined_roster_df = pd.DataFrame(combined_roster_rows)

def _style_combined_roster_row(row):
    styles = [""] * len(row)
    cols = list(row.index)

    rank_raw = row.get("Rank", "")
    threshold_raw = row.get("Threshold", "")

    try:
        rank_val = float(rank_raw)
        threshold_val = float(threshold_raw)
    except Exception:
        return styles

    if rank_val > threshold_val:
        row_style = "background-color: #17351f; color: #d7f5df;"
    elif rank_val == threshold_val:
        row_style = "background-color: #3a3217; color: #f7efc6;"
    else:
        row_style = "background-color: #4a232b; color: #ffd9df;"

    for i, col in enumerate(cols):
        if col not in {"Slot", "Threshold"}:
            styles[i] = row_style

    if "Rank" in cols:
        styles[cols.index("Rank")] += " font-weight: 600;"

    return styles

combined_roster_styler = combined_roster_df.style.apply(_style_combined_roster_row, axis=1)

tab_lineup, tab_slots, tab_fa, tab_pitchers = st.tabs(
    ["Starting Lineup", "Slots", "Batter Free Agents", "Pitchers"]
)

with tab_lineup:
    total_score = sum(int(r["ranking"]) for r in assignment.values() if r)
    st.subheader(f"Optimized starting lineup score: {total_score}")
    st.caption("All game times Eastern.")
    roster_table_height = max(420, 35 * (len(combined_roster_rows) + 1) + 3)
    st.dataframe(
        combined_roster_styler,
        width="stretch",
        height=roster_table_height,
        hide_index=True,
        column_config={
            "Rank": st.column_config.TextColumn("Rank", alignment="left"),
            "Threshold": st.column_config.TextColumn("Threshold", alignment="left"),
        },
    )

with tab_slots:
    for slot_id, slot_type in SLOT_ORDER:
        chosen = assignment.get(slot_id)
        chosen_name = make_player_key(chosen) if chosen else None
        st.subheader(slot_label(slot_id, slot_type))
        st.dataframe(
            build_slot_table(slot_id, slot_type, active_rows, chosen_name),
            use_container_width=True,
            hide_index=True,
        )

with tab_fa:
    st.subheader("Batter Free Agents")
    if not available_batters:
        st.info("Batter free-agent backend is not wired yet, so this tab is a placeholder for now.")
    else:
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

        filtered_batters = [r for r in available_batters if fa_matches_filter(r, current_filter)]

        st.caption(f"Filter: {current_filter} | Free Agents: {len(filtered_batters)}")

        fa_rows = []
        for r in filtered_batters:
            fa_rows.append(
                {
                    "Player": r.get("player_display", ""),
                    "Eligible": r.get("eligible_display", ""),
                    "Rank": r.get("ranking", ""),
                    "Game": game_with_pitcher(r),
                    "Lineup": r.get("lineup_status", ""),
                    "Status": r.get("status_display", ""),
                    "Rank Reason": compress_rank_reason(r.get("note_short", "")),
                }
            )
        st.dataframe(fa_rows, use_container_width=True, hide_index=True)

with tab_pitchers:
    st.subheader("Pitchers")
    st.info(
        "Pitcher UI is the next phase. "
        "Once we wire the pitcher query/ranking path, this tab can mirror the same pattern "
        "as Starting Lineup / Slots / Free Agents."
    )

st.divider()
st.caption("Rank Reason key: B=Bat | P=Pitcher | H=Hand | H/A=Home/Away | D/N=Day/Night | R=Recent | L=Lineup")
