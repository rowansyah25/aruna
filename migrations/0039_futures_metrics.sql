-- =====================================================================
-- ARUNA AI - Funding rate dan open interest yang tersimpan (bagian 16.2)
--
-- **Kenapa tabel ini ada.** Dua dari tiga belas pemicu bagian 16.2 - anomali
-- funding dan anomali open interest - tidak pernah bisa menyala karena
-- angkanya tidak ada di mana pun. Proses `futures-loop` mengambil keduanya dari
-- Binance REST tiap siklus dan memakainya di memori, lalu membuangnya.
-- `futures_plans.funding_cost_pct` bukan gantinya: ia biaya turunan atas
-- horizon sebuah rencana, bukan rate-nya.
--
-- Diperiksa langsung di skema 2026-08-22: tidak ada satu tabel pun yang memuat
-- keduanya.
--
-- **Satu baris per simbol per siklus, dan tidak lebih.** Pelajaran Phase 15.1
-- yang dibayar mahal: `market_snapshots` menjadi 62% basis data karena tiap
-- amatan ditulis apa adanya. Di sini yang ditulis hanya dua angka yang memang
-- dibaca pemicu, bukan seluruh `FuturesSnapshot`. Dua puluh simbol tiap lima
-- belas menit adalah 1.920 baris sehari; dengan retensi tiga puluh hari,
-- tabelnya berhenti tumbuh di sekitar 57.000 baris.
--
-- **Deret, bukan potret.** Anomali open interest adalah PERUBAHAN, dan
-- perubahan butuh dua titik. Itu sebabnya yang disimpan runtun waktunya dan
-- bukan nilai terakhir saja - satu baris yang ditimpa terus tidak akan pernah
-- bisa menjawab "naik berapa persen".
--
-- `UNIQUE (symbol, captured_at)` menahan siklus yang dijalankan dua kali dari
-- melahirkan deret ganda; repositorinya bersandar padanya lewat `INSERT IGNORE`.
-- =====================================================================

CREATE TABLE futures_metrics (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(32)    NOT NULL,
    captured_at     DATETIME(6)    NOT NULL
                    COMMENT 'kapan ARUNA menerimanya, bukan stempel venue',

    -- Pecahan yang dibayar per interval, BUKAN persen dan bukan tahunan -
    -- rate 0,0001 berarti 0,01% per interval. Menyimpannya sebagai persen di
    -- sini akan membuatnya dibandingkan dengan `EXTREME_RATE` yang bukan
    -- persen, dan selisih seratus kali itu tidak akan melempar apa pun.
    funding_rate    DECIMAL(16,10) NULL,
    funding_time    DATETIME(6)    NULL,
    next_funding_at DATETIME(6)    NULL,

    open_interest   DECIMAL(28,8)  NULL,
    oi_notional     DECIMAL(28,4)  NULL,

    created_at      DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_futures_metrics (symbol, captured_at),
    KEY idx_futures_metrics_deret (symbol, captured_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
