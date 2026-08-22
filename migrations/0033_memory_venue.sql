-- =====================================================================
-- ARUNA AI - dua dimensi venue di ingatan (PASAL 15.5)
--
-- **Yang tersisa sesudah 0032, dan keduanya ternyata sudah ada.**
--
-- Migrasi 0032 menutup lima dimensi teknikal dengan menghitung ulang dari
-- candle. Dua yang tertinggal - funding dan open interest - disebut waktu itu
-- sebagai "data venue perpetual yang tidak pernah disimpan per keputusan".
--
-- Itu benar untuk penyimpanannya, dan salah untuk ketersediaannya:
--
--   * `futures_plans.funding_cost_pct` **sudah terisi** pada 192 baris,
--     rentang -0,204 sampai +0,348 (terukur 2026-08-21). Kolomnya ada sejak
--     migrasi 0015 dan tidak pernah dibaca siapa pun untuk ingatan.
--   * `BinanceFuturesProvider.open_interest()` dan
--     `open_interest_history()` keduanya terimplementasi penuh, masuk
--     allowlist endpoint publik, dan **tidak pernah disimpan ke mana pun** -
--     kelas cacat yang sama dengan backtest yang dihitung lalu dibuang, dan
--     dengan mesin korelasi yang tabelnya nol baris.
--
-- Keduanya hanya berlaku untuk ingatan futures. Jalur spot tidak punya kontrak
-- perpetual, jadi di sana keduanya memang UNKNOWN - dan itu keadaan, bukan
-- kekurangan yang menunggu ditutup.
-- =====================================================================

ALTER TABLE market_memories
    ADD COLUMN funding_band VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'POSITIVE/FLAT/NEGATIVE dari funding_cost_pct; nol adalah bacaan'
        AFTER structure_band,
    ADD COLUMN oi_band      VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'RISING/FLAT/FALLING - ARAH open interest, bukan nilainya'
        AFTER funding_band;
