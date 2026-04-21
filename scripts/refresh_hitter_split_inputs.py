import argparse
import csv
import html
import re
import requests
from pathlib import Path

from services.db import get_connection

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

YAHOO_SPLITS_URL = "https://sports.yahoo.com/mlb/players/{player_id}/splits/"


def clean_cell(text: str) -> str:
    text = html.unescape(text)
    text = TAG_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return " ".join(text.split()).strip()


def extract_rows(html_text: str):
    rows = {}
    for tr in ROW_RE.findall(html_text):
        cells = [clean_cell(c) for c in CELL_RE.findall(tr)]
        if len(cells) >= 17:
            label = cells[0]
            rows[label] = cells
    return rows


def get_ab_ops(cells):
    if not cells or len(cells) < 17:
        return "", ""
    # cells[2] = AB, cells[16] = OPS
    return cells[2], cells[16]


def weighted_ops(ab1, ops1, ab2, ops2):
    try:
        ab1 = float(ab1)
        ab2 = float(ab2)
        ops1 = float(ops1)
        ops2 = float(ops2)
        total_ab = ab1 + ab2
        if total_ab <= 0:
            return ""
        return f"{(((ab1 * ops1) + (ab2 * ops2)) / total_ab):.3f}"
    except Exception:
        return ""


def yahoo_player_id_from_key(player_key: str) -> str:
    m = re.search(r"\.p\.(\d+)$", str(player_key or ""))
    return m.group(1) if m else ""


def fetch_batter_roster(as_of_date: str, league_key: str, team_key: str):
    sql = """
    SELECT full_name, yahoo_player_key
    FROM lineup_tool.roster_snapshot
    WHERE as_of_date = %s
      AND league_key = %s
      AND team_key = %s
      AND position_type = 'B'
    ORDER BY full_name;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (as_of_date, league_key, team_key))
            return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description="Refresh hitter split inputs from Yahoo public splits pages")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--league-key", required=True)
    parser.add_argument("--team-key", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out_path = Path(args.out)
    raw_dir = Path("/app/data/raw/yahoo") / f"splits_{args.as_of_date}"
    raw_dir.mkdir(parents=True, exist_ok=True)

    roster = fetch_batter_roster(args.as_of_date, args.league_key, args.team_key)

    fieldnames = [
        "player_name",
        "overall_ops",
        "vs_rhp_ops",
        "vs_rhp_ab",
        "vs_lhp_ops",
        "vs_lhp_ab",
        "home_ops",
        "home_ab",
        "away_ops",
        "away_ab",
        "day_ops",
        "day_ab",
        "night_ops",
        "night_ab",
    ]

    out_rows = []

    for player_name, yahoo_player_key in roster:
        yahoo_id = yahoo_player_id_from_key(yahoo_player_key)
        row = {k: "" for k in fieldnames}
        row["player_name"] = player_name

        if not yahoo_id:
            out_rows.append(row)
            continue

        url = YAHOO_SPLITS_URL.format(player_id=yahoo_id)
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        raw_path = raw_dir / f"{yahoo_id}_{player_name.replace('/', '-')}.html"
        raw_path.write_text(resp.text, encoding="utf-8")

        parsed = extract_rows(resp.text)

        lhp = parsed.get("vs. Left")
        rhp = parsed.get("vs. Right")
        home = parsed.get("Home")
        away = parsed.get("Away")
        day = parsed.get("Day")
        night = parsed.get("Night")

        row["vs_lhp_ab"], row["vs_lhp_ops"] = get_ab_ops(lhp)
        row["vs_rhp_ab"], row["vs_rhp_ops"] = get_ab_ops(rhp)
        row["home_ab"], row["home_ops"] = get_ab_ops(home)
        row["away_ab"], row["away_ops"] = get_ab_ops(away)
        row["day_ab"], row["day_ops"] = get_ab_ops(day)
        row["night_ab"], row["night_ops"] = get_ab_ops(night)

        row["overall_ops"] = weighted_ops(
            row["vs_lhp_ab"], row["vs_lhp_ops"],
            row["vs_rhp_ab"], row["vs_rhp_ops"]
        )

        out_rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"WROTE {out_path}")
    print(f"ROWS {len(out_rows)}")
    for row in out_rows:
        print(
            row["player_name"],
            row["vs_lhp_ops"],
            row["vs_rhp_ops"],
            row["home_ops"],
            row["away_ops"],
            row["day_ops"],
            row["night_ops"],
            sep=" | "
        )


if __name__ == "__main__":
    main()
