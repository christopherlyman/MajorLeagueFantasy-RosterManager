# Roster Manager Handoff

## Purpose
Daily roster-management tool for personal fantasy baseball lineup decisions.

Current scope:
- **Primary UI:** Streamlit
- **Primary focus:** batter sit/start decisions
- **Current strengths:** roster refresh, game/probable pitcher context, handedness, recent, splits, slot-based optimized lineup UI, manual slot overrides, working in-app **Refresh All** flow
- **Current gaps:** lineup matching still not fully turning posted lineups into `IN_POSTED_LINEUP` / `POSTED_BUT_NOT_FOUND`; batter free agents and pitchers are not fully built; MLBAM disambiguation still weak for some ambiguous names

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
- Prefer proving truth from current files / logs / queries over theorizing

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
- Streamlit is the **primary** UI now; Dash is no longer the active target UI.

### Important paths
- `streamlit_app.py` — primary UI
- `runtime/docker-compose.yml` — primary app container definition
- `runtime/refresh_live.sh` — live refresh pipeline
- `runtime/refresh_all.sh` — full refresh pipeline
- `services/queries.py` — row assembly and data loading
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
   - MLB game + probable pitcher data
   - probable pitcher handedness
   - recent inputs
   - hitter split inputs
   - lineup files
3. `services/scoring.py` computes ranking and short reason string.
4. `streamlit_app.py` renders:
   - Starting Lineup tab
   - Slots tab
   - Batter Free Agents tab (placeholder / not fully wired)
   - Pitchers tab (placeholder / not fully wired)

### Streamlit-specific notes
- Streamlit is the primary app on `http://Apollo:8050`
- The app includes:
  - slot overrides in the sidebar
  - a working **Refresh All** button path
  - compressed Rank Reason display (`B`, `P`, `H`, `H/A`, `D/N`, `R`, `L`)
- Streamlit reads runtime defaults from `/app/.env`

---

## Current confirmed working state
These are considered working unless proven otherwise:
- Streamlit is the primary UI on port `8050`
- Refresh scripts can be run from terminal and from inside the Streamlit container
- In-app **Refresh All** path has been proven to run end-to-end
- Roster snapshot refresh works
- MLB games / probable pitchers refresh works
- Probable pitcher handedness refresh works
- Recent refresh works
- Hitter splits refresh works
- Slot optimizer / manual slot override UI works
- Rank Reason compression and legend are in place

---

## Current known issues
### 1. Lineup matching is not fully resolved
Observed state:
- lineup files can contain posted rows
- but hitter rows may still remain `LINEUP_NOT_CONFIRMED`

Interpretation:
- lineup ingestion itself is now producing team/player files
- matching from lineup files into batter rows still needs work

Priority:
- **Highest remaining backend issue** for batter decisions

### 2. MLBAM disambiguation is still incomplete
Known examples:
- `Jose Fernandez` had multiple candidates
- some `matched_team_name` values are blank

Interpretation:
- current mapping is usable but not fully deterministic for ambiguous names

### 3. Recent still lacks true H/AB from Yahoo last-7 feed
Observed pattern:
- recent rows often show `0/0` for hits/AB
- recent scoring currently relies mainly on category totals instead of AVG contribution

### 4. Batter Free Agents tab is not wired yet
UI tab exists, backend is not fully implemented.

### 5. Pitchers tab is only a shell
Pitcher UI / ranking / optimizer still needs a later development cycle.

---

## Scoring model summary
### Current batter components
- `B` = Bat baseline
- `P` = Pitcher matchup
- `H` = Handedness
- `H/A` = Home/Away
- `D/N` = Day/Night
- `R` = Recent
- `L` = Lineup

### Important scoring notes
- Split scoring was intentionally recalibrated.
- Splits are now based on **active split vs player overall OPS**, not active split vs opposite split.
- Split effects are shrunk by sample size.
- Split effects are intentionally more modest than earlier versions.
- `L` is currently neutral (`+0.0`) unless lineup proof is trustworthy.

### Display note
Rank Reason is compressed in the UI to free space, for example:
- `B: +9.6 | P: -1.8 | H: +1.2 | H/A: +0.0 | D/N: -0.7 | R: -3.9 | L: +0.0`

---

## Known-good operational commands
### Rebuild / restart the primary app
```bash
Docker stop mlf_roster_manager
Docker rm mlf_roster_manager
Docker-compose -f /Volume1/Bots/fantasy/mlf_roster_manager/runtime/docker-compose.yml up -d --build
```

### Restart only
```bash
docker restart mlf_roster_manager
```

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

### Inspect current batter rows from engine
```bash
docker exec -i mlf_roster_manager bash -lc "
cd /app && python - << 'PY'
from services.queries import get_default_context, fetch_batter_roster_rows
ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx['league_key'], ctx['team_key'], ctx['as_of_date'])
print('ROWS', len(rows))
for r in rows[:15]:
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

### Inspect lineup files
```bash
TODAY=$(date +%F)

echo '--- players ---'
sed -n '1,20p' /Volume1/Bots/fantasy/mlf_roster_manager/data/derived/starting_lineup_players_${TODAY}.csv

echo
echo '--- teams ---'
sed -n '1,20p' /Volume1/Bots/fantasy/mlf_roster_manager/data/derived/starting_lineup_teams_${TODAY}.csv
```

### Inspect refresh logs / status
Host path:
- logs: `/Volume1/Bots/fantasy/mlf_roster_manager/runtime/logs/`
- status: `/Volume1/Bots/fantasy/mlf_roster_manager/runtime/status/`

Inside container:
- logs: `/app/runtime/logs/`
- status: `/app/runtime/status/`

---

## How to find answers instead of guessing
When the next chat needs to answer a question, use this search order:

### If the question is about UI behavior
Check:
- `streamlit_app.py`
- then verify with a live browser refresh or app log

### If the question is about how rows are built
Check:
- `services/queries.py`
- then run the batter-row verification command

### If the question is about scoring
Check:
- `services/scoring.py`
- then verify with a printed sample of batter rows and rank reasons

### If the question is about data freshness / refresh behavior
Check:
- `runtime/refresh_live.sh`
- `runtime/refresh_all.sh`
- then read latest files under `data/derived/`
- then inspect runtime logs / status JSON

### If the question is about lineup confirmation
Check in order:
1. `scripts/refresh_starting_lineups.py`
2. `data/derived/starting_lineup_players_<date>.csv`
3. `data/derived/starting_lineup_teams_<date>.csv`
4. matching logic in `services/queries.py`
5. final batter-row output from engine

### If the question is about a broken script import
Check whether the script is run with:
- `PYTHONPATH=/app`

### If the question is about Streamlit refresh behavior
Check:
- sidebar button code in `streamlit_app.py`
- `/app/.env` runtime values
- direct in-container execution of `/app/runtime/refresh_all.sh`

---

## UI status snapshot
### Implemented
- Starting Lineup tab
- Slots tab
- compressed Rank Reason display
- legend at bottom of page
- sidebar slot overrides
- in-app Refresh All button

### Placeholder / partial
- Batter Free Agents tab
- Pitchers tab

---

## Recommended next development queue
Stop after documentation + GitHub unless explicitly starting a new cycle.

If development resumes later, recommended order:
1. Fix lineup matching so posted team lineups become `IN_POSTED_LINEUP` / `POSTED_BUT_NOT_FOUND`
2. Wire Batter Free Agents backend
3. Build pitcher UI / ranking / free agents
4. Finish MLBAM team-aware disambiguation
5. General UI polish and width cleanup

---

## GitHub / repo status
Repository is now live and public:

- GitHub: `https://github.com/christopherlyman/MajorLeagueFantasy-RosterManager`
- Remote: `origin`
- Default branch: `main`
- Local branch tracks `origin/main`
- License: `MIT`

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
> First, confirm the current architecture, known-good commands, and open issues before proposing any changes.
> When investigating questions, prefer reading the current scripts/files and proving behavior from logs / CSV outputs rather than theorizing.

