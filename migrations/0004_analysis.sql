-- =====================================================================
-- ARUNA AI - PHASE 3 analysis (MySQL 8.0.16+)
--
-- SPEC 42: technical_snapshots, volume_snapshots, regimes.
--
-- Two design points worth stating:
--
-- 1. `as_of` is the open time of the newest SETTLED bar behind a row.
--    Everything in that row derives from data at or before it. When PHASE 9
--    replays a decision, `as_of` is what proves no future bar leaked in
--    (SPEC 24), so it is NOT NULL everywhere.
--
-- 2. Readings are stored as JSON with their sample sizes rather than as bare
--    float columns. SPEC 6 treats indicators as evidence, and a column
--    holding 47.3 with no record of how many bars produced it cannot be
--    weighed later - it can only be believed.
-- =====================================================================


-- ---------------------------------------------------------------------
-- technical_snapshots  (SPEC 6, 42)
-- ---------------------------------------------------------------------
CREATE TABLE technical_snapshots (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id           BIGINT UNSIGNED NOT NULL,
    market_code        VARCHAR(16)  NOT NULL,
    symbol             VARCHAR(64)  NOT NULL,
    interval_code      VARCHAR(8)   NOT NULL,
    as_of              DATETIME(6)  NOT NULL,
    computed_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    bars               INT UNSIGNED NOT NULL,
    reliable_readings  INT UNSIGNED NOT NULL,
    total_readings     INT UNSIGNED NOT NULL,
    excluded_open_bars INT UNSIGNED NOT NULL DEFAULT 0,

    -- Frequently queried values are promoted to columns for indexing; the
    -- full set with sample sizes stays in `readings`.
    close              DECIMAL(30,12) NULL,
    rsi                DECIMAL(10,4)  NULL,
    atr_pct            DECIMAL(10,4)  NULL,
    macd_histogram     DECIMAL(20,8)  NULL,
    bollinger_pct_b    DECIMAL(10,4)  NULL,

    readings           JSON NOT NULL,
    structure          JSON NOT NULL,
    notes              JSON NULL,

    PRIMARY KEY (id),
    -- One snapshot per asset/interval/bar. Recomputing the same bar refreshes
    -- rather than accumulating near-duplicates.
    UNIQUE KEY technical_snapshots_unique (asset_id, interval_code, as_of),
    KEY technical_snapshots_lookup_idx (market_code, symbol, interval_code, as_of),

    CONSTRAINT technical_snapshots_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT technical_snapshots_bars_positive CHECK (bars > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- volume_snapshots  (SPEC 6, 42)
-- ---------------------------------------------------------------------
CREATE TABLE volume_snapshots (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id       BIGINT UNSIGNED NOT NULL,
    market_code    VARCHAR(16)  NOT NULL,
    symbol         VARCHAR(64)  NOT NULL,
    interval_code  VARCHAR(8)   NOT NULL,
    as_of          DATETIME(6)  NOT NULL,
    computed_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    latest_volume  DECIMAL(30,12) NULL,
    average_volume DECIMAL(30,12) NULL,
    -- Latest bar as a multiple of its recent average. 1.0 is typical.
    volume_ratio   DECIMAL(14,4)  NULL,
    volume_trend_pct DECIMAL(14,4) NULL,
    vwap           DECIMAL(30,12) NULL,
    vwap_distance_pct DECIMAL(14,4) NULL,
    is_anomaly     BOOLEAN      NOT NULL DEFAULT FALSE,
    detail         VARCHAR(255) NULL,

    PRIMARY KEY (id),
    UNIQUE KEY volume_snapshots_unique (asset_id, interval_code, as_of),
    KEY volume_snapshots_anomaly_idx (is_anomaly, as_of),

    CONSTRAINT volume_snapshots_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- regimes  (SPEC 9, 42)
-- ---------------------------------------------------------------------
CREATE TABLE regimes (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id       BIGINT UNSIGNED NOT NULL,
    market_code    VARCHAR(16)  NOT NULL,
    symbol         VARCHAR(64)  NOT NULL,
    interval_code  VARCHAR(8)   NOT NULL,
    as_of          DATETIME(6)  NOT NULL,
    computed_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    regime         VARCHAR(24)  NOT NULL,
    confidence     DECIMAL(6,3) NOT NULL,
    trend          VARCHAR(24)  NULL,
    breakout       VARCHAR(32)  NULL,
    session_code   VARCHAR(24)  NULL,
    -- How much of the offered evidence was actually usable. A regime backed
    -- by 2 of 7 readings is a different claim from one backed by 7 of 7.
    evidence_used      INT UNSIGNED NOT NULL DEFAULT 0,
    evidence_available INT UNSIGNED NOT NULL DEFAULT 0,
    reasons        JSON NULL,
    alternatives   JSON NULL,

    PRIMARY KEY (id),
    UNIQUE KEY regimes_unique (asset_id, interval_code, as_of),
    KEY regimes_lookup_idx (market_code, symbol, interval_code, as_of),
    KEY regimes_regime_idx (regime, as_of),

    CONSTRAINT regimes_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT regimes_allowed CHECK (
        regime IN (
            'TRENDING', 'RANGING', 'BREAKOUT', 'REVERSAL',
            'HIGH_VOLATILITY', 'LOW_VOLATILITY', 'NEWS_SHOCK',
            'ACCUMULATION', 'DISTRIBUTION', 'UNCERTAIN', 'ANOMALY'
        )
    ),
    CONSTRAINT regimes_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
