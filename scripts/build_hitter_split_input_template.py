import argparse
import csv
from pathlib import Path

from services.db import get_connection


FIELDNAMES = [
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
    "recent7_ops",
    "recent7_ab",
]


def main():
    parser = argparse.ArgumentParser(description="Build hitter split input template for today's roster hitters")
    parser.add_argument("--league-key", required=True)
    parser.add_argument("--team-key", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    sql = """
    SELECT DISTINCT full_name
    FROM lineup_tool.roster_snapshot
    WHERE as_of_date = %s
      AND league_key = %s
      AND team_key = %s
      AND position_type = 'B'
    ORDER BY full_name;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (args.as_of_date, args.league_key, args.team_key))
            names = [row[0] for row in cur.fetchall()]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for name in names:
            writer.writerow({"player_name": name})

    print(f"WROTE {out}")
    print(f"ROWS {len(names)}")
    for name in names:
        print(name)


if __name__ == "__main__":
    main()
