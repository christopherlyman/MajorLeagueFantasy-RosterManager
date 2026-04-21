from typing import Any, Mapping

NEUTRAL_RANKING = 50.0
START_WORTHY_THRESHOLD = 55.0
MIN_RANKING = 0.0
MAX_RANKING = 100.0

HAND_MAX_POINTS = 2.5
HOME_AWAY_MAX_POINTS = 1.5
DAY_NIGHT_MAX_POINTS = 1.0
RECENT_FORM_MAX_POINTS = 5.0

LEAGUE7_R_BASELINE = 3.526851851851852
LEAGUE7_HR_BASELINE = 0.9990740740740741
LEAGUE7_RBI_BASELINE = 3.3814814814814813
LEAGUE7_SB_BASELINE = 0.5833333333333333
LEAGUE7_K_BASELINE = 5.883024691358025
LEAGUE_AVG_BASELINE = 0.2547142857142857

HAND_SMALL_EDGE = 0.030
HAND_CLEAR_EDGE = 0.060

HOME_AWAY_SMALL_EDGE = 0.025
HOME_AWAY_CLEAR_EDGE = 0.050

DAY_NIGHT_SMALL_EDGE = 0.020
DAY_NIGHT_CLEAR_EDGE = 0.040

RECENT_SMALL_EDGE = 0.030
RECENT_CLEAR_EDGE = 0.060


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_num(value: Any) -> bool:
    try:
        if value is None or value == "":
            return False
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _status_override(status: str) -> float | None:
    s = (status or "").strip().upper()
    if s == "NA":
        return 0.0
    if s.startswith("IL"):
        return 0.0
    return None


def _game_override(game_status: str) -> float | None:
    gs = (game_status or "").strip().upper()
    if gs == "NO_GAME_TODAY":
        return 0.0
    return None


def _reliability_from_pa(pa: Any) -> float:
    value = _num(pa, 0.0)
    return _clamp(value / 90.0, 0.0, 1.0)


def _reliability_from_ab(ab: Any, strong_ab: float) -> float:
    value = _num(ab, 0.0)
    return _clamp(value / strong_ab, 0.0, 1.0)


def _ops_edge_points(
    chosen_ops: Any,
    other_ops: Any,
    chosen_ab: Any,
    *,
    strong_ab: float,
    small_edge: float,
    clear_edge: float,
    max_points: float,
) -> float:
    if not (_has_num(chosen_ops) and _has_num(other_ops) and _has_num(chosen_ab)):
        return 0.0

    chosen = _num(chosen_ops)
    other = _num(other_ops)
    ab = _num(chosen_ab)

    edge = chosen - other
    abs_edge = abs(edge)

    if abs_edge < small_edge:
        return 0.0

    reliability = _reliability_from_ab(ab, strong_ab)
    if reliability == 0.0:
        return 0.0

    if clear_edge <= small_edge:
        intensity = 1.0
    else:
        intensity = (abs_edge - small_edge) / (clear_edge - small_edge)
        intensity = _clamp(intensity, 0.0, 1.0)

    points = max_points * reliability * max(0.35, intensity)
    return round(points if edge > 0 else -points, 2)


def ranking_band(ranking: float) -> str:
    if ranking >= 70:
        return "Strong Start"
    if ranking >= 55:
        return "Start"
    if ranking >= 45:
        return "Borderline"
    if ranking >= 35:
        return "Lean Sit"
    return "Sit"


def compute_baseline_points(row: Mapping[str, Any]) -> float:
    est_woba = _num(row.get("hitter_est_woba"))
    woba_gap = _num(row.get("hitter_woba_gap"))
    pa = _num(row.get("hitter_pa"))
    rel = _reliability_from_pa(pa)

    if est_woba == 0.0 and woba_gap == 0.0 and pa == 0.0:
        return 0.0

    raw = ((est_woba - 0.300) * 100.0 * 1.6) + (woba_gap * 100.0 * 0.6)
    points = raw * (0.5 + 0.5 * rel)
    return round(_clamp(points, -10.0, 15.0), 2)


def compute_pitcher_points(row: Mapping[str, Any]) -> float:
    est_woba_allowed = _num(row.get("pitcher_est_woba_allowed"))
    xera = _num(row.get("pitcher_xera"))
    pa = _num(row.get("pitcher_pa"))
    rel = _reliability_from_pa(pa)

    if est_woba_allowed == 0.0 and xera == 0.0 and pa == 0.0:
        return 0.0

    raw = ((est_woba_allowed - 0.320) * 100.0 * 1.0) + ((xera - 4.00) * 1.0)
    points = raw * (0.4 + 0.6 * rel)
    return round(_clamp(points, -12.0, 8.0), 2)


def _context_split_points(
    active_ops: Any,
    overall_ops: Any,
    split_ab: Any,
    *,
    shrink_k: float,
    small_edge: float,
    clear_edge: float,
    max_points: float,
) -> float:
    active = _num(active_ops)
    overall = _num(overall_ops)
    ab = _num(split_ab)

    if active <= 0.0 or overall <= 0.0 or ab <= 0.0:
        return 0.0

    raw_edge = active - overall
    abs_edge = abs(raw_edge)

    if abs_edge < small_edge:
        return 0.0

    if clear_edge <= small_edge:
        edge_scale = 1.0
    else:
        edge_scale = (abs_edge - small_edge) / (clear_edge - small_edge)
        edge_scale = _clamp(edge_scale, 0.0, 1.0)

    shrink = ab / (ab + shrink_k)
    points = max_points * edge_scale * shrink
    if raw_edge < 0:
        points = -points

    return round(points, 2)


def compute_handedness_points(row: Mapping[str, Any]) -> float:
    throws = str(row.get("opp_pitcher_throws") or "").strip().upper()
    if throws == "R":
        return _context_split_points(
            row.get("split_vs_rhp_ops"),
            row.get("overall_ops"),
            row.get("split_vs_rhp_ab"),
            shrink_k=150.0,
            small_edge=HAND_SMALL_EDGE,
            clear_edge=HAND_CLEAR_EDGE,
            max_points=HAND_MAX_POINTS,
        )
    if throws == "L":
        return _context_split_points(
            row.get("split_vs_lhp_ops"),
            row.get("overall_ops"),
            row.get("split_vs_lhp_ab"),
            shrink_k=150.0,
            small_edge=HAND_SMALL_EDGE,
            clear_edge=HAND_CLEAR_EDGE,
            max_points=HAND_MAX_POINTS,
        )
    return 0.0


def compute_home_away_points(row: Mapping[str, Any]) -> float:
    is_home = row.get("is_home")
    if is_home is True:
        return _context_split_points(
            row.get("split_home_ops"),
            row.get("overall_ops"),
            row.get("split_home_ab"),
            shrink_k=120.0,
            small_edge=HOME_AWAY_SMALL_EDGE,
            clear_edge=HOME_AWAY_CLEAR_EDGE,
            max_points=HOME_AWAY_MAX_POINTS,
        )
    if is_home is False:
        return _context_split_points(
            row.get("split_away_ops"),
            row.get("overall_ops"),
            row.get("split_away_ab"),
            shrink_k=120.0,
            small_edge=HOME_AWAY_SMALL_EDGE,
            clear_edge=HOME_AWAY_CLEAR_EDGE,
            max_points=HOME_AWAY_MAX_POINTS,
        )
    return 0.0


def compute_day_night_points(row: Mapping[str, Any]) -> float:
    daypart = str(row.get("game_daypart") or "").strip().upper()
    if daypart == "DAY":
        return _context_split_points(
            row.get("split_day_ops"),
            row.get("overall_ops"),
            row.get("split_day_ab"),
            shrink_k=100.0,
            small_edge=DAY_NIGHT_SMALL_EDGE,
            clear_edge=DAY_NIGHT_CLEAR_EDGE,
            max_points=DAY_NIGHT_MAX_POINTS,
        )
    if daypart == "NIGHT":
        return _context_split_points(
            row.get("split_night_ops"),
            row.get("overall_ops"),
            row.get("split_night_ab"),
            shrink_k=100.0,
            small_edge=DAY_NIGHT_SMALL_EDGE,
            clear_edge=DAY_NIGHT_CLEAR_EDGE,
            max_points=DAY_NIGHT_MAX_POINTS,
        )
    return 0.0


def _baseline_delta_score(actual: float, baseline: float, higher_is_better: bool = True) -> float:
    if baseline <= 0:
        return 0.0
    raw = (actual - baseline) / baseline if higher_is_better else (baseline - actual) / baseline
    return _clamp(raw, -1.0, 1.0)


def compute_recent_form_points(row: Mapping[str, Any]) -> float:
    r = _num(row.get("recent7_r"))
    hr = _num(row.get("recent7_hr"))
    rbi = _num(row.get("recent7_rbi"))
    sb = _num(row.get("recent7_sb"))
    k = _num(row.get("recent7_k"))
    hits = _num(row.get("recent7_hits"))
    ab = _num(row.get("recent7_ab"))

    scores = [
        _baseline_delta_score(r, LEAGUE7_R_BASELINE, True),
        _baseline_delta_score(hr, LEAGUE7_HR_BASELINE, True),
        _baseline_delta_score(rbi, LEAGUE7_RBI_BASELINE, True),
        _baseline_delta_score(sb, LEAGUE7_SB_BASELINE, True),
        _baseline_delta_score(k, LEAGUE7_K_BASELINE, False),
    ]

    # Only include AVG when we have real H and AB
    if ab > 0 and hits >= 0:
        expected_hits = LEAGUE_AVG_BASELINE * ab
        avg_score = _clamp((hits - expected_hits) / max(2.0, expected_hits), -1.0, 1.0)
        scores.append(avg_score)

    if not scores:
        return 0.0

    recent_raw = sum(scores) / len(scores)
    points = 5.0 * recent_raw
    return round(_clamp(points, -RECENT_FORM_MAX_POINTS, RECENT_FORM_MAX_POINTS), 2)


def compute_lineup_points(row: Mapping[str, Any]) -> float:
    lineup_status = str(row.get("lineup_status") or "").strip().upper()
    if lineup_status == "POSTED_BUT_NOT_FOUND":
        return -30.0
    return 0.0


def compute_usual_suspects_batter_ranking(row: Mapping[str, Any]) -> dict[str, Any]:
    override = _status_override(str(row.get("status_display") or row.get("status") or ""))
    if override is not None:
        ranking = override
        return {
            "ranking": int(round(ranking)),
            "ranking_band": ranking_band(ranking),
            "start_worthy": ranking >= START_WORTHY_THRESHOLD,
            "baseline_points": 0.0,
            "pitcher_points": 0.0,
            "handedness_points": 0.0,
            "home_away_points": 0.0,
            "day_night_points": 0.0,
            "recent_form_points": 0.0,
            "lineup_points": 0.0,
            "note_short": "Unavailable",
        }

    game_override = _game_override(str(row.get("game_status") or ""))
    if game_override is not None:
        ranking = game_override
        return {
            "ranking": int(round(ranking)),
            "ranking_band": ranking_band(ranking),
            "start_worthy": False,
            "baseline_points": 0.0,
            "pitcher_points": 0.0,
            "handedness_points": 0.0,
            "home_away_points": 0.0,
            "day_night_points": 0.0,
            "recent_form_points": 0.0,
            "lineup_points": 0.0,
            "note_short": "No game today",
        }

    baseline_points = compute_baseline_points(row)
    pitcher_points = compute_pitcher_points(row)
    handedness_points = compute_handedness_points(row)
    home_away_points = compute_home_away_points(row)
    day_night_points = compute_day_night_points(row)
    recent_form_points = compute_recent_form_points(row)
    lineup_points = compute_lineup_points(row)

    ranking = _clamp(
        NEUTRAL_RANKING
        + baseline_points
        + pitcher_points
        + handedness_points
        + home_away_points
        + day_night_points
        + recent_form_points
        + lineup_points,
        MIN_RANKING,
        MAX_RANKING,
    )

    note_parts = [
        f"Bat {baseline_points:+.1f}",
        f"Pitcher {pitcher_points:+.1f}",
        f"Hand {handedness_points:+.1f}",
        f"Home/Away {home_away_points:+.1f}",
        f"Day/Night {day_night_points:+.1f}",
        f"Recent {recent_form_points:+.1f}",
        f"Lineup {lineup_points:+.1f}",
    ]
    if str(row.get("game_status") or "").strip().upper() == "GAME_DATA_MISSING":
        note_parts.append("Game data missing")
    if str(row.get("lineup_status") or "").strip().upper() == "LINEUP_DATA_MISSING":
        note_parts.append("Lineup data missing")

    return {
        "ranking": int(round(ranking)),
        "ranking_band": ranking_band(ranking),
        "start_worthy": ranking >= START_WORTHY_THRESHOLD,
        "baseline_points": baseline_points,
        "pitcher_points": pitcher_points,
        "handedness_points": handedness_points,
        "home_away_points": home_away_points,
        "day_night_points": day_night_points,
        "recent_form_points": recent_form_points,
        "lineup_points": lineup_points,
        "note_short": " | ".join(note_parts),
    }
