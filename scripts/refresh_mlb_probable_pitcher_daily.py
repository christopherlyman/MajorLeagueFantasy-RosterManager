import argparse
from datetime import datetime
import requests

from psycopg.types.json import Jsonb
from services.db import get_connection

TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportId=1"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"


def fetch_team_map():
    resp = requests.get(TEAMS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    out = {}
    for team in data.get("teams", []):
        team_id = team.get("id")
        if team_id is None:
            continue
        out[team_id] = {
            "name": team.get("name", ""),
            "abbreviation": team.get("abbreviation") or str(team.get("teamCode", "")).upper(),
        }
    return out


def fetch_schedule(as_of_date: str):
    resp = requests.get(SCHEDULE_URL.format(date=as_of_date), timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Refresh lineup_tool.mlb_probable_pitcher_daily from MLB Stats API")
    parser.add_argument("--as-of-date", required=True, help="Date in YYYY-MM-DD")
    args = parser.parse_args()

    datetime.strptime(args.as_of_date, "%Y-%m-%d")

    team_map = fetch_team_map()
    sched = fetch_schedule(args.as_of_date)

    rows = []
    for date_block in sched.get("dates", []):
        as_of_date = date_block.get("date")
        for game in date_block.get("games", []):
            away_team = game.get("teams", {}).get("away", {}).get("team", {}) or {}
            home_team = game.get("teams", {}).get("home", {}).get("team", {}) or {}

            away_id = away_team.get("id")
            home_id = home_team.get("id")

            away_meta = team_map.get(away_id, {})
            home_meta = team_map.get(home_id, {})

            away_prob = game.get("teams", {}).get("away", {}).get("probablePitcher") or {}
            home_prob = game.get("teams", {}).get("home", {}).get("probablePitcher") or {}

            raw_obj = {
                "gamePk": game.get("gamePk"),
                "gameDate": game.get("gameDate", ""),
                "teams": {
                    "away": {
                        "team": {
                            "id": away_id,
                            "name": away_meta.get("name", away_team.get("name", "")),
                            "abbreviation": away_meta.get("abbreviation", ""),
                        }
                    },
                    "home": {
                        "team": {
                            "id": home_id,
                            "name": home_meta.get("name", home_team.get("name", "")),
                            "abbreviation": home_meta.get("abbreviation", ""),
                        }
                    },
                },
            }

            rows.append((
                as_of_date,
                game.get("gamePk"),
                away_id,
                away_meta.get("name", away_team.get("name", "")),
                away_prob.get("id"),
                away_prob.get("fullName"),
                home_id,
                home_meta.get("name", home_team.get("name", "")),
                home_prob.get("id"),
                home_prob.get("fullName"),
                Jsonb(raw_obj),
            ))

    if not rows:
        print(f"REFRESHED 0 games for {args.as_of_date}")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lineup_tool.mlb_probable_pitcher_daily WHERE as_of_date = %s",
                (args.as_of_date,),
            )
            cur.executemany(
                """
                INSERT INTO lineup_tool.mlb_probable_pitcher_daily (
                    as_of_date,
                    game_pk,
                    away_team_id,
                    away_team_name,
                    away_probable_pitcher_id,
                    away_probable_pitcher_name,
                    home_team_id,
                    home_team_name,
                    home_probable_pitcher_id,
                    home_probable_pitcher_name,
                    raw_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (as_of_date, game_pk)
                DO UPDATE SET
                    away_team_id = EXCLUDED.away_team_id,
                    away_team_name = EXCLUDED.away_team_name,
                    away_probable_pitcher_id = EXCLUDED.away_probable_pitcher_id,
                    away_probable_pitcher_name = EXCLUDED.away_probable_pitcher_name,
                    home_team_id = EXCLUDED.home_team_id,
                    home_team_name = EXCLUDED.home_team_name,
                    home_probable_pitcher_id = EXCLUDED.home_probable_pitcher_id,
                    home_probable_pitcher_name = EXCLUDED.home_probable_pitcher_name,
                    raw_json = EXCLUDED.raw_json,
                    loaded_at = now()
                """,
                rows,
            )
        conn.commit()

    print(f"REFRESHED {len(rows)} games for {args.as_of_date}")
    for row in rows[:10]:
        print(row[3], "at", row[7], "|", row[5], "|", row[9])


if __name__ == "__main__":
    main()
