#!/usr/bin/env bash
set -euo pipefail

TODAY="${1:-$(TZ=America/New_York date +%F)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REFRESH_ALL_MODE=recommendations "$SCRIPT_DIR/refresh_all.sh" "$TODAY"
