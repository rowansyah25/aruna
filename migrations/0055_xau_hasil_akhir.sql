-- =====================================================================
-- ARUNA AI - Dimensi ketiga hasil XAU: apa yang operator dapat
--
-- Operator memutuskan 2026-08-28: kalau ARUNA menyuruh menutup posisi saat
-- masih untung, itu terhitung MENANG - karena peringatannya nyata, pada harga
-- yang nyata, di waktu yang nyata. Alasannya sah: hasil itu bisa diatribusikan
-- ke panggilan ARUNA, bukan ke kebetulan.
--
-- **Tapi ada jebakan, dan `r_multiple` yang menjaganya.** Kalau "untung"
-- berarti untung sepeser pun, hampir semua horizon jadi kemenangan dan angka
-- win rate berhenti berarti - persis "mengubah histori agar win rate
-- meningkat" yang spec larang. Harga yang bergerak +0,01% adalah derau satu
-- bar, bukan hasil.
--
-- Karena itu untung diukur dalam R, bukan dalam persen. R adalah JARAK STOP:
-- persis yang dipertaruhkan kalau bacaannya salah. Menang menuntut sekurangnya
-- 0,5 R - setengah dari yang dipertaruhkan. Ambangnya disimpan di
-- `MIN_R_UNTUK_WIN`, dan `r_multiple` disimpan per baris supaya klaim apa pun
-- bisa diaudit ulang dengan ambang lain.
--
-- **Tiga dimensi, dan tak satu pun boleh menggantikan yang lain:**
--
--   arah_benar       ramalan  - apakah arahnya benar pada tutup horizon
--   level_tersentuh  eksekusi - level apa yang tersentuh lebih dulu
--   hasil_akhir      hasil    - apa yang operator dapat kalau mengikuti ARUNA
--
-- Menyatukannya jadi satu angka menghapus tepat perbedaan yang menentukan apa
-- yang harus diperbaiki - pelajaran yang sudah dibayar di jalur futures, saat
-- 92,2% hasil mendarat di satu ember yang tidak menjawab apa pun.
--
-- **`menang` boleh NULL, dan itu penting.** ARUNA yang menyuruh MENAHAN belum
-- punya hasil: posisinya belum ditutup. Menghitungnya kalah akan menghukum
-- kesabaran yang ARUNA sendiri sarankan, dan menghitungnya menang akan
-- mengarang hasil yang belum terjadi. NULL adalah jawaban yang benar.
-- =====================================================================

ALTER TABLE xau_results
    ADD COLUMN hasil_akhir VARCHAR(16) NULL
        COMMENT 'TARGET/STOP/TUTUP_UNTUNG/TUTUP_RUGI/TAHAN; NULL = baris pra-kolom',
    ADD COLUMN r_multiple DECIMAL(10,4) NULL
        COMMENT 'untung/rugi dalam satuan jarak stop; NULL = risiko nol, tak terukur',
    ADD COLUMN menang TINYINT(1) NULL
        COMMENT 'NULL = belum bisa dinilai (ARUNA menyuruh menahan), bukan kalah';

ALTER TABLE xau_results
    ADD CONSTRAINT xau_hasil_akhir_allowed CHECK (
        hasil_akhir IS NULL OR hasil_akhir IN (
            'TARGET', 'STOP', 'TUTUP_UNTUNG', 'TUTUP_RUGI', 'TAHAN')
    );

-- Untuk "win rate dengan rinciannya" tanpa memindai seluruh tabel. Rincian
-- itu wajib: sebuah klaim 90% yang seluruhnya TUTUP_UNTUNG di 0,5 R adalah
-- cerita yang sangat berbeda dari 90% yang TARGET.
CREATE INDEX idx_xau_hasil_akhir ON xau_results (hasil_akhir, menang);
