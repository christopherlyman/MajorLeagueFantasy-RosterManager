#!/usr/bin/env bash
set -euo pipefail

APP_ALIAS="${APP_ALIAS:-usual-rmt}"
CONTAINER_NAME="${RMT_CONTAINER_NAME:-usual-rmt}"
EVAL_DATE="${1:-$(TZ=America/New_York date +%F)}"
STAT_DATE="${2:-$EVAL_DATE}"

shift $(( $# >= 1 ? 1 : 0 )) || true
shift $(( $# >= 1 ? 1 : 0 )) || true

docker exec -i "$CONTAINER_NAME" bash -lc \
  "cd /app && python -m services.evaluation_actuals --eval-date '$EVAL_DATE' --stat-date '$STAT_DATE' $*"
