# Roster Manager Handoff

## Purpose
Daily roster-management tool for personal fantasy baseball lineup decisions.

Current scope:
- **Primary UI:** Streamlit
- **Primary focus:** batter sit/start decisions, with pitcher workflow next
- **Current strengths:** roster refresh, game/probable pitcher context, handedness, recent, splits, slot-based optimized lineup UI, Yahoo-confirmed Batter Free Agents tab, manual slot overrides, working in-app refresh flow, schedule-pressure slot thresholds, automatic remaining-start tracking from seed + roster snapshots, dynamic New York date resolution, FA/waiver separation, IL/NA/SUSP Free Agent exclusion, DTD risk penalty, and postponed-game handling
- **Current gaps:** Pitchers workflow is not built; MLBAM disambiguation still weak for some ambiguous names; recent H/AB still needs true last-7 AVG contribution; future-opportunity denominator still assumes team game-days are playable opportunities and needs lineup-reliability weighting

---

## Working rules for the next chat
Follow these rules strictly:
- **No Zombie Code**
- **Small micro-step instructions**
- **Deterministic / proof-first**
- **Concise responses**
- **Provide exact commands; user runs them and pastes output**
- **Do not guess**
- **Investigate first, then act**
- Prefer proving truth from current files / DB / logs / outputs over theorizing
- Do not patch unless the proposed change is directly supported by proof
- One read-only proof step should usually precede each write step

---

## Environment
### Host
- **Host name:** Apollo
- **Primary project path on host:** `/Volume1/Bots/fantasy/mlf_roster_manager`
- **Windows/UNC equivalent:** `\\Apollo\Bots\fantasy\mlf_roster_manager`

### Container/runtime assumptions
- **Primary container:** `mlf_roster_manager`
- **Primary UI port:** `8050`
- **Inside container project root:** `/app`
- Streamlit is the **primary** UI; Dash is no longer the active target UI.
- NAS shell has old `python` as Python 2.7; use `python3` on the NAS host or container `python`/`python3`.
- NAS shell does **not** have Git. Use Windows PowerShell from the laptop for Git operations against `\\Apollo\Bots\fantasy\mlf_roster_manager`.
- NAS uses `docker-compose`, not the newer `docker compose` plugin.

### Important paths
- `streamlit_app.py` — Streamlit navigation/router only
- `pages/batters.py` — primary Batters page and current main app logic
- `pages/pitchers.py` — Pitchers page shell
- `runtime/docker-compose.yml` — primary app container definition
- `runtime/refresh_live.sh` — live refresh pipeline
- `runtime/refresh_all.sh` — full refresh pipeline
- `services/queries.py` — row assembly, data loading, remaining-start calculation, date resolution
- `services/scoring.py` — ranking logic
- `scripts/refresh_starting_lineups.py` — lineup ingestion
- `scripts/refresh_mlb_probable_pitcher_daily.py` — daily games / probable pitchers
- `scripts/refresh_probable_pitcher_hand.py` — probable pitcher handedness
- `scripts/build_mlbam_player_map.py` — hitter MLBAM mapping
- `scripts/refresh_hitter_splits_mlb.py` — hitter split inputs
- `data/raw/` — raw source captures
- `data/derived/` — derived CSV inputs used by the app

---

## Runtime architecture
### High-level flow
1. Refresh scripts build/refresh source files and DB rows.
2. `services/queries.py` assembles batter rows using:
   - roster snapshot
   - MLB game + probable pitcher data, including MLB status for postponed-game detection
   - probable pitcher handedness
   - recent inputs
   - hitter split inputs
   - lineup files
3. `services/scoring.py` computes ranking and short reason string.
4. Streamlit renders through explicit navigation:
   - `streamlit_app.py` is the router
   - `pages/batters.py` renders Batters
   - `pages/pitchers.py` renders Pitchers shell
5. Batters page contains internal tabs:
   - Starting Lineup
   - Slots
   - Batter Free Agents
6. Pitchers is now a sidebar page, not an internal tab inside Batters.

### Date-resolution behavior
Current behavior:
- `DEFAULT_AS_OF_DATE=` blank means use **today in America/New_York**.
- New York midnight is the cutoff for “today.”
- `DEFAULT_DATE_OFFSET_DAYS=0` means today.
- `DEFAULT_DATE_OFFSET_DAYS=1` means tomorrow.
- `DEFAULT_DATE_OFFSET_DAYS=2` means day after tomorrow.
- `DEFAULT_AS_OF_DATE=YYYY-MM-DD` still works as an explicit override if needed.

Implementation notes:
- `services/queries.py::resolve_as_of_date(...)` is the shared date resolver.
- `services/queries.py::get_default_context()` uses `resolve_as_of_date(...)`.
- `pages/batters.py::get_runtime_context()` also uses `resolve_as_of_date(...)`.
- `.env` should contain `DEFAULT_AS_OF_DATE=` blank and `DEFAULT_DATE_OFFSET_DAYS=0` for normal daily use.
- If the app rolls past midnight ET before the new day’s refresh has run, it will look for the new date’s derived files.

### New slot-cap / threshold architecture
The batter start threshold is no longer a fixed hardcoded rule.

It has 3 moving parts:

1. **Slot usage seed**
   - DB table: `lineup_tool.slot_usage_seed`
   - Purpose: one-time seed of hitter **Played** counts by slot using Yahoo UI values
   - Current seeded team:
     - league: `469.l.22528`
     - team: `469.l.22528.t.11`
     - season: `2026`
     - seed date: `2026-05-03` (pregame Yahoo values)

2. **Roll-forward remaining starts**
   - Query helper: `services/queries.py::fetch_remaining_starts_by_slot(...)`
   - Formula:
     - start from `slot_usage_seed.seed_played`
     - count hitter starts from `lineup_tool.roster_snapshot.selected_position`
     - compute `Remaining = Max - Played`
   - This avoids daily Yahoo scraping and avoids trying to backfill pre-snapshot season history.

3. **V3 schedule-pressure floors**
   - `pages/batters.py` computes future slot opportunities from:
     - current active roster only
     - each player’s `mlb_team_abbr`
     - public MLB remaining schedule through `Sep 27`
     - slot eligibility
   - For each slot family:
     - `pressure = remaining_starts / future_roster_opps`
     - first-pass floor is mapped from that pressure around the neutral center of `50`
   - Current slot families:
     - `C`, `1B`, `2B`, `3B`, `SS`, `IF`, `OF`, `UTIL`

### Streamlit-specific notes
- Streamlit is the primary app on `http://Apollo:8050`.
- Sidebar navigation should show only:
  - Batters
  - Pitchers
- Batters page includes:
  - Starting Lineup tab
  - Slots tab
  - Batter Free Agents tab
  - slot overrides in the sidebar
  - working refresh button path
  - compressed Rank Reason display (`B`, `P`, `H`, `H/A`, `D/N`, `R`, `S`, `L`)
  - **Slot cap source** expander in sidebar
  - automatic remaining-start source from DB tracker
  - manual slot override fallback if needed
- Streamlit reads runtime defaults from `/app/.env`.

---

## Current confirmed working state
These are considered working unless proven otherwise:
- Streamlit is the primary UI on port `8050`.
- Sidebar navigation now shows Batters and Pitchers.
- The old auto-generated `Streamlit app` sidebar entry has been removed by making `streamlit_app.py` a router.
- Batters page contains the current working batter UI.
- Pitchers page exists as a separate sidebar page shell.
- Main-page Pitchers tab was removed from Batters.
- Dynamic New York date resolution works:
  - offset 0 resolved to `2026-05-04` during proof
  - offset 1 resolved to `2026-05-05`
  - offset 2 resolved to `2026-05-06`
- `.env` newline corruption was repaired; `.env` should not contain literal `\n` sequences.
- Refresh scripts can be run from terminal and from inside the Streamlit container.
- In-app refresh path has been proven to run end-to-end.
- Roster snapshot refresh works.
- MLB games / probable pitchers refresh works.
- Probable pitcher handedness refresh works.
- Recent refresh works.
- Hitter splits refresh works.
- Slot optimizer / manual slot override UI works.
- Rank Reason compression and legend are in place.
- Starting Lineup display includes live **Threshold** column.
- Starting Lineup display column order is:
  - `Slot | Threshold | Player | Eligible Pos. | Rank | Band | Game | Lineup | Status | Rank Reason`
- Starting Lineup / bench are shown in one combined table.
- Row highlighting rules in Starting Lineup tab:
  - starter row green when `Rank > Threshold`
  - starter row yellow when `Rank == Threshold`
  - starter row red when `Rank < Threshold`
  - `Slot` and `Threshold` columns stay unhighlighted
  - bench / IL / NA rows stay unhighlighted
- Bench icon styling:
  - `BN = ⬜ BN`
  - `IL` and `NA` keep their existing labels
- Slot usage tracker is seeded and proven.
- `fetch_remaining_starts_by_slot(...)` is proven to decrement correctly day over day from snapshots.
- Batter Free Agents tab is wired and usable.
- Batter Free Agents now use Yahoo-confirmed `status=FA`, sorted by `OR`, paginated in 25-row pages, with `;out=percent_owned`.
- Batter Free Agents exclude waiver players because `status=W` players are not included in the `status=FA` source.
- Batter Free Agents exclude Yahoo `IL`, `NA`, and `SUSP` candidates using Yahoo status fields and `eligible_positions`.
- Batter Free Agents apply the current screen: batter only, active only, MLB team playing today, DB `rank_value <= 600`, Yahoo percent-owned `> 0`.
- Chase Meidroth proof passed: present in generated CSV/app rows after the source fix.
- Jose Altuve proof passed: excluded because Yahoo returns him as waiver, not free agent.
- Luis Campusano proof passed: excluded because Yahoo returns him with `IL10` / `10-Day Injured List`.
- DTD batters remain eligible but receive a mild status-risk penalty.
- MLB game status is captured in `scripts/refresh_mlb_probable_pitcher_daily.py`.
- Postponed games are classified as `POSTPONED`, display the postponement reason when available, use `LINEUP_NOT_APPLICABLE`, and rank as unavailable.
- Proven postponed-game behavior from `2026-05-05`:
  - `POSTPONED_OWNED 6`
  - `POSTPONED_FA 16`
  - `BAD_POSTPONED_RANKS []`
- Current-date Yahoo FA discovery proof from `2026-05-10`:
  - generated CSV rows: `46`
  - app FA rows: `45`
  - `HAS_CHASE_MEIDROTH True`
  - `HAS_JOSE_ALTUVE False`
  - `HAS_LUIS_CAMPUSANO False`

---

## Recently completed / resolved items
### 1. Lineup matching proof cycle
Status: **Functionally resolved for this proof cycle.**

What was proven:
- Current lineup files for `2026-05-04` existed:
  - `data/derived/starting_lineup_players_2026-05-04.csv`
  - `data/derived/starting_lineup_teams_2026-05-04.csv`
- `2026-05-04` team file had 24 team rows:
  - `23` with `lineup_posted = Y`
  - `1` with `lineup_posted = N`
- Batter rows for `2026-05-04` returned:
  - `9 IN_POSTED_LINEUP`
  - `8 POSTED_BUT_NOT_FOUND`
  - `3 LINEUP_NOT_APPLICABLE`
- `POSTED_BUT_NOT_FOUND` diagnostics showed exact normalized match count `0` for each flagged player, and those players were absent from the posted lineups.
- Therefore, the tested `POSTED_BUT_NOT_FOUND` rows were true lineup absences, not failed matches.

Important interpretation:
- Lineup matching was not the active bug in this proof cycle.
- The earlier problem was partly stale runtime context: the container was pinned to `DEFAULT_AS_OF_DATE=2026-04-28` even though `.env` had been updated.
- Recreating the app container picked up the corrected env.
- The app now uses dynamic New York today by default.

Operational caution:
- If refresh runs before teams post lineups, rows may show `LINEUP_NOT_CONFIRMED` until a later refresh.
- `POSTED_BUT_NOT_FOUND` should be expected when a player’s team lineup is posted and the player is not in it.

### 2. Batter Free Agents proof cycle
Status: **Wired, usable, and source-corrected.**

Current source contract:
- Yahoo endpoint shape:
  - `/league/{league_key}/players;status=FA;sort=OR;start={start};count=25;out=percent_owned?format=json`
- Pagination:
  - `start += 25`
  - Yahoo returns 25 rows per page even if a larger count is requested
- `;out=percent_owned` is the valid syntax.
- `/out=percent_owned` is invalid and returns HTTP 400.

Current filtering contract:
- include only Yahoo `status=FA`
- exclude waivers by source selection, because Yahoo waiver players are `status=W`, not `status=FA`
- exclude pitchers (`P`, `SP`, `RP`)
- exclude inactive/unavailable (`IL`, `IL10`, `IL15`, `IL60`, `NA`, `SUSP`, or non-empty `status_full`)
- require MLB team to be playing today
- require DB `rank_value <= 600`
- require Yahoo percent-owned `> 0`

Proof from `2026-05-10` after the fix:
- `YAHOO_FA_TOTAL 1946`
- `FA_BATTERS 961`
- `ACTIVE_FA_BATTERS 190`
- generated CSV rows: `46`
- app FA rows: `45`
- `HAS_CHASE_MEIDROTH True`
- `HAS_JOSE_ALTUVE False`
- `HAS_LUIS_CAMPUSANO False`

Target-player interpretation:
- Chase Meidroth was missing under the old source but now appears correctly:
  - `469.p.62504,Chase Meidroth,CWS`
- Jose Altuve is excluded correctly because he is not Yahoo `status=FA`; he was proven under waiver status.
- Luis Campusano is excluded correctly because Yahoo marks him `IL10` with `status_full = 10-Day Injured List`.

Important interpretation:
- The old to-do item “Wire Batter Free Agents tab” is stale.
- Updated framing: “Batter Free Agents is wired and source-corrected; later enhancements should focus on add/drop recommendations, lineup gating, and replacement-market statistics.”

### 3. Streamlit navigation cleanup
Status: **Completed and pushed.**

What changed:
- `streamlit_app.py` became a Streamlit router using `st.navigation` / `st.Page`.
- `pages/batters.py` now holds the working Batters UI.
- `pages/pitchers.py` is a Streamlit Pitchers page shell.
- Old Dash-style page placeholders were replaced.
- Sidebar now shows only:
  - Batters
  - Pitchers
- Pitchers was removed from the internal Batters tab set.
- Batters internal tabs are now:
  - Starting Lineup
  - Slots
  - Batter Free Agents

### 4. Unavailable status and postponed-game handling
Status: **Completed and pushed.**

Commit:
- `9ee6129 Fix Yahoo free agent batter discovery`

What changed:
- Free Agent batters with `IL` or `NA` in Yahoo `eligible_positions` are excluded from recommendations.
- DTD batters are not excluded, but receive a mild `-3.0` status-risk penalty.
- Rank Reason now includes `S = Status`.
- MLB game status is preserved in `mlb_probable_pitcher_daily.raw_json`.
- `services/queries.py` classifies proven MLB postponed games where:
  - `detailedState = Postponed`
  - or `statusCode = DI`
- Postponed games display as `Postponed - <reason>` when MLB provides a reason.
- Postponed-game rows use `LINEUP_NOT_APPLICABLE`.
- Postponed-game rows rank `0`.

Proof:
- `COMPILE_OK`
- Historical postponed proof from `2026-05-05`:
  - `POSTPONED_OWNED 6`
  - `POSTPONED_FA 16`
  - `BAD_POSTPONED_RANKS []`
- Current-date proof from `2026-05-08`:
  - `FA_IL_NA_ROWS []`

Important interpretation:
- This fixed the problem where postponed-game players and unavailable Free Agents could still appear as playable recommendations.
- Threshold logic was intentionally left unchanged because it was already dynamic.
- DTD is treated as risk, not unavailability.

### 5. Yahoo free-agent discovery correction
Status: **Completed and pushed.**

Commit:
- `9ee6129 Fix Yahoo free agent batter discovery`

What changed:
- `runtime/refresh_all.sh` no longer uses the old DB anti-roster join for Batter Free Agent discovery.
- The active FA generator now uses Yahoo’s native `status=FA` source with:
  - `sort=OR`
  - `count=25`
  - `start += 25`
  - `;out=percent_owned`
- The generator writes the existing compatibility file:
  - `data/derived/true_free_agent_batters_<date>.csv`
- The filename is retained for compatibility, but its contents are now Yahoo-confirmed addable free agents.

Proof:
- `PYTHON_SYNTAX_OK`
- `BASH_SYNTAX_OK`
- `OLD_FA_SOURCE_ABSENT`
- generated CSV rows: `46`
- app FA rows: `45`
- Chase Meidroth included
- Jose Altuve excluded
- Luis Campusano excluded

Important interpretation:
- This resolved the bug where waiver players and unavailable players could leak into the Batter Free Agents tab.
- This also resolved the issue where valid addable players such as Chase Meidroth were missed by the earlier FA discovery logic.
- No new table, no new pipeline, and no new permanent script were added.

### 6. Git checkpoint
Status: **Latest code fix committed and pushed.**

Latest pushed checkpoint:
- `9ee6129 Fix Yahoo free agent batter discovery`

Earlier relevant checkpoints:
- `824d2a2 Handle unavailable batters and postponed games`
- `cca0f26 Fix roster manager date resolution and Streamlit navigation`

Repo hygiene after FA fix:
- local `runtime/refresh_all.sh.bak_*` backup artifacts were removed
- `.gitignore` includes `*.bak_*` for future deterministic proof backups
- generated Yahoo probe/script data should remain ignored

Do not commit `.env`.
Do not commit generated Yahoo data unless explicitly deciding it belongs in source.

## Current known issues / remaining work
### 1. Build pitcher workflow
Status: **Next major feature.**

Pitchers page currently exists as a shell only.

Likely pitcher workflow needs:
- roster pitchers
- today’s probable starters
- pitcher free agents
- pitcher ranking / scoring model
- starter vs reliever handling
- matchup and schedule context

Start with source/data proof before patching.

### 2. MLBAM disambiguation is still incomplete
Known examples:
- `Jose Fernandez` had multiple candidates
- some `matched_team_name` values are blank

Interpretation:
- current mapping is usable but not fully deterministic for ambiguous names.

### 3. Recent still lacks true H/AB from Yahoo last-7 feed
Observed pattern:
- recent rows often show `0/0` for hits/AB
- recent scoring currently relies mainly on category totals instead of AVG contribution

### 4. Future opportunity denominator is still optimistic
Current V3 denominator counts future team game-days as opportunities for current active hitters.
That means it does **not yet** discount for:
- player rest days
- platoons
- lineup uncertainty
- DTD reliability risk

Recommended later refinement:
- lineup-reliability weighting on `future_roster_opps`

### 5. Yahoo API does not currently provide the live max-games table through tested team endpoints
The following authenticated Yahoo team endpoints were probed successfully and returned 200:
- `/team/{team_key}`
- `/team/{team_key}/roster`
- `/team/{team_key}/stats`
- `/team/{team_key}/standings`
- `/team/{team_key}/matchups`

But none exposed the hitter slot cap table (`Played / Remaining / Projected / Max`) directly.

Current design:
- use **seed once from Yahoo UI**
- then **maintain internally** from `roster_snapshot`

### 6. Repo hygiene
Status: **Mostly clean after latest proof cycle.**

Current rules:
- generated Yahoo data should not be committed
- `.env` should never be committed
- local deterministic backup files like `*.bak_*` should not be committed
- `.gitignore` includes `*.bak_*` so future proof backups do not appear as untracked zombie files

If probe utilities are created later:
- remove them after the proof cycle unless they become intentional permanent source utilities
- do not keep one-off probes as zombie code

---

## Scoring model summary
### Current batter components
- `B` = Bat baseline
- `P` = Pitcher matchup
- `H` = Handedness
- `H/A` = Home/Away
- `D/N` = Day/Night
- `R` = Recent
- `S` = Status risk
- `L` = Lineup

### Important scoring notes
- Split scoring was intentionally recalibrated.
- Splits are now based on **active split vs player overall OPS**, not active split vs opposite split.
- Split effects are shrunk by sample size.
- Split effects are intentionally more modest than earlier versions.
- `L` is neutral (`+0.0`) for confirmed starters.
- A `-30.0` lineup modifier is expected when a posted lineup omits the player.
- DTD status applies a mild `-3.0` status-risk penalty.
- `IL*` and `NA` status override to unavailable.
- `NO_GAME_TODAY` and `POSTPONED` override to unavailable.
- Batter Free Agent candidates come from Yahoo `status=FA`; waiver players are excluded by source, and `IL`, `NA`, `SUSP`, and non-empty `status_full` candidates are excluded before display/scoring.
- Some unavailable/bench rows may show `POSTED_BUT_NOT_FOUND` with `0.0` lineup points depending on row status/scoring path.

### Display note
Rank Reason is compressed in the UI to free space, for example:
- `B: +9.6 | P: -1.8 | H: +1.2 | H/A: +0.0 | D/N: -0.7 | R: -3.9 | S: -3.0 | L: +0.0`

---

## Slot-cap tracker details
### Source tables
#### `lineup_tool.roster_snapshot`
Confirmed columns include:
- `as_of_date`
- `league_key`
- `season_year`
- `team_key`
- `yahoo_player_key`
- `mlb_team_abbr`
- `position_type`
- `primary_position`
- `display_position`
- `eligible_positions`
- `selected_position`
- `status`
- `status_full`

This table is sufficient to roll forward hitter starts by slot from the seed date.

#### `lineup_tool.slot_usage_seed`
Purpose:
- seed one team's slot-cap usage from authoritative Yahoo UI values on a known date

Current seed for `469.l.22528.t.11` on `2026-05-03`:
- `C = played 32 / remaining 130 / max 162`
- `1B = played 33 / remaining 129 / max 162`
- `2B = played 33 / remaining 129 / max 162`
- `3B = played 32 / remaining 130 / max 162`
- `SS = played 33 / remaining 129 / max 162`
- `IF = played 30 / remaining 132 / max 162`
- `OF = played 100 / remaining 386 / max 486`
- `UTIL = played 32 / remaining 130 / max 162`

### Important limitation
`roster_snapshot` history for this team currently runs only:
- first snapshot date: `2026-04-13`
- distinct snapshot days previously proven: `19` as of the seed proof

So:
- snapshots alone are **not enough** to backfill full-season played counts from Opening Day
- but they are enough to maintain correct counts **going forward** from the seed date

---

## Current confirmed tracker proof
`fetch_remaining_starts_by_slot('469.l.22528', '469.l.22528.t.11', ...)` returned:

For `2026-05-03`:
- `{'1B': 129, '2B': 129, '3B': 130, 'C': 130, 'IF': 132, 'OF': 386, 'SS': 129, 'UTIL': 130}`

For `2026-05-04`:
- `{'1B': 128, '2B': 128, '3B': 129, 'C': 129, 'IF': 131, 'OF': 383, 'SS': 128, 'UTIL': 129}`

Interpretation:
- seed values are being honored
- roll-forward from the `2026-05-03` snapshot is working

---

## Known-good operational commands
### Recreate only the app container with current `.env`
Use this after changing `.env` or app code:

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1
docker-compose -f runtime/docker-compose.yml up -d --force-recreate --no-deps roster_manager
```

### Rebuild / restart the primary app
```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1
docker-compose -f runtime/docker-compose.yml up -d --build roster_manager
```

### Restart only
```bash
docker restart mlf_roster_manager
```

Important:
- `docker restart` does **not** reload changed environment variables.
- Use controlled recreate when `.env` changes.

### Run full refresh from host
```bash
/Volume1/Bots/fantasy/mlf_roster_manager/runtime/refresh_all.sh
```

### Run full refresh exactly as the Streamlit button runs it
```bash
docker exec -i mlf_roster_manager bash -lc "/bin/bash /app/runtime/refresh_all.sh"
```

### Verify container is up
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'mlf_roster_manager|NAMES'
curl -I http://127.0.0.1:8050
```

### Compile app code in the container
```bash
docker exec -i mlf_roster_manager bash -lc '
cd /app || exit 1
python -m py_compile streamlit_app.py pages/batters.py pages/pitchers.py services/queries.py services/scoring.py scripts/refresh_mlb_probable_pitcher_daily.py
'
```

### Inspect current batter rows from engine
```bash
docker exec -i mlf_roster_manager bash -lc "
cd /app && python - << 'PY'
from collections import Counter
from services.queries import get_default_context, fetch_batter_roster_rows
ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])
print('CTX_DATE', ctx['as_of_date'])
print('ROWS', len(rows))
print('LINEUP_STATUS_COUNTS', dict(Counter(r.get('lineup_status', '') for r in rows)))
for r in rows[:20]:
    print(
        r['player_display'], '|',
        r.get('lineup_status', ''), '|',
        r.get('lineup_points', ''), '|',
        r['ranking'], '|',
        r['note_short']
    )
PY
"
```

### Inspect Batter Free Agents rows
```bash
docker exec -i mlf_roster_manager bash -lc '
cd /app || exit 1
python - << "PY"
from collections import Counter
from services.queries import get_default_context, fetch_available_batter_rows
ctx = get_default_context()
rows = fetch_available_batter_rows(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])
print("CTX_DATE", ctx["as_of_date"])
print("FA_ROWS", len(rows))
print("LINEUP_STATUS_COUNTS", dict(Counter(r.get("lineup_status", "") for r in rows)))
print("STATUS_COUNTS", dict(Counter(r.get("status_display", "") for r in rows)))
for r in rows[:25]:
    print(
        r.get("player_display", ""),
        "|", r.get("eligible_display", ""),
        "| Rank", r.get("ranking", ""),
        "|", r.get("mlb_team_abbr", ""),
        "|", r.get("game_display", ""),
        "|", r.get("lineup_status", ""),
        "|", r.get("status_display", ""),
        "|", r.get("note_short", "")
    )
PY
'
```

### Inspect current remaining starts from tracker
```bash
docker exec -i mlf_roster_manager bash -lc "
cd /app && python - << 'PY'
from services.queries import fetch_remaining_starts_by_slot
for as_of_date in ['2026-05-03', '2026-05-04']:
    print(as_of_date, fetch_remaining_starts_by_slot('469.l.22528', '469.l.22528.t.11', as_of_date))
PY
"
```

### Inspect lineup files
```bash
TODAY=$(date +%F)

echo '--- players ---'
sed -n '1,20p' /Volume1/Bots/fantasy/mlf_roster_manager/data/derived/starting_lineup_players_${TODAY}.csv

echo
echo '--- teams ---'
sed -n '1,20p' /Volume1/Bots/fantasy/mlf_roster_manager/data/derived/starting_lineup_teams_${TODAY}.csv
```

### Sanitized `.env` newline check
Do not print secrets.

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

### Inspect refresh logs / status
Host path:
- logs: `/Volume1/Bots/fantasy/mlf_roster_manager/runtime/logs/`
- status: `/Volume1/Bots/fantasy/mlf_roster_manager/runtime/status/`

Inside container:
- logs: `/app/runtime/logs/`
- status: `/app/runtime/status/`

---

## Git workflow
Git is not installed on the NAS shell. Use Windows PowerShell from the personal laptop.

Preferred workflow:
- Keep PowerShell parked at the repo root:

```powershell
Set-Location "\\Apollo\Bots\fantasy\mlf_roster_manager"
```

Then run Git commands directly from that location:

```powershell
git status --short
git diff --stat

git add <files>
git commit -m "message"
git push origin main

git status --short
```

Rules:
- Do not commit `.env`.
- Do not commit generated data unless explicitly intended.
- Do not commit local proof backups (`*.bak_*`).
- Commit small deterministic increments.
- Use NAS SSH for runtime/Docker proof and PowerShell for Git.

Latest pushed checkpoint:
- `9ee6129 Fix Yahoo free agent batter discovery`

## How to find answers instead of guessing
When the next chat needs to answer a question, use this search order:

### If the question is about UI behavior
Check:
1. `streamlit_app.py` for router behavior
2. `pages/batters.py` for Batters page behavior
3. `pages/pitchers.py` for Pitchers page behavior
4. live browser refresh or app logs

### If the question is about how rows are built
Check:
- `services/queries.py`
- then run the batter-row verification command

### If the question is about scoring
Check:
- `services/scoring.py`
- then verify with a printed sample of batter rows and rank reasons

### If the question is about threshold / slot-cap behavior
Check in order:
1. `services/queries.py::fetch_remaining_starts_by_slot`
2. `lineup_tool.slot_usage_seed`
3. `lineup_tool.roster_snapshot.selected_position`
4. `pages/batters.py::compute_schedule_pressure_meta`
5. live Starting Lineup table (`Threshold`, row highlighting, Slot floors caption, Skip budget caption)

### If the question is about date context
Check:
1. `.env` keys without printing secrets
2. `services/queries.py::resolve_as_of_date`
3. `services/queries.py::get_default_context`
4. `pages/batters.py::get_runtime_context`
5. container environment with sanitized key-length output

### If the question is about data freshness / refresh behavior
Check:
- `runtime/refresh_live.sh`
- `runtime/refresh_all.sh`
- then read latest files under `data/derived/`
- then inspect runtime logs / status JSON

### If the question is about Batter Free Agent availability
Check in order:
1. `runtime/refresh_all.sh` FA generator source
2. generated `data/derived/true_free_agent_batters_<date>.csv`
3. `services/queries.py::fetch_available_batter_rows(...)`
4. final app rows from the Batter Free Agents inspection command

Current Yahoo source rule:
- `status=FA`
- `sort=OR`
- `start += 25`
- `count=25`
- `;out=percent_owned`

Do not use:
- broad DB anti-roster joins as the source of FA truth
- Yahoo `status=A` for addable Free Agents, because it includes waiver players
- `/out=percent_owned`, because the working syntax is `;out=percent_owned`

### If the question is about lineup confirmation
Check in order:
1. `scripts/refresh_starting_lineups.py`
2. `data/derived/starting_lineup_players_<date>.csv`
3. `data/derived/starting_lineup_teams_<date>.csv`
4. matching logic in `services/queries.py`
5. final batter-row output from engine

Lineup status decision ladder in `services/queries.py`:
- missing lineup files → `LINEUP_DATA_MISSING`
- no MLB game or postponed MLB game -> `LINEUP_NOT_APPLICABLE`
- player found in posted lineup → `IN_POSTED_LINEUP`
- team lineup posted but player absent → `POSTED_BUT_NOT_FOUND`
- team lineup not posted / not proven → `LINEUP_NOT_CONFIRMED`

### If the question is about Yahoo max-games automation
Remember:
- tested Yahoo Fantasy API team endpoints do **not** expose the live max-games table directly
- current production design is **seed once from Yahoo UI, then maintain internally**

### If the question is about a broken script import
Check whether the script is run with:
- `PYTHONPATH=/app`

### If the question is about Streamlit refresh behavior
Check:
- sidebar button code in `pages/batters.py`
- `/app/.env` runtime values
- direct in-container execution of `/app/runtime/refresh_all.sh`

---

## UI status snapshot
### Implemented
- Explicit Streamlit sidebar navigation
- Batters sidebar page
- Pitchers sidebar page shell
- Starting Lineup tab under Batters
- Slots tab under Batters
- Batter Free Agents tab under Batters
- compressed Rank Reason display
- legend at bottom of Batters page
- sidebar slot overrides
- in-app refresh button
- Slot cap source sidebar expander
- Threshold column in Starting Lineup table
- Threshold-based starter row highlighting
- combined starting lineup + bench table
- schedule-pressure slot floors and skip-budget captions
- dynamic New York date resolution

### Placeholder / partial
- Pitchers page
- lineup-reliability weighting
- recent H/AB contribution
- MLBAM ambiguous-name cleanup

---

## Recommended next development queue
Stop after documentation + GitHub unless explicitly starting a new cycle.

Recommended order:
1. Commit documentation for the Yahoo FA discovery fix and repo hygiene updates
2. Resume Usual-RMT / MLF-RMT / MiLF-RMT architecture split only after docs are locked
3. Build pitcher workflow after the league-specific split decision is stable
4. Add lineup-reliability weighting to future opportunity denominator
5. Add replacement-market statistics for slot thresholds, likely starting with rolling 7-day top FA ranks by slot
6. Add tomorrow / day-after-tomorrow FA ranking support later
7. Fix true recent H/AB so AVG contribution is real
8. Finish MLBAM team-aware disambiguation
9. Optional: harden Batter Free Agents with add/drop recommendation logic and lineup-gated display filters
10. General UI polish and width cleanup

---

## GitHub / repo status
Repository is live and public:

- GitHub: `https://github.com/christopherlyman/MajorLeagueFantasy-RosterManager`
- Remote: `origin`
- Default branch: `main`
- Local branch tracks `origin/main`
- License: `MIT`

Latest pushed checkpoint:
- `9ee6129 Fix Yahoo free agent batter discovery`

Operational rule:
- make documentation/code changes locally
- commit in small deterministic increments
- push to `origin/main`

---

## Bootstrap prompt for a new chat
Use the text below when starting a fresh chat:

> Read this handoff as the source of truth for the Roster Manager project.
> Follow these rules: No Zombie Code, small micro-step instructions, deterministic proof-first, concise responses, exact commands only, user runs commands and pastes outputs, do not guess.
> Treat Streamlit as the primary UI on `mlf_roster_manager` port `8050`.
> Use NAS SSH for Docker/runtime proof and Windows PowerShell for Git.
> `streamlit_app.py` is now a router. Batters logic lives in `pages/batters.py`. Pitchers has its own sidebar page at `pages/pitchers.py`.
> Date resolution uses New York today by default when `DEFAULT_AS_OF_DATE=` is blank. `DEFAULT_DATE_OFFSET_DAYS=0/1/2` supports today/tomorrow/day-after.
> Lineup matching was proven functionally correct for the latest proof cycle; do not reopen it unless new proof shows `LINEUP_NOT_CONFIRMED` or incorrect `POSTED_BUT_NOT_FOUND` behavior.
> Batter Free Agents is wired and source-corrected; do not treat it as an unwired placeholder. FA discovery uses Yahoo `status=FA;sort=OR;count=25;start+=25;out=percent_owned`. Waivers, IL/NA/SUSP, and inactive candidates are excluded; DTD batters receive a mild status-risk penalty; postponed games rank as unavailable.
> Next major feature is the Pitchers workflow.
> First, confirm the current architecture, known-good commands, and open issues before proposing changes.
> When investigating, prefer current files, DB, logs, CSV outputs, and live row proof over theorizing.
