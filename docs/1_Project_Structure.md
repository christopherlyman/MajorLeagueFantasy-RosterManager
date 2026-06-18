# 1_Project_Structure

## Purpose
This document defines the intended folder layout for the `MajorLeagueFantasy-RosterManager` project and separates source code from generated runtime artifacts, DB-managed state, temporary proof/probe output, and local runtime configuration.

Last updated: 2026-06-18

---

## Current verified architecture

### Runtime instances
The project now supports multiple RMT app instances from the same codebase.

| Instance | Container | Local port | Purpose |
|---|---:|---:|---|
| Usual-RMT | `usual-rmt` | `8050` | Usual Suspects Roto daily roster manager |
| MLF-RMT | `mlf_rmt` | `8051` | MLF roster manager instance |
| MiLF-RMT | `milf_rmt` | `8052` | MiLF roster manager instance |

Recent runtime proof showed all three containers returning HTTP `200 OK` after restart.

### Host and Git workflow
- NAS host: `Apollo`
- NAS project path: `/Volume1/Bots/fantasy/mlf_roster_manager`
- Windows/UNC path: `\\Apollo\Bots\fantasy\mlf_roster_manager`
- Runtime/Docker work is done from NAS SSH.
- Git is **not installed on the NAS shell**.
- Git operations must be run from Windows PowerShell against the UNC path.
- Latest PowerShell proof from 2026-06-18 showed:
  - `git --no-pager status --short -uall` returned clean output
  - branch `main` was aligned with `origin/main`
  - latest known commit was `dacf682 Use Yahoo-style league average hitter cap projection`
- Keep runtime proof and Git proof separate:
  - NAS SSH for Docker/runtime/app checks
  - Windows PowerShell for Git status/diff/commit/push

### Operational remote access
Tailscale has been set up as a remote-access path to Apollo.

Current Tailnet proof from Apollo:
- Apollo Tailnet IP: `100.93.229.49`
- Laptop Tailnet IP: `100.114.190.31`
- `tailscale status` showed both `apollo` and `steady2` online in the same Tailnet.
- Native `/usr/sbin/tailscaled` was started manually with `nohup`.
- The manually started daemon is good enough for same-session remote access but is **not yet proven persistent across NAS reboot**.

Important caveats:
- Do not reboot Apollo until Tailscale startup persistence is solved.
- Tailscale private-network access does not automatically prove SSH service availability.
- Earlier SSH to Apollo on port `22` returned `Connection refused`; TerraMaster SSH must be enabled or Tailscale SSH configured separately before assuming remote shell access.
- Prove remote access from a phone hotspot before depending on it away from home.

Quick Tailnet proof:
```bash
/usr/bin/tailscale status
/usr/bin/tailscale ip -4
```

### Current active UI source path
The currently verified active batter UI code path is:

- `views/batters.py`

Older documentation referenced `pages/batters.py`. Treat those references as stale unless the router is proven to import `pages/batters.py` again. Do not copy or fork batter logic between `pages/` and `views/` without first proving the active router path.

---

## Source-of-truth structure

### Top level
- `docs/` — project documentation, handoff notes, structure docs, runbooks, model-evaluation notes, and canonicals
- `runtime/` — orchestration scripts, Docker Compose, refresh wrappers, status folders, and log folders
- `scripts/` — executable ingestion/build scripts
- `scripts/yahoo/` — Yahoo-authenticated acquisition/load scripts
- `services/` — reusable Python logic for DB access, row assembly, queries, scoring, projections, and league profiles
- `views/` — currently verified Streamlit view modules, including the main batter UI
- `streamlit_app.py` — Streamlit router / app entrypoint if present in the active architecture
- `.env` — local runtime configuration; **not for Git**
- `Dockerfile`, `requirements.txt`, `LICENSE` — project metadata/build inputs

### Documentation set
Current documentation roles:
- `docs/0_RosterManager_Handoff.md` — operational handoff, current state, commands, caveats, and next-chat bootstrap
- `docs/1_Project_Structure.md` — source layout, generated-artifact rules, DB-state boundaries, and operational proof paths
- `docs/2_RMT_Model_Evaluation.md` — recommended next doc for RMT-vs-YGMA audit evidence and RMT-v2 gate-threshold methodology
- `docs/3_Remote_Access_Tailscale_Runbook.md` — optional runbook for making Apollo Tailscale access persistent and testable

Do not turn `1_Project_Structure.md` into the full model-testing notebook. Keep detailed RMT-vs-YGMA evidence in a dedicated model-evaluation doc.

### Primary application files
- `views/batters.py` — primary batter UI and current main RMT workspace
- `services/queries.py` — batter row assembly, data loading, date context resolution, FA row assembly, lineup/game state classification, unavailable-game handling, and row context enrichment
- `services/scoring.py` — batter scoring/ranking logic, including handedness, splits, recent form, status, lineup, and unavailable overrides
- `services/batter_multiday.py` — reusable Today/Tomorrow/Day After Tomorrow batter projection service
- `services/pitcher_queries.py` — current pitcher data query helpers
- `services/pitcher_scoring.py` — current pitcher scoring helpers
- `runtime/refresh_live.sh` — live refresh path
- `runtime/refresh_all.sh` — full refresh path
- `scripts/refresh_projection_game_context.py` — refreshes game/probable-pitcher/hand context for Today, Tomorrow, and Day After Tomorrow
- `scripts/refresh_mlb_probable_pitcher_daily.py` — daily games/probable pitchers from MLB Stats API; preserves MLB game status in `raw_json`
- `scripts/refresh_probable_pitcher_hand.py` — probable pitcher handedness file refresh
- `scripts/refresh_starting_lineups.py` — starting lineup ingestion
- `scripts/build_mlbam_player_map.py` — hitter MLBAM mapping
- `scripts/refresh_hitter_splits_mlb.py` — hitter split inputs

### Scripts layout
- `scripts/yahoo/`
  - Yahoo-authenticated acquisition and load scripts
  - examples: `auth.py`, `yahoo_team_roster.py`, `refresh_recent_yahoo_api.py`, `yahoo_bulk_load.py`, `yahoo_league_player_pool_load.py`, `refresh_usual_daily_cap_usage.py`
- `scripts/` root
  - MLB, lineup, split, projection-context, and project-local pipeline scripts that are not Yahoo-auth specific
  - examples: `refresh_starting_lineups.py`, `refresh_mlb_probable_pitcher_daily.py`, `refresh_probable_pitcher_hand.py`, `refresh_projection_game_context.py`

---

## Runtime environment and date context

### `.env`
`.env` is local runtime configuration and must not be committed.

Important current date behavior:
- `DEFAULT_AS_OF_DATE=` blank means use **today in America/New_York**.
- `DEFAULT_DATE_OFFSET_DAYS=0` means today.
- `DEFAULT_DATE_OFFSET_DAYS=1` means tomorrow.
- `DEFAULT_DATE_OFFSET_DAYS=2` means day after tomorrow.
- `DEFAULT_AS_OF_DATE=YYYY-MM-DD` still works as an intentional fixed-date override.

### Date-resolution code
- `services/queries.py::resolve_as_of_date(...)`
  - central date resolver
  - uses America/New_York midnight boundary
  - supports explicit date override and integer offset days
- `services/queries.py::get_default_context()`
  - resolves `league_key`, `team_key`, and `as_of_date` for service-layer scripts

### `.env` newline caution
The project previously hit a real `.env` formatting issue where literal `\n` text replaced real line breaks and caused the database DSN to absorb later keys. Future `.env` edits should preserve real newline separators.

Sanitized proof command:

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

python3 - << 'PY'
from pathlib import Path
raw = Path('.env').read_bytes()
print('REAL_NEWLINE_COUNT', raw.count(b'\n'))
print('LITERAL_BACKSLASH_N_COUNT', raw.count(b'\\n'))
print('KEYS_ONLY')
for line in raw.decode('utf-8', errors='replace').splitlines():
    if '=' in line and not line.strip().startswith('#'):
        print(line.split('=', 1)[0])
PY
```

Expected:
- `REAL_NEWLINE_COUNT` greater than zero
- `LITERAL_BACKSLASH_N_COUNT = 0`

### Container recreation note
When `.env` values injected through Docker Compose `env_file` change, a plain `docker restart` may preserve stale container environment variables. Use a controlled recreate of the app service when needed.

For the current multi-container runtime, verify the actual service names in `runtime/docker-compose.yml` before recreating. Recent direct restarts used:

```bash
docker restart usual-rmt mlf_rmt milf_rmt
```

---

## Current Streamlit / UI structure

### Active design rule
Do **not** add new tabs for every date view. The UI should act as an information system: same table shape, small controls, quick comparison.

### Batters UI
`views/batters.py` owns the current batter workflow.

Implemented / usable from current docs and recent commit history:
- Starting Lineup table
- Slots view
- Batter Free Agents table
- Roster Policy editor
- read-only batter recommendations / action-plan surfaces
- compressed Rank Reason display
- sidebar refresh controls
- slot override controls
- threshold column
- threshold-based starter row highlighting
- combined starter/bench table
- `S = Status` Rank Reason component
- DTD status-risk penalty display
- postponed-game display and unavailable ranking
- locked already-started Yahoo hitter slots in the Today optimizer
- row coloring based on lineup confidence/status
- Today / Tomorrow / Day After Tomorrow radio selector for Starting Lineup
- Today / Tomorrow / Day After Tomorrow radio selector for Batter Free Agents
- projection explainer expander for future projection views

### Starting Lineup projection views
The Starting Lineup tab uses the same table shape for all projection views:

- `Today`
  - current/live behavior
  - uses current rows directly
  - includes posted lineup status when available
- `Tomorrow`
  - uses projected game context from `services/batter_multiday.py`
  - lineups are not confirmed
- `Day After Tomorrow`
  - uses projected game context from `services/batter_multiday.py`
  - lineups are not confirmed

Important implementation detail:
- Future projected rows must carry projected `game_status`.
- A prior bug left `game_display` populated but `game_status` stale, causing the optimizer to reject playable future rows.
- Fix committed: projected rows now set `game_status = GAME_FOUND` when the projected game is not `No game`.

### Batter Free Agents projection views
The Batter Free Agents tab uses the same table shape and a projection-view radio selector:

- `Today`
- `Tomorrow`
- `Day After Tomorrow`

The future FA views use today’s true FA pool and rescore those same players against the selected future date. Do **not** use a future top-300 fallback as the FA pool.

### RMT-v2 gate-threshold audit
The next model-development target is **not** a production scoring patch. It is a deterministic audit to decide which simple guardrails would improve RMT recommendations against the YGMA benchmark without overfitting.

Audit purpose:
- Determine when RMT matchup/spot-start alpha should be allowed to override a reliable baseline player.
- Separate pre-lineup `Watch` candidates from true `Start` recommendations.
- Test lineup-confirmation, batting-order, reliability, and elite-player guardrails before changing production model logic.

Likely future recommendation labels:
- `Start`
- `Lean Start`
- `Watch`
- `Bench`

Structural rule:
- Audit workbooks and exports are generated evidence artifacts.
- Audit methodology and durable findings should live in `docs/2_RMT_Model_Evaluation.md`.
- Production UI/code should only change after the gate rules are supported by reproducible audit output.

### Roster Experiment / Add-Drop Watchlist
Next likely **product/UI** feature after RMT-v2 gate proof:

- Separate planning surface, not clutter inside Starting Lineup or Batter Free Agents.
- Purpose: compare whether dropping a flagged owned player for a FA add is worth it.
- Initial scope should use:
  - Today
  - Tomorrow
  - Day After Tomorrow
  - 3-day total
  - net gain/loss
- Rest-of-week and rest-of-season projections should wait until the 3-day experiment view is stable.

Do not add Yahoo write actions yet.

## Multi-day batter projection service

### Source file
- `services/batter_multiday.py`

### Purpose
Build reusable Today/Tomorrow/Day After Tomorrow batter projections for owned and free-agent batters without embedding large scripts directly in the Streamlit view.

### Core behavior
The service:
1. Freezes today’s real player pool:
   - owned batters from today’s roster rows
   - today’s true FA batter pool
2. Builds projection dates from `ctx['as_of_date']`:
   - Today
   - Tomorrow
   - Day After Tomorrow
3. Joins the same Yahoo player keys to future MLB game context:
   - MLB team
   - opponent
   - home/away
   - game time
   - opposing probable pitcher
4. Loads scoring inputs for the selected projection date:
   - batter Savant data
   - pitcher Savant data
   - probable pitcher handedness
   - batter vs RHP / vs LHP splits
   - batter home/away splits
   - batter day/night splits
   - recent 7-day form
5. Re-runs the batter scoring model for future dates.

### Output columns used by the UI
The service returns per-player rows with fields such as:
- `Pool`
- `Player`
- `YahooKey`
- `Slot`
- `Policy`
- `Eligible`
- `Today`
- `Tomorrow`
- `Day2`
- `Total3`
- `TodayLineup`
- `TodayGame`
- `TomorrowGame`
- `Day2Game`
- `TodayNote`
- `TomorrowNote`
- `Day2Note`

### Proven bug/fix
Problem:
- Future views initially showed a future game and rank, but internal `game_status` could remain stale from Today.
- The optimizer rejected future playable rows because `has_game_today(row)` checked stale `game_status`.

Fix:
- `_project_batter_row(...)` in `views/batters.py` now derives `game_status` from the projected game display:
  - projected game present and not `No game` → `GAME_FOUND`
  - otherwise → `NO_GAME_TODAY`

Recent proof:
- Jackson Holliday became `StartableUTIL=True` after projected `game_status` was carried.
- Day After Tomorrow optimizer stopped leaving no-game players in OF/UTIL when playable alternatives existed.

---

## Projection game-context refresh

### Source file
- `scripts/refresh_projection_game_context.py`

### Purpose
Keep multi-day projection inputs fresh so Tomorrow and Day After Tomorrow views do not silently show all players as `No game` after date rollover.

### Refresh contract
For base date `as_of_date`, refresh:
- `as_of_date + 0`
- `as_of_date + 1`
- `as_of_date + 2`

For each date, refresh:
- `lineup_tool.mlb_probable_pitcher_daily`
- `data/derived/opposing_probable_pitchers_with_hand_<date>.csv`

### Pipeline wiring
The script is wired into:
- `runtime/refresh_live.sh`
- `runtime/refresh_all.sh`

This fixed the issue where Day After Tomorrow rolled to a new date but the DB/files only had context through the prior Day2 date.

### Proof pattern
Known-good proof command pattern:

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1
set -euo pipefail

bash runtime/refresh_live.sh

docker exec -i -w /app usual-rmt python - <<'PY'
from datetime import date, timedelta
from pathlib import Path
from services.db import get_connection
from services.queries import get_default_context

ctx = get_default_context()
base = date.fromisoformat(ctx['as_of_date'])
dates = [(base + timedelta(days=i)).isoformat() for i in range(3)]

print('CTX', ctx)
print('DATES', dates)

with get_connection() as conn:
    with conn.cursor() as cur:
        print('Date|ProbableGames|HandFileExists|HandFileSize')
        for d in dates:
            cur.execute(
                '''
                SELECT count(*)
                FROM lineup_tool.mlb_probable_pitcher_daily
                WHERE as_of_date = %s
                ''',
                (d,),
            )
            games = cur.fetchone()[0]
            hand_file = Path(f'/app/data/derived/opposing_probable_pitchers_with_hand_{d}.csv')
            print(f'{d}|{games}|{hand_file.exists()}|{hand_file.stat().st_size if hand_file.exists() else 0}')

print('PATCHED_REFRESH_LIVE_PROJECTION_CONTEXT_VERIFY_OK')
PY
```

---

## Generated / non-source artifacts
These are expected during operation but are **not** source-of-truth code.

### Generated raw captures
- `data/raw/yahoo/` — raw Yahoo payload captures
- `data/raw/yahoo/probes/` — temporary probe outputs used for endpoint discovery/debugging
- `scripts/yahoo/data/` — treat as generated/probe output unless explicitly promoted into source-controlled fixtures

### Generated derived inputs
- `data/derived/starting_lineup_players_<date>.csv`
- `data/derived/starting_lineup_teams_<date>.csv`
- `data/derived/recent7_hitter_inputs_<date>.csv`
- `data/derived/hitter_split_inputs_<date>.csv`
- `data/derived/true_free_agent_batters_<date>.csv`
- `data/derived/opposing_probable_pitchers_with_hand_<date>.csv`
- other app input CSVs produced by refresh pipelines

Generated data is not source code and should not be hand-edited.

### Model evaluation artifacts
RMT-vs-YGMA audit workbooks and derived evaluation exports are evidence artifacts, not source code.

Known artifact examples from model-testing work:
- broad RMT-vs-YGMA deterministic audit workbook
- flexible slot-matched RMT baseline audit workbook
- RMT fringe-vs-reliable YGMA test workbook
- future gate-threshold audit workbook(s)

Rules:
- Do not commit `.xlsx` audit workbooks by default.
- Do not put large generated audit output beside source files.
- Durable methodology, claims, and next decisions should be summarized in `docs/2_RMT_Model_Evaluation.md`.
- Commit only small source scripts or intentional fixtures needed to reproduce an audit.

### Runtime status / logs
- `runtime/logs/`
- `runtime/status/`

These are generated operational artifacts and should not be treated as source code.

## DB-managed state
Some important project state lives in Postgres and is therefore **not** represented as files in the repo.

### Main operational tables referenced by current workflows
- `lineup_tool.roster_snapshot` — daily roster snapshot truth, including `selected_position`
- `lineup_tool.mlb_probable_pitcher_daily` — MLB game/probable-pitcher context by date; `raw_json` preserves game status
- `rmt.usual_cap_usage_seed` — Usual-RMT seeded Max Games/IP baseline
- `rmt.usual_daily_cap_usage` — daily actual cap usage rows, including hitter slot played counts and pitcher IP actuals
- `rmt.roster_player_policy` — player policy statuses such as keeper/drop candidate behavior

Rule:
- DB tables are operational state, not Git-tracked source files.
- Code may depend on them, but schema/data changes should be documented explicitly in docs and migration/runbook notes.

---

## Usual-RMT Max Games & IP state

### Current state
Hitter Max Games projections now match Yahoo-style output closely after recent changes.

Current hitter model:
- Uses Yahoo-dated roster actual usage for roll-forward.
- Uses Yahoo-style league-average remaining-games projection rather than only the current roster occupants.
- Uses half-up style rounding for projected remaining games.
- OF uses the Yahoo-style multi-slot future baseline, rounding after multiplying league-average remaining games by `3`.
- Allows hitter projections to exceed Max where Yahoo does.

Recent commit proof:
- `dacf682 Use Yahoo-style league average hitter cap projection`

### Pitcher IP state
Pitcher IP used/remaining is close enough for operational use, but pitcher projected/diff is approximate.

Important proof:
- Tested obvious Yahoo Fantasy API team endpoints did **not** expose the displayed “Maximum Games & Innings Pitched” projection table directly.
- Current code has daily actual IP via Yahoo `stat_id 50` from `scripts/yahoo/refresh_usual_daily_cap_usage.py`.
- The current projected P/IP value is still a local RMT approximation.

Newer working hypothesis from Yahoo UI observation:
- Yahoo’s displayed pitcher projected IP appears to use the UI’s remaining projected IP values for currently rostered pitchers.
- The observed UI behavior looked like Yahoo floors each pitcher’s remaining projected IP and sums those values.
- RMT does **not** currently ingest those remaining projected IP values.
- Treat this as an ingestion gap, not as permission to patch the local formula with a fitted constant.

Do not tune the pitcher projection with one-off constants such as `+3` or `-2` unless a multi-day proof supports the formula. Earlier tests showed no single simple formula fit all observed Yahoo dates.

Recommended display behavior:
- Hitter projection rows may be treated as Yahoo-style.
- Pitcher projection/diff should be considered approximate until a direct Yahoo source, UI scrape, or stable multi-day ingestion method is proven.

## Batter scoring model summary

### Components
- `B` = Bat baseline
- `P` = Pitcher matchup
- `H` = Handedness
- `H/A` = Home/Away
- `D/N` = Day/Night
- `R` = Recent
- `S` = Status risk
- `L` = Lineup

### Current handedness model
Handedness was changed from a highly compressed threshold/shrink model to an OPS-gap model.

Current intent:
- Use the batter’s active split vs projected pitcher hand compared to overall OPS.
- Scale the OPS gap meaningfully.
- Apply confidence by split AB sample size.
- Clamp to a max absolute value to avoid absurd outliers.

Example proof case:
- Luke Raley’s weak-side LHP matchup previously showed only `Hand -0.5`.
- After OPS-gap scoring, his Hand value moved near `-7.7`, which better matched his LHP/RHP split profile.

### Important scoring notes
- `L` is neutral (`+0.0`) for confirmed starters.
- A `-30.0` lineup modifier is expected when a posted lineup omits the player.
- DTD status applies a mild status-risk penalty.
- `IL*`, `NA`, and similar unavailable states override to unavailable where relevant.
- `NO_GAME_TODAY` and `POSTPONED` override to unavailable.
- Batter Free Agent candidates come from Yahoo `status=FA`; waiver players are excluded by source.
- IL/NA/SUSP and inactive candidates are excluded before display/scoring.

---


## RMT model evaluation and gate work

### Current model-evaluation direction
RMT-v2 should not be created by ad hoc scoring-weight tweaks. The evidence so far points to a gating problem:

- RMT can identify useful spot-start candidates in some conditions.
- RMT becomes riskier when lineup status is not confirmed.
- Reliable/YGMA-style players often retain value through plate appearance volume, lineup role, lower strikeout risk, and category stability.
- The next step is to test gate rules before production model changes.

### Gate concepts to test
Candidate gates:
- confirmed lineup requirement for fringe/matchup overrides
- top-5 or top-6 batting-order preference
- stronger protection for reliable/elite everyday bats
- shrinkage for small-sample platoon edges
- league-specific thresholds for Usual roto versus MLF/MiLF H2H
- category-objective weighting by league context

### Code placement rule
When gate logic becomes production-ready:
- reusable decision logic should live in `services/`
- Streamlit rendering should stay in `views/`
- generated audit files should stay out of source paths
- model-evaluation methodology should be documented in `docs/2_RMT_Model_Evaluation.md`

Do not implement Yahoo write actions until read-only recommendation quality is stable.

## Batter Free Agents
The Batter Free Agents tab is wired and usable.

### Source contract
Yahoo endpoint pattern:

```text
/league/{league_key}/players;status=FA;sort=OR;start={n};count=25;out=percent_owned?format=json
```

Rules:
- `start` increments by `25`.
- Yahoo returns 25 players per page.
- `;out=percent_owned` is the proven valid syntax.
- `/out=percent_owned` fails.

### Filtering contract
Include only:
- Yahoo `status=FA`
- batters only
- active candidates only
- candidates passing rank/ownership screen

Exclude:
- waiver players
- pitchers
- `IL`, `IL10`, `IL15`, `IL60`
- `NA`
- `SUSP`
- candidates with inactive `status_full`

Important rule:
- Do not revert FA discovery to a broad DB anti-roster join. That reintroduces waiver and unavailable-player leakage.

---

## Roster Policy
Roster policy rows are stored in DB, not source files.

Current relevant table:
- `rmt.roster_player_policy`

Policy values seen in recent work include:
- `KEEPER`
- `DROPPABLE_HIGH`
- `DROPPABLE_LOW`

Recent behavior:
- Policy table supports manual investigation of drop candidates.
- Future Add-Drop Watchlist / Roster Experiment should use policy rows to restrict drop candidates and avoid recommending protected players.

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

## Naming and placement rules
- Use `scripts/yahoo/` for Yahoo-authenticated scripts.
- Use `scripts/` root for MLB/splits/projection-context scripts.
- Use `services/` for reusable Python logic.
- Use `views/` for the currently verified Streamlit view modules.
- Keep generated files under `data/` or `runtime/`, never beside source unless explicitly required.
- Keep DB-backed operational state out of file-based source trees.
- Keep one source path per concern; do not create parallel replacement scripts without retiring the old path in the same change.

---

## Source vs generated decision rule
When deciding where something belongs:

- **Source code** → `scripts/`, `scripts/yahoo/`, `services/`, `views/`, `streamlit_app.py`
- **Documentation** → `docs/`
- **Generated runtime artifact** → `data/` or `runtime/`
- **Operational DB state** → database table, documented in docs/runbooks, not as an ad hoc file
- **Local configuration** → `.env`, never committed
- **Temporary probes** → do not commit unless promoted into intentional source/test fixtures

---

## Operational proof commands

### Restart all current RMT containers
```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

docker restart usual-rmt mlf_rmt milf_rmt

sleep 8

for port in 8050 8051 8052; do
  echo
  echo "PORT=$port"
  curl -I --max-time 15 "http://127.0.0.1:${port}" | sed -n '1,12p'
done
```


### Prove Tailscale remote access state
```bash
/usr/bin/tailscale status || true
/usr/bin/tailscale ip -4 || true

echo
echo "Expected Apollo Tailnet IP from 2026-06-18 proof:"
echo "100.93.229.49"
```

Remote-access proof should be done from the laptop on a phone hotspot before relying on it away from home.

Test targets from the laptop:
```powershell
tailscale status
ping 100.93.229.49
```

Browser targets:
```text
http://100.93.229.49:8050
http://100.93.229.49:8051
http://100.93.229.49:8052
```

SMB target:
```text
\\100.93.229.49\Bots
```

Do not assume SSH works until `ssh Adm1n@100.93.229.49` or Tailscale SSH has been separately proven.

### Compile primary app code in all current RMT containers
```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

for C in usual-rmt mlf_rmt milf_rmt; do
  docker exec -i -w /app "$C" python -m py_compile \
    views/batters.py \
    services/queries.py \
    services/scoring.py \
    services/batter_multiday.py \
    scripts/refresh_projection_game_context.py
done
```

### Prove projection context is available for Today/Tomorrow/Day2
```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1
set -euo pipefail

docker exec -i -w /app usual-rmt python - <<'PY'
from datetime import date, timedelta
from pathlib import Path
from services.db import get_connection
from services.queries import get_default_context

ctx = get_default_context()
base = date.fromisoformat(ctx['as_of_date'])
dates = [(base + timedelta(days=i)).isoformat() for i in range(3)]
print('CTX', ctx)
print('DATES', dates)

with get_connection() as conn:
    with conn.cursor() as cur:
        print('Date|ProbableGames|HandFileExists|HandFileSize')
        for d in dates:
            cur.execute(
                '''
                SELECT count(*)
                FROM lineup_tool.mlb_probable_pitcher_daily
                WHERE as_of_date = %s
                ''',
                (d,),
            )
            games = cur.fetchone()[0]
            hand_file = Path(f'/app/data/derived/opposing_probable_pitchers_with_hand_{d}.csv')
            print(f'{d}|{games}|{hand_file.exists()}|{hand_file.stat().st_size if hand_file.exists() else 0}')
PY
```

### Inspect current batter rows
```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from collections import Counter
from services.queries import get_default_context, fetch_batter_roster_rows
ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])
print('CTX', ctx)
print('ROWS', len(rows))
print('LINEUP_STATUS_COUNTS', dict(Counter(r.get('lineup_status', '') for r in rows)))
for r in rows[:20]:
    print(r.get('player_display'), '|', r.get('lineup_status'), '|', r.get('ranking'), '|', r.get('note_short'))
PY
```

### Inspect current Batter Free Agent rows
```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from collections import Counter
from services.queries import get_default_context, fetch_available_batter_rows
ctx = get_default_context()
rows = fetch_available_batter_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])
print('CTX', ctx)
print('FA_ROWS', len(rows))
print('LINEUP_STATUS_COUNTS', dict(Counter(r.get('lineup_status', '') for r in rows)))
for r in rows[:25]:
    print(r.get('player_display'), '|', r.get('eligible_display'), '| Rank', r.get('ranking'), '|', r.get('game_display'), '|', r.get('lineup_status'), '|', r.get('note_short'))
PY
```

---

## Git workflow
Git is run from the user’s personal laptop, not directly on the NAS SSH shell.

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
Latest pushed commits from the 2026-06-18 PowerShell proof include:
- `dacf682 Use Yahoo-style league average hitter cap projection`
- `4aef775 Update lineup row color rules`
- `d92483e Build batter action plan after daily refresh`
- `0ffac87 Add daily batter action plan`
- `e9085b4 Use ceil for Usual OF cap projection`
- `b8c8c74 Add read-only batter recommendations tab`
- `f2ce2b4 Prefer behind-pace hitter slots in optimizer`
- `5bf2b6b Match Yahoo hitter cap projection rounding`
- `09983fd Expose batter roster policy status`
- `e9bd3fd Lock started Yahoo hitter slots in Today optimizer`
- `ffb804a Color starting lineup rows by lineup confidence`
- `7902086 Refresh RotoWire data during manual refresh`

Older important checkpoints still relevant to architecture:
- `32ae292 Carry projected game status into future batter optimization`
- `f4d6b35 Refresh projection game context for multi-day batter views`
- `fe02326 Add batter multi-day projection views`
- `35a57c4 Use OPS gap for batter handedness scoring`
- `82a73c3 Use Yahoo dated roster for Usual cap usage`
- `d212b1b Separate batter and pitcher roster policy views`

### Preferred PowerShell workflow
```powershell
Push-Location "\\Apollo\Bots\fantasy\mlf_roster_manager"

git status --short
git diff --check

git add <files>
git commit -m "message"
git push origin main

git status --short

Pop-Location
```

Rules:
- Do not commit `.env`.
- Do not commit generated data unless explicitly intended.
- Do not commit local proof backups (`*.bak_*`).
- Commit small deterministic increments.
- Use NAS SSH for runtime/Docker proof and PowerShell for Git.

---

## How to find answers instead of guessing

### If the question is about active UI behavior
Check:
1. `streamlit_app.py` router/imports
2. `views/batters.py`
3. live browser refresh or app logs

Do not assume `pages/batters.py` is active unless the router proves it.

### If the question is about row assembly
Check:
- `services/queries.py`
- then run a batter-row verification command

### If the question is about scoring
Check:
- `services/scoring.py`
- then verify with sample rows and rank reasons

### If the question is about Today/Tomorrow/Day After Tomorrow projections
Check:
1. `services/batter_multiday.py`
2. `views/batters.py::_project_batter_row(...)`
3. `scripts/refresh_projection_game_context.py`
4. `runtime/refresh_live.sh`
5. `runtime/refresh_all.sh`
6. DB rows in `lineup_tool.mlb_probable_pitcher_daily`
7. hand files under `data/derived/opposing_probable_pitchers_with_hand_<date>.csv`

### If the question is about Usual cap usage
Check:
1. `rmt.usual_cap_usage_seed`
2. `rmt.usual_daily_cap_usage`
3. `views/batters.py::_usual_cap_projection_values(...)`
4. Yahoo UI values supplied by user

Important pitcher note:
- hitter cap projections currently match Yahoo-style behavior
- pitcher projection remains approximate

### If the question is about Batter Free Agent availability
Check:
1. Yahoo `status=FA` source contract
2. generated `data/derived/true_free_agent_batters_<date>.csv`
3. `services/queries.py::fetch_available_batter_rows(...)`
4. final app rows from Batter Free Agents inspection command

### If the question is about lineup confirmation
Check:
1. `scripts/refresh_starting_lineups.py`
2. `data/derived/starting_lineup_players_<date>.csv`
3. `data/derived/starting_lineup_teams_<date>.csv`
4. matching logic in `services/queries.py`
5. final batter-row output from engine

Lineup status ladder:
- missing lineup files → `LINEUP_DATA_MISSING`
- no MLB game or postponed MLB game → `LINEUP_NOT_APPLICABLE`
- player found in posted lineup → `IN_POSTED_LINEUP`
- team lineup posted but player absent → `POSTED_BUT_NOT_FOUND`
- team lineup not posted / not proven → `LINEUP_NOT_CONFIRMED`


### If the question is about RMT-vs-YGMA model evaluation
Check:
1. `docs/2_RMT_Model_Evaluation.md` once created
2. generated audit workbook methodology
3. raw model-testing input workbooks
4. reproducible script output used to build the audit
5. league-specific results before generalizing

Do not change production scoring until the proposed gate improves or preserves results across enough audit slices to avoid one-off overfitting.

### If the question is about remote access
Check:
1. `/usr/bin/tailscale status`
2. `/usr/bin/tailscale ip -4`
3. laptop hotspot test to `100.93.229.49`
4. HTTP access to `8050`, `8051`, `8052`
5. SMB access to `\\100.93.229.49\Bots`
6. SSH only after TerraMaster SSH or Tailscale SSH is explicitly proven

### If the question is about Yahoo max-games/IP automation
Remember:
- tested Yahoo Fantasy API team endpoints did **not** expose the live max-games/IP projection table directly
- current production design seeds from Yahoo UI and maintains internally
- pitcher projected IP is approximate unless a better source/model is proven

### If the question is about a broken script import
Check whether the script is run with:
- `PYTHONPATH=/app`

---

## Recommended next development queue
1. Commit updated docs after review.
2. Create/update `docs/2_RMT_Model_Evaluation.md` with RMT-vs-YGMA audit methodology, evidence, and caveats.
3. Build the RMT-v2 gate-threshold audit before changing production scoring.
4. Use the audit to choose simple guardrails for `Start`, `Lean Start`, `Watch`, and `Bench`.
5. Implement gate labels first as read-only decision support.
6. Build Roster Experiment / Add-Drop Watchlist backend after model-gate proof.
7. Wire Roster Experiment UI only after backend proof.
8. Continue collecting Yahoo pitcher projected IP observations before changing the pitcher formula.
9. Make Tailscale startup persistent and document it in a remote-access runbook.
10. Build pitcher workflow as a major feature after the batter recommendation system and add/drop planning surface are stable.
11. Add lineup-reliability weighting to future opportunity denominator.
12. Improve recent H/AB so AVG contribution is real.
13. Finish MLBAM team-aware disambiguation.
14. General UI polish and width cleanup.

## Current cleanup intent
1. Keep repo focused on source + docs only.
2. Keep generated Yahoo probes and raw payloads out of commits unless explicitly needed.
3. Keep `.gitignore` hardened for generated data, logs, probe output, local backup artifacts such as `*.bak_*`, and generated model-evaluation workbooks.
4. Re-evaluate any ambiguous top-level folders after proof of use.
5. Keep one approved source path per concern and avoid duplicate script trees.
6. Keep the current working batter UI in `views/batters.py` unless router proof says otherwise.
7. Keep the current Yahoo FA generator path; do not add a parallel exporter unless intentionally replacing the old path in the same change.
8. Keep projection context refresh centralized in `scripts/refresh_projection_game_context.py`.
9. Keep RMT-vs-YGMA audit output out of source paths unless intentionally promoted into a small reproducible fixture.
10. Do not add Yahoo write actions until the read-only recommendation system is stable.
