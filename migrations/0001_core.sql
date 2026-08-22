-- =====================================================================
-- ARUNA AI - PHASE 1 core schema (MySQL 8.0.16+)
--
-- Scope: only the tables PHASE 1 reads and writes.  The full SPEC 42 list
-- is introduced by the phase that owns each table (docs/SCHEMA_ROADMAP.md),
-- because SPEC 45 requires each phase to be runnable and tested before the
-- next, and empty tables would misrepresent what is built.
--
-- Two conventions this file relies on, both non-obvious in MySQL:
--
-- 1. DATETIME(6), never TIMESTAMP.  TIMESTAMP is converted to and from the
--    session timezone on every read and write, and it cannot represent a
--    date past 2038.  DATETIME stores exactly what it is given.  ARUNA
--    always writes UTC and pins every connection to time_zone='+00:00', so
--    the CURRENT_TIMESTAMP(6) defaults below are UTC as well.
--
-- 2. VARCHAR, not TEXT, for anything indexed.  MySQL cannot index a TEXT
--    column without a prefix length, and a prefix index silently weakens
--    the uniqueness guarantees this schema depends on.
--
-- Statements are single-statement throughout - no BEGIN...END bodies - so
-- the migration runner can split the file without DELIMITER handling.
-- =====================================================================


-- ---------------------------------------------------------------------
-- markets  (SPEC 1 - CRYPTO and IDX only)
-- ---------------------------------------------------------------------
CREATE TABLE markets (
    code            VARCHAR(16)  NOT NULL,
    display_name    VARCHAR(128) NOT NULL,
    timezone        VARCHAR(64)  NOT NULL,
    is_continuous   BOOLEAN      NOT NULL,
    quote_currency  VARCHAR(16)  NOT NULL,
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                 ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (code),

    -- Forex was removed from ARUNA.  The constraint is the enforcement
    -- point: no code path, not even direct SQL, can add a third market.
    CONSTRAINT markets_code_allowed CHECK (code IN ('CRYPTO', 'IDX'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- assets  (SPEC 2, 3 - universe is configurable, never hardcoded)
-- ---------------------------------------------------------------------
CREATE TABLE assets (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market_code      VARCHAR(16)    NOT NULL,
    symbol           VARCHAR(64)    NOT NULL,
    display_name     VARCHAR(255)   NOT NULL,
    asset_class      VARCHAR(32)    NOT NULL,
    base_asset       VARCHAR(32)    NULL,
    quote_asset      VARCHAR(32)    NULL,
    sector           VARCHAR(64)    NULL,
    lot_size         INT            NULL,
    tick_size        DECIMAL(24,12) NULL,
    price_precision  SMALLINT       NULL,
    enabled          BOOLEAN        NOT NULL DEFAULT TRUE,
    listed_on        DATE           NULL,
    delisted_on      DATE           NULL,
    metadata         JSON           NOT NULL,
    created_at       DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at       DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY assets_symbol_unique (market_code, symbol),
    KEY assets_market_enabled_idx (market_code, enabled),
    KEY assets_sector_idx (sector),

    CONSTRAINT assets_market_fk FOREIGN KEY (market_code)
        REFERENCES markets (code) ON DELETE RESTRICT,
    CONSTRAINT assets_class_allowed CHECK (
        asset_class IN ('CRYPTO_SPOT', 'CRYPTO_PERP', 'IDX_EQUITY')
    ),
    CONSTRAINT assets_lot_size_positive CHECK (lot_size IS NULL OR lot_size > 0),
    -- SPEC 35: delisting matters for backtest survivorship bias, so the
    -- dates must stay coherent.
    CONSTRAINT assets_delisting_after_listing CHECK (
        delisted_on IS NULL OR listed_on IS NULL OR delisted_on >= listed_on
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- app_state  (durable runtime flags - kill switch, SPEC 40)
--
-- Columns are state_key / state_value because KEY is reserved in MySQL and
-- backticking it in every query invites the one place someone forgets.
-- ---------------------------------------------------------------------
CREATE TABLE app_state (
    state_key    VARCHAR(64) NOT NULL,
    state_value  JSON        NOT NULL,
    updated_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                             ON UPDATE CURRENT_TIMESTAMP(6),
    updated_by   VARCHAR(128) NULL,
    PRIMARY KEY (state_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- system_events  (SPEC 42 - operational timeline)
-- ---------------------------------------------------------------------
CREATE TABLE system_events (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    occurred_at  DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    instance     VARCHAR(64)  NOT NULL,
    phase        SMALLINT     NOT NULL,
    component    VARCHAR(64)  NOT NULL,
    event_type   VARCHAR(64)  NOT NULL,
    severity     VARCHAR(16)  NOT NULL,
    status       VARCHAR(16)  NULL,
    message      TEXT         NOT NULL,
    details      JSON         NOT NULL,
    PRIMARY KEY (id),
    KEY system_events_occurred_idx (occurred_at),
    KEY system_events_component_idx (component, occurred_at),
    -- PostgreSQL would make this a partial index on the two severities that
    -- matter; MySQL has no partial indexes, so it covers all four.
    KEY system_events_severity_idx (severity, occurred_at),

    CONSTRAINT system_events_severity_allowed CHECK (
        severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')
    ),
    CONSTRAINT system_events_status_allowed CHECK (
        status IS NULL OR status IN ('UP', 'DEGRADED', 'DOWN', 'DISABLED', 'UNKNOWN')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- audit_logs  (SPEC 42, 43 - who changed what, append only)
-- ---------------------------------------------------------------------
CREATE TABLE audit_logs (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    occurred_at   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    instance      VARCHAR(64)  NOT NULL,
    actor         VARCHAR(128) NOT NULL,
    action        VARCHAR(64)  NOT NULL,
    entity_type   VARCHAR(64)  NULL,
    entity_id     VARCHAR(128) NULL,
    before_state  JSON         NULL,
    after_state   JSON         NULL,
    result        VARCHAR(16)  NOT NULL,
    detail        TEXT         NULL,
    PRIMARY KEY (id),
    KEY audit_logs_occurred_idx (occurred_at),
    KEY audit_logs_actor_idx (actor, occurred_at),
    KEY audit_logs_action_idx (action, occurred_at),

    CONSTRAINT audit_logs_result_allowed CHECK (
        result IN ('SUCCESS', 'FAILURE', 'DENIED')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Audit rows are evidence.  These raise rather than silently doing nothing,
-- so a bug that tries to rewrite history shows up in the caller's stack
-- trace.  MySQL needs one trigger per event; there is no combined form.
CREATE TRIGGER audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'table audit_logs is append-only: UPDATE is not permitted';

CREATE TRIGGER audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'table audit_logs is append-only: DELETE is not permitted';


-- ---------------------------------------------------------------------
-- telegram_subscribers  (SPEC 21, 40 - command allowlist and delivery)
-- ---------------------------------------------------------------------
CREATE TABLE telegram_subscribers (
    chat_id        VARCHAR(64)  NOT NULL,
    chat_type      VARCHAR(32)  NULL,
    username       VARCHAR(128) NULL,
    display_name   VARCHAR(255) NULL,
    authorized     BOOLEAN      NOT NULL DEFAULT FALSE,
    first_seen_at  DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    command_count  BIGINT       NOT NULL DEFAULT 0,
    notes          TEXT         NULL,
    PRIMARY KEY (chat_id),
    KEY telegram_subscribers_authorized_idx (authorized)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
