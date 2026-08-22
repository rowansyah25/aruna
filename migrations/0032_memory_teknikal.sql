-- =====================================================================
-- ARUNA AI - lima dimensi teknikal di ingatan (PASAL 15.5)
--
-- **Kenapa kolom baru, padahal 0031 sengaja tidak memberinya.**
--
-- Migrasi 0031 menolak membuat kolom untuk volatility, volume, momentum,
-- trend, dan structure dengan alasan yang benar waktu itu: tidak satu pun
-- pernah ditulis ke database, jadi kolomnya akan selalu UNKNOWN - dan kolom
-- yang selalu UNKNOWN menyesatkan pembaca berikutnya.
--
-- Alasan itu sudah tidak berlaku. Kelimanya ternyata **tidak perlu ditulis
-- siapa pun**: `realised_volatility`, `momentum`, `volume_anomaly`, dan
-- `analyse_structure` semuanya berjalan atas candle yang sudah tersimpan sejak
-- Juli. Dihitung ulang pada bar yang sudah tutup sebelum tiap keputusan, mereka
-- lahir untuk seluruh korpus - termasuk 9.026 ingatan yang sudah ada.
--
-- **Yang memaksa ini terjadi:** evaluasi PASAL 15.44 pada 2026-08-21
-- melaporkan selisih hanya **+3 poin** antara keputusan yang sejarahnya
-- SUPPORTIVE dan CONTRARY - derau. Sidik jari berdimensi delapan tidak cukup
-- membedakan satu kondisi pasar dari yang lain, dan menambah dimensi adalah
-- satu-satunya jalan yang tidak berupa mengarang.
--
-- `open_interest` dan `funding` tetap TIDAK diberi kolom, dengan alasan asli
-- 0031 yang masih berlaku: keduanya data venue perpetual yang tidak pernah
-- disimpan per keputusan dan tidak bisa diturunkan dari candle spot.
-- =====================================================================

ALTER TABLE market_memories
    ADD COLUMN volatility_band VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'LOW/MEDIUM/HIGH dari realised_volatility, tercile korpus'
        AFTER liquidity_band,
    ADD COLUMN momentum_band   VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'NEGATIVE/FLAT/POSITIVE dari momentum 10 bar, tercile korpus'
        AFTER volatility_band,
    ADD COLUMN volume_band     VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'LOW/NORMAL/HIGH dari volume_anomaly, tercile korpus'
        AFTER momentum_band,
    ADD COLUMN trend_band      VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'BULLISH/BEARISH/SIDEWAYS - arah momentum, bukan besarnya'
        AFTER volume_band,
    ADD COLUMN structure_band  VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        COMMENT 'UPTREND/DOWNTREND/RANGE dari urutan swing (SPEC 6)'
        AFTER trend_band;

-- Rezim tetap punya indeksnya sendiri (0031). Yang ini melayani pertanyaan
-- yang berbeda: "apa yang terjadi ketika volatilitas setinggi ini" - dan tanpa
-- indeks, pertanyaan itu memindai seluruh tabel tiap tick.
CREATE INDEX idx_memory_teknikal
    ON market_memories (market_code, timeframe, volatility_band, momentum_band);
