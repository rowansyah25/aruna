-- =====================================================================
-- ARUNA AI - PHASE 5: agents, risk, no-trade (MySQL 8.0.16+)
--
-- SPEC 42: agent_decisions.  The council tables (council_sessions,
-- agent_objections, agent_rebuttals, veto_events, veto_reviews,
-- judge_decisions) belong to PHASE 6 and are not created here - SPEC 45
-- requires each phase runnable before the next, and empty tables would
-- misrepresent what exists.
--
-- `deliberations` is not in SPEC 42. It exists because SPEC 39 requires a
-- decision to be replayable in chronological order, and that needs the
-- surrounding record - risk assessment, no-trade verdict, the critique -
-- not just the individual votes.
-- =====================================================================


CREATE TABLE deliberations (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id            BIGINT UNSIGNED NOT NULL,
    market_code         VARCHAR(16)  NOT NULL,
    symbol              VARCHAR(64)  NOT NULL,
    interval_code       VARCHAR(8)   NOT NULL,
    -- Newest settled bar behind this decision. Everything considered is at or
    -- before it (SPEC 24), which is what makes a PHASE 9 replay provable.
    as_of               DATETIME(6)  NOT NULL,
    decided_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    outcome             VARCHAR(24)  NOT NULL,
    confidence          DECIMAL(6,3) NOT NULL DEFAULT 0,
    -- SPEC 17: how much of the cited evidence was actually distinct.
    independence        DECIMAL(6,3) NOT NULL DEFAULT 0,
    participating_agents INT UNSIGNED NOT NULL DEFAULT 0,
    total_agents        INT UNSIGNED NOT NULL DEFAULT 0,

    proposal_decision   VARCHAR(24)  NULL,
    proposal_confidence DECIMAL(6,3) NULL,
    prosecutor_decision VARCHAR(24)  NULL,
    prosecutor_confidence DECIMAL(6,3) NULL,
    -- SPEC 15: did the self-critic find stronger counter-evidence?
    reassessment_required BOOLEAN    NOT NULL DEFAULT FALSE,

    risk_level          VARCHAR(16)  NOT NULL,
    blocked             BOOLEAN      NOT NULL DEFAULT FALSE,
    no_trade_reasons    JSON         NULL,

    risk_factors        JSON         NULL,
    critique            JSON         NULL,
    notes               JSON         NULL,

    -- PHASE 5 runs round one only. Stored so a later phase cannot mistake
    -- these rows for council verdicts (SPEC 14, 16).
    is_council_decision BOOLEAN      NOT NULL DEFAULT FALSE,
    phase               SMALLINT     NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY deliberations_unique (asset_id, interval_code, as_of),
    KEY deliberations_lookup_idx (market_code, symbol, interval_code, as_of),
    KEY deliberations_outcome_idx (outcome, as_of),

    CONSTRAINT deliberations_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT deliberations_outcome_allowed CHECK (
        outcome IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    ),
    CONSTRAINT deliberations_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- agent_decisions  (SPEC 42)
--
-- One row per agent per deliberation. `evidence` records what the agent
-- actually consulted, which is what lets the PHASE 6 judge discount agents
-- that were reading the same input (SPEC 17).
-- ---------------------------------------------------------------------
CREATE TABLE agent_decisions (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    deliberation_id BIGINT UNSIGNED NOT NULL,
    role            VARCHAR(32)  NOT NULL,
    decision        VARCHAR(24)  NOT NULL,
    confidence      DECIMAL(6,3) NOT NULL DEFAULT 0,
    -- An abstention is a real answer: the agent had nothing to judge on.
    abstained       BOOLEAN      NOT NULL DEFAULT FALSE,
    horizon_code    VARCHAR(8)   NULL,
    reasoning       JSON         NOT NULL,
    evidence        JSON         NULL,
    evidence_count  INT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY agent_decisions_unique (deliberation_id, role),
    KEY agent_decisions_role_idx (role, decision),

    CONSTRAINT agent_decisions_deliberation_fk FOREIGN KEY (deliberation_id)
        REFERENCES deliberations (id) ON DELETE CASCADE,
    CONSTRAINT agent_decisions_decision_allowed CHECK (
        decision IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    ),
    CONSTRAINT agent_decisions_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
