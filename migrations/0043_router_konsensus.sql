-- =====================================================================
-- ARUNA AI - Seberapa bulat pilihan router, dan atas berapa kandidat
--
-- Bagian 17.31 - 17.32: konsensus dan konflik.
--
-- **Dicatat, tidak menggerbangi.** Dua strategi yang sama-sama cocok bukan
-- bukti yang lemah - ia dua jawaban yang sama baiknya, dan menahan pilihan
-- karenanya berarti menghukum katalog yang lengkap. Yang bagian 17.32 tuntut
-- adalah konfliknya TERLIHAT.
--
-- Dan kolom, bukan kalimat di `alasan`, justru supaya pertanyaannya bisa
-- ditanyakan kepada data alih-alih ditebak sekarang:
--
--     SELECT ROUND(konsensus/10)*10 AS pita, COUNT(*), AVG(...)
--     FROM router_pilihan JOIN ... GROUP BY pita
--
-- "Apakah pilihan yang terbelah berakhir lebih buruk?" Itu kebijakan yang
-- hanya boleh lahir dari pengukuran - dan pengukuran itu mustahil kalau
-- angkanya cuma hidup di dalam teks JSON.
--
-- `kandidat_layak` ikut karena konsensus 100 dari SATU kandidat dan dari LIMA
-- kandidat berarti hal yang sangat berbeda. Yang pertama berarti tidak ada
-- yang membantah; yang kedua mustahil. Tanpa kolom ini keduanya terbaca sama.
--
-- NULL pada baris lama, dan itu benar: baris yang ditulis sebelum kolom ini
-- ada memang tidak pernah mengukurnya.
-- =====================================================================

ALTER TABLE router_pilihan
    ADD COLUMN konsensus DECIMAL(6,3) NULL
        COMMENT '0-100: bagian skor layak yang dipegang pemenang'
        AFTER challenger_skor,
    ADD COLUMN kandidat_layak TINYINT UNSIGNED NULL
        COMMENT 'berapa kandidat lolos ambang layak'
        AFTER konsensus;
