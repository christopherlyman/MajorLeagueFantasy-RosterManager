import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import streamlit as st

from services.rotowire_lineups import fetch_rotowire_lineups, rotowire_cache_status


STATUS_DIR = Path(os.getenv("RMT_STATUS_DIR", "/app/runtime/status"))
LOG_DIR = Path(os.getenv("RMT_LOG_DIR", "/app/runtime/logs"))

REFRESH_LABELS = {
    "quick": "Quick Refresh",
    "daily": "Daily Refresh",
    "full": "Full Refresh",
    "deep": "Deep Refresh",
}


def build_refresh_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()

    app_alias = env.get("APP_ALIAS", "").strip()
    if app_alias:
        env["RMT_ENV_FILE"] = f"/app/instances/{app_alias}/.env"
    else:
        env.setdefault("RMT_ENV_FILE", "/app/.env")

    env.setdefault("RMT_PROJECT_ROOT", "/app")
    env.setdefault("RMT_RAW_ROOT", "/app/data/raw")
    env.setdefault("RMT_DERIVED_ROOT", "/app/data/derived")
    env.setdefault("RMT_SHARED_RAW_ROOT", "/app/data/raw")
    env.setdefault("RMT_LOG_DIR", str(LOG_DIR))
    env.setdefault("RMT_STATUS_DIR", str(STATUS_DIR))

    # When refresh scripts are launched from inside a Streamlit container,
    # docker cp destination paths must be container-visible /app paths.
    env.setdefault("RMT_HOST_RAW_ROOT", env["RMT_RAW_ROOT"])
    env.setdefault("RMT_HOST_DERIVED_ROOT", env["RMT_DERIVED_ROOT"])

    return env


def _parse_utc(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _format_seconds(total_s: int | float | None) -> str:
    if total_s is None:
        return "n/a"

    total_s = int(round(float(total_s)))
    minutes, seconds = divmod(total_s, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _status_elapsed_seconds(data: dict) -> int | None:
    started = _parse_utc(data.get("started_at_utc"))
    finished = _parse_utc(data.get("finished_at_utc"))
    if started and finished:
        return int((finished - started).total_seconds())
    return None


def _log_mode_and_elapsed(path: Path) -> tuple[str | None, int | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None, None

    total_match = re.search(r"RUN_END .* total_elapsed_s=(\d+)", text)
    elapsed = int(total_match.group(1)) if total_match else None

    if path.name.startswith("refresh_live_"):
        return "quick", elapsed

    mode_match = re.search(r"RUN_START .* run_mode=(\w+)", text)
    mode = mode_match.group(1) if mode_match else None
    return mode, elapsed


@st.cache_data(ttl=60)
def load_refresh_telemetry():
    status_rows = []

    for name in ("refresh_live_status.json", "refresh_all_status.json"):
        data = _load_json(STATUS_DIR / name)
        if not data:
            continue

        run_type = str(data.get("run_type") or "")
        run_mode = str(data.get("run_mode") or "")
        mode_key = "quick" if run_type == "live" else run_mode
        label = REFRESH_LABELS.get(mode_key, mode_key.title() if mode_key else "Unknown")

        finished = _parse_utc(data.get("finished_at_utc")) or _parse_utc(data.get("started_at_utc"))
        status_rows.append(
            {
                "finished": finished,
                "mode_key": mode_key,
                "label": label,
                "success": bool(data.get("success")),
                "message": str(data.get("message") or ""),
                "as_of_date": str(data.get("as_of_date") or ""),
                "elapsed_s": _status_elapsed_seconds(data),
            }
        )

    status_rows = [r for r in status_rows if r.get("finished") is not None]
    status_rows.sort(key=lambda r: r["finished"], reverse=True)
    last_refresh = status_rows[0] if status_rows else None

    buckets = {"quick": [], "daily": [], "full": [], "deep": []}
    log_paths = sorted(LOG_DIR.glob("refresh_*.log"), reverse=True)[:80]

    for path in log_paths:
        mode, elapsed = _log_mode_and_elapsed(path)
        if mode in buckets and elapsed is not None:
            buckets[mode].append(elapsed)

    averages = {}
    for mode, vals in buckets.items():
        averages[mode] = round(mean(vals[:8])) if vals else None

    return {"last_refresh": last_refresh, "averages": averages}



def force_rotowire_refresh_for_manual_button() -> dict:
    try:
        fetch_rotowire_lineups(force_refresh=True)
        status = rotowire_cache_status()
        status["success"] = True
        return status
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "team_count": 0,
            "status_counts": {},
            "fetched_at_utc": "",
        }


def render_refresh_sidebar(ctx: dict[str, str]) -> None:
    st.header("Refresh")

    telemetry = load_refresh_telemetry()
    st.caption(f"Active date: {ctx['as_of_date']}")

    last_refresh = telemetry.get("last_refresh")
    if last_refresh:
        icon = "✅" if last_refresh.get("success") else "❌"
        st.caption(
            f"Last: {last_refresh.get('label')} {icon} | "
            f"{_format_seconds(last_refresh.get('elapsed_s'))} | "
            f"{last_refresh.get('as_of_date')}"
        )

    averages = telemetry.get("averages") or {}
    avg_lines = []
    for mode in ("quick", "daily", "full", "deep"):
        avg = averages.get(mode)
        if avg is not None:
            avg_lines.append(f"{REFRESH_LABELS[mode]} avg: {_format_seconds(avg)}")
    if avg_lines:
        st.caption(" | ".join(avg_lines))

    rw_status = st.session_state.get("last_rotowire_refresh_status")
    if isinstance(rw_status, dict) and rw_status:
        if rw_status.get("success"):
            fetched = _parse_utc(rw_status.get("fetched_at_utc"))
            if fetched:
                eastern = fetched.astimezone(ZoneInfo("America/New_York"))
                fetched_text = eastern.strftime("%Y-%m-%d %-I:%M %p %Z")
            else:
                fetched_text = str(rw_status.get("fetched_at_utc") or "")
            st.caption(f"RotoWire last refresh: {fetched_text}")
        else:
            st.caption("RotoWire refresh failed.")

    lock_path = "/tmp/mlf_refresh_all.lock"
    refresh_running = os.path.exists(lock_path)

    st.caption(
        "Quick = roster, games, lineups. "
        "Daily = quick + league rosters + current scoring artifacts. "
        "Full = daily + Yahoo player-pool meta. "
        "Deep = full + Yahoo historical stats."
    )

    refresh_choice = None

    col1, col2 = st.columns(2)
    if col1.button(
        "Quick Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_quick_btn",
    ):
        refresh_choice = ("Quick Refresh", "/app/runtime/refresh_quick.sh")

    if col2.button(
        "Daily Refresh",
        type="primary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_daily_btn",
    ):
        refresh_choice = ("Daily Refresh", "/app/runtime/refresh_daily.sh")

    col3, col4 = st.columns(2)
    if col3.button(
        "Full Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_full_btn",
    ):
        refresh_choice = ("Full Refresh", "/app/runtime/refresh_full.sh")

    if col4.button(
        "Deep Refresh",
        type="secondary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_deep_btn",
    ):
        refresh_choice = ("Deep Refresh", "/app/runtime/refresh_deep.sh")

    if refresh_choice:
        refresh_label, refresh_script = refresh_choice
        try:
            with open(lock_path, "w", encoding="utf-8") as lock_file:
                lock_file.write("running\n")

            with st.spinner(f"{refresh_label} running..."):
                proc = subprocess.run(
                    ["/bin/bash", refresh_script],
                    capture_output=True,
                    text=True,
                    env=build_refresh_subprocess_env(),
                )

            st.session_state["last_refresh_mode"] = refresh_label
            st.session_state["last_refresh_returncode"] = proc.returncode
            st.session_state["last_refresh_stdout"] = proc.stdout[-20000:]
            st.session_state["last_refresh_stderr"] = proc.stderr[-8000:]

            if proc.returncode == 0:
                st.session_state["last_rotowire_refresh_status"] = force_rotowire_refresh_for_manual_button()

                try:
                    st.cache_data.clear()
                    st.cache_resource.clear()
                except Exception:
                    pass
                st.success(f"{refresh_label} completed.")
                st.rerun()
            else:
                st.error(f"{refresh_label} failed.")
        finally:
            if os.path.exists(lock_path):
                os.remove(lock_path)

    if refresh_running:
        st.info("Refresh already running.")

    if "last_refresh_stdout" in st.session_state:
        with st.expander(
            f"Last refresh log ({st.session_state.get('last_refresh_mode', 'Unknown')})"
        ):
            st.code(st.session_state.get("last_refresh_stdout", ""))
            stderr = st.session_state.get("last_refresh_stderr", "")
            if stderr:
                st.code(stderr)
