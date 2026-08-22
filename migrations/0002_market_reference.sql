-- =====================================================================
-- ARUNA AI - market reference rows
--
-- The two markets are fixed by the specification, so they belong in a
-- migration.  The tradable universe is NOT seeded here: SPEC 3 requires it
-- to be configurable, so assets are loaded by `aruna seed` from
-- src/aruna/seed/universe.py and can be edited without a migration.
-- =====================================================================

INSERT INTO markets (code, display_name, timezone, is_continuous, quote_currency)
VALUES
    ('CRYPTO', 'Crypto / Digital Assets', 'UTC',          TRUE,  'USDT'),
    ('IDX',    'Bursa Efek Indonesia',    'Asia/Jakarta', FALSE, 'IDR')
ON DUPLICATE KEY UPDATE code = code;
