-- =====================================================================
-- ARUNA AI - FUTURES: hasil ghost signal untuk WAIT
--
-- `futures_plan_results` (0015) menyimpan bagaimana sebuah POSISI berakhir:
-- TARGET_HIT, STOPPED_OUT, LIQUIDATED, EXPIRED. Sebuah WAIT tidak pernah
-- jadi posisi, jadi tidak satu pun dari kolom itu berlaku untuknya - tidak
-- ada exit price, tidak ada jarak adverse dari sebuah entry yang tidak
-- pernah ada.
--
-- Yang bisa diukur dari sebuah WAIT adalah pertanyaan yang berbeda: apakah
-- ada move yang cukup besar untuk menutupi biaya round trip, selama jendela
-- yang ARUNA pilih untuk menepi. Bukan "apakah kita akan menang" - WAIT
-- tidak menyatakan arah, jadi tidak ada hasil yang bisa membenarkannya.
--
-- Tabel terpisah, bukan kolom tambahan di tabel sebelah, karena bentuknya
-- memang berbeda. Memaksakan keduanya jadi satu berarti separuh kolomnya
-- selalu NULL dan pembaca harus tahu mana yang berlaku untuk baris mana.
-- =====================================================================

CREATE TABLE futures_ghost_results (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id       CHAR(16)       NOT NULL,
    verdict         VARCHAR(16)    NOT NULL,
    reference_price DECIMAL(30,12) NOT NULL,
    -- Move terbesar ke arah mana pun, persen dari harga referensi.
    max_move_pct    DECIMAL(14,4)  NULL,
    findings        JSON           NOT NULL,
    resolved_at     DATETIME(6)    NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY futures_ghost_results_signal_id (signal_id),
    KEY futures_ghost_results_verdict_idx (verdict, resolved_at),

    CONSTRAINT futures_ghost_results_plan_fk FOREIGN KEY (signal_id)
        REFERENCES futures_plans (signal_id) ON DELETE RESTRICT,
    CONSTRAINT futures_ghost_results_verdict_allowed CHECK (
        verdict IN ('QUIET', 'MOVE_EXISTED', 'UNKNOWN')
    ),
    -- UNKNOWN berarti tidak ada yang bisa diukur, jadi tidak boleh membawa
    -- angka. Sebaliknya, verdict yang terukur harus membawanya - kalau tidak,
    -- baris itu mengaku tahu sesuatu yang tidak pernah dihitung.
    CONSTRAINT futures_ghost_results_unknown_is_empty CHECK (
        verdict <> 'UNKNOWN' OR max_move_pct IS NULL
    ),
    CONSTRAINT futures_ghost_results_measured_has_a_number CHECK (
        verdict = 'UNKNOWN' OR max_move_pct IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
