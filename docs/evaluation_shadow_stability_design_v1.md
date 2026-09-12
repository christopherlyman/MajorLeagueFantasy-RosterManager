# RMT Shadow Stability Model — Evaluation Design v1

**Status:** Design approved for implementation planning
**Date:** 2026-09-11
**Scope:** Batter start/sit optimizer only
**Initial leagues:** MLF and MiLF
**Production behavior change:** None in v1

---

## 1. Problem statement

Historical Evaluation analysis found a strong relationship between the number
of hitter lineup changes made by RMT relative to the pre-RMT Yahoo/YGMA
lineup and realized performance.

Within the frozen historical H2H cohort:

- 1–2 change days: 5-3-2
- 3+ change days: 3-9-1
- 3–4 change days: 3-7-1
- 5+ change days: 0-2-0

A coarse counterfactual that accepted RMT with at most two hitter changes and
otherwise reverted to YGMA improved:

- MLF full-span category net from -1 to 0
- MiLF full-span category net from -4 to 0
- MLF complete Week 22 category net from -3 to -2

This does NOT establish that two changes is the correct production limit.
It establishes that lineup stability has enough potential value to justify
testing a softer stability-aware objective.

---

## 2. Mechanism identified in production optimizer

`optimize_lineup()` jointly optimizes the complete hitter lineup.

For each slot, the current objective contribution is:

    player ranking
    + slot assignment bonus
    + Usual cap-pace priority bonus

The dynamic-programming state is effectively:

    slot position
    + players already used

There is currently no optimizer state or objective term for:

- whether a player was already starting in the YGMA/pre-RMT lineup;
- how many YGMA starters have been displaced;
- the first versus third versus fifth lineup change;
- cumulative lineup instability.

Therefore several individually favorable substitutions can collectively
produce a much larger lineup overhaul without any additional hurdle.

---

## 3. Historical replay limitation

An exact retrospective soft-penalty replay is not defensible from the current
frozen Evaluation dataset.

For the 13 aggressive H2H league-days:

- RMT-promoted hitter rank coverage: 50/50 = 100%
- displaced YGMA hitter rank coverage: 32/50 = 64%
- fully rank-covered aggressive days: 2/13 = 15.4%

Two possible historical recovery tables were inspected:

- `rmt.hitter_rank_snapshot`
- `lineup_tool.league_player_pool_snapshot`

Both tables currently contain zero rows.

Missing historical ranks must NOT be inferred from current Yahoo/player-pool
state or reconstructed from later information.

---

## 4. Why the shadow model must NOT use eval_lineup_snapshot

The production Evaluation subsystem currently has three semantic tracks:

1. `YGMA_PRE_RMT`
2. `RMT_RECOMMENDED_BASELINE`
3. `USER_FINAL_LOCKED`

Although `rmt.eval_lineup_snapshot.snapshot_source` has no CHECK constraint,
adding `SHADOW_STABILITY` there is unsafe.

The actual-scorecard loader reads all starting hitter rows from
`eval_lineup_snapshot` without restricting the source list.

The scorecard builder then groups by:

    (eval_run_id, snapshot_source)

and writes an `rmt.eval_hitter_scorecard` for every source encountered.

Therefore inserting a shadow source into `eval_lineup_snapshot` would
automatically create a fourth ordinary Evaluation scorecard.

Existing Evaluation result logic contains explicit three-track labels,
ordering, and exact-source completeness assumptions.

The validated three-track production Evaluation contract must remain intact.

---

## 5. Architecture decision

The stability experiment will use dedicated shadow persistence linked to the
existing production `eval_run_id`.

The shadow subsystem must not alter:

- `rmt.eval_lineup_snapshot`
- `rmt.eval_recommendation_snapshot`
- `rmt.eval_hitter_scorecard`
- authoritative-run selection
- historical-final reconstruction
- current RMT recommendations
- Daily Action Plan behavior
- Yahoo roster state

The existing `eval_run_id` is the common lineage key.

---

## 6. Proposed tables

### 6.1 rmt.eval_shadow_model_run

One row per Evaluation run and shadow-model version.

Proposed fields:

- `eval_run_id bigint NOT NULL`
- `shadow_model_key text NOT NULL`
- `shadow_model_version integer NOT NULL`
- `baseline_source text NOT NULL`
- `enabled boolean NOT NULL`
- `parameters_json jsonb NOT NULL`
- `original_rmt_change_count integer NULL`
- `shadow_change_count integer NULL`
- `original_objective numeric NULL`
- `shadow_objective_before_penalty numeric NULL`
- `shadow_penalty numeric NULL`
- `shadow_objective_after_penalty numeric NULL`
- `created_at_utc timestamptz NOT NULL`
- `updated_at_utc timestamptz NOT NULL`

Primary key:

    (eval_run_id, shadow_model_key, shadow_model_version)

Foreign key:

    eval_run_id -> rmt.eval_run(eval_run_id) ON DELETE CASCADE

`parameters_json` makes each prospective result reproducible.

---

### 6.2 rmt.eval_shadow_player_snapshot

Persist the COMPLETE active-owned hitter universe seen by the optimizer, not
only the players selected into the shadow lineup.

This is required so future stability parameters can be replayed against the
same contemporaneous inputs without using current data.

Proposed identity/lineage fields:

- `eval_run_id`
- `shadow_model_key`
- `shadow_model_version`
- `row_ordinal`
- `yahoo_player_key`
- `player_name`
- `mlb_team_abbr`

Proposed historical lineup-state fields:

- `ygma_is_starting`
- `ygma_selected_position`
- `rmt_is_starting`
- `rmt_selected_position`
- `shadow_is_starting`
- `shadow_selected_position`
- `eligible_positions`

Proposed scoring fields:

- `ranking`
- `ranking_band`
- `baseline_points`
- `pitcher_points`
- `handedness_points`
- `home_away_points`
- `day_night_points`
- `recent_form_points`
- `rank_reliability_points`
- `status_risk_points`
- `lineup_points`

Proposed availability/context fields where available:

- `game_status`
- `lineup_status`
- `status_display`
- `game_started`
- `is_keeper`
- `is_undroppable`
- `policy_status`

Proposed model-decision fields:

- `is_ygma_incumbent`
- `shadow_displacement_number`
- `shadow_switch_penalty`
- `slot_objective_before_penalty`
- `slot_objective_after_penalty`

Also persist:

- `row_json jsonb NOT NULL`

`row_json` should contain the complete contemporaneous optimizer row so a
future model audit is not limited to fields anticipated in v1.

Primary key should identify a unique player row within one shadow-model run.

---

### 6.3 rmt.eval_shadow_hitter_scorecard

Shadow actual outcomes must be isolated from the validated production
`rmt.eval_hitter_scorecard`.

Proposed fields mirror the existing hitter scorecard:

- `eval_run_id`
- `shadow_model_key`
- `shadow_model_version`
- `stat_date`
- `is_final`
- `starting_hitter_rows`
- `distinct_hitter_keys`
- `actual_rows`
- `missing_actual_rows`
- `total_hits`
- `total_ab`
- `total_r`
- `total_hr`
- `total_rbi`
- `total_sb`
- `total_bb`
- `total_k`
- `batting_avg`
- `score_json`
- `created_at_utc`
- `updated_at_utc`

Primary key:

    (eval_run_id, shadow_model_key, shadow_model_version, stat_date)

This table may reuse the validated Yahoo daily-stat cache for actual outcomes
while remaining logically separate from production Evaluation scorecards.

---

## 7. Role of eval_run.context_json

`eval_run.context_json` may contain compact shadow metadata such as:

- shadow model enabled/disabled
- model key
- model version
- parameter-set identifier
- successful/failed shadow computation
- persisted row counts

It must NOT be the primary store for:

- full active-roster player rows
- per-player rankings
- ranking components
- per-player stability penalties
- shadow lineup membership

Those require normalized/queryable shadow persistence.

---

## 8. Initial shadow policy

The first shadow model will be experimental only.

It will use the pre-RMT YGMA starting hitter set as the incumbent baseline.

The optimizer will remain a joint lineup optimizer, but its DP objective will
incorporate a stability cost when the selected lineup displaces YGMA starters.

The experiment must support a progressive cost rather than a hard maximum
number of changes.

Conceptually:

    adjusted objective
      = normal optimizer objective
      - stability penalty(change_count)

The penalty function must be parameterized and persisted.

Candidate shapes may include:

- no/very small cost for the first displacement;
- modest additional hurdle for the second;
- increasing marginal hurdle for the third and later displacements.

No specific penalty values are approved by this document.

They must be evaluated prospectively.

---

## 9. Required optimizer design property

A truly progressive stability penalty cannot be implemented correctly by
simply adding one fixed bonus to every YGMA incumbent.

The optimizer must know how many incumbent starters have already been
displaced by the candidate assignment.

Therefore the shadow optimizer's dynamic-programming state must include a
change/displacement count or an equivalent state representation.

Conceptually:

    solve(slot_pos, used_mask, displacement_count)

The exact implementation must preserve:

- positional legality;
- manual locks;
- started-game locks;
- one-player-per-lineup-slot uniqueness;
- existing ranking behavior;
- deterministic tie behavior.

---

## 10. Initial league scope

### MLF and MiLF

Enabled for prospective shadow evaluation.

These leagues provide the cleanest test because their start/sit behavior does
not have Usual's conserved seasonal start-cap opportunity cost.

### Usual Suspects

Do NOT enable the v1 stability shadow model yet.

Usual uses slot cap pacing and can intentionally leave starts unused.
A future cap-aware stability model must distinguish:

- beneficial lineup stability;
- strategic conservation of limited starts;
- positional cap pace.

The existing cap optimizer behavior must not be weakened accidentally.

---

## 11. Shadow-mode safety contract

The v1 implementation must satisfy all of the following:

- no change to live RMT lineup output;
- no change to Daily Action Plan recommendations;
- no change to transaction recommendations;
- no additional Yahoo writes;
- no automatic lineup changes;
- no changes to production Evaluation source count;
- no changes to production hitter scorecards;
- no changes to historical authoritative-run logic;
- shadow computation failure must not fail normal Refresh;
- shadow persistence failure must be visible diagnostically but must not
  corrupt the normal Evaluation run.

---

## 12. Prospective evaluation plan

For every eligible Recommendations Refresh / Daily Refresh:

1. persist normal YGMA baseline;
2. persist normal RMT baseline;
3. compute shadow stability lineup from the same contemporaneous active-owned
   hitter universe;
4. persist full active-roster scoring evidence;
5. persist shadow assignment and model parameters;
6. leave the user-facing RMT recommendation unchanged.

After the stat date is final:

7. score the shadow lineup using the same validated Yahoo hitter actual cache;
8. persist the result only in `rmt.eval_shadow_hitter_scorecard`;
9. compare YGMA, original RMT, User Final, and Shadow in dedicated experimental
   analysis;
10. do not alter the existing three-track production Evaluation aggregates.

---

## 13. Evidence required before production promotion

The stability model must remain shadow-only until there is enough prospective
evidence to evaluate:

- category-level performance versus YGMA;
- performance versus original RMT;
- complete H2H-week performance;
- change-count distribution;
- performance by one/two/three/four-plus changes;
- how often User Final agrees with Shadow versus original RMT;
- whether gains persist outside the historical discovery cohort.

A hard two-change cap is NOT the production target.

The desired end state is a calibrated stability-aware optimizer that still
permits several changes when the modeled advantage is sufficiently strong.

---

## 14. Implementation sequence

1. Add isolated shadow persistence schema.
2. Add shadow optimizer function without changing `optimize_lineup()`.
3. Add full active-roster snapshot persistence.
4. Invoke shadow computation after the normal RMT baseline is built.
5. Verify all three live instances still produce identical production output.
6. Add isolated shadow actual-scorecard finalization.
7. Add read-only shadow Evaluation diagnostics.
8. Accumulate prospective sample.
9. Tune penalty parameters only from persisted contemporaneous evidence.
10. Consider production promotion only after prospective validation.

---

## 15. Non-goals for v1

v1 will NOT:

- change current RMT rankings;
- change scoring component weights;
- implement a keeper/star hard lock;
- implement a hard two-change cap;
- modify transaction add/drop logic;
- change Usual cap behavior;
- use current-state data to backfill prior shadow results;
- retroactively manufacture missing historical rankings.

---

## 16. Implementation addendum — integration bridge

### 16.1 Integration audit result

The live Daily Action Plan builder already has the contemporaneous data needed
for prospective shadow evaluation:

- complete `active_owned` hitter rows;
- manual and started-game locks;
- normal RMT `baseline_assignment`;
- active league `SLOT_ORDER`;
- production `startable_for_slot()` behavior;
- production `slot_optimizer_value()` behavior;
- ranking and component fields carried on the active-owned rows.

The shadow optimizer therefore must consume this exact in-memory universe.
It must not reconstruct the player universe later from mutable current state.

### 16.2 Do not change the Daily Action Plan tuple contract

`build_batter_daily_action_plan_preview()` currently returns exactly:

    (
        top_action,
        action_rows,
        baseline_rows,
        summary,
    )

This four-item tuple is consumed by:

- `services.evaluation.record_batter_recommendation_eval()`;
- `_daily_action_filter_cached_plan_for_current_state()`;
- the Streamlit cached Daily Action Plan workflow.

The tuple shape is therefore an established interface.

The shadow implementation must NOT add a fifth tuple item.

Instead, the builder may optionally populate a separate local sidecar object
provided by its immediate caller.

Conceptually:

    shadow_input = {}

    plan = build_batter_daily_action_plan_preview(
        ctx_obj,
        shadow_input_out=shadow_input,
    )

The normal four-item `plan` remains unchanged and continues to be stored in
Streamlit session state exactly as today.

The sidecar is ephemeral and is not itself stored in session state.

### 16.3 Required sidecar contents

The integration sidecar will contain only the contemporaneous inputs required
to reproduce the optimizer decision:

- full `active_owned` rows;
- combined manual + automatic locks;
- normal `baseline_assignment`;
- current `SLOT_ORDER`.

It must be built from the same objects used for the normal RMT baseline.

No second roster fetch is permitted for these inputs.

### 16.4 eval_run_id and YGMA incumbent source

`eval_run_id` becomes available only inside the normal Evaluation writer.

The existing normal flow is:

1. create `rmt.eval_run`;
2. capture `YGMA_PRE_RMT`;
3. persist `RMT_RECOMMENDED_BASELINE`;
4. persist recommendation rows;
5. commit;
6. return `eval_run_id` to the Streamlit caller.

The shadow path will run only AFTER this normal Evaluation save succeeds.

The shadow helper will derive YGMA incumbents from the newly persisted:

    rmt.eval_lineup_snapshot
    snapshot_source = 'YGMA_PRE_RMT'
    eval_run_id = <returned eval_run_id>

This is the frozen pre-RMT roster state already captured by the validated
Evaluation workflow.

The shadow path must NOT:

- perform another Yahoo roster fetch;
- use the mutable current player pool as the YGMA baseline;
- reconstruct YGMA from later state;
- change `_capture_ygma_pre_rmt_lineup()` semantics.

### 16.5 Player identity reconciliation

YGMA starter membership will be joined to `active_owned` by
`yahoo_player_key`.

After that join, the optimizer incumbent identity will use the same
`player_key_fn` as the normal optimizer.

This avoids relying on display-name equality when Yahoo player keys are
available.

If a frozen YGMA starter is not present in the contemporaneous active-owned
universe, it is recorded diagnostically but is not treated as an avoidable
optimizer displacement.

### 16.6 First prospective mode: capture-only zero penalty

The first integrated shadow mode will NOT choose or tune a stability penalty.

It will use:

    shadow_model_key = 'stability_capture_zero_penalty'
    shadow_model_version = 1
    enabled = false

The cumulative displacement penalty is explicitly zero for every possible
displacement count.

Purpose:

- prove end-to-end prospective persistence;
- persist the complete active-owned scoring universe;
- persist YGMA/RMT lineup membership;
- create the frozen evidence missing from the historical dataset;
- make later penalty-curve replay possible without current-state leakage.

Because the stability penalty is zero, the shadow assignment MUST exactly
match the normal RMT baseline assignment slot-for-slot.

This parity is a hard persistence gate.

If zero-penalty Shadow differs from RMT:

- do not persist the shadow observation;
- surface a diagnostic warning;
- leave the normal Evaluation run intact;
- leave the user-facing RMT recommendation unchanged.

### 16.7 Capture-only persisted values

For every eligible prospective capture, persist:

Run-level evidence:

- model key/version;
- capture-only mode;
- zero-penalty curve;
- original RMT change count versus YGMA;
- zero-penalty shadow change count;
- original optimizer objective;
- shadow objective before penalty;
- zero shadow penalty;
- shadow objective after penalty.

Player-level evidence for the COMPLETE active-owned universe:

- Yahoo player identity;
- eligible positions;
- complete raw contemporaneous optimizer row;
- normal ranking;
- all available ranking components;
- YGMA starting membership and selected position;
- RMT starting membership and selected slot;
- zero-penalty Shadow starting membership and selected slot;
- locks/context already present on the raw player row where applicable.

This is prospective evidence collection, not a production recommendation.

### 16.8 League scope

Capture-only integration is enabled only for:

- MLF — `469.l.41640`
- MiLF — `469.l.60688`

It remains disabled for:

- Usual Suspects — `469.l.22528`

Usual continues to require a separate cap-aware stability design because
unused starts can have future strategic value.

### 16.9 Failure isolation

The production Evaluation save happens first.

Shadow capture happens afterward in a separate guarded operation.

Therefore:

- normal Evaluation success must not depend on Shadow success;
- Shadow failure must not roll back a valid normal Evaluation run;
- Shadow failure must not fail Daily Refresh;
- Shadow failure must not alter recommendations;
- Shadow failure should be surfaced diagnostically.

### 16.10 No shadow scorecard in capture-only mode

`stability_capture_zero_penalty` is a data-collection model, not a candidate
stability model.

Do not create `rmt.eval_shadow_hitter_scorecard` rows for capture-only
zero-penalty observations.

Because zero-penalty Shadow is required to equal ordinary RMT, scoring it
would only duplicate the existing RMT scorecard.

Shadow scorecards begin when a non-zero candidate stability model is run
prospectively.

### 16.11 Integration implementation order

The approved implementation sequence from this point is:

1. add optional local sidecar output to the Daily Action Plan builder;
2. preserve the existing four-item plan return contract;
3. add an isolated shadow integration service;
4. read frozen YGMA incumbents using returned `eval_run_id`;
5. run zero-penalty Shadow against the exact captured `active_owned` universe;
6. require exact zero-penalty parity with the normal RMT assignment;
7. persist full player evidence only for MLF/MiLF;
8. keep Usual excluded;
9. restart and validate all three shared live containers;
10. begin prospective evidence collection on subsequent normal refreshes.

No stability penalty values are approved at this stage.
