#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="${RMT_PROJECT_ROOT:-/app}"
else
  ROOT="${RMT_PROJECT_ROOT:-/Volume1/Bots/fantasy/mlf_roster_manager}"
fi
APP_CONTAINER="${RMT_CONTAINER_NAME:-mlf_roster_manager}"
LIVE_SCRIPT="$ROOT/runtime/refresh_live.sh"
ENV_FILE="${RMT_ENV_FILE:-$ROOT/.env}"
LOG_DIR="${RMT_LOG_DIR:-$ROOT/runtime/logs}"
STATUS_DIR="${RMT_STATUS_DIR:-$ROOT/runtime/status}"
APP_RAW_ROOT="${RMT_RAW_ROOT:-/app/data/raw}"
APP_DERIVED_ROOT="${RMT_DERIVED_ROOT:-/app/data/derived}"
HOST_RAW_ROOT="${RMT_HOST_RAW_ROOT:-$ROOT/data/raw}"
HOST_DERIVED_ROOT="${RMT_HOST_DERIVED_ROOT:-$ROOT/data/derived}"
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

  docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
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
docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app/scripts/yahoo && \
YAHOO_TEAM_KEY=$TEAM_KEY \
YAHOO_AS_OF_DATE=$TODAY \
YAHOO_RECENT_OUT=$APP_DERIVED_ROOT/recent7_hitter_inputs_${TODAY}.csv \
python refresh_recent_yahoo_api.py
"

docker cp \
  "${APP_CONTAINER}:$APP_DERIVED_ROOT/recent7_hitter_inputs_${TODAY}.csv" \
  "$HOST_DERIVED_ROOT/recent7_hitter_inputs_${TODAY}.csv"

echo "COPIED $HOST_DERIVED_ROOT/recent7_hitter_inputs_${TODAY}.csv"

stage "REFRESH ALL: SPLITS PIPELINE"
docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && python scripts/build_mlbam_player_map.py \
  --src $APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --out $APP_DERIVED_ROOT/mlbam_player_map_${TODAY}.csv
"

docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && python scripts/refresh_hitter_splits_mlb.py \
  --src $APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --map $APP_DERIVED_ROOT/mlbam_player_map_${TODAY}.csv \
  --season-start 2025 \
  --season-end 2026 \
  --out $APP_DERIVED_ROOT/hitter_split_inputs_${TODAY}.csv
"

docker exec -i \
  -e RMT_RAW_ROOT="$APP_RAW_ROOT" \
  -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" \
  -e RMT_FA_AS_OF_DATE="${TODAY}" \
  -e RMT_FA_LEAGUE_KEY="${LEAGUE_KEY}" \
  -e RMT_FA_MODE="${MODE}" \
  -e RMT_FA_OUT="$APP_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv" \
  "$APP_CONTAINER" bash -lc 'cd /app && PYTHONPATH=/app python - << "PYFA"
import csv
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, "/app/scripts/yahoo")
from auth import get_access_token

from services.db import get_connection

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
PITCHER_POSITIONS = {"P", "SP", "RP"}
UNAVAILABLE_POSITIONS = {"IL", "NA"}
UNAVAILABLE_STATUSES = {"IL", "IL10", "IL15", "IL60", "NA", "SUSP"}

as_of_date = os.environ["RMT_FA_AS_OF_DATE"]
league_key = os.environ["RMT_FA_LEAGUE_KEY"]
mode = os.environ["RMT_FA_MODE"]
season_year = int(as_of_date[:4])
out = Path(os.environ["RMT_FA_OUT"])


def find_first(obj, wanted_key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == wanted_key:
                return v
            found = find_first(v, wanted_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_first(item, wanted_key)
            if found is not None:
                return found
    return None


def extract_player_lists(payload):
    league = payload.get("fantasy_content", {}).get("league", [])
    if not isinstance(league, list) or len(league) < 2 or not isinstance(league[1], dict):
        return []

    players = league[1].get("players")
    if not isinstance(players, dict):
        return []

    out_rows = []
    for k, v in players.items():
        if k == "count":
            continue
        if isinstance(v, dict) and isinstance(v.get("player"), list):
            out_rows.append(v["player"])
    return out_rows


def percent_owned_value(player_list):
    po = find_first(player_list, "percent_owned")
    if isinstance(po, list):
        for item in po:
            if isinstance(item, dict) and "value" in item:
                return item["value"]
    if isinstance(po, dict):
        return po.get("value")
    return po


def extract_positions(player_list):
    ep = find_first(player_list, "eligible_positions")
    out_rows = []
    if isinstance(ep, list):
        for item in ep:
            if isinstance(item, dict) and item.get("position"):
                out_rows.append(str(item["position"]))
    return out_rows


def clean_status(value):
    if value is False or value is None:
        return ""
    return str(value).strip().upper()


def extract_player_row(player_list):
    name = find_first(player_list, "name")
    if isinstance(name, dict):
        full_name = str(name.get("full") or "").strip()
    else:
        full_name = str(name or "").strip()

    return {
        "yahoo_player_key": str(find_first(player_list, "player_key") or "").strip(),
        "player_name": full_name,
        "editorial_team_abbr": str(find_first(player_list, "editorial_team_abbr") or "").strip(),
        "eligible_positions": extract_positions(player_list),
        "status": find_first(player_list, "status"),
        "status_full": find_first(player_list, "status_full"),
        "percent_owned_yahoo": percent_owned_value(player_list),
    }


def fetch_yahoo_free_agents():
    token = get_access_token()
    headers = {"Authorization": "Bearer {}".format(token)}
    rows = []

    for start in range(0, 3000, 25):
        url = "{}/league/{}/players;status=FA;sort=OR;start={};count=25;out=percent_owned?format=json".format(
            YAHOO_FANTASY_BASE,
            league_key,
            start,
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError("Yahoo status=FA request failed: HTTP {}: {}".format(resp.status_code, resp.text[:240]))

        player_lists = extract_player_lists(resp.json())
        if not player_lists:
            break

        rows.extend(extract_player_row(player_list) for player_list in player_lists)

    seen = set()
    deduped = []
    for row in rows:
        key = row["yahoo_player_key"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def is_batter(row):
    positions = {p.upper() for p in row.get("eligible_positions", [])}
    return not bool(positions & PITCHER_POSITIONS)


def is_active(row):
    positions = {p.upper() for p in row.get("eligible_positions", [])}
    status = clean_status(row.get("status"))
    status_full = str(row.get("status_full") or "").strip().upper()

    if positions & UNAVAILABLE_POSITIONS:
        return False
    if status and status in UNAVAILABLE_STATUSES:
        return False
    if status_full:
        return False
    return True


def load_daily_context():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT raw_json->$$teams$$->$$away$$->$$team$$->>$$abbreviation$$
                FROM lineup_tool.mlb_probable_pitcher_daily
                WHERE as_of_date = %s
                UNION
                SELECT DISTINCT raw_json->$$teams$$->$$home$$->$$team$$->>$$abbreviation$$
                FROM lineup_tool.mlb_probable_pitcher_daily
                WHERE as_of_date = %s
                """,
                (as_of_date, as_of_date),
            )
            game_teams = {str(r[0]) for r in cur.fetchall() if r[0]}

            cur.execute(
                """
                SELECT yahoo_player_key, rank_value, percent_owned
                FROM public.yahoo_league_player_pool
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (league_key, season_year),
            )
            meta = {str(k): {"rank": rank, "owned": owned} for k, rank, owned in cur.fetchall()}

    return game_teams, meta


def rank_owned_ok(row, meta):
    m = meta.get(row["yahoo_player_key"], {})

    try:
        rank = float(m.get("rank")) if m.get("rank") is not None else 999999.0
    except Exception:
        rank = 999999.0

    try:
        owned = float(row.get("percent_owned_yahoo")) if row.get("percent_owned_yahoo") is not None else float(m.get("owned") or 0)
    except Exception:
        owned = 0.0

    return rank <= 600 and owned > 0


yahoo_rows = fetch_yahoo_free_agents()
batter_rows = [r for r in yahoo_rows if is_batter(r)]
active_batter_rows = [r for r in batter_rows if is_active(r)]

if mode == "daily":
    game_teams, meta = load_daily_context()
    rows = [
        r for r in active_batter_rows
        if r["editorial_team_abbr"] in game_teams and rank_owned_ok(r, meta)
    ]
else:
    game_teams, meta = set(), {}
    rows = active_batter_rows

out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["yahoo_player_key", "player_name", "editorial_team_abbr"])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "yahoo_player_key": r["yahoo_player_key"],
            "player_name": r["player_name"],
            "editorial_team_abbr": r["editorial_team_abbr"],
        })

print("WROTE {}".format(out))
print("MODE {}".format(mode))
print("YAHOO_FA_TOTAL {}".format(len(yahoo_rows)))
print("FA_BATTERS {}".format(len(batter_rows)))
print("ACTIVE_FA_BATTERS {}".format(len(active_batter_rows)))
print("ROWS {}".format(len(rows)))
for row in rows[:30]:
    print("{} | {} | {}".format(row["yahoo_player_key"], row["player_name"], row["editorial_team_abbr"]))
PYFA'



docker cp \
  "$HOST_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv" \
  "${APP_CONTAINER}:$APP_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv"

docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app/scripts/yahoo && \
YAHOO_AS_OF_DATE=$TODAY \
YAHOO_PLAYERS_CSV=$APP_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv \
YAHOO_RECENT_OUT=$APP_DERIVED_ROOT/recent7_hitter_inputs_fa_${TODAY}.csv \
python refresh_recent_yahoo_api.py
"

docker cp \
  "${APP_CONTAINER}:$APP_DERIVED_ROOT/recent7_hitter_inputs_fa_${TODAY}.csv" \
  "$HOST_DERIVED_ROOT/recent7_hitter_inputs_fa_${TODAY}.csv"

echo "COPIED $HOST_DERIVED_ROOT/recent7_hitter_inputs_fa_${TODAY}.csv"

docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && python scripts/build_mlbam_player_map.py \
  --src $APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --players-csv $APP_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv \
  --out $APP_DERIVED_ROOT/mlbam_player_map_fa_${TODAY}.csv
"

docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && python scripts/refresh_hitter_splits_mlb.py \
  --src $APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json \
  --players-csv $APP_DERIVED_ROOT/true_free_agent_batters_${TODAY}.csv \
  --map $APP_DERIVED_ROOT/mlbam_player_map_fa_${TODAY}.csv \
  --season-start 2025 \
  --season-end 2026 \
  --out $APP_DERIVED_ROOT/hitter_split_inputs_fa_${TODAY}.csv
"

stage "REFRESH ALL: VERIFY BASELINES"
echo '--- recent ---'
sed -n '1,20p' "$HOST_DERIVED_ROOT/recent7_hitter_inputs_${TODAY}.csv"
echo
echo '--- mlbam map ---'
sed -n '1,20p' "$HOST_DERIVED_ROOT/mlbam_player_map_${TODAY}.csv"
echo
echo '--- splits ---'
sed -n '1,20p' "$HOST_DERIVED_ROOT/hitter_split_inputs_${TODAY}.csv"
echo
echo '--- fa splits ---'
sed -n '1,20p' "$HOST_DERIVED_ROOT/hitter_split_inputs_fa_${TODAY}.csv"

docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
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
