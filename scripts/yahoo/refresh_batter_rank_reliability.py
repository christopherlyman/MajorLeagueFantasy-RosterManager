#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from auth import get_access_token  # noqa: E402
from services.db import get_connection  # noqa: E402

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
SORT_TYPES = ("season", "lastmonth", "last14", "last7")

RANK_HALF_LIFE = 75.0
MAX_RELIABILITY_POINTS = 8.0
SEASON_WEIGHT = 0.45
LASTMONTH_WEIGHT = 0.35
LAST14_WEIGHT = 0.20
NOISE_FLOOR = 2.0
ROLLING_SUPPRESS_RANK = 200
LAST7_WARNING_RANK = 300


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-key", default=os.environ.get("YAHOO_LEAGUE_KEY"))
    parser.add_argument("--season-year", type=int, default=int(os.environ.get("YAHOO_STATS_SEASON", "2026")))
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--depth", type=int, default=int(os.environ.get("YAHOO_AR_RANK_DEPTH", "1000")))
    parser.add_argument("--sleep-seconds", type=float, default=float(os.environ.get("YAHOO_SLEEP_SECONDS", "0.25")))
    parser.add_argument("--targets", default="Juan Soto,Gunnar Henderson,Ryan Kreidler,Paul Goldschmidt")
    args = parser.parse_args()

    if not args.league_key:
        raise SystemExit("Missing --league-key or YAHOO_LEAGUE_KEY")

    if not args.as_of_date:
        args.as_of_date = str(datetime.now(ZoneInfo("America/New_York")).date())

    return args


def find_first_value(obj: Any, target_key: str):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == target_key:
                return value
            found = find_first_value(value, target_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_first_value(value, target_key)
            if found is not None:
                return found
    return None


def extract_players(payload: dict[str, Any], start: int) -> list[dict[str, Any]]:
    players_obj = find_first_value(payload, "players")
    if not isinstance(players_obj, dict):
        return []

    numeric_keys = sorted(int(k) for k in players_obj.keys() if str(k).isdigit())
    out = []

    for offset, numeric_key in enumerate(numeric_keys, 1):
        item = players_obj.get(str(numeric_key))
        if not isinstance(item, dict) or "player" not in item:
            continue

        player = item["player"]
        blocks = player[0] if isinstance(player, list) and player and isinstance(player[0], list) else player

        player_key = find_first_value(blocks, "player_key")
        if not player_key:
            continue

        out.append({
            "yahoo_player_key": str(player_key),
            "full_name": find_first_value(blocks, "full"),
            "editorial_team_abbr": find_first_value(blocks, "editorial_team_abbr"),
            "display_position": find_first_value(blocks, "display_position"),
            "position_type": find_first_value(blocks, "position_type"),
            "actual_rank": start + offset,
        })

    return out


def fetch_rank_window(league_key: str, sort_type: str, token: str, depth: int, sleep_seconds: float):
    headers = {"Authorization": f"Bearer {token}"}
    by_key = {}

    for start in range(0, depth, 25):
        url = (
            f"{YAHOO_FANTASY_BASE}/league/{league_key}/players;"
            f"sort=AR;sort_type={sort_type};start={start};count=25"
            f"?format=json"
        )
        response = requests.get(url, headers=headers, timeout=45)

        if response.status_code != 200:
            raise RuntimeError(
                f"Yahoo AR fetch failed sort_type={sort_type} start={start} "
                f"status={response.status_code} body={response.text[:500]}"
            )

        players = extract_players(response.json(), start)
        if not players:
            break

        for player in players:
            by_key[player["yahoo_player_key"]] = player

        if len(players) < 25:
            break

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return by_key


def rank_strength(rank):
    if rank is None or int(rank) <= 0:
        return 0.0
    return 2.0 ** (-(float(rank) - 1.0) / RANK_HALF_LIFE)


def reliability_label(points: float) -> str:
    if points >= 5.0:
        return "Anchor Start"
    if points >= 3.5:
        return "High Start"
    if points >= 2.0:
        return "Lean Start"
    return "No reliability bump"


def compute_reliability(season_ar, lastmonth_ar, last14_ar, last7_ar):
    raw = MAX_RELIABILITY_POINTS * (
        SEASON_WEIGHT * rank_strength(season_ar)
        + LASTMONTH_WEIGHT * rank_strength(lastmonth_ar)
        + LAST14_WEIGHT * rank_strength(last14_ar)
    )

    suppress = (
        lastmonth_ar is not None
        and last14_ar is not None
        and int(lastmonth_ar) > ROLLING_SUPPRESS_RANK
        and int(last14_ar) > ROLLING_SUPPRESS_RANK
    )

    if raw < NOISE_FLOOR or suppress:
        points = 0.0
    else:
        points = round(raw, 2)

    notes = [
        f"raw={raw:.2f}",
        f"season_ar={season_ar}",
        f"lastmonth_ar={lastmonth_ar}",
        f"last14_ar={last14_ar}",
        f"last7_ar={last7_ar}",
    ]

    if suppress:
        notes.append("suppressed=lastmonth_and_last14_over_200")
    elif raw < NOISE_FLOOR:
        notes.append("suppressed=below_noise_floor")

    if last7_ar is not None and int(last7_ar) > LAST7_WARNING_RANK and points > 0:
        notes.append("warning=last7_slump")

    return points, reliability_label(points), "; ".join(notes)


def ensure_table():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.yahoo_batter_rank_reliability (
                    league_key text NOT NULL,
                    season_year integer NOT NULL,
                    as_of_date date NOT NULL,
                    yahoo_player_key text NOT NULL,
                    full_name text NULL,
                    editorial_team_abbr text NULL,
                    display_position text NULL,
                    position_type text NULL,
                    season_ar integer NULL,
                    lastmonth_ar integer NULL,
                    last14_ar integer NULL,
                    last7_ar integer NULL,
                    rank_reliability_points numeric NOT NULL DEFAULT 0,
                    reliability_label text NOT NULL DEFAULT 'No reliability bump',
                    reliability_reason text NULL,
                    fetched_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (league_key, season_year, as_of_date, yahoo_player_key)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_yahoo_batter_rank_reliability_lookup
                ON public.yahoo_batter_rank_reliability
                (league_key, season_year, as_of_date, yahoo_player_key)
            """)
        conn.commit()


def upsert_rows(league_key, season_year, as_of_date, rows):
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                params = dict(row)
                params.update({
                    "league_key": league_key,
                    "season_year": season_year,
                    "as_of_date": as_of_date,
                })
                cur.execute("""
                    INSERT INTO public.yahoo_batter_rank_reliability (
                        league_key,
                        season_year,
                        as_of_date,
                        yahoo_player_key,
                        full_name,
                        editorial_team_abbr,
                        display_position,
                        position_type,
                        season_ar,
                        lastmonth_ar,
                        last14_ar,
                        last7_ar,
                        rank_reliability_points,
                        reliability_label,
                        reliability_reason,
                        fetched_at
                    )
                    VALUES (
                        %(league_key)s,
                        %(season_year)s,
                        %(as_of_date)s,
                        %(yahoo_player_key)s,
                        %(full_name)s,
                        %(editorial_team_abbr)s,
                        %(display_position)s,
                        %(position_type)s,
                        %(season_ar)s,
                        %(lastmonth_ar)s,
                        %(last14_ar)s,
                        %(last7_ar)s,
                        %(rank_reliability_points)s,
                        %(reliability_label)s,
                        %(reliability_reason)s,
                        now()
                    )
                    ON CONFLICT (league_key, season_year, as_of_date, yahoo_player_key)
                    DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        editorial_team_abbr = EXCLUDED.editorial_team_abbr,
                        display_position = EXCLUDED.display_position,
                        position_type = EXCLUDED.position_type,
                        season_ar = EXCLUDED.season_ar,
                        lastmonth_ar = EXCLUDED.lastmonth_ar,
                        last14_ar = EXCLUDED.last14_ar,
                        last7_ar = EXCLUDED.last7_ar,
                        rank_reliability_points = EXCLUDED.rank_reliability_points,
                        reliability_label = EXCLUDED.reliability_label,
                        reliability_reason = EXCLUDED.reliability_reason,
                        fetched_at = now()
                """, params)
        conn.commit()


def print_targets(league_key, season_year, as_of_date, targets):
    with get_connection() as conn:
        with conn.cursor() as cur:
            for target in targets:
                cur.execute("""
                    SELECT
                        full_name,
                        yahoo_player_key,
                        editorial_team_abbr,
                        display_position,
                        season_ar,
                        lastmonth_ar,
                        last14_ar,
                        last7_ar,
                        rank_reliability_points,
                        reliability_label,
                        reliability_reason
                    FROM public.yahoo_batter_rank_reliability
                    WHERE league_key = %s
                      AND season_year = %s
                      AND as_of_date = %s
                      AND full_name ILIKE %s
                    ORDER BY full_name
                    LIMIT 10
                """, (league_key, season_year, as_of_date, f"%{target}%"))
                rows = cur.fetchall()
                print()
                print(f"TARGET|{target}|ROWS|{len(rows)}")
                for row in rows:
                    print("|".join("" if value is None else str(value) for value in row))


def main():
    args = parse_args()
    as_of_date = date.fromisoformat(args.as_of_date)

    ensure_table()
    token = get_access_token()

    rank_windows = {}
    for sort_type in SORT_TYPES:
        print(f"FETCH_START|sort_type={sort_type}|depth={args.depth}", flush=True)
        rank_windows[sort_type] = fetch_rank_window(
            league_key=args.league_key,
            sort_type=sort_type,
            token=token,
            depth=args.depth,
            sleep_seconds=args.sleep_seconds,
        )
        print(f"FETCH_DONE|sort_type={sort_type}|rows={len(rank_windows[sort_type])}", flush=True)

    all_keys = set()
    for rows in rank_windows.values():
        all_keys.update(rows.keys())

    output_rows = []

    for player_key in sorted(all_keys):
        metadata = None
        for sort_type in SORT_TYPES:
            metadata = rank_windows[sort_type].get(player_key)
            if metadata:
                break

        if not metadata or metadata.get("position_type") != "B":
            continue

        season_ar = rank_windows["season"].get(player_key, {}).get("actual_rank")
        lastmonth_ar = rank_windows["lastmonth"].get(player_key, {}).get("actual_rank")
        last14_ar = rank_windows["last14"].get(player_key, {}).get("actual_rank")
        last7_ar = rank_windows["last7"].get(player_key, {}).get("actual_rank")

        points, label, reason = compute_reliability(season_ar, lastmonth_ar, last14_ar, last7_ar)

        output_rows.append({
            "yahoo_player_key": player_key,
            "full_name": metadata.get("full_name"),
            "editorial_team_abbr": metadata.get("editorial_team_abbr"),
            "display_position": metadata.get("display_position"),
            "position_type": metadata.get("position_type"),
            "season_ar": season_ar,
            "lastmonth_ar": lastmonth_ar,
            "last14_ar": last14_ar,
            "last7_ar": last7_ar,
            "rank_reliability_points": points,
            "reliability_label": label,
            "reliability_reason": reason,
        })

    upsert_rows(args.league_key, args.season_year, as_of_date, output_rows)

    print()
    print(
        "REFRESH_DONE|"
        f"league_key={args.league_key}|"
        f"season_year={args.season_year}|"
        f"as_of_date={as_of_date}|"
        f"batter_rows={len(output_rows)}"
    )

    targets = [value.strip() for value in str(args.targets or "").split(",") if value.strip()]
    print_targets(args.league_key, args.season_year, as_of_date, targets)


if __name__ == "__main__":
    main()
