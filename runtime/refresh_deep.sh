#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="/app"
else
  ROOT="/Volume1/Bots/fantasy/mlf_roster_manager"
fi

TODAY="${1:-$(TZ=America/New_York date +%F)}"
exec env REFRESH_ALL_MODE=deep "$ROOT/runtime/refresh_all.sh" "$TODAY"
