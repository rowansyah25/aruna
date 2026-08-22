-- =====================================================================
-- ARUNA AI - simpan suara tiap agent bersama sesi council (PASAL 11.10)
--
-- Terukur sebelum migrasi ini ada:
--
--     council_sessions   154
--     agent_decisions      0
--
-- Council berjalan seratus lima puluh empat kali di jalur hidup dan tidak
-- satu pun opini agent tersimpan. `CouncilRepository.save` menulis agregat
-- saja - berapa agent ikut, berapa keberatan diajukan - dan `verdict.opinions`
-- yang memuat suara tiap agent berhenti di memori lalu hilang bersama
-- prosesnya.
--
-- Tabel `agent_decisions` yang sudah ada tidak bisa dipakai: ia menggantung
-- pada `deliberations`, dan satu-satunya yang mengisi `deliberations` adalah
-- perintah CLI `aruna deliberate`. Jalur hidup memakai CouncilService, yang
-- tidak pernah menyentuh keduanya.
--
-- Tanpa baris-baris ini, empat pasal PHASE 11 bukan sulit dibangun melainkan
-- mustahil: 11.2 (keandalan agent), 11.10 (pertanggungjawaban), 11.11 (bobot
-- adaptif), dan bagian best/worst agent di 11.20 semuanya menghitung dari
-- data yang tidak pernah ditulis. Papan peringkat agent yang dibuat tanpa ini
-- hanya bisa berupa karangan, dan karangan itu akan terbaca sama meyakinkannya
-- dengan yang benar.
--
-- Rantai ke outcome tidak butuh kolom tambahan; ia sudah lengkap begitu tabel
-- ini ada:
--
--     council_votes -> council_sessions.id
--                   <- signal_snapshots.council_session_id
--                   -> signals.signal_id -> paper_results / paper_trades
--
-- Append-only tidak dipasang di sini. Baris ini ditulis satu kali bersama
-- sesinya lewat INSERT ... ON DUPLICATE KEY UPDATE, dan sesi yang sama bisa
-- ditulis ulang saat replay - lihat `council_sessions` yang berperilaku sama.
-- Yang tidak boleh berubah adalah prediksi terkunci, dan itu dijaga di
-- `signals` beserta trigger-nya (PASAL 11.14, 11.21).
-- =====================================================================

CREATE TABLE council_votes (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    council_session_id BIGINT UNSIGNED NOT NULL,

    role               VARCHAR(32)  NOT NULL,
    decision           VARCHAR(24)  NOT NULL,
    confidence         DECIMAL(6,3) NOT NULL DEFAULT 0,

    -- Abstain adalah jawaban yang sah: agent tidak punya bukti untuk dinilai.
    -- Dipisahkan dari "menentang" karena menghitungnya sebagai penentangan
    -- membuat feed yang mati terbaca sebagai council yang terbelah.
    abstained          BOOLEAN      NOT NULL DEFAULT FALSE,

    -- Apakah agent ini berada di sisi putusan akhir, dinilai memakai kosakata
    -- publik: WAIT dan NO_SIGNAL keduanya berarti "tidak ada posisi", jadi
    -- agent yang bilang WAIT saat council memutuskan NO_SIGNAL sepakat.
    --
    -- Disimpan, bukan dihitung ulang saat dibaca. Pemetaannya adalah keputusan
    -- aplikasi yang bisa berubah, dan statistik keandalan harus memakai
    -- penilaian yang berlaku SAAT ITU - bukan penilaian hari ini yang
    -- dipaksakan mundur ke catatan lama.
    agreed_with_council BOOLEAN     NOT NULL,

    reasoning          JSON         NOT NULL,
    evidence           JSON         NULL,
    evidence_count     INT UNSIGNED NOT NULL DEFAULT 0,

    created_at         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY council_votes_unique (council_session_id, role),
    KEY council_votes_role_idx (role, decision),
    KEY council_votes_agreement_idx (role, agreed_with_council),

    CONSTRAINT council_votes_session_fk FOREIGN KEY (council_session_id)
        REFERENCES council_sessions (id) ON DELETE CASCADE,

    CONSTRAINT council_votes_decision_allowed CHECK (
        decision IN ('BUY', 'SELL', 'WAIT', 'NO_SIGNAL', 'UNKNOWN_MARKET')
    ),
    CONSTRAINT council_votes_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    -- Agent yang abstain tidak boleh dicatat sebagai sepakat. Ia tidak
    -- menyatakan apa pun, dan menghitungnya sebagai dukungan akan menaikkan
    -- angka "setuju" dengan suara yang tidak pernah diberikan.
    CONSTRAINT council_votes_abstain_not_agreement CHECK (
        abstained = FALSE OR agreed_with_council = FALSE
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
