import argparse
import csv
import json
from pathlib import Path

import requests

TEAM_ABBR_TO_MLB_NAME = {
    "ARI": "Arizona Diamondbacks",
    "AZ": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "ATH": "Athletics",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "OAK": "Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SDP": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
}

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
        editorial_team_abbr = first_value(blocks, "editorial_team_abbr") or ""

        if player_name:
            out.append(
                {
                    "player_name": player_name,
                    "editorial_team_abbr": editorial_team_abbr,
                }
            )
    return out

def normalize_team_name(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())

def resolve_mlbam_id(session: requests.Session, player_name: str, editorial_team_abbr: str):
    url = "https://statsapi.mlb.com/api/v1/people/search"
    r = session.get(url, params={"names": player_name}, timeout=30)
    r.raise_for_status()
    data = r.json()
    people = data.get("people", [])

    exact = [p for p in people if str(p.get("fullName", "")).casefold() == player_name.casefold()]
    candidates = exact if exact else people

    expected_team_name = TEAM_ABBR_TO_MLB_NAME.get(editorial_team_abbr.strip().upper(), "")
    expected_team_norm = normalize_team_name(expected_team_name)

    team_matches = []
    if expected_team_norm:
        for p in candidates:
            current_team_name = normalize_team_name((p.get("currentTeam") or {}).get("name", ""))
            if current_team_name == expected_team_norm:
                team_matches.append(p)

    if team_matches:
        chosen = team_matches[0]
        note = "EXACT_NAME_AND_TEAM" if exact else "TEAM_MATCH_FALLBACK"
    elif candidates:
        chosen = candidates[0]
        note = "EXACT_NAME_ONLY" if exact else "FIRST_RESULT"
    else:
        return "", "", "", "NO_MATCH", 0

    current_team_name = str((chosen.get("currentTeam") or {}).get("name", ""))
    return (
        str(chosen.get("id", "")),
        str(chosen.get("fullName", "")),
        current_team_name,
        note,
        len(people),
    )

def main():
    parser = argparse.ArgumentParser(description="Build MLBAM player-id map for current roster hitters")
    parser.add_argument("--src", required=True, help="Roster JSON source")
    parser.add_argument("--out", required=True, help="Output CSV path")
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)

    batters = load_current_roster_batters(src)

    rows = []
    with requests.Session() as session:
        for batter in batters:
            mlbam_id, matched_name, matched_team_name, match_note, candidate_count = resolve_mlbam_id(
                session, batter["player_name"], batter["editorial_team_abbr"]
            )
            rows.append(
                {
                    "player_name": batter["player_name"],
                    "editorial_team_abbr": batter["editorial_team_abbr"],
                    "mlbam_id": mlbam_id,
                    "matched_name": matched_name,
                    "matched_team_name": matched_team_name,
                    "match_note": match_note,
                    "candidate_count": candidate_count,
                }
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "player_name",
                "editorial_team_abbr",
                "mlbam_id",
                "matched_name",
                "matched_team_name",
                "match_note",
                "candidate_count",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {out}")
    print(f"ROWS {len(rows)}")
    for row in rows:
        print(
            row["player_name"],
            row["editorial_team_abbr"],
            row["mlbam_id"],
            row["matched_name"],
            row["matched_team_name"],
            row["match_note"],
            row["candidate_count"],
            sep=" | ",
        )

if __name__ == "__main__":
    main()
