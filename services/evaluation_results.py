from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from services.db import get_connection


TRACK_LABELS = {
    "YGMA_PRE_RMT": "YGMA",
    "RMT_RECOMMENDED_BASELINE": "RMT Baseline",
    "USER_FINAL_LOCKED": "Final Lineup",
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
        WITH candidate_runs AS (
            SELECT DISTINCT
                r.eval_run_id,
                r.eval_date,
                sc.stat_date,
                r.instance_alias,
                r.league_key,
                r.team_key,
                CASE
                    WHEN r.context_json -> 'final_lock' ->> 'source'
                         = 'yahoo_historical_roster'
                    THEN 0
                    ELSE 1
                END AS authority_priority
            FROM rmt.eval_hitter_scorecard sc
            JOIN rmt.eval_run r
              ON r.eval_run_id = sc.eval_run_id
        ),
        authoritative_runs AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            eval_date,
                            stat_date,
                            instance_alias,
                            league_key,
                            team_key
                        ORDER BY
                            authority_priority ASC,
                            eval_run_id DESC
                    ) AS authority_rank
                FROM candidate_runs
            ) x
            WHERE authority_rank = 1
        ),
        scored AS (
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
            FROM authoritative_runs ar
            JOIN rmt.eval_run r
              ON r.eval_run_id = ar.eval_run_id
            JOIN rmt.eval_hitter_scorecard sc
              ON sc.eval_run_id = ar.eval_run_id
             AND sc.stat_date = ar.stat_date
        ),
        ranked AS (
            SELECT
                *,
                RANK() OVER (
                    PARTITION BY
                        eval_date,
                        stat_date,
                        instance_alias,
                        league_key,
                        team_key
                    ORDER BY
                        event_score DESC,
                        batting_avg DESC NULLS LAST,
                        total_hits DESC,
                        total_k ASC
                ) AS outcome_rank
            FROM scored
        )
        SELECT *
        FROM ranked
        ORDER BY
            stat_date DESC,
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


def summarize_hitter_evaluation_scorecards(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize complete three-source hitter league-days without double-counting ties."""
    expected_sources = set(TRACK_ORDER)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}

    for row in rows:
        key = (
            str(row.get("stat_date") or ""),
            str(row.get("instance_alias") or ""),
            str(row.get("league_key") or ""),
            str(row.get("team_key") or ""),
        )
        groups.setdefault(key, []).append(row)

    eligible: list[
        tuple[tuple[str, str, str, str], list[dict[str, Any]]]
    ] = []

    for key, day_rows in groups.items():
        complete = [
            row
            for row in day_rows
            if bool(row.get("is_final"))
            and int(row.get("missing_actual_rows") or 0) == 0
        ]

        sources = {
            str(row.get("snapshot_source") or "")
            for row in complete
        }

        if sources == expected_sources and len(complete) == len(expected_sources):
            eligible.append((key, complete))

    eligible.sort(key=lambda item: (item[0][0], item[0][1]))

    overall_by_source: dict[str, dict[str, Any]] = {
        source: {
            "snapshot_source": source,
            "track_label": TRACK_LABELS[source],
            "league_days": 0,
            "outright_wins": 0,
            "ties_for_best": 0,
            "win_share": 0.0,
            "total_event_score": 0,
            "total_hits": 0,
            "total_ab": 0,
            "total_r": 0,
            "total_hr": 0,
            "total_rbi": 0,
            "total_sb": 0,
            "total_bb": 0,
            "total_k": 0,
        }
        for source in expected_sources
    }

    daily: list[dict[str, Any]] = []

    for (
        stat_date,
        instance_alias,
        league_key,
        team_key,
    ), day_rows in eligible:
        ordered_rows = sorted(
            day_rows,
            key=lambda row: TRACK_ORDER[str(row["snapshot_source"])],
        )

        winners = [
            row
            for row in ordered_rows
            if int(row.get("outcome_rank") or 0) == 1
        ]
        win_share_each = 1.0 / len(winners)

        winner_sources = [
            str(row["snapshot_source"])
            for row in winners
        ]
        winner_labels = [
            TRACK_LABELS[source]
            for source in winner_sources
        ]

        by_source = {
            str(row["snapshot_source"]): row
            for row in ordered_rows
        }

        daily.append(
            {
                "stat_date": stat_date,
                "instance_alias": instance_alias,
                "league_key": league_key,
                "team_key": team_key,
                "winner": " + ".join(winner_labels),
                "winner_sources": winner_sources,
                "winner_count": len(winners),
                "win_share_each": win_share_each,
                "ygma_event_score": int(
                    by_source["YGMA_PRE_RMT"].get("event_score") or 0
                ),
                "rmt_event_score": int(
                    by_source["RMT_RECOMMENDED_BASELINE"].get("event_score") or 0
                ),
                "final_event_score": int(
                    by_source["USER_FINAL_LOCKED"].get("event_score") or 0
                ),
            }
        )

        for row in ordered_rows:
            source = str(row["snapshot_source"])
            summary = overall_by_source[source]

            summary["league_days"] += 1
            summary["total_event_score"] += int(row.get("event_score") or 0)
            summary["total_hits"] += int(row.get("total_hits") or 0)
            summary["total_ab"] += int(row.get("total_ab") or 0)
            summary["total_r"] += int(row.get("total_r") or 0)
            summary["total_hr"] += int(row.get("total_hr") or 0)
            summary["total_rbi"] += int(row.get("total_rbi") or 0)
            summary["total_sb"] += int(row.get("total_sb") or 0)
            summary["total_bb"] += int(row.get("total_bb") or 0)
            summary["total_k"] += int(row.get("total_k") or 0)

            if int(row.get("outcome_rank") or 0) == 1:
                summary["win_share"] += win_share_each
                if len(winners) == 1:
                    summary["outright_wins"] += 1
                else:
                    summary["ties_for_best"] += 1

    overall: list[dict[str, Any]] = []

    for source in sorted(expected_sources, key=lambda item: TRACK_ORDER[item]):
        summary = overall_by_source[source]
        league_days = int(summary["league_days"])
        total_ab = int(summary["total_ab"])

        summary["avg_event_score"] = (
            float(summary["total_event_score"]) / league_days
            if league_days
            else 0.0
        )
        summary["batting_avg"] = (
            float(summary["total_hits"]) / total_ab
            if total_ab
            else 0.0
        )
        overall.append(summary)

    overall_lookup = {
        row["snapshot_source"]: row
        for row in overall
    }

    ygma = overall_lookup["YGMA_PRE_RMT"]
    rmt = overall_lookup["RMT_RECOMMENDED_BASELINE"]

    rmt_vs_ygma = {
        "win_share": float(rmt["win_share"]) - float(ygma["win_share"]),
        "total_event_score": (
            int(rmt["total_event_score"])
            - int(ygma["total_event_score"])
        ),
        "avg_event_score": (
            float(rmt["avg_event_score"])
            - float(ygma["avg_event_score"])
        ),
        "batting_avg": (
            float(rmt["batting_avg"])
            - float(ygma["batting_avg"])
        ),
        "r": int(rmt["total_r"]) - int(ygma["total_r"]),
        "hr": int(rmt["total_hr"]) - int(ygma["total_hr"]),
        "rbi": int(rmt["total_rbi"]) - int(ygma["total_rbi"]),
        "sb": int(rmt["total_sb"]) - int(ygma["total_sb"]),
        "bb": int(rmt["total_bb"]) - int(ygma["total_bb"]),
        "k": int(rmt["total_k"]) - int(ygma["total_k"]),
    }

    return {
        "eligible_league_days": len(eligible),
        "daily": daily,
        "overall": overall,
        "rmt_vs_ygma": rmt_vs_ygma,
    }
