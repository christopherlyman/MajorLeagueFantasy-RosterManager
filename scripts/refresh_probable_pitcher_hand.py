import argparse
import csv
import requests
from pathlib import Path

from services.db import get_connection

PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}"


def fetch_pitch_hand(person_id: int):
    resp = requests.get(PEOPLE_URL.format(person_id=person_id), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    people = data.get("people", [])
    if not people:
        return "", ""
    hand = people[0].get("pitchHand") or {}
    return hand.get("code", ""), hand.get("description", "")


def main():
    parser = argparse.ArgumentParser(description="Refresh opposing probable pitcher hand file")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    sql = """
    WITH p AS (
        SELECT away_probable_pitcher_id AS mlb_person_id, away_probable_pitcher_name AS pitcher_name
        FROM lineup_tool.mlb_probable_pitcher_daily
        WHERE as_of_date = %s
          AND away_probable_pitcher_id IS NOT NULL
          AND away_probable_pitcher_name IS NOT NULL

        UNION

        SELECT home_probable_pitcher_id AS mlb_person_id, home_probable_pitcher_name AS pitcher_name
        FROM lineup_tool.mlb_probable_pitcher_daily
        WHERE as_of_date = %s
          AND home_probable_pitcher_id IS NOT NULL
          AND home_probable_pitcher_name IS NOT NULL
    )
    SELECT mlb_person_id, pitcher_name
    FROM p
    ORDER BY pitcher_name;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (args.as_of_date, args.as_of_date))
            rows = cur.fetchall()

    out_rows = []
    for person_id, pitcher_name in rows:
        throws, throws_description = fetch_pitch_hand(person_id)
        out_rows.append(
            {
                "pitcher_name": pitcher_name,
                "mlb_person_id": person_id,
                "throws": throws,
                "throws_description": throws_description,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["pitcher_name", "mlb_person_id", "throws", "throws_description"],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"WROTE {out}")
    print(f"ROWS {len(out_rows)}")
    for row in out_rows:
        print(row["pitcher_name"], row["throws"], row["throws_description"], sep=" | ")


if __name__ == "__main__":
    main()
