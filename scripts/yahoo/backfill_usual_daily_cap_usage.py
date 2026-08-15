from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.db import get_connection


USUAL_LEAGUE_KEY = "469.l.22528"
EXPECTED_SLOTS = ("C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL", "P")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except Exception:
        return default


def _active_date() -> date:
    override = str(os.environ.get("USUAL_CAP_BACKFILL_AS_OF_DATE") or "").strip()
    if override:
        return date.fromisoformat(override)

    return datetime.now(ZoneInfo("America/New_York")).date()


def _season_year(active_date: date) -> int:
    return _env_int("SEASON_YEAR", active_date.year)


def _load_seed_context(season_year: int) -> tuple[str, date]:
    team_override = str(os.environ.get("USUAL_CAP_TEAM_KEY") or "").strip()

    sql = """
        SELECT team_key, min(seed_as_of_date)::date AS seed_as_of_date
        FROM rmt.usual_cap_usage_seed
        WHERE league_key = %s
          AND season_year = %s
          AND (%s = '' OR team_key = %s)
        GROUP BY team_key
        ORDER BY team_key
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (USUAL_LEAGUE_KEY, season_year, team_override, team_override),
            )
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError(
            f"No usual_cap_usage_seed rows found for league={USUAL_LEAGUE_KEY} "
            f"season_year={season_year} team_override={team_override!r}"
        )

    if len(rows) > 1 and not team_override:
        teams = ", ".join(str(r[0]) for r in rows)
        raise RuntimeError(
            "Multiple Usual seed teams found; set USUAL_CAP_TEAM_KEY. "
            f"teams={teams}"
        )

    team_key, seed_as_of_date = rows[0]
    return str(team_key), seed_as_of_date


def _expected_dates(seed_as_of_date: date, through_date: date) -> list[date]:
    dates: list[date] = []
    d = seed_as_of_date + timedelta(days=1)
    while d <= through_date:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def _load_coverage(team_key: str, season_year: int, seed_as_of_date: date, through_date: date) -> dict[date, set[str]]:
    sql = """
        SELECT usage_date, slot_family
        FROM rmt.usual_daily_cap_usage
        WHERE league_key = %s
          AND team_key = %s
          AND usage_date > %s
          AND usage_date <= %s
        ORDER BY usage_date, slot_family
    """

    by_date: dict[date, set[str]] = {}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (USUAL_LEAGUE_KEY, team_key, seed_as_of_date, through_date),
            )
            for usage_date, slot_family in cur.fetchall():
                by_date.setdefault(usage_date, set()).add(str(slot_family))

    return by_date


def _missing_or_incomplete_dates(expected_dates: list[date], coverage: dict[date, set[str]]) -> list[tuple[date, list[str]]]:
    missing: list[tuple[date, list[str]]] = []

    for usage_date in expected_dates:
        slots_present = coverage.get(usage_date, set())
        missing_slots = [slot for slot in EXPECTED_SLOTS if slot not in slots_present]
        if missing_slots:
            missing.append((usage_date, missing_slots))

    return missing


def _run_single_date_backfill(usage_date: date) -> None:
    script_path = Path(__file__).with_name("refresh_usual_daily_cap_usage.py")

    env = os.environ.copy()
    env["USUAL_CAP_USAGE_DATE"] = usage_date.isoformat()

    subprocess.run(
        [sys.executable, str(script_path)],
        env=env,
        check=True,
    )


def main() -> None:
    active_date = _active_date()
    through_date = active_date - timedelta(days=1)
    season_year = _season_year(active_date)

    if through_date.year != season_year:
        raise RuntimeError(
            f"Refusing cross-season cap backfill: season_year={season_year} through_date={through_date}"
        )

    team_key, seed_as_of_date = _load_seed_context(season_year)
    expected_dates = _expected_dates(seed_as_of_date, through_date)
    coverage = _load_coverage(team_key, season_year, seed_as_of_date, through_date)
    missing = _missing_or_incomplete_dates(expected_dates, coverage)

    max_dates = _env_int("USUAL_CAP_BACKFILL_MAX_DATES", 31)
    sleep_seconds = max(0.0, _env_float("USUAL_CAP_BACKFILL_SLEEP_SECONDS", 20.0))

    print(
        "BEGIN usual_daily_cap_usage_backfill "
        f"league_key={USUAL_LEAGUE_KEY} team_key={team_key} "
        f"season_year={season_year} active_date={active_date} "
        f"through_date={through_date} seed_as_of_date={seed_as_of_date} "
        f"expected_dates={len(expected_dates)} missing_or_incomplete={len(missing)}",
        flush=True,
    )

    if not missing:
        print("USUAL_DAILY_CAP_USAGE_BACKFILL_NONE", flush=True)
        return

    if len(missing) > max_dates:
        listed = ",".join(d.isoformat() for d, _slots in missing[:max_dates + 1])
        raise RuntimeError(
            f"Refusing to backfill {len(missing)} dates because "
            f"USUAL_CAP_BACKFILL_MAX_DATES={max_dates}. "
            f"First dates={listed}"
        )

    for idx, (usage_date, missing_slots) in enumerate(missing, start=1):
        row_count = len(EXPECTED_SLOTS) - len(missing_slots)
        print(
            f"BACKFILL_DATE {idx}/{len(missing)} usage_date={usage_date} "
            f"row_count={row_count} missing_slots={','.join(missing_slots)}",
            flush=True,
        )

        _run_single_date_backfill(usage_date)

        if idx < len(missing) and sleep_seconds > 0:
            print(f"SLEEP {sleep_seconds}s to avoid Yahoo request throttle", flush=True)
            time.sleep(sleep_seconds)

    coverage_after = _load_coverage(team_key, season_year, seed_as_of_date, through_date)
    missing_after = _missing_or_incomplete_dates(expected_dates, coverage_after)

    if missing_after:
        for usage_date, missing_slots in missing_after:
            print(
                f"STILL_MISSING usage_date={usage_date} missing_slots={','.join(missing_slots)}",
                flush=True,
            )
        raise RuntimeError(f"Usual cap usage backfill incomplete: remaining_dates={len(missing_after)}")

    print("USUAL_DAILY_CAP_USAGE_BACKFILL_OK", flush=True)


if __name__ == "__main__":
    main()
