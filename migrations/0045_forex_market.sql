-- =====================================================================
-- ARUNA AI - Buka market FOREX untuk modul XAUUSD M5
--
-- Audit 2026-08-27 menemukan forex dijaga empat lapis, dan lapisan storage
-- inilah yang paling keras: komentar aslinya di `0001_core.sql` berbunyi
-- "no code path, not even direct SQL, can add a third market". Itu memang
-- tujuannya - mencegah forex masuk TANPA SENGAJA. Operator sekarang
-- memasukkannya DENGAN SENGAJA untuk XAUUSD M5, jadi penjaganya
-- DIPERSEMPIT, bukan dicabut: tiga kode sah, sisanya tetap mati di storage.
--
-- Alias `FX`, `CURRENCY`, `FOREIGN_EXCHANGE` sengaja TIDAK ditambahkan.
-- Satu ejaan sah berarti `WHERE market_code = 'FOREX'` tidak pernah
-- diam-diam melewatkan baris - dan `parse_market()` di Python menolak
-- ketiganya dengan pesan yang menyuruh menulis FOREX.
--
-- `is_continuous = TRUE` karena valas tidak punya jeda sesi HARIAN seperti
-- IDX; ia berjalan menerus dari Minggu 22:00 UTC sampai Jumat 22:00 UTC.
-- Akhir pekan tetap tutup, dan itu BUKAN urusan kolom ini - pengenal sesi
-- dan status buka/tutup diisi belakangan lewat `Snapshot.session` dan
-- `Snapshot.market_open`, yang sampai saat itu bernilai NULL alias
-- "belum diukur".
--
-- `FOREX_METAL` ditambahkan ke kelas aset yang sah. XAU/USD adalah logam
-- yang diperdagangkan di pasar valas; menumpangkannya ke `CRYPTO_SPOT`
-- akan membuat setiap kueri per-kelas berbohong.
--
-- `lot_size` NULL karena ARUNA analyst-only dan tidak pernah menghitung
-- ukuran lot broker. NULL di sini berarti "tidak berlaku", bukan "belum
-- diukur" - dan `assets_lot_size_positive` memang mengizinkan NULL.
-- =====================================================================

ALTER TABLE markets DROP CHECK markets_code_allowed;

ALTER TABLE markets
    ADD CONSTRAINT markets_code_allowed
    CHECK (code IN ('CRYPTO', 'IDX', 'FOREX'));

ALTER TABLE assets DROP CHECK assets_class_allowed;

ALTER TABLE assets
    ADD CONSTRAINT assets_class_allowed
    CHECK (asset_class IN ('CRYPTO_SPOT', 'CRYPTO_PERP', 'IDX_EQUITY', 'FOREX_METAL'));

INSERT INTO markets (code, display_name, timezone, is_continuous, quote_currency)
VALUES ('FOREX', 'Foreign Exchange', 'UTC', TRUE, 'USD')
ON DUPLICATE KEY UPDATE code = code;

INSERT INTO assets (
    market_code, symbol, display_name, asset_class,
    base_asset, quote_asset, lot_size, metadata
)
VALUES (
    'FOREX', 'XAU/USD', 'Gold Spot vs US Dollar', 'FOREX_METAL',
    'XAU', 'USD', NULL, JSON_OBJECT('timeframe_primer', '5m')
)
ON DUPLICATE KEY UPDATE market_code = market_code;
