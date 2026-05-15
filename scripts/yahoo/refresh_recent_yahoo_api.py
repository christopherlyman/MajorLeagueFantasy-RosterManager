import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, "/app")

from auth import get_access_token
from services.db import get_connection

STAT_ID_HAB = "60"   # Legacy H/AB shape, if Yahoo ever returns it.
STAT_ID_AB = "6"
STAT_ID_H = "8"
STAT_ID_R = "7"
STAT_ID_HR = "12"
STAT_ID_RBI = "13"
STAT_ID_SB = "16"
STAT_ID_BB = "18"
STAT_ID_K = "21"
STAT_ID_AVG = "3"

RAW_DIR = Path(os.environ.get("RMT_RAW_ROOT", "/app/data/raw")) / "yahoo"
DERIVED_DIR = Path(os.environ.get("RMT_DERIVED_ROOT", "/app/data/derived"))


def _safe_avg_num(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _hits_ab_from_stat_map(stat_map: dict) -> tuple[int, int]:
    hits, ab = parse_hab(stat_map.get(STAT_ID_HAB, ""))

    if ab <= 0:
        ab = to_int(stat_map.get(STAT_ID_AB, 0))
    if hits <= 0:
        hits = to_int(stat_map.get(STAT_ID_H, 0))

    return hits, ab


def _stat_map_to_cache_row(stat_map: dict):
    hits, ab = _hits_ab_from_stat_map(stat_map)
    return {
        "hits": hits,
        "ab": ab,
        "r": to_int(stat_map.get(STAT_ID_R, 0)),
        "hr": to_int(stat_map.get(STAT_ID_HR, 0)),
        "rbi": to_int(stat_map.get(STAT_ID_RBI, 0)),
        "sb": to_int(stat_map.get(STAT_ID_SB, 0)),
        "bb": to_int(stat_map.get(STAT_ID_BB, 0)),
        "k": to_int(stat_map.get(STAT_ID_K, 0)),
        "avg": _safe_avg_num(stat_map.get(STAT_ID_AVG, "")),
    }


def _cache_row_to_stat_map(row):
    if row is None:
        return None

    hits, ab, r, hr, rbi, sb, bb, k, avg = row
    hab = f"{hits}/{ab}" if hits or ab else ""
    avg_text = "" if avg is None else f"{float(avg):.3f}"

    return {
        STAT_ID_HAB: hab,
        STAT_ID_AB: str(ab or 0),
        STAT_ID_H: str(hits or 0),
        STAT_ID_R: str(r or 0),
        STAT_ID_HR: str(hr or 0),
        STAT_ID_RBI: str(rbi or 0),
        STAT_ID_SB: str(sb or 0),
        STAT_ID_BB: str(bb or 0),
        STAT_ID_K: str(k or 0),
        STAT_ID_AVG: avg_text,
    }


def _get_cached_daily_stats(player_key: str, stat_date: str):
    sql = """
    SELECT hits, ab, r, hr, rbi, sb, bb, k, avg
    FROM rmt.yahoo_batter_daily_stat_cache
    WHERE yahoo_player_key = %s
      AND stat_date = %s
      AND fetch_status = 'success'
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (player_key, stat_date))
            row = cur.fetchone()
    return _cache_row_to_stat_map(row)


def _put_cached_daily_stats(player_key: str, stat_date: str, stat_map: dict):
    row = _stat_map_to_cache_row(stat_map)
    sql = """
    INSERT INTO rmt.yahoo_batter_daily_stat_cache (
        yahoo_player_key, stat_date, hits, ab, r, hr, rbi, sb, bb, k, avg, fetch_status, response_excerpt
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'success', NULL)
    ON CONFLICT (yahoo_player_key, stat_date)
    DO UPDATE SET
        hits = EXCLUDED.hits,
        ab = EXCLUDED.ab,
        r = EXCLUDED.r,
        hr = EXCLUDED.hr,
        rbi = EXCLUDED.rbi,
        sb = EXCLUDED.sb,
        bb = EXCLUDED.bb,
        k = EXCLUDED.k,
        avg = EXCLUDED.avg,
        fetch_status = EXCLUDED.fetch_status,
        response_excerpt = EXCLUDED.response_excerpt,
        fetched_at_utc = now()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    player_key,
                    stat_date,
                    row["hits"],
                    row["ab"],
                    row["r"],
                    row["hr"],
                    row["rbi"],
                    row["sb"],
                    row["bb"],
                    row["k"],
                    row["avg"],
                ),
            )
        conn.commit()


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item)


def first_value(blocks, key):
    for item in blocks:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def load_current_roster_batters(team_key: str):
    safe_team_key = team_key.replace(".", "_")
    src = RAW_DIR / f"team_{safe_team_key}_roster.json"
    if not src.exists():
        raise FileNotFoundError(f"Missing current roster JSON: {src}")

    data = json.loads(src.read_text(encoding="utf-8"))
    team = data["fantasy_content"]["team"]
    roster = team[1]["roster"]
    players_obj = roster["0"]["players"]
    player_keys = sorted([k for k in players_obj.keys() if str(k).isdigit()], key=int)

    out = []
    for idx in player_keys:
        player_outer = players_obj[idx]["player"]
        blocks = player_outer[0]

        position_type = first_value(blocks, "position_type") or ""
        if position_type != "B":
            continue

        name_obj = first_value(blocks, "name") or {}
        player_key = first_value(blocks, "player_key") or ""
        player_name = name_obj.get("full", "")

        if player_key and player_name:
            out.append({"player_key": player_key, "player_name": player_name})

    return out


def load_batters_from_players_csv(src_path: str):
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Missing players CSV: {src}")

    out = []
    with src.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            player_key = str(row.get("yahoo_player_key") or "").strip()
            player_name = str(row.get("player_name") or "").strip()
            if player_key and player_name:
                out.append({"player_key": player_key, "player_name": player_name})
    return out


def get_player_daily_stats(session: requests.Session, headers: dict, player_key: str, stat_date: str):
    cached = _get_cached_daily_stats(player_key, stat_date)
    if cached is not None:
        return cached

    url = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/"
        f"player/{player_key}/stats;type=date;date={stat_date}?format=json"
    )
    r = session.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:
        body = " ".join((r.text or "").strip().split())[:200]
        print(
            f"WARN recent_non_json player_key={player_key} stat_date={stat_date} "
            f"status_code={r.status_code} body={body}"
        )
        return {}

    player_stats_obj = None
    for node in walk(data):
        if "player_stats" in node:
            player_stats_obj = node["player_stats"]
            break

    if not player_stats_obj:
        return {}

    stats_list = player_stats_obj.get("stats", [])
    stat_map = {}
    for item in stats_list:
        if isinstance(item, dict) and "stat" in item:
            stat = item["stat"]
            stat_id = str(stat.get("stat_id", ""))
            value = stat.get("value", "")
            if stat_id:
                stat_map[stat_id] = value

    _put_cached_daily_stats(player_key, stat_date, stat_map)
    return stat_map


def parse_hab(value: str):
    text = str(value or "").strip()
    if "/" not in text:
        return 0, 0
    left, right = text.split("/", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return 0, 0


def to_int(value):
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def main():
    as_of_date = os.environ["YAHOO_AS_OF_DATE"]
    team_key = str(os.environ.get("YAHOO_TEAM_KEY", "")).strip()
    players_csv = str(os.environ.get("YAHOO_PLAYERS_CSV", "")).strip()
    out_override = str(os.environ.get("YAHOO_RECENT_OUT", "")).strip()

    dt = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    dates = [(dt - timedelta(days=i)).isoformat() for i in range(7)]

    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_override) if out_override else (DERIVED_DIR / f"recent7_hitter_inputs_{as_of_date}.csv")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    if players_csv:
        batters = load_batters_from_players_csv(players_csv)
    else:
        if not team_key:
            raise RuntimeError("YAHOO_TEAM_KEY required when YAHOO_PLAYERS_CSV is not provided")
        batters = load_current_roster_batters(team_key)

    rows = []
    with requests.Session() as session:
        for batter in batters:
            player_key = batter["player_key"]
            player_name = batter["player_name"]

            hits = 0
            ab = 0
            r = 0
            hr = 0
            rbi = 0
            sb = 0
            bb = 0
            k = 0

            for stat_date in dates:
                stat_map = get_player_daily_stats(session, headers, player_key, stat_date)

                d_hits, d_ab = _hits_ab_from_stat_map(stat_map)
                hits += d_hits
                ab += d_ab
                r += to_int(stat_map.get(STAT_ID_R, 0))
                hr += to_int(stat_map.get(STAT_ID_HR, 0))
                rbi += to_int(stat_map.get(STAT_ID_RBI, 0))
                sb += to_int(stat_map.get(STAT_ID_SB, 0))
                bb += to_int(stat_map.get(STAT_ID_BB, 0))
                k += to_int(stat_map.get(STAT_ID_K, 0))

            avg = f"{(hits / ab):.3f}" if ab > 0 else ""

            rows.append(
                {
                    "player_name": player_name,
                    "recent7_hits": hits,
                    "recent7_ab": ab,
                    "recent7_r": r,
                    "recent7_hr": hr,
                    "recent7_rbi": rbi,
                    "recent7_sb": sb,
                    "recent7_bb": bb,
                    "recent7_k": k,
                    "recent7_avg": avg,
                }
            )

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "player_name",
                "recent7_hits",
                "recent7_ab",
                "recent7_r",
                "recent7_hr",
                "recent7_rbi",
                "recent7_sb",
                "recent7_bb",
                "recent7_k",
                "recent7_avg",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {out_path}")
    print(f"ROWS {len(rows)}")
    for row in rows:
        print(
            row["player_name"],
            f'{row["recent7_hits"]}/{row["recent7_ab"]}',
            row["recent7_r"],
            row["recent7_hr"],
            row["recent7_rbi"],
            row["recent7_sb"],
            row["recent7_bb"],
            row["recent7_k"],
            row["recent7_avg"],
            sep=" | "
        )


if __name__ == "__main__":
    main()
