BEGIN;

-- ============================================================
-- RMT Shadow Stability Evaluation Schema — v1
--
-- Purpose:
--   Persist prospective stability-model evidence without
--   altering the validated three-source Evaluation contract.
--
-- Production tables intentionally untouched:
--   rmt.eval_lineup_snapshot
--   rmt.eval_recommendation_snapshot
--   rmt.eval_hitter_scorecard
-- ============================================================


-- ------------------------------------------------------------
-- 1. One record per eval run / shadow-model version
-- ------------------------------------------------------------

CREATE TABLE rmt.eval_shadow_model_run (
    eval_run_id bigint NOT NULL,
    shadow_model_key text NOT NULL,
    shadow_model_version integer NOT NULL,
    baseline_source text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    parameters_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    original_rmt_change_count integer NULL,
    shadow_change_count integer NULL,

    original_objective numeric NULL,
    shadow_objective_before_penalty numeric NULL,
    shadow_penalty numeric NULL,
    shadow_objective_after_penalty numeric NULL,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT eval_shadow_model_run_pkey
        PRIMARY KEY (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        ),

    CONSTRAINT eval_shadow_model_run_eval_run_id_fkey
        FOREIGN KEY (eval_run_id)
        REFERENCES rmt.eval_run(eval_run_id)
        ON DELETE CASCADE,

    CONSTRAINT eval_shadow_model_run_key_nonempty_chk
        CHECK (btrim(shadow_model_key) <> ''),

    CONSTRAINT eval_shadow_model_run_version_chk
        CHECK (shadow_model_version > 0),

    CONSTRAINT eval_shadow_model_run_original_changes_chk
        CHECK (
            original_rmt_change_count IS NULL
            OR original_rmt_change_count >= 0
        ),

    CONSTRAINT eval_shadow_model_run_shadow_changes_chk
        CHECK (
            shadow_change_count IS NULL
            OR shadow_change_count >= 0
        ),

    CONSTRAINT eval_shadow_model_run_penalty_chk
        CHECK (
            shadow_penalty IS NULL
            OR shadow_penalty >= 0
        )
);

CREATE INDEX idx_eval_shadow_model_run_eval
    ON rmt.eval_shadow_model_run (
        eval_run_id,
        shadow_model_key,
        shadow_model_version
    );


-- ------------------------------------------------------------
-- 2. Full contemporaneous active-owned hitter universe
-- ------------------------------------------------------------

CREATE TABLE rmt.eval_shadow_player_snapshot (
    eval_run_id bigint NOT NULL,
    shadow_model_key text NOT NULL,
    shadow_model_version integer NOT NULL,
    row_ordinal integer NOT NULL,

    yahoo_player_key text NULL,
    player_name text NULL,
    mlb_team_abbr text NULL,

    ygma_is_starting boolean NOT NULL DEFAULT false,
    ygma_selected_position text NULL,

    rmt_is_starting boolean NOT NULL DEFAULT false,
    rmt_selected_position text NULL,

    shadow_is_starting boolean NOT NULL DEFAULT false,
    shadow_selected_position text NULL,

    eligible_positions text[] NOT NULL DEFAULT '{}'::text[],

    ranking numeric NULL,
    ranking_band text NULL,

    baseline_points numeric NULL,
    pitcher_points numeric NULL,
    handedness_points numeric NULL,
    home_away_points numeric NULL,
    day_night_points numeric NULL,
    recent_form_points numeric NULL,
    rank_reliability_points numeric NULL,
    status_risk_points numeric NULL,
    lineup_points numeric NULL,

    game_status text NULL,
    lineup_status text NULL,
    status_display text NULL,
    game_started boolean NULL,
    is_keeper boolean NULL,
    is_undroppable boolean NULL,
    policy_status text NULL,

    is_ygma_incumbent boolean NOT NULL DEFAULT false,
    shadow_displacement_number integer NULL,
    shadow_switch_penalty numeric NULL,
    slot_objective_before_penalty numeric NULL,
    slot_objective_after_penalty numeric NULL,

    row_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT eval_shadow_player_snapshot_pkey
        PRIMARY KEY (
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            row_ordinal
        ),

    CONSTRAINT eval_shadow_player_snapshot_model_fkey
        FOREIGN KEY (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        )
        REFERENCES rmt.eval_shadow_model_run (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        )
        ON DELETE CASCADE,

    CONSTRAINT eval_shadow_player_snapshot_ordinal_chk
        CHECK (row_ordinal > 0),

    CONSTRAINT eval_shadow_player_snapshot_displacement_chk
        CHECK (
            shadow_displacement_number IS NULL
            OR shadow_displacement_number >= 0
        ),

    CONSTRAINT eval_shadow_player_snapshot_penalty_chk
        CHECK (
            shadow_switch_penalty IS NULL
            OR shadow_switch_penalty >= 0
        )
);

CREATE INDEX idx_eval_shadow_player_lookup
    ON rmt.eval_shadow_player_snapshot (
        yahoo_player_key,
        player_name
    );

CREATE INDEX idx_eval_shadow_player_model
    ON rmt.eval_shadow_player_snapshot (
        eval_run_id,
        shadow_model_key,
        shadow_model_version
    );

CREATE UNIQUE INDEX uq_eval_shadow_player_key
    ON rmt.eval_shadow_player_snapshot (
        eval_run_id,
        shadow_model_key,
        shadow_model_version,
        yahoo_player_key
    )
    WHERE yahoo_player_key IS NOT NULL;


-- ------------------------------------------------------------
-- 3. Shadow-only realized hitter outcomes
-- ------------------------------------------------------------

CREATE TABLE rmt.eval_shadow_hitter_scorecard (
    eval_run_id bigint NOT NULL,
    shadow_model_key text NOT NULL,
    shadow_model_version integer NOT NULL,
    stat_date date NOT NULL,

    is_final boolean NOT NULL DEFAULT false,

    starting_hitter_rows integer NOT NULL DEFAULT 0,
    distinct_hitter_keys integer NOT NULL DEFAULT 0,
    actual_rows integer NOT NULL DEFAULT 0,
    missing_actual_rows integer NOT NULL DEFAULT 0,

    total_hits integer NOT NULL DEFAULT 0,
    total_ab integer NOT NULL DEFAULT 0,
    total_r integer NOT NULL DEFAULT 0,
    total_hr integer NOT NULL DEFAULT 0,
    total_rbi integer NOT NULL DEFAULT 0,
    total_sb integer NOT NULL DEFAULT 0,
    total_bb integer NOT NULL DEFAULT 0,
    total_k integer NOT NULL DEFAULT 0,

    batting_avg numeric NULL,

    score_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT eval_shadow_hitter_scorecard_pkey
        PRIMARY KEY (
            eval_run_id,
            shadow_model_key,
            shadow_model_version,
            stat_date
        ),

    CONSTRAINT eval_shadow_hitter_scorecard_model_fkey
        FOREIGN KEY (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        )
        REFERENCES rmt.eval_shadow_model_run (
            eval_run_id,
            shadow_model_key,
            shadow_model_version
        )
        ON DELETE CASCADE,

    CONSTRAINT eval_shadow_hitter_scorecard_starters_chk
        CHECK (starting_hitter_rows >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_distinct_chk
        CHECK (distinct_hitter_keys >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_actual_chk
        CHECK (actual_rows >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_missing_chk
        CHECK (missing_actual_rows >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_hits_chk
        CHECK (total_hits >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_ab_chk
        CHECK (total_ab >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_r_chk
        CHECK (total_r >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_hr_chk
        CHECK (total_hr >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_rbi_chk
        CHECK (total_rbi >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_sb_chk
        CHECK (total_sb >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_bb_chk
        CHECK (total_bb >= 0),

    CONSTRAINT eval_shadow_hitter_scorecard_k_chk
        CHECK (total_k >= 0)
);

CREATE INDEX idx_eval_shadow_hitter_scorecard_stat_date
    ON rmt.eval_shadow_hitter_scorecard (
        stat_date
    );

CREATE INDEX idx_eval_shadow_hitter_scorecard_model
    ON rmt.eval_shadow_hitter_scorecard (
        shadow_model_key,
        shadow_model_version,
        stat_date
    );

COMMIT;
