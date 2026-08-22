-- =====================================================================
-- ARUNA AI - PHASE 8 correction: publication is a fact, not a runtime guess
--
-- `should_lock` decides whether a verdict is fit to publish - it can
-- decline for confidence below the floor, or for evidence older than the
-- horizon being predicted. That decision was made at runtime and thrown
-- away, so the stored record could not tell a claim ARUNA made from a call
-- it explicitly refused to stand behind.
--
-- Two things went wrong because of that:
--
--   * /signals listed WAIT and NO_SIGNAL records as "open locked
--     predictions", so a quiet market looked like eight live calls;
--   * SPEC 29 calibration would have scored withheld calls as though they
--     had been published, measuring the system against claims it never
--     made. Nothing directional has resolved yet, so no wrong number has
--     been reported - this lands before the first one could be.
--
-- The flag lives on `signals` (the mutable lifecycle table), never on
-- `signal_snapshots`, which is frozen by SPEC 20.
-- =====================================================================

ALTER TABLE signals
    ADD COLUMN published BOOLEAN NOT NULL DEFAULT TRUE
        COMMENT 'false when the lock declined to publish this verdict',
    ADD COLUMN withheld_reason VARCHAR(255) NULL
        COMMENT 'why it was not published, verbatim from should_lock';

-- Backfill from what the record can actually prove. A non-directional
-- verdict was never publishable, so those are certain. Directional rows
-- locked before this migration keep `published = TRUE`: the reason they
-- would have been withheld was not recorded, and inventing one would be
-- exactly the fabrication this column exists to prevent.
UPDATE signals g
    JOIN signal_snapshots s ON s.signal_id = g.signal_id
    SET g.published = FALSE,
        g.withheld_reason = CONCAT(
            'backfilled: verdict is ', s.direction, ', not a position'
        )
    WHERE s.direction NOT IN ('BUY', 'SELL');

CREATE INDEX signals_published_idx ON signals (published, status, resolves_at);
