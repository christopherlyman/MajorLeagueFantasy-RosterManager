import argparse
import csv
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from services.db import get_connection

TEAM_KEY_RE = re.compile(r"\.t\.\d+$")


def parse_bool(value):
    text = str(value or "").strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n", ""}:
            return False
    return False


def parse_positions(value):
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def derive_league_key(team_key: str) -> str:
    return TEAM_KEY_RE.sub("", team_key)


def main():
    parser = argparse.ArgumentParser(description="Load roster snapshot CSV into lineup_tool.roster_snapshot")
    parser.add_argument("--src", required=True, help="Path to roster snapshot CSV")
    parser.add_argument("--source-system", default="yahoo", help="Source system label")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise FileNotFoundError(f"Missing source CSV: {src}")

    with src.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("Roster snapshot CSV is empty")

    roster_dates = {r["roster_date"] for r in rows}
    team_keys = {r["team_key"] for r in rows}

    if len(roster_dates) != 1 or len(team_keys) != 1:
        raise RuntimeError(
            f"Expected one roster_date and one team_key, got roster_dates={roster_dates}, team_keys={team_keys}"
        )

    as_of_date = next(iter(roster_dates))
    team_key = next(iter(team_keys))
    league_key = derive_league_key(team_key)
    season_year = datetime.strptime(as_of_date, "%Y-%m-%d").year

    insert_rows = []
    for r in rows:
        insert_rows.append((
            as_of_date,
            league_key,
            season_year,
            r["team_key"],
            r["player_key"],
            int(r["player_id"]) if str(r["player_id"]).strip() else None,
            r["full_name"],
            r["editorial_team_abbr"],
            r["position_type"],
            r["primary_position"],
            r["display_position"],
            parse_positions(r["eligible_positions"]),
            r["selected_position"],
            r["status"],
            r["status_full"],
            parse_bool(r["is_keeper"]),
            parse_bool(r["is_undroppable"]),
            args.source_system,
        ))

    delete_sql = """
    DELETE FROM lineup_tool.roster_snapshot
    WHERE as_of_date = %s
      AND league_key = %s
      AND team_key = %s
    """

    insert_sql = """
    INSERT INTO lineup_tool.roster_snapshot (
        as_of_date,
        league_key,
        season_year,
        team_key,
        yahoo_player_key,
        yahoo_player_id,
        full_name,
        mlb_team_abbr,
        position_type,
        primary_position,
        display_position,
        eligible_positions,
        selected_position,
        status,
        status_full,
        is_keeper,
        is_undroppable,
        source_system
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(delete_sql, (as_of_date, league_key, team_key))
            cur.executemany(insert_sql, insert_rows)
        conn.commit()

    slot_counts = Counter(r["selected_position"] for r in rows)

    print(f"LOADED {len(rows)} roster rows")
    print(f"DATE {as_of_date}")
    print(f"LEAGUE {league_key}")
    print(f"TEAM {team_key}")
    print("SLOT_COUNTS")
    for slot, ct in sorted(slot_counts.items()):
        print(f"{slot} | {ct}")


if __name__ == "__main__":
    main()
