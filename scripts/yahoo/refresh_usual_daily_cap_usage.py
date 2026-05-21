from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

from scripts.yahoo.auth import get_access_token
from services.db import get_connection
from services.queries import get_default_context


USUAL_LEAGUE_KEY = "469.l.22528"
HITTER_SLOTS = ("C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL")
PITCHER_SLOTS = ("SP", "RP", "P")


def _usage_date_from_context(as_of_date: str) -> date:
    override = str(os.environ.get("USUAL_CAP_USAGE_DATE") or "").strip()
    if override:
        return date.fromisoformat(override)

    active_day = date.fromisoformat(as_of_date)
    return active_day - timedelta(days=1)


def _norm_slot(value) -> str:
    slot = str(value or "").strip().upper()
    if slot == "UTIL":
        return "UTIL"
    return slot


def _create_table() -> None:
    sql = """
    CREATE SCHEMA IF NOT EXISTS rmt;

    CREATE TABLE IF NOT EXISTS rmt.usual_daily_cap_usage (
        league_key text NOT NULL,
        team_key text NOT NULL,
        usage_date date NOT NULL,
        slot_family text NOT NULL,
        used_value numeric(12, 3) NOT NULL,
        source text NOT NULL,
        detail_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        loaded_at_utc timestamp with time zone NOT NULL DEFAULT now(),
        PRIMARY KEY (league_key, team_key, usage_date, slot_family)
    );
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def _safe_int(value) -> int:
    try:
        if value in (None, "", "-"):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _hitter_game_used_from_cache(row) -> int:
    if row is None:
        return 0

    hits, ab, r, hr, rbi, sb, bb, k, fetch_status = row

    if str(fetch_status or "").strip().lower() != "success":
        return 0

    return int(
        _safe_int(ab) > 0
        or _safe_int(r) > 0
        or _safe_int(hr) > 0
        or _safe_int(rbi) > 0
        or _safe_int(sb) > 0
        or _safe_int(bb) > 0
        or _safe_int(k) > 0
        or _safe_int(hits) > 0
    )


def _hitter_game_used_from_yahoo_stat_map(stat_map: dict[str, str]) -> int | None:
    """
    Return:
      1 when Yahoo daily roster stats show hitter activity
      0 when Yahoo daily roster stats show no hitter activity
      None when the Yahoo stat map is missing/insufficient and cache fallback should be used

    Known hitter stat IDs:
      Team roster daily payload:
        60 = H/AB
         7 = R
        12 = HR
        13 = RBI
        16 = SB
        21 = K
         3 = AVG

      Direct player daily payload:
         0 = Games
         6 = AB
         7 = R
        12 = HR
        13 = RBI
        16 = SB
        21 = K
    """
    if not stat_map:
        return None

    h_ab = str(stat_map.get("60") or "").strip()
    if h_ab and h_ab != "-":
        if "/" in h_ab:
            h_s, ab_s = h_ab.split("/", 1)
            if _safe_int(h_s) > 0 or _safe_int(ab_s) > 0:
                return 1
        elif _safe_int(h_ab) > 0:
            return 1

    for sid in ("0", "6", "7", "12", "13", "16", "21"):
        val = str(stat_map.get(sid) or "").strip()
        if val and val != "-" and _safe_int(val) > 0:
            return 1

    # If Yahoo sent explicit hitter stat fields but none indicate activity,
    # treat as no cap game used. AVG alone is intentionally ignored.
    explicit = any(str(stat_map.get(sid) or "").strip() not in ("", "-") for sid in ("60", "0", "6", "7", "12", "13", "16", "21"))
    if explicit:
        return 0

    return None


def _baseball_ip_to_decimal(value) -> Decimal:
    raw = str(value or "").strip()
    if raw in ("", "-"):
        return Decimal("0")

    if "." not in raw:
        return Decimal(int(raw))

    whole_s, frac_s = raw.split(".", 1)
    whole = int(whole_s or "0")
    frac = frac_s[:1] if frac_s else "0"

    if frac == "0":
        thirds = Decimal("0")
    elif frac == "1":
        thirds = Decimal(1) / Decimal(3)
    elif frac == "2":
        thirds = Decimal(2) / Decimal(3)
    else:
        raise ValueError(f"Invalid baseball IP value: {raw!r}")

    return Decimal(whole) + thirds


def _first_value(blocks, key: str):
    if not isinstance(blocks, list):
        return None
    for item in blocks:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def _extract_stat_map(player_entry) -> dict[str, str]:
    stats: dict[str, str] = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "stat" in obj and isinstance(obj["stat"], dict):
                sid = str(obj["stat"].get("stat_id") or "").strip()
                val = str(obj["stat"].get("value") or "").strip()
                if sid:
                    stats[sid] = val
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(player_entry)
    return stats


def _fetch_yahoo_team_daily_stat_maps(team_key: str, usage_date: date) -> dict[str, dict[str, str]]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/team/"
        f"{team_key}/roster/players/stats;type=date;date={usage_date.isoformat()}?format=json"
    )

    r = requests.get(url, headers=headers, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"Yahoo daily stats request failed status={r.status_code}: {r.text[:1000]}")

    data = r.json()
    team = data.get("fantasy_content", {}).get("team", [])

    roster = None
    for item in team:
        if isinstance(item, dict) and "roster" in item:
            roster = item["roster"]
            break

    if roster is None:
        raise RuntimeError("Yahoo daily stats roster block not found")

    players_obj = None
    for value in roster.values():
        if isinstance(value, dict) and "players" in value:
            players_obj = value["players"]
            break

    if players_obj is None:
        raise RuntimeError("Yahoo daily stats players block not found")

    out: dict[str, dict[str, str]] = {}

    for idx in sorted([k for k in players_obj.keys() if str(k).isdigit()], key=lambda x: int(x)):
        entry = players_obj[idx].get("player")
        if not isinstance(entry, list) or not entry:
            continue

        key = _first_value(entry[0], "player_key")
        if not key:
            continue

        out[str(key)] = _extract_stat_map(entry)

    return out




def _extract_yahoo_roster_player(player_entry) -> dict:
    out = {
        "selected_position": "",
        "full_name": "",
        "yahoo_player_key": "",
        "mlb_team_abbr": "",
        "eligible_positions": [],
        "status": "",
    }

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("player_key"):
                out["yahoo_player_key"] = str(obj["player_key"])

            name = obj.get("name")
            if isinstance(name, dict) and name.get("full"):
                out["full_name"] = str(name["full"])

            if obj.get("editorial_team_abbr"):
                out["mlb_team_abbr"] = str(obj["editorial_team_abbr"])

            if obj.get("status"):
                out["status"] = str(obj["status"])

            if obj.get("display_position") and not out["eligible_positions"]:
                out["eligible_positions"] = [
                    x.strip()
                    for x in str(obj["display_position"]).split(",")
                    if x.strip()
                ]

            raw_eligible = obj.get("eligible_positions")
            if isinstance(raw_eligible, list):
                vals = [
                    str(item["position"])
                    for item in raw_eligible
                    if isinstance(item, dict) and item.get("position")
                ]
                if vals:
                    out["eligible_positions"] = vals

            raw_selected = obj.get("selected_position")
            if isinstance(raw_selected, list):
                for item in raw_selected:
                    if isinstance(item, dict) and item.get("position"):
                        out["selected_position"] = str(item["position"])
            elif isinstance(raw_selected, dict) and raw_selected.get("position"):
                out["selected_position"] = str(raw_selected["position"])

            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(player_entry)
    out["selected_position"] = _norm_slot(out["selected_position"])
    return out


def _fetch_yahoo_dated_roster_slots(team_key: str, usage_date: date, slots: tuple[str, ...]) -> list[dict]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/team/"
        f"{team_key}/roster;date={usage_date.isoformat()}?format=json"
    )

    r = requests.get(url, headers=headers, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"Yahoo dated roster request failed status={r.status_code}: {r.text[:1000]}")

    data = r.json()

    def find_players(obj):
        if isinstance(obj, dict):
            if "players" in obj and isinstance(obj["players"], dict):
                return obj["players"]
            for v in obj.values():
                found = find_players(v)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = find_players(v)
                if found is not None:
                    return found
        return None

    players_obj = find_players(data)
    if players_obj is None:
        raise RuntimeError("Yahoo dated roster players block not found")

    wanted = {_norm_slot(s) for s in slots}
    out = []

    for idx in sorted([k for k in players_obj.keys() if str(k).isdigit()], key=lambda x: int(x)):
        entry = players_obj[idx].get("player")
        if not isinstance(entry, list) or not entry:
            continue

        row = _extract_yahoo_roster_player(entry)
        if row["selected_position"] in wanted:
            out.append(row)

    return out



def _fetch_yahoo_player_daily_stat_map(player_key: str, usage_date: date) -> dict[str, str]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/player/"
        f"{player_key}/stats;type=date;date={usage_date.isoformat()}?format=json"
    )

    r = requests.get(url, headers=headers, timeout=45)
    if r.status_code != 200:
        return {}

    try:
        data = r.json()
    except Exception:
        return {}

    stats: dict[str, str] = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "stat" in obj and isinstance(obj["stat"], dict):
                sid = str(obj["stat"].get("stat_id") or "").strip()
                val = str(obj["stat"].get("value") or "").strip()
                if sid:
                    stats[sid] = val
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return stats

def _load_active_slots(league_key: str, team_key: str, usage_date: date, slots: tuple[str, ...]) -> list[dict]:
    slot_params = tuple(slots)
    sql = """
    SELECT
        selected_position,
        full_name,
        yahoo_player_key,
        mlb_team_abbr,
        eligible_positions,
        status
    FROM lineup_tool.roster_snapshot
    WHERE league_key = %s
      AND team_key = %s
      AND as_of_date = %s
      AND upper(selected_position) = ANY(%s)
    ORDER BY selected_position, full_name
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, team_key, usage_date, list(slot_params)))
            rows = cur.fetchall()

    return [
        {
            "selected_position": row[0],
            "full_name": row[1],
            "yahoo_player_key": row[2],
            "mlb_team_abbr": row[3],
            "eligible_positions": row[4],
            "status": row[5],
        }
        for row in rows
    ]


def _batter_cache_map(player_keys: list[str], usage_date: date) -> dict[str, tuple]:
    if not player_keys:
        return {}

    sql = """
    SELECT
        yahoo_player_key,
        hits,
        ab,
        r,
        hr,
        rbi,
        sb,
        bb,
        k,
        fetch_status
    FROM rmt.yahoo_batter_daily_stat_cache
    WHERE stat_date = %s
      AND yahoo_player_key = ANY(%s)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (usage_date, player_keys))
            rows = cur.fetchall()

    return {row[0]: row[1:] for row in rows}


def _upsert_usage_rows(
    league_key: str,
    team_key: str,
    usage_date: date,
    usage_rows: list[dict],
) -> None:
    sql = """
    INSERT INTO rmt.usual_daily_cap_usage (
        league_key,
        team_key,
        usage_date,
        slot_family,
        used_value,
        source,
        detail_json,
        loaded_at_utc
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
    ON CONFLICT (league_key, team_key, usage_date, slot_family)
    DO UPDATE SET
        used_value = EXCLUDED.used_value,
        source = EXCLUDED.source,
        detail_json = EXCLUDED.detail_json,
        loaded_at_utc = now()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in usage_rows:
                cur.execute(
                    sql,
                    (
                        league_key,
                        team_key,
                        usage_date,
                        row["slot_family"],
                        str(row["used_value"]),
                        row["source"],
                        json.dumps(row["detail_json"], sort_keys=True),
                    ),
                )
        conn.commit()


def main() -> None:
    ctx = get_default_context()
    league_key = str(ctx.get("league_key") or "").strip()
    team_key = str(ctx.get("team_key") or "").strip()
    as_of_date = str(ctx.get("as_of_date") or "").strip()

    if league_key != USUAL_LEAGUE_KEY:
        print(f"SKIP usual daily cap usage: league_key={league_key} is not {USUAL_LEAGUE_KEY}", flush=True)
        return

    if not team_key or not as_of_date:
        raise SystemExit("Missing team_key/as_of_date for usual daily cap usage refresh")

    usage_date = _usage_date_from_context(as_of_date)

    print(
        f"BEGIN usual_daily_cap_usage league_key={league_key} team_key={team_key} "
        f"as_of_date={as_of_date} usage_date={usage_date}",
        flush=True,
    )

    _create_table()

    hitter_rows = _fetch_yahoo_dated_roster_slots(team_key, usage_date, HITTER_SLOTS)
    pitcher_rows = _fetch_yahoo_dated_roster_slots(team_key, usage_date, PITCHER_SLOTS)

    batter_cache = _batter_cache_map(
        [str(r["yahoo_player_key"]) for r in hitter_rows if r.get("yahoo_player_key")],
        usage_date,
    )

    yahoo_stat_maps = _fetch_yahoo_team_daily_stat_maps(team_key, usage_date)

    usage_by_slot = {slot: Decimal("0") for slot in HITTER_SLOTS}
    hitter_detail = []

    direct_player_stat_maps: dict[str, dict[str, str]] = {}

    for r in hitter_rows:
        key = str(r["yahoo_player_key"])
        slot = _norm_slot(r["selected_position"])
        stat_map = yahoo_stat_maps.get(key, {})
        used_from_yahoo = _hitter_game_used_from_yahoo_stat_map(stat_map)
        source = "yahoo_team_roster_daily_stats_hitter"

        if used_from_yahoo is None:
            direct_stat_map = direct_player_stat_maps.get(key)
            if direct_stat_map is None:
                direct_stat_map = _fetch_yahoo_player_daily_stat_map(key, usage_date)
                direct_player_stat_maps[key] = direct_stat_map

            direct_used = _hitter_game_used_from_yahoo_stat_map(direct_stat_map)

            if direct_used is not None:
                stat_map = direct_stat_map
                used = direct_used
                source = "yahoo_player_daily_stats_hitter"
            else:
                cache_row = batter_cache.get(key)
                used = _hitter_game_used_from_cache(cache_row)
                source = "rmt.yahoo_batter_daily_stat_cache_fallback"
        else:
            used = used_from_yahoo

        usage_by_slot[slot] = usage_by_slot.get(slot, Decimal("0")) + Decimal(used)
        hitter_detail.append(
            {
                "slot": slot,
                "player": r["full_name"],
                "yahoo_player_key": key,
                "used": used,
                "source": source,
                "yahoo_stat_map": stat_map,
            }
        )

    pitcher_ip_total = Decimal("0")
    pitcher_detail = []

    for r in pitcher_rows:
        key = str(r["yahoo_player_key"])
        stat_map = yahoo_stat_maps.get(key, {})
        ip_raw = stat_map.get("50", "-")
        ip_decimal = _baseball_ip_to_decimal(ip_raw)
        pitcher_ip_total += ip_decimal

        pitcher_detail.append(
            {
                "slot": _norm_slot(r["selected_position"]),
                "player": r["full_name"],
                "yahoo_player_key": key,
                "ip_raw": ip_raw,
                "ip_decimal": float(ip_decimal),
                "source": "yahoo_team_roster_daily_stats_stat_50",
            }
        )

    usage_rows = []

    for slot in HITTER_SLOTS:
        usage_rows.append(
            {
                "slot_family": slot,
                "used_value": usage_by_slot.get(slot, Decimal("0")),
                "source": "yahoo_dated_roster_x_daily_stats",
                "detail_json": {
                    "players": [d for d in hitter_detail if d["slot"] == slot],
                },
            }
        )

    usage_rows.append(
        {
            "slot_family": "P",
            "used_value": pitcher_ip_total,
            "source": "yahoo_team_roster_daily_stats_stat_50",
            "detail_json": {
                "players": pitcher_detail,
                "ip_note": "Yahoo baseball IP parsed as .1=1/3 and .2=2/3",
            },
        }
    )

    _upsert_usage_rows(league_key, team_key, usage_date, usage_rows)

    print("WROTE usual_daily_cap_usage")
    for row in usage_rows:
        print(f"{row['slot_family']}|{row['used_value']}|{row['source']}")

    print("USUAL_DAILY_CAP_USAGE_REFRESH_OK", flush=True)


if __name__ == "__main__":
    main()
