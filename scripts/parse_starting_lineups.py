import argparse
import csv
import re
from pathlib import Path

PLAYER_PATTERN = re.compile(
    r'<li class="starting-lineups__player"><a class="starting-lineups__player--link" href="(?P<href>[^"]+)"[^>]*>(?P<name>[^<]+)</a><span class="starting-lineups__player--position">\s*\((?P<hand>[RLS])\)\s*(?P<pos>[^<]+)</span></li>',
    re.IGNORECASE
)

def main():
    parser = argparse.ArgumentParser(description="Parse MLB starting lineups HTML into cleaned player CSV.")
    parser.add_argument("--src", required=True, help="Source HTML file")
    parser.add_argument("--as-of-date", required=True, help="Date in YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output CSV path")
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)

    html = src.read_text(encoding="utf-8", errors="ignore")

    best_by_href = {}

    for m in PLAYER_PATTERN.finditer(html):
        href = m.group("href").strip()
        name = m.group("name").strip()
        hand = m.group("hand").strip()
        pos = m.group("pos").strip()

        candidate = {
            "as_of_date": args.as_of_date,
            "player_name": name,
            "bats": hand,
            "lineup_position": pos,
            "player_href": href,
            "source_file": src.name,
        }

        current = best_by_href.get(href)
        if current is None or len(name) > len(current["player_name"]):
            best_by_href[href] = candidate

    rows = sorted(best_by_href.values(), key=lambda r: (r["player_name"], r["player_href"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["as_of_date", "player_name", "bats", "lineup_position", "player_href", "source_file"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {out}")
    print(f"ROWS {len(rows)}")
    for row in rows[:20]:
        print(
            row["player_name"],
            row["bats"],
            row["lineup_position"],
            row["player_href"],
            sep=" | "
        )

if __name__ == "__main__":
    main()
