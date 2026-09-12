from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping


SHADOW_MODEL_RUN_TABLE = "rmt.eval_shadow_model_run"
SHADOW_PLAYER_TABLE = "rmt.eval_shadow_player_snapshot"
SHADOW_SCORECARD_TABLE = "rmt.eval_shadow_hitter_scorecard"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, set):
        return sorted(value)

    if isinstance(value, tuple):
        return list(value)

    raise TypeError(
        f"Object of type {type(value).__name__} "
        "is not JSON serializable"
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None

    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        return int(value)
    except Exception:
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {
        "1",
        "true",
        "t",
        "yes",
        "y",
    }:
        return True

    if text in {
        "0",
        "false",
        "f",
        "no",
        "n",
        "",
    }:
        return False

    return None


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _text_array(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        raw = value
    else:
        raw = [value]

    out: list[str] = []

    for item in raw:
        text = str(
            item or ""
        ).strip()

        if (
            text
            and text not in out
        ):
            out.append(text)

    return out


def _first(
    row: Mapping[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        if key in row:
            value = row.get(key)

            if value not in (
                None,
                "",
            ):
                return value

    return None


def validate_shadow_identity(
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
) -> tuple[int, str, int]:
    run_id = int(
        eval_run_id
    )

    model_key = str(
        shadow_model_key or ""
    ).strip()

    version = int(
        shadow_model_version
    )

    if run_id <= 0:
        raise ValueError(
            "eval_run_id must be positive."
        )

    if not model_key:
        raise ValueError(
            "shadow_model_key must be non-empty."
        )

    if version <= 0:
        raise ValueError(
            "shadow_model_version must be positive."
        )

    return (
        run_id,
        model_key,
        version,
    )


def assert_shadow_schema(
    conn,
) -> None:
    required = {
        SHADOW_MODEL_RUN_TABLE,
        SHADOW_PLAYER_TABLE,
        SHADOW_SCORECARD_TABLE,
    }

    with conn.cursor() as cur:
        for table in sorted(
            required
        ):
            cur.execute(
                "SELECT to_regclass(%s)",
                (table,),
            )

            if cur.fetchone()[0] is None:
                raise RuntimeError(
                    f"Required shadow table "
                    f"is missing: {table}"
                )


def upsert_shadow_model_run(
    conn,
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
    baseline_source: str,
    enabled: bool,
    parameters: Mapping[str, Any] | None = None,
    original_rmt_change_count: int | None = None,
    shadow_change_count: int | None = None,
    original_objective: float | None = None,
    shadow_objective_before_penalty: float | None = None,
    shadow_penalty: float | None = None,
    shadow_objective_after_penalty: float | None = None,
) -> None:
    (
        run_id,
        model_key,
        version,
    ) = validate_shadow_identity(
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
    )

    baseline = str(
        baseline_source or ""
    ).strip()

    if not baseline:
        raise ValueError(
            "baseline_source must be non-empty."
        )

    for name, value in (
        (
            "original_rmt_change_count",
            original_rmt_change_count,
        ),
        (
            "shadow_change_count",
            shadow_change_count,
        ),
    ):
        if (
            value is not None
            and int(value) < 0
        ):
            raise ValueError(
                f"{name} cannot be negative."
            )

    if (
        shadow_penalty is not None
        and float(
            shadow_penalty
        ) < 0
    ):
        raise ValueError(
            "shadow_penalty cannot be negative."
        )

    sql = """
        INSERT INTO rmt.eval_shadow_model_run (
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            baseline_source,
            enabled,
            parameters_json,
            original_rmt_change_count,
            shadow_change_count,
            original_objective,
            shadow_objective_before_penalty,
            shadow_penalty,
            shadow_objective_after_penalty,
            created_at_utc,
            updated_at_utc
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s::jsonb,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            now(),
            now()
        )
        ON CONFLICT (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        )
        DO UPDATE SET
            baseline_source =
                EXCLUDED.baseline_source,
            enabled =
                EXCLUDED.enabled,
            parameters_json =
                EXCLUDED.parameters_json,
            original_rmt_change_count =
                EXCLUDED.original_rmt_change_count,
            shadow_change_count =
                EXCLUDED.shadow_change_count,
            original_objective =
                EXCLUDED.original_objective,
            shadow_objective_before_penalty =
                EXCLUDED.shadow_objective_before_penalty,
            shadow_penalty =
                EXCLUDED.shadow_penalty,
            shadow_objective_after_penalty =
                EXCLUDED.shadow_objective_after_penalty,
            updated_at_utc = now()
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                run_id,
                model_key,
                version,
                baseline,
                bool(enabled),
                _json_dumps(
                    dict(
                        parameters
                        or {}
                    )
                ),
                (
                    None
                    if original_rmt_change_count
                    is None
                    else int(
                        original_rmt_change_count
                    )
                ),
                (
                    None
                    if shadow_change_count
                    is None
                    else int(
                        shadow_change_count
                    )
                ),
                _safe_float(
                    original_objective
                ),
                _safe_float(
                    shadow_objective_before_penalty
                ),
                _safe_float(
                    shadow_penalty
                ),
                _safe_float(
                    shadow_objective_after_penalty
                ),
            ),
        )


def replace_shadow_player_snapshot(
    conn,
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    (
        run_id,
        model_key,
        version,
    ) = validate_shadow_identity(
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
    )

    normalized_rows = [
        dict(row)
        for row in rows
    ]

    seen_player_keys: set[str] = set()

    for row in normalized_rows:
        player_key = _safe_text(
            _first(
                row,
                "yahoo_player_key",
                "Yahoo Key",
                "player_key",
            )
        )

        if not player_key:
            continue

        if player_key in seen_player_keys:
            raise ValueError(
                "Duplicate yahoo_player_key in "
                "shadow player snapshot: "
                f"{player_key}"
            )

        seen_player_keys.add(
            player_key
        )

    delete_sql = """
        DELETE FROM
            rmt.eval_shadow_player_snapshot
        WHERE eval_run_id = %s
          AND shadow_model_key = %s
          AND shadow_model_version = %s
    """

    insert_sql = """
        INSERT INTO rmt.eval_shadow_player_snapshot (
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            row_ordinal,
            yahoo_player_key,
            player_name,
            mlb_team_abbr,
            ygma_is_starting,
            ygma_selected_position,
            rmt_is_starting,
            rmt_selected_position,
            shadow_is_starting,
            shadow_selected_position,
            eligible_positions,
            ranking,
            ranking_band,
            baseline_points,
            pitcher_points,
            handedness_points,
            home_away_points,
            day_night_points,
            recent_form_points,
            rank_reliability_points,
            status_risk_points,
            lineup_points,
            game_status,
            lineup_status,
            status_display,
            game_started,
            is_keeper,
            is_undroppable,
            policy_status,
            is_ygma_incumbent,
            shadow_displacement_number,
            shadow_switch_penalty,
            slot_objective_before_penalty,
            slot_objective_after_penalty,
            row_json,
            created_at_utc
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s::text[],
            %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s,
            %s, %s, %s, %s,
            %s::jsonb,
            now()
        )
    """

    with conn.cursor() as cur:
        cur.execute(
            delete_sql,
            (
                run_id,
                model_key,
                version,
            ),
        )

        for ordinal, row in enumerate(
            normalized_rows,
            start=1,
        ):
            player_key = _safe_text(
                _first(
                    row,
                    "yahoo_player_key",
                    "Yahoo Key",
                    "player_key",
                )
            )

            player_name = _safe_text(
                _first(
                    row,
                    "player_name",
                    "full_name",
                    "Player",
                    "name",
                )
            )

            mlb_team_abbr = _safe_text(
                _first(
                    row,
                    "mlb_team_abbr",
                    "editorial_team_abbr",
                    "Team",
                    "MLB",
                )
            )

            eligible_positions = _text_array(
                _first(
                    row,
                    "eligible_positions",
                    "Eligible Positions",
                )
            )

            raw_payload = dict(
                row
            )

            cur.execute(
                insert_sql,
                (
                    run_id,
                    model_key,
                    version,
                    ordinal,
                    player_key,
                    player_name,
                    mlb_team_abbr,
                    bool(
                        _safe_bool(
                            row.get(
                                "ygma_is_starting"
                            )
                        )
                    ),
                    _safe_text(
                        row.get(
                            "ygma_selected_position"
                        )
                    ),
                    bool(
                        _safe_bool(
                            row.get(
                                "rmt_is_starting"
                            )
                        )
                    ),
                    _safe_text(
                        row.get(
                            "rmt_selected_position"
                        )
                    ),
                    bool(
                        _safe_bool(
                            row.get(
                                "shadow_is_starting"
                            )
                        )
                    ),
                    _safe_text(
                        row.get(
                            "shadow_selected_position"
                        )
                    ),
                    eligible_positions,
                    _safe_float(
                        _first(
                            row,
                            "ranking",
                            "Rank",
                            "Projected Rank",
                        )
                    ),
                    _safe_text(
                        _first(
                            row,
                            "ranking_band",
                            "Band",
                        )
                    ),
                    _safe_float(
                        row.get(
                            "baseline_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "pitcher_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "handedness_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "home_away_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "day_night_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "recent_form_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "rank_reliability_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "status_risk_points"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "lineup_points"
                        )
                    ),
                    _safe_text(
                        row.get(
                            "game_status"
                        )
                    ),
                    _safe_text(
                        row.get(
                            "lineup_status"
                        )
                    ),
                    _safe_text(
                        _first(
                            row,
                            "status_display",
                            "status",
                        )
                    ),
                    _safe_bool(
                        row.get(
                            "game_started"
                        )
                    ),
                    _safe_bool(
                        row.get(
                            "is_keeper"
                        )
                    ),
                    _safe_bool(
                        row.get(
                            "is_undroppable"
                        )
                    ),
                    _safe_text(
                        row.get(
                            "policy_status"
                        )
                    ),
                    bool(
                        _safe_bool(
                            row.get(
                                "is_ygma_incumbent"
                            )
                        )
                    ),
                    _safe_int(
                        row.get(
                            "shadow_displacement_number"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "shadow_switch_penalty"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "slot_objective_before_penalty"
                        )
                    ),
                    _safe_float(
                        row.get(
                            "slot_objective_after_penalty"
                        )
                    ),
                    _json_dumps(
                        raw_payload
                    ),
                ),
            )

    return len(
        normalized_rows
    )


def persist_shadow_snapshot(
    conn,
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
    baseline_source: str,
    enabled: bool,
    parameters: Mapping[str, Any] | None,
    player_rows: Iterable[Mapping[str, Any]],
    original_rmt_change_count: int | None = None,
    shadow_change_count: int | None = None,
    original_objective: float | None = None,
    shadow_objective_before_penalty: float | None = None,
    shadow_penalty: float | None = None,
    shadow_objective_after_penalty: float | None = None,
) -> int:
    assert_shadow_schema(
        conn
    )

    upsert_shadow_model_run(
        conn,
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
        baseline_source=baseline_source,
        enabled=enabled,
        parameters=parameters,
        original_rmt_change_count=original_rmt_change_count,
        shadow_change_count=shadow_change_count,
        original_objective=original_objective,
        shadow_objective_before_penalty=shadow_objective_before_penalty,
        shadow_penalty=shadow_penalty,
        shadow_objective_after_penalty=shadow_objective_after_penalty,
    )

    return replace_shadow_player_snapshot(
        conn,
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
        rows=player_rows,
    )


def fetch_shadow_model_run(
    conn,
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
) -> dict[str, Any] | None:
    (
        run_id,
        model_key,
        version,
    ) = validate_shadow_identity(
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
    )

    sql = """
        SELECT
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            baseline_source,
            enabled,
            parameters_json,
            original_rmt_change_count,
            shadow_change_count,
            original_objective,
            shadow_objective_before_penalty,
            shadow_penalty,
            shadow_objective_after_penalty,
            created_at_utc,
            updated_at_utc
        FROM rmt.eval_shadow_model_run
        WHERE eval_run_id = %s
          AND shadow_model_key = %s
          AND shadow_model_version = %s
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                run_id,
                model_key,
                version,
            ),
        )

        row = cur.fetchone()

        if row is None:
            return None

        columns = [
            d.name
            for d in cur.description
        ]

    return dict(
        zip(
            columns,
            row,
        )
    )


def fetch_shadow_player_snapshot(
    conn,
    *,
    eval_run_id: int,
    shadow_model_key: str,
    shadow_model_version: int,
) -> list[dict[str, Any]]:
    (
        run_id,
        model_key,
        version,
    ) = validate_shadow_identity(
        eval_run_id=eval_run_id,
        shadow_model_key=shadow_model_key,
        shadow_model_version=shadow_model_version,
    )

    sql = """
        SELECT
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            row_ordinal,
            yahoo_player_key,
            player_name,
            mlb_team_abbr,
            ygma_is_starting,
            ygma_selected_position,
            rmt_is_starting,
            rmt_selected_position,
            shadow_is_starting,
            shadow_selected_position,
            eligible_positions,
            ranking,
            ranking_band,
            baseline_points,
            pitcher_points,
            handedness_points,
            home_away_points,
            day_night_points,
            recent_form_points,
            rank_reliability_points,
            status_risk_points,
            lineup_points,
            game_status,
            lineup_status,
            status_display,
            game_started,
            is_keeper,
            is_undroppable,
            policy_status,
            is_ygma_incumbent,
            shadow_displacement_number,
            shadow_switch_penalty,
            slot_objective_before_penalty,
            slot_objective_after_penalty,
            row_json,
            created_at_utc
        FROM rmt.eval_shadow_player_snapshot
        WHERE eval_run_id = %s
          AND shadow_model_key = %s
          AND shadow_model_version = %s
        ORDER BY row_ordinal
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                run_id,
                model_key,
                version,
            ),
        )

        columns = [
            d.name
            for d in cur.description
        ]

        return [
            dict(
                zip(
                    columns,
                    row,
                )
            )
            for row in cur.fetchall()
        ]
