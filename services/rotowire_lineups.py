from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.request import Request, urlopen
import html
import re

ROTOWIRE_URL = "https://www.rotowire.com/baseball/daily-lineups.php"

POSTED_FINAL_LINEUP_STATUSES = {"IN_POSTED_LINEUP", "POSTED_BUT_NOT_FOUND"}

TEAM_TO_ABBR = {
    "diamondbacks": "AZ", "d-backs": "AZ",
    "athletics": "ATH",
    "braves": "ATL",
    "orioles": "BAL",
    "red sox": "BOS",
    "cubs": "CHC",
    "white sox": "CWS",
    "reds": "CIN",
    "guardians": "CLE",
    "rockies": "COL",
    "tigers": "DET",
    "astros": "HOU",
    "royals": "KC",
    "angels": "LAA",
    "dodgers": "LAD",
    "marlins": "MIA",
    "brewers": "MIL",
    "twins": "MIN",
    "mets": "NYM",
    "yankees": "NYY",
    "phillies": "PHI",
    "pirates": "PIT",
    "padres": "SD",
    "giants": "SF",
    "mariners": "SEA",
    "cardinals": "STL",
    "rays": "TB",
    "rangers": "TEX",
    "blue jays": "TOR",
    "nationals": "WSH",
}

TEAM_ALIASES = {
    "ARI": "AZ",
    "WAS": "WSH",
    "CHW": "CWS",
    "TBR": "TB",
    "SFG": "SF",
    "KCR": "KC",
    "SDP": "SD",
    "OAK": "ATH",
}


def _clean(value: Any) -> str:
    return html.unescape(re.sub(r"\s+", " ", str(value or "")).strip())


def _strip_tags(value: Any) -> str:
    return _clean(re.sub(r"<[^>]+>", "", str(value or "")))


def _norm_name(value: Any) -> str:
    value = _clean(value).lower()
    value = re.sub(r"[^a-z0-9 ]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _team_abbr(value: Any) -> str:
    raw = _clean(value)
    key = raw.lower()
    if key in TEAM_TO_ABBR:
        return TEAM_TO_ABBR[key]
    upper = raw.upper()
    return TEAM_ALIASES.get(upper, upper)


def _row_player_name(row: dict[str, Any]) -> str:
    for key in ("player_name", "Player", "player_display", "full_name"):
        value = row.get(key)
        if value:
            return re.sub(r"\s+\([A-Z]{2,3}\)$", "", str(value)).strip()
    return ""


def _row_team(row: dict[str, Any]) -> str:
    for key in ("mlb_team_abbr", "editorial_team_abbr", "Team", "team"):
        value = row.get(key)
        if value:
            return _team_abbr(value)

    value = str(row.get("player_display") or row.get("Player") or "")
    match = re.search(r"\(([A-Z]{2,3})\)\s*$", value)
    return _team_abbr(match.group(1)) if match else ""


def _base_lineup_status(row: dict[str, Any]) -> str:
    for key in ("lineup_status", "TodayLineup", "Lineup"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def _find_blocks(page: str) -> list[str]:
    starts = [m.start() for m in re.finditer(r'class="lineup__matchup"', page)]
    blocks: list[str] = []

    for i, pos in enumerate(starts):
        start = page.rfind('<div class="lineup', 0, pos)
        end = page.rfind('<div class="lineup', 0, starts[i + 1]) if i + 1 < len(starts) else len(page)
        if start >= 0:
            blocks.append(page[start:end])

    return blocks


def _matchup_team_names(block: str) -> list[str]:
    out: list[str] = []
    for side in ("is-visit", "is-home"):
        match = re.search(
            rf'<div class="lineup__mteam {side}">\s*([^<]+?)\s*<span',
            block,
            flags=re.I | re.S,
        )
        out.append(_clean(match.group(1)) if match else "")
    return out


def _team_list(block: str, side: str) -> str:
    match = re.search(rf'<ul class="lineup__list {side}">(.*?)</ul>', block, flags=re.I | re.S)
    return match.group(1) if match else ""


def _source_status(team_html: str) -> str:
    match = re.search(
        r'<li class="lineup__status\s+([^"]+)".*?>(.*?)</li>',
        team_html,
        flags=re.I | re.S,
    )
    if not match:
        return "NO_STATUS"

    cls = _clean(match.group(1)).lower()
    text = _strip_tags(match.group(2)).lower()

    if "expected" in cls or "expected" in text:
        return "EXPECTED"
    if "confirmed" in cls or "confirmed" in text:
        return "CONFIRMED"
    return "UNKNOWN"


def _players(team_html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<li class="lineup__player">\s*'
        r'<div class="lineup__pos">([^<]+)</div>\s*'
        r'<a title="([^"]+)" href="/baseball/player/[^"]+-(\d+)">.*?</a>\s*'
        r'<span class="lineup__bats">([^<]*)</span>',
        flags=re.I | re.S,
    )

    for match in pattern.finditer(team_html):
        out.append(
            {
                "order": len(out) + 1,
                "pos": _clean(match.group(1)),
                "name": _clean(match.group(2)),
                "rotowire_id": _clean(match.group(3)),
                "bats": _clean(match.group(4)),
            }
        )

    return out


@lru_cache(maxsize=1)
def fetch_rotowire_lineups() -> dict[str, dict[str, Any]]:
    try:
        page = urlopen(
            Request(ROTOWIRE_URL, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=30,
        ).read().decode("utf-8", errors="replace")
    except Exception:
        return {}

    lineups: dict[str, dict[str, Any]] = {}

    for block in _find_blocks(page):
        names = _matchup_team_names(block)

        for side, name in zip(("is-visit", "is-home"), names):
            abbr = _team_abbr(name)
            team_html = _team_list(block, side)
            players = _players(team_html)

            if abbr and players:
                lineups[abbr] = {
                    "team_name": name,
                    "status": _source_status(team_html),
                    "players": players,
                }

    return lineups


def rotowire_lineup_advisory(row: dict[str, Any] | None) -> str:
    if not row:
        return ""

    team = _row_team(row)
    player = _row_player_name(row)

    if not team or not player:
        return ""

    lineup = fetch_rotowire_lineups().get(team)
    if not lineup:
        return ""

    found = None
    for p in lineup.get("players") or []:
        if _norm_name(p.get("name")) == _norm_name(player):
            found = p
            break

    status = str(lineup.get("status") or "UNKNOWN")

    if status == "CONFIRMED":
        if found:
            return f"RW Posted In #{found.get('order')} {found.get('pos')}"
        return "RW Posted Out"

    if status == "EXPECTED":
        if found:
            return f"RW Expected In #{found.get('order')} {found.get('pos')}"
        return "RW Expected Out"

    if found:
        return f"RW Unknown In #{found.get('order')} {found.get('pos')}"
    return "RW Unknown Out"


def lineup_status_with_rotowire(row: dict[str, Any] | None) -> str:
    if not row:
        return ""

    base = _base_lineup_status(row)

    if not base:
        return ""

    if base in POSTED_FINAL_LINEUP_STATUSES:
        return base

    if base != "LINEUP_NOT_CONFIRMED":
        return base

    advisory = rotowire_lineup_advisory(row)
    if not advisory:
        return base

    return f"{base} | {advisory}"
