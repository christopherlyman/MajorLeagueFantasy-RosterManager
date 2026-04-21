import argparse
import csv
from pathlib import Path

import requests

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"


def fetch_json(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def normalize_batting_order(value) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text) // 100 if len(text) >= 3 else int(text)
    except Exception:
        return 0


def extract_team_lineup(team_box: dict) -> list[dict]:
    players = team_box.get("players", {}) or {}
    rows = []

    for player_obj in players.values():
        order_num = normalize_batting_order(player_obj.get("battingOrder"))
        if order_num <= 0:
            continue

        person = player_obj.get("person", {}) or {}
        position = player_obj.get("position", {}) or {}
        rows.append(
            {
                "player_name": person.get("fullName", ""),
                "player_id": person.get("id", ""),
                "bats": "",
                "lineup_position": position.get("abbreviation", ""),
                "batting_order": order_num,
            }
        )

    rows.sort(key=lambda r: (r["batting_order"], r["player_name"]))
    return rows


def team_abbr_from_box(team_box: dict, fallback: str = "") -> str:
    team = (team_box.get("team") or {})
    return str(team.get("abbreviation") or fallback or "").strip().upper()


def main():
    parser = argparse.ArgumentParser(description="Refresh starting lineups from MLB game boxscore feeds")
    parser.add_argument("--as-of-date", required=True, help="Date in YYYY-MM-DD")
    args = parser.parse_args()

    as_of_date = args.as_of_date

    raw_dir = Path("/app/data/raw/mlb")
    derived_dir = Path("/app/data/derived")
    raw_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)

    out_players = derived_dir / f"starting_lineup_players_{as_of_date}.csv"
    out_teams = derived_dir / f"starting_lineup_teams_{as_of_date}.csv"

    schedule = fetch_json(
        f"{MLB_STATS_API}/schedule",
        params={"sportId": 1, "date": as_of_date},
    )

    game_rows = []
    for date_block in schedule.get("dates", []):
        for game in date_block.get("games", []):
            game_rows.append(game)

    team_rows: list[dict] = []
    player_rows: list[dict] = []

    for game in game_rows:
        game_pk = game.get("gamePk")
        if not game_pk:
            continue

        sched_away = (((game.get("teams") or {}).get("away") or {}).get("team") or {})
        sched_home = (((game.get("teams") or {}).get("home") or {}).get("team") or {})

        box = fetch_json(f"{MLB_STATS_API}/game/{game_pk}/boxscore")
        teams = box.get("teams", {}) or {}

        away_box = teams.get("away", {}) or {}
        home_box = teams.get("home", {}) or {}

        away_abbr = team_abbr_from_box(away_box, sched_away.get("abbreviation", ""))
        home_abbr = team_abbr_from_box(home_box, sched_home.get("abbreviation", ""))

        for side, team_box, team_abbr, opp_abbr in [
            ("away", away_box, away_abbr, home_abbr),
            ("home", home_box, home_abbr, away_abbr),
        ]:
            lineup = extract_team_lineup(team_box)
            lineup_posted = len(lineup) >= 9

            team_rows.append(
                {
                    "as_of_date": as_of_date,
                    "game_pk": game_pk,
                    "team_abbr": team_abbr,
                    "opponent_abbr": opp_abbr,
                    "home_away": "AWAY" if side == "away" else "HOME",
                    "lineup_posted": "Y" if lineup_posted else "N",
                    "starter_count": len(lineup),
                    "source": "mlb_boxscore_api",
                }
            )

            for row in lineup:
                player_rows.append(
                    {
                        "as_of_date": as_of_date,
                        "game_pk": game_pk,
                        "team_abbr": team_abbr,
                        "opponent_abbr": opp_abbr,
                        "home_away": "AWAY" if side == "away" else "HOME",
                        "player_name": row["player_name"],
                        "player_id": row["player_id"],
                        "bats": row["bats"],
                        "lineup_position": row["lineup_position"],
                        "batting_order": row["batting_order"],
                        "source": "mlb_boxscore_api",
                    }
                )

    with out_players.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "as_of_date",
                "game_pk",
                "team_abbr",
                "opponent_abbr",
                "home_away",
                "player_name",
                "player_id",
                "bats",
                "lineup_position",
                "batting_order",
                "source",
            ],
        )
        writer.writeheader()
        writer.writerows(player_rows)

    with out_teams.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "as_of_date",
                "game_pk",
                "team_abbr",
                "opponent_abbr",
                "home_away",
                "lineup_posted",
                "starter_count",
                "source",
            ],
        )
        writer.writeheader()
        writer.writerows(team_rows)

    raw_marker = raw_dir / f"starting_lineups_{as_of_date}.txt"
    raw_marker.write_text(
        f"games={len(game_rows)}\nteam_rows={len(team_rows)}\nplayer_rows={len(player_rows)}\n",
        encoding="utf-8",
    )

    print(f"WROTE_RAW {raw_marker}")
    print(f"WROTE_PARSED {out_players}")
    print(f"WROTE_TEAMS {out_teams}")
    print(f"GAMES {len(game_rows)}")
    print(f"TEAM_ROWS {len(team_rows)}")
    print(f"ROWS {len(player_rows)}")
    for row in player_rows[:20]:
        print(
            row["team_abbr"],
            row["player_name"],
            row["lineup_position"],
            row["batting_order"],
            sep=" | ",
        )


if __name__ == "__main__":
    main()
