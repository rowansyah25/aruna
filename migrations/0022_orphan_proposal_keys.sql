-- =====================================================================
-- ARUNA AI - tandai proposal yang kehilangan pertanyaannya
--
-- Migrasi 0021 menghapus research_questions era-IDR. Tiga model_proposals
-- selamat dan tetap menunjuk ke `no_measurable_edge_1d`, kunci yang kini
-- tidak ada barisnya. 0021 sengaja tidak meng-NULL-kan question_key dengan
-- alasan tertulis: kunci yang terisi tapi tidak ketemu bisa ditelusuri ke
-- migrasi itu.
--
-- Alasan itu hanya berlaku selama kuncinya tidak dipakai ulang. Dan kuncinya
-- PASTI dipakai ulang: `question_key` tidak membawa nama market dan tidak
-- membawa rezim biaya, jadi satu backtest IDX 1d saja sudah mencetak kembali
-- `no_measurable_edge_1d` lewat INSERT ... ON DUPLICATE KEY UPDATE di
-- GovernanceRepository.record_question.
--
-- Begitu itu terjadi, ketiga proposal era-IDR - termasuk exit-at-target yang
-- VALIDATED - menyambung mulus ke pertanyaan IDX yang baru. Pasar lain, mata
-- uang lain, skedul biaya lain, tanpa satu pun penanda. Tautan rusak yang
-- kelihatan rusak berubah jadi tautan utuh yang salah, dan justru versi salah
-- itu yang terlihat benar.
--
-- Sufiks di bawah membuat sambungan itu mustahil: tidak ada jalur kode yang
-- menghasilkan kunci ber-'@', jadi baris ini tidak akan pernah cocok dengan
-- pertanyaan yang dicetak ulang. Riwayat proposalnya utuh - kunci aslinya
-- masih terbaca di depan '@' - dan asal-usulnya jadi jujur: pertanyaannya
-- dihapus, bukan hilang entah ke mana.
--
-- TIDAK ADA BARIS YANG DIHAPUS DI SINI. Ketiga proposal tetap ada beserta
-- status dan riwayat persetujuannya.
--
-- Akarnya belum tertutup: `question_key` tanpa market juga yang membuat
-- CRYPTO dan IDX bertabrakan di kunci yang sama untuk pertanyaan yang
-- BERBEDA. Memasukkan market ke dalam kunci adalah perubahan tersendiri
-- dengan pembacanya sendiri, dan tidak dititipkan ke migrasi ini.
-- =====================================================================

UPDATE model_proposals p
SET p.question_key = CONCAT(p.question_key, '@lost-0021')
WHERE p.question_key IS NOT NULL
  AND p.question_key NOT LIKE '%@lost-%'
  AND NOT EXISTS (
      SELECT 1 FROM research_questions r WHERE r.question_key = p.question_key
  );
