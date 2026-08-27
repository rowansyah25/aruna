-- =====================================================================
-- ARUNA AI - Kabar lanjutan atas sinyal XAU yang masih berjalan
--
-- **Tabel ini yang membuat "hanya saat berganti" bisa ditegakkan.** XAU
-- menick tiap lima menit, jadi satu sinyal berhorizon empat jam melewati 48
-- tick. Tanpa catatan keadaan terakhir yang sudah dikabarkan, tiap tick akan
-- mengirim ulang hal yang sama - empat puluh delapan pesan untuk satu
-- gagasan, dan yang benar-benar penting tenggelam di antaranya.
--
-- Keadaan terakhir TIDAK disimpan di memori proses. Alasannya sudah dibayar
-- sekali di modul ini: penjaga "satu bar dinilai sekali" pernah hidup di
-- variabel proses, restart menghapusnya, dan supervisor mengubah tabrakan
-- kunci unik jadi crash loop. Di sini akibatnya lebih ringan - pesan terkirim
-- dua kali - tapi sebabnya sama persis, dan sudah diketahui.
--
-- **Satu baris per PERUBAHAN, bukan per tick.** Riwayatnya karena itu
-- terbaca sebagai cerita: BERJALAN -> MENDEKAT_STOP -> TESIS_BATAL. Itu yang
-- kelak menjawab "apakah pembatalan dini menyelamatkan lebih banyak daripada
-- yang dibatalkannya terlalu cepat" - pertanyaan yang tidak bisa dijawab
-- kalau yang tersimpan cuma keadaan terakhir.
--
-- `disarankan_tutup` dipisah dari keadaannya sendiri karena ia yang dicari
-- saat menilai kembali: berapa kali ARUNA menyuruh menutup, dan berapa di
-- antaranya ternyata benar.
-- =====================================================================

CREATE TABLE IF NOT EXISTS xau_kabar (
    id               BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    prediction_id    BIGINT UNSIGNED NOT NULL,
    keputusan        VARCHAR(16)  NOT NULL
        COMMENT 'disalin demi FK gabungan - kabar hanya untuk sinyal berarah',

    keadaan          VARCHAR(24)  NOT NULL,
    alasan           VARCHAR(255) NOT NULL,
    harga            DECIMAL(24,8) NOT NULL,
    sisa_bar         SMALLINT UNSIGNED NOT NULL,
    ke_target_atr    DECIMAL(10,4) NULL,
    ke_stop_atr      DECIMAL(10,4) NULL,
    disarankan_tutup BOOLEAN      NOT NULL DEFAULT FALSE,
    terkirim         BOOLEAN      NOT NULL DEFAULT FALSE
        COMMENT 'FALSE = keadaannya tercatat tapi pesannya gagal terkirim',

    dikabarkan_pada  DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    KEY idx_xau_kabar_baca (prediction_id, id DESC),
    KEY idx_xau_kabar_tutup (disarankan_tutup, dikabarkan_pada),

    CONSTRAINT xau_kabar_keadaan_allowed CHECK (
        keadaan IN ('BERJALAN', 'MENDEKAT_TARGET', 'MENDEKAT_STOP',
                    'TESIS_BATAL', 'HAMPIR_HABIS')
    ),
    -- Penjaga yang sama seperti `xau_results`: kabar hanya untuk sinyal
    -- berarah, ditegakkan bentuk lewat FK gabungan - sebuah NO SIGNAL tidak
    -- punya gagasan yang bisa batal.
    CONSTRAINT xau_kabar_hanya_untuk_arah CHECK (keputusan IN ('BUY', 'SELL')),
    CONSTRAINT xau_kabar_prediction_fk FOREIGN KEY (prediction_id, keputusan)
        REFERENCES xau_predictions (id, keputusan) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
