#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="/app"
else
  ROOT="/Volume1/Bots/fantasy/mlf_roster_manager"
fi
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/runtime/logs"
STATUS_DIR="$ROOT/runtime/status"
TODAY="${1:-$(TZ=America/New_York date +%F)}"

mkdir -p "$LOG_DIR" "$STATUS_DIR"
LOG_FILE="$LOG_DIR/refresh_live_${TODAY}_$(date +%H%M%S).log"
STATUS_FILE="$STATUS_DIR/refresh_live_status.json"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +%FT%TZ)"
CURRENT_STAGE="init"

write_status() {
  local success="$1"
  local message="$2"
  local roster_count="${3:-0}"
  local game_count="${4:-0}"
  local lineup_count="${5:-0}"
  local hand_count="${6:-0}"

  python3 - "$STATUS_FILE" <<PY
import json
from pathlib import Path

path = Path(r"$STATUS_FILE")
payload = {
    "run_type": "live",
    "as_of_date": "$TODAY",
    "started_at_utc": "$STARTED_AT",
    "finished_at_utc": "$(date -u +%FT%TZ)",
    "success": True if "$success".lower() == "true" else False,
    "current_stage": "$CURRENT_STAGE",
    "message": "$message",
    "log_file": r"$LOG_FILE",
    "counts": {
        "roster_count": int("$roster_count"),
        "game_count": int("$game_count"),
        "lineup_count": int("$lineup_count"),
        "hand_count": int("$hand_count"),
    },
}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"WROTE_STATUS {path}")
PY
}

fail() {
  local message="$1"
  write_status false "$message" 0 0 0 0
  echo "ERROR: $message" >&2
  exit 1
}

trap 'fail "Failed during stage: $CURRENT_STAGE"' ERR

stage() {
  CURRENT_STAGE="$1"
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"
}

require_cmd docker
require_cmd python3

[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"

LEAGUE_KEY=$(grep '^DEFAULT_LEAGUE_KEY=' "$ENV_FILE" | cut -d= -f2-)
TEAM_KEY=$(grep '^DEFAULT_TEAM_KEY=' "$ENV_FILE" | cut -d= -f2-)
SAFE_TEAM_KEY=${TEAM_KEY//./_}

[[ -n "$LEAGUE_KEY" ]] || fail "DEFAULT_LEAGUE_KEY missing"
[[ -n "$TEAM_KEY" ]] || fail "DEFAULT_TEAM_KEY missing"

stage "SET DEFAULT_AS_OF_DATE=$TODAY"
python3 - "$ENV_FILE" "$TODAY" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
today = sys.argv[2]

lines = path.read_text(encoding="utf-8").splitlines()
out = []
found = False

for line in lines:
    if line.startswith("DEFAULT_AS_OF_DATE="):
        out.append(f"DEFAULT_AS_OF_DATE={today}")
        found = True
    else:
        out.append(line)

if not found:
    out.append(f"DEFAULT_AS_OF_DATE={today}")

path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"UPDATED {path}")
print(f"DEFAULT_AS_OF_DATE={today}")
PY

stage "REFRESH YAHOO ROSTER JSON"
docker exec -i mlf_roster_manager bash -lc "
cd /app/scripts/yahoo && \
YAHOO_TEAM_KEY=$TEAM_KEY python yahoo_team_roster.py
"

RAW_ROSTER_HOST="$ROOT/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json"
docker cp \
  "mlf_roster_manager:/app/scripts/yahoo/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster.json" \
  "$RAW_ROSTER_HOST"

echo "COPIED $RAW_ROSTER_HOST"

stage "BUILD ROSTER SNAPSHOT CSV"
docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/build_roster_snapshot.py \
  --src /app/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --out /app/data/derived/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv
"

stage "NORMALIZE SNAPSHOT DATE TO TODAY"
SNAPSHOT_HOST="$ROOT/data/derived/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv"
python3 - "$SNAPSHOT_HOST" "$TODAY" <<'PY'
import csv
from pathlib import Path
import sys

path = Path(sys.argv[1])
today = sys.argv[2]

rows = []
with path.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    for row in reader:
        row["roster_date"] = today
        rows.append(row)

with path.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"UPDATED {path}")
print(f"ROWS {len(rows)}")
print(f"FORCED roster_date={today}")
PY

stage "LOAD ROSTER SNAPSHOT INTO POSTGRES"
docker exec -i mlf_roster_manager bash -lc "
cd /app && PYTHONPATH=/app python scripts/load_roster_snapshot.py \
  --src /app/data/derived/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv
"

stage "REFRESH MLB GAMES / PROBABLE PITCHERS"
docker exec -i mlf_roster_manager bash -lc "
cd /app && PYTHONPATH=/app python scripts/refresh_mlb_probable_pitcher_daily.py \
  --as-of-date $TODAY
"

stage "REFRESH MLB STARTING LINEUPS"
docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/refresh_starting_lineups.py \
  --as-of-date $TODAY
"

stage "REFRESH PROBABLE PITCHER HANDEDNESS"
docker exec -i mlf_roster_manager bash -lc "
cd /app && PYTHONPATH=/app python scripts/refresh_probable_pitcher_hand.py \
  --as-of-date $TODAY \
  --out /app/data/derived/opposing_probable_pitchers_with_hand_${TODAY}.csv
"

stage "VERIFY COUNTS"
ROSTER_COUNT=$(docker exec -i mlf_postgres psql -U mlf -d mlf -tA -c "
SELECT count(*)
FROM lineup_tool.roster_snapshot
WHERE as_of_date = DATE '$TODAY'
  AND league_key = '$LEAGUE_KEY'
  AND team_key = '$TEAM_KEY';
")

GAME_COUNT=$(docker exec -i mlf_postgres psql -U mlf -d mlf -tA -c "
SELECT count(*)
FROM lineup_tool.mlb_probable_pitcher_daily
WHERE as_of_date = DATE '$TODAY';
")

LINEUP_COUNT=0
LINEUP_FILE="$ROOT/data/derived/starting_lineup_players_${TODAY}.csv"
if [[ -f "$LINEUP_FILE" ]]; then
  LINEUP_COUNT=$(( $(wc -l < "$LINEUP_FILE") - 1 ))
  if [[ "$LINEUP_COUNT" -lt 0 ]]; then LINEUP_COUNT=0; fi
fi

HAND_COUNT=0
HAND_FILE="$ROOT/data/derived/opposing_probable_pitchers_with_hand_${TODAY}.csv"
if [[ -f "$HAND_FILE" ]]; then
  HAND_COUNT=$(( $(wc -l < "$HAND_FILE") - 1 ))
  if [[ "$HAND_COUNT" -lt 0 ]]; then HAND_COUNT=0; fi
fi

echo "ROSTER_COUNT=$ROSTER_COUNT"
echo "GAME_COUNT=$GAME_COUNT"
echo "LINEUP_COUNT=$LINEUP_COUNT"
echo "HAND_COUNT=$HAND_COUNT"

stage "SAMPLE BATTING ROWS"
docker exec -i mlf_roster_manager bash -lc "
cd /app && DEFAULT_AS_OF_DATE=${TODAY} python - << 'PY'
from services.queries import get_default_context, fetch_batter_roster_rows

ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])

for r in rows[:10]:
    print(
        r['slot_display'], '|',
        r['player_display'], '|',
        r['game_display'], '|',
        r['lineup_status'], '|',
        r['ranking'], '|',
        r['note_short']
    )
PY
"

stage "DONE"
write_status true "Refresh Live completed" "$ROSTER_COUNT" "$GAME_COUNT" "$LINEUP_COUNT" "$HAND_COUNT"
echo "LOG_FILE=$LOG_FILE"
echo "STATUS_FILE=$STATUS_FILE"
