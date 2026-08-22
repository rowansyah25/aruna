-- =====================================================================
-- ARUNA AI - jejak pengiriman rencana futures ke Telegram
--
-- Dilaporkan operator: "saat signal dikirim ke tele gaada resultnya, hilang
-- semua".
--
-- **Sebabnya bukan yang rusak, melainkan yang tidak pernah ada.**
-- `PlanNotifier` punya tepat dua metode: `announce` untuk rencana dan `daily`
-- untuk laporan penutup hari. Tidak ada metode hasil. Jadi rencana futures
-- dikabarkan, horizonnya lewat, hasilnya diskor, disimpan ke
-- `futures_plan_results`, dicatat ke log - dan operator tidak pernah diberi
-- tahu bagaimana akhirnya.
--
-- Terukur: nol pesan hasil futures pernah terkirim, sejak jalur futures ada.
--
-- **Kenapa kolom, bukan ingatan dalam proses.** Rencana futures baru selesai
-- berjam-jam sesudah dikabarkan - horizonnya empat jam pada bentuk yang paling
-- sering - dan penjaga proses menyalakan ulang loop tiap dua puluh empat jam,
-- di luar restart manual. Ingatan yang hilang saat restart berarti hampir
-- setiap hasil datang ke jalur kirim tanpa catatan bahwa rencananya pernah
-- dikabarkan, dan penjaganya akan membungkam semuanya. Itu persis kegagalan
-- yang migrasi ini perbaiki, dibangun ulang dengan bentuk yang berbeda.
--
-- **Dua kolom, masing-masing menjawab tepat satu pertanyaan** - sama dengan
-- 0028 untuk `signals`, dan disengaja sama supaya kedua jalur bisa dibaca
-- dengan cara yang sama:
--
--   `pushed_at`           apakah rencananya benar-benar terkirim, dan kapan
--   `telegram_message_id` pesan mana, supaya hasilnya bisa membalasnya
--
-- Keduanya NULL untuk seluruh baris lama, dan itu benar: tidak ada catatan
-- bahwa baris-baris itu terkirim. Konsekuensinya disebut supaya tidak
-- mengagetkan - hasil dari rencana yang dikabarkan SEBELUM migrasi ini tidak
-- akan didorong, karena tidak ada yang bisa membuktikan operator pernah
-- melihatnya. Mengarang "mungkin terkirim" untuk mereka adalah kesalahan yang
-- sama yang sudah tiga kali ditolak di jalur signal.
-- =====================================================================

ALTER TABLE futures_plans
    ADD COLUMN pushed_at DATETIME(6) NULL
        COMMENT 'kapan rencana ini benar-benar terkirim ke Telegram; NULL = tidak pernah',
    ADD COLUMN telegram_message_id BIGINT NULL
        COMMENT 'id pesan Telegram-nya, supaya hasilnya bisa membalas pesan itu';

-- Dicari per signal_id saat hasilnya diskor, satu kali per hasil.
CREATE INDEX idx_futures_plans_pushed ON futures_plans (pushed_at);
