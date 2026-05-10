# 1_Project_Structure

## Purpose
This document defines the intended folder layout for the MajorLeagueFantasy-RosterManager project and separates source code from generated runtime artifacts, DB-managed state, temporary probe/debug output, and local runtime configuration.

Last updated: 2026-05-10

---

## Source-of-truth structure

### Top level
- `docs/` — project documentation, handoff notes, canonicals, and runbooks
- `runtime/` — orchestration scripts, Docker Compose, status folders, and log folders
- `scripts/` — executable ingestion/build scripts
- `services/` — reusable Python logic for DB access, row assembly, queries, scoring, and league profiles
- `pages/` — Streamlit page modules
- `streamlit_app.py` — Streamlit router / app entrypoint
- `.env` — local runtime configuration; **not for Git**
- `Dockerfile`, `requirements.txt`, `LICENSE` — project metadata/build inputs

### Current Streamlit UI structure
Streamlit is the only active UI target.

- `streamlit_app.py`
  - Thin Streamlit router only
  - Uses `st.Page(...)` and `st.navigation(...)`
  - Sidebar pages are:
    - `Batters`
    - `Pitchers`

- `pages/batters.py`
  - Primary working batter UI
  - Contains the current batter workflow:
    - `Starting Lineup`
    - `Slots`
    - `Batter Free Agents`
  - This file now holds the substantial UI code that previously lived in `streamlit_app.py`.

- `pages/pitchers.py`
  - Streamlit pitcher page shell
  - Reserved for the next major workflow:
    - roster pitchers
    - daily pitcher decisions
    - pitcher free agents
    - pitcher ranking / optimization

### Primary application files
- `streamlit_app.py` — Streamlit router / entrypoint
- `pages/batters.py` — primary batter UI and current application workspace
- `pages/pitchers.py` — pitcher workflow shell
- `services/queries.py` — batter row assembly, data loading, date context resolution, FA row assembly, slot usage queries, unavailable-game classification, postponed-game display, and collapsed unavailable ranking
- `services/scoring.py` — batter scoring / ranking logic, including DTD status-risk penalty and unavailable game/status overrides
- `runtime/refresh_live.sh` — lighter refresh path
- `runtime/refresh_all.sh` — full refresh path; also generates the Yahoo-confirmed active batter free-agent candidate CSV
- `runtime/refresh_league_rosters.sh` — roster snapshot refresh orchestration

### Scripts layout
- `scripts/yahoo/` — Yahoo-authenticated acquisition / load scripts
  - examples: `auth.py`, `yahoo_team_roster.py`, `refresh_recent_yahoo_api.py`, `yahoo_bulk_load.py`, `yahoo_league_player_pool_load.py`
- `scripts/` root — MLB/splits/roster pipeline scripts that are project-local but not Yahoo-auth specific
  - examples:
    - `refresh_starting_lineups.py`
    - `refresh_mlb_probable_pitcher_daily.py` — preserves MLB `status` inside `raw_json` for postponed-game detection
    - `refresh_probable_pitcher_hand.py`
    - `build_mlbam_player_map.py`
    - `refresh_hitter_splits_mlb.py`

### Data layout
- `data/raw/` — generated raw source pulls
- `data/derived/` — generated transformed outputs used by the app
- Generated data is not source code and should not be hand-edited.

### Runtime layout
- `runtime/docker-compose.yml` — local container orchestration
- `runtime/refresh_*.sh` — refresh wrappers
- `runtime/logs/` — generated logs
- `runtime/status/` — generated status files

---

## Runtime environment and date context

### `.env`
`.env` is local runtime configuration and must not be committed.

Important current date behavior:
- `DEFAULT_AS_OF_DATE=` blank means use **today in America/New_York**
- `DEFAULT_DATE_OFFSET_DAYS=0` means today
- `DEFAULT_DATE_OFFSET_DAYS=1` means tomorrow
- `DEFAULT_DATE_OFFSET_DAYS=2` means day after tomorrow
- `DEFAULT_AS_OF_DATE=YYYY-MM-DD` still works as an intentional fixed-date override

### Date-resolution code
- `services/queries.py::resolve_as_of_date(...)`
  - central date resolver
  - uses America/New_York midnight boundary
  - supports explicit date override and integer offset days
- `services/queries.py::get_default_context()`
  - resolves `league_key`, `team_key`, and `as_of_date` for service-layer scripts
- `pages/batters.py::get_runtime_context()`
  - reads `/app/.env`
  - uses `resolve_as_of_date(...)`
  - keeps Streamlit UI aligned with service-layer date behavior

### Operational caution
The project hit a real `.env` formatting issue where literal `\n` text replaced real line breaks and caused the database DSN to absorb later keys. Future `.env` edits should preserve real newline separators.

Sanitized proof command:
```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

python3 - << 'PY'
from pathlib import Path
raw = Path(".env").read_bytes()
print("REAL_NEWLINE_COUNT", raw.count(b"\n"))
print("LITERAL_BACKSLASH_N_COUNT", raw.count(b"\\n"))
print("KEYS_ONLY")
for line in raw.decode("utf-8", errors="replace").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        print(line.split("=", 1)[0])
PY
```

Expected:
- `REAL_NEWLINE_COUNT` greater than zero
- `LITERAL_BACKSLASH_N_COUNT = 0`

### Container recreation note
When `.env` values that are injected through Docker Compose `env_file` change, a plain `docker restart` may preserve stale container environment variables. Use a controlled recreate of the app service when needed:

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1
docker-compose -f runtime/docker-compose.yml up -d --force-recreate --no-deps roster_manager
```

---

## Generated / non-source artifacts
These are expected to exist during operation but are **not** source-of-truth code.

### Generated raw captures
- `data/raw/yahoo/` — raw Yahoo payload captures
- `data/raw/yahoo/probes/` — temporary probe outputs used for endpoint discovery / debugging
- `scripts/yahoo/data/` — should be treated as generated/probe output unless explicitly converted into source-controlled fixtures

### Generated derived inputs
- `data/derived/starting_lineup_players_<date>.csv`
- `data/derived/starting_lineup_teams_<date>.csv`
- `data/derived/recent7_hitter_inputs_<date>.csv`
- `data/derived/hitter_split_inputs_<date>.csv`
- `data/derived/true_free_agent_batters_<date>.csv` — generated batter FA candidate input; despite the legacy filename, this now means Yahoo-confirmed `status=FA`, active, batter-only candidates that pass the refresh filter
- other app input CSVs produced by refresh pipelines

`true_free_agent_batters_<date>.csv` is generated, not hand-edited. Its source contract is documented here because it prevents waiver/IL leakage:
- Yahoo endpoint pattern: `/league/{league_key}/players;status=FA;sort=OR;start={n};count=25;out=percent_owned?format=json`
- `start` increments by `25`
- generated rows are later enriched by `services/queries.py`

### Runtime status / logs
- `runtime/logs/` and `runtime/status/` are generated operational artifacts and should not be treated as source code.

---

## DB-managed state (not repo files)
Some important project state now lives in Postgres and is therefore **not** represented as files in the repo.

### Seeded / rolling slot-cap state
- `lineup_tool.roster_snapshot` — daily roster snapshot truth, including `selected_position`
- `lineup_tool.slot_usage_seed` — seeded hitter slot usage baseline used to maintain remaining starts automatically

Rule:
- DB tables are operational state, not Git-tracked source files
- code may depend on them, but schema/data changes should be documented explicitly in docs and migration/runbook notes

---

## Current confirmed feature placement

### Batters page
`pages/batters.py` owns the current batter workflow.

Implemented / usable:
- Starting Lineup
- Slots
- Batter Free Agents
- compressed Rank Reason display
- sidebar refresh controls
- slot override controls
- slot cap source expander
- threshold column
- threshold-based starter row highlighting
- combined starter/bench table
- `S = Status` Rank Reason component
- DTD status-risk penalty display
- postponed-game display and unavailable ranking

### Batter Free Agents
The Batter Free Agents tab is wired and usable.

Confirmed behavior:
- `runtime/refresh_all.sh` generates `data/derived/true_free_agent_batters_<date>.csv` from Yahoo's public Fantasy API.
- The current Yahoo source is:
  - `/league/{league_key}/players;status=FA;sort=OR;start={n};count=25;out=percent_owned?format=json`
- Pagination must increment by `25`, because Yahoo returns 25 players per page.
- `;out=percent_owned` is the proven valid syntax; `/out=percent_owned` fails.
- The generator excludes:
  - waiver players
  - pitchers
  - `IL`, `IL10`, `IL15`, `IL60`
  - `NA`
  - `SUSP`
  - candidates whose MLB team is not playing today
  - candidates that fail `rank_value <= 600 AND percent_owned > 0`
- `fetch_available_batter_rows(...)` then assembles game, lineup, scoring, status, and ranking context for the UI.
- FA rows include eligibility, ranking, game context, lineup status, active status, and rank reason.

Confirmed proof from `2026-05-10` after commit `9ee6129`:
- generated CSV rows: `46`
- app FA rows: `45`
- Chase Meidroth included
- Jose Altuve excluded because he was not Yahoo `status=FA`
- Luis Campusano excluded because he was `IL10`
- `HAS_CHASE_MEIDROTH True`
- `HAS_JOSE_ALTUVE False`
- `HAS_LUIS_CAMPUSANO False`

Important implementation note:
- Do not revert this to a DB anti-roster join. That reintroduced waiver and unavailable-player leakage.
- The broad DB player pool remains useful for rank/Ros% metadata, but Yahoo `status=FA` is the availability source of truth.

### Pitchers page
`pages/pitchers.py` is currently only a Streamlit shell.

Next major feature work should start here, after read-only proof of current pitcher-related source files and data sources.

---

## Structural notes learned from recent work
- Streamlit is the only active UI target.
- `streamlit_app.py` is now the router, not the main UI workspace.
- `pages/batters.py` is the current main working UI page.
- `pages/pitchers.py` is reserved for pitcher workflow build-out.
- Old Dash-style page placeholders were replaced.
- The internal main-page `Pitchers` tab was removed from the batter UI.
- Starting Lineup display logic is rendered from a combined lineup/bench table in `pages/batters.py`.
- Slot-threshold logic is tied to schedule-pressure calculations plus remaining-start counts from DB-backed slot usage state.
- Remaining starts are no longer intended to rely on manual sidebar entry as the primary source; manual override is only a fallback/debug path.
- Yahoo Fantasy API auth and team endpoints are in active use, but the live Yahoo max-games table was **not** proven to exist in the tested structured team endpoints; therefore the current design seeds once and rolls forward internally.
- Lineup matching was proven functional for the 2026-05-04 proof cycle:
  - posted-team/player matching produced `IN_POSTED_LINEUP`
  - posted-team/player absence produced `POSTED_BUT_NOT_FOUND`
  - no-game rows produced `LINEUP_NOT_APPLICABLE`
  - observed `POSTED_BUT_NOT_FOUND` rows were true absences from posted lineups, not matching failures
- MLB game status is now preserved in `mlb_probable_pitcher_daily.raw_json` during probable-pitcher refresh.
- Postponed rows use `LINEUP_NOT_APPLICABLE`, display `Postponed - <reason>` when available, and rank `0`.
- Threshold logic was intentionally left unchanged during the unavailable/postponed-game fix because it was already dynamic.
- Batter Free Agent discovery now uses Yahoo `status=FA`, `sort=OR`, `count=25`, `start += 25`, and `;out=percent_owned`.
- The earlier broad `status=FA` scan was too light because it did not use the proven sorted/paginated source and missed players such as Chase Meidroth.
- The current FA generator is the active path in `runtime/refresh_all.sh`; no new exporter script or table was added.

---

## Cleanup rules
- No `.env` in Git
- No `.env.backup_*` files in the repo tree
- No `*.bak` files in the repo tree
- No `*.bak_*` files in the repo tree
- No `*.backup_*` files in the repo tree
- No `__pycache__` or `*.pyc`
- No seam/test files such as `*SEAM1_TEST*` or `*.rmt_test.csv`
- No ad hoc duplicate script trees
- Prefer one approved path per concern
- Do not commit temporary probe outputs under `data/raw/yahoo/probes/` unless explicitly converting them into a permanent test fixture
- Do not commit `scripts/yahoo/data/` unless explicitly proven to be source-controlled fixture data
- Do not commit generated runtime logs or status files

---

## Naming rules
- Use `scripts/yahoo/`, not temporary names like `scripts/rmt_yahoo/`
- Keep generated files under `data/` or `runtime/`, never beside source unless explicitly required
- Keep DB-backed operational state out of file-based source trees
- Use `pages/batters.py` and `pages/pitchers.py` for Streamlit page modules
- Keep `streamlit_app.py` as the thin Streamlit router

---

## Source vs generated decision rule
When deciding where something belongs:
- **Source code** → `scripts/`, `services/`, `pages/`, `streamlit_app.py`
- **Documentation** → `docs/`
- **Generated runtime artifact** → `data/` or `runtime/`
- **Operational DB state** → database table, documented in docs/runbooks, not as an ad hoc file
- **Local configuration** → `.env`, never committed
- **Temporary probes** → do not commit unless promoted into intentional source/test fixtures

---

## Git workflow
Git is run from the user's personal laptop, not directly on the NAS SSH shell.

### Current workflow
- Runtime/Docker commands: NAS SSH shell
- Git commands: Windows PowerShell from:
  - `\\Apollo\Bots\fantasy\mlf_roster_manager`

### Known GitHub repo
- GitHub: `https://github.com/christopherlyman/MajorLeagueFantasy-RosterManager`
- Remote: `origin`
- Default branch: `main`
- License: `MIT`

### Recent code checkpoints
Committed and pushed:
- `824d2a2 Handle unavailable batters and postponed games`
- `9ee6129 Fix Yahoo free agent batter discovery`

`824d2a2` included:
- IL/NA Free Agent exclusion from Yahoo `eligible_positions`
- DTD status-risk penalty
- MLB status preservation in probable-pitcher refresh
- postponed-game classification and display
- postponed-game unavailable ranking
- Rank Reason `S = Status` UI support

`9ee6129` included:
- replacement of the Batter FA discovery source in `runtime/refresh_all.sh`
- Yahoo `status=FA` discovery using `sort=OR`, 25-row pagination, and `;out=percent_owned`
- exclusion proof for waiver Jose Altuve
- exclusion proof for IL10 Luis Campusano
- inclusion proof for Chase Meidroth

### Staging rule
Commit in small deterministic increments. Stage only files verified in the current work cycle.

Preferred PowerShell workflow:
- Keep PowerShell parked at the repo root:
  - `\\Apollo\Bots\fantasy\mlf_roster_manager`
- Then run Git commands directly from that location.

Example:
```powershell
git status --short
git diff --check
git add runtime/refresh_all.sh
git commit -m "Fix Yahoo free agent batter discovery"
git push origin main
git status --short
```

---

## Current cleanup intent
1. Keep repo focused on source + docs only
2. Keep generated Yahoo probes and raw payloads out of commits unless explicitly needed
3. Keep `.gitignore` hardened for generated data, logs, probe output, and local backup artifacts such as `*.bak_*`
4. Re-evaluate any ambiguous top-level folders after proof of use
5. Keep one approved source path per concern and avoid duplicate script trees
6. Keep `streamlit_app.py` as a router and page content under `pages/`
7. Keep Batter work on `pages/batters.py`
8. Keep the current Yahoo FA generator inside `runtime/refresh_all.sh`; do not add a parallel exporter unless intentionally replacing the old path in the same change
9. Resume league-specific architecture work next: Usual-RMT, MLF-RMT, MiLF-RMT
10. Pitcher workflow remains a later major feature unless explicitly reprioritized
