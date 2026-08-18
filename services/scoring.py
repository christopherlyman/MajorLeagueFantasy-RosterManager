from typing import Any, Mapping

NEUTRAL_RANKING = 50.0
START_WORTHY_THRESHOLD = 55.0
MIN_RANKING = 0.0
MAX_RANKING = 100.0

HAND_MAX_POINTS = 2.5
HOME_AWAY_MAX_POINTS = 1.5
DAY_NIGHT_MAX_POINTS = 1.0
RECENT_FORM_MAX_POINTS = 3.0
PITCHER_SAMPLE_CAP_LOW_PA = 70.0
PITCHER_SAMPLE_CAP_MEDIUM_PA = 170.0
PITCHER_SAMPLE_CAP_HIGH_PA = 300.0
PITCHER_SAMPLE_CAP_LOW_POINTS = 3.0
PITCHER_SAMPLE_CAP_MEDIUM_POINTS = 5.0
PITCHER_SAMPLE_CAP_HIGH_POINTS = 8.0

LEAGUE7_R_BASELINE = 3.526851851851852
LEAGUE7_HR_BASELINE = 0.9990740740740741
LEAGUE7_RBI_BASELINE = 3.3814814814814813
LEAGUE7_SB_BASELINE = 0.5833333333333333
LEAGUE7_K_BASELINE = 5.883024691358025
LEAGUE_AVG_BASELINE = 0.2547142857142857

HAND_SMALL_EDGE = 0.030
HAND_CLEAR_EDGE = 0.060
HAND_OPS_GAP_SCALE = 25.0
HAND_STARTER_OPS_BASELINE = 0.740
HAND_STARTER_OPS_MAX_POINTS = 6.0
HAND_OPS_GAP_MAX_POINTS = 12.0

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


DTD_STATUS_PENALTY = -3.0


def _status_override(status: str) -> float | None:
    s = (status or "").strip().upper()
    if s == "NA":
        return 0.0
    if s.startswith("IL"):
        return 0.0
    return None


def _game_override(game_status: str) -> float | None:
    gs = (game_status or "").strip().upper()
    if gs in {"NO_GAME_TODAY", "POSTPONED"}:
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


YAHOO_WOBA_WEIGHTS_2026 = {
    "bb": 0.699,
    "hbp": 0.730,
    "1b": 0.891,
    "2b": 1.262,
    "3b": 1.596,
    "hr": 2.048,
}

YAHOO_WOBA_FIT_INTERCEPT = 0.092374
YAHOO_WOBA_FIT_SLOPE = 0.693929


def _yahoo_batter_woba(row: Mapping[str, Any]) -> float | None:
    h = _num(row.get("hitter_yahoo_h"))
    ab = _num(row.get("hitter_yahoo_ab"))
    doubles = _num(row.get("hitter_yahoo_2b"))
    triples = _num(row.get("hitter_yahoo_3b"))
    hr = _num(row.get("hitter_yahoo_hr"))
    bb = _num(row.get("hitter_yahoo_bb"))
    ibb = _num(row.get("hitter_yahoo_ibb"))
    hbp = _num(row.get("hitter_yahoo_hbp"))
    sf = _num(row.get("hitter_yahoo_sf"))

    singles = max(0.0, h - doubles - triples - hr)
    unintentional_bb = max(0.0, bb - ibb)
    denominator = ab + unintentional_bb + sf + hbp

    if denominator <= 0.0:
        return None

    numerator = (
        YAHOO_WOBA_WEIGHTS_2026["bb"] * unintentional_bb
        + YAHOO_WOBA_WEIGHTS_2026["hbp"] * hbp
        + YAHOO_WOBA_WEIGHTS_2026["1b"] * singles
        + YAHOO_WOBA_WEIGHTS_2026["2b"] * doubles
        + YAHOO_WOBA_WEIGHTS_2026["3b"] * triples
        + YAHOO_WOBA_WEIGHTS_2026["hr"] * hr
    )
    return numerator / denominator


def _yahoo_woba_fit_to_xwoba(yahoo_woba: float | None) -> float | None:
    if yahoo_woba is None or yahoo_woba <= 0.0:
        return None
    return YAHOO_WOBA_FIT_INTERCEPT + (YAHOO_WOBA_FIT_SLOPE * yahoo_woba)


def _yahoo_woba_stabilizer_points(row: Mapping[str, Any], est_woba: float) -> float:
    yahoo_woba = _yahoo_batter_woba(row)
    fitted_xwoba = _yahoo_woba_fit_to_xwoba(yahoo_woba)

    if fitted_xwoba is None or est_woba <= 0.0:
        return 0.0

    ab = _num(row.get("hitter_yahoo_ab"))
    ab_confidence = _clamp((ab - 50.0) / 250.0, 0.0, 1.0)

    raw_delta_points = (fitted_xwoba - est_woba) * 100.0 * 1.6
    stabilizer = raw_delta_points * 0.20 * ab_confidence

    return _clamp(stabilizer, -2.0, 2.0)


def compute_baseline_points(row: Mapping[str, Any]) -> float:
    est_woba = _num(row.get("hitter_est_woba"))
    pa = _num(row.get("hitter_pa"))
    rel = _reliability_from_pa(pa)

    if est_woba > 0.0:
        raw = (est_woba - 0.300) * 100.0 * 1.6
        points = raw * (0.5 + 0.5 * rel)
        points += _yahoo_woba_stabilizer_points(row, est_woba)
        return round(_clamp(points, -10.0, 15.0), 2)

    yahoo_woba = _yahoo_batter_woba(row)
    fitted_xwoba = _yahoo_woba_fit_to_xwoba(yahoo_woba)
    if fitted_xwoba is None:
        return 0.0

    ab = _num(row.get("hitter_yahoo_ab"))
    yahoo_rel = _clamp(ab / 250.0, 0.0, 1.0)
    raw = (fitted_xwoba - 0.300) * 100.0 * 1.6
    points = raw * (0.35 + 0.65 * yahoo_rel)

    return round(_clamp(points, -10.0, 15.0), 2)



def _pitcher_sample_points_cap(pa: float) -> float:
    """Cap pitcher matchup impact by pitcher sample size.

    This preserves meaningful ace/weak-pitcher effects when there is enough
    evidence, while preventing tiny pitcher samples from producing ace-level
    hitter penalties.
    """
    if pa < PITCHER_SAMPLE_CAP_LOW_PA:
        return PITCHER_SAMPLE_CAP_LOW_POINTS
    if pa < PITCHER_SAMPLE_CAP_MEDIUM_PA:
        return PITCHER_SAMPLE_CAP_MEDIUM_POINTS
    if pa < PITCHER_SAMPLE_CAP_HIGH_PA:
        return PITCHER_SAMPLE_CAP_HIGH_POINTS
    return 12.0


def compute_pitcher_points(row: Mapping[str, Any]) -> float:
    est_woba_allowed = _num(row.get("pitcher_est_woba_allowed"))
    xera = _num(row.get("pitcher_xera"))
    pa = _num(row.get("pitcher_pa"))
    rel = _reliability_from_pa(pa)

    if est_woba_allowed == 0.0 and xera == 0.0 and pa == 0.0:
        return 0.0

    raw = ((est_woba_allowed - 0.320) * 100.0 * 1.0) + ((xera - 4.00) * 1.0)
    points = raw * (0.4 + 0.6 * rel)
    sample_cap = _pitcher_sample_points_cap(pa)
    return round(_clamp(points, -sample_cap, min(8.0, sample_cap)), 2)

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


def _hand_ops_gap_confidence(split_ab: Any) -> float:
    ab = _num(split_ab)
    if ab < 10:
        return 0.25
    if ab < 25:
        return 0.50
    if ab < 50:
        return 0.75
    return 1.00



def _hand_ops_gap_points(split_ops: Any, overall_ops: Any, split_ab: Any) -> float:
    """Score handedness using split OPS versus a fantasy-starter OPS baseline.

    This avoids penalizing elite hitters merely because their weaker side is
    lower than their own elite overall line. For lineup choice, the question is
    whether today's split is strong relative to startable fantasy hitters.
    """
    split = _num(split_ops)
    ab = _num(split_ab)

    if split <= 0.0 or ab <= 0.0:
        return 0.0

    rel = _reliability_from_ab(ab, 150.0)
    gap = split - HAND_STARTER_OPS_BASELINE

    if abs(gap) < HAND_SMALL_EDGE:
        return 0.0

    points = gap * HAND_OPS_GAP_SCALE * (0.5 + 0.5 * rel)
    return round(_clamp(points, -HAND_STARTER_OPS_MAX_POINTS, HAND_STARTER_OPS_MAX_POINTS), 2)

def compute_handedness_points(row: Mapping[str, Any]) -> float:
    throws = str(row.get("opp_pitcher_throws") or "").strip().upper()
    if throws == "R":
        return _hand_ops_gap_points(
            row.get("split_vs_rhp_ops"),
            row.get("overall_ops"),
            row.get("split_vs_rhp_ab"),
        )
    if throws == "L":
        return _hand_ops_gap_points(
            row.get("split_vs_lhp_ops"),
            row.get("overall_ops"),
            row.get("split_vs_lhp_ab"),
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

    # Current Yahoo recent inputs do not reliably populate H/AB/AVG.
    # When AB is missing/zero, treat recent form as unknown instead of cold.
    if ab <= 0:
        return 0.0

    scores = [
        _baseline_delta_score(r, LEAGUE7_R_BASELINE, True),
        _baseline_delta_score(hr, LEAGUE7_HR_BASELINE, True),
        _baseline_delta_score(rbi, LEAGUE7_RBI_BASELINE, True),
        _baseline_delta_score(sb, LEAGUE7_SB_BASELINE, True),
        _baseline_delta_score(k, LEAGUE7_K_BASELINE, False),
    ]

    if hits >= 0:
        expected_hits = LEAGUE_AVG_BASELINE * ab
        avg_score = _clamp((hits - expected_hits) / max(2.0, expected_hits), -1.0, 1.0)
        scores.append(avg_score)

    if not scores:
        return 0.0

    recent_raw = sum(scores) / len(scores)
    points = RECENT_FORM_MAX_POINTS * recent_raw
    return round(_clamp(points, -RECENT_FORM_MAX_POINTS, RECENT_FORM_MAX_POINTS), 2)


def compute_status_risk_points(row: Mapping[str, Any]) -> float:
    status = str(row.get("status_display") or row.get("status") or "").strip().upper()
    if status == "DTD":
        return DTD_STATUS_PENALTY
    return 0.0


def compute_lineup_points(row: Mapping[str, Any]) -> float:
    lineup_status = str(row.get("lineup_status") or "").strip().upper()
    if lineup_status == "POSTED_BUT_NOT_FOUND":
        return -30.0
    return 0.0



def compute_rank_reliability_points(row: Mapping[str, Any]) -> float:
    return round(_clamp(_num(row.get("rank_reliability_points")) * 2.0, 0.0, 16.0), 2)

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
            "note_short": "Postponed" if str(row.get("game_status") or "").strip().upper() == "POSTPONED" else "No game today",
        }

    baseline_points = compute_baseline_points(row)
    pitcher_points = compute_pitcher_points(row)
    handedness_points = compute_handedness_points(row)
    home_away_points = compute_home_away_points(row)
    day_night_points = compute_day_night_points(row)
    recent_form_points = compute_recent_form_points(row)
    status_risk_points = compute_status_risk_points(row)
    lineup_points = compute_lineup_points(row)
    status_points = status_risk_points + lineup_points
    rank_reliability_points = compute_rank_reliability_points(row)
    reliability_label = str(row.get("reliability_label") or "").strip()
    reliability_reason = str(row.get("reliability_reason") or "").strip()

    ranking = _clamp(
        NEUTRAL_RANKING
        + baseline_points
        + pitcher_points
        + handedness_points
        + home_away_points
        + day_night_points
        + recent_form_points
        + rank_reliability_points
        + status_risk_points
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
    ]
    if rank_reliability_points:
        reliability_note = f"Reliability {rank_reliability_points:+.1f}"
        if reliability_label and reliability_label != "No reliability bump":
            reliability_note = f"{reliability_note} {reliability_label}"
        note_parts.append(reliability_note)
    note_parts.append(f"Status {status_points:+.1f}")
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
        "rank_reliability_points": rank_reliability_points,
        "reliability_label": reliability_label,
        "reliability_reason": reliability_reason,
        "status_risk_points": status_risk_points,
        "lineup_points": lineup_points,
        "note_short": " | ".join(note_parts),
    }
