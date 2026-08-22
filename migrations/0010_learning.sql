-- =====================================================================
-- ARUNA AI - PHASE 8: learning from outcomes
--
-- SPEC 25 loss autopsy, SPEC 26 successful objections, SPEC 27
-- counterfactual, SPEC 28 ghost signals, SPEC 29 calibration,
-- SPEC 30 agent reliability.
--
-- Two shapes here, and the difference matters.
--
-- `loss_autopsies`, `counterfactuals` and `ghost_signals` are *findings
-- about one prediction*. They are recomputed from an immutable record, so
-- they are upsertable: rerunning the analysis on the same signal replaces
-- the finding rather than accumulating duplicates. The evidence they read
-- cannot change (signal_snapshots is append-only), so a recomputation can
-- only differ if the analysis code improved.
--
-- `calibration_snapshots` and `agent_reliability` are *measurements at an
-- instant*, and those are append-only. A calibration curve that was
-- overwritten each run would destroy the only record of whether the system
-- was becoming better calibrated over time - which is the entire question
-- SPEC 29 exists to answer.
-- =====================================================================


-- ---------------------------------------------------------------------
-- loss_autopsies  (SPEC 25)
-- ---------------------------------------------------------------------
CREATE TABLE loss_autopsies (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id          CHAR(16)      NOT NULL,
    outcome_class      VARCHAR(32)   NOT NULL,
    hypothesis         VARCHAR(255)  NOT NULL,
    confidence         DECIMAL(6,3)  NOT NULL,
    predicted_move_pct DECIMAL(14,6) NULL,
    actual_move_pct    DECIMAL(14,6) NOT NULL,
    max_adverse_pct    DECIMAL(14,6) NOT NULL DEFAULT 0,
    net_pnl            DECIMAL(30,2) NULL,

    regime             VARCHAR(24)   NULL,
    risk_level         VARCHAR(16)   NULL,
    news_state         VARCHAR(255)  NULL,

    -- Kept as JSON: these are the argument, read by humans and by the
    -- pattern queries in SPEC 26, not joined on.
    backers            JSON          NOT NULL,
    dissenters         JSON          NOT NULL,
    unanswered_objections JSON       NOT NULL,
    rejected_vetoes    JSON          NOT NULL,
    findings           JSON          NOT NULL,
    performed_at       DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY loss_autopsies_signal (signal_id),
    KEY loss_autopsies_class_idx (outcome_class, performed_at),

    CONSTRAINT loss_autopsies_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- counterfactuals  (SPEC 27) - what the other decision would have done
-- ---------------------------------------------------------------------
CREATE TABLE counterfactuals (
    id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id              CHAR(16)      NOT NULL,
    taken                  VARCHAR(24)   NOT NULL,
    taken_move_pct         DECIMAL(14,6) NOT NULL,
    alternative            VARCHAR(24)   NOT NULL,
    alternative_move_pct   DECIMAL(14,6) NOT NULL,
    alternative_was_better BOOLEAN       NOT NULL DEFAULT FALSE,
    computed_at            DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY counterfactuals_signal (signal_id),

    CONSTRAINT counterfactuals_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- ghost_signals  (SPEC 28) - the WAITs that cost something
--
-- The table exists so that standing aside is as visible as trading. A
-- performance report built only from taken positions makes a system that
-- never trades look flawless.
-- ---------------------------------------------------------------------
CREATE TABLE ghost_signals (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id        CHAR(16)      NOT NULL,
    symbol           VARCHAR(64)   NOT NULL,
    horizon_code     VARCHAR(8)    NOT NULL,
    missed_move_pct  DECIMAL(14,6) NOT NULL,
    direction        VARCHAR(24)   NOT NULL,
    why_we_waited    JSON          NOT NULL,
    computed_at      DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY ghost_signals_signal (signal_id),
    KEY ghost_signals_size_idx (missed_move_pct),

    CONSTRAINT ghost_signals_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- calibration_snapshots  (SPEC 29) - append-only measurements
--
-- `sufficient_sample` is stored rather than derived at read time. The
-- threshold may change; what this run was entitled to claim must not.
-- ---------------------------------------------------------------------
CREATE TABLE calibration_snapshots (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    measured_at       DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    total_resolved    INT UNSIGNED  NOT NULL,
    correct           INT UNSIGNED  NOT NULL,
    brier_score       DECIMAL(8,4)  NULL,
    sufficient_sample BOOLEAN       NOT NULL DEFAULT FALSE,
    verdict           VARCHAR(255)  NOT NULL,
    buckets           JSON          NOT NULL,

    PRIMARY KEY (id),
    KEY calibration_snapshots_time_idx (measured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TRIGGER calibration_snapshots_no_update
    BEFORE UPDATE ON calibration_snapshots
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'calibration_snapshots is append-only: a measurement is a record of what was true at a moment, and rewriting it destroys the trend it exists to show (SPEC 29)';


-- ---------------------------------------------------------------------
-- agent_reliability  (SPEC 30) - append-only, same reasoning
-- ---------------------------------------------------------------------
CREATE TABLE agent_reliability (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    measured_at       DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    agent             VARCHAR(32)   NOT NULL,
    scored_opinions   INT UNSIGNED  NOT NULL,
    correct           INT UNSIGNED  NOT NULL,
    accuracy          DECIMAL(6,4)  NULL,
    multiplier        DECIMAL(6,4)  NULL,
    vindicated        INT UNSIGNED  NOT NULL DEFAULT 0,
    overruled_correctly INT UNSIGNED NOT NULL DEFAULT 0,
    status            VARCHAR(24)   NOT NULL,

    PRIMARY KEY (id),
    KEY agent_reliability_agent_idx (agent, measured_at),

    -- NULL is the honest value for an unmeasured agent, so the constraint
    -- only bounds the figure when one exists.
    CONSTRAINT agent_reliability_multiplier_range CHECK (
        multiplier IS NULL OR (multiplier >= 0.5 AND multiplier <= 1.5)
    ),
    CONSTRAINT agent_reliability_status_allowed CHECK (
        status IN ('INSUFFICIENT_SAMPLE', 'BELOW_NEUTRAL', 'NEUTRAL', 'ABOVE_NEUTRAL')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TRIGGER agent_reliability_no_update
    BEFORE UPDATE ON agent_reliability
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'agent_reliability is append-only: an agent record is a measurement at an instant (SPEC 30)';
