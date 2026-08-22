-- 0034: buang `market_snapshots.raw`.
--
-- Terukur 2026-08-21 di basis data produksi: 422.172 baris, rata-rata 513
-- karakter di kolom ini, yaitu sekitar 216 MB - 42% dari seluruh database yang
-- 506 MB. Tidak ada satu pun SELECT di seluruh pohon kode yang membacanya.
--
-- Ketiga pembaca `market_snapshots` (agents/service, bot Telegram, dan
-- permukaan pasar) semuanya membaca baris TERBARU per simbol dan semuanya
-- mengeja kolomnya satu per satu; `raw` tidak ada di antaranya.
--
-- Dibuang, bukan dikosongkan: kolom yang ada dan selalu NULL memberitahu
-- pembaca berikutnya bahwa ia menyimpan sesuatu.
--
-- Bagian 33 spec mengizinkan ini karena tidak ada pembacanya. Bagian 32
-- menuntut backup lebih dulu - lihat catatan eksekusi di
-- docs/superpowers/plans/2026-08-21-phase15-optimasi-database.md.

ALTER TABLE market_snapshots DROP COLUMN raw;
