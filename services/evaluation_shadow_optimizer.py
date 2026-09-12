from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import inf
from typing import Any, Callable, Mapping, Sequence


PlayerRow = Mapping[str, Any]

PlayerKeyFn = Callable[
    [PlayerRow],
    str,
]

StartableFn = Callable[
    [PlayerRow, str, str],
    bool,
]

SlotValueFn = Callable[
    [str, str, PlayerRow],
    float,
]


@dataclass(frozen=True)
class StabilityOptimizationResult:
    assignment: dict[str, dict[str, Any] | None]

    base_objective: float
    cumulative_penalty: float
    adjusted_objective: float

    displacement_count: int

    incumbent_count_requested: int
    incumbent_count_considered: int
    incumbent_count_selected: int


def _normalize_penalty_curve(
    penalty_curve: Mapping[int, float],
    *,
    max_displacements: int,
) -> dict[int, float]:
    if max_displacements < 0:
        raise ValueError(
            "max_displacements cannot be negative."
        )

    normalized: dict[int, float] = {}

    for key, value in penalty_curve.items():
        count = int(
            key
        )

        penalty = float(
            value
        )

        if count < 0:
            raise ValueError(
                "Penalty-curve displacement counts "
                "cannot be negative."
            )

        if penalty < 0:
            raise ValueError(
                "Penalty values cannot be negative."
            )

        normalized[
            count
        ] = penalty

    required = set(
        range(
            0,
            max_displacements + 1,
        )
    )

    missing = sorted(
        required
        - set(normalized)
    )

    if missing:
        raise ValueError(
            "Penalty curve is missing explicit "
            "cumulative values for displacement "
            "counts: "
            + ", ".join(
                str(value)
                for value in missing
            )
        )

    if normalized[0] != 0.0:
        raise ValueError(
            "Penalty at displacement_count=0 "
            "must equal 0."
        )

    previous = 0.0

    for count in range(
        0,
        max_displacements + 1,
    ):
        penalty = normalized[
            count
        ]

        if penalty < previous:
            raise ValueError(
                "Cumulative stability penalty "
                "must be non-decreasing."
            )

        previous = penalty

    return normalized


def optimize_lineup_with_stability(
    *,
    rows: Sequence[PlayerRow],
    locked_assignments: Mapping[str, str | None],
    incumbent_player_keys: set[str],
    slot_order: Sequence[tuple[str, str]],
    player_key_fn: PlayerKeyFn,
    startable_for_slot_fn: StartableFn,
    slot_value_fn: SlotValueFn,
    cumulative_penalty_by_displacements: Mapping[int, float],
) -> StabilityOptimizationResult:
    """
    Joint lineup optimizer with a cumulative stability penalty.

    The function is intentionally independent of Streamlit, database
    state, Yahoo APIs, and RMT Evaluation persistence.

    Parameters
    ----------
    rows:
        Complete active-owned hitter universe available to the optimizer.

    locked_assignments:
        Mapping of slot_id -> player key. The key must use the same
        identity produced by player_key_fn().

    incumbent_player_keys:
        YGMA/pre-RMT starting-player keys.

        Only incumbents that are present in rows AND legally startable in
        at least one slot are included in the stability penalty. An
        unavailable/impossible incumbent is therefore not penalized as a
        "displacement."

    slot_order:
        Ordered sequence of (slot_id, slot_type).

    cumulative_penalty_by_displacements:
        Explicit total penalty for every possible displacement count.

        Example shape for testing only:

            {
                0: 0.0,
                1: 0.0,
                2: 1.0,
                3: 4.0,
                4: 8.0,
            }

        These are cumulative penalties, not marginal penalties.

    Objective
    ---------
        sum(normal slot optimizer values)
        - cumulative stability penalty(final displacement count)

    The dynamic-programming state includes the number of incumbents
    selected, which allows the final objective to distinguish the first,
    second, third, etc. displacement.
    """

    players = [
        dict(row)
        for row in rows
    ]

    slots = [
        (
            str(slot_id),
            str(slot_type),
        )
        for (
            slot_id,
            slot_type,
        ) in slot_order
    ]

    player_keys: list[str] = []
    player_index: dict[str, int] = {}

    for idx, row in enumerate(
        players
    ):
        key = str(
            player_key_fn(
                row
            )
            or ""
        ).strip()

        if not key:
            raise ValueError(
                "Every optimizer row must have "
                "a non-empty player key."
            )

        if key in player_index:
            raise ValueError(
                "Duplicate player key in optimizer rows: "
                f"{key}"
            )

        player_keys.append(
            key
        )

        player_index[
            key
        ] = idx

    slot_candidates: list[list[int]] = []

    for (
        slot_id,
        slot_type,
    ) in slots:
        eligible = [
            idx
            for idx, row in enumerate(
                players
            )
            if bool(
                startable_for_slot_fn(
                    row,
                    slot_id,
                    slot_type,
                )
            )
        ]

        slot_candidates.append(
            eligible
        )

    candidate_anywhere: set[int] = set()

    for candidates in slot_candidates:
        candidate_anywhere.update(
            candidates
        )

    requested_incumbents = {
        str(key).strip()
        for key in incumbent_player_keys
        if str(key).strip()
    }

    considered_incumbent_indices = {
        idx
        for idx, key in enumerate(
            player_keys
        )
        if (
            key in requested_incumbents
            and idx in candidate_anywhere
        )
    }

    incumbent_count = len(
        considered_incumbent_indices
    )

    penalty_curve = _normalize_penalty_curve(
        cumulative_penalty_by_displacements,
        max_displacements=incumbent_count,
    )

    locked_indices: dict[int, int] = {}

    for slot_pos, (
        slot_id,
        _slot_type,
    ) in enumerate(
        slots
    ):
        locked_key = str(
            locked_assignments.get(
                slot_id
            )
            or ""
        ).strip()

        if not locked_key:
            continue

        idx = player_index.get(
            locked_key
        )

        if (
            idx is not None
            and idx
            in slot_candidates[
                slot_pos
            ]
        ):
            locked_indices[
                slot_pos
            ] = idx

    # Preserve the production optimizer's duplicate-lock cleaning
    # semantics: first slot wins.
    seen_locked: set[int] = set()
    cleaned_locked: dict[int, int] = {}

    for slot_pos in range(
        len(slots)
    ):
        if slot_pos not in locked_indices:
            continue

        idx = locked_indices[
            slot_pos
        ]

        if idx in seen_locked:
            continue

        cleaned_locked[
            slot_pos
        ] = idx

        seen_locked.add(
            idx
        )

    locked_indices = cleaned_locked

    @lru_cache(
        maxsize=None
    )
    def solve(
        slot_pos: int,
        used_mask: int,
        selected_incumbents: int,
    ) -> tuple[
        float,
        tuple[int | None, ...],
    ]:
        if slot_pos >= len(
            slots
        ):
            displacement_count = (
                incumbent_count
                - selected_incumbents
            )

            penalty = penalty_curve[
                displacement_count
            ]

            return (
                -float(
                    penalty
                ),
                (),
            )

        slot_id, slot_type = slots[
            slot_pos
        ]

        if slot_pos in locked_indices:
            idx = locked_indices[
                slot_pos
            ]

            bit = (
                1
                << idx
            )

            if used_mask & bit:
                return (
                    -inf,
                    (),
                )

            selected_increment = (
                1
                if idx
                in considered_incumbent_indices
                else 0
            )

            next_score, next_assignment = solve(
                slot_pos + 1,
                used_mask | bit,
                selected_incumbents
                + selected_increment,
            )

            if next_score == -inf:
                return (
                    -inf,
                    (),
                )

            total = (
                float(
                    slot_value_fn(
                        slot_id,
                        slot_type,
                        players[
                            idx
                        ],
                    )
                )
                + next_score
            )

            return (
                total,
                (
                    idx,
                )
                + next_assignment,
            )

        best_score = -inf
        best_assignment: (
            tuple[
                int | None,
                ...
            ]
            | None
        ) = None

        # Preserve current optimizer ordering:
        # evaluate the empty-slot path first.
        empty_score, empty_assignment = solve(
            slot_pos + 1,
            used_mask,
            selected_incumbents,
        )

        if empty_score > best_score:
            best_score = empty_score

            best_assignment = (
                None,
            ) + empty_assignment

        for idx in slot_candidates[
            slot_pos
        ]:
            bit = (
                1
                << idx
            )

            if used_mask & bit:
                continue

            selected_increment = (
                1
                if idx
                in considered_incumbent_indices
                else 0
            )

            next_score, next_assignment = solve(
                slot_pos + 1,
                used_mask | bit,
                selected_incumbents
                + selected_increment,
            )

            if next_score == -inf:
                continue

            total = (
                float(
                    slot_value_fn(
                        slot_id,
                        slot_type,
                        players[
                            idx
                        ],
                    )
                )
                + next_score
            )

            # Strict greater-than intentionally preserves
            # deterministic first-found tie behavior.
            if total > best_score:
                best_score = total

                best_assignment = (
                    idx,
                ) + next_assignment

        if best_assignment is None:
            return (
                -inf,
                (),
            )

        return (
            best_score,
            best_assignment,
        )

    (
        adjusted_objective,
        assignment_indices,
    ) = solve(
        0,
        0,
        0,
    )

    if adjusted_objective == -inf:
        raise RuntimeError(
            "No legal stability-aware lineup "
            "assignment could be produced."
        )

    assignment: dict[
        str,
        dict[str, Any] | None,
    ] = {}

    selected_indices: set[int] = set()

    base_objective = 0.0

    for slot_pos, (
        slot_id,
        slot_type,
    ) in enumerate(
        slots
    ):
        idx = (
            assignment_indices[
                slot_pos
            ]
            if slot_pos
            < len(
                assignment_indices
            )
            else None
        )

        if idx is None:
            assignment[
                slot_id
            ] = None

            continue

        selected_indices.add(
            idx
        )

        row = dict(
            players[
                idx
            ]
        )

        assignment[
            slot_id
        ] = row

        base_objective += float(
            slot_value_fn(
                slot_id,
                slot_type,
                players[
                    idx
                ],
            )
        )

    selected_incumbents = len(
        selected_indices
        & considered_incumbent_indices
    )

    displacement_count = (
        incumbent_count
        - selected_incumbents
    )

    cumulative_penalty = float(
        penalty_curve[
            displacement_count
        ]
    )

    recalculated_adjusted = (
        base_objective
        - cumulative_penalty
    )

    if abs(
        recalculated_adjusted
        - adjusted_objective
    ) > 1e-9:
        raise RuntimeError(
            "Shadow optimizer objective "
            "recalculation mismatch."
        )

    return StabilityOptimizationResult(
        assignment=assignment,
        base_objective=float(
            base_objective
        ),
        cumulative_penalty=float(
            cumulative_penalty
        ),
        adjusted_objective=float(
            adjusted_objective
        ),
        displacement_count=int(
            displacement_count
        ),
        incumbent_count_requested=len(
            requested_incumbents
        ),
        incumbent_count_considered=int(
            incumbent_count
        ),
        incumbent_count_selected=int(
            selected_incumbents
        ),
    )
