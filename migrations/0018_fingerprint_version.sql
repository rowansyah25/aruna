-- =====================================================================
-- ARUNA AI - FUTURES: versi untuk fingerprint
--
-- Migrasi 0016 menambahkan `reference_price` ke plan, dan basis fingerprint
-- ikut berubah bersamanya. Akibatnya langsung dan tidak kelihatan sampai
-- resolver-nya jalan: setiap baris yang ditulis SEBELUM perubahan itu
-- menghitung ulang jadi hash yang berbeda, dan `verify()` menolak 26 baris
-- sebagai "berubah setelah diterbitkan" - padahal tidak satu pun tersentuh.
--
-- Cek-nya bekerja persis seperti seharusnya. Yang salah adalah basisnya
-- bergerak di bawahnya.
--
-- PHASE 7 kena hal yang sama lewat pembulatan DECIMAL, dan perbaikannya
-- waktu itu adalah mengunci skala. Ini bentuk keduanya: yang bergerak bukan
-- nilainya, melainkan DAFTAR FIELD yang ikut di-hash. Menyimpan versinya
-- berarti baris lama diverifikasi dengan basis yang berlaku saat ia ditulis,
-- dan perubahan basis berikutnya tidak lagi membatalkan seluruh arsip.
-- =====================================================================

ALTER TABLE futures_plans
    ADD COLUMN fingerprint_version TINYINT UNSIGNED NOT NULL DEFAULT 1
    AFTER fingerprint;

-- Baris yang sudah ada ditulis dengan basis v1 (tanpa reference_price).
-- Default kolomnya sudah 1, jadi tidak ada backfill yang perlu dijalankan -
-- baris baru yang menuliskan versinya sendiri akan menimpanya.
