from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from services.db import get_connection
from services.pitcher_scoring import score_pitcher
from services.queries import _derived_root


def _app_alias() -> str:
    return os.getenv("APP_ALIAS", "").strip().lower()


def fetch_owned_pitcher_rows(league_key: str, team_key: str, as_of_date: str) -> list[dict[str, Any]]:
    app_alias = _app_alias()
    season_year = int(str(as_of_date)[:4])

    with get_connection() as conn:
        with conn.cursor() as cur:
            if app_alias == "usual-rmt":
                cur.execute("""
                    WITH pstats AS (
                        SELECT
                            yahoo_player_key,
                            max(value_num) FILTER (WHERE stat_id = 26) AS era,
                            max(value_num) FILTER (WHERE stat_id = 27) AS whip,
                            max(value_num) FILTER (WHERE stat_id = 28) AS w,
                            max(value_num) FILTER (WHERE stat_id = 32) AS sv,
                            max(value_num) FILTER (WHERE stat_id = 42) AS k_pit,
                            max(value_num) FILTER (WHERE stat_id = 48) AS hld,
                            max(value_num) FILTER (WHERE stat_id = 50) AS ip
                        FROM public.yahoo_player_league_season_stat
                        WHERE league_key = %s
                          AND season_year = %s
                        GROUP BY yahoo_player_key
                    )
                    SELECT
                        r.selected_position,
                        r.full_name,
                        r.yahoo_player_key,
                        r.mlb_team_abbr,
                        r.primary_position,
                        r.eligible_positions,
                        r.status,
                        r.status_full,
                        pool.percent_owned,
                        p.era,
                        p.whip,
                        p.w,
                        p.sv,
                        p.k_pit,
                        p.hld,
                        p.ip
                    FROM lineup_tool.roster_snapshot r
                    LEFT JOIN pstats p
                      ON p.yahoo_player_key = r.yahoo_player_key
                    LEFT JOIN public.yahoo_league_player_pool pool
                      ON pool.league_key = r.league_key
                     AND pool.season_year = %s
                     AND pool.yahoo_player_key = r.yahoo_player_key
                    WHERE r.league_key = %s
                      AND r.team_key = %s
                      AND r.as_of_date = %s
                      AND (
                        r.position_type = 'P'
                        OR r.primary_position IN ('P','SP','RP')
                        OR r.selected_position IN ('P','SP','RP')
                        OR r.eligible_positions && ARRAY['P','SP','RP']
                      )
                    ORDER BY
                        CASE r.selected_position
                            WHEN 'P' THEN 1
                            WHEN 'BN' THEN 2
                            WHEN 'IL' THEN 3
                            WHEN 'NA' THEN 4
                            ELSE 9
                        END,
                        r.full_name;
                """, (league_key, season_year, season_year, league_key, team_key, as_of_date))
            else:
                cur.execute("""
                    WITH pstats AS (
                        SELECT
                            yahoo_player_key,
                            max(value_num) FILTER (WHERE stat_id = 26) AS era,
                            max(value_num) FILTER (WHERE stat_id = 27) AS whip,
                            max(value_num) FILTER (WHERE stat_id = 28) AS w,
                            max(value_num) FILTER (WHERE stat_id = 42) AS k_pit,
                            max(value_num) FILTER (WHERE stat_id = 49) AS tb,
                            max(value_num) FILTER (WHERE stat_id = 50) AS ip,
                            max(value_num) FILTER (WHERE stat_id = 83) AS qs,
                            max(value_num) FILTER (WHERE stat_id = 89) AS sv_h
                        FROM public.yahoo_player_league_season_stat
                        WHERE league_key = %s
                          AND season_year = %s
                        GROUP BY yahoo_player_key
                    )
                    SELECT
                        r.selected_position,
                        r.full_name,
                        r.yahoo_player_key,
                        r.mlb_team_abbr,
                        r.primary_position,
                        r.eligible_positions,
                        r.status,
                        r.status_full,
                        pool.percent_owned,
                        p.era,
                        p.whip,
                        p.w,
                        p.k_pit,
                        p.tb,
                        p.ip,
                        p.qs,
                        p.sv_h
                    FROM lineup_tool.roster_snapshot r
                    LEFT JOIN pstats p
                      ON p.yahoo_player_key = r.yahoo_player_key
                    LEFT JOIN public.yahoo_league_player_pool pool
                      ON pool.league_key = r.league_key
                     AND pool.season_year = %s
                     AND pool.yahoo_player_key = r.yahoo_player_key
                    WHERE r.league_key = %s
                      AND r.team_key = %s
                      AND r.as_of_date = %s
                      AND (
                        r.position_type = 'P'
                        OR r.primary_position IN ('P','SP','RP')
                        OR r.selected_position IN ('P','SP','RP')
                        OR r.eligible_positions && ARRAY['P','SP','RP']
                      )
                    ORDER BY
                        CASE r.selected_position
                            WHEN 'SP' THEN 1
                            WHEN 'RP' THEN 2
                            WHEN 'P' THEN 3
                            WHEN 'BN' THEN 4
                            WHEN 'IL' THEN 5
                            WHEN 'NA' THEN 6
                            ELSE 9
                        END,
                        r.full_name;
                """, (league_key, season_year, season_year, league_key, team_key, as_of_date))

            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    for row in rows:
        row.update(score_pitcher(row, app_alias))

    return rows


def _split_eligible_positions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x]
    return [part.strip() for part in str(value or "").replace(",", "|").split("|") if part.strip()]


def fetch_available_pitcher_rows(league_key: str, team_key: str, as_of_date: str) -> list[dict[str, Any]]:
    app_alias = _app_alias()
    season_year = int(str(as_of_date)[:4])
    candidate_file = _derived_root() / f"true_free_agent_pitchers_{as_of_date}.csv"

    if not candidate_file.exists():
        return []

    candidates: list[dict[str, Any]] = []
    with candidate_file.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pkey = (row.get("yahoo_player_key") or "").strip()
            if not pkey:
                continue

            candidates.append(
                {
                    "selected_position": "FA",
                    "full_name": row.get("player_name", ""),
                    "yahoo_player_key": pkey,
                    "mlb_team_abbr": row.get("editorial_team_abbr", ""),
                    "primary_position": "",
                    "eligible_positions": _split_eligible_positions(row.get("eligible_positions", "")),
                    "status": row.get("status", ""),
                    "status_full": row.get("status_full", ""),
                    "percent_owned_yahoo": row.get("percent_owned_yahoo", ""),
                    "yahoo_rank": row.get("yahoo_rank", ""),
                }
            )

    if not candidates:
        return []

    by_key = {row["yahoo_player_key"]: row for row in candidates}
    player_keys = list(by_key.keys())

    with get_connection() as conn:
        with conn.cursor() as cur:
            if app_alias == "usual-rmt":
                cur.execute("""
                    SELECT
                        yahoo_player_key,
                        max(value_num) FILTER (WHERE stat_id = 26) AS era,
                        max(value_num) FILTER (WHERE stat_id = 27) AS whip,
                        max(value_num) FILTER (WHERE stat_id = 28) AS w,
                        max(value_num) FILTER (WHERE stat_id = 32) AS sv,
                        max(value_num) FILTER (WHERE stat_id = 42) AS k_pit,
                        max(value_num) FILTER (WHERE stat_id = 48) AS hld,
                        max(value_num) FILTER (WHERE stat_id = 50) AS ip
                    FROM public.yahoo_player_league_season_stat
                    WHERE league_key = %s
                      AND season_year = %s
                      AND yahoo_player_key = ANY(%s)
                    GROUP BY yahoo_player_key;
                """, (league_key, season_year, player_keys))
            else:
                cur.execute("""
                    SELECT
                        yahoo_player_key,
                        max(value_num) FILTER (WHERE stat_id = 26) AS era,
                        max(value_num) FILTER (WHERE stat_id = 27) AS whip,
                        max(value_num) FILTER (WHERE stat_id = 28) AS w,
                        max(value_num) FILTER (WHERE stat_id = 42) AS k_pit,
                        max(value_num) FILTER (WHERE stat_id = 49) AS tb,
                        max(value_num) FILTER (WHERE stat_id = 50) AS ip,
                        max(value_num) FILTER (WHERE stat_id = 83) AS qs,
                        max(value_num) FILTER (WHERE stat_id = 89) AS sv_h
                    FROM public.yahoo_player_league_season_stat
                    WHERE league_key = %s
                      AND season_year = %s
                      AND yahoo_player_key = ANY(%s)
                    GROUP BY yahoo_player_key;
                """, (league_key, season_year, player_keys))

            columns = [desc[0] for desc in cur.description]
            for db_row in cur.fetchall():
                stat_row = dict(zip(columns, db_row))
                by_key[stat_row["yahoo_player_key"]].update(stat_row)

            cur.execute("""
                SELECT yahoo_player_key, percent_owned, rank_value
                FROM public.yahoo_league_player_pool
                WHERE league_key = %s
                  AND season_year = %s
                  AND yahoo_player_key = ANY(%s)
            """, (league_key, season_year, player_keys))
            for yahoo_player_key, percent_owned, rank_value in cur.fetchall():
                key = str(yahoo_player_key)
                if key in by_key:
                    by_key[key]["percent_owned"] = percent_owned
                    if not by_key[key].get("yahoo_rank"):
                        by_key[key]["yahoo_rank"] = rank_value

    rows = list(by_key.values())
    for row in rows:
        row.update(score_pitcher(row, app_alias))

    rows.sort(
        key=lambda r: (
            -int(r.get("ranking") or 0),
            str(r.get("full_name") or ""),
        )
    )

    return rows
