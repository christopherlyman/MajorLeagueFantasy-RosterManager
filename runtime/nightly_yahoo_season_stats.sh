#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="${RMT_PROJECT_ROOT:-/app}"
else
  ROOT="${RMT_PROJECT_ROOT:-/Volume1/Bots/fantasy/mlf_roster_manager}"
fi

DRY_RUN=false
ALIASES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all-enabled)
      ALIASES=("usual-rmt" "mlf-rmt" "milf-rmt")
      shift
      ;;
    --app-alias)
      [[ $# -ge 2 ]] || { echo "ERROR: --app-alias requires a value" >&2; exit 2; }
      ALIASES+=("$2")
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ${#ALIASES[@]} -eq 0 ]]; then
  current_alias="${APP_ALIAS:-}"
  if [[ -n "$current_alias" ]]; then
    ALIASES=("$current_alias")
  else
    ALIASES=("usual-rmt")
  fi
fi

container_path_to_host() {
  local raw="$1"
  if [[ "$raw" == /app/* || "$raw" == "/app" ]]; then
    printf '%s\n' "$ROOT${raw#/app}"
  else
    printf '%s\n' "$raw"
  fi
}

env_value() {
  local file="$1"
  local key="$2"
  local fallback="${3:-}"
  local value
  value="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}

json_status() {
  local status_file="$1"
  local app_alias="$2"
  local container_name="$3"
  local league_key="$4"
  local season_year="$5"
  local as_of_date="$6"
  local enabled="$7"
  local skipped="$8"
  local success="$9"
  local stage="${10}"
  local message="${11}"
  local log_file="${12}"
  local started_at="${13}"
  local finished_at="${14}"
  local elapsed_s="${15}"

  mkdir -p "$(dirname "$status_file")"

  python3 - "$status_file" "$app_alias" "$container_name" "$league_key" "$season_year" "$as_of_date" "$enabled" "$skipped" "$success" "$stage" "$message" "$log_file" "$started_at" "$finished_at" "$elapsed_s" <<'PY'
import json
import sys
from pathlib import Path

(
    status_file,
    app_alias,
    container_name,
    league_key,
    season_year,
    as_of_date,
    enabled,
    skipped,
    success,
    stage,
    message,
    log_file,
    started_at,
    finished_at,
    elapsed_s,
) = sys.argv[1:]

payload = {
    "run_type": "nightly_yahoo_season_stats",
    "app_alias": app_alias,
    "container_name": container_name,
    "league_key": league_key,
    "season_year": int(season_year),
    "as_of_date": as_of_date,
    "enabled": enabled.lower() == "true",
    "skipped": skipped.lower() == "true",
    "success": success.lower() == "true",
    "current_stage": stage,
    "message": message,
    "log_file": log_file,
    "started_at_utc": started_at,
    "finished_at_utc": finished_at,
    "elapsed_s": int(float(elapsed_s)) if str(elapsed_s).strip() else None,
}

Path(status_file).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"WROTE_STATUS {status_file}")
PY
}

is_enabled_file_on() {
  local enabled_file="$1"
  [[ -f "$enabled_file" ]] || return 1

  local value
  value="$(tr '[:upper:]' '[:lower:]' < "$enabled_file" | tr -d '[:space:]')"

  case "$value" in
    1|true|yes|on|enabled) return 0 ;;
    *) return 1 ;;
  esac
}

run_instance() {
  local app_alias="$1"
  local env_file="$ROOT/instances/${app_alias}/.env"

  if [[ ! -f "$env_file" ]]; then
    echo "SKIP app_alias=$app_alias reason=missing_env_file env_file=$env_file"
    return 0
  fi

  local container_name league_key team_key status_dir_container log_dir_container status_dir_host log_dir_host config_dir_host enabled_file status_file lock_file
  container_name="$(env_value "$env_file" "RMT_CONTAINER_NAME" "$app_alias")"
  league_key="$(env_value "$env_file" "DEFAULT_LEAGUE_KEY" "")"
  team_key="$(env_value "$env_file" "DEFAULT_TEAM_KEY" "")"
  status_dir_container="$(env_value "$env_file" "RMT_STATUS_DIR" "/app/instances/${app_alias}/status")"
  log_dir_container="$(env_value "$env_file" "RMT_LOG_DIR" "/app/instances/${app_alias}/logs")"

  if [[ -z "$league_key" || -z "$team_key" ]]; then
    echo "SKIP app_alias=$app_alias reason=missing_league_or_team_key"
    return 0
  fi

  status_dir_host="$(container_path_to_host "$status_dir_container")"
  log_dir_host="$(container_path_to_host "$log_dir_container")"

  config_dir_host="$(dirname "$status_dir_host")/config"
  enabled_file="$config_dir_host/nightly_yahoo_season_stats.enabled"
  status_file="$status_dir_host/nightly_yahoo_season_stats_status.json"
  lock_file="$status_dir_host/nightly_yahoo_season_stats.lock"

  local today season_year started_at finished_at start_epoch end_epoch elapsed_s log_file
  today="${RMT_NIGHTLY_AS_OF_DATE:-$(TZ=America/New_York date +%F)}"
  season_year="${RMT_NIGHTLY_SEASON_YEAR:-${today:0:4}}"

  mkdir -p "$status_dir_host" "$log_dir_host"
  started_at="$(date -u +%FT%TZ)"
  start_epoch="$(date +%s)"
  log_file="$log_dir_host/nightly_yahoo_season_stats_${today}_$(date +%H%M%S).log"

  if ! is_enabled_file_on "$enabled_file"; then
    finished_at="$(date -u +%FT%TZ)"
    end_epoch="$(date +%s)"
    elapsed_s=$((end_epoch - start_epoch))
    json_status "$status_file" "$app_alias" "$container_name" "$league_key" "$season_year" "$today" false true true "disabled" "Nightly Yahoo season stats disabled for this instance." "$log_file" "$started_at" "$finished_at" "$elapsed_s"
    echo "SKIP app_alias=$app_alias enabled=false"
    return 0
  fi

  if [[ -f "$lock_file" ]]; then
    finished_at="$(date -u +%FT%TZ)"
    end_epoch="$(date +%s)"
    elapsed_s=$((end_epoch - start_epoch))
    json_status "$status_file" "$app_alias" "$container_name" "$league_key" "$season_year" "$today" true true false "locked" "Nightly Yahoo season stats already running or stale lock exists." "$log_file" "$started_at" "$finished_at" "$elapsed_s"
    echo "SKIP app_alias=$app_alias reason=lock_exists lock_file=$lock_file"
    return 1
  fi

  echo "$$" > "$lock_file"

  local rc=0
  {
    echo "RUN_START ts=$started_at app_alias=$app_alias container=$container_name league_key=$league_key season_year=$season_year as_of_date=$today dry_run=$DRY_RUN"

    if [[ "$DRY_RUN" == "true" ]]; then
      echo "DRY_RUN would refresh Yahoo player pool meta and season stats."
    else
      echo
      echo "STAGE_START stage=refresh_yahoo_player_pool_meta"
      docker exec -i \
        -e YAHOO_LEAGUE_KEY="$league_key" \
        -e SEASON_YEAR="$season_year" \
        -e PLAYER_POOL_REFRESH_MODE="${RMT_NIGHTLY_PLAYER_POOL_REFRESH_MODE:-meta_only}" \
        -e YAHOO_SLEEP_SECONDS="${RMT_NIGHTLY_YAHOO_PLAYER_POOL_SLEEP_SECONDS:-1}" \
        -e YAHOO_REQUEST_MAX_ATTEMPTS="${YAHOO_REQUEST_MAX_ATTEMPTS:-4}" \
        -e YAHOO_REQUEST_BACKOFF_SECONDS="${YAHOO_REQUEST_BACKOFF_SECONDS:-20}" \
        "$container_name" bash -lc 'cd /app/scripts/yahoo && POSTGRES_DSN=${POSTGRES_DSN:-$MLF_POSTGRES_DSN} python yahoo_league_player_pool_load.py'

      echo
      echo "STAGE_START stage=refresh_yahoo_season_stats_full_universe"
      docker exec -i \
        -e YAHOO_LEAGUE_KEY="$league_key" \
        -e YAHOO_STATS_SEASON="$season_year" \
        -e YAHOO_GAME_KEY="${YAHOO_GAME_KEY:-469}" \
        -e YAHOO_BATCH_SIZE="${RMT_NIGHTLY_YAHOO_BATCH_SIZE:-25}" \
        -e YAHOO_SLEEP_SECONDS="${RMT_NIGHTLY_YAHOO_SLEEP_SECONDS:-0.25}" \
        -e YAHOO_FETCH_META="${RMT_NIGHTLY_YAHOO_FETCH_META:-false}" \
        -e YAHOO_WRITE_RAW="${RMT_NIGHTLY_YAHOO_WRITE_RAW:-false}" \
        "$container_name" bash -lc 'cd /app/scripts/yahoo && POSTGRES_DSN=${POSTGRES_DSN:-$MLF_POSTGRES_DSN} python yahoo_bulk_load.py'
    fi

    echo "RUN_END ts=$(date -u +%FT%TZ) success=true"
  } >> "$log_file" 2>&1 || rc=$?

  rm -f "$lock_file"

  finished_at="$(date -u +%FT%TZ)"
  end_epoch="$(date +%s)"
  elapsed_s=$((end_epoch - start_epoch))

  if [[ "$rc" -eq 0 ]]; then
    json_status "$status_file" "$app_alias" "$container_name" "$league_key" "$season_year" "$today" true false true "complete" "Nightly Yahoo season stats completed." "$log_file" "$started_at" "$finished_at" "$elapsed_s"
    echo "OK app_alias=$app_alias log_file=$log_file"
  else
    json_status "$status_file" "$app_alias" "$container_name" "$league_key" "$season_year" "$today" true false false "failed" "Nightly Yahoo season stats failed." "$log_file" "$started_at" "$finished_at" "$elapsed_s"
    echo "FAILED app_alias=$app_alias rc=$rc log_file=$log_file" >&2
    return "$rc"
  fi
}

overall_rc=0
for app_alias in "${ALIASES[@]}"; do
  echo
  echo "============================================================"
  echo "NIGHTLY YAHOO SEASON STATS app_alias=$app_alias"
  echo "============================================================"
  if ! run_instance "$app_alias"; then
    overall_rc=1
  fi
done

exit "$overall_rc"
