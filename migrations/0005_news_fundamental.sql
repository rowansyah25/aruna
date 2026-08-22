-- =====================================================================
-- ARUNA AI - PHASE 4: news, fundamentals, correlation (MySQL 8.0.16+)
--
-- SPEC 42: news_events, fundamentals.  Correlation is not in SPEC 42 but
-- SPEC 17 and SPEC 32 both need it stored: evidence independence and
-- concentration risk are judged against history, not recomputed guesses.
--
-- SPEC 8 requires every news item to carry timestamp, source, asset,
-- category, importance, sentiment and freshness, and requires the source to
-- be auditable.  All seven are columns here and the URL is retained so any
-- classification can be checked against the original.
-- =====================================================================


-- ---------------------------------------------------------------------
-- news_events  (SPEC 8, 42)
-- ---------------------------------------------------------------------
CREATE TABLE news_events (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    -- Hash of the URL. Outlets syndicate each other, so this is what keeps
    -- one story from being counted as several pieces of evidence (SPEC 17).
    fingerprint          CHAR(32)     NOT NULL,
    title                VARCHAR(512) NOT NULL,
    url                  VARCHAR(1024) NOT NULL,
    summary              TEXT         NULL,

    source               VARCHAR(64)  NOT NULL,
    market_code          VARCHAR(16)  NULL,
    category             VARCHAR(32)  NOT NULL,
    importance           VARCHAR(16)  NOT NULL,
    sentiment            VARCHAR(16)  NOT NULL,
    -- How much the lexicon actually had to go on. Low means "barely fired",
    -- which a later phase must be able to see rather than infer.
    sentiment_confidence DECIMAL(6,3) NOT NULL DEFAULT 0,
    matched_terms        JSON         NULL,

    -- Publisher time vs our receipt time. The gap is SPEC 8 freshness, and a
    -- stale feed is invisible if only one is recorded.
    published_at         DATETIME(6)  NULL,
    fetched_at           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY news_events_fingerprint (fingerprint),
    KEY news_events_published_idx (published_at),
    KEY news_events_market_idx (market_code, published_at),
    KEY news_events_category_idx (category, published_at),
    KEY news_events_importance_idx (importance, published_at),

    CONSTRAINT news_events_sentiment_allowed CHECK (
        sentiment IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL', 'UNKNOWN')
    ),
    CONSTRAINT news_events_importance_allowed CHECK (
        importance IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- One story can name several assets, so the link is its own table rather
-- than a column.
CREATE TABLE news_asset_links (
    news_id   BIGINT UNSIGNED NOT NULL,
    asset_id  BIGINT UNSIGNED NOT NULL,
    symbol    VARCHAR(64) NOT NULL,
    PRIMARY KEY (news_id, asset_id),
    KEY news_asset_links_asset_idx (asset_id),

    CONSTRAINT news_asset_links_news_fk FOREIGN KEY (news_id)
        REFERENCES news_events (id) ON DELETE CASCADE,
    CONSTRAINT news_asset_links_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- fundamentals  (SPEC 7, 42)
--
-- Every metric is nullable on purpose: a provider that does not report ROA
-- must leave it NULL, never 0. Those mean opposite things, and conflating
-- them would corrupt any valuation built on top (SPEC 4).
-- ---------------------------------------------------------------------
CREATE TABLE fundamentals (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id              BIGINT UNSIGNED NOT NULL,
    symbol                VARCHAR(64)  NOT NULL,
    source                VARCHAR(32)  NOT NULL,
    fetched_at            DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    as_of_date            DATE         NOT NULL,

    currency              VARCHAR(16)  NULL,
    sector                VARCHAR(64)  NULL,
    industry              VARCHAR(128) NULL,

    revenue_growth_pct    DECIMAL(14,4) NULL,
    earnings_growth_pct   DECIMAL(14,4) NULL,
    eps                   DECIMAL(20,6) NULL,
    roe_pct               DECIMAL(14,4) NULL,
    roa_pct               DECIMAL(14,4) NULL,
    debt_to_equity        DECIMAL(14,4) NULL,
    free_cash_flow        DECIMAL(30,2) NULL,
    total_debt            DECIMAL(30,2) NULL,
    price_to_earnings     DECIMAL(14,4) NULL,
    price_to_book         DECIMAL(14,4) NULL,
    book_value_per_share  DECIMAL(20,6) NULL,
    dividend_yield_pct    DECIMAL(14,4) NULL,
    profit_margin_pct     DECIMAL(14,4) NULL,
    market_cap            DECIMAL(30,2) NULL,

    -- Share of the SPEC 7 metric set actually reported. A verdict from three
    -- metrics must stay distinguishable from one built on twelve.
    coverage              DECIMAL(6,3) NOT NULL DEFAULT 0,
    missing_metrics       JSON         NULL,

    -- SPEC 7 verdict. Explicitly not a recommendation.
    verdict               VARCHAR(16)  NULL,
    verdict_confidence    DECIMAL(6,3) NULL,
    verdict_reasons       JSON         NULL,
    verdict_concerns      JSON         NULL,

    PRIMARY KEY (id),
    -- One row per asset per day; refetching the same day refreshes it.
    UNIQUE KEY fundamentals_unique (asset_id, as_of_date),
    KEY fundamentals_symbol_idx (symbol, as_of_date),
    KEY fundamentals_verdict_idx (verdict, as_of_date),

    CONSTRAINT fundamentals_asset_fk FOREIGN KEY (asset_id)
        REFERENCES assets (id) ON DELETE CASCADE,
    CONSTRAINT fundamentals_verdict_allowed CHECK (
        verdict IS NULL OR verdict IN
            ('UNDERVALUED', 'FAIR_VALUE', 'OVERVALUED', 'UNCERTAIN')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- correlations  (SPEC 17, 32)
--
-- Computed on returns, never raw prices, and only over bars the two assets
-- genuinely share. `overlap` is stored because a coefficient from 12 bars is
-- a different claim from one built on 200.
-- ---------------------------------------------------------------------
CREATE TABLE correlations (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    market_code    VARCHAR(16)  NOT NULL,
    interval_code  VARCHAR(8)   NOT NULL,
    left_symbol    VARCHAR(64)  NOT NULL,
    right_symbol   VARCHAR(64)  NOT NULL,
    coefficient    DECIMAL(8,5) NOT NULL,
    overlap        INT UNSIGNED NOT NULL,
    strength       VARCHAR(16)  NOT NULL,
    computed_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    as_of          DATETIME(6)  NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY correlations_unique (interval_code, left_symbol, right_symbol, as_of),
    KEY correlations_lookup_idx (market_code, interval_code, as_of),

    CONSTRAINT correlations_range CHECK (coefficient >= -1 AND coefficient <= 1),
    CONSTRAINT correlations_distinct CHECK (left_symbol <> right_symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
