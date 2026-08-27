-- =====================================================================
-- ARUNA AI - Hasil sinyal XAUUSD, pada DUA sumbu yang terpisah
--
-- **Dua kolom sejak baris pertama, bukan satu.** Pelajaran ini sudah dibayar
-- mahal di jalur futures: `0044_futures_arah.sql` ditulis 2026-08-25 setelah
-- 218 baris hasil diukur dan 201 di antaranya (92,2%) mendarat di `EXPIRED` -
-- satu ember yang secara eksplisit menyatakan dirinya tidak menjawab apakah
-- arahnya benar. Akibatnya jalur futures tidak punya akurasi arah sama sekali
-- selama berbulan-bulan.
--
-- Taksonomi satu sumbu hanya bertanya LEVEL APA YANG TERSENTUH LEBIH DULU.
-- Itu pertanyaan EKSEKUSI. Pertanyaan yang tidak ditanyakannya - APAKAH
-- ARAHNYA BENAR - adalah pertanyaan RAMALAN, dan keduanya menunjuk perbaikan
-- yang berbeda:
--
--     arah benar + stop kena   -> stop-nya terlalu ketat untuk jalur ini
--     arah salah + target kena -> beruntung, dan bukan bukti apa pun
--     arah salah + stop kena   -> agennya yang salah baca
--
-- Menyatukannya jadi satu angka menghapus tepat perbedaan yang menentukan APA
-- yang harus diperbaiki.
--
-- **NO SIGNAL tidak punya baris di sini, dan itu ditegakkan constraint.**
-- Sebuah NO SIGNAL tidak menyatakan arah, jadi tidak ada hasil yang bisa
-- membenarkan atau menyalahkannya; mengklaim "seharusnya untung" berarti
-- mengarang panggilan yang tidak pernah ARUNA buat. Ini bukan kehati-hatian
-- teoretis: di jalur lain WAIT pernah dicatat kalah, dan win rate yang
-- dilaporkan 17% padahal akurasi sesungguhnya 44,2%.
--
-- **`arah_benar` NULL berarti tidak terukur**, bukan salah. Jalur harganya
-- belum cukup panjang untuk menutup horizon - keadaan yang berbeda dari
-- terukur-lalu-meleset, dan menyamakan keduanya akan menghitung tiap sinyal
-- yang masih berjalan sebagai kekalahan.
--
-- **LOSS tidak pernah dihapus.** Tidak ada kolom, indeks, atau constraint di
-- sini yang memperlakukan hasil rugi berbeda dari hasil untung, dan foreign
-- key-nya RESTRICT supaya menghapus prediksi gagal selama hasilnya ada.
-- =====================================================================

-- Kunci gabungan yang membuat penjaga di bawah mungkin. Lihat komentar
-- `xau_hasil_hanya_untuk_arah`.
ALTER TABLE xau_predictions
    ADD UNIQUE KEY uq_xau_id_keputusan (id, keputusan);


CREATE TABLE IF NOT EXISTS xau_results (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    prediction_id   BIGINT UNSIGNED NOT NULL,

    -- Disalin dari prediksinya, dan itu BUKAN duplikasi yang malas. Ia yang
    -- membuat aturan "NO SIGNAL tidak punya hasil" bisa ditegakkan struktur:
    -- lihat `xau_hasil_hanya_untuk_arah` di bawah.
    keputusan       VARCHAR(16)  NOT NULL,

    -- SUMBU RAMALAN. Diukur pada TUTUP HORIZON, bukan pada level yang
    -- tersentuh - itu yang membuatnya pertanyaan arah dan bukan pertanyaan
    -- eksekusi.
    arah_benar      TINYINT(1)   NULL
        COMMENT 'harga bergerak searah panggilan pada tutup horizon; NULL = tidak terukur',

    -- SUMBU EKSEKUSI. Level apa yang tersentuh lebih dulu.
    level_tersentuh VARCHAR(16)  NOT NULL,

    harga_tutup     DECIMAL(24,8) NOT NULL
        COMMENT 'close bar terakhir dalam horizon',
    gerak_pct       DECIMAL(12,6) NOT NULL
        COMMENT 'persen gerak dari entry ke harga_tutup, bertanda',
    bar_dipakai     SMALLINT UNSIGNED NOT NULL
        COMMENT 'bar M5 yang benar-benar ada; kurang dari horizon = belum tuntas',
    horizon_bar     SMALLINT UNSIGNED NOT NULL
        COMMENT 'disimpan supaya hasil bisa dinilai ulang saat horizonnya berubah',

    resolved_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_xau_hasil (prediction_id),
    KEY idx_xau_hasil_arah (arah_benar, level_tersentuh),

    CONSTRAINT xau_level_allowed
        CHECK (level_tersentuh IN ('TARGET', 'STOP', 'TIDAK_SATU_PUN')),

    -- **Penjaga strukturalnya, dan ia bekerja tanpa satu baris kode pun.**
    --
    -- CHECK di MySQL tidak boleh membaca tabel lain, dan trigger butuh badan
    -- BEGIN...END - yang tidak bisa dipakai di sini karena runner migrasi ini
    -- memecah berkas per titik koma tanpa penanganan DELIMITER (lihat catatan
    -- di kepala 0001_core.sql).
    --
    -- Jadi aturannya ditegakkan bentuk, bukan prosedur: `keputusan` disalin ke
    -- sini, CHECK melarangnya NO_SIGNAL, dan foreign key GABUNGAN memaksa
    -- pasangan (id, keputusan)-nya benar-benar ada di `xau_predictions`.
    -- Sebuah hasil untuk prediksi NO_SIGNAL karena itu mustahil dua kali:
    -- CHECK menolak nilainya, dan andai seseorang menuliskan 'BUY' di sini
    -- untuk prediksi yang sebenarnya NO_SIGNAL, FK-nya yang menolak.
    --
    -- Ini penting karena yang menulis ke tabel ini nanti adalah pipeline
    -- pembelajaran yang belum ditulis siapa pun - dan aturan yang hanya hidup
    -- di kode berlaku selama setiap penulis berikutnya mengingatnya.
    CONSTRAINT xau_hasil_hanya_untuk_arah CHECK (keputusan IN ('BUY', 'SELL')),
    -- RESTRICT, bukan CASCADE: sama seperti `xau_evidence`, menghapus prediksi
    -- harus GAGAL selama hasilnya masih ada.
    CONSTRAINT xau_hasil_prediction_fk FOREIGN KEY (prediction_id, keputusan)
        REFERENCES xau_predictions (id, keputusan) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
