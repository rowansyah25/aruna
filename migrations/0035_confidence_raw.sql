-- 0035: simpan keyakinan MENTAH di samping yang dinyatakan.
--
-- Bagian 9 spec menuntut keyakinan yang dinyatakan sesuai kenyataan: "confidence
-- 80%" harus berarti keberhasilan mendekati 80%. Terukur 2026-08-21, syarat itu
-- dilanggar dengan arah terbalik - pita >=90% menang 47,7% sementara pita <50%
-- menang 55,2%.
--
-- Mulai sekarang `confidence` adalah angka TERKALIBRASI, yaitu yang dinyatakan
-- kepada operator. Kolom ini menyimpan keluaran model sebelum dipetakan.
--
-- Keduanya perlu, dan bukan demi kerapian: pengukuran kalibrasi berikutnya
-- HARUS memakai yang mentah. Kalibrasi yang mengukur keluarannya sendiri akan
-- melaporkan bahwa semuanya baik-baik saja pada putaran kedua, dan makin
-- meyakinkan justru makin salah.
--
-- NULL untuk baris lama: prediksi yang dikunci sebelum kalibrator ada memang
-- tidak punya nilai mentah yang terpisah, dan pembacanya memakai
-- COALESCE(confidence_raw, confidence).

ALTER TABLE signal_snapshots
    ADD COLUMN confidence_raw DECIMAL(6,3) NULL AFTER confidence;
