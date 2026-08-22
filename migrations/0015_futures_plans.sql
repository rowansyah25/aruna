-- =====================================================================
-- ARUNA AI - FUTURES F5: the perpetual plan, locked
--
-- FUTURES SPEC 37-39 (signal format), 47 (immutability), 50 (ARUNA never
-- executes).
--
-- Same shape and the same reasoning as `signal_snapshots` in 0008: a plan
-- may never change after it is issued. A stop that can be edited afterwards
-- makes every risk figure derived from it unfalsifiable, and F6 scores
-- these rows against what actually happened.
--
-- What this migration adds beyond the spot lock is a pair of CHECK
-- constraints that make a HALF PLAN unrepresentable.
--
-- A refused plan carrying an entry price and a leverage reads exactly like
-- an accepted one to anybody skimming, and skimming is how these get read.
-- The application already refuses to build one; the database refuses to
-- store one. Two independent layers, because the whole point of the
-- refusal is that it is the part a reader must not miss.
-- =====================================================================


-- ---------------------------------------------------------------------
-- futures_plans  (FUTURES SPEC 37-39, 47) - APPEND-ONLY
-- ---------------------------------------------------------------------
CREATE TABLE futures_plans (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id           CHAR(16)       NOT NULL,
    -- SHA-256 over the fields that must not move after issue.
    fingerprint         CHAR(64)       NOT NULL,

    symbol              VARCHAR(64)    NOT NULL,
    side                VARCHAR(8)     NOT NULL,
    verdict             VARCHAR(16)    NOT NULL,
    horizon_hours       DECIMAL(12,4)  NOT NULL,

    -- Present only for a PLAN. The CHECKs below enforce that, in both
    -- directions.
    entry               DECIMAL(30,12) NULL,
    stop                DECIMAL(30,12) NULL,
    target              DECIMAL(30,12) NULL,
    quantity            DECIMAL(30,12) NULL,
    notional            DECIMAL(30,12) NULL,
    leverage            SMALLINT UNSIGNED NULL,
    margin_mode         VARCHAR(16)    NOT NULL DEFAULT 'ISOLATED',
    margin_required     DECIMAL(30,12) NULL,
    liquidation_price   DECIMAL(30,12) NULL,
    buffer_score        SMALLINT       NULL,
    buffer_band         VARCHAR(16)    NULL,
    net_rr              DECIMAL(14,4)  NULL,
    expected_net_pnl    DECIMAL(30,12) NULL,

    -- The workings, kept whether or not the plan cleared. A refusal that
    -- cannot be audited is a shrug.
    integrity_verdict   VARCHAR(16)    NULL,
    refusals            JSON           NOT NULL,
    caveats             JSON           NOT NULL,
    settlements         SMALLINT UNSIGNED NULL,
    funding_cost_pct    DECIMAL(14,6)  NULL,

    created_at          DATETIME(6)    NOT NULL,
    model_version       VARCHAR(32)    NOT NULL,
    council_session_id  BIGINT UNSIGNED NULL,

    PRIMARY KEY (id),
    UNIQUE KEY futures_plans_signal_id (signal_id),
    KEY futures_plans_lookup_idx (symbol, created_at),
    KEY futures_plans_verdict_idx (verdict, created_at),

    CONSTRAINT futures_plans_side_allowed CHECK (
        side IN ('LONG', 'SHORT', 'FLAT')
    ),
    CONSTRAINT futures_plans_verdict_allowed CHECK (
        verdict IN ('PLAN', 'NO_SIGNAL', 'WAIT', 'REFUSED')
    ),
    CONSTRAINT futures_plans_margin_mode_allowed CHECK (
        margin_mode IN ('ISOLATED', 'CROSS')
    ),
    CONSTRAINT futures_plans_horizon_positive CHECK (horizon_hours > 0),

    -- A plan that did not clear carries no numbers at all.
    CONSTRAINT futures_plans_refusal_is_empty CHECK (
        verdict = 'PLAN' OR (
            entry IS NULL AND stop IS NULL AND target IS NULL
            AND quantity IS NULL AND notional IS NULL
            AND leverage IS NULL AND margin_required IS NULL
            AND liquidation_price IS NULL AND net_rr IS NULL
            AND expected_net_pnl IS NULL
        )
    ),
    -- And a plan that did clear carries all of them. Half a plan is not a
    -- weaker plan, it is a different claim.
    CONSTRAINT futures_plans_plan_is_complete CHECK (
        verdict <> 'PLAN' OR (
            entry IS NOT NULL AND stop IS NOT NULL AND target IS NOT NULL
            AND quantity IS NOT NULL AND leverage IS NOT NULL
            AND margin_required IS NOT NULL
        )
    ),
    -- FUTURES SPEC 15: no leveraged position is recommended above the
    -- ladder's top rung, and 1x is not a "recommendation" this table stores.
    CONSTRAINT futures_plans_leverage_range CHECK (
        leverage IS NULL OR (leverage >= 1 AND leverage <= 125)
    ),
    -- A refusal must say why. An empty reason list is a shrug with a
    -- verdict attached.
    CONSTRAINT futures_plans_refusal_has_reasons CHECK (
        verdict = 'PLAN' OR JSON_LENGTH(refusals) > 0
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- FUTURES SPEC 47. MESSAGE_TEXT is capped at 128 characters by MySQL; over
-- that it raises 1648 instead of the message, which is how a clear error
-- becomes an obscure one.
CREATE TRIGGER futures_plans_no_update
    BEFORE UPDATE ON futures_plans
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'futures_plans is append-only: an issued plan cannot change (FUTURES SPEC 47) - issue a new one';

CREATE TRIGGER futures_plans_no_delete
    BEFORE DELETE ON futures_plans
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'futures_plans is append-only: an issued plan cannot be deleted (FUTURES SPEC 47)';


-- ---------------------------------------------------------------------
-- futures_plan_results  (FUTURES SPEC 40-45) - how each plan ended
--
-- Separate and mutable-by-append: a plan is frozen, its outcome arrives
-- later, and folding them into one table would mean either freezing the
-- outcome before it exists or unfreezing the plan.
-- ---------------------------------------------------------------------
CREATE TABLE futures_plan_results (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id           CHAR(16)       NOT NULL,
    outcome             VARCHAR(24)    NOT NULL,
    entry               DECIMAL(30,12) NOT NULL,
    exit_price          DECIMAL(30,12) NULL,
    max_adverse_pct     DECIMAL(14,4)  NULL,
    -- Reached the liquidation level at any point, whether or not the stop
    -- would have fired first. A win that touched it is not a clean win.
    touched_liquidation TINYINT(1)     NOT NULL DEFAULT 0,
    findings            JSON           NOT NULL,
    resolved_at         DATETIME(6)    NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY futures_plan_results_signal_id (signal_id),
    KEY futures_plan_results_outcome_idx (outcome, resolved_at),

    CONSTRAINT futures_plan_results_plan_fk FOREIGN KEY (signal_id)
        REFERENCES futures_plans (signal_id) ON DELETE RESTRICT,
    CONSTRAINT futures_plan_results_outcome_allowed CHECK (
        outcome IN ('TARGET_HIT', 'STOPPED_OUT', 'LIQUIDATED', 'EXPIRED', 'OPEN')
    ),
    -- A liquidation that does not record having touched the liquidation
    -- level is a contradiction, and this is the one outcome no arithmetic
    -- may quietly smooth over.
    CONSTRAINT futures_plan_results_liquidation_consistent CHECK (
        outcome <> 'LIQUIDATED' OR touched_liquidation = 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
