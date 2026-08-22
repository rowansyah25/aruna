-- =====================================================================
-- ARUNA AI - Simpan APAKAH SYARAT BATALNYA TERPICU, bukan cuma hasilnya
--
-- **Bagian 16.19 menuntut dua kegagalan dinilai terpisah, dan tabel ini tidak
-- bisa membedakannya.** Skenario bisa gagal dengan dua cara yang menuntut
-- tindakan berlawanan:
--
--   * Syarat batalnya terpicu. Skenario mengatakan "batal kalau harga kembali
--     di bawah area tembusan", dan harga kembali di bawah area tembusan. Ini
--     simulasi yang BEKERJA: ia menyebutkan syarat batalnya, syarat itu
--     terjadi, dan pembacanya sudah diperingatkan.
--   * Tidak satu pun syarat batalnya terpicu, dan ia tetap salah. Ini mesin
--     yang meleset SEKALIGUS invalidasi yang tidak berguna.
--
-- Menyatukan keduanya menghasilkan satu angka yang MEMBAIK ketika skenario
-- berhenti menyebutkan syarat batalnya - persis arah yang salah.
--
-- Terukur 2026-08-23 sebelum kolom ini ada: 928 baris SALAH (68,2% dari yang
-- dinilai), dan tidak satu pun bisa dipisahkan antara keduanya. `Putusan`
-- sudah membawa bendera ini sejak awal; yang hilang adalah tempat
-- menyimpannya, jadi `catat_hasil` menuliskan `hasil` lalu membuangnya.
--
-- **NULL berarti tidak bisa diperiksa, bukan "tidak terpicu".** Tiap keluarga
-- menyebut dua syarat batal dan hanya yang pertama berbicara tentang harga;
-- yang kedua menyebut volume, kedalaman order book, atau berita. Untuk
-- `News-Driven Reversal` bahkan syarat pertamanya pun bukan tentang bentuk
-- harga. Memaksa kolom ini NOT NULL akan mengubah "tidak diperiksa" menjadi
-- "lulus pemeriksaan", dan itu mengarang pengukuran.
--
-- Baris lama tetap NULL, dan itu benar: mereka dinilai oleh kode yang memang
-- tidak pernah memeriksanya.
-- =====================================================================

ALTER TABLE scenario_evidence
    ADD COLUMN diinvalidasi TINYINT(1) NULL
        COMMENT 'syarat batalnya terpicu; NULL = tidak bisa diperiksa dari jejak'
        AFTER hasil;
