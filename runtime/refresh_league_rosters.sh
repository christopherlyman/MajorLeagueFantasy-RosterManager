#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app/runtime" ]]; then
  ROOT="${RMT_PROJECT_ROOT:-/app}"
else
  ROOT="${RMT_PROJECT_ROOT:-/Volume1/Bots/fantasy/mlf_roster_manager}"
fi

APP_CONTAINER="${RMT_CONTAINER_NAME:-mlf_roster_manager}"
ENV_FILE="${RMT_ENV_FILE:-$ROOT/.env}"
APP_RAW_ROOT="${RMT_RAW_ROOT:-/app/data/raw}"
APP_DERIVED_ROOT="${RMT_DERIVED_ROOT:-/app/data/derived}"
HOST_RAW_ROOT="${RMT_HOST_RAW_ROOT:-$ROOT/data/raw}"
HOST_DERIVED_ROOT="${RMT_HOST_DERIVED_ROOT:-$ROOT/data/derived}"
TODAY="${1:-$(TZ=America/New_York date +%F)}"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

DEFAULT_LEAGUE_KEY=$(grep '^DEFAULT_LEAGUE_KEY=' "$ENV_FILE" | cut -d= -f2-)
[[ -n "$DEFAULT_LEAGUE_KEY" ]] || { echo "DEFAULT_LEAGUE_KEY missing" >&2; exit 1; }

SEASON_YEAR="${TODAY:0:4}"

mapfile -t TEAM_KEYS < <(
docker exec -i \
  -e RMT_RAW_ROOT="$APP_RAW_ROOT" \
  -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" \
  -e DEFAULT_LEAGUE_KEY="$DEFAULT_LEAGUE_KEY" \
  -e SEASON_YEAR="$SEASON_YEAR" \
  "$APP_CONTAINER" bash -lc "cd /app && python - <<'PYTEAM'
from services.db import get_connection
import os

league_key = os.environ['DEFAULT_LEAGUE_KEY']
season_year = int(os.environ['SEASON_YEAR'])

sql = '''
SELECT team_key
FROM lineup_tool.team_map
WHERE league_key = %s
  AND season_year = %s
ORDER BY team_key
'''

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(sql, (league_key, season_year))
        rows = cur.fetchall()

for row in rows:
    print(row[0])
PYTEAM"
)

echo "TODAY=$TODAY"
echo "LEAGUE=$DEFAULT_LEAGUE_KEY"
echo "SEASON_YEAR=$SEASON_YEAR"
echo "TEAM_COUNT=${#TEAM_KEYS[@]}"

for TEAM_KEY in "${TEAM_KEYS[@]}"; do
  SAFE_TEAM_KEY=${TEAM_KEY//./_}

  echo
  echo "============================================================"
  echo "TEAM_KEY=$TEAM_KEY"
  echo "============================================================"

  docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app/scripts/yahoo && YAHOO_TEAM_KEY=$TEAM_KEY python yahoo_team_roster.py
"

  RAW_ROSTER_HOST="$HOST_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json"
  docker cp     "${APP_CONTAINER}:$APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster.json"     "$RAW_ROSTER_HOST"

  echo "COPIED $RAW_ROSTER_HOST"

  docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && python scripts/build_roster_snapshot.py   --src $APP_RAW_ROOT/yahoo/team_${SAFE_TEAM_KEY}_roster_${TODAY}.json   --out $APP_DERIVED_ROOT/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv
"

  SNAPSHOT_HOST="$HOST_DERIVED_ROOT/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv"
  python3 - "$SNAPSHOT_HOST" "$TODAY" <<'PYNORM'
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
PYNORM

  docker exec -i -e RMT_RAW_ROOT="$APP_RAW_ROOT" -e RMT_DERIVED_ROOT="$APP_DERIVED_ROOT" "$APP_CONTAINER" bash -lc "
cd /app && PYTHONPATH=/app python scripts/load_roster_snapshot.py   --src $APP_DERIVED_ROOT/team_${SAFE_TEAM_KEY}_roster_${TODAY}_snapshot.csv
"
done

echo
echo "DONE refresh_league_rosters.sh"
