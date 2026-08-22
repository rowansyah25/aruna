-- 0036: simpan KENAPA sebuah keputusan salah, bukan hanya apa yang terjadi.
--
-- Bagian 12 Phase 15 minta klasifikasi sebab. `learning/sebab.py` menghitungnya
-- dari bukti yang sudah tersimpan pada tiap autopsy - regime, keadaan berita,
-- tingkat risiko, keyakinan, keberatan yang tak terjawab - tapi `record_autopsy`
-- menulis kolom eksplisit dan `sebab` tidak ada di antaranya.
--
-- Tanpa kolom ini klasifikasinya dihitung lalu dibuang: terlihat di keluaran
-- CLI, tidak pernah bisa dikueri, dan tidak pernah bisa dipakai pembelajaran.
-- Itu keluarga cacat yang sama dengan `korelasi` yang tak pernah dipanggil dan
-- `HIGH_VOLATILITY` yang tak pernah menang.
--
-- Terukur atas 1.433 autopsy: BAD_TECHNICAL_SIGNAL 36,0%, OTHER 18,1%,
-- AGENT_OVERCONFIDENCE 13,2%, FALSE_BREAKOUT 13,0%, NEWS_SHOCK 8,8%,
-- TIMING_ERROR 5,9%, INSUFFICIENT_DATA 2,9%, WRONG_REGIME 2,0%.
--
-- ADD COLUMN, bukan rewrite (bagian 33). NULL untuk baris lama: autopsy yang
-- ditulis sebelum klasifikasinya ada memang belum punya sebab, dan menebaknya
-- sekarang berarti mengarang.

ALTER TABLE loss_autopsies
    ADD COLUMN sebab VARCHAR(32) NULL AFTER hypothesis,
    ADD INDEX loss_autopsies_sebab (sebab);
