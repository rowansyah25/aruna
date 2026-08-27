-- =====================================================================
-- ARUNA AI - Koreksi diri modul XAU, tanpa persetujuan operator
--
-- Operator memutuskan 2026-08-28: koreksi berjalan sendiri tiap sekian
-- sinyal, tidak menunggu approval. Tabel ini yang membuat keputusan itu bisa
-- DIBANTAH - tiap putaran koreksi menyimpan bahan, angka, dan akibatnya.
--
-- **Yang dikoreksi adalah BOBOT AGEN, bukan ambang gerbang.** Bedanya
-- menentukan. Menyetel ambang terhadap hasilnya sendiri adalah cara tercepat
-- membuat win rate naik di atas kertas tanpa satu pun keputusan membaik -
-- persis yang spec larang sebagai overfitting. Bobot agen diukur terhadap
-- GARIS DASAR PASAR pada baris yang sama: seorang agen yang selalu bilang BUY
-- di pasar yang naik 60% waktu tidak punya keahlian, ia punya keberuntungan
-- yang bisa dihitung.
--
-- **`sampel` dan `garis_dasar` disimpan bersama tiap putaran**, bukan cuma
-- hasilnya. Sebuah multiplier tanpa penyebutnya tidak bisa dinilai, dan
-- perbandingan antar-putaran yang garis dasarnya bergeser adalah perbandingan
-- yang menguap - terukur di jalur lain: dasar bergerak 46% ke 62% antar paruh,
-- dan seluruh temuan gabungan ikut hilang.
--
-- **`diterapkan` boleh FALSE.** Putaran yang sampelnya kurang tetap dicatat -
-- itu yang membedakan "belum cukup bahan" dari "tidak pernah dijalankan", dan
-- keduanya terlihat sama persis kalau yang gagal tidak menulis apa-apa.
--
-- Model lama tidak pernah dihapus: tiap putaran menambah baris, dan
-- `versi_sebelumnya` menunjuk pendahulunya. Itu jalur fallback yang spec minta
-- - untuk kembali, cukup terapkan bobot dari baris sebelumnya.
-- =====================================================================

CREATE TABLE IF NOT EXISTS xau_model_versions (
    id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    versi             VARCHAR(32)  NOT NULL,
    versi_sebelumnya  VARCHAR(32)  NULL
        COMMENT 'jalur fallback: terapkan bobot baris ini untuk kembali',

    dijalankan_pada   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    dipicu_oleh       SMALLINT UNSIGNED NOT NULL
        COMMENT 'jumlah hasil terselesaikan saat putaran ini dipicu',

    -- Bahan. Tanpa keduanya, multiplier di bawah tak bisa dinilai siapa pun.
    sampel            SMALLINT UNSIGNED NOT NULL
        COMMENT 'suara berarah yang punya hasil; 0 = tidak ada yang bisa diukur',
    garis_dasar       DECIMAL(6,4) NULL
        COMMENT 'porsi pasar naik pada baris yang SAMA; NULL = tak terukur',

    diterapkan        BOOLEAN      NOT NULL DEFAULT FALSE
        COMMENT 'FALSE = sampel kurang; dicatat supaya beda dari tidak pernah jalan',
    alasan            VARCHAR(255) NULL
        COMMENT 'kenapa tidak diterapkan; NULL berarti diterapkan',

    -- Bobot per agen, JSON: {"TECHNICAL": 1.12, ...}. Agen yang sampelnya
    -- kurang TIDAK muncul di sini - ketiadaan berarti tidak diukur, dan
    -- menuliskannya 1.0 akan membuatnya tak bisa dibedakan dari yang diukur
    -- lalu ternyata netral.
    bobot             JSON         NULL,

    UNIQUE KEY uq_xau_versi (versi),
    KEY idx_xau_versi_waktu (dijalankan_pada DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
