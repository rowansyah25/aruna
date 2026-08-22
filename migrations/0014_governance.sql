-- =====================================================================
-- ARUNA AI - PHASE 10: research, proposals, shadow, drift, approval
--
-- SPEC 31, 44.
--
-- The decision record here is append-only, and that is the whole point of
-- the phase. A model change that could be approved, applied, and then have
-- its approval quietly edited would leave nobody accountable for what the
-- system does. `proposal_decisions` is the row a person signs.
--
-- Proposals themselves are mutable: a draft is edited, validated, shadowed,
-- submitted. What may never change is who decided, when, and on what
-- evidence - so the decision lives in its own append-only table rather
-- than as columns on the proposal.
-- =====================================================================


-- ---------------------------------------------------------------------
-- research_questions  (SPEC 31)
--
-- Derived from measurements, so the same question re-derives on each run;
-- keyed and upserted rather than accumulating duplicates. `status` is the
-- only human-editable part.
-- ---------------------------------------------------------------------
CREATE TABLE research_questions (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    question_key VARCHAR(96)   NOT NULL,
    question     VARCHAR(500)  NOT NULL,
    source       VARCHAR(16)   NOT NULL,
    evidence     JSON          NOT NULL,
    severity     DECIMAL(5,3)  NOT NULL DEFAULT 0.5,
    status       VARCHAR(16)   NOT NULL DEFAULT 'OPEN',
    raised_at    DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY research_questions_key (question_key),
    KEY research_questions_rank_idx (status, severity),

    CONSTRAINT research_questions_source_allowed CHECK (
        source IN ('BACKTEST', 'CALIBRATION', 'AUTOPSY', 'OBJECTIONS', 'DRIFT')
    ),
    CONSTRAINT research_questions_status_allowed CHECK (
        status IN ('OPEN', 'PROPOSED', 'CLOSED')
    ),
    CONSTRAINT research_questions_severity_range CHECK (
        severity >= 0 AND severity <= 1
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- model_proposals  (SPEC 44)
-- ---------------------------------------------------------------------
CREATE TABLE model_proposals (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    proposal_key  VARCHAR(96)   NOT NULL,
    title         VARCHAR(255)  NOT NULL,
    hypothesis    VARCHAR(1000) NOT NULL,
    change_note   VARCHAR(1000) NOT NULL,
    question_key  VARCHAR(96)   NULL,
    parameters    JSON          NOT NULL,
    status        VARCHAR(24)   NOT NULL DEFAULT 'DRAFT',
    validation    JSON          NULL,
    shadow        JSON          NULL,
    raised_at     DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at    DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                 ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY model_proposals_key (proposal_key),
    KEY model_proposals_status_idx (status, updated_at),

    CONSTRAINT model_proposals_status_allowed CHECK (
        status IN ('DRAFT', 'SHADOWED', 'VALIDATED', 'AWAITING_APPROVAL',
                   'APPROVED', 'REJECTED', 'ABANDONED')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- proposal_decisions  (SPEC 44) - the row a person signs
--
-- Append-only, and `decided_by` may not be blank. There is no application
-- path that writes 'system' here, and the constraint means there could not
-- be one added by accident: a decision without a name is not a decision.
-- ---------------------------------------------------------------------
CREATE TABLE proposal_decisions (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    proposal_key  VARCHAR(96)   NOT NULL,
    decision      VARCHAR(16)   NOT NULL,
    decided_by    VARCHAR(96)   NOT NULL,
    decided_at    DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    note          VARCHAR(500)  NULL,
    -- The evidence as it stood when the person decided. Copied rather than
    -- referenced: re-running validation later must not rewrite the grounds
    -- somebody actually approved on.
    validation    JSON          NULL,

    PRIMARY KEY (id),
    KEY proposal_decisions_key_idx (proposal_key, decided_at),

    CONSTRAINT proposal_decisions_proposal_fk FOREIGN KEY (proposal_key)
        REFERENCES model_proposals (proposal_key) ON DELETE RESTRICT,
    CONSTRAINT proposal_decisions_kind_allowed CHECK (
        decision IN ('APPROVED', 'REJECTED')
    ),
    CONSTRAINT proposal_decisions_named_human CHECK (
        char_length(trim(decided_by)) >= 2
        AND lower(trim(decided_by)) NOT IN ('system', 'aruna', 'auto', 'automatic', 'none')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TRIGGER proposal_decisions_no_update
    BEFORE UPDATE ON proposal_decisions
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'proposal_decisions is append-only: who approved a model change cannot be rewritten (SPEC 44)';

CREATE TRIGGER proposal_decisions_no_delete
    BEFORE DELETE ON proposal_decisions
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'proposal_decisions is append-only: a recorded approval cannot be deleted (SPEC 44)';


-- ---------------------------------------------------------------------
-- drift_checks  (SPEC 44)
-- ---------------------------------------------------------------------
CREATE TABLE drift_checks (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    checked_at        DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    baseline_resolved INT UNSIGNED  NOT NULL DEFAULT 0,
    recent_resolved   INT UNSIGNED  NOT NULL DEFAULT 0,
    performance_drift DECIMAL(8,4)  NULL,
    regime_shift      DECIMAL(8,4)  NULL,
    sufficient_sample BOOLEAN       NOT NULL DEFAULT FALSE,
    verdict           VARCHAR(500)  NOT NULL,
    findings          JSON          NOT NULL,

    PRIMARY KEY (id),
    KEY drift_checks_time_idx (checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
