import os
import json
import time
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path(os.environ.get("RMT_RAW_ROOT", "/app/data/raw")) / "yahoo"


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except Exception:
        return default


def _body_snippet(resp, limit: int = 300) -> str:
    if resp is None:
        return ""
    return " ".join((resp.text or "").strip().split())[:limit]


def _is_request_denied(status_code, body: str) -> bool:
    return status_code == 999 or "request denied" in (body or "").lower()


def _is_retryable_status(status_code, body: str) -> bool:
    return _is_request_denied(status_code, body) or status_code in {429, 500, 502, 503, 504}


def main():
    team_key = os.environ.get("YAHOO_TEAM_KEY")
    if not team_key:
        raise SystemExit("Missing env var YAHOO_TEAM_KEY (e.g. 458.l.11506.t.1 for 2025 end roster)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/team/{team_key}/roster?format=json"

    max_attempts = max(1, _env_int("YAHOO_ROSTER_MAX_ATTEMPTS", 3))
    backoff_seconds = max(0.0, _env_float("YAHOO_ROSTER_BACKOFF_SECONDS", 30.0))

    last_err = None
    for attempt in range(1, max_attempts + 1):
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
                print(f"YAHOO_ROSTER_RETRY_OK attempt={attempt} team_key={team_key}")
            return
        except Exception as e:
            status_code = getattr(resp, "status_code", "NA")
            body = _body_snippet(resp)

            print(
                f"WARN roster_fetch_fail attempt={attempt}/{max_attempts} team_key={team_key} "
                f"status_code={status_code} error={type(e).__name__} body={body}",
                flush=True,
            )

            last_err = RuntimeError(
                f"Yahoo roster fetch failed after retries: team_key={team_key} "
                f"status_code={status_code} error={type(e).__name__} body={body}"
            )

            if status_code == 403:
                print(
                    f"WARN roster_fetch_no_retry team_key={team_key} "
                    f"status_code={status_code} body={body}",
                    flush=True,
                )
                break

            if attempt < max_attempts:
                if _is_retryable_status(status_code, body):
                    sleep_for = backoff_seconds * attempt
                    print(
                        f"WARN roster_fetch_backoff attempt={attempt}/{max_attempts} "
                        f"team_key={team_key} status_code={status_code} "
                        f"sleep_seconds={sleep_for}",
                        flush=True,
                    )
                else:
                    sleep_for = float(attempt)

                time.sleep(sleep_for)

    raise last_err

if __name__ == "__main__":
    main()
