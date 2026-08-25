from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from services.db import get_connection
from scripts.yahoo.dated_roster import fetch_yahoo_dated_roster


ACTIVE_SLOTS = {"C", "1B", "2B", "3B", "SS", "IF", "OF", "UTIL", "P", "SP", "RP"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)


def _normalize_player_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+\(([A-Z]{2,4})\)$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _lookup_player(lookup: dict[str, dict], player_name: Any) -> dict:
    raw = str(player_name or "").strip()
    return lookup.get(raw) or lookup.get(_normalize_player_name(raw)) or {}


def _first(row: dict, *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def _is_starting_slot(slot: Any, player_name: Any = None) -> bool:
    slot_text = str(slot or "").strip().upper()
    if str(player_name or "").strip().upper() == "EMPTY":
        return False
    if slot_text in ACTIVE_SLOTS:
        return True
    if re.fullmatch(r"OF\d+", slot_text):
        return True
    return False


def ensure_eval_tables() -> None:
    sql = """
    CREATE SCHEMA IF NOT EXISTS rmt;

    CREATE TABLE IF NOT EXISTS rmt.eval_run (
        eval_run_id bigserial PRIMARY KEY,
        league_key text NOT NULL,
        team_key text NOT NULL,
        instance_alias text NOT NULL DEFAULT '',
        eval_date date NOT NULL,
        app_section text NOT NULL DEFAULT 'batters',
        refresh_label text NOT NULL,
        git_commit text NULL,
        context_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at_utc timestamptz NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_rmt_eval_run_lookup
    ON rmt.eval_run (league_key, team_key, eval_date, app_section, created_at_utc DESC);

    CREATE TABLE IF NOT EXISTS rmt.eval_lineup_snapshot (
        eval_run_id bigint NOT NULL REFERENCES rmt.eval_run(eval_run_id) ON DELETE CASCADE,
        snapshot_source text NOT NULL,
        row_ordinal integer NOT NULL,
        selected_position text NULL,
        yahoo_player_key text NULL,
        player_name text NULL,
        mlb_team_abbr text NULL,
        projected_ranking numeric NULL,
        ranking_band text NULL,
        is_starting_slot boolean NOT NULL DEFAULT false,
        row_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at_utc timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (eval_run_id, snapshot_source, row_ordinal)
    );

    CREATE INDEX IF NOT EXISTS idx_rmt_eval_lineup_lookup
    ON rmt.eval_lineup_snapshot (snapshot_source, yahoo_player_key, player_name);

    CREATE TABLE IF NOT EXISTS rmt.eval_recommendation_snapshot (
        eval_run_id bigint NOT NULL REFERENCES rmt.eval_run(eval_run_id) ON DELETE CASCADE,
        recommendation_rank integer NOT NULL,
        decision text NULL,
        drop_player_key text NULL,
        drop_player_name text NULL,
        add_player_key text NULL,
        add_player_name text NULL,
        backup_player_key text NULL,
        backup_player_name text NULL,
        primary_gain numeric NULL,
        backup_gain numeric NULL,
        action_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at_utc timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (eval_run_id, recommendation_rank)
    );

    CREATE INDEX IF NOT EXISTS idx_rmt_eval_recommendation_lookup
    ON rmt.eval_recommendation_snapshot (decision, drop_player_name, add_player_name);
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def _create_eval_run(
    conn,
    *,
    league_key: str,
    team_key: str,
    eval_date: str,
    refresh_label: str,
    context: dict,
) -> int:
    payload = {
        "context": context,
        "instance_alias": os.environ.get("APP_ALIAS", ""),
        "refresh_label": refresh_label,
    }
    sql = """
    INSERT INTO rmt.eval_run (
        league_key,
        team_key,
        instance_alias,
        eval_date,
        app_section,
        refresh_label,
        git_commit,
        context_json,
        created_at_utc
    )
    VALUES (%s, %s, %s, %s::date, 'batters', %s, %s, %s::jsonb, now())
    RETURNING eval_run_id
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                league_key,
                team_key,
                os.environ.get("APP_ALIAS", ""),
                eval_date,
                refresh_label,
                os.environ.get("RMT_GIT_COMMIT") or None,
                _json_dumps(payload),
            ),
        )
        return int(cur.fetchone()[0])


def _player_lookup(conn, league_key: str, team_key: str, eval_date: str) -> dict[str, dict]:
    lookup: dict[str, dict] = {}

    def add_player(full_name: Any, yahoo_player_key: Any, mlb_team_abbr: Any, *, overwrite: bool = True) -> None:
        if not full_name:
            return
        rec = {
            "yahoo_player_key": yahoo_player_key,
            "mlb_team_abbr": mlb_team_abbr,
        }
        raw_key = str(full_name).strip()
        norm_key = _normalize_player_name(raw_key)
        if overwrite or raw_key not in lookup:
            lookup[raw_key] = rec
        if overwrite or norm_key not in lookup:
            lookup[norm_key] = rec

    roster_sql = """
    SELECT full_name, yahoo_player_key, mlb_team_abbr
    FROM lineup_tool.roster_snapshot
    WHERE league_key = %s
      AND team_key = %s
      AND as_of_date = %s::date
    """
    pool_sql = """
    SELECT full_name, yahoo_player_key, mlb_team_abbr
    FROM lineup_tool.league_player_pool_snapshot
    WHERE league_key = %s
      AND as_of_date = %s::date
    """

    with conn.cursor() as cur:
        cur.execute(roster_sql, (league_key, team_key, eval_date))
        for full_name, yahoo_player_key, mlb_team_abbr in cur.fetchall():
            add_player(full_name, yahoo_player_key, mlb_team_abbr, overwrite=True)

        cur.execute(pool_sql, (league_key, eval_date))
        for full_name, yahoo_player_key, mlb_team_abbr in cur.fetchall():
            add_player(full_name, yahoo_player_key, mlb_team_abbr, overwrite=False)

    return lookup


def _insert_lineup_rows(conn, eval_run_id: int, source: str, rows: list[dict]) -> None:
    delete_sql = """
    DELETE FROM rmt.eval_lineup_snapshot
    WHERE eval_run_id = %s
      AND snapshot_source = %s
    """
    insert_sql = """
    INSERT INTO rmt.eval_lineup_snapshot (
        eval_run_id,
        snapshot_source,
        row_ordinal,
        selected_position,
        yahoo_player_key,
        player_name,
        mlb_team_abbr,
        projected_ranking,
        ranking_band,
        is_starting_slot,
        row_json,
        created_at_utc
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
    ON CONFLICT (eval_run_id, snapshot_source, row_ordinal)
    DO UPDATE SET
        selected_position = EXCLUDED.selected_position,
        yahoo_player_key = EXCLUDED.yahoo_player_key,
        player_name = EXCLUDED.player_name,
        mlb_team_abbr = EXCLUDED.mlb_team_abbr,
        projected_ranking = EXCLUDED.projected_ranking,
        ranking_band = EXCLUDED.ranking_band,
        is_starting_slot = EXCLUDED.is_starting_slot,
        row_json = EXCLUDED.row_json,
        created_at_utc = now()
    """

    with conn.cursor() as cur:
        cur.execute(delete_sql, (eval_run_id, source))
        for idx, row in enumerate(rows, start=1):
            selected_position = _first(row, "selected_position", "Slot", "slot", "current_slot")
            player_name = _first(row, "full_name", "player_name", "Player", "name")
            yahoo_player_key = _first(row, "yahoo_player_key", "Yahoo Key", "player_key")
            mlb_team_abbr = _first(row, "mlb_team_abbr", "editorial_team_abbr", "Team", "MLB")
            projected_ranking = _safe_float(_first(row, "ranking", "Rank", "Projected Rank"))
            ranking_band = _first(row, "ranking_band", "Band")
            cur.execute(
                insert_sql,
                (
                    eval_run_id,
                    source,
                    idx,
                    selected_position,
                    yahoo_player_key,
                    player_name,
                    mlb_team_abbr,
                    projected_ranking,
                    ranking_band,
                    _is_starting_slot(selected_position, player_name),
                    _json_dumps(row),
                ),
            )


def _capture_ygma_pre_rmt_lineup(
    conn,
    eval_run_id: int,
    *,
    league_key: str,
    team_key: str,
    eval_date: str,
    snapshot_source: str = "YGMA_PRE_RMT",
) -> int:
    sql = """
    SELECT
        selected_position,
        yahoo_player_key,
        full_name,
        mlb_team_abbr,
        status,
        status_full,
        is_keeper,
        is_undroppable,
        eligible_positions,
        loaded_at
    FROM lineup_tool.roster_snapshot
    WHERE league_key = %s
      AND team_key = %s
      AND as_of_date = %s::date
    ORDER BY
      CASE upper(coalesce(selected_position, ''))
        WHEN 'C' THEN 1
        WHEN '1B' THEN 2
        WHEN '2B' THEN 3
        WHEN '3B' THEN 4
        WHEN 'SS' THEN 5
        WHEN 'IF' THEN 6
        WHEN 'OF' THEN 7
        WHEN 'UTIL' THEN 8
        WHEN 'P' THEN 9
        WHEN 'BN' THEN 10
        WHEN 'IL' THEN 11
        WHEN 'NA' THEN 12
        ELSE 99
      END,
      full_name
    """
    rows: list[dict] = []
    with conn.cursor() as cur:
        cur.execute(sql, (league_key, team_key, eval_date))
        for row in cur.fetchall():
            rows.append(
                {
                    "selected_position": row[0],
                    "yahoo_player_key": row[1],
                    "player_name": row[2],
                    "mlb_team_abbr": row[3],
                    "status": row[4],
                    "status_full": row[5],
                    "is_keeper": row[6],
                    "is_undroppable": row[7],
                    "eligible_positions": row[8],
                    "loaded_at": row[9],
                }
            )

    _insert_lineup_rows(conn, eval_run_id, snapshot_source, rows)
    return len(rows)


def _persist_rmt_recommended_lineup(
    conn,
    eval_run_id: int,
    *,
    league_key: str,
    team_key: str,
    eval_date: str,
    baseline_rows: list[dict],
) -> int:
    lookup = _player_lookup(conn, league_key, team_key, eval_date)
    rows: list[dict] = []

    for row in baseline_rows:
        out = dict(row)
        player_name = str(out.get("Player") or "").strip()
        found = _lookup_player(lookup, player_name)
        out.setdefault("yahoo_player_key", found.get("yahoo_player_key"))
        out.setdefault("mlb_team_abbr", found.get("mlb_team_abbr"))
        rows.append(out)

    _insert_lineup_rows(conn, eval_run_id, "RMT_RECOMMENDED_BASELINE", rows)
    return len(rows)


def _persist_recommendations(conn, eval_run_id: int, action_rows: list[dict], lookup: dict[str, dict]) -> int:
    delete_sql = "DELETE FROM rmt.eval_recommendation_snapshot WHERE eval_run_id = %s"
    insert_sql = """
    INSERT INTO rmt.eval_recommendation_snapshot (
        eval_run_id,
        recommendation_rank,
        decision,
        drop_player_key,
        drop_player_name,
        add_player_key,
        add_player_name,
        backup_player_key,
        backup_player_name,
        primary_gain,
        backup_gain,
        action_json,
        created_at_utc
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
    ON CONFLICT (eval_run_id, recommendation_rank)
    DO UPDATE SET
        decision = EXCLUDED.decision,
        drop_player_key = EXCLUDED.drop_player_key,
        drop_player_name = EXCLUDED.drop_player_name,
        add_player_key = EXCLUDED.add_player_key,
        add_player_name = EXCLUDED.add_player_name,
        backup_player_key = EXCLUDED.backup_player_key,
        backup_player_name = EXCLUDED.backup_player_name,
        primary_gain = EXCLUDED.primary_gain,
        backup_gain = EXCLUDED.backup_gain,
        action_json = EXCLUDED.action_json,
        created_at_utc = now()
    """
    with conn.cursor() as cur:
        cur.execute(delete_sql, (eval_run_id,))
        for idx, row in enumerate(action_rows, start=1):
            drop_name = row.get("Drop")
            add_name = row.get("Primary Add")
            backup_name = row.get("Backup Add")
            drop_found = _lookup_player(lookup, drop_name)
            add_found = _lookup_player(lookup, add_name)
            backup_found = _lookup_player(lookup, backup_name)

            cur.execute(
                insert_sql,
                (
                    eval_run_id,
                    idx,
                    row.get("Decision"),
                    drop_found.get("yahoo_player_key"),
                    drop_name,
                    add_found.get("yahoo_player_key"),
                    add_name,
                    backup_found.get("yahoo_player_key"),
                    backup_name,
                    _safe_float(row.get("Primary Gain")),
                    _safe_float(row.get("Backup Gain")),
                    _json_dumps(row),
                ),
            )

    return len(action_rows)

def record_batter_recommendation_eval(
    ctx_obj: dict,
    *,
    refresh_label: str,
    plan: tuple,
) -> int:
    """Persist YGMA baseline lineup, RMT baseline lineup, and RMT action rows."""
    ensure_eval_tables()

    league_key = str(ctx_obj["league_key"])
    team_key = str(ctx_obj["team_key"])
    eval_date = str(ctx_obj["as_of_date"])

    top_action, action_rows, baseline_rows, summary = plan

    context = {
        "ctx": ctx_obj,
        "summary": summary,
        "top_action": top_action,
    }

    with get_connection() as conn:
        eval_run_id = _create_eval_run(
            conn,
            league_key=league_key,
            team_key=team_key,
            eval_date=eval_date,
            refresh_label=refresh_label,
            context=context,
        )
        ygma_rows = _capture_ygma_pre_rmt_lineup(
            conn,
            eval_run_id,
            league_key=league_key,
            team_key=team_key,
            eval_date=eval_date,
        )
        rmt_rows = _persist_rmt_recommended_lineup(
            conn,
            eval_run_id,
            league_key=league_key,
            team_key=team_key,
            eval_date=eval_date,
            baseline_rows=list(baseline_rows or []),
        )
        rec_rows = _persist_recommendations(conn, eval_run_id, list(action_rows or []), _player_lookup(conn, league_key, team_key, eval_date))

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rmt.eval_run
                SET context_json = context_json || %s::jsonb
                WHERE eval_run_id = %s
                """,
                (
                    _json_dumps(
                        {
                            "persisted_counts": {
                                "ygma_pre_rmt_lineup_rows": ygma_rows,
                                "rmt_recommended_lineup_rows": rmt_rows,
                                "recommendation_rows": rec_rows,
                            }
                        }
                    ),
                    eval_run_id,
                ),
            )

        conn.commit()

    return eval_run_id



def record_historical_final_roster_eval(
    *,
    league_key: str,
    team_key: str,
    eval_date: str,
    refresh_label: str = "Yahoo Historical Final",
) -> int:
    """Replace USER_FINAL_LOCKED with Yahoo's authoritative dated roster."""
    ensure_eval_tables()

    roster_date = date.fromisoformat(eval_date)
    final_rows = fetch_yahoo_dated_roster(team_key, roster_date)

    if not final_rows:
        raise RuntimeError(
            f"Yahoo historical roster returned no players "
            f"team_key={team_key} eval_date={eval_date}"
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT eval_run_id
                FROM rmt.eval_run
                WHERE league_key = %s
                  AND team_key = %s
                  AND eval_date = %s::date
                  AND app_section = 'batters'
                ORDER BY eval_run_id DESC
                LIMIT 1
                """,
                (league_key, team_key, eval_date),
            )
            row = cur.fetchone()

        if row:
            eval_run_id = int(row[0])
        else:
            eval_run_id = _create_eval_run(
                conn,
                league_key=league_key,
                team_key=team_key,
                eval_date=eval_date,
                refresh_label=refresh_label,
                context={
                    "warning": "historical_final_without_prior_eval_run",
                    "source": "yahoo_historical_roster",
                },
            )

        _insert_lineup_rows(
            conn,
            eval_run_id,
            "USER_FINAL_LOCKED",
            final_rows,
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rmt.eval_run
                SET context_json = context_json || %s::jsonb
                WHERE eval_run_id = %s
                """,
                (
                    _json_dumps(
                        {
                            "final_lock": {
                                "refresh_label": refresh_label,
                                "source": "yahoo_historical_roster",
                                "authoritative": True,
                                "roster_date": eval_date,
                                "user_final_locked_rows": len(final_rows),
                                "locked_at_utc": datetime.utcnow().isoformat(),
                            }
                        }
                    ),
                    eval_run_id,
                ),
            )

        conn.commit()

    return eval_run_id

def record_final_roster_eval(
    ctx_obj: dict,
    *,
    refresh_label: str = "Lock Final Snapshot",
) -> int:
    """Attach the current Yahoo roster snapshot as USER_FINAL_LOCKED to today's latest eval run."""
    ensure_eval_tables()

    league_key = str(ctx_obj["league_key"])
    team_key = str(ctx_obj["team_key"])
    eval_date = str(ctx_obj["as_of_date"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT eval_run_id
                FROM rmt.eval_run
                WHERE league_key = %s
                  AND team_key = %s
                  AND eval_date = %s::date
                  AND app_section = 'batters'
                ORDER BY eval_run_id DESC
                LIMIT 1
                """,
                (league_key, team_key, eval_date),
            )
            row = cur.fetchone()

        if row:
            eval_run_id = int(row[0])
        else:
            eval_run_id = _create_eval_run(
                conn,
                league_key=league_key,
                team_key=team_key,
                eval_date=eval_date,
                refresh_label=refresh_label,
                context={
                    "warning": "final_snapshot_without_prior_eval_run",
                    "ctx": ctx_obj,
                },
            )

        final_rows = _capture_ygma_pre_rmt_lineup(
            conn,
            eval_run_id,
            league_key=league_key,
            team_key=team_key,
            eval_date=eval_date,
            snapshot_source="USER_FINAL_LOCKED",
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rmt.eval_run
                SET context_json = context_json || %s::jsonb
                WHERE eval_run_id = %s
                """,
                (
                    _json_dumps(
                        {
                            "final_lock": {
                                "refresh_label": refresh_label,
                                "user_final_locked_rows": final_rows,
                                "locked_at_utc": datetime.utcnow().isoformat(),
                            }
                        }
                    ),
                    eval_run_id,
                ),
            )

        conn.commit()

    return eval_run_id
