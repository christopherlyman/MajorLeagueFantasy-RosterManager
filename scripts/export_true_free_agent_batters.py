import argparse
import csv
from pathlib import Path

from services.db import get_connection


def main():
    parser = argparse.ArgumentParser(description="Export true free-agent batters for a league/date")
    parser.add_argument("--league-key", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = Path(args.out)

    sql = '''
    SELECT
        p.full_name AS player_name,
        p.editorial_team_abbr
    FROM public.yahoo_league_player_pool p
    LEFT JOIN lineup_tool.roster_snapshot r
      ON r.league_key = p.league_key
     AND r.as_of_date = %s
     AND r.yahoo_player_key = p.yahoo_player_key
    WHERE p.league_key = %s
      AND p.season_year = %s
      AND r.yahoo_player_key IS NULL
      AND NOT (
        COALESCE(p.eligible_positions, '[]'::jsonb) ? 'P'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'SP'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'RP'
      )
    ORDER BY
      COALESCE(p.rank_value, 999999),
      COALESCE(p.percent_owned, -1) DESC,
      p.full_name
    '''

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (args.as_of_date, args.league_key, int(args.as_of_date[:4])))
            rows = cur.fetchall()

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_name", "editorial_team_abbr"])
        writer.writeheader()
        for player_name, editorial_team_abbr in rows:
            writer.writerow(
                {
                    "player_name": player_name,
                    "editorial_team_abbr": editorial_team_abbr or "",
                }
            )

    print(f"WROTE {out}")
    print(f"ROWS {len(rows)}")
    for row in rows[:20]:
        print(f"{row[0]} | {row[1]}")


if __name__ == "__main__":
    main()
