from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from services.queries import get_default_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh MLB probable pitcher and pitcher-hand context for Today/Tomorrow/Day2 projections."
    )
    parser.add_argument("--base-date", default="", help="Base date YYYY-MM-DD. Defaults to RMT context as_of_date.")
    parser.add_argument("--days", type=int, default=3, help="Number of dates to refresh, starting at base date.")
    parser.add_argument(
        "--derived-root",
        default=os.getenv("RMT_DERIVED_ROOT", "/app/data/derived"),
        help="Derived output root for pitcher hand files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.days < 1:
        raise SystemExit("days must be >= 1")

    if args.base_date:
        base = date.fromisoformat(args.base_date)
        ctx = {"as_of_date": args.base_date}
    else:
        ctx = get_default_context()
        base = date.fromisoformat(str(ctx["as_of_date"]))

    derived_root = Path(args.derived_root)
    env = dict(os.environ)
    env["PYTHONPATH"] = "/app"

    dates = [(base + timedelta(days=i)).isoformat() for i in range(args.days)]

    print("CTX", ctx)
    print("PROJECTION_CONTEXT_DATES", dates)

    for d in dates:
        print()
        print(f"=== refresh MLB probable pitchers for {d} ===")
        subprocess.run(
            [sys.executable, "scripts/refresh_mlb_probable_pitcher_daily.py", "--as-of-date", d],
            check=True,
            env=env,
        )

        out_path = derived_root / f"opposing_probable_pitchers_with_hand_{d}.csv"
        print()
        print(f"=== refresh probable pitcher hand file for {d} ===")
        subprocess.run(
            [
                sys.executable,
                "scripts/refresh_probable_pitcher_hand.py",
                "--as-of-date",
                d,
                "--out",
                str(out_path),
            ],
            check=True,
            env=env,
        )

    print("PROJECTION_GAME_CONTEXT_REFRESH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
