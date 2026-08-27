-- =====================================================================
-- ARUNA AI - Proksi kekuatan dolar pada tiap keputusan XAU
--
-- **Kolomnya bernama `proksi_dolar`, BUKAN `dxy`, dan itu disengaja.**
-- Diukur 2026-08-28 terhadap venue ini: `DXY`, `DX=F`, `US10Y`, dan `TNX`
-- semuanya menjawab 404. Indeks dolar dan yield sama sekali tidak tersedia di
-- paket ini. Yang tersedia EUR/USD, dan ia 57,6% bobot keranjang DXY - proksi
-- yang wajar, tapi tetap BUKAN DXY.
--
-- Simbolnya disimpan per baris (`proksi_simbol`) supaya tidak pernah ada
-- keraguan tentang apa yang sebenarnya diukur, dan supaya baris lama tetap
-- terbaca kalau proksinya kelak diganti.
--
-- **Jebakan yang hampir termakan:** simbol `USDX` ADA di venue ini - tapi ia
-- "SGI Enhanced Core ETF", bukan indeks dolar. Simbolnya resolve, harganya
-- masuk akal, artinya salah. Sebuah kolom bernama `dxy` yang diam-diam berisi
-- ETF obligasi tidak akan pernah membantah apa pun, karena ia tidak
-- berhubungan dengan apa pun.
--
-- **Korelasi diukur atas RETURN, bukan harga.** Diukur atas 5000 bar M5 yang
-- stempel waktunya disejajarkan:
--
--     return : r = 0,348
--     harga  : r = 0,879   <- SPURIOUS
--
-- Keduanya sekadar sama-sama menanjak selama tujuh belas hari. Angka harga
-- membuat bukti ini terlihat empat kali lebih kuat daripada sebenarnya.
--
-- **Kekuatannya jujur: lemah, tandanya konsisten.** Sembilan belas jendela
-- 250-bar: median +0,366, rentang -0,046 sampai +0,586, positif pada 17 dari
-- 19. EUR/USD naik cenderung bersamaan dengan XAU naik - dolar melemah, emas
-- menguat - tapi r sekitar 0,35 hanya menjelaskan sekitar seperdelapan ragam.
--
-- Itu sebabnya kolom ini MEREKAM dan tidak ada gerbang yang membacanya. Spec
-- melarang "DXY naik = pasti SELL XAUUSD", dan angka di atas menunjukkan
-- kenapa larangan itu benar: aturan absolut di atas r=0,35 akan salah sangat
-- sering. Pertanyaan "apakah sinyal XAU lebih baik saat dolar melemah" dijawab
-- SQL kelak, bukan dijawab kode hari ini.
--
-- NULL berarti TIDAK TERUKUR - proksinya gagal ditarik, sampelnya terlalu
-- kecil, atau barisnya ditulis sebelum kolom ini ada. Bukan nol.
-- =====================================================================

ALTER TABLE xau_predictions
    ADD COLUMN proksi_simbol VARCHAR(24) NULL
        COMMENT 'simbol proksi dolar yang dipakai; BUKAN DXY - lihat migrasi ini',
    ADD COLUMN proksi_korelasi DECIMAL(7,4) NULL
        COMMENT 'korelasi RETURN dengan XAU; NULL = tidak terukur, bukan nol',
    ADD COLUMN proksi_sampel SMALLINT UNSIGNED NULL
        COMMENT 'return yang masuk hitungan - penyebut yang membuat r bisa dinilai',
    ADD COLUMN proksi_gerak_pct DECIMAL(12,6) NULL
        COMMENT 'gerak proksi selama jendela, bertanda';
