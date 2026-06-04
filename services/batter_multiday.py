from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from services.db import get_connection
from services.scoring import compute_usual_suspects_batter_ranking
from services.queries import (
    fetch_batter_roster_rows,
    fetch_available_batter_rows,
    apply_start_frequency_penalty,
    apply_rotowire_expected_out_penalty,
    apply_h2h_matchup_score,
    normalize_name,
    _season_year,
    _load_savant_map,
    _load_pitcher_hand_map,
    _load_hitter_split_map,
    _load_recent7_map,
    _slot_display,
    _player_display,
    _clean_eligible_positions,
    _status_display,
    _slot_sort_key,
    _mlb_game_status_from_raw_json,
    _format_game_time_et,
    _game_daypart_et,
    _mlb_game_display_override,
    _game_display,
)


def _key(row: dict[str, Any]) -> str:
    return str(row.get("yahoo_player_key") or "").strip()


def _player(row: dict[str, Any]) -> str:
    return (
        row.get("player_display")
        or row.get("player_name")
        or row.get("full_name")
        or ""
    )


def _rank(row: dict[str, Any]) -> float:
    try:
        return float(row.get("ranking") or 0)
    except Exception:
        return 0.0


def _eligible(row: dict[str, Any]) -> str:
    return row.get("eligible_display") or row.get("eligible_positions") or ""


def _projection_dates(as_of_date: str, days: int = 3) -> list[str]:
    base = date.fromisoformat(as_of_date)
    return [(base + timedelta(days=i)).isoformat() for i in range(days)]


def _policy_map(ctx: dict[str, Any]) -> dict[str, dict[str, str]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT yahoo_player_key, policy_status, notes
                FROM rmt.roster_player_policy
                WHERE league_key = %s
                  AND team_key = %s
                """,
                (ctx["league_key"], ctx["team_key"]),
            )
            return {
                str(k): {"policy": p, "notes": notes or ""}
                for k, p, notes in cur.fetchall()
            }


def _fetch_key_context(
    ctx: dict[str, Any],
    projection_date: str,
    player_keys: list[str],
) -> dict[str, dict[str, Any]]:
    if not player_keys:
        return {}

    season_year = int(str(ctx["as_of_date"])[:4])

    sql = """
    WITH games AS (
        SELECT
            raw_json,
            away_team_name,
            home_team_name,
            away_probable_pitcher_name,
            home_probable_pitcher_name
        FROM lineup_tool.mlb_probable_pitcher_daily
        WHERE as_of_date = %s
    )
    SELECT
        p.full_name AS player_name,
        p.editorial_team_abbr AS mlb_team_abbr,
        '' AS current_slot,
        p.eligible_positions,
        '' AS status,
        p.yahoo_player_key,
        p.percent_owned,
        p.rank_value,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.home_probable_pitcher_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.away_probable_pitcher_name
            ELSE ''
        END AS opposing_probable_pitcher,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.home_team_name
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN g.away_team_name
            ELSE ''
        END AS opponent_team,
        CASE
            WHEN g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN FALSE
            WHEN g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
                THEN TRUE
            ELSE NULL
        END AS is_home,
        COALESCE(g.raw_json->>'gameDate', '') AS game_date_utc,
        g.raw_json AS raw_json
    FROM public.yahoo_league_player_pool p
    LEFT JOIN games g
      ON g.raw_json->'teams'->'away'->'team'->>'abbreviation' = p.editorial_team_abbr
      OR g.raw_json->'teams'->'home'->'team'->>'abbreviation' = p.editorial_team_abbr
    WHERE p.league_key = %s
      AND p.season_year = %s
      AND p.yahoo_player_key = ANY(%s::text[])
    ORDER BY p.full_name
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (projection_date, ctx["league_key"], season_year, player_keys))
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    return {str(r["yahoo_player_key"]): r for r in rows}


def _resources_for_date(projection_date: str) -> dict[str, Any]:
    year = _season_year(projection_date)
    return {
        "hitter_savant": _load_savant_map("batters", year),
        "pitcher_savant": _load_savant_map("pitchers", year),
        "pitcher_hand_map": _load_pitcher_hand_map(projection_date),
        "hitter_split_map": _load_hitter_split_map(projection_date),
        "recent7_map": _load_recent7_map(projection_date),
    }


def _score_future_batter(
    ctx: dict[str, Any],
    base_row: dict[str, Any],
    context_row: dict[str, Any],
    projection_date: str,
    resources: dict[str, Any],
) -> dict[str, Any]:
    row = deepcopy(base_row)

    for field, value in context_row.items():
        row[field] = value

    row["slot_display"] = _slot_display(row.get("current_slot"))
    row["player_display"] = _player_display(row.get("player_name", ""), row.get("mlb_team_abbr", ""))
    row["eligible_display"] = _clean_eligible_positions(row.get("eligible_positions"))
    row["status_display"] = _status_display(row.get("status"))
    row["_slot_sort"] = _slot_sort_key(row.get("current_slot"))

    if row.get("opponent_team") or row.get("game_date_utc"):
        row["game_status"] = _mlb_game_status_from_raw_json(row.get("raw_json"))
        row["game_time_et"] = _format_game_time_et(row.get("game_date_utc", ""))
        row["game_daypart"] = _game_daypart_et(row.get("game_date_utc", ""))
        row["game_started"] = False
        row["game_display"] = _mlb_game_display_override(row.get("raw_json")) or _game_display(
            row.get("opponent_team", ""),
            row.get("is_home"),
            row["game_time_et"],
        )
    else:
        row["game_status"] = "NO_GAME_TODAY"
        row["game_display"] = "No game"
        row["game_daypart"] = ""
        row["game_started"] = False

    hitter_row = resources["hitter_savant"].get(normalize_name(row.get("player_name", "")), {})
    pitcher_row = resources["pitcher_savant"].get(normalize_name(row.get("opposing_probable_pitcher", "")), {})
    hand_row = resources["pitcher_hand_map"].get(normalize_name(row.get("opposing_probable_pitcher", "")), {})
    split_row = resources["hitter_split_map"].get(normalize_name(row.get("player_name", "")), {})
    recent_row = resources["recent7_map"].get(normalize_name(row.get("player_name", "")), {})

    row["lineup_status"] = "LINEUP_NOT_CONFIRMED" if row["game_status"] == "GAME_FOUND" else "LINEUP_NOT_APPLICABLE"
    row["lineup_points"] = 0.0

    row["hitter_pa"] = hitter_row.get("pa", "")
    row["hitter_ba"] = hitter_row.get("ba", "")
    row["hitter_est_woba"] = hitter_row.get("est_woba", "")
    row["hitter_woba_gap"] = hitter_row.get("est_woba_minus_woba_diff", "")

    row["pitcher_pa"] = pitcher_row.get("pa", "")
    row["pitcher_est_woba_allowed"] = pitcher_row.get("est_woba", "")
    row["pitcher_xera"] = pitcher_row.get("xera", "")
    row["opp_pitcher_throws"] = (hand_row.get("throws") or "").strip().upper()

    row["overall_ops"] = split_row.get("overall_ops", "")
    row["split_vs_rhp_ops"] = split_row.get("vs_rhp_ops", "")
    row["split_vs_rhp_ab"] = split_row.get("vs_rhp_ab", "")
    row["split_vs_lhp_ops"] = split_row.get("vs_lhp_ops", "")
    row["split_vs_lhp_ab"] = split_row.get("vs_lhp_ab", "")

    row["split_home_ops"] = split_row.get("home_ops", "")
    row["split_home_ab"] = split_row.get("home_ab", "")
    row["split_away_ops"] = split_row.get("away_ops", "")
    row["split_away_ab"] = split_row.get("away_ab", "")
    row["split_day_ops"] = split_row.get("day_ops", "")
    row["split_day_ab"] = split_row.get("day_ab", "")
    row["split_night_ops"] = split_row.get("night_ops", "")
    row["split_night_ab"] = split_row.get("night_ab", "")

    row["recent7_hits"] = recent_row.get("recent7_hits", "")
    row["recent7_ab"] = recent_row.get("recent7_ab", "")
    row["recent7_avg"] = recent_row.get("recent7_avg", "")
    row["recent7_r"] = recent_row.get("recent7_r", "")
    row["recent7_hr"] = recent_row.get("recent7_hr", "")
    row["recent7_rbi"] = recent_row.get("recent7_rbi", "")
    row["recent7_sb"] = recent_row.get("recent7_sb", "")
    row["recent7_bb"] = recent_row.get("recent7_bb", "")
    row["recent7_k"] = recent_row.get("recent7_k", "")

    score = compute_usual_suspects_batter_ranking(row)
    score = apply_start_frequency_penalty(row, score, ctx["as_of_date"])
    score = apply_rotowire_expected_out_penalty(row, score, projection_date, ctx["as_of_date"])
    score = apply_h2h_matchup_score(row, score, ctx["league_key"], ctx["team_key"], projection_date)

    row.update(score)
    row["projection_date"] = projection_date
    return row


def build_batter_multiday_projection(
    ctx: dict[str, Any],
    days: int = 3,
    include_fa: bool = True,
) -> dict[str, Any]:
    dates = _projection_dates(str(ctx["as_of_date"]), days)

    owned_today = fetch_batter_roster_rows(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])
    fa_today = fetch_available_batter_rows(ctx["league_key"], ctx["team_key"], ctx["as_of_date"]) if include_fa else []

    owned_base = {_key(r): r for r in owned_today if _key(r)}
    fa_base = {_key(r): r for r in fa_today if _key(r)}
    all_keys = sorted(set(owned_base) | set(fa_base))

    policies = _policy_map(ctx)
    contexts = {d: _fetch_key_context(ctx, d, all_keys) for d in dates}
    resources = {d: _resources_for_date(d) for d in dates if d != ctx["as_of_date"]}

    coverage = []
    for d in dates:
        context = contexts[d]
        coverage.append(
            {
                "date": d,
                "owned_covered": sum(1 for k in owned_base if k in context),
                "owned_total": len(owned_base),
                "fa_covered": sum(1 for k in fa_base if k in context),
                "fa_total": len(fa_base),
                "all_covered": len(context),
                "all_total": len(all_keys),
            }
        )

    def build_rows(base_pool: dict[str, dict[str, Any]], pool: str) -> list[dict[str, Any]]:
        out = []

        for player_key, base_row in base_pool.items():
            item = {
                "Pool": pool,
                "Player": _player(base_row),
                "YahooKey": player_key,
                "Slot": base_row.get("current_slot") or "",
                "Policy": policies.get(player_key, {}).get("policy", "FA" if pool == "FA" else "MISSING"),
                "Eligible": _eligible(base_row),
                "Today": None,
                "Tomorrow": None,
                "Day2": None,
                "Total3": 0.0,
                "TodayLineup": "",
                "TodayGame": "",
                "TomorrowGame": "",
                "Day2Game": "",
                "TodayNote": "",
                "TomorrowNote": "",
                "Day2Note": "",
            }

            ranks = []

            for idx, d in enumerate(dates):
                label = "Today" if idx == 0 else "Tomorrow" if idx == 1 else "Day2"
                context = contexts[d].get(player_key)

                if d == ctx["as_of_date"]:
                    scored = base_row
                elif context:
                    scored = _score_future_batter(ctx, base_row, context, d, resources[d])
                else:
                    scored = None

                if scored is None:
                    item[label] = None
                    item[f"{label}Note"] = "MISSING_CONTEXT"
                    continue

                item[label] = _rank(scored)
                item[f"{label}Note"] = scored.get("note_short") or ""

                if label == "Today":
                    item["TodayLineup"] = scored.get("lineup_status") or ""
                    item["TodayGame"] = scored.get("game_display") or ""
                elif label == "Tomorrow":
                    item["TomorrowGame"] = scored.get("game_display") or ""
                elif label == "Day2":
                    item["Day2Game"] = scored.get("game_display") or ""

                ranks.append(_rank(scored))

            item["Total3"] = round(sum(ranks), 1)
            out.append(item)

        return out

    rows = build_rows(owned_base, "OWNED")
    if include_fa:
        rows.extend(build_rows(fa_base, "FA"))

    rows.sort(key=lambda r: (-float(r.get("Total3") or 0), r.get("Pool", ""), r.get("Player", "")))

    return {
        "dates": dates,
        "coverage": coverage,
        "rows": rows,
    }
