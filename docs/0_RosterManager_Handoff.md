# Roster Manager Handoff

**Last updated:** 2026-06-18  
**Project:** MajorLeagueFantasy-RosterManager  
**Primary repo path:** `/Volume1/Bots/fantasy/mlf_roster_manager`  
**Windows/UNC path:** `\\Apollo\Bots\fantasy\mlf_roster_manager`

---

## Purpose

Daily roster-management tool for personal fantasy baseball lineup decisions across RMT instances.

The current highest-value workflow is the **Usual-RMT batter decision system**, including live lineup ranking, future batter projection views, Batter Free Agents, slot-cap pressure, daily action planning, and RMT-vs-YGMA model evaluation.

Current scope:
- **Primary UI:** Streamlit
- **Primary host:** Apollo NAS
- **Primary runtime style:** Docker containers
- **Primary current instance:** `usual-rmt` on port `8050`
- **Additional RMT instances:** `mlf_rmt` on port `8051`, `milf_rmt` on port `8052`
- **Current next evidence target:** RMT-v2 gate-threshold audit for startability, reliability, and elite-player protection.
- **Next product/UI feature after gate proof:** Roster Experiment / Add-Drop Watchlist.
- **Current known weak spot:** Usual-RMT pitcher IP projection is approximate; batter max-games projection currently matches Yahoo-style behavior.
- **Current model caution:** Do not patch RMT scoring weights until the RMT-vs-YGMA gate audit identifies rules that preserve good RMT spot-start signal while blocking bad overrides.

---

## Working rules for the next chat

Follow these strictly:
- **No Zombie Code**
- **Small micro-step instructions**
- **Deterministic / proof-first**
- **Concise responses**
- **Provide exact commands; user runs them and pastes output**
- **Do not guess**
- **Investigate first, then act**
- Prefer current files, DB rows, logs, generated CSVs, and live runtime output over theory.
- Do not patch unless the proposed change is directly supported by proof.
- One read-only proof step should usually precede each write step.
- Use NAS SSH for Docker/runtime proof.
- Use Windows PowerShell for Git because Git is **not installed on the NAS shell**.

---

## Environment

### Host

- **Host name:** Apollo
- **NAS project path:** `/Volume1/Bots/fantasy/mlf_roster_manager`
- **Windows/UNC path:** `\\Apollo\Bots\fantasy\mlf_roster_manager`
- NAS shell has old `python` as Python 2.7; use `python3` on host scripts.
- Inside containers, use `python` / `python3` from `/app`.
- NAS shell does **not** have Git. Run Git from Windows PowerShell against the UNC path.
- NAS uses `docker-compose`, not the newer `docker compose` plugin.

### Remote access / Tailscale

Tailscale has been proven running on Apollo during the June 2026 remote-access setup.

Current known Tailnet devices from `tailscale status`:
- Apollo NAS: `100.93.229.49`
- Windows laptop `steady2`: `100.114.190.31`

Important facts:
- `/usr/bin/tailscale` and `/usr/sbin/tailscaled` exist on Apollo.
- Docker Tailscale failed because a native `tailscaled` process already owned `tailscale0`.
- The working command started native `tailscaled` manually with `nohup`.
- The daemon is authenticated under `chris.h.lyman@gmail.com`.
- This setup is useful for reaching app ports and NAS network services over the Tailnet.

Cautions:
- This manual `nohup` start is **not yet proven persistent after NAS reboot**.
- Do not reboot Apollo before persistence is proven.
- SSH on port `22` previously returned `connection refused`; Tailscale reachability does not prove TerraMaster SSH service is enabled.
- Remote development is proven only after laptop hotspot testing reaches the RMT apps and/or SMB share.

Useful remote URLs after laptop is on Tailscale:
- Usual-RMT: `http://100.93.229.49:8050`
- MLF-RMT: `http://100.93.229.49:8051`
- MiLF-RMT: `http://100.93.229.49:8052`
- NAS share candidate: `\\100.93.229.49\Bots`


### Current containers and ports

Current RMT containers proven via HTTP checks:
- `usual-rmt` — Usual Suspects RMT — port `8050`
- `mlf_rmt` — MLF RMT — port `8051`
- `milf_rmt` — MiLF RMT — port `8052`

Shared Postgres container:
- `mlf_postgres`

Legacy/single-instance references may still exist in older docs/logs as:
- `mlf_roster_manager`
- port `8050`

Treat those as stale unless the current runtime proves otherwise.

### Important source paths

Current code paths observed in the active work:
- `views/batters.py` — primary Batters page / main batter UI logic
- `views/pitchers.py` — Pitchers UI logic
- `services/queries.py` — row assembly, data loading, date resolution, FA row assembly, game context
- `services/scoring.py` — batter scoring / ranking logic
- `services/batter_multiday.py` — reusable Today/Tomorrow/Day2 batter projection service
- `services/pitcher_queries.py` — pitcher row queries
- `services/pitcher_scoring.py` — pitcher ranking logic
- `runtime/refresh_live.sh` — live refresh pipeline
- `runtime/refresh_all.sh` — full refresh pipeline
- `scripts/refresh_projection_game_context.py` — refreshes Today/Tomorrow/Day2 probable pitcher + pitcher-hand context
- `scripts/yahoo/refresh_usual_daily_cap_usage.py` — Yahoo-dated roster/stat-based Usual cap usage refresh
- `scripts/refresh_mlb_probable_pitcher_daily.py` — MLB game/probable pitcher context
- `scripts/refresh_probable_pitcher_hand.py` — probable pitcher handedness
- `scripts/refresh_starting_lineups.py` — MLB starting lineup ingestion
- `scripts/refresh_hitter_splits_mlb.py` — hitter splits
- `data/raw/` — raw source captures
- `data/derived/` — generated CSV inputs used by the app
- `runtime/logs/` — refresh logs
- `runtime/status/` — refresh status JSON

Older docs may reference `pages/batters.py`; current active proof has been against `views/batters.py`.

---

## Git workflow

Git is not installed on the NAS shell.

Use Windows PowerShell:

```powershell
Push-Location "\\Apollo\Bots\fantasy\mlf_roster_manager"

git --no-pager status --short -uall
git --no-pager diff -- <files>

git add <files>
git commit -m "message"
git push origin main

git --no-pager status --short -uall
git --no-pager log --oneline -8

Pop-Location
```

Rules:
- Do not commit `.env`.
- Do not commit generated data unless explicitly intended.
- Do not commit local proof backups.
- Keep commits small and deterministic.
- Runtime/Docker proof happens over NAS SSH; Git proof happens from PowerShell.

Latest known repo proof from Windows PowerShell:
- Working tree was clean.
- `HEAD -> main`, `origin/main`, and `origin/HEAD` pointed to `dacf682`.
- Git operations are still run from Windows PowerShell against the UNC path.

Latest known pushed commits from the recent work:
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

---

## Runtime architecture

### High-level flow

1. Refresh scripts build/refresh source files and DB rows.
2. `services/queries.py` assembles live Today rows using:
   - roster snapshot
   - MLB game/probable pitcher data
   - MLB game status, including postponed-game detection
   - probable pitcher handedness
   - recent inputs
   - hitter split inputs
   - lineup files
3. `services/scoring.py` computes ranking and short rank-reason strings.
4. `services/batter_multiday.py` builds reusable Today/Tomorrow/Day2 batter projection rows.
5. `views/batters.py` renders:
   - Starting Lineup
   - Slots
   - Batter Free Agents
   - Roster Policy
   - Usual max-games/IP sidebar
6. `views/pitchers.py` renders current pitcher workflow, which is functional but still early.

### Current UI tabs under Batters

Batters internal tabs known from prior docs:
- Starting Lineup
- Slots
- Batter Free Agents
- Roster Policy

Recent commits also indicate:
- read-only batter recommendations tab
- daily batter action plan
- action plan build after daily refresh
- updated lineup row color rules

Before modifying the recommendations/action-plan UI, verify the exact active tab names and helper functions in `views/batters.py`.

Starting Lineup and Batter Free Agents now have a **Projection View** radio selector:
- Today
- Tomorrow
- Day After Tomorrow

The table shape stays the same while the selected projection date changes the rank/game/reason context.

---

## Date-resolution behavior

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
- `.env` should normally contain:
  - `DEFAULT_AS_OF_DATE=`
  - `DEFAULT_DATE_OFFSET_DAYS=0`
- If the app rolls past midnight ET before refresh has run, it will look for new-date files and rows.

---

## Usual-RMT cap usage / max-games architecture

### Current design

Usual-RMT now has a separate cap tracker for Yahoo max games and innings pitched.

Source tables:
- `rmt.usual_cap_usage_seed`
- `rmt.usual_daily_cap_usage`

The newer Usual cap tracker differs from the older `lineup_tool.slot_usage_seed` notes in older docs.

Current seed proof:
- league: `469.l.22528`
- team: `469.l.22528.t.11`
- season: `2026`
- seed date: `2026-05-17`
- source: `manual_yahoo_ui_actual_played_anchor`

Known P seed:
- `P|seed_used=406.000|max_allowed=1450.000|seed_as_of_date=2026-05-17`

Important boundary rule:
- `seed_used` already includes usage through the seed date.
- When reconstructing used values after the seed:
  - use daily rows where `usage_date > seed_as_of_date`
  - do **not** include `usage_date >= seed_as_of_date`
- Including the seed date daily row double-counts seed-day usage.

### Actual daily cap usage

The live refresh pipeline calls:
- `scripts/yahoo/refresh_usual_daily_cap_usage.py`

Important current behavior:
- Batter usage is based on Yahoo dated roster source of truth.
- The process uses the Yahoo roster for the historical date, not the RMT’s recommended lineup.
- This fixed drift caused by late manual pivots, such as moving Jo Adell into Util after Ty France was not starting.
- Pitcher IP actuals come from Yahoo team roster daily stats, `stat_id 50`.

### Hitter cap projection

Hitter max-game projection currently matches Yahoo-style behavior closely.

Current approach after latest proof:
- Calculate current used from seed + daily usage.
- Use Yahoo-style league-average MLB remaining games as the future-games baseline.
- Single-slot future games use half-up rounding from the average remaining MLB team games.
- OF future games use half-up rounding of `average_remaining_games * 3`, not `round(single_slot) * 3`.
- Yahoo appears to allow hitter projections to exceed Max.
- Latest relevant commit: `dacf682 Use Yahoo-style league average hitter cap projection`.

Recent proof pattern:
- Yahoo OF projection was matched by averaging remaining games across MLB teams and multiplying by three before rounding.
- This replaced earlier active-roster-only future baseline logic.

Older Yahoo/RMT alignment example retained as historical context:
- C `48/114/159/-3`
- 1B `52/110/163/+1`
- 2B `52/110/163/+1`
- 3B `51/111/162/0`
- SS `50/112/161/-1`
- IF `50/112/161/-1`
- OF `152/334/485/-1`
- Util `50/112/161/-1`

### Pitcher IP projection

Pitcher IP **actual used/remaining** is based on Yahoo daily IP actuals and is considered useful.

Pitcher IP **projection/diff** is approximate.

What was proven earlier:
- Yahoo API endpoints tested did not expose the displayed max-games/IP projection table directly.
- Tested endpoints returned league/team/roster/stats data, but not the UI table fields `Played / Remaining / Projected / Max`.
- Local code currently has daily `stat_id 50` IP actuals, but the projected IP value is RMT-local logic.

Newer user-assisted Yahoo UI observation:
- Yahoo’s displayed projected IP appeared to equal current used IP plus the sum of displayed remaining projected IP for current starting pitchers.
- The Yahoo UI remaining projected IP values appeared to be floored per pitcher before summing.
- Example pattern supplied by the user: `48.1 -> 48`, `41.2 -> 41`, `84.8 -> 84`.
- In that observation, floor-summing UI remaining projected IP gave `852`; `589 + 852 = 1441`, matching Yahoo’s displayed projected IP.

Important interpretation:
- This is an **ingestion gap**, not a formula patch.
- RMT does not currently ingest those Yahoo UI remaining projected IP values.
- Existing local `stat_id 50` values are current/actual IP stats, not Yahoo UI rest-of-season projected IP.
- Do not substitute current-season IP totals as ROS projected IP.

Current caution:
- Do not claim P projected/diff exactly match Yahoo.
- UI should label P projection/diff as approximate if shown.
- Do not patch the P projection formula using a single day’s fit.
- Recent attempts showed no single simple formula matched Yahoo P projections across 2026-05-21, 2026-05-22, and 2026-05-23.

Useful proof points:
- 2026-05-21 Yahoo P projected `1448`; current `-2` heuristic happened to match.
- 2026-05-22 Yahoo P projected `1444`; current `-2` heuristic was 4 IP too high.
- 2026-05-23 Yahoo P projected `1438`; a blend model was close, but not stable enough to patch.
- The correct reconstructed used IP for 2026-05-23 was `444.667` decimal IP, displayed as baseball IP `444.2`.

Recommended handling:
- Keep batter cap projection as Yahoo-style.
- Mark pitcher projection/diff approximate.
- Revisit only after finding a direct source for Yahoo UI remaining projected IP or collecting enough UI observations to build a stable, documented ingestion/model approach.

---

## Threshold / lineup optimization logic

### Threshold rule for Usual-RMT

Current Usual-RMT threshold behavior:
- For H2H RMTs (`MLF` / `MiLF`), start-every-active mode uses threshold `1.0`.
- For Usual-RMT:
  - apply threshold only when the slot diff is `>= +1`
  - when `diff < +1`, threshold becomes `0.0`
  - this lets the optimizer maximize total ranking when a slot is even or behind pace.

Patch intent:
- If a slot is projected ahead by at least 1, enforce the slot floor.
- If a slot is even or behind, do not block low-ranking game-day starts purely due to threshold.

Recent proof showed:
- C / 3B / SS / IF / OF / UTIL threshold `0.0` when not ahead.
- 1B and 2B should enforce threshold when at `+1`.

### Recent optimizer / recommendation commits

Recent commits indicate additional optimizer and recommendation behavior:
- `e9bd3fd Lock started Yahoo hitter slots in Today optimizer`
- `f2ce2b4 Prefer behind-pace hitter slots in optimizer`
- `b8c8c74 Add read-only batter recommendations tab`
- `0ffac87 Add daily batter action plan`
- `d92483e Build batter action plan after daily refresh`
- `4aef775 Update lineup row color rules`
- `ffb804a Color starting lineup rows by lineup confidence`

Interpretation rules:
- Treat these as implemented code checkpoints, but verify exact current behavior in `views/batters.py` and live app rows before modifying.
- Do not add Yahoo write actions from recommendation/action-plan surfaces until read-only behavior is stable.
- Started Yahoo hitter slots should remain protected from Today optimizer churn unless direct proof supports changing that behavior.

### Future-view optimizer bug fixed

Problem:
- Day After Tomorrow rows showed players with games and ranks, but the optimizer still ignored them.
- Example: Jackson Holliday had a game and positive rank but could not be placed into Util.

Root cause:
- Future projected rows updated `game_display`, but did not update internal `game_status`.
- `startable_for_slot(...)` calls `has_game_today(row)`, which checks `game_status`.
- Projected rows could display a future game while internally still failing as `NO_GAME_TODAY`.

Fix:
- `_project_batter_row(...)` now sets:
  - `game_status = "GAME_FOUND"` when projected game exists and is not `No game`
  - `game_status = "NO_GAME_TODAY"` otherwise

Proof after fix:
- Jackson Holliday changed from `StartableUTIL=False` to `StartableUTIL=True`.
- Day2 optimizer used playable game-day hitters correctly.
- Example Day2 assignment after fix:
  - OF3 = Jakob Marsee
  - UTIL = Bo Bichette
  - Jackson Holliday = SS

---

## Batter multi-day projection system

### What exists

A reusable backend service exists:
- `services/batter_multiday.py`

Main function:
- `build_batter_multiday_projection(ctx, days=3, include_fa=True)`

Projection dates:
- Today
- Tomorrow
- Day After Tomorrow

UI wiring:
- Starting Lineup tab has `Projection View` radio selector.
- Batter Free Agents tab has `Projection View` radio selector.
- Table shape stays the same across dates.
- Tomorrow and Day After Tomorrow show projected ranks and `PROJECTED` lineup status.
- Future views include an expander explaining what goes into projected rank.

### Data used for future batter ranks

Tomorrow / Day2 ranks are not pulled from Yahoo as future ranks. They are calculated by the RMT scoring model.

Future views use:
- today’s real owned roster
- today’s true Yahoo free-agent pool
- future game date
- player MLB team
- opponent
- home/away
- game time
- opposing probable pitcher
- probable pitcher handedness
- batter vs RHP / vs LHP OPS splits
- batter home/away splits
- batter day/night splits
- batter and pitcher Savant inputs
- recent 7-day form
- Start% / recent-start reliability
- H2H matchup adjustment when available

Future views are planning projections:
- lineups are not confirmed
- probable pitchers can change
- Yahoo transactions are not implied
- future FA pool uses today’s true FA pool, not a future top-300 fallback

### Projection context refresh

Permanent fix added:
- `scripts/refresh_projection_game_context.py`

Purpose:
- Refresh MLB probable pitcher and pitcher-hand context for:
  - Today
  - Tomorrow
  - Day After Tomorrow

It runs:
- `scripts/refresh_mlb_probable_pitcher_daily.py --as-of-date <date>`
- `scripts/refresh_probable_pitcher_hand.py --as-of-date <date> --out <hand_file>`

It is wired into both:
- `runtime/refresh_live.sh`
- `runtime/refresh_all.sh`

Why this matters:
- Without this, Day After Tomorrow rolled forward each morning and could show all players as `No game`.
- Proof after patch:
  - dates refreshed: `2026-05-23`, `2026-05-24`, `2026-05-25`
  - 2026-05-25 probable games: `13`
  - 2026-05-25 hand file existed
  - Day2 no-game rows dropped from all rows to a realistic subset

---

## Batter scoring model

### Current components

Rank Reason components:
- `B` = Bat baseline
- `P` = Pitcher matchup
- `H` = Handedness
- `H/A` = Home/Away
- `D/N` = Day/Night
- `R` = Recent
- `S` = Status risk
- `L` = Lineup

Rank Reason display is compressed:
- `B: +9.6 | P: -1.8 | H: +1.2 | H/A: +0.0 | D/N: -0.7 | R: -3.9 | S: -3.0 | L: +0.0`

### OPS-gap handedness scoring

Recent scoring fix:
- Current Hand formula was too compressed.
- Luke Raley vs LHP showed a severe split weakness but only received about `Hand -0.5`.

Old behavior:
- `HAND_MAX_POINTS = 2.5`
- sample shrink made severe weak-side splits too small
- Luke Raley stayed around rank `65`

New behavior:
- Hand uses OPS gap:
  - `(split_ops_vs_pitcher_hand - overall_ops) * scale * confidence`
- Current starting settings:
  - scale roughly `25`
  - max around `12`
  - confidence based on split AB
- Luke Raley proof:
  - old `Hand -0.5`
  - new `Hand -7.65`
  - rank moved from about `65` to about `58`

Important interpretation:
- No separate Platoon modifier was added.
- Start% remains separate.
- Hand is matchup quality, not start probability.
- A confirmed starter still keeps the Hand penalty/bonus.

### Other scoring notes

- DTD status applies a mild status-risk penalty.
- `IL*` and `NA` override to unavailable.
- `NO_GAME_TODAY` and `POSTPONED` override to unavailable.
- A `-30.0` lineup modifier is expected when a posted lineup omits the player.
- `POSTED_BUT_NOT_FOUND` is expected when the team lineup is posted and the player is absent.
- Postponed games display the reason when available, use `LINEUP_NOT_APPLICABLE`, and rank unavailable.

---

## Batter Free Agents

Status:
- Wired and usable.

Source rule:
- Yahoo `status=FA`
- `sort=OR`
- pagination `start += 25`
- `count=25`
- `;out=percent_owned`

Do not use:
- broad DB anti-roster joins as FA truth
- Yahoo `status=A`, because it includes waiver players
- `/out=percent_owned`, because working syntax is `;out=percent_owned`

Filtering:
- include only Yahoo `status=FA`
- exclude waivers by source
- exclude pitchers for Batter FA tab
- exclude IL / NA / SUSP / unavailable candidates
- DTD remains eligible but receives a risk penalty
- require true active/addable FA pool

The Batter Free Agents tab now supports:
- Today rank
- Tomorrow projected rank
- Day After Tomorrow projected rank
- Same table shape by Projection View radio selector
- Position filter still works

---

## Roster Policy

Roster Policy table exists and is league/team scoped.

Policy statuses used in current decision support:
- `KEEPER`
- `DROPPABLE_HIGH`
- `DROPPABLE_LOW`
- missing policies should be seeded rather than ignored

Drop action interpretation used in proofs:
- `KEEPER` -> `NEVER_DROP`
- IL/NA slots/status -> `IGNORE_ACTIVE_SLOT`
- `DROPPABLE_HIGH` -> `HIGH_BAR_ONLY`
- `DROPPABLE_LOW` -> `EVALUATE`

Recently seeded missing current roster policy rows:
- Jacob Young
- Paul Goldschmidt
- Curtis Mead
- Jose Fernandez

Policy should eventually support all RMTs, not only Usual-RMT.

---

## RMT vs YGMA model evaluation

### Status

The current model-improvement target is not an immediate scoring-weight patch.

The next evidence target is a **RMT-v2 gate-threshold audit** to determine which simple rules preserve RMT's useful spot-start signal while blocking weak overrides against reliable YGMA-style alternatives.

### Evaluation files / artifacts

Generated model-testing workbooks are evidence artifacts, not source code.

Recent generated audit workbooks from the ChatGPT analysis environment:
- `YGMA_vs_RMT_Deterministic_Audit_2026-05-16_to_06-09.xlsx`
- `RMT_Baseline_Flexible_Slot_Matched_YGMA_Audit_2026-05-16_to_06-09.xlsx`
- `RMT_Fringe_vs_Reliable_YGMA_Test_2026-05-16_to_06-09.xlsx`

Do not commit generated `.xlsx` audit workbooks unless intentionally promoted into a documented evidence/fixture folder.

### Broad deterministic audit

Coverage:
- 69 league-day workbooks.
- Dates: 2026-05-16 through 2026-06-09, excluding 2026-05-30 and 2026-05-31.
- Leagues: Usual, MLF, MiLF.
- Tabs used: Results, RMT, YGMA. LE should be ignored going forward unless explicitly reintroduced.

Broad result:
- YGMA won more league-days than RMT.
- YGMA had stronger opportunity/production volume.
- RMT had some batting-average advantage but did not beat YGMA overall.

Interpretation:
- As a one-shot morning picker, YGMA was safer and more reliable.
- Broad scoring penalized RMT for speculative spot-starts that the user later replaced after lineup information changed.

### Flexible slot-matched audit

Purpose:
- Test RMT more fairly by only evaluating RMT picks that survived into the final Results lineup.
- Allow flexible positional movement, including 1B/UTIL-style swaps and pooled OF slots.

Result:
- RMT and YGMA were roughly even by league-day after RMT survival filtering.
- RMT performed better in MLF than in Usual/MiLF.
- RMT still did not prove broad superiority over YGMA.

Interpretation:
- RMT has some real spot-start signal.
- The signal is not strong enough to justify overriding reliable players without a startability/reliability gate.

### Fringe-vs-reliable audit

Purpose:
- Test the user's hypothesis that RMT finds useful low/fringe spot-starts but may overvalue them against reliable YGMA alternatives.

Primary finding:
- RMT fringe candidates survived final lineup only about 61.8% of the time.
- When RMT candidates were confirmed in posted lineups, they were competitive with YGMA.
- When lineup status was not confirmed, RMT was much riskier.
- RMT found AVG/SB and some HR upside, but YGMA still won more runs, walks, strikeout safety, and slightly RBI overall.

Interpretation:
- The issue is not that RMT cannot find spot starts.
- The issue is that RMT allows speculative matchup candidates to override reliable players too early.

### Current model direction

Do not patch scoring weights yet.

First RMT-v2 design should be a gate/label system:
- `Start`
- `Lean Start`
- `Watch`
- `Bench`

Recommended gate concepts to audit:
- Require confirmed lineup or strong expected-start evidence before a fringe player can override a reliable player.
- Treat pre-lineup fringe picks as `Watch` rather than automatic `Start`.
- Protect elite/everyday bats unless RMT edge clears a larger threshold.
- Penalize or block lower-order projected starts if the reliable alternative is strong.
- Shrink platoon/split-driven edges when sample size or start probability is weak.
- Split strategy by league:
  - Usual-RMT: roto marginal category value plus games-cap context.
  - MLF/MiLF: weekly H2H category leverage.

### Next proof target

Build a deterministic gate-threshold audit before changing production recommendations.

Suggested audit output:
- candidate picks allowed
- candidate picks blocked
- RMT win rate when allowed
- YGMA win rate when blocked
- impact by league
- impact by category
- category impact for AVG/R/HR/RBI/SB/BB/K
- results by lineup status
- results by batting-order bucket
- results by reliability gap


## Current confirmed working state

These are considered working unless new proof contradicts them:

### Runtime / containers
- `usual-rmt`, `mlf_rmt`, and `milf_rmt` restart successfully.
- Ports `8050`, `8051`, and `8052` return HTTP `200 OK`.
- Runtime proof has been performed repeatedly after commits.

### Batters
- Starting Lineup table works.
- Slots tab works.
- Batter Free Agents tab works.
- Roster Policy tab works.
- Starting Lineup / bench appear in one combined table.
- Threshold column works.
- Row highlighting works.
- Projection View radio buttons exist on Starting Lineup and Batter Free Agents.
- Today/Tomorrow/Day After Tomorrow future views work.
- Future projected ranks display as integers.
- Future projected game status is carried into optimizer logic.
- Future projection explainer is present.
- Recent commits indicate a read-only batter recommendations tab and daily batter action plan exist; verify exact current UI behavior in `views/batters.py` before modifying.
- Today optimizer should lock started Yahoo hitter slots unless direct proof supports changing that behavior.

### Refresh
- `runtime/refresh_live.sh` runs and now refreshes projection game context.
- `runtime/refresh_all.sh` is wired to refresh projection game context.
- MLB games / probable pitchers refresh works.
- Probable pitcher handedness refresh works.
- Starting lineups refresh works.
- Usual daily cap usage refresh works.
- The patched `refresh_live.sh` produced:
  - 2026-05-23 probable games and hand file
  - 2026-05-24 probable games and hand file
  - 2026-05-25 probable games and hand file

### Cap usage
- Batter max games currently align with Yahoo UI.
- Pitcher used/remaining are useful, but projected/diff are approximate.
- P projection should not be treated as exact.

---

## Current known issues / remaining work

### 1. RMT-v2 gate-threshold audit
Status: **Next recommended evidence target.**

Goal:
- Determine which reliability/startability/elite-player gates would improve RMT decisions without overfitting.
- Preserve RMT's useful spot-start signal.
- Block speculative fringe overrides when a reliable YGMA-style alternative is safer.
- Produce a deterministic rule recommendation before patching scoring weights.

Candidate gates to test:
- confirmed lineup required for fringe-over-reliable override
- batting-order bucket threshold
- elite/everyday player protection
- reliability gap threshold using Start%, Ros%, and pre-season rank
- league-specific category leverage
- separate `Watch` vs `Start` classification

Do not change production scoring weights until this audit is complete.

### 2. Roster Experiment / Add-Drop Watchlist
Status: **Next product/UI feature after model gate proof.**

Goal:
- Compare owned drop candidates vs FA add candidates across:
  - Today
  - Tomorrow
  - Day After Tomorrow
  - 3-day total
  - net gain/loss

Initial version should be read-only decision support.

Suggested table:
- Drop
- Drop Policy
- Drop Slot
- Drop Today / Tomorrow / Day2 / Total3
- Add
- Add Eligible
- Add Today / Tomorrow / Day2 / Total3
- Net Today
- Net 3-Day
- Add lineup/game context
- Decision cue

Do not add Yahoo write actions yet.

### 3. Rest of Week / Rest of Season
Status: **Not built.**

Rest of Week requires reliable future game context for more dates.
Rest of Season requires a real ROS projection source or a clearly labeled approximation.

Do not fake these.

### 4. Pitcher workflow
Status: **V1 exists but is not mature.**

Known needs:
- better pitcher FA workflow
- probable starter context
- RP usage / next-start context
- league-specific scoring refinement
- IP cap planning
- clear separation of SP and RP decisions

### 5. Pitcher IP projection
Status: **Approximate.**

Do not overfit the current formula.
Either:
- mark P projected/diff approximate, or
- find a direct Yahoo source, or
- collect more Yahoo UI observations and build a stable model.

### 6. MLBAM disambiguation
Known weak area:
- ambiguous names such as Jose Fernandez
- some mappings may still have blank/missing team disambiguation

### 7. Recent H/AB
Recent H/AB still needs a true last-7 AVG contribution if the current feed lacks real H/AB.

### 8. Future opportunity denominator
Future opportunity logic is still optimistic because it treats team game-days as playable opportunities and does not fully discount:
- rest days
- platoons
- lineup uncertainty
- DTD reliability risk

Future improvement:
- lineup reliability weighting
- hand-specific start probability
- replacement-market statistics by slot

---

## Known-good commands

### Prove Tailscale remote access on Apollo

```bash
echo "=== TAILSCALE ==="
/usr/bin/tailscale status || true
/usr/bin/tailscale ip -4 || true

echo
echo "=== RMT HTTP OVER TAILSCALE IP FROM APOLLO ==="
for port in 8050 8051 8052; do
  echo "PORT=$port"
  curl -I --max-time 10 "http://100.93.229.49:${port}" | sed -n '1,6p'
done
```

From laptop on phone hotspot:

```powershell
tailscale status
ping 100.93.229.49

# Browser checks:
# http://100.93.229.49:8050
# http://100.93.229.49:8051
# http://100.93.229.49:8052

# Optional SMB check:
# \\100.93.229.49\Bots
```

Caution:
- SSH on port `22` is not proven working.
- Manual `tailscaled` startup is not reboot-persistent until proven.


### Restart all RMT containers

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

### Compile active app code in all RMT containers

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

for C in usual-rmt mlf_rmt milf_rmt; do
  docker exec -i -w /app "$C" python -m py_compile \
    views/batters.py \
    services/batter_multiday.py \
    services/scoring.py \
    scripts/refresh_projection_game_context.py
done
```

### Prove projection game context exists for Today/Tomorrow/Day2

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

docker exec -i -w /app usual-rmt python - <<'PY'
from datetime import date, timedelta
from pathlib import Path

from services.db import get_connection
from services.queries import get_default_context

ctx = get_default_context()
base = date.fromisoformat(ctx["as_of_date"])
dates = [(base + timedelta(days=i)).isoformat() for i in range(3)]

print("CTX", ctx)
print("Date|ProbableGames|HandFileExists|HandFileSize")

with get_connection() as conn:
    with conn.cursor() as cur:
        for d in dates:
            cur.execute(
                """
                SELECT count(*)
                FROM lineup_tool.mlb_probable_pitcher_daily
                WHERE as_of_date = %s
                """,
                (d,),
            )
            games = cur.fetchone()[0]
            hand_file = Path(f"/app/data/derived/opposing_probable_pitchers_with_hand_{d}.csv")
            print(
                f"{d}|{games}|{hand_file.exists()}|"
                f"{hand_file.stat().st_size if hand_file.exists() else 0}"
            )
PY
```

### Refresh projection game context manually, if needed

Use this only as a proof/repair step. The refresh pipelines should normally do this automatically.

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

docker exec -i -w /app usual-rmt bash -lc '
set -euo pipefail
cd /app
PYTHONPATH=/app python scripts/refresh_projection_game_context.py --days 3
'
```

### Inspect Batter rows

```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from collections import Counter
from services.queries import get_default_context, fetch_batter_roster_rows

ctx = get_default_context()
rows = fetch_batter_roster_rows(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])

print("CTX", ctx)
print("ROWS", len(rows))
print("LINEUP_STATUS_COUNTS", dict(Counter(r.get("lineup_status", "") for r in rows)))

for r in rows[:25]:
    print(
        r.get("player_display", ""),
        "|", r.get("current_slot", ""),
        "| Rank", r.get("ranking", ""),
        "|", r.get("game_display", ""),
        "|", r.get("lineup_status", ""),
        "|", r.get("note_short", "")
    )
PY
```

### Inspect Batter Free Agent rows

```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from collections import Counter
from services.queries import get_default_context, fetch_available_batter_rows

ctx = get_default_context()
rows = fetch_available_batter_rows(ctx["league_key"], ctx["team_key"], ctx["as_of_date"])

print("CTX", ctx)
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
```

### Inspect Usual cap usage summary

```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from services.db import get_connection
from services.queries import get_default_context

ctx = get_default_context()
print("CTX", ctx)

with get_connection() as conn:
    with conn.cursor() as cur:
        print("Date|Slot|Used|Source|LoadedAtUTC")
        cur.execute(
            """
            SELECT usage_date, slot_family, used_value, source, loaded_at_utc
            FROM rmt.usual_daily_cap_usage
            WHERE league_key = %s
              AND team_key = %s
            ORDER BY usage_date DESC, slot_family
            LIMIT 40
            """,
            (ctx["league_key"], ctx["team_key"]),
        )
        for row in cur.fetchall():
            print("|".join(str(x) for x in row))
PY
```

### Prove P seed and daily rows

```bash
docker exec -i -w /app usual-rmt python - <<'PY'
from services.db import get_connection
from services.queries import get_default_context

ctx = get_default_context()
print("CTX", ctx)

with get_connection() as conn:
    with conn.cursor() as cur:
        print()
        print("=== P seed ===")
        cur.execute(
            """
            SELECT season_year, slot_family, seed_used, max_allowed, seed_as_of_date, source, loaded_at_utc
            FROM rmt.usual_cap_usage_seed
            WHERE league_key = %s
              AND team_key = %s
              AND slot_family = 'P'
            ORDER BY season_year
            """,
            (ctx["league_key"], ctx["team_key"]),
        )
        for row in cur.fetchall():
            print("|".join(str(x) for x in row))

        print()
        print("=== P daily rows from seed forward ===")
        cur.execute(
            """
            SELECT usage_date, used_value, source, loaded_at_utc
            FROM rmt.usual_daily_cap_usage
            WHERE league_key = %s
              AND team_key = %s
              AND slot_family = 'P'
              AND usage_date >= DATE '2026-05-17'
            ORDER BY usage_date
            """,
            (ctx["league_key"], ctx["team_key"]),
        )
        for row in cur.fetchall():
            print("|".join(str(x) for x in row))
PY
```

### Sanitized `.env` newline check

Do not print secrets.

```bash
cd /Volume1/Bots/fantasy/mlf_roster_manager || exit 1

python3 - <<'PY'
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

---

## How to investigate without guessing

### Model evaluation / RMT-vs-YGMA audits
Check:
1. generated audit workbooks from the model-testing analysis
2. raw daily workbooks with `Results`, `RMT`, and `YGMA` tabs
3. whether comparisons are broad, slot-matched, or RMT-survival filtered
4. lineup status bucket
5. reliability bucket
6. batting-order bucket
7. league-specific category objective

Rules:
- Ignore LE unless explicitly reintroduced.
- Do not treat a backtest-only improvement as production-ready.
- Prefer simple rules that generalize across leagues and categories.
- Do not patch scoring weights until a gate-threshold audit shows which rule class is supported.


### UI behavior
Check:
1. `views/batters.py`
2. `views/pitchers.py`
3. live browser behavior
4. app logs

### Row assembly
Check:
1. `services/queries.py`
2. DB tables used by the row query
3. generated CSVs under `data/derived/`
4. live row proof from the engine

### Scoring
Check:
1. `services/scoring.py`
2. sample rows and rank reasons
3. specific player proof, not broad assumptions

### Batter multi-day projections
Check:
1. `services/batter_multiday.py`
2. `scripts/refresh_projection_game_context.py`
3. `lineup_tool.mlb_probable_pitcher_daily` rows for Today/Tomorrow/Day2
4. `opposing_probable_pitchers_with_hand_<date>.csv` files
5. `views/batters.py::_project_batter_row(...)`

### Threshold / max-games behavior
Check:
1. `rmt.usual_cap_usage_seed`
2. `rmt.usual_daily_cap_usage`
3. `scripts/yahoo/refresh_usual_daily_cap_usage.py`
4. `views/batters.py::_usual_cap_projection_values(...)`
5. Yahoo UI values supplied by user

### Batter Free Agent availability
Check:
1. Yahoo FA source logic
2. `true_free_agent_batters_<date>.csv`
3. `services/queries.py::fetch_available_batter_rows(...)`
4. final app rows

Current Yahoo source rule:
- `status=FA`
- `sort=OR`
- `count=25`
- `start += 25`
- `;out=percent_owned`

### Lineup confirmation
Decision ladder:
- missing lineup files -> `LINEUP_DATA_MISSING`
- no MLB game or postponed MLB game -> `LINEUP_NOT_APPLICABLE`
- player found in posted lineup -> `IN_POSTED_LINEUP`
- team lineup posted but player absent -> `POSTED_BUT_NOT_FOUND`
- team lineup not posted / not proven -> `LINEUP_NOT_CONFIRMED`

### Broken script imports
Use:
- `PYTHONPATH=/app`

### Data freshness
Check:
- `runtime/refresh_live.sh`
- `runtime/refresh_all.sh`
- runtime logs
- `runtime/status/*.json`
- newest rows/files under `data/derived/`

---

## Bootstrap prompt for a new chat

Use this when starting a fresh chat:

> Read this handoff as the source of truth for the Roster Manager project.
> Follow these rules: No Zombie Code, small micro-step instructions, deterministic proof-first, concise responses, exact commands only, user runs commands and pastes outputs, do not guess.
> Use NAS SSH for Docker/runtime proof and Windows PowerShell for Git because Git is not installed on the NAS shell.
> Current RMT containers are `usual-rmt` on 8050, `mlf_rmt` on 8051, and `milf_rmt` on 8052.
> Active batter logic is in `views/batters.py`; multi-day batter projections are in `services/batter_multiday.py`; projection context refresh is in `scripts/refresh_projection_game_context.py`.
> Date resolution uses New York today by default when `DEFAULT_AS_OF_DATE=` is blank.
> Batter Free Agents is wired and source-corrected. FA discovery uses Yahoo `status=FA;sort=OR;count=25;start+=25;out=percent_owned`.
> Starting Lineup and Batter Free Agents both support Projection View radio buttons for Today, Tomorrow, and Day After Tomorrow.
> Future batter ranks are RMT projections using future game context, probable pitchers, handedness, splits, recent form, and current true FA pool; lineups are not confirmed.
> Usual batter max-games projection matches Yahoo-style behavior; Usual pitcher IP projected/diff is approximate unless proven otherwise.
> Current model-testing direction: RMT has useful spot-start signal, but it should not override reliable/YGMA-style players without startability/reliability gates.
> Next recommended evidence target is the RMT-v2 gate-threshold audit. Next product/UI feature after gate proof is Roster Experiment / Add-Drop Watchlist.
> Tailscale remote access was manually started on Apollo; Apollo Tailnet IP is 100.93.229.49. Do not assume reboot persistence or SSH port 22 until proven.
> When investigating, prefer current files, DB, logs, CSV outputs, and live row proof over theorizing.
