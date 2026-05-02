import os
import json
import time
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("/app/data/raw/yahoo")

def main():
    team_key = os.environ.get("YAHOO_TEAM_KEY")
    if not team_key:
        raise SystemExit("Missing env var YAHOO_TEAM_KEY (e.g. 458.l.11506.t.1 for 2025 end roster)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/team/{team_key}/roster?format=json"

    last_err = None
    for attempt in range(1, 4):
        resp = None
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            out_path = OUT_DIR / f"team_{team_key.replace('.','_')}_roster.json"
            out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            print("Wrote:", out_path.as_posix())
            if attempt > 1:
                print(f"YAHOO_RETRY_OK attempt={attempt} team_key={team_key}")
            return
        except Exception as e:
            status_code = getattr(resp, "status_code", "NA")
            body = ""
            if resp is not None:
                body = " ".join((resp.text or "").strip().split())[:200]

            print(
                f"WARN roster_fetch_fail attempt={attempt} team_key={team_key} "
                f"status_code={status_code} error={type(e).__name__} body={body}"
            )

            last_err = RuntimeError(
                f"Yahoo roster fetch failed after retries: team_key={team_key} "
                f"status_code={status_code} error={type(e).__name__} body={body}"
            )

            if attempt < 3:
                time.sleep(attempt)

    raise last_err

if __name__ == "__main__":
    main()
