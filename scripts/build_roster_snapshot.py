import argparse
import csv
import json
from pathlib import Path


def first_value(blocks, key):
    for item in blocks:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def parse_positions(value):
    if not isinstance(value, list):
        return ""
    vals = []
    for item in value:
        if isinstance(item, dict) and "position" in item:
            vals.append(str(item["position"]))
    return "|".join(vals)


def parse_selected_position(selected_block):
    if not isinstance(selected_block, list):
        return ""
    for item in selected_block:
        if isinstance(item, dict) and "position" in item:
            return str(item["position"])
    return ""


def default_out_path(src: Path) -> Path:
    name = src.name
    if name.endswith(".json"):
        name = name[:-5] + "_snapshot.csv"
    else:
        name = name + "_snapshot.csv"
    return src.parent.parent / "derived" / name


def main():
    parser = argparse.ArgumentParser(description="Build roster snapshot CSV from Yahoo roster JSON")
    parser.add_argument("--src", required=True, help="Source Yahoo roster JSON")
    parser.add_argument("--out", required=False, help="Output snapshot CSV")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise FileNotFoundError(f"Missing source JSON: {src}")

    out = Path(args.out) if args.out else default_out_path(src)

    data = json.loads(src.read_text(encoding="utf-8"))
    team = data["fantasy_content"]["team"]
    team_meta = team[0]
    roster = team[1]["roster"]

    team_key = next(x["team_key"] for x in team_meta if isinstance(x, dict) and "team_key" in x)
    team_name = next(x["name"] for x in team_meta if isinstance(x, dict) and "name" in x)
    roster_date = roster["date"]

    players_obj = roster["0"]["players"]
    player_keys = sorted([k for k in players_obj.keys() if str(k).isdigit()], key=int)

    rows = []

    for idx in player_keys:
        player_outer = players_obj[idx]["player"]
        blocks = player_outer[0]
        selected_position = parse_selected_position(player_outer[1].get("selected_position", []))

        keeper_obj = first_value(blocks, "is_keeper") or {}

        row = {
            "roster_date": roster_date,
            "team_key": team_key,
            "team_name": team_name,
            "player_key": first_value(blocks, "player_key") or "",
            "player_id": first_value(blocks, "player_id") or "",
            "full_name": (first_value(blocks, "name") or {}).get("full", ""),
            "editorial_team_abbr": first_value(blocks, "editorial_team_abbr") or "",
            "position_type": first_value(blocks, "position_type") or "",
            "primary_position": first_value(blocks, "primary_position") or "",
            "display_position": first_value(blocks, "display_position") or "",
            "eligible_positions": parse_positions(first_value(blocks, "eligible_positions")),
            "selected_position": selected_position,
            "status": first_value(blocks, "status") or "",
            "status_full": first_value(blocks, "status_full") or "",
            "is_keeper": keeper_obj.get("status", ""),
            "is_undroppable": first_value(blocks, "is_undroppable") or "",
        }
        rows.append(row)

    rows.sort(key=lambda r: (r["selected_position"], r["full_name"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {out}")
    print(f"ROWS {len(rows)}")
    print("")
    print("selected_position | full_name | eligible_positions | mlb | status | status_full")
    for r in rows:
        print(
            f'{r["selected_position"]} | {r["full_name"]} | {r["eligible_positions"]} | '
            f'{r["editorial_team_abbr"]} | {r["status"]} | {r["status_full"]}'
        )


if __name__ == "__main__":
    main()
