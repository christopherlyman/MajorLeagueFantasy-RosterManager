import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

from auth import get_access_token

STAT_ID_HAB = "60"   # H/AB, e.g. "1/4"
STAT_ID_R = "7"
STAT_ID_HR = "12"
STAT_ID_RBI = "13"
STAT_ID_SB = "16"
STAT_ID_K = "21"
STAT_ID_AVG = "3"

RAW_DIR = Path("data/raw/yahoo")
DERIVED_DIR = Path("data/derived")


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
    url = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/"
        f"player/{player_key}/stats;type=date;date={stat_date}?format=json"
    )
    r = session.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

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
            k = 0

            for stat_date in dates:
                stat_map = get_player_daily_stats(session, headers, player_key, stat_date)

                d_hits, d_ab = parse_hab(stat_map.get(STAT_ID_HAB, ""))
                hits += d_hits
                ab += d_ab
                r += to_int(stat_map.get(STAT_ID_R, 0))
                hr += to_int(stat_map.get(STAT_ID_HR, 0))
                rbi += to_int(stat_map.get(STAT_ID_RBI, 0))
                sb += to_int(stat_map.get(STAT_ID_SB, 0))
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
            row["recent7_k"],
            row["recent7_avg"],
            sep=" | "
        )


if __name__ == "__main__":
    main()
