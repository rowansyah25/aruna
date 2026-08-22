-- =====================================================================
-- ARUNA AI - Market Memory (PASAL 15.2, 15.25, 15.26, 15.27)
--
-- **Proyeksi, bukan salinan.** Tiap baris lahir dari `signal_snapshots` dan
-- `outcome_snapshots` yang keduanya sudah immutable dan sudah berisi 8.914
-- keputusan beserta hasilnya. PASAL 15.27 melarang menyimpan raw market data
-- berulang-ulang, jadi tidak ada satu pun harga, candle, atau hasil polling di
-- sini - hanya sidik jari kondisinya, arah keputusannya, dan nasibnya.
--
-- Alasan tabel ini ada sama sekali, alih-alih JOIN empat tabel tiap tick:
-- pencarian kemiripan membaca ratusan baris per simbol per tick, dan JOIN
-- `signals` x `signal_snapshots` x `outcome_snapshots` untuk itu adalah kerja
-- yang sama diulang 20 kali tiap lima belas menit. Ini indeks, bukan gudang
-- kedua.
--
-- **`signal_id` UNIQUE menegakkan PASAL 15.26 di database, bukan di niat.**
-- Proyeksi yang dijalankan dua kali tidak boleh melahirkan ingatan kedua untuk
-- peristiwa yang sama; `INSERT IGNORE` di repositori bersandar pada kunci ini.
--
-- **Kolom `resolved_at` boleh NULL dan itu disengaja**, tapi barisnya hanya
-- ditulis ketika hasilnya sudah final - lihat `MemoryRepository.proyeksikan`.
-- NULL di sini berarti "resolusinya tidak tercatat", dan PASAL 15.39 menyaring
-- barisnya keluar: yang tidak punya waktu tidak bisa dibuktikan terjadi
-- sebelum keputusan yang sedang dinilai.
-- =====================================================================

CREATE TABLE market_memories (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    signal_id      CHAR(16)      NOT NULL
                   COMMENT 'sama dengan signal_snapshots.signal_id - satu peristiwa, satu ingatan (PASAL 15.26)',
    market_code    VARCHAR(16)   NOT NULL,
    symbol         VARCHAR(32)   NOT NULL,
    timeframe      VARCHAR(8)    NOT NULL,

    -- Delapan dimensi sidik jari yang punya kolom historis. Yang PASAL 15.5
    -- sebut dan tidak pernah tersimpan - volatility, volume, momentum, trend,
    -- open interest, funding, structure - sengaja TIDAK diberi kolom: kolom
    -- yang selalu UNKNOWN adalah kolom yang menyesatkan pembaca berikutnya.
    regime         VARCHAR(24)   NOT NULL DEFAULT 'UNKNOWN',
    risk_level     VARCHAR(24)   NOT NULL DEFAULT 'UNKNOWN',
    news           VARCHAR(24)   NOT NULL DEFAULT 'UNKNOWN',
    quality_band   VARCHAR(24)   NOT NULL DEFAULT 'UNKNOWN',
    liquidity_band VARCHAR(24)   NOT NULL DEFAULT 'UNKNOWN',

    arah           VARCHAR(16)   NOT NULL
                   COMMENT 'keputusan waktu itu: BUY / SELL / WAIT / NO_SIGNAL',
    hasil          VARCHAR(16)   NOT NULL DEFAULT 'UNKNOWN'
                   COMMENT 'WIN / LOSS / NEUTRAL / UNKNOWN - LOSS tidak pernah dihapus (§11.21)',
    move_pct       DECIMAL(12,4) NULL
                   COMMENT 'gerak pasar apa adanya; positif = harga naik, tanpa pembalikan untuk SHORT',

    cakupan        TINYINT       NOT NULL DEFAULT 0
                   COMMENT 'persen bobot dimensi yang benar-benar terbaca (PASAL 15.23)',
    mutu           VARCHAR(8)    NOT NULL DEFAULT 'LOW'
                   COMMENT 'HIGH / MEDIUM / LOW (PASAL 15.24)',
    model_version  VARCHAR(64)   NOT NULL DEFAULT 'UNKNOWN',

    locked_at      DATETIME(6)   NOT NULL
                   COMMENT 'kapan keputusannya dibuat',
    resolved_at    DATETIME(6)   NULL
                   COMMENT 'kapan hasilnya final - PASAL 15.39 menyaring dengan ini',
    created_at     DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_memory_signal (signal_id),
    KEY idx_memory_cari (market_code, timeframe, resolved_at),
    KEY idx_memory_regime (market_code, regime, resolved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Immutability PASAL 15.25 ditegakkan trigger, sama seperti `futures_plans`.
-- Koreksi tetap mungkin - lewat rekaman baru, bukan overwrite. Sebuah ingatan
-- yang bisa disunting adalah jalan memutar untuk "menghapus LOSS" yang tidak
-- menyentuh tabel aslinya sama sekali, jadi tidak akan terlihat oleh siapa pun
-- yang mengawasi `signals`.
-- Bentuknya mengikuti `futures_plans_no_update` dari 0015: satu pernyataan,
-- tanpa DELIMITER, dan MESSAGE_TEXT di bawah 128 karakter - di atas itu MySQL
-- melempar 1648 alih-alih pesannya, dan pesan yang jelas berubah jadi kode
-- yang tidak berarti apa-apa.
CREATE TRIGGER market_memories_no_update
    BEFORE UPDATE ON market_memories
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'market_memories is append-only (PASAL 15.25) - record a correction instead';

-- **Tidak ada trigger no-delete**, dan itu keputusan, bukan kelalaian.
-- `futures_plans` punya satu karena rencananya tidak boleh hilang sama sekali;
-- di sini PASAL 15.28 justru mewajibkan retention policy bisa membuang yang
-- tidak punya nilai analitis. Yang dilarang PASAL 15.25 adalah **mengubah**
-- hasil - menghapus LOSS satu per satu tetap terlarang oleh §11.21, dan yang
-- menahannya adalah tidak adanya satu pun jalur penghapus per baris di kode.
