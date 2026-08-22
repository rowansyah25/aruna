-- =====================================================================
-- ARUNA AI - PHASE 13.27: riwayat penilaian risiko
--
-- Satu baris per signal, ditulis SEKALI saat signal dibuat.
--
-- **Kenapa ini harus disimpan, bukan dihitung ulang.** PASAL 13.27 menyatakan
-- historical risk tidak boleh berubah sesudah signal dibuat, dan risk score
-- adalah angka TURUNAN - ia lahir dari bobot yang PASAL 13.29 justru meminta
-- dikalibrasi. Menghitungnya ulang nanti berarti menilai keputusan lama dengan
-- bobot baru, dan kalibrasi yang membandingkan prediksi dengan hasil akan
-- membandingkan prediksi yang tidak pernah dibuat.
--
-- Itu bukan ketidaktelitian; ia membuat kalibrasi mustahil. Sebuah model yang
-- selalu dinilai dengan bobotnya sendiri yang terbaru selalu terlihat
-- terkalibrasi.
--
-- Immutabilitasnya ditegakkan database, bukan kesopanan: trigger di bawah
-- menolak UPDATE. Baris yang salah dihapus dan ditulis ulang oleh operator -
-- yang meninggalkan jejak - bukan diperbaiki diam-diam.
-- =====================================================================

CREATE TABLE IF NOT EXISTS risk_history (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id           VARCHAR(64)     NOT NULL,
    symbol              VARCHAR(64)     NOT NULL,
    market_code         VARCHAR(16)     NOT NULL,

    -- NULL berarti tidak bisa dinilai - bukan nol. PASAL 13.26: yang tidak
    -- terukur dicatat sebagai tidak terukur, dan nol adalah pernyataan bahwa
    -- risikonya tidak ada.
    risk_score          DECIMAL(5,1)    NULL,
    risk_category       VARCHAR(16)     NOT NULL,
    coverage            DECIMAL(5,4)    NOT NULL
        COMMENT 'bagian bobot faktor yang benar-benar terukur',
    vetoed              BOOLEAN         NOT NULL DEFAULT FALSE,
    veto_reason         VARCHAR(255)    NULL,

    gate_decision       VARCHAR(32)     NOT NULL
        COMMENT 'KIRIM | KIRIM DENGAN PERINGATAN | TAHAN',
    gate_reason         VARCHAR(500)    NOT NULL,

    -- Angka yang PASAL 13.27 sebut namanya satu per satu.
    risk_reward         DECIMAL(10,4)   NULL,
    leverage            INT UNSIGNED    NULL,
    liquidation_distance_pct DECIMAL(10,4) NULL,
    correlation_risk    DECIMAL(5,1)    NULL,
    volatility_risk     DECIMAL(5,1)    NULL,
    news_risk           DECIMAL(5,1)    NULL,
    position_size       DECIMAL(30,12)  NULL,

    -- Faktor mentahnya, supaya sebuah penilaian bisa dibaca ulang persis
    -- seperti saat ia dibuat - termasuk faktor mana yang tidak terukur.
    readings            JSON            NOT NULL,

    model_version       VARCHAR(64)     NOT NULL,
    risk_model_version  VARCHAR(64)     NOT NULL
        COMMENT 'versi bobot risiko; kalibrasi tidak boleh melintasi versi',
    assessed_at         DATETIME(6)     NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY risk_history_signal (signal_id),
    KEY risk_history_category (risk_category, assessed_at),
    KEY risk_history_decision (gate_decision, assessed_at),

    CONSTRAINT risk_history_category_allowed CHECK (
        risk_category IN (
            'VERY LOW', 'LOW', 'MEDIUM', 'HIGH', 'VERY HIGH', 'UNKNOWN'
        )
    ),
    CONSTRAINT risk_history_decision_allowed CHECK (
        gate_decision IN ('KIRIM', 'KIRIM DENGAN PERINGATAN', 'TAHAN')
    ),
    -- Skor yang tidak ada harus berkategori UNKNOWN, dan sebaliknya. Dua
    -- kolom yang saling bertentangan adalah dua kebenaran tentang satu
    -- penilaian, dan pembacanya tidak punya cara memilih.
    CONSTRAINT risk_history_unknown_has_no_score CHECK (
        (risk_score IS NULL AND risk_category = 'UNKNOWN')
        OR (risk_score IS NOT NULL AND risk_category <> 'UNKNOWN')
        OR (risk_score IS NOT NULL AND vetoed = TRUE)
    ),
    CONSTRAINT risk_history_veto_has_reason CHECK (
        vetoed = FALSE OR veto_reason IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Immutabilitas PASAL 13.27, ditegakkan database.
--
-- DELETE sengaja TIDAK dilarang - berbeda dengan `audit_logs`. Isi tabel ini
-- adalah hasil analisis yang bisa dihitung ulang dari sumbernya, dan sebuah
-- baris yang lahir dari bug harus bisa dibuang lalu ditulis ulang. Yang tidak
-- boleh adalah mengubahnya di tempat: itu membuat penilaian lama tampak
-- seolah-olah selalu berbunyi begitu, dan kalibrasi yang membandingkannya
-- dengan hasil akan membandingkan sesuatu yang tidak pernah diprediksi.
CREATE TRIGGER risk_history_no_update
    BEFORE UPDATE ON risk_history
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'risk_history is immutable (PASAL 13.27): UPDATE is not permitted';
