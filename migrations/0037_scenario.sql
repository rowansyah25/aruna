-- =====================================================================
-- ARUNA AI - Skenario simulasi (bagian 16.14, 16.15)
--
-- **Satu baris per skenario, bukan per aktivitas simulasi.** Ini pelajaran
-- Phase 15.1 yang dibayar mahal: `market_snapshots` menjadi 62% basis data
-- (216 MB kolom `raw` sendirian, dengan nol pembaca) karena tiap amatan
-- ditulis apa adanya. Simulasi menghasilkan jauh lebih banyak keadaan antara
-- daripada skenario; yang disimpan di sini hanya keluarannya.
--
-- Yang **tidak** ada kolomnya, dan tiap satu disengaja:
--
--   * masukan simulasi - sudah ada di `signal_snapshots` dan `candles`, dan
--     menyalinnya ke sini adalah gudang kedua atas data yang sama;
--   * pertanyaan simulasi - diturunkan dari `pemicu` dan kondisi awal, bisa
--     disusun ulang kapan saja oleh `scenario.pertanyaan`;
--   * langkah antara, ronde, atau jejak agent - bagian 16.14 melarang persis
--     jenis pertumbuhan ini.
--
-- **Sebelas bidang bagian 16.15** hadir seluruhnya: scenario_id, market,
-- asset, timestamp, scenario, scenario_weight, trigger, invalidation, risk,
-- evidence, simulation_version. Nama kolomnya mengikuti spec supaya bisa diadu
-- langsung dengannya; `trigger` adalah kata kunci MySQL sehingga ditulis
-- `pemicu`, dan itu satu-satunya penyimpangan.
--
-- **Bukan tabel terlindung bagian 31.** Daftar `DILINDUNGI` di
-- `upkeep/retensi.py` berisi keputusan dan buktinya; skenario bukan keputusan
-- (bagian 16.18 menaruh keputusan seluruhnya di Phase 14), jadi ia ikut
-- retensi biasa. Aturannya ada di `RENCANA`.
--
-- **Tidak ada trigger append-only**, berbeda dari `market_memories`. Alasannya
-- bukan kelonggaran: `hasil` dan `dinilai_pada` memang HARUS bisa diisi
-- belakangan, karena bagian 16.19 menilai skenario setelah pasarnya bergerak.
-- Yang dijaga adalah `scenario_id` UNIQUE - simulasi yang diulang atas masukan
-- yang sama menghasilkan id yang sama (mesinnya deterministik), jadi baris
-- ganda ditolak database alih-alih menumpuk diam-diam.
-- =====================================================================

CREATE TABLE scenario_evidence (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    scenario_id     VARCHAR(96)   NOT NULL
                    COMMENT 'deterministik: aset + waktu + urutan (bagian 16.15)',
    market_code     VARCHAR(16)   NOT NULL,
    asset           VARCHAR(32)   NOT NULL,
    dibuat_pada     DATETIME(6)   NOT NULL
                    COMMENT 'bagian 16.15 timestamp - kapan simulasinya berjalan',

    nama            VARCHAR(64)   NOT NULL
                    COMMENT 'bagian 16.15 scenario, mis. Bullish Continuation',
    deskripsi       VARCHAR(255)  NOT NULL DEFAULT '',
    bobot           TINYINT       NOT NULL DEFAULT 0
                    COMMENT 'bagian 16.15 scenario_weight, 0-100 RELATIF - bukan probabilitas pasar (bagian 16.6)',
    keyakinan       DECIMAL(5,4)  NOT NULL DEFAULT 0
                    COMMENT 'keyakinan mesin pada skenarionya, bukan pada arah pasar',
    pemicu          VARCHAR(255)  NOT NULL DEFAULT ''
                    COMMENT 'bagian 16.15 trigger - `trigger` kata kunci MySQL',
    risiko          VARCHAR(16)   NOT NULL DEFAULT 'UNKNOWN',

    -- JSON, dan bukan tabel anak, karena tidak ada satu pun kueri yang mencari
    -- SATU syarat invalidasi lintas skenario. Tabel anak untuk daftar yang
    -- selalu dibaca utuh adalah JOIN yang dibayar tiap pembacaan tanpa satu
    -- pun kueri yang memanfaatkannya.
    kondisi_awal    JSON          NULL,
    perkembangan    JSON          NULL
                    COMMENT 'rantai konsekuensi berurutan (bagian 16.8) - urutannya bagian dari datanya',
    invalidasi      JSON          NOT NULL
                    COMMENT 'bagian 16.15 invalidation; kosong ditolak di kode (bagian 16.11)',
    bukti           JSON          NULL
                    COMMENT 'bagian 16.15 evidence',

    kerapuhan       VARCHAR(8)    NOT NULL DEFAULT 'KOKOH'
                    COMMENT 'RAPUH kalau runtuh oleh satu syarat (bagian 16.10)',
    versi_simulasi  VARCHAR(32)   NOT NULL DEFAULT 'UNKNOWN'
                    COMMENT 'bagian 16.15 simulation_version - tanpanya, hasil dua mesin tercampur',
    sumber          VARCHAR(16)   NOT NULL DEFAULT 'INTERNAL'
                    COMMENT 'INTERNAL atau EKSTERNAL - keduanya tidak boleh dinilai dalam satu angka',

    -- Diisi belakangan oleh evaluasi bagian 16.19. NULL berarti horizonnya
    -- belum lewat, BUKAN skenario yang salah - menyatukan keduanya membuat
    -- evaluasi menghukum simulasi karena waktu belum berjalan.
    hasil           VARCHAR(16)   NULL
                    COMMENT 'BENAR / SALAH / SEBAGIAN (bagian 16.19); NULL = belum dinilai',
    dinilai_pada    DATETIME(6)   NULL,

    created_at      DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_scenario_id (scenario_id),
    KEY idx_scenario_aset (market_code, asset, dibuat_pada),
    KEY idx_scenario_nilai (hasil, dibuat_pada),
    KEY idx_scenario_retensi (dibuat_pada)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
