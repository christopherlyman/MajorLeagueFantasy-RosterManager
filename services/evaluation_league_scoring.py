from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import requests

from scripts.yahoo.auth import get_access_token
from services.h2h_matchup import fetch_h2h_week_matchup


EXPECTED_SOURCES = {
    "YGMA_PRE_RMT",
    "RMT_RECOMMENDED_BASELINE",
    "USER_FINAL_LOCKED",
}

TRACK_LABELS = {
    "YGMA_PRE_RMT": "YGMA",
    "RMT_RECOMMENDED_BASELINE": "RMT Baseline",
    "USER_FINAL_LOCKED": "Final Lineup",
}

STAT_FIELDS = {
    "R": "total_r",
    "HR": "total_hr",
    "RBI": "total_rbi",
    "SB": "total_sb",
    "BB": "total_bb",
    "K": "total_k",
}


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _yahoo_get(url: str) -> dict[str, Any]:
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=16)
def fetch_yahoo_hitter_evaluation_rules(
    league_key: str,
) -> dict[str, Any]:
    payload = _yahoo_get(
        "https://fantasysports.yahooapis.com/fantasy/v2/"
        f"league/{league_key}/settings?format=json"
    )

    settings = None
    scoring_type = None

    for node in _walk(payload):
        if not isinstance(node, dict):
            continue

        if scoring_type is None and node.get("scoring_type"):
            scoring_type = str(node["scoring_type"]).strip().lower()

        if isinstance(node.get("stat_categories"), dict):
            settings = node
            break

    if settings is None:
        raise RuntimeError(
            f"Yahoo settings for {league_key} did not contain stat_categories"
        )

    if not scoring_type:
        scoring_type = str(settings.get("scoring_type") or "").strip().lower()

    categories: list[str] = []
    directions: dict[str, bool] = {}

    stat_categories = settings["stat_categories"]
    stats = stat_categories.get("stats") or []

    for wrapper in stats:
        if not isinstance(wrapper, dict):
            continue

        stat = wrapper.get("stat")
        if not isinstance(stat, dict):
            continue

        if str(stat.get("enabled") or "") != "1":
            continue

        if str(stat.get("group") or "").lower() != "batting":
            continue

        if str(stat.get("is_only_display_stat") or "") == "1":
            continue

        category = str(
            stat.get("display_name")
            or stat.get("abbr")
            or ""
        ).strip()

        if not category:
            continue

        categories.append(category)

        # Yahoo sort_order=1 means higher is better.
        # Yahoo sort_order=0 means lower is better.
        directions[category] = str(stat.get("sort_order") or "1") != "0"

    if not categories:
        raise RuntimeError(
            f"Yahoo settings for {league_key} did not contain active batting categories"
        )

    return {
        "league_key": league_key,
        "scoring_type": scoring_type,
        "categories": categories,
        "higher_is_better": directions,
        "source": "yahoo_live_settings",
    }


def _find_week_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue

        if all(
            key in node
            for key in ("week", "week_start", "week_end")
        ):
            return {
                "week": int(node["week"]),
                "week_start": str(node["week_start"]),
                "week_end": str(node["week_end"]),
                "is_playoffs": str(node.get("is_playoffs") or "0") == "1",
                "is_consolation": str(node.get("is_consolation") or "0") == "1",
            }

    return None


@lru_cache(maxsize=128)
def fetch_yahoo_h2h_week(
    league_key: str,
    week: int,
) -> dict[str, Any]:
    payload = _yahoo_get(
        "https://fantasysports.yahooapis.com/fantasy/v2/"
        f"league/{league_key}/scoreboard;week={int(week)}?format=json"
    )

    metadata = _find_week_metadata(payload)
    if metadata is None:
        raise RuntimeError(
            f"Yahoo scoreboard for {league_key} week {week} "
            "did not contain week boundaries"
        )

    return metadata


@lru_cache(maxsize=16)
def _current_week_anchor(league_key: str) -> dict[str, Any]:
    payload = _yahoo_get(
        "https://fantasysports.yahooapis.com/fantasy/v2/"
        f"league/{league_key}/scoreboard?format=json"
    )

    metadata = _find_week_metadata(payload)
    if metadata is None:
        raise RuntimeError(
            f"Yahoo current scoreboard for {league_key} "
            "did not contain week boundaries"
        )

    return metadata


@lru_cache(maxsize=512)
def resolve_yahoo_h2h_week_for_date(
    league_key: str,
    stat_date: str,
) -> dict[str, Any]:
    target = datetime.strptime(stat_date, "%Y-%m-%d").date()
    anchor = _current_week_anchor(league_key)

    anchor_start = date.fromisoformat(anchor["week_start"])
    anchor_end = date.fromisoformat(anchor["week_end"])
    anchor_week = int(anchor["week"])

    span_days = max(
        1,
        (anchor_end - anchor_start).days + 1,
    )

    candidate = anchor_week + math.floor(
        (target - anchor_start).days / span_days
    )

    checked: set[int] = set()

    # Candidate math is only an optimization.
    # Every assignment is verified against Yahoo's returned boundaries.
    for week in (
        candidate,
        candidate - 1,
        candidate + 1,
        candidate - 2,
        candidate + 2,
        candidate - 3,
        candidate + 3,
    ):
        if week < 1 or week in checked:
            continue

        checked.add(week)

        try:
            metadata = fetch_yahoo_h2h_week(league_key, week)
        except Exception:
            continue

        start = date.fromisoformat(metadata["week_start"])
        end = date.fromisoformat(metadata["week_end"])

        if start <= target <= end:
            return metadata

    # Rare fallback for unusual Yahoo opening/playoff week lengths.
    for week in range(1, anchor_week + 1):
        if week in checked:
            continue

        try:
            metadata = fetch_yahoo_h2h_week(league_key, week)
        except Exception:
            continue

        start = date.fromisoformat(metadata["week_start"])
        end = date.fromisoformat(metadata["week_end"])

        if start <= target <= end:
            return metadata

    raise RuntimeError(
        f"Could not map {stat_date} to a Yahoo week for {league_key}"
    )


def _empty_totals() -> dict[str, int]:
    return {
        "total_hits": 0,
        "total_ab": 0,
        "total_r": 0,
        "total_hr": 0,
        "total_rbi": 0,
        "total_sb": 0,
        "total_bb": 0,
        "total_k": 0,
    }


def _add_row(
    totals: dict[str, int],
    row: dict[str, Any],
) -> None:
    for key in totals:
        totals[key] += int(row.get(key) or 0)


def _category_value(
    totals: dict[str, int],
    category: str,
) -> float:
    if category == "AVG":
        ab = int(totals["total_ab"])
        return (
            float(totals["total_hits"]) / ab
            if ab
            else 0.0
        )

    field = STAT_FIELDS.get(category)
    if not field:
        raise RuntimeError(
            f"Unsupported hitter evaluation category: {category}"
        )

    return float(totals[field])


def _compare_pair(
    left: dict[str, int],
    right: dict[str, int],
    rules: dict[str, Any],
) -> dict[str, Any]:
    wins = 0
    losses = 0
    ties = 0
    category_results: list[dict[str, Any]] = []

    for category in rules["categories"]:
        left_value = _category_value(left, category)
        right_value = _category_value(right, category)
        higher_is_better = bool(
            rules["higher_is_better"][category]
        )

        if abs(left_value - right_value) < 1e-12:
            outcome = "T"
            ties += 1
        else:
            left_better = (
                left_value > right_value
                if higher_is_better
                else left_value < right_value
            )

            if left_better:
                outcome = "W"
                wins += 1
            else:
                outcome = "L"
                losses += 1

        category_results.append(
            {
                "category": category,
                "outcome": outcome,
                "left_value": left_value,
                "right_value": right_value,
                "higher_is_better": higher_is_better,
            }
        )

    if wins > losses:
        overall_result = "Win"
    elif wins < losses:
        overall_result = "Loss"
    else:
        overall_result = "Tie"

    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "record": f"{wins}-{losses}-{ties}",
        "result": overall_result,
        "categories": category_results,
        "detail": " | ".join(
            f"{item['category']} {item['outcome']}"
            for item in category_results
        ),
    }


def _yahoo_stats_to_totals(
    stats: dict[str, Any],
) -> dict[str, int]:
    def _int_value(key: str) -> int:
        return int(float(stats.get(key) or 0))

    return {
        "total_hits": _int_value("H"),
        "total_ab": _int_value("AB"),
        "total_r": _int_value("R"),
        "total_hr": _int_value("HR"),
        "total_rbi": _int_value("RBI"),
        "total_sb": _int_value("SB"),
        "total_bb": _int_value("BB"),
        "total_k": _int_value("K"),
    }


def _final_week_integrity(
    evaluation_totals: dict[str, int],
    yahoo_totals: dict[str, int],
) -> dict[str, Any]:
    fields = [
        ("H", "total_hits"),
        ("AB", "total_ab"),
        ("R", "total_r"),
        ("HR", "total_hr"),
        ("RBI", "total_rbi"),
        ("SB", "total_sb"),
        ("BB", "total_bb"),
        ("K", "total_k"),
    ]

    mismatches = []

    for label, field in fields:
        evaluation_value = int(
            evaluation_totals.get(field) or 0
        )
        yahoo_value = int(
            yahoo_totals.get(field) or 0
        )

        if evaluation_value != yahoo_value:
            mismatches.append(
                {
                    "stat": label,
                    "evaluation": evaluation_value,
                    "yahoo": yahoo_value,
                }
            )

    eval_ab = int(
        evaluation_totals.get("total_ab") or 0
    )
    yahoo_ab = int(
        yahoo_totals.get("total_ab") or 0
    )

    eval_avg = (
        float(evaluation_totals["total_hits"])
        / eval_ab
        if eval_ab
        else 0.0
    )
    yahoo_avg = (
        float(yahoo_totals["total_hits"])
        / yahoo_ab
        if yahoo_ab
        else 0.0
    )

    return {
        "status": "PASS" if not mismatches else "FAIL",
        "mismatches": mismatches,
        "evaluation_avg": eval_avg,
        "yahoo_avg": yahoo_avg,
    }


def _increment_record(
    record: dict[str, int],
    comparison: dict[str, Any],
) -> None:
    result = comparison["result"]

    if result == "Win":
        record["wins"] += 1
    elif result == "Loss":
        record["losses"] += 1
    else:
        record["ties"] += 1


def _eligible_days(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str],
        list[dict[str, Any]],
    ] = {}

    for row in rows:
        key = (
            str(row.get("stat_date") or ""),
            str(row.get("instance_alias") or ""),
            str(row.get("league_key") or ""),
            str(row.get("team_key") or ""),
        )
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []

    for (
        stat_date,
        instance_alias,
        league_key,
        team_key,
    ), day_rows in grouped.items():
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

        if (
            sources != EXPECTED_SOURCES
            or len(complete) != len(EXPECTED_SOURCES)
        ):
            continue

        out.append(
            {
                "stat_date": stat_date,
                "instance_alias": instance_alias,
                "league_key": league_key,
                "team_key": team_key,
                "rows": {
                    str(row["snapshot_source"]): row
                    for row in complete
                },
            }
        )

    out.sort(
        key=lambda item: (
            item["stat_date"],
            item["instance_alias"],
        )
    )

    return out


def _aggregate_sources(
    days: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    totals = {
        source: _empty_totals()
        for source in EXPECTED_SOURCES
    }

    for day in days:
        for source, row in day["rows"].items():
            _add_row(totals[source], row)

    return totals


def summarize_league_aware_hitter_evaluation(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible = _eligible_days(rows)

    by_league: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = {}

    for day in eligible:
        key = (
            day["instance_alias"],
            day["league_key"],
            day["team_key"],
        )
        by_league.setdefault(key, []).append(day)

    league_results: list[dict[str, Any]] = []
    errors: list[str] = []

    completed_h2h = {
        "rmt": {"wins": 0, "losses": 0, "ties": 0},
        "final": {"wins": 0, "losses": 0, "ties": 0},
    }

    completed_vs_opponent = {
        "ygma": {"wins": 0, "losses": 0, "ties": 0},
        "rmt": {"wins": 0, "losses": 0, "ties": 0},
        "final": {"wins": 0, "losses": 0, "ties": 0},
    }

    for (
        instance_alias,
        league_key,
        team_key,
    ), league_days in sorted(by_league.items()):
        try:
            rules = fetch_yahoo_hitter_evaluation_rules(
                league_key
            )
        except Exception as exc:
            errors.append(
                f"{instance_alias}: could not load Yahoo scoring rules: {exc}"
            )
            continue

        scoring_type = str(
            rules.get("scoring_type") or ""
        ).lower()

        if scoring_type == "roto":
            totals = _aggregate_sources(league_days)

            rmt_vs_ygma = _compare_pair(
                totals["RMT_RECOMMENDED_BASELINE"],
                totals["YGMA_PRE_RMT"],
                rules,
            )
            final_vs_ygma = _compare_pair(
                totals["USER_FINAL_LOCKED"],
                totals["YGMA_PRE_RMT"],
                rules,
            )

            dates = sorted(
                day["stat_date"]
                for day in league_days
            )

            league_results.append(
                {
                    "instance_alias": instance_alias,
                    "league_key": league_key,
                    "team_key": team_key,
                    "format": "Roto",
                    "unit": "Selected evaluation span",
                    "period": f"{dates[0]} to {dates[-1]}",
                    "status": "Selected span",
                    "is_complete_h2h": False,
                    "eligible_days": len(league_days),
                    "categories": ", ".join(
                        rules["categories"]
                    ),
                    "rmt_vs_ygma": rmt_vs_ygma,
                    "final_vs_ygma": final_vs_ygma,
                }
            )
            continue

        if scoring_type != "head":
            errors.append(
                f"{instance_alias}: unsupported Yahoo scoring_type "
                f"{scoring_type!r}"
            )
            continue

        by_week: dict[
            int,
            dict[str, Any],
        ] = {}

        for day in league_days:
            try:
                metadata = resolve_yahoo_h2h_week_for_date(
                    league_key,
                    day["stat_date"],
                )
            except Exception as exc:
                errors.append(
                    f"{instance_alias} {day['stat_date']}: "
                    f"could not resolve Yahoo week: {exc}"
                )
                continue

            week = int(metadata["week"])
            bucket = by_week.setdefault(
                week,
                {
                    "metadata": metadata,
                    "days": [],
                },
            )
            bucket["days"].append(day)

        for week in sorted(by_week):
            bucket = by_week[week]
            metadata = bucket["metadata"]
            week_days = bucket["days"]

            start = date.fromisoformat(
                metadata["week_start"]
            )
            end = date.fromisoformat(
                metadata["week_end"]
            )

            expected_dates = {
                (start + timedelta(days=offset)).isoformat()
                for offset in range(
                    (end - start).days + 1
                )
            }

            captured_dates = {
                day["stat_date"]
                for day in week_days
            }

            is_complete = (
                captured_dates == expected_dates
            )

            totals = _aggregate_sources(week_days)

            rmt_vs_ygma = _compare_pair(
                totals["RMT_RECOMMENDED_BASELINE"],
                totals["YGMA_PRE_RMT"],
                rules,
            )
            final_vs_ygma = _compare_pair(
                totals["USER_FINAL_LOCKED"],
                totals["YGMA_PRE_RMT"],
                rules,
            )

            opponent_name = None
            ygma_vs_opponent = None
            rmt_vs_opponent = None
            final_vs_opponent = None
            final_integrity = None

            if is_complete:
                _increment_record(
                    completed_h2h["rmt"],
                    rmt_vs_ygma,
                )
                _increment_record(
                    completed_h2h["final"],
                    final_vs_ygma,
                )

                try:
                    matchup = fetch_h2h_week_matchup(
                        league_key,
                        team_key,
                        week,
                    )

                    opponent_name = str(
                        matchup["opponent"]["name"]
                    )

                    opponent_totals = _yahoo_stats_to_totals(
                        matchup["opponent"]["stats"]
                    )
                    yahoo_our_totals = _yahoo_stats_to_totals(
                        matchup["our_team"]["stats"]
                    )

                    ygma_vs_opponent = _compare_pair(
                        totals["YGMA_PRE_RMT"],
                        opponent_totals,
                        rules,
                    )
                    rmt_vs_opponent = _compare_pair(
                        totals["RMT_RECOMMENDED_BASELINE"],
                        opponent_totals,
                        rules,
                    )
                    final_vs_opponent = _compare_pair(
                        totals["USER_FINAL_LOCKED"],
                        opponent_totals,
                        rules,
                    )

                    _increment_record(
                        completed_vs_opponent["ygma"],
                        ygma_vs_opponent,
                    )
                    _increment_record(
                        completed_vs_opponent["rmt"],
                        rmt_vs_opponent,
                    )
                    _increment_record(
                        completed_vs_opponent["final"],
                        final_vs_opponent,
                    )

                    final_integrity = _final_week_integrity(
                        totals["USER_FINAL_LOCKED"],
                        yahoo_our_totals,
                    )

                    if final_integrity["status"] != "PASS":
                        errors.append(
                            f"{instance_alias} Yahoo Week {week}: "
                            "reconstructed Final totals do not match "
                            "Yahoo weekly team totals"
                        )

                except Exception as exc:
                    errors.append(
                        f"{instance_alias} Yahoo Week {week}: "
                        f"could not evaluate against real opponent: {exc}"
                    )

            league_results.append(
                {
                    "instance_alias": instance_alias,
                    "league_key": league_key,
                    "team_key": team_key,
                    "format": "H2H Categories",
                    "unit": f"Yahoo Week {week}",
                    "period": (
                        f"{metadata['week_start']} to "
                        f"{metadata['week_end']}"
                    ),
                    "status": (
                        "Complete"
                        if is_complete
                        else "Partial / Diagnostic"
                    ),
                    "is_complete_h2h": is_complete,
                    "eligible_days": len(week_days),
                    "expected_days": len(expected_dates),
                    "categories": ", ".join(
                        rules["categories"]
                    ),
                    "rmt_vs_ygma": rmt_vs_ygma,
                    "final_vs_ygma": final_vs_ygma,
                    "opponent_name": opponent_name,
                    "ygma_vs_opponent": ygma_vs_opponent,
                    "rmt_vs_opponent": rmt_vs_opponent,
                    "final_vs_opponent": final_vs_opponent,
                    "final_integrity": final_integrity,
                }
            )

    return {
        "eligible_league_days": len(eligible),
        "league_results": league_results,
        "completed_h2h": completed_h2h,
        "completed_vs_opponent": completed_vs_opponent,
        "completed_h2h_weeks": sum(
            1
            for row in league_results
            if row.get("is_complete_h2h")
        ),
        "partial_h2h_weeks": sum(
            1
            for row in league_results
            if row.get("format") == "H2H Categories"
            and not row.get("is_complete_h2h")
        ),
        "errors": errors,
    }
