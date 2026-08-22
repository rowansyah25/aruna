-- =====================================================================
-- ARUNA AI - PHASE 7: prediction lock, paper trading, outcomes
--
-- SPEC 42: signals, signal_snapshots, paper_trades, paper_results,
-- outcome_snapshots.
--
-- SPEC 20 is the reason this migration exists and the reason it looks the
-- way it does. A locked prediction may never change: not its direction,
-- confidence, entry, target, reasoning, timestamp or horizon. That is
-- enforced *in the database*, not left to application discipline, because
-- SPEC 22 requires outcomes to be scored against what was actually
-- predicted - and a system that can quietly revise its own past forecasts
-- cannot be evaluated at all.
--
-- The split between `signals` and `signal_snapshots` is deliberate:
--   * signal_snapshots is the immutable record. Append-only, enforced by
--     trigger. Nothing may ever update or delete a row.
--   * signals carries the mutable lifecycle (status, resolution) and
--     references the frozen snapshot.
-- Folding them into one table would mean either freezing the status - so a
-- signal could never be marked resolved - or leaving the prediction
-- editable. Neither is acceptable.
-- =====================================================================


-- ---------------------------------------------------------------------
-- signal_snapshots  (SPEC 20) - THE IMMUTABLE PREDICTION LOCK
-- ---------------------------------------------------------------------
CREATE TABLE signal_snapshots (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id           CHAR(16)      NOT NULL,
    -- SHA-256 over exactly the fields SPEC 20 forbids changing. Recomputing
    -- and comparing detects tampering that somehow bypassed the trigger.
    fingerprint         CHAR(64)      NOT NULL,

    asset_id            BIGINT UNSIGNED NOT NULL,
    market_code         VARCHAR(16)   NOT NULL,
    symbol              VARCHAR(64)   NOT NULL,
    horizon_code        VARCHAR(8)    NOT NULL,

    direction           VARCHAR(24)   NOT NULL,
    confidence          DECIMAL(6,3)  NOT NULL,
    reference_price     DECIMAL(30,12) NOT NULL,
    entry_price         DECIMAL(30,12) NOT NULL,
    target_price        DECIMAL(30,12) NULL,
    expected_move_pct   DECIMAL(14,6) NULL,

    bid                 DECIMAL(30,12) NULL,
    ask                 DECIMAL(30,12) NULL,
    spread_bps          DECIMAL(14,4) NULL,

    locked_at           DATETIME(6)   NOT NULL,
    as_of               DATETIME(6)   NOT NULL,
    resolves_at         DATETIME(6)   NOT NULL,
    data_timestamp      DATETIME(6)   NULL,

    reasoning           JSON          NOT NULL,
    regime              VARCHAR(24)   NULL,
    news_state          VARCHAR(255)  NULL,
    risk_level          VARCHAR(16)   NULL,
    data_source         VARCHAR(32)   NULL,
    model_version       VARCHAR(32)   NOT NULL,
    council_session_id  BIGINT UNSIGNED NULL,
    supersedes          CHAR(16)      NULL,

    PRIMARY KEY (id),
    UNIQUE KEY signal_snapshots_signal_id (signal_id),
    KEY signal_snapshots_lookup_idx (market_code, symbol, horizon_code, locked_at),
    KEY signal_snapshots_resolves_idx (resolves_at),

    CONSTRAINT signal_snapshots_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE RESTRICT,
    CONSTRAINT signal_snapshots_direction_allowed CHECK (
        direction IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    ),
    CONSTRAINT signal_snapshots_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    CONSTRAINT signal_snapshots_reference_positive CHECK (reference_price > 0),
    -- SPEC 24: a prediction cannot be based on data from after it was made.
    CONSTRAINT signal_snapshots_no_future_data CHECK (as_of <= locked_at),
    CONSTRAINT signal_snapshots_horizon_forward CHECK (resolves_at > locked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SPEC 20: after LOCK, nothing may be changed. These raise rather than
-- silently doing nothing, so an attempt shows up in the caller's stack trace.
CREATE TRIGGER signal_snapshots_no_update
    BEFORE UPDATE ON signal_snapshots
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'signal_snapshots is append-only: a locked prediction cannot be changed (SPEC 20) - issue a new signal that supersedes it';

CREATE TRIGGER signal_snapshots_no_delete
    BEFORE DELETE ON signal_snapshots
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'signal_snapshots is append-only: a locked prediction cannot be deleted (SPEC 20)';


-- ---------------------------------------------------------------------
-- signals  (SPEC 42) - the mutable lifecycle around a frozen snapshot
-- ---------------------------------------------------------------------
CREATE TABLE signals (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id      CHAR(16)     NOT NULL,
    asset_id       BIGINT UNSIGNED NOT NULL,
    market_code    VARCHAR(16)  NOT NULL,
    symbol         VARCHAR(64)  NOT NULL,
    horizon_code   VARCHAR(8)   NOT NULL,
    status         VARCHAR(16)  NOT NULL DEFAULT 'LOCKED',
    locked_at      DATETIME(6)  NOT NULL,
    resolves_at    DATETIME(6)  NOT NULL,
    resolved_at    DATETIME(6)  NULL,
    superseded_by  CHAR(16)     NULL,
    updated_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY signals_signal_id (signal_id),
    KEY signals_status_idx (status, resolves_at),
    KEY signals_lookup_idx (market_code, symbol, horizon_code, locked_at),

    CONSTRAINT signals_snapshot_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT,
    CONSTRAINT signals_status_allowed CHECK (
        status IN ('LOCKED', 'RESOLVED', 'SUPERSEDED', 'INVALIDATED')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- outcome_snapshots  (SPEC 23) - observations during the horizon
-- ---------------------------------------------------------------------
CREATE TABLE outcome_snapshots (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id     CHAR(16)      NOT NULL,
    offset_label  VARCHAR(16)   NOT NULL,
    sampled_at    DATETIME(6)   NOT NULL,
    price         DECIMAL(30,12) NOT NULL,
    move_pct      DECIMAL(14,6) NOT NULL,
    favourable    BOOLEAN       NOT NULL,
    is_final      BOOLEAN       NOT NULL DEFAULT FALSE,

    PRIMARY KEY (id),
    UNIQUE KEY outcome_snapshots_unique (signal_id, sampled_at),
    KEY outcome_snapshots_signal_idx (signal_id, sampled_at),

    CONSTRAINT outcome_snapshots_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- paper_results  (SPEC 22, 42) - the verdict on a prediction
--
-- A separate row rather than columns on the signal, so that scoring can
-- never be mistaken for editing the forecast.
-- ---------------------------------------------------------------------
CREATE TABLE paper_results (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id            CHAR(16)      NOT NULL,
    original_direction   VARCHAR(24)   NOT NULL,
    reference_price      DECIMAL(30,12) NOT NULL,
    final_price          DECIMAL(30,12) NOT NULL,
    predicted_move_pct   DECIMAL(14,6) NULL,
    actual_move_pct      DECIMAL(14,6) NOT NULL,
    prediction_error     DECIMAL(14,6) NULL,
    direction_correct    BOOLEAN       NOT NULL,
    outcome_class        VARCHAR(32)   NOT NULL,
    target_reached       BOOLEAN       NOT NULL DEFAULT FALSE,
    max_adverse_pct      DECIMAL(14,6) NOT NULL DEFAULT 0,
    max_favourable_pct   DECIMAL(14,6) NOT NULL DEFAULT 0,
    resolved_at          DATETIME(6)   NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY paper_results_signal (signal_id),
    KEY paper_results_class_idx (outcome_class, resolved_at),

    CONSTRAINT paper_results_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT,
    CONSTRAINT paper_results_class_allowed CHECK (
        outcome_class IN (
            'WRONG_FROM_START', 'RIGHT_THEN_REVERSED',
            'RIGHT_DIRECTION_BAD_TIMING', 'TARGET_REACHED',
            'TARGET_NOT_REACHED', 'HORIZON_MISMATCH', 'NO_POSITION'
        )
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- paper_trades  (SPEC 34, 42)
--
-- SPEC 34 requires net PnL, so every cost gets its own column. A single
-- "pnl" column would make it impossible to see later whether an edge was
-- real or was eaten by fees.
-- ---------------------------------------------------------------------
CREATE TABLE paper_trades (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id        CHAR(16)      NOT NULL,
    market_code      VARCHAR(16)   NOT NULL,
    symbol           VARCHAR(64)   NOT NULL,
    direction        VARCHAR(24)   NOT NULL,
    quantity         DECIMAL(30,12) NOT NULL,

    entry_price      DECIMAL(30,12) NOT NULL,
    entry_at         DATETIME(6)   NOT NULL,
    exit_price       DECIMAL(30,12) NULL,
    exit_at          DATETIME(6)   NULL,
    holding_seconds  BIGINT        NULL,

    entry_fee        DECIMAL(30,2) NOT NULL DEFAULT 0,
    exit_fee         DECIMAL(30,2) NOT NULL DEFAULT 0,
    slippage_cost    DECIMAL(30,2) NOT NULL DEFAULT 0,
    spread_cost      DECIMAL(30,2) NOT NULL DEFAULT 0,
    gross_pnl        DECIMAL(30,2) NOT NULL DEFAULT 0,
    net_pnl          DECIMAL(30,2) NOT NULL DEFAULT 0,
    net_pnl_pct      DECIMAL(14,6) NOT NULL DEFAULT 0,
    r_multiple       DECIMAL(14,4) NULL,
    result           VARCHAR(16)   NOT NULL DEFAULT 'OPEN',

    -- SPEC 46. A column, not a comment: any row claiming otherwise is
    -- rejected by the constraint below.
    is_paper         BOOLEAN       NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),
    UNIQUE KEY paper_trades_signal (signal_id),
    KEY paper_trades_result_idx (result, exit_at),

    CONSTRAINT paper_trades_signal_fk FOREIGN KEY (signal_id)
        REFERENCES signal_snapshots (signal_id) ON DELETE RESTRICT,
    CONSTRAINT paper_trades_result_allowed CHECK (
        result IN ('WIN', 'LOSS', 'BREAKEVEN', 'OPEN')
    ),
    CONSTRAINT paper_trades_paper_only CHECK (is_paper = TRUE),
    CONSTRAINT paper_trades_quantity_positive CHECK (quantity > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
