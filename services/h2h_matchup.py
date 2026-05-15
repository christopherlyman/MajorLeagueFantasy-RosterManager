from __future__ import annotations

import os
import sys
from datetime import datetime
from functools import lru_cache
from typing import Any, Mapping

import requests

sys.path.insert(0, "/app/scripts/yahoo")

from auth import get_access_token
from services.scoring import MAX_RANKING, MIN_RANKING, START_WORTHY_THRESHOLD, ranking_band

H2H_APP_ALIASES = {"mlf-rmt", "milf-rmt"}

STAT_IDS = {
    "HAB": "60",
    "R": "7",
    "HR": "12",
    "RBI": "13",
    "SB": "16",
    "BB": "18",
    "K": "21",
    "AVG": "3",
}

HIGHER_IS_BETTER = {
    "R": True,
    "HR": True,
    "RBI": True,
    "SB": True,
    "BB": True,
    "K": False,
    "AVG": True,
}

COUNTING_THRESHOLDS = {
    "R": (5, 12),
    "HR": (2, 5),
    "RBI": (5, 12),
    "SB": (2, 5),
    "BB": (4, 10),
    "K": (5, 12),
}

CATEGORY_BASELINES = {
    "R": (2.0, 2.0),
    "HR": (0.5, 0.8),
    "RBI": (2.0, 2.0),
    "SB": (0.3, 0.7),
    "BB": (1.0, 1.3),
    "K": (4.0, 2.5),
    "AVG": (0.255, 0.080),
}


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _z(value: float, avg: float, sd: float) -> float:
    if sd <= 0:
        return 0.0
    return _clamp((value - avg) / (sd * 1.5))


def _parse_hab(value: Any) -> tuple[float, float]:
    text = str(value or "").strip()
    if "/" not in text:
        return 0.0, 0.0
    left, right = text.split("/", 1)
    try:
        return float(left), float(right)
    except Exception:
        return 0.0, 0.0


def _team_meta(team_node: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for node in _walk(team_node):
        if not isinstance(node, dict):
            continue
        for key in ["team_key", "team_id", "name", "is_owned_by_current_login"]:
            if key in node:
                meta[key] = node[key]
    return meta


def _team_stats(team_node: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for node in _walk(team_node):
        stat = node.get("stat") if isinstance(node, dict) else None
        if isinstance(stat, dict) and stat.get("stat_id") is not None:
            stats[str(stat["stat_id"])] = stat.get("value")
    return stats


def _parse_team(team_node: Any) -> dict[str, Any]:
    return {"meta": _team_meta(team_node), "stats": _team_stats(team_node)}


def _find_matchup(payload: dict[str, Any], team_key: str) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()

    for node in _walk(payload):
        teams = node.get("teams") if isinstance(node, dict) else None
        if not isinstance(teams, dict):
            continue

        team_nodes = [
            value["team"]
            for key, value in teams.items()
            if str(key).isdigit() and isinstance(value, dict) and "team" in value
        ]

        parsed = [_parse_team(team_node) for team_node in team_nodes]
        parsed = [team for team in parsed if team["meta"].get("team_key")]

        if len(parsed) < 2:
            continue

        keys = tuple(sorted(str(team["meta"]["team_key"]) for team in parsed))
        if keys in seen:
            continue
        seen.add(keys)

        if any(team["meta"].get("team_key") == team_key for team in parsed):
            return parsed

    return []


def _league_meta(payload: dict[str, Any]) -> dict[str, Any]:
    for node in _walk(payload):
        if isinstance(node, dict) and node.get("league_key") and node.get("current_week"):
            return node
    return {}


def _category_stats(team: dict[str, Any]) -> dict[str, float]:
    stats = team["stats"]
    hits, ab = _parse_hab(stats.get(STAT_IDS["HAB"]))
    return {
        "H": hits,
        "AB": ab,
        "AVG": _float(stats.get(STAT_IDS["AVG"])),
        "R": _float(stats.get(STAT_IDS["R"])),
        "HR": _float(stats.get(STAT_IDS["HR"])),
        "RBI": _float(stats.get(STAT_IDS["RBI"])),
        "SB": _float(stats.get(STAT_IDS["SB"])),
        "BB": _float(stats.get(STAT_IDS["BB"])),
        "K": _float(stats.get(STAT_IDS["K"])),
    }


def _week_urgency(current_date: str) -> float:
    try:
        weekday = datetime.strptime(current_date, "%Y-%m-%d").date().weekday()
    except Exception:
        return 0.50

    return {
        0: 0.20,
        1: 0.25,
        2: 0.40,
        3: 0.55,
        4: 0.75,
        5: 0.90,
        6: 1.00,
    }.get(weekday, 0.50)


def _counting_weight(cat: str, ours: float, opponent: float) -> float:
    higher = HIGHER_IS_BETTER[cat]
    close, far = COUNTING_THRESHOLDS[cat]
    good_margin = (ours - opponent) if higher else (opponent - ours)
    abs_margin = abs(good_margin)

    if good_margin > far:
        return 0.10
    if good_margin > close:
        return 0.25
    if good_margin >= 0:
        return 0.70
    if abs_margin <= close:
        return 1.00
    if abs_margin <= far:
        return 0.40
    return 0.00


def _avg_weight(ours_avg: float, opponent_avg: float) -> float:
    good_margin = ours_avg - opponent_avg
    abs_margin = abs(good_margin)

    if good_margin >= 0.040:
        return 0.10
    if good_margin >= 0.020:
        return 0.25
    if good_margin >= 0:
        return 0.75
    if abs_margin <= 0.015:
        return 1.00
    if abs_margin <= 0.040:
        return 0.40
    return 0.00


@lru_cache(maxsize=32)
def _weights(league_key: str, team_key: str, as_of_date: str, app_alias: str) -> dict[str, float]:
    if app_alias not in H2H_APP_ALIASES:
        return {}

    try:
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/scoreboard?format=json"
        headers = {"Authorization": f"Bearer {get_access_token()}"}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}

    meta = _league_meta(payload)
    urgency = _week_urgency(str(meta.get("current_date") or as_of_date))

    matchup = _find_matchup(payload, team_key)
    if len(matchup) < 2:
        return {}

    ours = next((team for team in matchup if team["meta"].get("team_key") == team_key), None)
    opponent = next((team for team in matchup if team["meta"].get("team_key") != team_key), None)
    if not ours or not opponent:
        return {}

    ours_stats = _category_stats(ours)
    opp_stats = _category_stats(opponent)

    out: dict[str, float] = {}
    for cat in ["R", "HR", "RBI", "SB", "BB", "K"]:
        out[cat] = _counting_weight(cat, ours_stats[cat], opp_stats[cat]) * urgency
    out["AVG"] = _avg_weight(ours_stats["AVG"], opp_stats["AVG"]) * urgency
    return out


def _has_recent_profile(row: Mapping[str, Any]) -> bool:
    # Blank recent fields mean the row did not join to the current-date recent7 CSV.
    # A real recent profile can legitimately contain zeros, so "0" counts as present.
    keys = [
        "recent7_r",
        "recent7_hr",
        "recent7_rbi",
        "recent7_sb",
        "recent7_bb",
        "recent7_k",
        "recent7_hits",
        "recent7_ab",
        "recent7_avg",
    ]
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return True
    return False


def _player_component(row: Mapping[str, Any], cat: str) -> float:
    if cat in {"R", "HR", "RBI", "SB", "BB"}:
        avg, sd = CATEGORY_BASELINES[cat]
        return _z(_float(row.get(f"recent7_{cat.lower()}")), avg, sd)

    if cat == "K":
        avg, sd = CATEGORY_BASELINES["K"]
        return -_z(_float(row.get("recent7_k")), avg, sd)

    if cat == "AVG":
        avg_base, avg_spread = CATEGORY_BASELINES["AVG"]
        ab = _float(row.get("recent7_ab"))
        avg_value = _float(row.get("recent7_avg"))
        reliability = _clamp(ab / 20.0, 0.0, 1.0)
        return _clamp(((avg_value - avg_base) / avg_spread) * reliability)

    return 0.0


def _eligible_for_h2h(row: Mapping[str, Any], score: Mapping[str, Any]) -> bool:
    if int(score.get("ranking") or 0) <= 0:
        return False
    if not _has_recent_profile(row):
        return False
    if _float(score.get("lineup_points")) <= -30.0:
        return False
    if str(row.get("lineup_status") or "").strip().upper() == "POSTED_BUT_NOT_FOUND":
        return False
    return True


def apply_h2h_matchup_score(
    row: Mapping[str, Any],
    score: Mapping[str, Any],
    league_key: str,
    team_key: str,
    as_of_date: str,
) -> dict[str, Any]:
    app_alias = os.getenv("APP_ALIAS", "").strip().lower()
    if app_alias not in H2H_APP_ALIASES:
        return dict(score)

    out = dict(score)
    if not _eligible_for_h2h(row, out):
        return out

    category_weights = _weights(league_key, team_key, as_of_date, app_alias)
    if not category_weights:
        return out

    raw = 0.0
    for cat, weight in category_weights.items():
        if weight > 0:
            raw += weight * _player_component(row, cat)

    h2h_points = round(_clamp(raw * 2.0, -5.0, 5.0), 2)
    adjusted = max(MIN_RANKING, min(MAX_RANKING, _float(out.get("ranking")) + h2h_points))

    out["h2h_matchup_points"] = h2h_points
    out["ranking"] = int(round(adjusted))
    out["ranking_band"] = ranking_band(adjusted)
    out["start_worthy"] = adjusted >= START_WORTHY_THRESHOLD

    note = str(out.get("note_short") or "")
    h2h_note = f"H2H {h2h_points:+.1f}"

    # Keep Status last because it is the most day-sensitive display modifier.
    marker = " | Status "
    if marker in note:
        prefix, suffix = note.rsplit(marker, 1)
        out["note_short"] = f"{prefix} | {h2h_note}{marker}{suffix}"
    else:
        out["note_short"] = f"{note} | {h2h_note}" if note else h2h_note

    return out
