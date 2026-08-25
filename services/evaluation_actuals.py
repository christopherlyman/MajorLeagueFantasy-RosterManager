from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.db import get_connection
from services.evaluation import record_historical_final_roster_eval

HITTER_BASE_SLOTS = {"C", "1B", "2B", "3B", "SS", "IF", "UTIL"}


def _is_hitter_slot(slot: Any) -> bool:
    text = str(slot or "").strip().upper()
    return text in HITTER_BASE_SLOTS or text.startswith("OF")


def _is_final_stat_date(stat_date: str) -> bool:
    stat_day = datetime.strptime(str(stat_date), "%Y-%m-%d").date()
    final_after = datetime.combine(
        stat_day + timedelta(days=1),
        time(hour=10, minute=0),
        tzinfo=timezone.utc,
    )
    return datetime.now(timezone.utc) >= final_after


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _safe_decimal(value: Any) -> Decimal:
    try:
        if value in ("", None):
            return Decimal("0")
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def ensure_eval_actual_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS rmt.eval_hitter_scorecard (
        eval_run_id bigint NOT NULL REFERENCES rmt.eval_run(eval_run_id) ON DELETE CASCADE,
        snapshot_source text NOT NULL,
        stat_date date NOT NULL,
        is_final boolean NOT NULL DEFAULT false,
        starting_hitter_rows integer NOT NULL DEFAULT 0,
        distinct_hitter_keys integer NOT NULL DEFAULT 0,
        actual_rows integer NOT NULL DEFAULT 0,
        missing_actual_rows integer NOT NULL DEFAULT 0,
        total_hits integer NOT NULL DEFAULT 0,
        total_ab integer NOT NULL DEFAULT 0,
        total_r integer NOT NULL DEFAULT 0,
        total_hr integer NOT NULL DEFAULT 0,
        total_rbi integer NOT NULL DEFAULT 0,
        total_sb integer NOT NULL DEFAULT 0,
        total_bb integer NOT NULL DEFAULT 0,
        total_k integer NOT NULL DEFAULT 0,
        batting_avg numeric,
        score_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at_utc timestamptz NOT NULL DEFAULT now(),
        updated_at_utc timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (eval_run_id, snapshot_source, stat_date)
    );

    CREATE INDEX IF NOT EXISTS idx_eval_hitter_scorecard_stat_date
        ON rmt.eval_hitter_scorecard(stat_date);

    CREATE INDEX IF NOT EXISTS idx_eval_hitter_scorecard_source
        ON rmt.eval_hitter_scorecard(snapshot_source);
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def _load_eval_hitter_players(
    eval_date: str,
    league_key: str | None = None,
    team_key: str | None = None,
    include_unlocked: bool = False,
    eval_run_id: int | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [eval_date]
    league_filter = ""
    team_filter = ""
    run_filter = ""

    if league_key:
        league_filter = "AND r.league_key = %s"
        params.append(league_key)

    if team_key:
        team_filter = "AND r.team_key = %s"
        params.append(team_key)

    if eval_run_id is not None:
        run_filter = "AND r.eval_run_id = %s"
        params.append(eval_run_id)

    lock_filter = "" if include_unlocked else "AND r.context_json ? 'final_lock'"

    sql = f"""
        SELECT
            r.eval_run_id,
            r.instance_alias,
            r.league_key,
            r.team_key,
            s.snapshot_source,
            s.row_ordinal,
            s.selected_position,
            s.yahoo_player_key,
            s.player_name
        FROM rmt.eval_run r
        JOIN rmt.eval_lineup_snapshot s
          ON s.eval_run_id = r.eval_run_id
        WHERE r.eval_date = %s::date
          {league_filter}
          {team_filter}
          {run_filter}
          {lock_filter}
          AND s.is_starting_slot
          AND s.yahoo_player_key IS NOT NULL
          AND (
                upper(s.selected_position) IN ('C','1B','2B','3B','SS','IF','UTIL')
                OR upper(s.selected_position) LIKE 'OF%%'
              )
        ORDER BY r.eval_run_id, s.snapshot_source, s.row_ordinal
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _cached_actual_map(stat_date: str, player_keys: list[str]) -> dict[str, dict[str, Any]]:
    if not player_keys:
        return {}

    sql = """
        SELECT yahoo_player_key,
               hits,
               ab,
               r,
               hr,
               rbi,
               sb,
               bb,
               k,
               avg,
               fetch_status,
               fetched_at_utc
        FROM rmt.yahoo_batter_daily_stat_cache
        WHERE stat_date = %s::date
          AND yahoo_player_key = ANY(%s)
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (stat_date, player_keys))
            rows = cur.fetchall()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row[0])] = {
            "yahoo_player_key": row[0],
            "hits": row[1],
            "ab": row[2],
            "r": row[3],
            "hr": row[4],
            "rbi": row[5],
            "sb": row[6],
            "bb": row[7],
            "k": row[8],
            "avg": row[9],
            "fetch_status": row[10],
            "fetched_at_utc": row[11],
        }
    return out


def _refresh_daily_actuals(stat_date: str, player_keys: list[str]) -> None:
    # Route every required historical player through the existing Yahoo
    # daily-stat loader. That loader reuses cache rows fetched after its
    # finalization cutoff and re-fetches provisional or missing rows.
    import sys
    import requests

    yahoo_script_dir = Path(__file__).resolve().parents[1] / "scripts" / "yahoo"
    yahoo_script_dir_text = str(yahoo_script_dir)
    if yahoo_script_dir_text not in sys.path:
        sys.path.insert(0, yahoo_script_dir_text)

    from auth import get_access_token
    from refresh_recent_yahoo_api import get_player_daily_stats

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    with requests.Session() as session:
        for idx, player_key in enumerate(player_keys, start=1):
            print(
                f"REFRESH_ACTUAL [{idx}/{len(player_keys)}] "
                f"player_key={player_key} stat_date={stat_date}",
                flush=True,
            )
            try:
                get_player_daily_stats(session, headers, player_key, stat_date)
            except Exception as exc:
                print(
                    f"WARN_REFRESH_ACTUAL_FAILED player_key={player_key} "
                    f"stat_date={stat_date} error={type(exc).__name__}: {exc}",
                    flush=True,
                )


def _upsert_scorecard_rows(
    eval_date: str,
    stat_date: str,
    rows: list[dict[str, Any]],
    actuals: dict[str, dict[str, Any]],
    finalization_cutoff_met: bool,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["eval_run_id"]), str(row["snapshot_source"])), []).append(row)

    scorecards: list[dict[str, Any]] = []

    sql = """
        INSERT INTO rmt.eval_hitter_scorecard (
            eval_run_id,
            snapshot_source,
            stat_date,
            is_final,
            starting_hitter_rows,
            distinct_hitter_keys,
            actual_rows,
            missing_actual_rows,
            total_hits,
            total_ab,
            total_r,
            total_hr,
            total_rbi,
            total_sb,
            total_bb,
            total_k,
            batting_avg,
            score_json,
            updated_at_utc
        )
        VALUES (
            %s, %s, %s::date, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now()
        )
        ON CONFLICT (eval_run_id, snapshot_source, stat_date)
        DO UPDATE SET
            is_final = EXCLUDED.is_final,
            starting_hitter_rows = EXCLUDED.starting_hitter_rows,
            distinct_hitter_keys = EXCLUDED.distinct_hitter_keys,
            actual_rows = EXCLUDED.actual_rows,
            missing_actual_rows = EXCLUDED.missing_actual_rows,
            total_hits = EXCLUDED.total_hits,
            total_ab = EXCLUDED.total_ab,
            total_r = EXCLUDED.total_r,
            total_hr = EXCLUDED.total_hr,
            total_rbi = EXCLUDED.total_rbi,
            total_sb = EXCLUDED.total_sb,
            total_bb = EXCLUDED.total_bb,
            total_k = EXCLUDED.total_k,
            batting_avg = EXCLUDED.batting_avg,
            score_json = EXCLUDED.score_json,
            updated_at_utc = now()
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            for (eval_run_id, snapshot_source), source_rows in sorted(grouped.items()):
                keys = [str(r["yahoo_player_key"]) for r in source_rows if r.get("yahoo_player_key")]
                distinct_keys = sorted(set(keys))

                total_hits = total_ab = total_r = total_hr = total_rbi = total_sb = total_bb = total_k = 0
                actual_rows = 0
                missing_keys: list[str] = []

                for key in distinct_keys:
                    actual = actuals.get(key)
                    if not actual or str(actual.get("fetch_status") or "").strip().lower() != "success":
                        missing_keys.append(key)
                        continue

                    actual_rows += 1
                    total_hits += _safe_int(actual.get("hits"))
                    total_ab += _safe_int(actual.get("ab"))
                    total_r += _safe_int(actual.get("r"))
                    total_hr += _safe_int(actual.get("hr"))
                    total_rbi += _safe_int(actual.get("rbi"))
                    total_sb += _safe_int(actual.get("sb"))
                    total_bb += _safe_int(actual.get("bb"))
                    total_k += _safe_int(actual.get("k"))

                batting_avg = None
                if total_ab > 0:
                    batting_avg = Decimal(total_hits) / Decimal(total_ab)

                row_is_final = finalization_cutoff_met and not missing_keys

                score_json = {
                    "eval_date": eval_date,
                    "stat_date": stat_date,
                    "missing_keys": missing_keys,
                    "finalization_cutoff_met": finalization_cutoff_met,
                    "notes": "Hitter-only actual scorecard. Pitchers are excluded until pitcher actual cache exists.",
                }

                payload = (
                    eval_run_id,
                    snapshot_source,
                    stat_date,
                    row_is_final,
                    len(source_rows),
                    len(distinct_keys),
                    actual_rows,
                    len(missing_keys),
                    total_hits,
                    total_ab,
                    total_r,
                    total_hr,
                    total_rbi,
                    total_sb,
                    total_bb,
                    total_k,
                    batting_avg,
                    json.dumps(score_json),
                )
                cur.execute(sql, payload)

                scorecards.append(
                    {
                        "eval_run_id": eval_run_id,
                        "snapshot_source": snapshot_source,
                        "stat_date": stat_date,
                        "is_final": row_is_final,
                        "starting_hitter_rows": len(source_rows),
                        "distinct_hitter_keys": len(distinct_keys),
                        "actual_rows": actual_rows,
                        "missing_actual_rows": len(missing_keys),
                        "H": total_hits,
                        "AB": total_ab,
                        "AVG": "" if batting_avg is None else f"{float(batting_avg):.3f}",
                        "R": total_r,
                        "HR": total_hr,
                        "RBI": total_rbi,
                        "SB": total_sb,
                        "BB": total_bb,
                        "K": total_k,
                    }
                )
        conn.commit()

    return scorecards


def run_hitter_scorecard(
    eval_date: str,
    stat_date: str | None = None,
    *,
    league_key: str | None = None,
    team_key: str | None = None,
    refresh_final_roster: bool = False,
    refresh_actuals: bool = False,
    allow_incomplete: bool = False,
    include_unlocked: bool = False,
) -> list[dict[str, Any]]:
    stat_date = stat_date or eval_date
    finalization_cutoff_met = _is_final_stat_date(stat_date)

    if not finalization_cutoff_met and not allow_incomplete:
        raise RuntimeError(
            f"Stats for {stat_date} are not final yet. Re-run after 10:00 UTC next day, "
            "or pass --allow-incomplete for a provisional scorecard."
        )

    authoritative_eval_run_id: int | None = None

    if refresh_final_roster:
        if not league_key or not team_key:
            raise RuntimeError(
                "--refresh-final-roster requires both league_key and team_key"
            )

        authoritative_eval_run_id = record_historical_final_roster_eval(
            league_key=league_key,
            team_key=team_key,
            eval_date=eval_date,
        )
        print(
            f"HISTORICAL_FINAL_ROSTER_REFRESHED "
            f"eval_run_id={authoritative_eval_run_id} eval_date={eval_date}",
            flush=True,
        )

    ensure_eval_actual_tables()

    rows = _load_eval_hitter_players(
        eval_date,
        league_key=league_key,
        team_key=team_key,
        include_unlocked=include_unlocked,
        eval_run_id=authoritative_eval_run_id,
    )
    keys = sorted({str(r["yahoo_player_key"]) for r in rows if r.get("yahoo_player_key")})

    actuals = _cached_actual_map(stat_date, keys)

    if refresh_actuals and keys:
        _refresh_daily_actuals(stat_date, keys)
        actuals = _cached_actual_map(stat_date, keys)

    return _upsert_scorecard_rows(
        eval_date,
        stat_date,
        rows,
        actuals,
        finalization_cutoff_met,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build hitter-only actual outcome scorecards for RMT eval snapshots.")
    parser.add_argument("--eval-date", required=True)
    parser.add_argument("--stat-date", default="")
    parser.add_argument("--league-key", default="")
    parser.add_argument("--team-key", default="")
    parser.add_argument(
        "--refresh-final-roster",
        action="store_true",
        help="Replace USER_FINAL_LOCKED from Yahoo's historical roster for eval-date.",
    )
    parser.add_argument(
        "--refresh-actuals",
        "--fetch-missing",
        dest="refresh_actuals",
        action="store_true",
        help="Revalidate every required hitter through the Yahoo daily-stat cache loader.",
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--include-unlocked", action="store_true")
    args = parser.parse_args()

    rows = run_hitter_scorecard(
        args.eval_date,
        args.stat_date or args.eval_date,
        league_key=args.league_key or None,
        team_key=args.team_key or None,
        refresh_final_roster=args.refresh_final_roster,
        refresh_actuals=args.refresh_actuals,
        allow_incomplete=args.allow_incomplete,
        include_unlocked=args.include_unlocked,
    )

    print("EVAL_RUN | SOURCE | FINAL | STARTERS | DISTINCT | ACTUAL | MISSING | H/AB | AVG | R | HR | RBI | SB | BB | K")
    for row in rows:
        print(
            row["eval_run_id"],
            row["snapshot_source"],
            row["is_final"],
            row["starting_hitter_rows"],
            row["distinct_hitter_keys"],
            row["actual_rows"],
            row["missing_actual_rows"],
            f'{row["H"]}/{row["AB"]}',
            row["AVG"],
            row["R"],
            row["HR"],
            row["RBI"],
            row["SB"],
            row["BB"],
            row["K"],
            sep=" | ",
        )

    print("RMT_HITTER_ACTUAL_SCORECARD_COMPLETE=YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
