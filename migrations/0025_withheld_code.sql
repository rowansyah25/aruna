-- =====================================================================
-- ARUNA AI - kelompokkan alasan penahanan (PASAL 11.12)
--
-- Hampir semua yang PASAL 11.12 minta sudah tersimpan: bukti bullish dan
-- bearish di `council_votes`, confidence dan signal quality di
-- `signal_snapshots`, ambang tiap faktor di `quality_detail`. Yang hilang cuma
-- satu, dan hal itu yang membuat sisanya sulit dipakai:
--
--     withheld_reason: "verdict is WAIT, not a position"
--
-- Kalimat itu benar dan tidak bisa dihitung. Pertanyaan yang sebenarnya ingin
-- dijawab - KENAPA NO SIGNAL SEBANYAK INI - adalah pertanyaan pengelompokan,
-- dan mengelompokkan prosa berarti mencocokkan potongan teks yang berubah
-- setiap kali kalimatnya diperbaiki.
--
-- Seratus penahanan karena confidence di bawah lantai dan seratus karena data
-- basi adalah dua masalah yang sangat berbeda dengan dua perbaikan yang sangat
-- berbeda - dan keduanya terbaca sama persis selama alasannya hanya kalimat.
--
-- Kolom kalimatnya TIDAK diganti. Kode menjawab "kelompok apa", kalimat
-- menjawab "apa persisnya", dan menghapus yang kedua akan menghapus
-- satu-satunya tempat yang menyebut angka yang meleset.
--
-- NULL untuk baris lama, dan itu benar: prediksi yang ditahan sebelum
-- pengelompokan ini ada tidak punya kelompok. Mengisinya sekarang dengan
-- menebak dari kalimatnya akan mencampur data yang diukur dengan data yang
-- dikarang, di kolom yang gunanya justru menghitung (PASAL 11.21).
-- =====================================================================

ALTER TABLE signals
    ADD COLUMN withheld_code   VARCHAR(32) NULL
        COMMENT 'kelompok alasan penahanan; NULL untuk baris sebelum PASAL 11.12',
    ADD COLUMN withheld_detail JSON        NULL
        COMMENT 'nilai terukur dan ambang yang seharusnya dilewati';

CREATE INDEX signals_withheld_code_idx ON signals (withheld_code, locked_at);

ALTER TABLE signals
    ADD CONSTRAINT signals_withheld_code_allowed CHECK (
        withheld_code IS NULL OR withheld_code IN (
            'NON_DIRECTIONAL', 'CONFIDENCE_FLOOR', 'STALE_EVIDENCE',
            'QUALITY_GATE', 'DUPLICATE', 'COOLDOWN', 'UNKNOWN'
        )
    ),
    -- Sebuah prediksi yang dipublikasikan tidak ditahan, jadi ia tidak boleh
    -- punya kelompok penahanan. Tanpa aturan ini, satu bug di jalur penguncian
    -- bisa mengisi kolom ini untuk signal yang benar-benar terbit - dan
    -- hitungan "kenapa diam" akan memuat baris yang justru tidak diam.
    ADD CONSTRAINT signals_withheld_code_only_when_withheld CHECK (
        published = FALSE OR withheld_code IS NULL
    );
