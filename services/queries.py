import csv
import importlib.util
import os
import re
from functools import lru_cache
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.db import get_connection
from services.scoring import compute_usual_suspects_batter_ranking, ranking_band, START_WORTHY_THRESHOLD, MIN_RANKING, MAX_RANKING
from services.h2h_matchup import apply_h2h_matchup_score

SLOT_ORDER = {
    "C": 1,
    "1B": 2,
    "2B": 3,
    "3B": 4,
    "SS": 5,
    "IF": 6,
    "OF": 7,
    "UTIL": 10,
    "BN": 20,
    "IL": 30,
    "NA": 40,
}

NAME_HELPER_PATH = Path("/shared/runtime/name_normalization.py")


def _load_name_normalizer():
    if NAME_HELPER_PATH.exists():
        spec = importlib.util.spec_from_file_location("name_normalization", NAME_HELPER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.normalize_name

    def fallback(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return text

    return fallback


normalize_name = _load_name_normalizer()


def _today_ny() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def resolve_as_of_date(explicit_date: str | None = None, offset_days: str | int | None = None) -> str:
    explicit = str(explicit_date or "").strip()
    if explicit:
        return explicit

    try:
        offset = int(str(offset_days or "0").strip() or "0")
    except ValueError:
        offset = 0

    return (datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=offset)).isoformat()


def get_default_context():
    as_of_date = resolve_as_of_date(
        os.environ.get("DEFAULT_AS_OF_DATE"),
        os.environ.get("DEFAULT_DATE_OFFSET_DAYS"),
    )

    return {
        "league_key": os.environ.get("DEFAULT_LEAGUE_KEY", ""),
        "team_key": os.environ.get("DEFAULT_TEAM_KEY", ""),
        "as_of_date": as_of_date,
    }


def _season_year(as_of_date: str) -> int:
    return datetime.strptime(as_of_date, "%Y-%m-%d").year


def _raw_root() -> Path:
    return Path(os.environ.get("RMT_RAW_ROOT", "/app/data/raw"))


def _shared_raw_root() -> Path:
    return Path(os.environ.get("RMT_SHARED_RAW_ROOT", "/app/data/raw"))


def _derived_root() -> Path:
    return Path(os.environ.get("RMT_DERIVED_ROOT", "/app/data/derived"))


def _pick_savant_file(kind: str, year: int) -> Path:
    if kind == "batters":
        filename = f"expected_stats_batters_{year}.csv"
    elif kind == "pitchers":
        filename = f"expected_stats_pitchers_{year}.csv"
    else:
        raise ValueError(f"Unsupported savant kind: {kind}")

    preferred = _raw_root() / "savant" / filename
    if preferred.exists():
        return preferred

    return _shared_raw_root() / "savant" / filename


def _pick_lineup_file(as_of_date: str) -> Path:
    return _derived_root() / f"starting_lineup_players_{as_of_date}.csv"


def _pick_pitcher_hand_file(as_of_date: str) -> Path:
    return _derived_root() / f"opposing_probable_pitchers_with_hand_{as_of_date}.csv"


def _pick_hitter_split_file(as_of_date: str) -> Path:
    return _derived_root() / f"hitter_split_inputs_{as_of_date}.csv"


def _pick_hitter_split_file_fa(as_of_date: str) -> Path:
    return _derived_root() / f"hitter_split_inputs_fa_{as_of_date}.csv"


def _pick_recent7_file(as_of_date: str, variant: str = "roster") -> Path:
    suffix = "" if variant == "roster" else f"_{variant}"
    return _derived_root() / f"recent7_hitter_inputs{suffix}_{as_of_date}.csv"




@lru_cache(maxsize=512)
def _posted_lineup_names_for_team(stat_date: str, team_abbr: str) -> tuple[str, ...] | None:
    team = str(team_abbr or "").strip().upper()
    if not team:
        return None

    root = _derived_root()
    teams_path = root / f"starting_lineup_teams_{stat_date}.csv"
    players_path = root / f"starting_lineup_players_{stat_date}.csv"

    if not teams_path.exists() or not players_path.exists():
        return None

    lineup_posted = False
    with teams_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("team_abbr") or "").strip().upper() != team:
                continue
            if str(row.get("lineup_posted") or "").strip().upper() == "Y":
                lineup_posted = True
                break

    if not lineup_posted:
        return None

    names: list[str] = []
    with players_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("team_abbr") or "").strip().upper() != team:
                continue
            names.append(normalize_name(row.get("player_name", "")))

    return tuple(name for name in names if name)


def _hitter_recent_start_rate(row: dict, as_of_date: str) -> tuple[int, int, float | None]:
    player_name = str(row.get("player_name") or row.get("full_name") or "").strip()
    team = str(
        row.get("mlb_team_abbr")
        or row.get("editorial_team_abbr")
        or ""
    ).strip().upper()

    if not player_name or not team:
        return 0, 0, None

    player_key = normalize_name(player_name)
    active_day = datetime.strptime(as_of_date, "%Y-%m-%d").date()

    starts = 0
    team_lineup_days = 0

    for days_back in range(7, 0, -1):
        stat_date = (active_day - timedelta(days=days_back)).isoformat()
        posted_names = _posted_lineup_names_for_team(stat_date, team)

        if posted_names is None:
            continue

        team_lineup_days += 1
        if player_key in posted_names:
            starts += 1

    rate = starts / team_lineup_days if team_lineup_days else None
    return starts, team_lineup_days, rate


def _start_frequency_penalty(rate: float | None) -> int:
    if rate is None:
        return 0
    if rate >= 0.80:
        return 0
    if rate >= 0.60:
        return -1
    if rate >= 0.40:
        return -3
    if rate >= 0.20:
        return -5
    return -10


def _insert_rank_reason_before_status(note: str, new_part: str) -> str:
    note = str(note or "")
    marker = " | Status "

    if marker in note:
        prefix, suffix = note.rsplit(marker, 1)
        return f"{prefix} | {new_part}{marker}{suffix}"

    return f"{note} | {new_part}" if note else new_part


def apply_start_frequency_penalty(row: dict, score: dict, as_of_date: str) -> dict:
    if str(row.get("lineup_status") or "").strip().upper() != "LINEUP_NOT_CONFIRMED":
        return score

    if int(score.get("ranking") or 0) <= 0:
        return score

    starts, days, rate = _hitter_recent_start_rate(row, as_of_date)
    penalty = _start_frequency_penalty(rate)

    if penalty == 0:
        return score

    out = dict(score)
    ranking = max(MIN_RANKING, min(MAX_RANKING, int(out.get("ranking") or 0) + penalty))

    out["ranking"] = ranking
    out["band"] = ranking_band(ranking)
    out["start_frequency_starts"] = starts
    out["start_frequency_days"] = days
    out["start_frequency_rate"] = rate
    out["start_frequency_points"] = penalty
    out["note_short"] = _insert_rank_reason_before_status(
        str(out.get("note_short") or ""),
        f"Start% {penalty:+.1f}",
    )

    return out


def fetch_hitter_slot_order(league_key: str, season_year: int) -> list[tuple[str, str]]:
    default_slots = [
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

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT settings_json->'roster_positions'
                FROM lineup_tool.league_profile
                WHERE league_key = %s
                  AND season_year = %s;
            """, (league_key, season_year))
            row = cur.fetchone()

    positions = row[0] if row else None
    if not positions:
        return default_slots

    hitter_types = {"C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL"}
    out: list[tuple[str, str]] = []
    counts: dict[str, int] = {}

    for raw in positions:
        slot_type = str(raw or "").strip().upper()
        if slot_type == "UTIL":
            slot_type = "UTIL"

        if slot_type not in hitter_types:
            continue

        counts[slot_type] = counts.get(slot_type, 0) + 1
        n = counts[slot_type]

        if slot_type == "OF":
            slot_id = f"OF{n}"
        elif slot_type == "UTIL":
            slot_id = "UTIL" if n == 1 else f"UTIL{n}"
        else:
            slot_id = slot_type if n == 1 else f"{slot_type}{n}"

        out.append((slot_id, slot_type))

    return out or default_slots


def _split_positions(value):
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = [x.strip() for x in re.split(r"[|,]", str(value)) if x.strip()]
    return raw


def _clean_eligible_positions(value):
    vals = _split_positions(value)
    cleaned = []
    seen = set()
    for v in vals:
        norm = v.upper()
        if norm in {"UTIL", "IL", "NA"}:
            continue
        if norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return ", ".join(cleaned)


def _slot_display(slot):
    if not slot:
        return ""
    slot = str(slot).strip()
    if slot.lower() == "util":
        return "UTIL"
    return slot.upper()


def _status_display(status):
    s = (status or "").strip()
    return s if s else "Active"


def _mlb_game_status_from_raw_json(raw_json):
    if not isinstance(raw_json, dict):
        return "GAME_FOUND"

    status = raw_json.get("status") or {}
    if not isinstance(status, dict):
        return "GAME_FOUND"

    detailed = str(status.get("detailedState") or "").strip().upper()
    status_code = str(status.get("statusCode") or "").strip().upper()

    if detailed == "POSTPONED" or status_code == "DI":
        return "POSTPONED"

    return "GAME_FOUND"


def _mlb_game_display_override(raw_json):
    if not isinstance(raw_json, dict):
        return ""

    status = raw_json.get("status") or {}
    if not isinstance(status, dict):
        return ""

    detailed = str(status.get("detailedState") or "").strip()
    reason = str(status.get("reason") or "").strip()

    if detailed.lower() == "postponed":
        return f"Postponed — {reason}" if reason else "Postponed"

    return ""


def _player_display(player_name, mlb_team_abbr):
    team = (mlb_team_abbr or "").strip()
    return f"{player_name} ({team})" if team else player_name


def _slot_sort_key(slot):
    return SLOT_ORDER.get(_slot_display(slot), 999)


def _last_first_to_full_name(value: str) -> str:
    text = (value or "").strip()
    if "," not in text:
        return text
    last, first = [x.strip() for x in text.split(",", 1)]
    return f"{first} {last}".strip()


def _load_savant_map(kind: str, year: int) -> dict[str, dict]:
    path = _pick_savant_file(kind, year)
    if not path.exists():
        return {}

    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out = {}
    for row in rows:
        full_name = _last_first_to_full_name(row.get("last_name, first_name", ""))
        if not full_name:
            continue
        out[normalize_name(full_name)] = row
    return out


def _pick_lineup_team_file(as_of_date: str) -> Path:
    return _pick_lineup_file(as_of_date).parent / f"starting_lineup_teams_{as_of_date}.csv"


def _load_lineup_map(as_of_date: str) -> tuple[dict[tuple[str, str], dict], dict[str, dict], str]:
    player_path = _pick_lineup_file(as_of_date)
    team_path = _pick_lineup_team_file(as_of_date)

    if not player_path.exists() or not team_path.exists():
        return {}, {}, "LINEUP_DATA_MISSING"

    with player_path.open(encoding="utf-8-sig") as f:
        player_rows = list(csv.DictReader(f))

    with team_path.open(encoding="utf-8-sig") as f:
        team_rows = list(csv.DictReader(f))

    lineup_map: dict[tuple[str, str], dict] = {}
    for row in player_rows:
        name = row.get("player_name", "")
        team_abbr = row.get("team_abbr", "")
        if name and team_abbr:
            lineup_map[(normalize_name(name), str(team_abbr).strip().upper())] = row

    lineup_team_map: dict[str, dict] = {}
    for row in team_rows:
        team_abbr = str(row.get("team_abbr", "")).strip().upper()
        if team_abbr:
            lineup_team_map[team_abbr] = row

    if len(team_rows) == 0:
        return lineup_map, lineup_team_map, "LINEUP_NOT_POSTED_YET"

    return lineup_map, lineup_team_map, "LINEUPS_AVAILABLE"


def _load_pitcher_hand_map(as_of_date: str) -> dict[str, dict]:
    path = _pick_pitcher_hand_file(as_of_date)
    if not path.exists():
        return {}

    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out = {}
    for row in rows:
        name = row.get("pitcher_name", "")
        if name:
            out[normalize_name(name)] = row
    return out


def _load_hitter_split_map(as_of_date: str, variant: str = "roster") -> dict[str, dict]:
    if variant == "fa":
        path = _pick_hitter_split_file_fa(as_of_date)
    else:
        path = _pick_hitter_split_file(as_of_date)
    if not path.exists():
        return {}

    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out = {}
    for row in rows:
        name = row.get("player_name", "")
        if name:
            out[normalize_name(name)] = row
    return out


def _load_recent7_map(as_of_date: str, variant: str = "roster") -> dict[str, dict]:
    path = _pick_recent7_file(as_of_date, variant=variant)
    if not path.exists():
        return {}

    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out = {}
    for row in rows:
        name = row.get("player_name", "")
        if name:
            out[normalize_name(name)] = row
    return out


def _player_day_key(row: dict) -> str:
    key = str(row.get("yahoo_player_key", "") or "").strip()
    if key:
        return key
    return f"{normalize_name(str(row.get('player_name') or ''))}|{str(row.get('mlb_team_abbr') or '').strip().upper()}"


def _mean_component(group: list[dict], field: str) -> float:
    vals = []
    for r in group:
        try:
            vals.append(float(r.get(field) or 0.0))
        except Exception:
            vals.append(0.0)
    return sum(vals) / len(vals) if vals else 0.0


def _collapse_scored_player_day_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(_player_day_key(r), []).append(r)

    lineup_priority = {
        "LINEUP_DATA_MISSING": 50,
        "IN_POSTED_LINEUP": 40,
        "POSTED_BUT_NOT_FOUND": 30,
        "LINEUP_NOT_CONFIRMED": 20,
        "LINEUP_NOT_APPLICABLE": 10,
        "": 0,
    }

    collapsed: list[dict] = []
    for group in grouped.values():
        ordered = sorted(group, key=lambda r: str(r.get("game_date_utc") or ""))
        base = dict(ordered[0])

        game_lines = []
        seen = set()
        for r in ordered:
            disp = str(r.get("game_display") or "").strip()
            if disp and disp not in seen:
                seen.add(disp)
                game_lines.append(disp)

        if game_lines:
            base["game_display"] = "\n".join(game_lines)

        pitcher_lines = []
        for r in ordered:
            p = str(r.get("opposing_probable_pitcher") or "").strip()
            pitcher_lines.append(p)
        if pitcher_lines:
            base["opposing_probable_pitcher"] = "\n".join(pitcher_lines)

        base["game_count"] = len(game_lines) if game_lines else len(group)
        base["is_doubleheader"] = base["game_count"] > 1
        base["game_started"] = any(bool(r.get("game_started")) for r in group)

        statuses = [str(r.get("lineup_status") or "") for r in group]
        if statuses:
            base["lineup_status"] = max(statuses, key=lambda s: lineup_priority.get(s, 0))

        game_statuses = [str(r.get("game_status") or "").strip().upper() for r in group]
        if any(s == "GAME_FOUND" for s in game_statuses):
            base["game_status"] = "GAME_FOUND"
        elif any(s == "POSTPONED" for s in game_statuses):
            base["game_status"] = "POSTPONED"
        elif any(s == "NO_GAME_TODAY" for s in game_statuses):
            base["game_status"] = "NO_GAME_TODAY"

        # keep player/day components once; combine game-context by mean
        baseline_points = _mean_component(group, "baseline_points")
        recent_form_points = _mean_component(group, "recent_form_points")
        status_risk_points = _mean_component(group, "status_risk_points")
        lineup_points = min(float(r.get("lineup_points") or 0.0) for r in group) if group else 0.0

        pitcher_points = _mean_component(group, "pitcher_points")
        handedness_points = _mean_component(group, "handedness_points")
        home_away_points = _mean_component(group, "home_away_points")
        day_night_points = _mean_component(group, "day_night_points")

        status_text = str(base.get("status_display") or base.get("status") or "").strip().upper()
        game_status = str(base.get("game_status") or "").strip().upper()
        force_unavailable = (
            status_text == "NA"
            or status_text.startswith("IL")
            or game_status in {"NO_GAME_TODAY", "POSTPONED"}
        )

        if force_unavailable:
            raw = 0.0
            ranking = 0
        else:
            raw = (
                50.0
                + baseline_points
                + pitcher_points
                + handedness_points
                + home_away_points
                + day_night_points
                + recent_form_points
                + status_risk_points
                + lineup_points
            )
            raw = max(MIN_RANKING, min(MAX_RANKING, raw))
            ranking = int(round(raw))

        base["baseline_points"] = round(baseline_points, 2)
        base["pitcher_points"] = round(pitcher_points, 2)
        base["handedness_points"] = round(handedness_points, 2)
        base["home_away_points"] = round(home_away_points, 2)
        base["day_night_points"] = round(day_night_points, 2)
        base["recent_form_points"] = round(recent_form_points, 2)
        base["status_risk_points"] = round(status_risk_points, 2)
        base["lineup_points"] = round(lineup_points, 2)
        base["ranking"] = ranking
        base["ranking_band"] = ranking_band(raw)
        base["start_worthy"] = raw >= START_WORTHY_THRESHOLD

        if force_unavailable:
            if game_status == "POSTPONED":
                base["note_short"] = "Postponed"
            elif game_status == "NO_GAME_TODAY":
                base["note_short"] = "No game today"
            else:
                base["note_short"] = "Unavailable"
        elif base["is_doubleheader"]:
            reason = " | ".join(
                [
                    f"Bat {baseline_points:+.1f}",
                    f"Pitcher {pitcher_points:+.1f}",
                    f"Hand {handedness_points:+.1f}",
                    f"Home/Away {home_away_points:+.1f}",
                    f"Day/Night {day_night_points:+.1f}",
                    f"Recent {recent_form_points:+.1f}",
                    f"Status {status_risk_points:+.1f}",
                    f"Lineup {lineup_points:+.1f}",
                ]
            )
            base["note_short"] = f"Doubleheader | {reason}"

        collapsed.append(base)

    return collapsed


def _format_game_time_et(game_date_utc: str) -> str:
    text = (game_date_utc or "").strip()
    if not text:
        return ""

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        return dt_et.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return ""


def _game_daypart_et(game_date_utc: str) -> str:
    text = (game_date_utc or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        return "DAY" if dt_et.hour < 18 else "NIGHT"
    except Exception:
        return ""


def _game_started_et(game_date_utc: str) -> bool:
    text = (game_date_utc or "").strip()
    if not text:
        return False
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        now_et = datetime.now(ZoneInfo("America/New_York"))
        return dt_et <= now_et
    except Exception:
        return False


def _game_display(opponent_team: str, is_home, game_time_et: str) -> str:
    opp = (opponent_team or "").strip()
    prefix = ""
    if is_home is True:
        prefix = "vs"
    elif is_home is False:
        prefix = "@"

    left = f"{prefix} {opp}".strip()
    if left and game_time_et:
        return f"{left} — {game_time_et}"
    if left:
        return left
    return game_time_et


def fetch_batter_roster_rows(league_key: str, team_key: str, as_of_date: str):
    sql = """
    WITH games AS (
        SELECT
            raw_json,
            away_team_name,
            home_team_name,
            away_probable_pitcher_name,
            home_probable_pitcher_name
        FROM lineup_tool.mlb_probable_pitcher_daily
        WHERE as_of_date = %s
    )
    SELECT
        r.full_name AS player_name,
        r.mlb_team_abbr,
        r.selected_position AS current_slot,
        r.eligible_positions,
        COALESCE(r.status, '') AS status,
        r.yahoo_player_key,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN g.home_probable_pitcher_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN g.away_probable_pitcher_name
            ELSE ''
        END AS opposing_probable_pitcher,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN g.home_team_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN g.away_team_name
            ELSE ''
        END AS opponent_team,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN FALSE
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = r.mlb_team_abbr
                THEN TRUE
            ELSE NULL
        END AS is_home,
        COALESCE(g.raw_json->>'gameDate', '') AS game_date_utc,
        g.raw_json AS raw_json
    FROM lineup_tool.roster_snapshot r
    LEFT JOIN games g
      ON g.raw_json->'teams'->'away'->'team'->>'abbreviation' = r.mlb_team_abbr
      OR g.raw_json->'teams'->'home'->'team'->>'abbreviation' = r.mlb_team_abbr
    WHERE r.as_of_date = %s
      AND r.league_key = %s
      AND r.team_key = %s
      AND r.position_type = 'B'
    ORDER BY r.full_name;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM lineup_tool.mlb_probable_pitcher_daily WHERE as_of_date = %s",
                (as_of_date,),
            )
            day_game_rows = cur.fetchone()[0]

            cur.execute(sql, (as_of_date, as_of_date, league_key, team_key))
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    year = _season_year(as_of_date)
    hitter_savant = _load_savant_map("batters", year)
    pitcher_savant = _load_savant_map("pitchers", year)
    lineup_map, lineup_team_map, lineup_data_status = _load_lineup_map(as_of_date)
    pitcher_hand_map = _load_pitcher_hand_map(as_of_date)
    hitter_split_map = _load_hitter_split_map(as_of_date)
    recent7_map = _load_recent7_map(as_of_date)

    for r in rows:
        r["slot_display"] = _slot_display(r.get("current_slot"))
        r["player_display"] = _player_display(r.get("player_name", ""), r.get("mlb_team_abbr", ""))
        r["eligible_display"] = _clean_eligible_positions(r.get("eligible_positions"))
        r["status_display"] = _status_display(r.get("status"))
        r["_slot_sort"] = _slot_sort_key(r.get("current_slot"))

        if day_game_rows == 0:
            r["game_status"] = "GAME_DATA_MISSING"
            r["game_display"] = "Game data missing"
            r["game_started"] = False
        elif r.get("opponent_team") or r.get("game_date_utc"):
            r["game_status"] = _mlb_game_status_from_raw_json(r.get("raw_json"))
            r["game_time_et"] = _format_game_time_et(r.get("game_date_utc", ""))
            r["game_daypart"] = _game_daypart_et(r.get("game_date_utc", ""))
            r["game_started"] = _game_started_et(r.get("game_date_utc", ""))
            r["game_display"] = _mlb_game_display_override(r.get("raw_json")) or _game_display(r.get("opponent_team", ""), r.get("is_home"), r["game_time_et"])
        else:
            r["game_status"] = "NO_GAME_TODAY"
            r["game_display"] = "No game today"
            r["game_daypart"] = ""
            r["game_started"] = False

        hitter_row = hitter_savant.get(normalize_name(r.get("player_name", "")), {})
        pitcher_row = pitcher_savant.get(normalize_name(r.get("opposing_probable_pitcher", "")), {})
        hand_row = pitcher_hand_map.get(normalize_name(r.get("opposing_probable_pitcher", "")), {})
        split_row = hitter_split_map.get(normalize_name(r.get("player_name", "")), {})
        recent_row = recent7_map.get(normalize_name(r.get("player_name", "")), {})

        lineup_row = lineup_map.get(
            (normalize_name(r.get("player_name", "")), str(r.get("mlb_team_abbr", "")).strip().upper())
        )
        lineup_team_row = lineup_team_map.get(str(r.get("mlb_team_abbr", "")).strip().upper())

        if lineup_data_status == "LINEUP_DATA_MISSING":
            r["lineup_status"] = "LINEUP_DATA_MISSING"
        elif r.get("game_status") != "GAME_FOUND":
            r["lineup_status"] = "LINEUP_NOT_APPLICABLE"
        elif lineup_row:
            r["lineup_status"] = "IN_POSTED_LINEUP"
        elif lineup_team_row and str(lineup_team_row.get("lineup_posted", "")).strip().upper() == "Y":
            r["lineup_status"] = "POSTED_BUT_NOT_FOUND"
        else:
            r["lineup_status"] = "LINEUP_NOT_CONFIRMED"

        r["hitter_pa"] = hitter_row.get("pa", "")
        r["hitter_ba"] = hitter_row.get("ba", "")
        r["hitter_est_woba"] = hitter_row.get("est_woba", "")
        r["hitter_woba_gap"] = hitter_row.get("est_woba_minus_woba_diff", "")
        r["pitcher_pa"] = pitcher_row.get("pa", "")
        r["pitcher_est_woba_allowed"] = pitcher_row.get("est_woba", "")
        r["pitcher_xera"] = pitcher_row.get("xera", "")
        r["opp_pitcher_throws"] = (hand_row.get("throws") or "").strip().upper()

        r["overall_ops"] = split_row.get("overall_ops", "")
        r["split_vs_rhp_ops"] = split_row.get("vs_rhp_ops", "")
        r["split_vs_rhp_ab"] = split_row.get("vs_rhp_ab", "")
        r["split_vs_lhp_ops"] = split_row.get("vs_lhp_ops", "")
        r["split_vs_lhp_ab"] = split_row.get("vs_lhp_ab", "")
        r["split_home_ops"] = split_row.get("home_ops", "")
        r["split_home_ab"] = split_row.get("home_ab", "")
        r["split_away_ops"] = split_row.get("away_ops", "")
        r["split_away_ab"] = split_row.get("away_ab", "")
        r["split_day_ops"] = split_row.get("day_ops", "")
        r["split_day_ab"] = split_row.get("day_ab", "")
        r["split_night_ops"] = split_row.get("night_ops", "")
        r["split_night_ab"] = split_row.get("night_ab", "")
        r["recent7_hits"] = recent_row.get("recent7_hits", "")
        r["recent7_ab"] = recent_row.get("recent7_ab", "")
        r["recent7_avg"] = recent_row.get("recent7_avg", "")
        r["recent7_r"] = recent_row.get("recent7_r", "")
        r["recent7_hr"] = recent_row.get("recent7_hr", "")
        r["recent7_rbi"] = recent_row.get("recent7_rbi", "")
        r["recent7_sb"] = recent_row.get("recent7_sb", "")
        r["recent7_bb"] = recent_row.get("recent7_bb", "")
        r["recent7_k"] = recent_row.get("recent7_k", "")

        score = compute_usual_suspects_batter_ranking(r)
        score = apply_start_frequency_penalty(r, score, as_of_date)
        score = apply_h2h_matchup_score(r, score, league_key, team_key, as_of_date)
        r.update(score)

    rows = _collapse_scored_player_day_rows(rows)
    rows.sort(key=lambda r: (r["_slot_sort"], r["player_name"]))
    return rows



def _pick_true_free_agent_candidate_file(as_of_date: str) -> Path:
    return _derived_root() / f"true_free_agent_batters_{as_of_date}.csv"


def _load_true_free_agent_candidate_rows(as_of_date: str) -> list[dict]:
    path = _pick_true_free_agent_candidate_file(as_of_date)
    if not path.exists():
        return []

    out: list[dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            player_key = str(row.get("yahoo_player_key") or "").strip()
            name = str(row.get("player_name") or "").strip()
            team = str(row.get("editorial_team_abbr") or "").strip().upper()
            if player_key and name:
                out.append(
                    {
                        "yahoo_player_key": player_key,
                        "player_name": name,
                        "editorial_team_abbr": team,
                    }
                )
    return out


def _load_true_free_agent_candidate_keys(as_of_date: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for row in _load_true_free_agent_candidate_rows(as_of_date):
        name = str(row.get("player_name") or "").strip()
        team = str(row.get("editorial_team_abbr") or "").strip().upper()
        if name:
            out.add((normalize_name(name), team))
    return out


def _load_true_free_agent_candidate_player_keys(as_of_date: str) -> list[str]:
    keys = {
        str(row.get("yahoo_player_key") or "").strip()
        for row in _load_true_free_agent_candidate_rows(as_of_date)
        if str(row.get("yahoo_player_key") or "").strip()
    }
    return sorted(keys)


def fetch_available_batter_rows(league_key: str, team_key: str, as_of_date: str):
    sql = """
    WITH games AS (
        SELECT
            raw_json,
            away_team_name,
            home_team_name,
            away_probable_pitcher_name,
            home_probable_pitcher_name
        FROM lineup_tool.mlb_probable_pitcher_daily
        WHERE as_of_date = %s
    )
    SELECT
        p.full_name AS player_name,
        p.editorial_team_abbr AS mlb_team_abbr,
        '' AS current_slot,
        p.eligible_positions,
        '' AS status,
        p.yahoo_player_key,
        p.percent_owned,
        p.rank_value,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.home_probable_pitcher_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.away_probable_pitcher_name
            ELSE ''
        END AS opposing_probable_pitcher,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.home_team_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.away_team_name
            ELSE ''
        END AS opponent_team,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN FALSE
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN TRUE
            ELSE NULL
        END AS is_home,
        COALESCE(g.raw_json->>'gameDate', '') AS game_date_utc,
        g.raw_json AS raw_json
    FROM public.yahoo_league_player_pool p
    LEFT JOIN games g
      ON g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
      OR g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
    LEFT JOIN lineup_tool.roster_snapshot r
      ON r.league_key = %s
     AND r.as_of_date = %s
     AND r.yahoo_player_key = p.yahoo_player_key
    WHERE p.league_key = %s
      AND p.season_year = %s
      AND r.yahoo_player_key IS NULL
      AND NOT (
        COALESCE(p.eligible_positions, '[]'::jsonb) ? 'P'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'SP'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'RP'
      )
      AND NOT (
        COALESCE(p.eligible_positions, '[]'::jsonb) ? 'IL'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'NA'
      )
    ORDER BY
      COALESCE(p.rank_value, 999999),
      COALESCE(p.percent_owned, -1) DESC,
      p.full_name
    LIMIT 300;
    """

    candidate_player_keys = _load_true_free_agent_candidate_player_keys(as_of_date)
    if candidate_player_keys:
        sql = sql.replace(
            """    ORDER BY
      COALESCE(p.rank_value, 999999),
      COALESCE(p.percent_owned, -1) DESC,
      p.full_name
    LIMIT 300;""",
            """      AND p.yahoo_player_key = ANY(%s::text[])
    ORDER BY
      COALESCE(p.rank_value, 999999),
      COALESCE(p.percent_owned, -1) DESC,
      p.full_name;""",
        )
        if "p.yahoo_player_key = ANY(%s::text[])" not in sql:
            raise RuntimeError("Failed to inject true FA player-key filter into FA batter query")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM lineup_tool.mlb_probable_pitcher_daily WHERE as_of_date = %s",
                (as_of_date,),
            )
            day_game_rows = cur.fetchone()[0]

            cur.execute(
                sql,
                (
                    as_of_date,
                    league_key,
                    as_of_date,
                    league_key,
                    _season_year(as_of_date),
                    *([candidate_player_keys] if candidate_player_keys else []),
                ),
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    year = _season_year(as_of_date)
    hitter_savant = _load_savant_map("batters", year)
    pitcher_savant = _load_savant_map("pitchers", year)
    lineup_map, lineup_team_map, lineup_data_status = _load_lineup_map(as_of_date)
    pitcher_hand_map = _load_pitcher_hand_map(as_of_date)
    hitter_split_map = _load_hitter_split_map(as_of_date, variant="fa")
    recent7_map = _load_recent7_map(as_of_date, variant="fa")

    for r in rows:
        r["slot_display"] = _slot_display(r.get("current_slot"))
        r["player_display"] = _player_display(r.get("player_name", ""), r.get("mlb_team_abbr", ""))
        r["eligible_display"] = _clean_eligible_positions(r.get("eligible_positions"))
        r["status_display"] = _status_display(r.get("status"))
        r["_slot_sort"] = _slot_sort_key(r.get("current_slot"))

        if day_game_rows == 0:
            r["game_status"] = "GAME_DATA_MISSING"
            r["game_display"] = "Game data missing"
            r["game_started"] = False
        elif r.get("opponent_team") or r.get("game_date_utc"):
            r["game_status"] = _mlb_game_status_from_raw_json(r.get("raw_json"))
            r["game_time_et"] = _format_game_time_et(r.get("game_date_utc", ""))
            r["game_daypart"] = _game_daypart_et(r.get("game_date_utc", ""))
            r["game_started"] = _game_started_et(r.get("game_date_utc", ""))
            r["game_display"] = _mlb_game_display_override(r.get("raw_json")) or _game_display(r.get("opponent_team", ""), r.get("is_home"), r["game_time_et"])
        else:
            r["game_status"] = "NO_GAME_TODAY"
            r["game_display"] = "No game today"
            r["game_daypart"] = ""
            r["game_started"] = False

        hitter_row = hitter_savant.get(normalize_name(r.get("player_name", "")), {})
        pitcher_row = pitcher_savant.get(normalize_name(r.get("opposing_probable_pitcher", "")), {})
        hand_row = pitcher_hand_map.get(normalize_name(r.get("opposing_probable_pitcher", "")), {})
        split_row = hitter_split_map.get(normalize_name(r.get("player_name", "")), {})
        recent_row = recent7_map.get(normalize_name(r.get("player_name", "")), {})

        lineup_row = lineup_map.get(
            (normalize_name(r.get("player_name", "")), str(r.get("mlb_team_abbr", "")).strip().upper())
        )
        lineup_team_row = lineup_team_map.get(str(r.get("mlb_team_abbr", "")).strip().upper())

        if lineup_data_status == "LINEUP_DATA_MISSING":
            r["lineup_status"] = "LINEUP_DATA_MISSING"
        elif r.get("game_status") != "GAME_FOUND":
            r["lineup_status"] = "LINEUP_NOT_APPLICABLE"
        elif lineup_row:
            r["lineup_status"] = "IN_POSTED_LINEUP"
        elif lineup_team_row and str(lineup_team_row.get("lineup_posted", "")).strip().upper() == "Y":
            r["lineup_status"] = "POSTED_BUT_NOT_FOUND"
        else:
            r["lineup_status"] = "LINEUP_NOT_CONFIRMED"

        r["hitter_pa"] = hitter_row.get("pa", "")
        r["hitter_ba"] = hitter_row.get("ba", "")
        r["hitter_est_woba"] = hitter_row.get("est_woba", "")
        r["hitter_woba_gap"] = hitter_row.get("est_woba_minus_woba_diff", "")
        r["pitcher_pa"] = pitcher_row.get("pa", "")
        r["pitcher_est_woba_allowed"] = pitcher_row.get("est_woba", "")
        r["pitcher_xera"] = pitcher_row.get("xera", "")
        r["opp_pitcher_throws"] = (hand_row.get("throws") or "").strip().upper()

        r["overall_ops"] = split_row.get("overall_ops", "")
        r["split_vs_rhp_ops"] = split_row.get("vs_rhp_ops", "")
        r["split_vs_rhp_ab"] = split_row.get("vs_rhp_ab", "")
        r["split_vs_lhp_ops"] = split_row.get("vs_lhp_ops", "")
        r["split_vs_lhp_ab"] = split_row.get("vs_lhp_ab", "")
        r["split_home_ops"] = split_row.get("home_ops", "")
        r["split_home_ab"] = split_row.get("home_ab", "")
        r["split_away_ops"] = split_row.get("away_ops", "")
        r["split_away_ab"] = split_row.get("away_ab", "")
        r["split_day_ops"] = split_row.get("day_ops", "")
        r["split_day_ab"] = split_row.get("day_ab", "")
        r["split_night_ops"] = split_row.get("night_ops", "")
        r["split_night_ab"] = split_row.get("night_ab", "")
        r["recent7_hits"] = recent_row.get("recent7_hits", "")
        r["recent7_ab"] = recent_row.get("recent7_ab", "")
        r["recent7_avg"] = recent_row.get("recent7_avg", "")
        r["recent7_r"] = recent_row.get("recent7_r", "")
        r["recent7_hr"] = recent_row.get("recent7_hr", "")
        r["recent7_rbi"] = recent_row.get("recent7_rbi", "")
        r["recent7_sb"] = recent_row.get("recent7_sb", "")
        r["recent7_bb"] = recent_row.get("recent7_bb", "")
        r["recent7_k"] = recent_row.get("recent7_k", "")

        score = compute_usual_suspects_batter_ranking(r)
        score = apply_start_frequency_penalty(r, score, as_of_date)
        score = apply_h2h_matchup_score(r, score, league_key, team_key, as_of_date)
        r.update(score)
        r["comparison_delta"] = ""

    rows = _collapse_scored_player_day_rows(rows)
    rows.sort(key=lambda r: (-int(r.get("ranking", 0)), str(r.get("player_name", ""))))
    return rows

def fetch_remaining_starts_by_slot(league_key: str, team_key: str, as_of_date: str) -> dict[str, int]:
    season_year = int(str(as_of_date)[:4])

    sql = """
    WITH seed AS (
        SELECT
            slot_family,
            max_games,
            seed_played,
            seed_as_of_date
        FROM lineup_tool.slot_usage_seed
        WHERE league_key = %s
          AND team_key = %s
          AND season_year = %s
    ),
    usage_since_seed AS (
        SELECT
            CASE
                WHEN rs.selected_position = 'Util' THEN 'UTIL'
                ELSE UPPER(rs.selected_position)
            END AS slot_family,
            COUNT(DISTINCT (rs.as_of_date, rs.yahoo_player_key, rs.selected_position))::integer AS played_since_seed
        FROM lineup_tool.roster_snapshot rs
        JOIN seed s
          ON s.slot_family = CASE
                                WHEN rs.selected_position = 'Util' THEN 'UTIL'
                                ELSE UPPER(rs.selected_position)
                             END
        WHERE rs.league_key = %s
          AND rs.team_key = %s
          AND rs.position_type = 'B'
          AND rs.selected_position IN ('C','1B','2B','3B','SS','IF','OF','Util')
          AND rs.as_of_date >= s.seed_as_of_date
          AND rs.as_of_date < %s::date
        GROUP BY 1
    )
    SELECT
        s.slot_family,
        GREATEST(
            0,
            s.max_games - (s.seed_played + COALESCE(u.played_since_seed, 0))
        )::integer AS remaining_starts
    FROM seed s
    LEFT JOIN usage_since_seed u
      ON u.slot_family = s.slot_family
    ORDER BY s.slot_family
    """

    out: dict[str, int] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    league_key, team_key, season_year,
                    league_key, team_key, as_of_date,
                ),
            )
            rows = cur.fetchall()

    for slot_family, remaining_starts in rows:
        out[str(slot_family).upper()] = int(remaining_starts)

    return out
