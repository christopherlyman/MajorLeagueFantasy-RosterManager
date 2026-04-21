import argparse
import csv
import html
import re
from pathlib import Path

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
NAME_RE = re.compile(r'title="([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    text = html.unescape(text)
    text = TAG_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return " ".join(text.split()).strip()


def main():
    parser = argparse.ArgumentParser(description="Parse Yahoo Fantasy Last 7 Days hitters page HTML")
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)

    html_text = src.read_text(encoding="utf-8", errors="ignore")
    rows_out = []

    for tr in ROW_RE.findall(html_text):
        # get player name from sports.yahoo player link title
        name_match = re.search(r'https://sports\.yahoo\.com/mlb/players/\d+"[^>]*title="([^"]+)"', tr)
        if not name_match:
            continue

        player_name = html.unescape(name_match.group(1)).strip()

        cells = [clean(c) for c in CELL_RE.findall(tr)]
        cells = [c for c in cells if c != ""]

        if len(cells) < 7:
            continue

        tail = cells[-7:]  # H/AB, R, HR, RBI, SB, K, AVG
        hab, r, hr, rbi, sb, k, avg = tail

        if "/" not in hab:
            continue

        try:
            hits, ab = hab.split("/", 1)
        except ValueError:
            continue

        rows_out.append(
            {
                "player_name": player_name,
                "recent7_hits": hits,
                "recent7_ab": ab,
                "recent7_r": r,
                "recent7_hr": hr,
                "recent7_rbi": rbi,
                "recent7_sb": sb,
                "recent7_k": k,
                "recent7_avg": avg,
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "player_name",
                "recent7_hits",
                "recent7_ab",
                "recent7_r",
                "recent7_hr",
                "recent7_rbi",
                "recent7_sb",
                "recent7_k",
                "recent7_avg",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"WROTE {out}")
    print(f"ROWS {len(rows_out)}")
    for row in rows_out[:20]:
        print(
            row["player_name"],
            row["recent7_hits"] + "/" + row["recent7_ab"],
            row["recent7_r"],
            row["recent7_hr"],
            row["recent7_rbi"],
            row["recent7_sb"],
            row["recent7_k"],
            row["recent7_avg"],
            sep=" | "
        )


if __name__ == "__main__":
    main()
