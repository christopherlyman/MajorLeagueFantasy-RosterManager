import json
import html
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


NIGHTLY_YAHOO_CONFIG_FILENAME = "nightly_yahoo_season_stats.enabled"
NIGHTLY_YAHOO_STATUS_FILENAME = "nightly_yahoo_season_stats_status.json"


def _nightly_yahoo_config_dir() -> Path:
    explicit = os.getenv("RMT_CONFIG_DIR", "").strip()
    if explicit:
        return Path(explicit)

    return STATUS_DIR.parent / "config"


def _nightly_yahoo_enabled_path() -> Path:
    return _nightly_yahoo_config_dir() / NIGHTLY_YAHOO_CONFIG_FILENAME


def _nightly_yahoo_status_path() -> Path:
    return STATUS_DIR / NIGHTLY_YAHOO_STATUS_FILENAME


def load_nightly_yahoo_enabled() -> bool:
    path = _nightly_yahoo_enabled_path()
    if not path.exists():
        return False

    try:
        value = path.read_text(encoding="utf-8").strip().lower()
    except Exception:
        return False

    return value in {"1", "true", "yes", "on", "enabled"}


def set_nightly_yahoo_enabled(enabled: bool) -> None:
    path = _nightly_yahoo_enabled_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("enabled\n" if enabled else "disabled\n", encoding="utf-8")


def load_nightly_yahoo_status() -> dict:
    return _load_json(_nightly_yahoo_status_path())


def render_nightly_yahoo_controls() -> None:
    app_alias = os.getenv("APP_ALIAS", "unknown")
    enabled = load_nightly_yahoo_enabled()
    status = load_nightly_yahoo_status()

    st.caption(
        "Nightly Yahoo season stats: "
        + ("ON" if enabled else "OFF")
        + f" | Instance: {app_alias}"
    )

    if status:
        icon = "✅" if status.get("success") else "❌"
        skipped = " skipped" if status.get("skipped") else ""
        elapsed = _format_seconds(status.get("elapsed_s"))
        st.caption(
            f"Nightly last: {icon}{skipped} | "
            f"{status.get('as_of_date', '')} | "
            f"{elapsed} | "
            f"{status.get('message', '')}"
        )

    toggle_value = st.toggle(
        "Enable nightly Yahoo season stats",
        value=enabled,
        key=f"nightly_yahoo_enabled_toggle_{app_alias}",
        help=(
            "When enabled, the 3am host scheduled task will refresh Yahoo player-pool "
            "metadata and current-season stats for this RMT instance."
        ),
    )

    if toggle_value != enabled:
        set_nightly_yahoo_enabled(toggle_value)
        if toggle_value:
            st.success("Nightly Yahoo season stats enabled for this RMT instance.")
        else:
            st.success("Nightly Yahoo season stats disabled for this RMT instance.")
        st.rerun()


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
                "current_stage": str(data.get("current_stage") or ""),
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

    nightly_yahoo_status = _load_json(STATUS_DIR / NIGHTLY_YAHOO_STATUS_FILENAME)
    return {
        "last_refresh": last_refresh,
        "status_rows": status_rows,
        "averages": averages,
        "nightly_yahoo_status": nightly_yahoo_status,
    }


def _is_yahoo_api_blocked_status(row: dict) -> bool:
    if row.get("success"):
        return False

    haystack = " ".join(
        [
            str(row.get("current_stage") or ""),
            str(row.get("message") or ""),
        ]
    ).lower()

    return (
        "yahoo" in haystack
        or "403" in haystack
        or "not authorized" in haystack
        or "application is not authorized" in haystack
    )


def _derive_yahoo_api_badge(telemetry: dict, active_date: str) -> tuple[str, str, str]:
    rows = telemetry.get("status_rows") or []
    active_date = str(active_date or "")

    if rows:
        latest = rows[0]
        recent_rows = rows[:2]

        if _is_yahoo_api_blocked_status(latest) or any(
            _is_yahoo_api_blocked_status(row) for row in recent_rows
        ):
            return (
                "Blocked",
                "#c62828",
                "Yahoo Fantasy API rejected the app/token. Yahoo-dependent data may be stale.",
            )

        if latest.get("success") and str(latest.get("as_of_date") or "") == active_date:
            return (
                "OK",
                "#2e7d32",
                "Yahoo-dependent refresh completed for the active date.",
            )

    nightly = telemetry.get("nightly_yahoo_status") or {}
    if (
        nightly.get("success")
        and not nightly.get("skipped")
        and str(nightly.get("as_of_date") or "") == active_date
    ):
        return (
            "OK",
            "#2e7d32",
            "Nightly Yahoo season stats completed for the active date.",
        )

    return (
        "Stale",
        "#f9ab00",
        "Yahoo API status is not current for the active date.",
    )


def render_yahoo_api_badge(telemetry: dict, active_date: str) -> None:
    label, color, help_text = _derive_yahoo_api_badge(telemetry, active_date)

    st.caption("Yahoo API:")
    st.markdown(
        (
            '<div title="{help_text}" '
            'style="display:flex;align-items:center;gap:0.4rem;margin-top:-0.35rem;margin-bottom:0.35rem;">'
            '<span style="display:inline-block;width:0.7rem;height:0.7rem;'
            'border-radius:50%;background:{color};"></span>'
            '<span style="font-weight:600;">{label}</span>'
            '</div>'
        ).format(
            help_text=html.escape(help_text, quote=True),
            color=color,
            label=html.escape(label),
        ),
        unsafe_allow_html=True,
    )



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
    render_yahoo_api_badge(telemetry, ctx.get("as_of_date", ""))
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
        "Recommendations = quick + recommendation inputs/action plan. "
        "Daily = full daily rebuild. "
        "Full/Deep = deeper Yahoo maintenance."
    )

    render_nightly_yahoo_controls()

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

    if st.button(
        "Recommendations Refresh",
        type="primary",
        use_container_width=True,
        disabled=refresh_running,
        key="refresh_recommendations_btn",
        help="Refresh live/date context and recommendation inputs, then rebuild the Daily Action Plan.",
    ):
        refresh_choice = ("Recommendations Refresh", "/app/runtime/refresh_recommendations.sh")

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
                st.session_state["last_successful_refresh_label_for_post_rerun"] = refresh_label

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
