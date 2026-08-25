from __future__ import annotations

from datetime import date

import requests

from scripts.yahoo.auth import get_access_token


def _norm_slot(value) -> str:
    return str(value or "").strip().upper()


def _extract_yahoo_roster_player(player_entry) -> dict:
    out = {
        "selected_position": "",
        "full_name": "",
        "yahoo_player_key": "",
        "mlb_team_abbr": "",
        "eligible_positions": [],
        "status": "",
    }

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("player_key"):
                out["yahoo_player_key"] = str(obj["player_key"])

            name = obj.get("name")
            if isinstance(name, dict) and name.get("full"):
                out["full_name"] = str(name["full"])

            if obj.get("editorial_team_abbr"):
                out["mlb_team_abbr"] = str(obj["editorial_team_abbr"])

            if obj.get("status"):
                out["status"] = str(obj["status"])

            if obj.get("display_position") and not out["eligible_positions"]:
                out["eligible_positions"] = [
                    x.strip()
                    for x in str(obj["display_position"]).split(",")
                    if x.strip()
                ]

            raw_eligible = obj.get("eligible_positions")
            if isinstance(raw_eligible, list):
                vals = [
                    str(item["position"])
                    for item in raw_eligible
                    if isinstance(item, dict) and item.get("position")
                ]
                if vals:
                    out["eligible_positions"] = vals

            raw_selected = obj.get("selected_position")
            if isinstance(raw_selected, list):
                for item in raw_selected:
                    if isinstance(item, dict) and item.get("position"):
                        out["selected_position"] = str(item["position"])
            elif isinstance(raw_selected, dict) and raw_selected.get("position"):
                out["selected_position"] = str(raw_selected["position"])

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(player_entry)
    out["selected_position"] = _norm_slot(out["selected_position"])
    return out


def fetch_yahoo_dated_roster(team_key: str, roster_date: date) -> list[dict]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/team/"
        f"{team_key}/roster;date={roster_date.isoformat()}?format=json"
    )

    response = requests.get(url, headers=headers, timeout=45)
    if response.status_code != 200:
        raise RuntimeError(
            f"Yahoo dated roster request failed status={response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    def find_players(obj):
        if isinstance(obj, dict):
            if "players" in obj and isinstance(obj["players"], dict):
                return obj["players"]
            for value in obj.values():
                found = find_players(value)
                if found is not None:
                    return found

        elif isinstance(obj, list):
            for value in obj:
                found = find_players(value)
                if found is not None:
                    return found

        return None

    players_obj = find_players(data)
    if players_obj is None:
        raise RuntimeError("Yahoo dated roster players block not found")

    rows = []

    indexes = sorted(
        [key for key in players_obj.keys() if str(key).isdigit()],
        key=lambda value: int(value),
    )

    for idx in indexes:
        entry = players_obj[idx].get("player")
        if not isinstance(entry, list) or not entry:
            continue

        row = _extract_yahoo_roster_player(entry)
        if row.get("yahoo_player_key"):
            rows.append(row)

    return rows


def fetch_yahoo_dated_roster_slots(
    team_key: str,
    roster_date: date,
    slots: tuple[str, ...],
) -> list[dict]:
    wanted = {_norm_slot(slot) for slot in slots}
    return [
        row
        for row in fetch_yahoo_dated_roster(team_key, roster_date)
        if _norm_slot(row.get("selected_position")) in wanted
    ]
