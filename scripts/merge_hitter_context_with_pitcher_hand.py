import csv
import importlib.util
from pathlib import Path

ROOT = Path("/Volume1/Bots/fantasy/UsualSuspects/data/derived")
CTX = ROOT / "daily_hitter_context_with_savant_and_lineups_2026-04-14.csv"
HAND = ROOT / "opposing_probable_pitchers_with_hand_2026-04-14.csv"
OUT = ROOT / "daily_hitter_context_with_savant_lineups_and_hand_2026-04-14.csv"

HELPER_PATH = Path("/Volume1/Bots/fantasy/shared/runtime/name_normalization.py")
spec = importlib.util.spec_from_file_location("name_normalization", HELPER_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

with CTX.open(encoding="utf-8-sig") as f:
    ctx_rows = list(csv.DictReader(f))

with HAND.open(encoding="utf-8-sig") as f:
    hand_rows = list(csv.DictReader(f))

hand_map = {
    mod.normalize_name(r["pitcher_name"]): r
    for r in hand_rows
    if r.get("pitcher_name")
}

merged = []
for r in ctx_rows:
    row = dict(r)
    key = mod.normalize_name(r.get("opposing_probable_pitcher", ""))
    pr = hand_map.get(key, {})
    row["opp_pitcher_mlb_person_id"] = pr.get("mlb_person_id", "")
    row["opp_pitcher_throws"] = pr.get("throws", "")
    row["opp_pitcher_throws_description"] = pr.get("throws_description", "")
    merged.append(row)

with OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(merged[0].keys()))
    writer.writeheader()
    writer.writerows(merged)

print(f"WROTE {OUT} ROWS {len(merged)}")
for row in merged:
    print(
        row["player_name"],
        row["opposing_probable_pitcher"],
        row["opp_pitcher_throws"],
        row["opp_pitcher_throws_description"],
        sep=" | "
    )
