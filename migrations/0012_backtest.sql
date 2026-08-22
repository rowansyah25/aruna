-- =====================================================================
-- ARUNA AI - PHASE 9: backtest, walk-forward, out-of-sample, replay
--
-- SPEC 35-39.
--
-- Backtest runs are append-only. A result is a claim about how a set of
-- rules performed over a period; re-running the same period after changing
-- the rules produces a *different* claim, and overwriting the old one would
-- destroy the only evidence that the rules changed at all. PHASE 10 chooses
-- between model variants on the strength of these numbers, and that choice
-- is only auditable if every run it saw is still on the record.
--
-- `known_optimism` is stored per run rather than assumed. The list of things
-- a backtest cannot reproduce will shrink as ARUNA records more (an order
-- book feed would remove the spread caveat), and a two-year-old result must
-- keep the caveats that applied when it was produced.
-- =====================================================================


-- ---------------------------------------------------------------------
-- backtest_runs  (SPEC 35, 36)
-- ---------------------------------------------------------------------
CREATE TABLE backtest_runs (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    ran_at              DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    model_version       VARCHAR(32)   NOT NULL,
    market_code         VARCHAR(16)   NOT NULL,
    interval_code       VARCHAR(8)    NOT NULL,

    period_start        DATETIME(6)   NOT NULL,
    period_end          DATETIME(6)   NOT NULL,
    -- The reserved tail (SPEC 38). Stored so a later reader can tell whether
    -- a given run was allowed to see it.
    holdout_start       DATETIME(6)   NULL,
    holdout_included    BOOLEAN       NOT NULL DEFAULT FALSE,

    assets              INT UNSIGNED  NOT NULL DEFAULT 0,
    decisions_simulated INT UNSIGNED  NOT NULL DEFAULT 0,
    published           INT UNSIGNED  NOT NULL DEFAULT 0,
    resolved            INT UNSIGNED  NOT NULL DEFAULT 0,
    direction_correct   INT UNSIGNED  NOT NULL DEFAULT 0,
    net_pnl             DECIMAL(30,2) NOT NULL DEFAULT 0,
    gross_pnl           DECIMAL(30,2) NOT NULL DEFAULT 0,
    total_costs         DECIMAL(30,2) NOT NULL DEFAULT 0,

    walk_forward        JSON          NULL,
    per_asset           JSON          NOT NULL,
    known_optimism      JSON          NOT NULL,

    PRIMARY KEY (id),
    KEY backtest_runs_time_idx (ran_at),
    KEY backtest_runs_scope_idx (market_code, interval_code, ran_at),

    CONSTRAINT backtest_runs_period_forward CHECK (period_end >= period_start),
    CONSTRAINT backtest_runs_correct_bounded CHECK (direction_correct <= resolved)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TRIGGER backtest_runs_no_update
    BEFORE UPDATE ON backtest_runs
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'backtest_runs is append-only: a run is a claim about a set of rules over a period, and PHASE 10 chooses between variants using these records (SPEC 35, 38)';


-- ---------------------------------------------------------------------
-- replay_checks  (SPEC 39)
--
-- A decision that cannot be reproduced from its stored inputs cannot be
-- audited. Recording each check - including the ones that passed - is what
-- makes "ARUNA is deterministic" a measured statement rather than a claim.
-- ---------------------------------------------------------------------
CREATE TABLE replay_checks (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    checked_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    signal_id     CHAR(16)     NOT NULL,
    status        VARCHAR(16)  NOT NULL,
    divergences   JSON         NOT NULL,
    unavailable   VARCHAR(255) NULL,

    PRIMARY KEY (id),
    KEY replay_checks_signal_idx (signal_id, checked_at),
    KEY replay_checks_status_idx (status, checked_at),

    CONSTRAINT replay_checks_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT,
    CONSTRAINT replay_checks_status_allowed CHECK (
        status IN ('REPRODUCED', 'DIVERGED', 'NOT_REPLAYABLE')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
