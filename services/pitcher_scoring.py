from __future__ import annotations

from typing import Any, Mapping


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def baseball_ip_to_decimal(value: Any) -> float:
    """
    Yahoo stores baseball IP as 49.1 / 49.2 meaning 49 1/3 or 49 2/3.
    """
    if value in (None, ""):
        return 0.0

    text = str(value)
    if "." not in text:
        return _num(value)

    whole, frac = text.split(".", 1)
    base = float(whole or 0)

    if frac.startswith("1"):
        return base + (1.0 / 3.0)
    if frac.startswith("2"):
        return base + (2.0 / 3.0)

    return _num(value)


def pitcher_band(score: float) -> str:
    if score >= 65:
        return "Start"
    if score >= 55:
        return "Conditional Start"
    if score >= 45:
        return "Risky / Context"
    return "Sit"


def infer_pitcher_role(row: Mapping[str, Any], app_alias: str) -> str:
    selected = str(row.get("selected_position") or "").upper()
    primary = str(row.get("primary_position") or "").upper()
    eligible = row.get("eligible_positions") or []

    if isinstance(eligible, str):
        eligible_set = {eligible.upper()}
    else:
        eligible_set = {str(x).upper() for x in eligible}

    if primary == "SP" or selected == "SP" or "SP" in eligible_set:
        return "SP"
    if primary == "RP" or selected == "RP" or "RP" in eligible_set:
        return "RP"

    # Usual-RMT often labels pitchers generically as P.
    if app_alias == "usual-rmt":
        sv = _num(row.get("sv"))
        hld = _num(row.get("hld"))
        ip = baseball_ip_to_decimal(row.get("ip"))
        if sv + hld > 0 and ip < 30:
            return "RP"

    return "SP"


def _status_penalty(row: Mapping[str, Any]) -> tuple[float, str]:
    selected = str(row.get("selected_position") or "").upper()
    status = str(row.get("status") or "").upper()

    if selected in {"IL", "NA"} or status == "NA" or "IL" in status:
        return -100.0, "Unavailable"

    return 0.0, "Active"


def _score_sp(row: Mapping[str, Any], app_alias: str) -> tuple[float, list[str]]:
    era = _num(row.get("era"))
    whip = _num(row.get("whip"))
    wins = _num(row.get("w"))
    strikeouts = _num(row.get("k_pit"))
    ip_dec = baseball_ip_to_decimal(row.get("ip"))

    k_rate = strikeouts / ip_dec if ip_dec > 0 else 0.0

    era_pts = _clamp((4.20 - era) / 0.75 * 6.0, -12.0, 12.0)
    whip_pts = _clamp((1.30 - whip) / 0.18 * 6.0, -12.0, 12.0)
    k_pts = _clamp((k_rate - 0.90) / 0.25 * 5.0, -8.0, 8.0)
    w_pts = _clamp(wins * 0.8, 0.0, 5.0)

    score = 50.0 + era_pts + whip_pts + k_pts + w_pts
    reasons = [
        f"ERA {era_pts:+.1f}",
        f"WHIP {whip_pts:+.1f}",
        f"K/IP {k_pts:+.1f}",
        f"W {w_pts:+.1f}",
    ]

    if app_alias in {"mlf-rmt", "milf-rmt"}:
        qs = _num(row.get("qs"))
        tb = _num(row.get("tb"))
        tb_rate = tb / ip_dec if ip_dec > 0 else 0.0

        qs_pts = _clamp(qs * 1.2, 0.0, 7.0)
        tb_pts = _clamp((1.45 - tb_rate) / 0.35 * 7.0, -12.0, 10.0)

        score += qs_pts + tb_pts
        reasons.extend([f"QS {qs_pts:+.1f}", f"TB/IP {tb_pts:+.1f}"])

    return score, reasons


def _score_rp(row: Mapping[str, Any], app_alias: str) -> tuple[float, list[str]]:
    era = _num(row.get("era"))
    whip = _num(row.get("whip"))
    strikeouts = _num(row.get("k_pit"))
    ip_dec = baseball_ip_to_decimal(row.get("ip"))

    k_rate = strikeouts / ip_dec if ip_dec > 0 else 0.0

    era_pts = _clamp((3.75 - era) / 0.70 * 5.0, -10.0, 10.0)
    whip_pts = _clamp((1.25 - whip) / 0.18 * 6.0, -12.0, 12.0)
    k_pts = _clamp((k_rate - 1.00) / 0.35 * 5.0, -8.0, 8.0)

    if app_alias == "usual-rmt":
        sv = _num(row.get("sv"))
        hld = _num(row.get("hld"))
        role_pts = _clamp((sv * 2.0) + (hld * 1.4), 0.0, 15.0)
        role_note = f"SV/HLD {role_pts:+.1f}"
    else:
        svh = _num(row.get("sv_h"))
        role_pts = _clamp(svh * 1.5, 0.0, 15.0)
        role_note = f"SV+H {role_pts:+.1f}"

    score = 50.0 + era_pts + whip_pts + k_pts + role_pts
    reasons = [
        f"ERA {era_pts:+.1f}",
        f"WHIP {whip_pts:+.1f}",
        f"K/IP {k_pts:+.1f}",
        role_note,
    ]

    return score, reasons


def score_pitcher(row: Mapping[str, Any], app_alias: str) -> dict[str, Any]:
    role = infer_pitcher_role(row, app_alias)
    status_pts, status_label = _status_penalty(row)

    if status_pts <= -100:
        return {
            "role": role,
            "ranking": 0,
            "band": "Sit",
            "start_worthy": False,
            "note_short": "Unavailable",
        }

    if role == "RP":
        raw_score, reasons = _score_rp(row, app_alias)
    else:
        raw_score, reasons = _score_sp(row, app_alias)

    score = _clamp(raw_score + status_pts, 0.0, 100.0)

    return {
        "role": role,
        "ranking": int(round(score)),
        "band": pitcher_band(score),
        "start_worthy": score >= 65,
        "note_short": " | ".join(reasons + [f"S {status_pts:+.1f}"]),
    }
