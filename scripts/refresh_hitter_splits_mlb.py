import argparse
import csv
import json
from pathlib import Path

import requests

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"

SPLIT_CODES = {
    "vs_rhp": "vr",
    "vs_lhp": "vl",
    "home": "h",
    "away": "a",
    "day": "d",
    "night": "n",
}


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


def load_current_roster_batters(src: Path):
    if not src.exists():
        raise FileNotFoundError(f"Missing roster JSON: {src}")

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
        player_name = name_obj.get("full", "")
        if player_name:
            out.append({"player_name": player_name})
    return out


def load_mlbam_map(src: Path):
    if not src.exists():
        raise FileNotFoundError(f"Missing MLBAM map CSV: {src}")

    out = {}
    with src.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("player_name") or "").strip()
            if name:
                out[name] = row
    return out


def to_int(value):
    try:
        return int(str(value or "0").replace(",", "").strip())
    except ValueError:
        return 0


def extract_first_split_stat(data: dict) -> dict:
    for node in walk(data):
        if isinstance(node, dict) and "splits" in node and isinstance(node["splits"], list):
            splits = node["splits"]
            if not splits:
                return {}
            first = splits[0]
            if isinstance(first, dict):
                return first.get("stat", {}) or {}
    return {}


def fetch_split_stat(session: requests.Session, mlbam_id: str, season: int, sit_code: str) -> dict:
    url = (
        f"{MLB_STATS_API}/people/{mlbam_id}/stats"
        f"?stats=statSplits&group=hitting&season={season}&sitCodes={sit_code}"
    )
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return extract_first_split_stat(r.json())


def stat_row_from_api(stat: dict) -> dict:
    ab = to_int(stat.get("atBats"))
    hits = to_int(stat.get("hits"))
    doubles = to_int(stat.get("doubles"))
    triples = to_int(stat.get("triples"))
    hr = to_int(stat.get("homeRuns"))
    bb = to_int(stat.get("baseOnBalls"))
    hbp = to_int(stat.get("hitByPitch"))
    sf = to_int(stat.get("sacFlies"))

    return {
        "ab": ab,
        "hits": hits,
        "doubles": doubles,
        "triples": triples,
        "home_runs": hr,
        "bb": bb,
        "hbp": hbp,
        "sf": sf,
    }


def zero_totals():
    return {
        "ab": 0,
        "hits": 0,
        "doubles": 0,
        "triples": 0,
        "home_runs": 0,
        "bb": 0,
        "hbp": 0,
        "sf": 0,
    }


def add_totals(a: dict, b: dict):
    return {k: a.get(k, 0) + b.get(k, 0) for k in zero_totals().keys()}


def ops_from_totals(t: dict) -> str:
    ab = t["ab"]
    hits = t["hits"]
    doubles = t["doubles"]
    triples = t["triples"]
    hr = t["home_runs"]
    bb = t["bb"]
    hbp = t["hbp"]
    sf = t["sf"]

    if ab <= 0:
        return ""

    total_bases = hits + doubles + (2 * triples) + (3 * hr)
    slg = total_bases / ab

    obp_denom = ab + bb + hbp + sf
    obp = (hits + bb + hbp) / obp_denom if obp_denom > 0 else 0.0

    return f"{(obp + slg):.3f}"


def derive_overall_ops(vs_lhp: dict, vs_rhp: dict) -> str:
    combo = add_totals(vs_lhp, vs_rhp)
    return ops_from_totals(combo)


def main():
    parser = argparse.ArgumentParser(description="Build hitter split inputs from MLB Stats API statSplits")
    parser.add_argument("--src", required=True, help="Roster JSON source")
    parser.add_argument("--map", required=True, help="MLBAM player map CSV")
    parser.add_argument("--season-start", type=int, required=True, help="First season to include")
    parser.add_argument("--season-end", type=int, required=True, help="Last season to include")
    parser.add_argument("--out", required=True, help="Output CSV path")
    args = parser.parse_args()

    src = Path(args.src)
    map_path = Path(args.map)
    out = Path(args.out)

    batters = load_current_roster_batters(src)
    mlbam_map = load_mlbam_map(map_path)

    rows = []
    with requests.Session() as session:
        for batter in batters:
            name = batter["player_name"]
            mapped = mlbam_map.get(name, {})
            mlbam_id = str(mapped.get("mlbam_id", "")).strip()

            out_row = {
                "player_name": name,
                "overall_ops": "",
                "vs_rhp_ops": "",
                "vs_rhp_ab": "",
                "vs_lhp_ops": "",
                "vs_lhp_ab": "",
                "home_ops": "",
                "home_ab": "",
                "away_ops": "",
                "away_ab": "",
                "day_ops": "",
                "day_ab": "",
                "night_ops": "",
                "night_ab": "",
            }

            if not mlbam_id:
                rows.append(out_row)
                continue

            combined = {key: zero_totals() for key in SPLIT_CODES.keys()}

            for season in range(args.season_start, args.season_end + 1):
                for split_key, sit_code in SPLIT_CODES.items():
                    stat = fetch_split_stat(session, mlbam_id, season, sit_code)
                    combined[split_key] = add_totals(combined[split_key], stat_row_from_api(stat))

            out_row["vs_rhp_ops"] = ops_from_totals(combined["vs_rhp"])
            out_row["vs_rhp_ab"] = combined["vs_rhp"]["ab"]
            out_row["vs_lhp_ops"] = ops_from_totals(combined["vs_lhp"])
            out_row["vs_lhp_ab"] = combined["vs_lhp"]["ab"]
            out_row["home_ops"] = ops_from_totals(combined["home"])
            out_row["home_ab"] = combined["home"]["ab"]
            out_row["away_ops"] = ops_from_totals(combined["away"])
            out_row["away_ab"] = combined["away"]["ab"]
            out_row["day_ops"] = ops_from_totals(combined["day"])
            out_row["day_ab"] = combined["day"]["ab"]
            out_row["night_ops"] = ops_from_totals(combined["night"])
            out_row["night_ab"] = combined["night"]["ab"]
            out_row["overall_ops"] = derive_overall_ops(combined["vs_lhp"], combined["vs_rhp"])

            rows.append(out_row)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "player_name",
                "overall_ops",
                "vs_rhp_ops",
                "vs_rhp_ab",
                "vs_lhp_ops",
                "vs_lhp_ab",
                "home_ops",
                "home_ab",
                "away_ops",
                "away_ab",
                "day_ops",
                "day_ab",
                "night_ops",
                "night_ab",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {out}")
    print(f"ROWS {len(rows)}")
    for row in rows:
        print(
            row["player_name"],
            row["vs_rhp_ops"],
            row["vs_rhp_ab"],
            row["vs_lhp_ops"],
            row["vs_lhp_ab"],
            row["home_ops"],
            row["home_ab"],
            row["away_ops"],
            row["away_ab"],
            row["day_ops"],
            row["day_ab"],
            row["night_ops"],
            row["night_ab"],
            sep=" | ",
        )


if __name__ == "__main__":
    main()
