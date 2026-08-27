-- =====================================================================
-- ARUNA AI - Sumbu arah untuk hasil futures
--
-- Diukur 2026-08-25 dari 218 baris `futures_plan_results`:
--
--     EXPIRED      201   (92,2%)
--     STOPPED_OUT   14
--     TARGET_HIT     3
--
-- Taksonomi lama hanya punya satu sumbu: level apa yang tersentuh lebih
-- dulu. Itu pertanyaan EKSEKUSI. Pertanyaan yang tidak pernah ditanyakan -
-- "apakah arahnya benar" - adalah pertanyaan RAMALAN, dan sembilan dari
-- sepuluh plan mendarat di satu ember yang secara eksplisit menyatakan
-- dirinya tidak menjawabnya. Akibatnya jalur futures tidak punya akurasi
-- arah sama sekali; satu-satunya angka akurasi yang pernah ARUNA punya
-- lahir di jalur spot, dan jalur spot dicabut 2026-08-25.
--
-- Dua sumbu, karena keduanya menunjuk perbaikan yang berbeda:
--
--   arah benar + stop kena  -> stop-nya terlalu ketat untuk jalur ini
--   arah salah + target kena -> beruntung, dan bukan bukti apa pun
--   arah salah + stop kena  -> agennya yang salah baca
--
-- Menyatukannya jadi satu angka menghapus tepat perbedaan yang menentukan
-- APA yang harus diperbaiki.
--
-- NULL pada 218 baris lama, dan itu jujur: `exit_price` mereka menyimpan
-- level stop/target untuk yang keluar lebih awal, bukan harga tutup
-- horizon, jadi arahnya TIDAK bisa direkonstruksi tanpa menarik ulang
-- jalur harganya. Mengisinya dari `exit_price` akan mengarang jawaban untuk
-- 17 baris dan memberi 201 sisanya kepastian yang tidak dimilikinya.
-- =====================================================================

ALTER TABLE futures_plan_results
    ADD COLUMN direction_correct TINYINT(1) NULL
        COMMENT 'pasar bergerak searah panggilan pada tutup horizon; NULL = tidak terukur'
        AFTER touched_liquidation;
