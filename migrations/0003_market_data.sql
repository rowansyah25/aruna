-- =====================================================================
-- ARUNA AI - PHASE 2 market data (MySQL 8.0.16+)
--
-- Three tables, each with the SPEC 4 provenance columns:
--   source, provider_timestamp, server_timestamp, latency_ms
-- and the SPEC 5 quality verdict that was reached when the row arrived.
-- Storing the verdict alongside the data is what lets a later phase replay a
-- decision and see exactly how trustworthy its inputs were at the time.
--
-- Storage policy, stated because it is a real design choice:
--   * candles      - the workhorse. Complete history per interval.
--   * market_ticks - SAMPLED quotes, not every trade. A crypto trade stream
--                    is thousands of rows per second and analysis does not
--                    need them; what a signal needs is a reference price with
--                    bid/ask and spread at a known instant (SPEC 20).
--   * market_snapshots - one consolidated row per observation, the shape the
--                    council will read in later phases.
-- =====================================================================


-- ---------------------------------------------------------------------
-- candles  (SPEC 42)
-- ---------------------------------------------------------------------
CREATE TABLE candles (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id            BIGINT UNSIGNED NOT NULL,
    market_code         VARCHAR(16)    NOT NULL,
    symbol              VARCHAR(64)    NOT NULL,
    interval_code       VARCHAR(8)     NOT NULL,
    open_time           DATETIME(6)    NOT NULL,
    close_time          DATETIME(6)    NOT NULL,
    open                DECIMAL(30,12) NOT NULL,
    high                DECIMAL(30,12) NOT NULL,
    low                 DECIMAL(30,12) NOT NULL,
    close               DECIMAL(30,12) NOT NULL,
    volume              DECIMAL(30,12) NOT NULL,
    quote_volume        DECIMAL(30,12) NULL,
    trade_count         INT UNSIGNED   NULL,
    -- FALSE while the interval is still forming.  An open candle must never
    -- be treated as settled evidence (SPEC 24).
    is_closed           BOOLEAN        NOT NULL DEFAULT TRUE,

    source              VARCHAR(32)    NOT NULL,
    provider_timestamp  DATETIME(6)    NULL,
    server_timestamp    DATETIME(6)    NOT NULL,
    latency_ms          DECIMAL(12,3)  NULL,
    quality             VARCHAR(32)    NOT NULL DEFAULT 'OK',

    created_at          DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at          DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                       ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    -- One row per asset/interval/open_time.  Re-fetching the same window is a
    -- normal thing to do, and it must update rather than duplicate.
    UNIQUE KEY candles_unique (asset_id, interval_code, open_time),
    KEY candles_lookup_idx (market_code, symbol, interval_code, open_time),
    KEY candles_open_time_idx (open_time),

    CONSTRAINT candles_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT candles_high_is_highest CHECK (high >= low),
    CONSTRAINT candles_window_ordered CHECK (close_time > open_time),
    CONSTRAINT candles_volume_not_negative CHECK (volume >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- market_ticks  (SPEC 42) - sampled quotes
-- ---------------------------------------------------------------------
CREATE TABLE market_ticks (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id            BIGINT UNSIGNED NOT NULL,
    market_code         VARCHAR(16)    NOT NULL,
    symbol              VARCHAR(64)    NOT NULL,
    price               DECIMAL(30,12) NOT NULL,
    quantity            DECIMAL(30,12) NULL,
    bid                 DECIMAL(30,12) NULL,
    ask                 DECIMAL(30,12) NULL,
    bid_quantity        DECIMAL(30,12) NULL,
    ask_quantity        DECIMAL(30,12) NULL,
    spread_bps          DECIMAL(14,4)  NULL,

    source              VARCHAR(32)    NOT NULL,
    provider_timestamp  DATETIME(6)    NULL,
    server_timestamp    DATETIME(6)    NOT NULL,
    latency_ms          DECIMAL(12,3)  NULL,
    quality             VARCHAR(32)    NOT NULL,
    quality_detail      VARCHAR(255)   NULL,

    PRIMARY KEY (id),
    KEY market_ticks_lookup_idx (asset_id, server_timestamp),
    KEY market_ticks_symbol_idx (market_code, symbol, server_timestamp),

    CONSTRAINT market_ticks_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT market_ticks_price_positive CHECK (price > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- market_snapshots  (SPEC 42) - consolidated point-in-time view
-- ---------------------------------------------------------------------
CREATE TABLE market_snapshots (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id            BIGINT UNSIGNED NOT NULL,
    market_code         VARCHAR(16)    NOT NULL,
    symbol              VARCHAR(64)    NOT NULL,
    captured_at         DATETIME(6)    NOT NULL,

    last_price          DECIMAL(30,12) NOT NULL,
    bid                 DECIMAL(30,12) NULL,
    ask                 DECIMAL(30,12) NULL,
    spread_bps          DECIMAL(14,4)  NULL,
    high_24h            DECIMAL(30,12) NULL,
    low_24h             DECIMAL(30,12) NULL,
    volume_24h          DECIMAL(30,12) NULL,
    change_24h_pct      DECIMAL(14,6)  NULL,
    -- Summed depth on each side, for the liquidity signals in SPEC 32.
    bid_depth           DECIMAL(30,12) NULL,
    ask_depth           DECIMAL(30,12) NULL,

    -- SPEC 3, 9: which session produced this, and whether the venue was open.
    session_code        VARCHAR(24)    NULL,
    market_open         BOOLEAN        NULL,
    -- SPEC 5: FALSE for a delayed feed. Nothing downstream may call a
    -- snapshot realtime unless this says so.
    is_realtime         BOOLEAN        NOT NULL,
    declared_delay_sec  INT UNSIGNED   NOT NULL DEFAULT 0,

    source              VARCHAR(32)    NOT NULL,
    provider_timestamp  DATETIME(6)    NULL,
    server_timestamp    DATETIME(6)    NOT NULL,
    latency_ms          DECIMAL(12,3)  NULL,
    quality             VARCHAR(32)    NOT NULL,
    quality_detail      VARCHAR(255)   NULL,
    raw                 JSON           NULL,

    PRIMARY KEY (id),
    KEY market_snapshots_lookup_idx (asset_id, captured_at),
    KEY market_snapshots_symbol_idx (market_code, symbol, captured_at),

    CONSTRAINT market_snapshots_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT market_snapshots_price_positive CHECK (last_price > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- provider_events  - ingestion audit trail
--
-- Not in SPEC 42, but SPEC 5 requires detecting provider disconnects and
-- latency spikes, and SPEC 4 requires the source to be auditable.  Without a
-- record of when a feed dropped, a later loss autopsy (SPEC 25) cannot tell
-- "the model was wrong" from "the data was missing".
-- ---------------------------------------------------------------------
CREATE TABLE provider_events (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    occurred_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    provider      VARCHAR(32)   NOT NULL,
    market_code   VARCHAR(16)   NOT NULL,
    symbol        VARCHAR(64)   NULL,
    event_type    VARCHAR(32)   NOT NULL,
    quality       VARCHAR(32)   NULL,
    message       TEXT          NOT NULL,
    latency_ms    DECIMAL(12,3) NULL,
    details       JSON          NULL,
    PRIMARY KEY (id),
    KEY provider_events_occurred_idx (occurred_at),
    KEY provider_events_provider_idx (provider, occurred_at),

    CONSTRAINT provider_events_type_allowed CHECK (
        event_type IN (
            'CONNECTED', 'DISCONNECTED', 'REQUEST_FAILED', 'RATE_LIMITED',
            'QUALITY_REJECTED', 'BACKFILL_COMPLETED', 'GAP_DETECTED'
        )
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
