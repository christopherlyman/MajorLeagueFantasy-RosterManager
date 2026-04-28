#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="/app"
else
  ROOT="/Volume1/Bots/fantasy/mlf_roster_manager"
fi
LIVE_SCRIPT="$ROOT/runtime/refresh_live.sh"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/runtime/logs"
STATUS_DIR="$ROOT/runtime/status"
TODAY="${1:-$(date +%F)}"

mkdir -p "$LOG_DIR" "$STATUS_DIR"
LOG_FILE="$LOG_DIR/refresh_all_${TODAY}_$(date +%H%M%S).log"
STATUS_FILE="$STATUS_DIR/refresh_all_status.json"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +%FT%TZ)"
CURRENT_STAGE="init"

write_status() {
  local success="$1"
  local message="$2"

  python3 - "$STATUS_FILE" <<PY
import json
from pathlib import Path

path = Path(r"$STATUS_FILE")
payload = {
    "run_type": "all",
    "as_of_date": "$TODAY",
    "started_at_utc": "$STARTED_AT",
    "finished_at_utc": "$(date -u +%FT%TZ)",
    "success": True if "$success".lower() == "true" else False,
    "current_stage": "$CURRENT_STAGE",
    "message": "$message",
    "log_file": r"$LOG_FILE"
}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"WROTE_STATUS {path}")
PY
}

fail() {
  local message="$1"
  write_status false "$message"
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

[[ -x "$LIVE_SCRIPT" ]] || fail "Missing $LIVE_SCRIPT"
[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"

TEAM_KEY=$(grep '^DEFAULT_TEAM_KEY=' "$ENV_FILE" | cut -d= -f2-)
[[ -n "$TEAM_KEY" ]] || fail "DEFAULT_TEAM_KEY missing"
SAFE_TEAM_KEY=${TEAM_KEY//./_}

stage "REFRESH ALL: LIVE PIPELINE"
"$LIVE_SCRIPT" "$TODAY"

stage "REFRESH ALL: RECENT PIPELINE"
docker exec -i mlf_draftboard bash -lc "
cd /app/scripts/yahoo && \
YAHOO_TEAM_KEY=$TEAM_KEY \
YAHOO_AS_OF_DATE=$TODAY \
python refresh_recent_yahoo_api.py
"

docker cp \
  "mlf_draftboard:/app/scripts/yahoo/data/derived/recent7_hitter_inputs_${TODAY}.csv" \
  "$ROOT/data/derived/recent7_hitter_inputs_${TODAY}.csv"

echo "COPIED $ROOT/data/derived/recent7_hitter_inputs_${TODAY}.csv"

stage "REFRESH ALL: SPLITS PIPELINE"
docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/build_mlbam_player_map.py \
  --src /app/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --out /app/data/derived/mlbam_player_map_${TODAY}.csv
"

docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/refresh_hitter_splits_mlb.py \
  --src /app/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --map /app/data/derived/mlbam_player_map_${TODAY}.csv \
  --season-start 2025 \
  --season-end 2026 \
  --out /app/data/derived/hitter_split_inputs_${TODAY}.csv
"

stage "REFRESH ALL: VERIFY BASELINES"
echo '--- recent ---'
sed -n '1,20p' "$ROOT/data/derived/recent7_hitter_inputs_${TODAY}.csv"
echo
echo '--- mlbam map ---'
sed -n '1,20p' "$ROOT/data/derived/mlbam_player_map_${TODAY}.csv"
echo
echo '--- splits ---'
sed -n '1,20p' "$ROOT/data/derived/hitter_split_inputs_${TODAY}.csv"

docker exec -i mlf_roster_manager bash -lc "
cd /app && python - << 'PY'
from services.queries import get_default_context, fetch_batter_roster_rows

ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])

for r in rows[:10]:
    print(
        r['player_display'], '|',
        r['ranking'], '|',
        r['note_short']
    )
PY
"

stage "DONE"
write_status true "Refresh All completed"
echo "LOG_FILE=$LOG_FILE"
echo "STATUS_FILE=$STATUS_FILE"
