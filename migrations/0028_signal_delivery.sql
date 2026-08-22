-- =====================================================================
-- ARUNA AI - jejak pengiriman signal ke Telegram
--
-- Dilaporkan operator: "banyak yang ga ada sinyal tiba-tiba kirim result".
--
-- **Dua pengertian yang sudah bergeser.** Kolom `published` menjawab "apakah
-- prediksi ini LAYAK diterbitkan", dan ia ditulis saat prediksi dikunci. Yang
-- dipakai pesan hasil untuk memutuskan apakah operator pernah melihat signal
-- itu adalah pertanyaan yang berbeda: "apakah ia BENAR-BENAR sampai".
--
-- Keduanya sama selama tidak ada gerbang lain di antaranya. Lalu gerbang itu
-- ada: `SignalNotifier` menolak mendorong signal yang tidak bisa
-- ditindaklanjuti - tanpa entry, stop, target, atau timeframe - karena
-- operator memintanya. Terukur sesudahnya: 80 signal ditahan gerbang itu
-- sementara barisnya tetap `published = TRUE`, jadi hasil dari kedelapan
-- puluhnya tetap didorong. Hasil tanpa signalnya tidak bisa dipakai untuk
-- apa-apa: tidak ada yang bisa diperiksa ulang.
--
-- **Dua kolom, masing-masing menjawab tepat satu pertanyaan.**
--
--   `pushed_at`           apakah ia benar-benar terkirim, dan kapan
--   `telegram_message_id` pesan mana, supaya hasilnya bisa membalasnya
--
-- Dipisah karena satu bisa ada tanpa yang lain: pengirim yang tidak melaporkan
-- id pesannya tetap berhasil mengirim, dan menyimpan satu kolom saja akan
-- memaksa salah satu pertanyaan dijawab dengan tebakan.
--
-- Keduanya NULL untuk seluruh baris lama. Itu benar: tidak ada catatan bahwa
-- baris-baris itu terkirim, dan mengarang "mungkin terkirim" untuk mereka
-- adalah persis kesalahan yang migrasi ini perbaiki.
-- =====================================================================

ALTER TABLE signals
    ADD COLUMN pushed_at DATETIME(6) NULL
        COMMENT 'kapan signal ini benar-benar terkirim ke Telegram; NULL = tidak pernah',
    ADD COLUMN telegram_message_id BIGINT NULL
        COMMENT 'id pesan Telegram-nya, supaya hasilnya bisa membalas pesan itu';

-- Dicari per signal_id saat hasilnya diskor, satu kali per hasil.
CREATE INDEX idx_signals_pushed ON signals (pushed_at);
