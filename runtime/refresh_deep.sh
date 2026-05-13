#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="${RMT_PROJECT_ROOT:-/app}"
else
  ROOT="${RMT_PROJECT_ROOT:-/Volume1/Bots/fantasy/mlf_roster_manager}"
fi

TODAY="${1:-$(TZ=America/New_York date +%F)}"
exec env REFRESH_ALL_MODE=deep "$ROOT/runtime/refresh_all.sh" "$TODAY"
