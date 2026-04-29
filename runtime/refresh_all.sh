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
TODAY="${1:-$(TZ=America/New_York date +%F)}"
MODE="${REFRESH_ALL_MODE:-full}"

case "$MODE" in
  daily|full|deep) ;;
  *)
    echo "Invalid REFRESH_ALL_MODE=$MODE (expected daily|full|deep)" >&2
    exit 1
    ;;
esac

mkdir -p "$LOG_DIR" "$STATUS_DIR"
LOG_FILE="$LOG_DIR/refresh_all_${TODAY}_$(date +%H%M%S).log"
STATUS_FILE="$STATUS_DIR/refresh_all_status.json"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +%FT%TZ)"
CURRENT_STAGE="init"
START_EPOCH="$(date +%s)"
LAST_STAGE_NAME=""
LAST_STAGE_START_EPOCH="$START_EPOCH"

record_stage_timing() {
  local now_epoch elapsed_s
  now_epoch="$(date +%s)"
  if [[ -n "$LAST_STAGE_NAME" ]]; then
    elapsed_s=$((now_epoch - LAST_STAGE_START_EPOCH))
    echo "STAGE_DONE ts=$(date -u +%FT%TZ) stage=$LAST_STAGE_NAME elapsed_s=$elapsed_s"
  fi
}

echo "RUN_START ts=$STARTED_AT script=$(basename "$0") run_type=all run_mode=$MODE as_of_date=$TODAY"

write_status() {
  local success="$1"
  local message="$2"

  python3 - "$STATUS_FILE" <<PY
import json
from pathlib import Path

path = Path(r"$STATUS_FILE")
payload = {
    "run_type": "all",
    "run_mode": "$MODE",
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
  record_stage_timing
  CURRENT_STAGE="$1"
  LAST_STAGE_NAME="$1"
  LAST_STAGE_START_EPOCH="$(date +%s)"
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
  echo "STAGE_START ts=$(date -u +%FT%TZ) stage=$1"
}

[[ -x "$LIVE_SCRIPT" ]] || fail "Missing $LIVE_SCRIPT"
[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"

LEAGUE_KEY=$(grep '^DEFAULT_LEAGUE_KEY=' "$ENV_FILE" | cut -d= -f2-)
[[ -n "$LEAGUE_KEY" ]] || fail "DEFAULT_LEAGUE_KEY missing"

TEAM_KEY=$(grep '^DEFAULT_TEAM_KEY=' "$ENV_FILE" | cut -d= -f2-)
[[ -n "$TEAM_KEY" ]] || fail "DEFAULT_TEAM_KEY missing"
SAFE_TEAM_KEY=${TEAM_KEY//./_}

stage "REFRESH ALL: LIVE PIPELINE"
"$LIVE_SCRIPT" "$TODAY"

stage "REFRESH ALL: LEAGUE ROSTERS"
"$ROOT/runtime/refresh_league_rosters.sh" "$TODAY"

if [[ "$MODE" == "full" || "$MODE" == "deep" ]]; then
  stage "REFRESH ALL: YAHOO PLAYER POOL"

  PLAYER_POOL_MODE="meta_only"
  if [[ "$MODE" == "deep" ]]; then
    PLAYER_POOL_MODE="full"
  fi

  docker exec -i mlf_draftboard bash -lc "
cd /app/scripts/yahoo && \
YAHOO_LEAGUE_KEY=$LEAGUE_KEY \
SEASON_YEAR=${TODAY:0:4} \
PLAYER_POOL_REFRESH_MODE=${PLAYER_POOL_MODE} \
POSTGRES_DSN=\${POSTGRES_DSN:-\$MLF_POSTGRES_DSN} \
python yahoo_league_player_pool_load.py
"
else
  stage "REFRESH ALL: YAHOO PLAYER POOL (SKIPPED)"
  echo "SKIP player pool refresh for mode=$MODE"
fi

stage "REFRESH ALL: RECENT PIPELINE"
docker exec -i mlf_roster_manager bash -lc "
cd /app/scripts/yahoo && \
YAHOO_TEAM_KEY=$TEAM_KEY \
YAHOO_AS_OF_DATE=$TODAY \
YAHOO_RECENT_OUT=/app/data/derived/recent7_hitter_inputs_${TODAY}.csv \
python refresh_recent_yahoo_api.py
"

docker cp \
  "mlf_roster_manager:/app/data/derived/recent7_hitter_inputs_${TODAY}.csv" \
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

docker exec -i mlf_roster_manager bash -lc "
cd /app && python - << 'PYFA'
import csv
from pathlib import Path
from services.db import get_connection

as_of_date = '${TODAY}'
league_key = '${LEAGUE_KEY}'
season_year = int(as_of_date[:4])
mode = '${MODE}'
out = Path('/app/data/derived/true_free_agent_batters_${TODAY}.csv')

sql_all = '''
WITH fa_base AS (
    SELECT
        p.full_name,
        p.editorial_team_abbr,
        p.yahoo_player_key,
        p.eligible_positions,
        p.rank_value,
        p.percent_owned
    FROM public.yahoo_league_player_pool p
    LEFT JOIN lineup_tool.roster_snapshot r
      ON r.league_key = p.league_key
     AND r.as_of_date = %s
     AND r.yahoo_player_key = p.yahoo_player_key
    WHERE p.league_key = %s
      AND p.season_year = %s
      AND r.yahoo_player_key IS NULL
      AND NOT (
        COALESCE(p.eligible_positions, '[]'::jsonb) ? 'P'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'SP'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'RP'
      )
)
SELECT
    full_name,
    editorial_team_abbr
FROM fa_base
ORDER BY
  COALESCE(rank_value, 999999),
  COALESCE(percent_owned, -1) DESC,
  full_name
'''

sql_daily = '''
WITH game_teams AS (
    SELECT DISTINCT raw_json->'teams'->'away'->'team'->>'abbreviation' AS team_abbr
    FROM lineup_tool.mlb_probable_pitcher_daily
    WHERE as_of_date = %s
    UNION
    SELECT DISTINCT raw_json->'teams'->'home'->'team'->>'abbreviation' AS team_abbr
    FROM lineup_tool.mlb_probable_pitcher_daily
    WHERE as_of_date = %s
),
fa_base AS (
    SELECT
        p.yahoo_player_key,
        p.full_name,
        p.editorial_team_abbr,
        p.rank_value,
        p.percent_owned
    FROM public.yahoo_league_player_pool p
    JOIN game_teams gt
      ON gt.team_abbr = p.editorial_team_abbr
    LEFT JOIN lineup_tool.roster_snapshot r
      ON r.league_key = p.league_key
     AND r.as_of_date = %s
     AND r.yahoo_player_key = p.yahoo_player_key
    WHERE p.league_key = %s
      AND p.season_year = %s
      AND r.yahoo_player_key IS NULL
      AND NOT (
        COALESCE(p.eligible_positions, '[]'::jsonb) ? 'P'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'SP'
        OR COALESCE(p.eligible_positions, '[]'::jsonb) ? 'RP'
      )
      AND (
        COALESCE(p.rank_value, 999999) <= 600
        OR COALESCE(p.percent_owned, -1) >= 1
      )
)
SELECT
    yahoo_player_key,
    full_name,
    editorial_team_abbr
FROM fa_base
ORDER BY
  COALESCE(rank_value, 999999),
  COALESCE(percent_owned, -1) DESC,
  full_name
'''

with get_connection() as conn:
    with conn.cursor() as cur:
        if mode == 'daily':
            cur.execute(sql_daily, (as_of_date, as_of_date, as_of_date, league_key, season_year))
        else:
            cur.execute(sql_all, (as_of_date, league_key, season_year))
        rows = cur.fetchall()

out.parent.mkdir(parents=True, exist_ok=True)
with out.open('w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['yahoo_player_key', 'player_name', 'editorial_team_abbr'])
    writer.writeheader()
    for yahoo_player_key, player_name, editorial_team_abbr in rows:
        writer.writerow({
            'yahoo_player_key': yahoo_player_key or '',
            'player_name': player_name,
            'editorial_team_abbr': editorial_team_abbr or '',
        })

print(f'WROTE {out}')
print(f'MODE {mode}')
print(f'ROWS {len(rows)}')
for row in rows[:30]:
    print(f'{row[0]} | {row[1]} | {row[2]}')
PYFA
"

docker cp \
  "$ROOT/data/derived/true_free_agent_batters_${TODAY}.csv" \
  "mlf_roster_manager:/app/data/derived/true_free_agent_batters_${TODAY}.csv"

docker exec -i mlf_roster_manager bash -lc "
cd /app/scripts/yahoo && \
YAHOO_AS_OF_DATE=$TODAY \
YAHOO_PLAYERS_CSV=/app/data/derived/true_free_agent_batters_${TODAY}.csv \
YAHOO_RECENT_OUT=/app/data/derived/recent7_hitter_inputs_fa_${TODAY}.csv \
python refresh_recent_yahoo_api.py
"

docker cp \
  "mlf_roster_manager:/app/data/derived/recent7_hitter_inputs_fa_${TODAY}.csv" \
  "$ROOT/data/derived/recent7_hitter_inputs_fa_${TODAY}.csv"

echo "COPIED $ROOT/data/derived/recent7_hitter_inputs_fa_${TODAY}.csv"

docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/build_mlbam_player_map.py \
  --src /app/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --players-csv /app/data/derived/true_free_agent_batters_${TODAY}.csv \
  --out /app/data/derived/mlbam_player_map_fa_${TODAY}.csv
"

docker exec -i mlf_roster_manager bash -lc "
cd /app && python scripts/refresh_hitter_splits_mlb.py \
  --src /app/data/raw/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --players-csv /app/data/derived/true_free_agent_batters_${TODAY}.csv \
  --map /app/data/derived/mlbam_player_map_fa_${TODAY}.csv \
  --season-start 2025 \
  --season-end 2026 \
  --out /app/data/derived/hitter_split_inputs_fa_${TODAY}.csv
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
echo
echo '--- fa splits ---'
sed -n '1,20p' "$ROOT/data/derived/hitter_split_inputs_fa_${TODAY}.csv"

docker exec -i mlf_roster_manager bash -lc "
cd /app && DEFAULT_AS_OF_DATE=${TODAY} python - << 'PY'
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
record_stage_timing
TOTAL_ELAPSED_S=$(( $(date +%s) - START_EPOCH ))
echo "RUN_END ts=$(date -u +%FT%TZ) total_elapsed_s=$TOTAL_ELAPSED_S"
echo "LOG_FILE=$LOG_FILE"
echo "STATUS_FILE=$STATUS_FILE"
