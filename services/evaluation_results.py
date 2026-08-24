from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from services.db import get_connection


TRACK_LABELS = {
    "YGMA_PRE_RMT": "YGMA",
    "RMT_RECOMMENDED_BASELINE": "RMT Baseline",
    "USER_FINAL_LOCKED": "Final Locked",
}

TRACK_ORDER = {
    "YGMA_PRE_RMT": 1,
    "RMT_RECOMMENDED_BASELINE": 2,
    "USER_FINAL_LOCKED": 3,
}


def _serializable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows_as_dicts(cursor) -> list[dict[str, Any]]:
    cols = [d.name for d in cursor.description]
    out: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        item = {col: _serializable(value) for col, value in zip(cols, row)}
        item["track_label"] = TRACK_LABELS.get(str(item.get("snapshot_source") or ""), str(item.get("snapshot_source") or ""))
        item["is_winner"] = int(item.get("outcome_rank") or 0) == 1
        item["completion"] = (
            "Complete"
            if item.get("is_final") and int(item.get("missing_actual_rows") or 0) == 0
            else "Incomplete"
        )
        out.append(item)
    return out


def fetch_hitter_evaluation_scorecards(limit: int = 500) -> list[dict[str, Any]]:
    sql = """
        WITH scored AS (
            SELECT
                r.eval_run_id,
                r.eval_date,
                sc.stat_date,
                r.instance_alias,
                r.league_key,
                r.team_key,
                sc.snapshot_source,
                sc.is_final,
                sc.starting_hitter_rows,
                sc.actual_rows,
                sc.missing_actual_rows,
                sc.total_hits,
                sc.total_ab,
                sc.batting_avg,
                sc.total_r,
                sc.total_hr,
                sc.total_rbi,
                sc.total_sb,
                sc.total_bb,
                sc.total_k,
                (
                    sc.total_r
                    + sc.total_hr
                    + sc.total_rbi
                    + sc.total_sb
                    + sc.total_bb
                ) AS event_score
            FROM rmt.eval_hitter_scorecard sc
            JOIN rmt.eval_run r
              ON r.eval_run_id = sc.eval_run_id
        ),
        ranked AS (
            SELECT
                *,
                RANK() OVER (
                    PARTITION BY eval_date, stat_date, instance_alias
                    ORDER BY event_score DESC,
                             batting_avg DESC NULLS LAST,
                             total_hits DESC,
                             total_k ASC
                ) AS outcome_rank
            FROM scored
        )
        SELECT *
        FROM ranked
        ORDER BY stat_date DESC,
                 instance_alias,
                 outcome_rank,
                 CASE snapshot_source
                   WHEN 'YGMA_PRE_RMT' THEN 1
                   WHEN 'RMT_RECOMMENDED_BASELINE' THEN 2
                   WHEN 'USER_FINAL_LOCKED' THEN 3
                   ELSE 9
                 END
        LIMIT %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(limit),))
            return _rows_as_dicts(cur)
