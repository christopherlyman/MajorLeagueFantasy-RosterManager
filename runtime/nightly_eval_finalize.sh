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
  ALIASES=("usual-rmt")
fi

env_value() {
  local file="$1"
  local key="$2"
  local fallback="${3:-}"
  local value

  value="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"

  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}

container_path_to_host() {
  local raw="$1"

  if [[ "$raw" == /app/* || "$raw" == "/app" ]]; then
    printf '%s\n' "$ROOT${raw#/app}"
  else
    printf '%s\n' "$raw"
  fi
}

run_instance() {
  local app_alias="$1"
  local env_file="$ROOT/instances/${app_alias}/.env"

  if [[ ! -f "$env_file" ]]; then
    echo "SKIP app_alias=$app_alias reason=missing_env_file"
    return 0
  fi

  local container_name league_key team_key status_dir_container log_dir_container
  local status_dir_host log_dir_host lock_file log_file
  local eval_date started_at

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
  mkdir -p "$status_dir_host" "$log_dir_host"

  eval_date="${RMT_EVAL_FINALIZE_DATE:-$(TZ=America/New_York date -d 'yesterday' +%F)}"
  lock_file="$status_dir_host/nightly_eval_finalize.lock"
  log_file="$log_dir_host/nightly_eval_finalize_${eval_date}_$(date +%H%M%S).log"
  started_at="$(date -u +%FT%TZ)"

  echo
  echo "============================================================"
  echo "MORNING EVALUATION FINALIZER"
  echo "DATE=$eval_date"
  echo "LEAGUE=$app_alias"
  echo "LEAGUE_KEY=$league_key"
  echo "TEAM_KEY=$team_key"
  echo "CONTAINER=$container_name"
  echo "============================================================"

  if [[ -f "$lock_file" ]]; then
    echo "SKIP app_alias=$app_alias date=$eval_date reason=lock_exists"
    return 1
  fi

  # Determine whether yesterday actually has an RMT batter analysis.
  set +e
  check_output="$(
    docker exec -i \
      -e CHECK_EVAL_DATE="$eval_date" \
      -e CHECK_LEAGUE_KEY="$league_key" \
      -e CHECK_TEAM_KEY="$team_key" \
      "$container_name" \
      python - <<'PY'
import os
from services.db import get_connection

eval_date = os.environ["CHECK_EVAL_DATE"]
league_key = os.environ["CHECK_LEAGUE_KEY"]
team_key = os.environ["CHECK_TEAM_KEY"]

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT eval_run_id, refresh_label, created_at_utc
            FROM rmt.eval_run
            WHERE league_key = %s
              AND team_key = %s
              AND eval_date = %s::date
              AND app_section = 'batters'
            ORDER BY eval_run_id DESC
            LIMIT 1
            """,
            (league_key, team_key, eval_date),
        )
        row = cur.fetchone()

if not row:
    print("NO_EVAL_RUN")
    raise SystemExit(3)

print(
    f"EVAL_RUN_FOUND eval_run_id={row[0]} "
    f"refresh_label={row[1]} created_at_utc={row[2]}"
)
PY
  )"
  check_rc=$?
  set -e

  printf '%s\n' "$check_output"

  if [[ "$check_rc" -eq 3 ]]; then
    echo "SKIP app_alias=$app_alias date=$eval_date reason=no_eval_run"
    return 0
  elif [[ "$check_rc" -ne 0 ]]; then
    echo "FAILED app_alias=$app_alias date=$eval_date reason=eval_run_check_failed rc=$check_rc" >&2
    return "$check_rc"
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY_RUN would reconstruct Yahoo historical Final roster and refresh actuals."
    return 0
  fi

  echo "$$" > "$lock_file"

  local rc=0
  {
    echo "RUN_START ts=$started_at date=$eval_date league=$app_alias"

    if docker exec -i "$container_name" \
      bash -lc "
        cd /app &&
        python -m services.evaluation_actuals \
          --eval-date '$eval_date' \
          --stat-date '$eval_date' \
          --league-key '$league_key' \
          --team-key '$team_key' \
          --refresh-final-roster \
          --refresh-actuals
      "
    then
      echo "RUN_END ts=$(date -u +%FT%TZ) success=true"
    else
      rc=$?
      echo "RUN_END ts=$(date -u +%FT%TZ) success=false rc=$rc"
    fi
  } >> "$log_file" 2>&1

  rm -f "$lock_file"

  if [[ "$rc" -eq 0 ]]; then
    echo "OK date=$eval_date league=$app_alias log_file=$log_file"
  else
    echo "FAILED date=$eval_date league=$app_alias rc=$rc log_file=$log_file" >&2
  fi

  return "$rc"
}

overall_rc=0

for app_alias in "${ALIASES[@]}"; do
  if ! run_instance "$app_alias"; then
    overall_rc=1
  fi
done

exit "$overall_rc"
