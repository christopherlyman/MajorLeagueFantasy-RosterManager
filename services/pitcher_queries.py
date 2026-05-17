from __future__ import annotations

import os
from typing import Any

from services.db import get_connection
from services.pitcher_scoring import score_pitcher


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
                """, (league_key, season_year, league_key, team_key, as_of_date))
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
                """, (league_key, season_year, league_key, team_key, as_of_date))

            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    for row in rows:
        row.update(score_pitcher(row, app_alias))

    return rows
