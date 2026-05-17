from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, "/app/scripts/yahoo")

from auth import get_access_token
from services.db import get_connection
from services.queries import get_default_context


YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
UNAVAILABLE_STATUSES = {"IL", "IL7", "IL10", "IL15", "IL60", "NA", "SUSP"}


def walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def player_blocks(payload: dict) -> list[list]:
    blocks: list[list] = []

    for node in walk(payload):
        if isinstance(node, dict) and isinstance(node.get("player"), list):
            blocks.append(node["player"])

    if blocks:
        return blocks

    for node in walk(payload):
        if isinstance(node, list) and any(
            isinstance(item, dict) and "player_key" in item for item in node
        ):
            blocks.append(node)

    return blocks


def first(block: Any, key: str) -> str:
    for node in walk(block):
        if isinstance(node, dict) and key in node and node.get(key) not in (None, ""):
            return str(node.get(key)).strip()
    return ""


def full_name(block: Any) -> str:
    for node in walk(block):
        if not isinstance(node, dict):
            continue

        name = node.get("name")
        if isinstance(name, dict) and name.get("full"):
            return str(name["full"]).strip()

        if node.get("full"):
            return str(node["full"]).strip()

    return ""


def eligible_positions(block: Any) -> list[str]:
    out: list[str] = []

    for node in walk(block):
        if not isinstance(node, dict):
            continue

        ep = node.get("eligible_positions")
        if isinstance(ep, list):
            for item in ep:
                if isinstance(item, dict) and item.get("position"):
                    out.append(str(item["position"]).strip())

        if isinstance(ep, dict):
            for item in walk(ep):
                if isinstance(item, dict) and item.get("position"):
                    out.append(str(item["position"]).strip())

        display_position = node.get("display_position")
        if display_position:
            for part in str(display_position).replace("/", ",").split(","):
                part = part.strip()
                if part:
                    out.append(part)

    seen: list[str] = []
    for value in out:
        if value and value not in seen:
            seen.append(value)

    return seen


def percent_owned(block: Any) -> str:
    for node in walk(block):
        if not isinstance(node, dict):
            continue

        po = node.get("percent_owned")
        if isinstance(po, dict) and po.get("value") not in (None, ""):
            return str(po["value"]).strip()

        if po not in (None, "") and not isinstance(po, dict):
            return str(po).strip()

    return ""


def yahoo_rank(block: Any) -> str:
    for key in ("rank", "overall_rank"):
        value = first(block, key)
        if value:
            return value
    return ""


def is_unavailable(block: Any) -> bool:
    status = first(block, "status").upper()
    status_full = first(block, "status_full").upper()

    if status in UNAVAILABLE_STATUSES:
        return True

    return (
        "INJURED" in status_full
        or "NOT ACTIVE" in status_full
        or "SUSP" in status_full
    )


def is_pitcher(block: Any) -> bool:
    position_type = first(block, "position_type").upper()
    display_position = first(block, "display_position").upper()
    eligible = {value.upper() for value in eligible_positions(block)}

    return (
        position_type == "P"
        or "P" in eligible
        or "SP" in eligible
        or "RP" in eligible
        or display_position in {"P", "SP", "RP"}
        or "SP" in {p.strip() for p in display_position.split(",")}
        or "RP" in {p.strip() for p in display_position.split(",")}
    )


def fetch_fa_pitchers(league_key: str, max_rows: int) -> list[dict[str, str]]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    rows: list[dict[str, str]] = []

    # Prefer the Yahoo-side pitcher filter. Fall back to broader FA scan if needed.
    endpoint_variants = [
        "status=FA;position=P;sort=OR",
        "status=FA;sort=OR",
    ]

    for variant in endpoint_variants:
        rows.clear()
        start = 0

        while len(rows) < max_rows and start < 1000:
            url = (
                f"{YAHOO_FANTASY_BASE}/league/{league_key}/players;"
                f"{variant};start={start};count=25;out=percent_owned?format=json"
            )

            response = requests.get(url, headers=headers, timeout=45)
            response.raise_for_status()

            blocks = player_blocks(response.json())
            if not blocks:
                break

            for block in blocks:
                pkey = first(block, "player_key")
                if not pkey:
                    continue

                if is_unavailable(block):
                    continue

                if not is_pitcher(block):
                    continue

                elig = eligible_positions(block)
                meaningful = [value for value in elig if value in {"SP", "RP", "P"}]

                rows.append(
                    {
                        "yahoo_player_key": pkey,
                        "player_name": full_name(block),
                        "editorial_team_abbr": first(block, "editorial_team_abbr"),
                        "eligible_positions": "|".join(meaningful),
                        "display_position": first(block, "display_position"),
                        "status": first(block, "status"),
                        "status_full": first(block, "status_full"),
                        "percent_owned_yahoo": percent_owned(block),
                        "yahoo_rank": yahoo_rank(block),
                    }
                )

                if len(rows) >= max_rows:
                    break

            start += 25

        if rows:
            return rows

    return rows


def owned_pitcher_keys() -> set[str]:
    ctx = get_default_context()
    keys: set[str] = set()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT r.yahoo_player_key
                FROM lineup_tool.roster_snapshot r
                WHERE r.league_key = %s
                  AND r.team_key = %s
                  AND r.as_of_date = %s
                  AND r.yahoo_player_key IS NOT NULL
                  AND (
                    r.position_type = 'P'
                    OR r.primary_position IN ('P','SP','RP')
                    OR r.selected_position IN ('P','SP','RP')
                    OR r.eligible_positions && ARRAY['P','SP','RP']
                  )
                ORDER BY r.yahoo_player_key;
                """,
                (ctx["league_key"], ctx["team_key"], ctx["as_of_date"]),
            )
            keys.update(row[0] for row in cur.fetchall())

    return keys


def main() -> None:
    league_key = os.environ["RMT_PITCHER_FA_LEAGUE_KEY"]
    max_rows = int(os.environ.get("RMT_PITCHER_FA_MAX", "100"))

    out_path = Path(os.environ["RMT_PITCHER_FA_OUT"])
    keyset_path = Path(os.environ["RMT_PITCHER_KEYSET_OUT"])

    rows = fetch_fa_pitchers(league_key, max_rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "yahoo_player_key",
                "player_name",
                "editorial_team_abbr",
                "eligible_positions",
                "display_position",
                "status",
                "status_full",
                "percent_owned_yahoo",
                "yahoo_rank",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    keys = owned_pitcher_keys()
    keys.update(row["yahoo_player_key"] for row in rows if row.get("yahoo_player_key"))

    keyset_path.parent.mkdir(parents=True, exist_ok=True)
    keyset_path.write_text(",".join(sorted(keys)), encoding="utf-8")

    print(f"WROTE {out_path}")
    print(f"FA_PITCHERS {len(rows)}")
    print(f"WROTE_KEYSET {keyset_path}")
    print(f"PITCHER_KEYS {len(keys)}")

    for row in rows[:30]:
        print(
            f"{row['yahoo_player_key']} | {row['player_name']} | "
            f"{row['eligible_positions']} | {row['editorial_team_abbr']}"
        )


if __name__ == "__main__":
    main()
