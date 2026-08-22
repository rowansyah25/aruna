-- =====================================================================
-- ARUNA AI - PHASE 12: pembelajaran adaptif
--
-- Empat tabel, dan tidak satupun menyimpan data pasar mentah. PASAL 12.23
-- melarang Phase 12 menggembungkan database dengan tick, candle duplikat,
-- berita duplikat, atau log polling; yang disimpan di sini adalah HASIL
-- analisis - pola yang ditemukan, strategi dan performanya, dan peristiwa
-- pembelajaran yang perlu bisa diaudit nanti.
--
-- Semua yang ada di sini bisa dihitung ulang dari `signals`, `paper_trades`
-- dan `council_votes`. Itu properti yang disengaja: kalau sebuah baris di sini
-- ternyata salah, ia bisa dibuang dan dibangun ulang tanpa kehilangan satu pun
-- fakta. Tabel-tabel sumber itu yang catatannya, bukan yang ini.
--
-- **Yang TIDAK dilakukan migrasi ini: menyentuh catatan historis.** PASAL 12.1
-- menyatakan historical record bersifat IMMUTABLE, dan tidak ada satu pun
-- ALTER di bawah yang mengubah `signals` atau `signal_snapshots`.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Pola yang ditemukan (PASAL 12.2, 12.3)
-- ---------------------------------------------------------------------
--
-- Satu baris = satu irisan data dan hasilnya. `sample_size` dan `evidence`
-- BUKAN kolom tambahan yang boleh kosong: PASAL 12.3 meminta setiap analisis
-- menampilkan sample size, dan sebuah pola tanpa keduanya adalah persis
-- kesimpulan-dari-tiga-lemparan-koin yang pasal itu larang.
--
-- Korelasi tidak disebut sebab, dan kolomnya menegakkan itu: tidak ada kolom
-- bernama `cause` di sini. Yang ada hanya `dimensions` - irisan apa - dan
-- angka hasilnya.
CREATE TABLE IF NOT EXISTS discovered_patterns (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    pattern_key     VARCHAR(255)    NOT NULL
        COMMENT 'irisan yang dinormalkan: pasangan dimensi=nilai, dipisah pipa',
    dimensions      JSON            NOT NULL
        COMMENT 'irisan dalam bentuk terstruktur; korelasi, bukan sebab',
    wins            INT UNSIGNED    NOT NULL,
    losses          INT UNSIGNED    NOT NULL,
    sample_size     INT UNSIGNED    NOT NULL,
    win_rate        DECIMAL(6,5)    NULL
        COMMENT 'NULL kalau sample nol - nol dari nol bukan nol persen',
    ci_low          DECIMAL(6,5)    NOT NULL COMMENT 'batas bawah selang Wilson',
    ci_high         DECIMAL(6,5)    NOT NULL COMMENT 'batas atas selang Wilson',
    evidence        VARCHAR(24)     NOT NULL
        COMMENT 'INSUFFICIENT_SAMPLE | SUGGESTIVE | STRONG',
    beats_baseline  BOOLEAN         NOT NULL DEFAULT FALSE
        COMMENT 'seluruh selang di atas baseline, bukan sekadar titik tengahnya',
    model_version   VARCHAR(64)     NOT NULL,
    computed_at     DATETIME(6)     NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY discovered_patterns_key (pattern_key, model_version),
    KEY discovered_patterns_evidence (evidence, sample_size),
    CONSTRAINT discovered_patterns_sample_adds_up
        CHECK (sample_size = wins + losses),
    CONSTRAINT discovered_patterns_evidence_allowed
        CHECK (evidence IN ('INSUFFICIENT_SAMPLE', 'SUGGESTIVE', 'STRONG')),
    -- Sebuah pola tidak boleh mengaku mengalahkan baseline tanpa sample yang
    -- cukup. Ditegakkan di database dan bukan hanya di Python: ini satu-satunya
    -- kolom yang bisa membuat ARUNA merekomendasikan sesuatu.
    CONSTRAINT discovered_patterns_no_claim_without_sample
        CHECK (beats_baseline = FALSE OR evidence <> 'INSUFFICIENT_SAMPLE')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Katalog strategi (PASAL 12.7, 12.15)
-- ---------------------------------------------------------------------
--
-- Strategi TIDAK PERNAH dihapus (PASAL 12.15). Yang berubah hanya statusnya,
-- dan `retired_at` mencatat kapan - bukan menghapus barisnya. Sebuah strategi
-- yang dihapus membawa serta seluruh sejarah kekalahannya, dan itu bentuk
-- cherry picking yang paling rapi (PASAL 11.21).
CREATE TABLE IF NOT EXISTS strategies (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code                VARCHAR(32)     NOT NULL COMMENT 'mis. STR-001',
    name                VARCHAR(120)    NOT NULL,
    description         TEXT            NOT NULL,
    conditions          JSON            NOT NULL
        COMMENT 'kondisi yang harus terpenuhi supaya strategi ini relevan',
    preferred_regimes   JSON            NOT NULL,
    preferred_horizons  JSON            NOT NULL,
    status              VARCHAR(16)     NOT NULL DEFAULT 'ACTIVE',
    status_reason       VARCHAR(500)    NULL,
    model_version       VARCHAR(64)     NOT NULL,
    created_at          DATETIME(6)     NOT NULL,
    updated_at          DATETIME(6)     NOT NULL,
    retired_at          DATETIME(6)     NULL
        COMMENT 'kapan dipensiunkan; barisnya tetap tinggal',
    PRIMARY KEY (id),
    UNIQUE KEY strategies_code (code),
    CONSTRAINT strategies_status_allowed CHECK (
        status IN ('ACTIVE', 'DEGRADED', 'UNDER_REVIEW', 'SUSPENDED', 'RETIRED')
    ),
    -- Status yang menuntut penjelasan harus membawanya. "SUSPENDED" tanpa
    -- alasan adalah keputusan yang tidak bisa ditinjau siapa pun nanti.
    CONSTRAINT strategies_reason_when_not_active CHECK (
        status = 'ACTIVE' OR status_reason IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Performa strategi per irisan (PASAL 12.4)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_performance (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    strategy_code   VARCHAR(32)     NOT NULL,
    slice_key       VARCHAR(255)    NOT NULL
        COMMENT 'irisan yang diukur: market|symbol|horizon|direction|regime',
    dimensions      JSON            NOT NULL,
    wins            INT UNSIGNED    NOT NULL,
    losses          INT UNSIGNED    NOT NULL,
    sample_size     INT UNSIGNED    NOT NULL,
    win_rate        DECIMAL(6,5)    NULL,
    ci_low          DECIMAL(6,5)    NOT NULL,
    ci_high         DECIMAL(6,5)    NOT NULL,
    evidence        VARCHAR(24)     NOT NULL,
    net_pnl         DECIMAL(30,12)  NOT NULL DEFAULT 0
        COMMENT 'bersih sesudah biaya; win rate saja tidak menentukan apa pun',
    max_drawdown    DECIMAL(30,12)  NOT NULL DEFAULT 0,
    model_version   VARCHAR(64)     NOT NULL,
    computed_at     DATETIME(6)     NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY strategy_performance_slice
        (strategy_code, slice_key, model_version),
    KEY strategy_performance_code (strategy_code, evidence),
    CONSTRAINT strategy_performance_sample_adds_up
        CHECK (sample_size = wins + losses),
    CONSTRAINT strategy_performance_evidence_allowed
        CHECK (evidence IN ('INSUFFICIENT_SAMPLE', 'SUGGESTIVE', 'STRONG'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Log audit pembelajaran (PASAL 12.22)
-- ---------------------------------------------------------------------
--
-- Hanya peristiwa yang berarti: pola ditemukan, strategi dievaluasi, model
-- dibandingkan, proposal diajukan, disetujui, ditolak, dipromosikan,
-- dipensiunkan. TIDAK ada baris per tick, per perhitungan agent, atau per
-- perdebatan internal - PASAL 12.22 dan 12.23 melarangnya, dan pelanggaran
-- paling mudah terjadi justru di tabel bernama "audit log".
CREATE TABLE IF NOT EXISTS learning_events (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_type      VARCHAR(32)     NOT NULL,
    subject         VARCHAR(255)    NOT NULL
        COMMENT 'apa yang dibicarakan: kode strategi, kunci pola, versi model',
    summary         VARCHAR(500)    NOT NULL,
    evidence        JSON            NULL
        COMMENT 'angka yang menopang peristiwa ini; bukan data pasar mentah',
    sample_size     INT UNSIGNED    NULL,
    model_version   VARCHAR(64)     NOT NULL,
    occurred_at     DATETIME(6)     NOT NULL,
    PRIMARY KEY (id),
    KEY learning_events_type_time (event_type, occurred_at),
    KEY learning_events_subject (subject, occurred_at),
    CONSTRAINT learning_events_type_allowed CHECK (
        event_type IN (
            'PATTERN_DISCOVERED',
            'STRATEGY_EVALUATED',
            'STRATEGY_STATUS_CHANGED',
            'MODEL_COMPARED',
            'REGRESSION_CHECKED',
            'DRIFT_DETECTED',
            'PROPOSAL_SUBMITTED',
            'PROPOSAL_APPROVED',
            'PROPOSAL_REJECTED',
            'MODEL_PROMOTED',
            'MODEL_RETIRED'
        )
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
