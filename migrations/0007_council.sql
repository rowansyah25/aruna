-- =====================================================================
-- ARUNA AI - PHASE 6: council (MySQL 8.0.16+)
--
-- SPEC 42: council_sessions, agent_objections, agent_rebuttals,
-- veto_events, veto_reviews, judge_decisions.
--
-- Everything is kept, including vetoes that were rejected on review and
-- objections that were answered. SPEC 26 later asks which objections turned
-- out to be right, and that question is unanswerable if the losing side of
-- an argument is discarded.
-- =====================================================================


CREATE TABLE council_sessions (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id             BIGINT UNSIGNED NOT NULL,
    market_code          VARCHAR(16)  NOT NULL,
    symbol               VARCHAR(64)  NOT NULL,
    interval_code        VARCHAR(8)   NOT NULL,
    as_of                DATETIME(6)  NOT NULL,
    decided_at           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    decision             VARCHAR(24)  NOT NULL,
    confidence           DECIMAL(6,3) NOT NULL DEFAULT 0,
    participating_agents INT UNSIGNED NOT NULL DEFAULT 0,
    total_agents         INT UNSIGNED NOT NULL DEFAULT 0,
    rounds_run           INT UNSIGNED NOT NULL DEFAULT 0,
    disagreement         DECIMAL(6,3) NOT NULL DEFAULT 0,
    objection_count      INT UNSIGNED NOT NULL DEFAULT 0,
    correction_count     INT UNSIGNED NOT NULL DEFAULT 0,

    risk_level           VARCHAR(16)  NOT NULL,
    veto_raised          INT UNSIGNED NOT NULL DEFAULT 0,
    veto_upheld          INT UNSIGNED NOT NULL DEFAULT 0,
    blocked              BOOLEAN      NOT NULL DEFAULT FALSE,
    no_trade_reasons     JSON         NULL,
    notes                JSON         NULL,

    -- A verdict is not a locked prediction; SPEC 20 arrives in PHASE 7.
    is_locked_signal     BOOLEAN      NOT NULL DEFAULT FALSE,
    phase                SMALLINT     NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY council_sessions_unique (asset_id, interval_code, as_of),
    KEY council_sessions_lookup_idx (market_code, symbol, interval_code, as_of),
    KEY council_sessions_decision_idx (decision, as_of),

    CONSTRAINT council_sessions_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT council_sessions_decision_allowed CHECK (
        decision IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    ),
    CONSTRAINT council_sessions_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- agent_objections  (SPEC 14 round 2, SPEC 42)
-- ---------------------------------------------------------------------
CREATE TABLE agent_objections (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id  BIGINT UNSIGNED NOT NULL,
    accuser     VARCHAR(32)  NOT NULL,
    target      VARCHAR(32)  NOT NULL,
    stance      VARCHAR(24)  NOT NULL,
    ground      VARCHAR(48)  NOT NULL,
    detail      TEXT         NOT NULL,
    severity    DECIMAL(6,3) NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    KEY agent_objections_session_idx (session_id),
    KEY agent_objections_target_idx (target, ground),

    CONSTRAINT agent_objections_session_fk FOREIGN KEY (session_id)
        REFERENCES council_sessions (id) ON DELETE CASCADE,
    CONSTRAINT agent_objections_stance_allowed CHECK (
        stance IN ('SUPPORT', 'OBJECT', 'COUNTER_PROPOSE', 'ACCEPT_CORRECTION')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- agent_rebuttals  (SPEC 14 round 3, SPEC 42)
-- ---------------------------------------------------------------------
CREATE TABLE agent_rebuttals (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id  BIGINT UNSIGNED NOT NULL,
    target      VARCHAR(32)  NOT NULL,
    accuser     VARCHAR(32)  NOT NULL,
    ground      VARCHAR(48)  NOT NULL,
    stance      VARCHAR(24)  NOT NULL,
    detail      TEXT         NOT NULL,
    conceded    BOOLEAN      NOT NULL DEFAULT FALSE,

    PRIMARY KEY (id),
    KEY agent_rebuttals_session_idx (session_id),
    KEY agent_rebuttals_conceded_idx (conceded, target),

    CONSTRAINT agent_rebuttals_session_fk FOREIGN KEY (session_id)
        REFERENCES council_sessions (id) ON DELETE CASCADE,
    CONSTRAINT agent_rebuttals_stance_allowed CHECK (
        stance IN ('SUPPORT', 'OBJECT', 'COUNTER_PROPOSE', 'ACCEPT_CORRECTION')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- veto_events / veto_reviews  (SPEC 18, 19, 42)
--
-- Kept as two tables because SPEC 19 makes the review a separate act with
-- its own outcome. Folding it into a column on the veto would lose the
-- distinction between "never reviewed" and "reviewed and rejected".
-- ---------------------------------------------------------------------
CREATE TABLE veto_events (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id    BIGINT UNSIGNED NOT NULL,
    raised_by     VARCHAR(32)  NOT NULL,
    reason        VARCHAR(48)  NOT NULL,
    detail        TEXT         NOT NULL,
    evidence_key  VARCHAR(64)  NULL,
    raised_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    KEY veto_events_session_idx (session_id),
    KEY veto_events_reason_idx (reason, raised_at),

    CONSTRAINT veto_events_session_fk FOREIGN KEY (session_id)
        REFERENCES council_sessions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


CREATE TABLE veto_reviews (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    veto_id     BIGINT UNSIGNED NOT NULL,
    outcome     VARCHAR(32)  NOT NULL,
    rationale   TEXT         NOT NULL,
    reviewed_at DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY veto_reviews_unique (veto_id),

    CONSTRAINT veto_reviews_veto_fk FOREIGN KEY (veto_id)
        REFERENCES veto_events (id) ON DELETE CASCADE,
    CONSTRAINT veto_reviews_outcome_allowed CHECK (
        outcome IN ('VETO_UPHELD_NO_TRADE', 'VETO_REJECTED')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- judge_decisions  (SPEC 16, 42)
--
-- `weights` records why each opinion counted for what it did, and
-- `unavailable_factors` records which SPEC 16 inputs did not exist yet.
-- Without the second, a later reader could not tell a judge that weighed
-- calibration from one that never had it.
-- ---------------------------------------------------------------------
CREATE TABLE judge_decisions (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id          BIGINT UNSIGNED NOT NULL,
    decision            VARCHAR(24)  NOT NULL,
    confidence          DECIMAL(6,3) NOT NULL DEFAULT 0,
    buy_weight          DECIMAL(12,5) NOT NULL DEFAULT 0,
    sell_weight         DECIMAL(12,5) NOT NULL DEFAULT 0,
    -- SPEC 16: a minority may win. Recorded so it can be audited later.
    minority_prevailed  BOOLEAN      NOT NULL DEFAULT FALSE,
    reasoning           JSON         NOT NULL,
    weights             JSON         NOT NULL,
    unavailable_factors JSON         NULL,
    decided_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY judge_decisions_unique (session_id),
    KEY judge_decisions_minority_idx (minority_prevailed),

    CONSTRAINT judge_decisions_session_fk FOREIGN KEY (session_id)
        REFERENCES council_sessions (id) ON DELETE CASCADE,
    CONSTRAINT judge_decisions_decision_allowed CHECK (
        decision IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
