-- =====================================================================
-- ARUNA AI - `scenario_evidence.pemicu` diperlebar
--
-- Terukur 2026-08-22 lewat tulisan sungguhan ke tabel ini: seluruh tiga belas
-- pemicu bagian 16.2 yang menyala bersamaan menghasilkan string **245
-- karakter** di kolom VARCHAR(255). Muat, dengan sisa sepuluh karakter.
--
-- Sepuluh karakter bukan margin. Satu pemicu keempat belas, atau satu nama yang
-- diperpanjang, melewatinya - dan yang terjadi bukan galat melainkan pemotongan
-- diam-diam, karena repositori memanggil `s.pemicu[:255]` sebelum mengirimnya.
-- Skenario yang kehilangan sebagian daftar pemicunya tetap tersimpan, tetap
-- terbaca rapi, dan tidak bisa diperiksa ulang terhadap apa yang sebenarnya
-- membangunkannya.
--
-- Lima ratus dua belas: dua kali lipat lebih dari yang terukur, cukup untuk
-- menggandakan kosakata pemicu tanpa menyentuh skema lagi. ALTER ini berjalan
-- pada tabel kosong (nol baris pada saat ditulis), jadi tidak ada penulisan
-- ulang data sama sekali.
--
-- Pemotongan diam-diamnya sendiri dicabut di `db/repositories/scenario.py`;
-- kolom yang lebar tidak menolong kalau kodenya tetap memotong lebih dulu.
-- =====================================================================

ALTER TABLE scenario_evidence
    MODIFY COLUMN pemicu VARCHAR(512) NOT NULL DEFAULT ''
    COMMENT 'bagian 16.15 trigger - `trigger` kata kunci MySQL; 245 char terukur untuk 13 pemicu';
