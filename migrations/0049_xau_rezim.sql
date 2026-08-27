-- =====================================================================
-- ARUNA AI - Rezim pasar pada tiap keputusan XAU
--
-- **Kolom, bukan tabel `xau_market_regimes` tersendiri.** Spec menyebut tabel
-- itu, dan spec yang sama berkata "jangan membuat duplicate table jika
-- struktur existing sudah bisa digunakan". Sebuah rezim di sini selalu milik
-- tepat satu keputusan pada tepat satu bar; tabel terpisah akan mengulang
-- `as_of` dan `symbol` untuk hubungan satu-ke-satu, lalu menuntut join untuk
-- pertanyaan paling sering ditanyakan - "rezim apa saat ia menolak".
--
-- **Direkam karena gerbangnya nyata dan angkanya sudah diukur.** Diukur
-- 2026-08-27 atas 396 jendela M5 sepanjang 17 hari:
--
--     RANGING           33,3%      HIGH_VOLATILITY    9,1%
--     UNCERTAIN         17,4%      TRENDING_BULLISH   8,3%
--     TRENDING_BEARISH  13,4%      LOW_VOLATILITY     3,5%
--     REVERSAL          11,9%      BREAKOUT/DOWN      3,0%
--
-- 17,4% keputusan diblokir gerbang UNKNOWN_REGIME sebelum agen mana pun
-- sempat berselisih, dan SELURUHNYA karena dua rezim seri ketat - bukan
-- karena bukti tak ada. Tanpa kolom ini, angka itu harus diukur ulang dari
-- luar tiap kali seseorang bertanya, dan tidak pernah bisa disandingkan
-- dengan hasil keputusannya.
--
-- Valas spot tidak menerbitkan volume, jadi dua dari tujuh slot bukti rezim
-- (`volume_anomaly`, `volume_trend`) TIDAK AKAN PERNAH terisi untuk XAU -
-- keduanya memulangkan NULL dan menyatakan dirinya tidak andal, bukan
-- mengarang nol. `bukti_dipakai` merekam berapa yang benar-benar terpakai
-- supaya keyakinan rezim bisa dinilai ulang dengan penyebutnya.
--
-- NULL berarti baris pra-kolom, bukan rezim yang tak terbaca - yang kedua
-- punya namanya sendiri, `UNCERTAIN`.
-- =====================================================================

ALTER TABLE xau_predictions
    ADD COLUMN regime VARCHAR(24) NULL
        COMMENT 'rezim M5 saat keputusan; UNCERTAIN = terbaca-tapi-seri, NULL = pra-kolom',
    ADD COLUMN regime_confidence DECIMAL(6,4) NULL,
    ADD COLUMN bukti_dipakai TINYINT UNSIGNED NULL
        COMMENT 'bacaan andal yang masuk klasifikasi; maksimum 5 untuk XAU, bukan 7',
    ADD COLUMN bukti_tersedia TINYINT UNSIGNED NULL;

-- Untuk "akurasi per rezim" tanpa memindai seluruh tabel.
CREATE INDEX idx_xau_regime ON xau_predictions (regime, keputusan);
