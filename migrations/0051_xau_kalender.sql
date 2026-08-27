-- =====================================================================
-- ARUNA AI - Konteks kalender ekonomi pada tiap keputusan XAU
--
-- **Direkam sebagai bukti; tidak ada gerbang yang membacanya.** Tidak ada
-- aturan "jangan sinyal menjelang NFP" di ARUNA. Jarak ke peristiwa
-- berdampak tinggi disimpan supaya pertanyaan "apakah sinyal XAU lebih buruk
-- menjelang rilis" DIJAWAB DATA kelak - bukan dijawab keyakinan hari ini,
-- yang tidak akan pernah diuji siapa pun.
--
-- **Dua sumber, karena lubang masing-masing ditutup yang lain.** Diukur
-- 2026-08-28:
--
--   ForexFactory  jadwal + dampak + forecast + previous, tanpa kunci
--                 TIDAK punya `actual` sama sekali - nol dari 71 peristiwa,
--                 termasuk 50 yang sudah lewat, dan bidangnya tak eksis
--   FRED          `actual` resmi, terbit memang setelah rilis
--                 tidak punya forecast konsensus
--
-- Twelve Data - yang kuncinya sudah kita punya - TIDAK punya kalender sama
-- sekali: /economic_calendar menjawab 404.
--
-- **Keamanan timestamp berbentuk, bukan dijanjikan.** `actual` sebuah
-- peristiwa hanya bisa dibaca lewat `PeristiwaEkonomi.actual_pada(sekarang)`,
-- yang memulangkan NULL selama peristiwanya belum rilis. Sebuah sumber yang
-- keliru memuat actual lebih awal - atau jam kita yang meleset - tidak cukup
-- untuk membocorkannya; keduanya harus salah bersamaan.
--
-- **`sumber_kalender` kosong berarti TIDAK ADA KALENDER**, yang berbeda dari
-- "tidak ada peristiwa". Yang pertama berarti sumbernya tak menjawab; yang
-- kedua berarti sumbernya menjawab dan minggu itu memang sepi. Menyamakan
-- keduanya akan membuat kegagalan jaringan terbaca sebagai pasar yang tenang.
--
-- Endpoint ForexFactory tidak resmi dan tidak berdokumen. Ia dipakai luas dan
-- stabil bertahun-tahun, tapi bisa berubah kapan saja - karena itu
-- kegagalannya ditelan dan dicatat, tidak pernah menjatuhkan keputusan XAU.
-- =====================================================================

ALTER TABLE xau_predictions
    ADD COLUMN sumber_kalender VARCHAR(48) NULL
        COMMENT 'sumber yang menjawab, dipisah koma; NULL/kosong = tidak ada kalender',
    ADD COLUMN menit_ke_rilis DECIMAL(10,1) NULL
        COMMENT 'menit menuju peristiwa relevan berikutnya; NULL = tidak ada/tak terukur',
    ADD COLUMN rilis_berikutnya VARCHAR(128) NULL,
    ADD COLUMN dampak_berikutnya VARCHAR(20) NULL
        COMMENT 'HIGH/MEDIUM/LOW/TIDAK_DINYATAKAN - yang terakhir bukan LOW',
    ADD COLUMN menit_sejak_rilis DECIMAL(10,1) NULL,
    ADD COLUMN dampak_tinggi_24j SMALLINT UNSIGNED NULL
        COMMENT 'peristiwa HIGH dalam 24 jam sekitar keputusan, ke belakang dan ke depan';

-- Untuk "akurasi menjelang rilis" tanpa memindai seluruh tabel.
CREATE INDEX idx_xau_rilis ON xau_predictions (dampak_berikutnya, menit_ke_rilis);
