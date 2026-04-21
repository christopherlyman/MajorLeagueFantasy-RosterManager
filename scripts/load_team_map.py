import argparse
import json
import os
from pathlib import Path

import psycopg


def extract_from_blocks(team_blocks, key: str):
    for block in team_blocks:
        if isinstance(block, dict) and key in block:
            return block[key]
    return None


def extract_owner(team_blocks):
    managers_block = extract_from_blocks(team_blocks, "managers")
    if not isinstance(managers_block, list):
        return (None, None)

    managers = []
    for item in managers_block:
        if isinstance(item, dict) and "manager" in item and isinstance(item["manager"], dict):
            managers.append(item["manager"])

    if not managers:
        return (None, None)

    chosen = managers[0]
    return (chosen.get("nickname"), chosen.get("guid"))


def main():
    parser = argparse.ArgumentParser(description="Load Yahoo league teams JSON into lineup_tool.team_map")
    parser.add_argument("--src", required=True, help="Path to Yahoo league teams JSON file.")
    parser.add_argument(
        "--league-key",
        required=False,
        help="Optional explicit league key. If omitted, derive from JSON.",
    )
    parser.add_argument(
        "--season-year",
        type=int,
        required=False,
        help="Optional explicit season year. If omitted, derive from JSON season.",
    )
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")

    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("Missing POSTGRES_DSN / MLF_POSTGRES_DSN")

    payload = json.loads(src.read_text(encoding="utf-8"))
    league = payload["fantasy_content"]["league"]
    meta = league[0]
    teams_container = league[1]["teams"]

    league_key = args.league_key or meta["league_key"]
    season_year = args.season_year or int(meta["season"])
    expected = int(meta["num_teams"])

    rows = []
    for _, team_obj in teams_container.items():
        if not isinstance(team_obj, dict) or "team" not in team_obj:
            continue

        team_outer = team_obj["team"]
        if not isinstance(team_outer, list) or not team_outer:
            continue

        team_blocks = team_outer[0]
        if not isinstance(team_blocks, list):
            continue

        team_key = extract_from_blocks(team_blocks, "team_key")
        team_id_raw = extract_from_blocks(team_blocks, "team_id")
        team_name = extract_from_blocks(team_blocks, "name")
        manager_name, owner_guid = extract_owner(team_blocks)

        if not team_key or not team_name:
            continue

        team_id = int(team_id_raw) if team_id_raw is not None and str(team_id_raw).strip() != "" else None

        rows.append((
            league_key,
            season_year,
            team_key,
            team_id,
            team_name,
            manager_name,
            owner_guid,
        ))

    if len(rows) != expected:
        raise RuntimeError(f"Expected {expected} teams, extracted {len(rows)}")

    sql = """
    INSERT INTO lineup_tool.team_map (
        league_key, season_year, team_key, team_id, team_name, manager_name, owner_guid
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (league_key, season_year, team_key)
    DO UPDATE SET
        team_id = EXCLUDED.team_id,
        team_name = EXCLUDED.team_name,
        manager_name = EXCLUDED.manager_name,
        owner_guid = EXCLUDED.owner_guid,
        loaded_at = now();
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)

    print(f"UPSERTED {len(rows)} team_map rows for {league_key} season {season_year}")


if __name__ == "__main__":
    main()
