-- =====================================================================
-- ARUNA AI - Simpan PILIHAN router, bukan tiap perhitungannya
--
-- Phase 17, bagian 17.27 / 17.44 / 17.52.
--
-- **Satu baris per pilihan, dan itu keputusan yang sudah punya pelajarannya.**
-- Router berjalan tiap siklus atas dua puluh aset. Menyimpan tiap peringkat
-- berarti mengulang `market_snapshots`, yang menjadi 62% basis data ini dengan
-- nol pembaca. Yang disimpan keputusannya, bukan jalan menuju keputusannya.
--
-- **`alasan_kosong` justru kolom terpenting di sini.** Nol karena tidak ada
-- strategi yang cocok dan nol karena fasenya mati terlihat SAMA PERSIS dari
-- luar - yang pertama normal sementara yang kedua bug. Tanpa kolom ini,
-- laporan "router tidak memilih siapa pun hari ini" tidak bisa dibantah.
--
-- Dan ia akan sering terisi. Diukur 2026-08-23 sebelum router menyala:
--
--     UNCERTAIN       1.860 dari 9.437 bacaan 15m (19,7%)  - rezim tak terbaca
--     HIGH_VOLATILITY   453                                - tak ada strateginya
--     ANOMALY            49                                - tak ada strateginya
--
-- ditambah tiap aset yang cuma punya satu horizon segar, yang keyakinannya
-- paling tinggi 48 dan ambangnya 50. NONE adalah keluaran yang WAJAR di sini,
-- bukan kegagalan yang perlu disembunyikan.
--
-- **Tidak pernah ditimpa** (bagian 17.9, 17.27, 17.44). Rezim berganti sesudah
-- sebuah pilihan tercatat adalah hal biasa; mengubah catatannya membuat seluruh
-- evaluasi Phase 12 mengukur keputusan yang tidak pernah diambil siapa pun.
-- Kunci uniknya `(asset, dipilih_pada)` supaya siklus yang berjalan dua kali
-- pada bar yang sama tidak menghasilkan dua baris - tapi baris yang sudah ada
-- tidak diubah, ia ditolak.
--
-- **`versi_router` bukan hiasan.** Ia yang membedakan baris yang dilabeli
-- ROUTER dari baris turunan `classify()`, dan tanpanya slice performa per rezim
-- kembali melingkar - lihat `src/aruna/router/label.py`.
-- =====================================================================

CREATE TABLE IF NOT EXISTS router_pilihan (
    id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    asset_id          BIGINT UNSIGNED NOT NULL,
    market_code       VARCHAR(16)  NOT NULL,
    symbol            VARCHAR(64)  NOT NULL,
    dipilih_pada      DATETIME(6)  NOT NULL
        COMMENT 'awal bar yang jadi dasar keputusan, bukan jam sistem',

    -- Rezim yang jadi dasar. NULL berarti tidak terbaca sama sekali; itu
    -- keadaan yang berbeda dari terbaca-tapi-tak-yakin, dan `alasan_kosong`
    -- yang membedakannya dalam kata-kata.
    regime_primary    VARCHAR(24)  NULL,
    regime_confidence DECIMAL(6,3) NULL
        COMMENT '0-100: cakupan x kesepakatan x keyakinan classifier',
    regime_stability  DECIMAL(6,3) NULL
        COMMENT '0-100; NULL = riwayatnya terlalu pendek untuk diukur',
    interval_hilang   VARCHAR(64)  NULL
        COMMENT 'horizon yang diminta tapi tak punya bacaan, dipisah koma',

    champion          VARCHAR(32)  NULL,
    champion_skor     TINYINT UNSIGNED NULL,
    challenger        VARCHAR(32)  NULL,
    challenger_skor   TINYINT UNSIGNED NULL,

    -- Kosong (NULL) berarti ADA champion. Terisi berarti TIDAK ada, dan
    -- sebabnya ada di sini.
    alasan_kosong     VARCHAR(255) NULL,
    -- Alasan champion terpilih, satu kalimat per faktor (bagian 17.6).
    alasan            JSON         NULL,

    versi_router      VARCHAR(32)  NOT NULL,
    created_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_router_pilihan (asset_id, dipilih_pada),
    KEY idx_router_baca (asset_id, dipilih_pada DESC),
    -- Untuk pertanyaan "berapa sering router menolak, dan kenapa" tanpa
    -- memindai seluruh tabel.
    KEY idx_router_kosong (dipilih_pada, champion)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
