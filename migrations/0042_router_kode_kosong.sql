-- =====================================================================
-- ARUNA AI - Sebab penolakan router dalam bentuk yang bisa DIHITUNG
--
-- `alasan_kosong` (0041) menyimpan kalimat untuk dibaca manusia, dan kalimat
-- itu menyebut angkanya: "keyakinan rezim 20% di bawah ambang 50%",
-- "keyakinan rezim 32% di bawah ambang 50%". Berguna dibaca, tidak bisa
-- dikelompokkan.
--
-- **Dan itu bukan dugaan.** Versi pertama fase router mengelompokkan penolakan
-- dengan memotong kalimatnya, dan test menangkapnya sebelum dikomit: dua aset
-- yang ditolak karena hal yang SAMA menghasilkan dua kelompok berisi satu.
-- Laporan "router menolak 19 aset" dengan 19 kelompok berisi satu sama tak
-- bergunanya dengan daftar mentah.
--
-- Yang di Python sudah diperbaiki lewat `AlasanKosong`; kolom ini membawa
-- bentuk yang sama ke SQL, supaya pertanyaan "berapa sering router menolak,
-- dan kenapa" tidak dijawab dengan LIKE '%keyakinan%'.
--
-- Tiga nilai, dan ketiganya menuntut tindakan yang berbeda:
--
--   REZIM_TAK_TERBACA   Belum ada bacaan, atau seluruhnya UNCERTAIN. Yang
--                       perlu diperiksa PEMINDAINYA, bukan katalog strategi.
--   KEYAKINAN_KURANG    Terbaca tapi tipis atau terbelah. Yang perlu diperiksa
--                       kesegaran bacaan per horizon.
--   TAK_ADA_YANG_COCOK  Rezimnya jelas, katalognya yang tidak menutupinya.
--                       Terukur 2026-08-23: HIGH_VOLATILITY (453 bacaan) dan
--                       ANOMALY (49) memang tidak punya strategi.
--
-- NULL berarti ada champion. Baris 0041 yang sudah ada tetap NULL, dan itu
-- benar: tabelnya masih kosong saat kolom ini lahir.
-- =====================================================================

ALTER TABLE router_pilihan
    ADD COLUMN kode_kosong VARCHAR(24) NULL
        COMMENT 'sebab NONE yang bisa dikelompokkan; NULL = ada champion'
        AFTER alasan_kosong,
    ADD KEY idx_router_sebab (dipilih_pada, kode_kosong);
