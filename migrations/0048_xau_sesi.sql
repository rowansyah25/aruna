-- =====================================================================
-- ARUNA AI - Sesi perdagangan pada tiap keputusan XAU
--
-- **Direkam sebagai BUKTI, bukan dipakai sebagai aturan.** Spec melarang
-- keras "London = BUY, New York = SELL", dan kolom ini justru cara menaati
-- larangan itu dengan benar: sesi disimpan supaya pertanyaan "apakah XAU
-- lebih baik di LONDON" bisa DIJAWAB DATA kelak, bukan dijawab kode hari ini.
--
-- Sebuah aturan sesi yang ditulis sekarang akan menjadi keyakinan yang tidak
-- pernah diuji. Sebuah kolom sesi yang diisi sekarang menjadi bahan yang bisa
-- membantahnya.
--
-- Diambil pada CLOSE bar yang mendasari keputusan, bukan pada jam sistem saat
-- barisnya ditulis. Keduanya berbeda tiap kali tick terlambat, dan sesi yang
-- melekat pada sebuah keputusan adalah sesi saat barnya tutup.
--
-- `TUTUP` adalah nilai yang sah, bukan NULL: pasar tutup adalah keadaan yang
-- TERUKUR. NULL di kolom ini berarti barisnya ditulis sebelum kolom ini ada -
-- keadaan yang berbeda, dan yang tidak boleh tercampur saat mengiris nanti.
--
-- Batas sesinya perkiraan dan itu dinyatakan di `ForexCalendar.SESI_UTC`:
-- pusat perdagangan buka menurut jam lokalnya, jadi batas UTC-nya bergeser
-- satu jam saat daylight saving berlaku - dan Eropa dan Amerika tidak
-- bergeser pada tanggal yang sama. Cukup benar untuk MENGELOMPOKKAN,
-- sengaja tidak dipakai untuk MEMICU.
-- =====================================================================

ALTER TABLE xau_predictions
    ADD COLUMN sesi VARCHAR(16) NULL
        COMMENT 'ASIA/LONDON/NEW_YORK/OVERLAP/TUTUP pada close bar; NULL = baris pra-kolom'
        AFTER as_of,
    ADD COLUMN pasar_buka TINYINT(1) NULL
        COMMENT 'valas buka pada close bar; NULL = baris pra-kolom, bukan tutup';

-- Untuk "akurasi per sesi" tanpa memindai seluruh tabel.
CREATE INDEX idx_xau_sesi ON xau_predictions (sesi, keputusan);
