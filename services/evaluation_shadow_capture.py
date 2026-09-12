from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from services.db import get_connection
from services.evaluation_shadow import persist_shadow_snapshot
from services.evaluation_shadow_optimizer import (
    optimize_lineup_with_stability,
)


SHADOW_MODEL_KEY = "stability_capture_zero_penalty"
SHADOW_MODEL_VERSION = 1

ELIGIBLE_LEAGUES = {
    "469.l.41640",  # MLF
    "469.l.60688",  # MiLF
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _eligible_positions(
    row: Mapping[str, Any],
) -> list[str]:
    raw = row.get("eligible_positions")

    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif raw not in (None, ""):
        values = re.split(r"[,/;|]+", str(raw))
    else:
        values = re.split(
            r"[,/;|]+",
            str(
                row.get("eligible_display")
                or row.get("Eligible Pos.")
                or row.get("Eligible")
                or ""
            ),
        )

    out: list[str] = []

    for value in values:
        pos = _text(value).upper()

        if pos and pos not in out:
            out.append(pos)

    return out


def _assignment_signature(
    assignment: Mapping[
        str,
        Mapping[str, Any] | None,
    ],
    slot_order: Sequence[tuple[str, str]],
    player_key_fn: Callable[
        [Mapping[str, Any]],
        str,
    ],
) -> tuple[tuple[str, str | None], ...]:
    return tuple(
        (
            slot_id,
            (
                _text(player_key_fn(row))
                if row is not None
                else None
            ),
        )
        for slot_id, _slot_type in slot_order
        for row in [assignment.get(slot_id)]
    )


def _assignment_yahoo_slots(
    assignment: Mapping[
        str,
        Mapping[str, Any] | None,
    ],
    slot_order: Sequence[tuple[str, str]],
) -> dict[str, str]:
    out: dict[str, str] = {}

    for slot_id, _slot_type in slot_order:
        row = assignment.get(slot_id)

        if row is None:
            continue

        yahoo_key = _text(
            row.get("yahoo_player_key")
        )

        if not yahoo_key:
            raise RuntimeError(
                "Selected optimizer player is missing "
                "yahoo_player_key: "
                + _text(
                    row.get("full_name")
                    or row.get("player_name")
                    or row.get("player_display")
                )
            )

        if yahoo_key in out:
            raise RuntimeError(
                "Duplicate selected Yahoo player key: "
                + yahoo_key
            )

        out[yahoo_key] = slot_id

    return out


def _change_count(
    left: set[str],
    right: set[str],
) -> int:
    return max(
        len(left - right),
        len(right - left),
    )


def _load_eval_and_ygma(
    conn,
    *,
    eval_run_id: int,
    league_key: str,
    team_key: str,
    eval_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                eval_run_id,
                league_key,
                team_key,
                instance_alias,
                eval_date,
                refresh_label
            FROM rmt.eval_run
            WHERE eval_run_id = %s
            """,
            (int(eval_run_id),),
        )

        row = cur.fetchone()

        if row is None:
            raise RuntimeError(
                f"eval_run_id={eval_run_id} not found."
            )

        identity = {
            "eval_run_id": int(row[0]),
            "league_key": str(row[1]),
            "team_key": str(row[2]),
            "instance_alias": str(row[3] or ""),
            "eval_date": str(row[4]),
            "refresh_label": str(row[5] or ""),
        }

        if identity["league_key"] != league_key:
            raise RuntimeError(
                "Evaluation league mismatch."
            )

        if identity["team_key"] != team_key:
            raise RuntimeError(
                "Evaluation team mismatch."
            )

        if identity["eval_date"] != eval_date:
            raise RuntimeError(
                "Evaluation date mismatch."
            )

        cur.execute(
            """
            SELECT
                row_ordinal,
                selected_position,
                yahoo_player_key,
                player_name,
                mlb_team_abbr,
                row_json
            FROM rmt.eval_lineup_snapshot
            WHERE eval_run_id = %s
              AND snapshot_source = 'YGMA_PRE_RMT'
              AND is_starting_slot
              AND yahoo_player_key IS NOT NULL
              AND (
                    upper(selected_position) IN (
                        'C','1B','2B','3B',
                        'SS','IF','UTIL'
                    )
                    OR upper(selected_position) LIKE 'OF%%'
                  )
            ORDER BY row_ordinal
            """,
            (int(eval_run_id),),
        )

        rows = [
            {
                "row_ordinal": int(dbrow[0]),
                "selected_position": _text(dbrow[1]),
                "yahoo_player_key": _text(dbrow[2]),
                "player_name": _text(dbrow[3]),
                "mlb_team_abbr": _text(dbrow[4]),
                "row_json": (
                    dbrow[5]
                    if isinstance(dbrow[5], dict)
                    else {}
                ),
            }
            for dbrow in cur.fetchall()
        ]

    if not rows:
        raise RuntimeError(
            "Frozen YGMA starting hitter snapshot "
            f"is empty for eval_run_id={eval_run_id}."
        )

    return identity, rows


def capture_zero_penalty_shadow(
    *,
    ctx_obj: Mapping[str, Any],
    eval_run_id: int,
    shadow_input: Mapping[str, Any],
    player_key_fn: Callable[
        [Mapping[str, Any]],
        str,
    ],
    startable_for_slot_fn: Callable[
        [Mapping[str, Any], str, str],
        bool,
    ],
    slot_value_fn: Callable[
        [str, str, Mapping[str, Any]],
        float,
    ],
) -> dict[str, Any]:

    league_key = _text(
        ctx_obj.get("league_key")
    )
    team_key = _text(
        ctx_obj.get("team_key")
    )
    eval_date = _text(
        ctx_obj.get("as_of_date")
    )

    if league_key not in ELIGIBLE_LEAGUES:
        return {
            "status": "SKIPPED_LEAGUE",
            "eval_run_id": int(eval_run_id),
            "league_key": league_key,
        }

    active_owned_raw = shadow_input.get(
        "active_owned"
    )
    locks_raw = shadow_input.get("locks")
    baseline_raw = shadow_input.get(
        "baseline_assignment"
    )
    slot_order_raw = shadow_input.get(
        "slot_order"
    )

    if not isinstance(active_owned_raw, list):
        raise RuntimeError(
            "Shadow sidecar missing active_owned."
        )

    if not isinstance(locks_raw, dict):
        raise RuntimeError(
            "Shadow sidecar missing locks."
        )

    if not isinstance(baseline_raw, dict):
        raise RuntimeError(
            "Shadow sidecar missing baseline_assignment."
        )

    if not isinstance(slot_order_raw, list):
        raise RuntimeError(
            "Shadow sidecar missing slot_order."
        )

    active_owned = [
        dict(row)
        for row in active_owned_raw
    ]

    locks = dict(locks_raw)

    baseline_assignment = {
        str(slot_id): (
            None
            if row is None
            else dict(row)
        )
        for slot_id, row in baseline_raw.items()
    }

    slot_order = [
        (str(slot_id), str(slot_type))
        for slot_id, slot_type in slot_order_raw
    ]

    active_by_yahoo: dict[
        str,
        dict[str, Any],
    ] = {}

    for row in active_owned:
        yahoo_key = _text(
            row.get("yahoo_player_key")
        )

        if not yahoo_key:
            continue

        if yahoo_key in active_by_yahoo:
            raise RuntimeError(
                "Duplicate active-owned Yahoo key: "
                + yahoo_key
            )

        active_by_yahoo[yahoo_key] = row

    with get_connection() as conn:
        try:
            identity, ygma_rows = _load_eval_and_ygma(
                conn,
                eval_run_id=int(eval_run_id),
                league_key=league_key,
                team_key=team_key,
                eval_date=eval_date,
            )

            ygma_by_yahoo = {
                row["yahoo_player_key"]: row
                for row in ygma_rows
            }

            ygma_yahoo_keys = set(
                ygma_by_yahoo
            )

            incumbent_player_keys: set[str] = set()
            unmatched_ygma: list[dict[str, Any]] = []

            for ygma_row in ygma_rows:
                yahoo_key = ygma_row[
                    "yahoo_player_key"
                ]

                active_row = active_by_yahoo.get(
                    yahoo_key
                )

                if active_row is None:
                    unmatched_ygma.append(
                        {
                            "yahoo_player_key": yahoo_key,
                            "player_name": ygma_row[
                                "player_name"
                            ],
                            "selected_position": ygma_row[
                                "selected_position"
                            ],
                        }
                    )
                    continue

                optimizer_key = _text(
                    player_key_fn(active_row)
                )

                if not optimizer_key:
                    raise RuntimeError(
                        "Matched YGMA incumbent has "
                        "empty optimizer key: "
                        + yahoo_key
                    )

                incumbent_player_keys.add(
                    optimizer_key
                )

            zero_curve = {
                count: 0.0
                for count in range(
                    len(incumbent_player_keys) + 1
                )
            }

            shadow_result = (
                optimize_lineup_with_stability(
                    rows=active_owned,
                    locked_assignments=locks,
                    incumbent_player_keys=
                        incumbent_player_keys,
                    slot_order=slot_order,
                    player_key_fn=player_key_fn,
                    startable_for_slot_fn=
                        startable_for_slot_fn,
                    slot_value_fn=slot_value_fn,
                    cumulative_penalty_by_displacements=
                        zero_curve,
                )
            )

            normal_signature = _assignment_signature(
                baseline_assignment,
                slot_order,
                player_key_fn,
            )

            shadow_signature = _assignment_signature(
                shadow_result.assignment,
                slot_order,
                player_key_fn,
            )

            if shadow_signature != normal_signature:
                raise RuntimeError(
                    "ZERO_PENALTY_PARITY_FAILURE "
                    f"eval_run_id={eval_run_id} "
                    f"normal={normal_signature} "
                    f"shadow={shadow_signature}"
                )

            rmt_slots = _assignment_yahoo_slots(
                baseline_assignment,
                slot_order,
            )

            shadow_slots = _assignment_yahoo_slots(
                shadow_result.assignment,
                slot_order,
            )

            rmt_keys = set(rmt_slots)
            shadow_keys = set(shadow_slots)

            original_change_count = _change_count(
                ygma_yahoo_keys,
                rmt_keys,
            )

            shadow_change_count = _change_count(
                ygma_yahoo_keys,
                shadow_keys,
            )

            slot_type_by_id = {
                slot_id: slot_type
                for slot_id, slot_type in slot_order
            }

            original_objective = 0.0

            for slot_id, slot_type in slot_order:
                row = baseline_assignment.get(
                    slot_id
                )

                if row is not None:
                    original_objective += float(
                        slot_value_fn(
                            slot_id,
                            slot_type,
                            row,
                        )
                    )

            displaced = [
                row["yahoo_player_key"]
                for row in ygma_rows
                if row["yahoo_player_key"]
                not in shadow_keys
            ]

            displacement_number = {
                yahoo_key: idx
                for idx, yahoo_key in enumerate(
                    displaced,
                    start=1,
                )
            }

            slot_objectives: dict[
                str,
                float,
            ] = {}

            for yahoo_key, slot_id in shadow_slots.items():
                row = active_by_yahoo.get(
                    yahoo_key
                )

                if row is None:
                    raise RuntimeError(
                        "Shadow selected player missing "
                        "from active_owned: "
                        + yahoo_key
                    )

                slot_objectives[yahoo_key] = float(
                    slot_value_fn(
                        slot_id,
                        slot_type_by_id[slot_id],
                        row,
                    )
                )

            player_rows: list[dict[str, Any]] = []

            for active_row in active_owned:
                out = dict(active_row)

                yahoo_key = _text(
                    active_row.get(
                        "yahoo_player_key"
                    )
                )

                ygma_row = (
                    ygma_by_yahoo.get(yahoo_key)
                    if yahoo_key
                    else None
                )

                out["player_name"] = (
                    active_row.get("full_name")
                    or active_row.get("player_name")
                    or active_row.get("player_display")
                    or _text(
                        player_key_fn(active_row)
                    )
                )

                out["eligible_positions"] = (
                    _eligible_positions(active_row)
                )

                out["ygma_is_starting"] = bool(
                    ygma_row
                )
                out["ygma_selected_position"] = (
                    ygma_row["selected_position"]
                    if ygma_row
                    else None
                )

                out["rmt_is_starting"] = bool(
                    yahoo_key
                    and yahoo_key in rmt_slots
                )
                out["rmt_selected_position"] = (
                    rmt_slots.get(yahoo_key)
                    if yahoo_key
                    else None
                )

                out["shadow_is_starting"] = bool(
                    yahoo_key
                    and yahoo_key in shadow_slots
                )
                out["shadow_selected_position"] = (
                    shadow_slots.get(yahoo_key)
                    if yahoo_key
                    else None
                )

                out["is_ygma_incumbent"] = bool(
                    ygma_row
                )

                out[
                    "shadow_displacement_number"
                ] = (
                    displacement_number.get(yahoo_key)
                    if yahoo_key
                    else None
                )

                out["shadow_switch_penalty"] = 0.0

                slot_objective = (
                    slot_objectives.get(yahoo_key)
                    if yahoo_key
                    else None
                )

                out[
                    "slot_objective_before_penalty"
                ] = slot_objective
                out[
                    "slot_objective_after_penalty"
                ] = slot_objective

                player_rows.append(out)

            persisted_count = persist_shadow_snapshot(
                conn,
                eval_run_id=int(eval_run_id),
                shadow_model_key=SHADOW_MODEL_KEY,
                shadow_model_version=
                    SHADOW_MODEL_VERSION,
                baseline_source="YGMA_PRE_RMT",
                enabled=False,
                parameters={
                    "mode": "capture_only",
                    "penalty_type":
                        "cumulative_displacements",
                    "penalty_curve": zero_curve,
                    "zero_penalty_parity": True,
                    "eligible_leagues": sorted(
                        ELIGIBLE_LEAGUES
                    ),
                    "ygma_starting_hitter_count":
                        len(ygma_yahoo_keys),
                    "matched_incumbent_count":
                        len(incumbent_player_keys),
                    "unmatched_ygma_count":
                        len(unmatched_ygma),
                    "unmatched_ygma":
                        unmatched_ygma,
                    "slot_order": [
                        [slot_id, slot_type]
                        for slot_id, slot_type
                        in slot_order
                    ],
                    "eval_identity": identity,
                },
                player_rows=player_rows,
                original_rmt_change_count=
                    original_change_count,
                shadow_change_count=
                    shadow_change_count,
                original_objective=
                    original_objective,
                shadow_objective_before_penalty=
                    shadow_result.base_objective,
                shadow_penalty=
                    shadow_result.cumulative_penalty,
                shadow_objective_after_penalty=
                    shadow_result.adjusted_objective,
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    return {
        "status": "PERSISTED",
        "eval_run_id": int(eval_run_id),
        "league_key": league_key,
        "model_key": SHADOW_MODEL_KEY,
        "model_version": SHADOW_MODEL_VERSION,
        "player_rows": int(persisted_count),
        "ygma_starting_hitter_count":
            len(ygma_yahoo_keys),
        "matched_incumbent_count":
            len(incumbent_player_keys),
        "unmatched_ygma_count":
            len(unmatched_ygma),
        "original_rmt_change_count":
            int(original_change_count),
        "shadow_change_count":
            int(shadow_change_count),
        "zero_penalty_parity": True,
    }
