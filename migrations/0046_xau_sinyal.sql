-- =====================================================================
-- ARUNA AI - Penyimpanan keputusan XAUUSD M5
--
-- Tiga tabel, dan yang PERTAMA menyimpan penolakan juga.
--
-- **`alasan_kosong` adalah kolom terpenting di sini**, pola yang sama dengan
-- `router_pilihan`. "Tidak ada sinyal karena memang tak ada setup" dan "tidak
-- ada sinyal karena feed mati" terlihat SAMA PERSIS dari luar - yang pertama
-- normal, yang kedua kerusakan. Tanpa kolom ini, laporan "XAU diam hari ini"
-- tidak bisa dibantah, dan tidak ada yang bisa dipelajari darinya.
--
-- Dan ia akan sering terisi. Diukur lewat probe 2026-08-27 atas data uji:
-- dewan mengembalikan WAIT di ketiga bentuk pasar yang dicoba, karena
-- sebagian besar agen abstain - XAU tidak punya volume (valas spot tidak
-- menerbitkannya), tidak punya berita yang dirangkai, dan tidak punya
-- fundamental. NO SIGNAL adalah keluaran yang WAJAR di sini, bukan kegagalan
-- yang perlu disembunyikan.
--
-- **Kosakata ditegakkan di storage, bukan cuma di Python.** `keputusan` hanya
-- menerima BUY, SELL, NO_SIGNAL; `suara` hanya AGREE, DISAGREE, NEUTRAL. Spec
-- melarang WAIT di modul XAU, dan sebuah CHECK berarti larangan itu berlaku
-- juga bagi penulis SQL langsung yang tidak pernah melewati `suara.py`.
--
-- **NULL berarti TIDAK DIUKUR, tidak pernah nol.** `confidence` NULL saat
-- dewan tak sempat menilai; `kontradiksi` NULL saat tak ada agen yang
-- mengambil arah - itu berbeda dari kontradiksi yang diukur lalu hasilnya nol,
-- dan menyamakan keduanya akan membuat sinyal yang tak seorang pun mendukung
-- terbaca sebagai kesepakatan bulat. `spread_bps` NULL karena Twelve Data
-- memang tidak menerbitkan bid/ask - diukur 2026-08-27 - dan `spread_diukur`
-- yang membedakan "tidak aktif" dari "lulus".
--
-- **Tidak pernah ditimpa.** Tidak ada ON DUPLICATE KEY UPDATE, dengan alasan
-- yang sama seperti `futures_plans`: penulisan kedua atas setup dan bar yang
-- sama adalah upaya mengubah keputusan yang sudah diambil, dan itu harus gagal
-- keras alih-alih diam-diam menang. Siklus yang berjalan dua kali pada bar
-- yang sama ditolak, bukan menghasilkan dua baris.
--
-- **LOSS tidak pernah dihapus** (spec). Tidak ada kolom, indeks, atau
-- constraint di sini yang memperlakukan hasil rugi berbeda dari hasil untung;
-- tabel hasilnya sendiri dibangun di Rencana 3.
-- =====================================================================

CREATE TABLE IF NOT EXISTS xau_predictions (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(32)  NOT NULL,

    -- Penanda satu gagasan: simbol, arah, level yang dituju. Sengaja TIDAK
    -- memuat waktu - penanda yang memuat waktu berbeda tiap bar dan cooldown
    -- tidak akan pernah menahan apa pun.
    setup_id        VARCHAR(96)  NOT NULL,

    -- Close bar M5 tersettle terbaru yang mendasari keputusan. BUKAN jam
    -- sistem dan BUKAN kapan datanya ditarik: keputusan yang berdiri di atas
    -- bar sejam lalu tidak menjadi segar karena permintaannya baru dikirim.
    as_of           DATETIME(6)  NOT NULL,
    decided_at      DATETIME(6)  NOT NULL,

    keputusan       VARCHAR(16)  NOT NULL,

    -- Kosong (NULL) berarti ADA sinyal. Terisi berarti tidak, dan sebabnya
    -- ada di sini lengkap dengan angkanya.
    alasan_kosong   VARCHAR(255) NULL,

    confidence      DECIMAL(6,4) NULL
        COMMENT 'NULL = dewan tak sempat menilai, bukan nol keyakinan',

    -- Rekap suara. Netral tidak masuk penyebut kontradiksi: sepuluh agen diam
    -- bukan sepuluh agen bertengkar.
    setuju          SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    menentang       SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    netral          SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    kontradiksi     DECIMAL(6,4) NULL
        COMMENT 'NULL = tak ada yang bersuara; berbeda dari nol kontradiksi',

    -- Geometri. Seluruhnya NULL saat tidak ada level struktur di arah tujuan -
    -- keadaan yang berarti jaraknya TIDAK DIKETAHUI, bukan nol.
    entry           DECIMAL(24,8) NULL,
    stop            DECIMAL(24,8) NULL,
    target          DECIMAL(24,8) NULL,
    atr             DECIMAL(24,8) NULL,
    rr              DECIMAL(10,4) NULL,
    target_atr      DECIMAL(10,4) NULL
        COMMENT 'jarak target dalam satuan ATR; lantainya 2,0',
    sentuhan_target SMALLINT UNSIGNED NULL
        COMMENT 'berapa kali level target disentuh - bukti kekuatannya',

    spread_bps      DECIMAL(10,4) NULL,
    spread_diukur   BOOLEAN      NOT NULL DEFAULT FALSE
        COMMENT 'FALSE = gerbang spread TIDAK AKTIF, bukan lulus',

    model_version   VARCHAR(32)  NOT NULL,
    created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_xau_setup_bar (setup_id, as_of),
    KEY idx_xau_baca (symbol, as_of DESC),
    -- Untuk "berapa sering XAU diam, dan kenapa" tanpa memindai seluruh tabel.
    KEY idx_xau_kosong (as_of, keputusan),

    CONSTRAINT xau_keputusan_allowed
        CHECK (keputusan IN ('BUY', 'SELL', 'NO_SIGNAL')),
    -- Sinyal berarah wajib punya geometri lengkap; tanpa target tak ada yang
    -- bisa dinilai benar atau salah di Rencana 3.
    CONSTRAINT xau_arah_punya_geometri CHECK (
        keputusan = 'NO_SIGNAL'
        OR (entry IS NOT NULL AND stop IS NOT NULL AND target IS NOT NULL)
    ),
    -- NO SIGNAL WAJIB menyebut sebabnya. Sebuah penolakan tanpa alasan tidak
    -- bisa dibantah dan tidak bisa dipelajari - itu seluruh gunanya kolom ini.
    CONSTRAINT xau_kosong_wajib_beralasan CHECK (
        keputusan <> 'NO_SIGNAL' OR alasan_kosong IS NOT NULL
    ),
    -- Dan sebaliknya: sinyal yang terbit tidak boleh membawa alasan kosong.
    -- Sebuah baris BUY dengan alasan_kosong terisi berarti dua bagian kode
    -- tidak sepakat tentang apa yang baru saja diputuskan.
    CONSTRAINT xau_arah_tanpa_alasan CHECK (
        keputusan = 'NO_SIGNAL' OR alasan_kosong IS NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- Bukti yang mendasari satu keputusan, per timeframe.
--
-- Disimpan supaya keputusan bisa DIPUTAR ULANG. Sebuah prediksi yang salah
-- tanpa buktinya cuma memberi tahu bahwa ia salah; dengan buktinya, ia memberi
-- tahu kenapa.
--
-- `sample_size` dan `required` ikut disimpan karena sebuah indikator yang
-- bahannya kurang BUKAN indikator yang nilainya kecil - `andal` adalah
-- perbandingan keduanya, dan tanpa keduanya ia tidak bisa dihitung ulang.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xau_evidence (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    prediction_id BIGINT UNSIGNED NOT NULL,
    horizon_code  VARCHAR(8)   NOT NULL COMMENT '5m, 15m, 1h, 4h',
    nama          VARCHAR(64)  NOT NULL,
    nilai         DECIMAL(24,8) NULL
        COMMENT 'NULL = tidak terhitung, bukan nol',
    sample_size   INT UNSIGNED NOT NULL DEFAULT 0,
    required      INT UNSIGNED NOT NULL DEFAULT 0,

    UNIQUE KEY uq_xau_evidence (prediction_id, horizon_code, nama),
    KEY idx_xau_evidence_nama (nama, horizon_code),

    -- RESTRICT, bukan CASCADE. Spec melarang menghapus hasil, dan sebuah
    -- cascade justru membuat penghapusan MUDAH: satu DELETE atas prediksi
    -- akan menyapu bukti dan suaranya tanpa perlawanan. RESTRICT membuat
    -- penghapusan gagal selama buktinya masih ada, yang di sini adalah
    -- perilaku yang diinginkan - bukan halangan.
    CONSTRAINT xau_evidence_prediction_fk FOREIGN KEY (prediction_id)
        REFERENCES xau_predictions (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- Satu baris per agen per keputusan.
--
-- `suara` adalah sikap terhadap arah yang diusulkan; `decision` adalah apa
-- yang agen itu sendiri katakan. Keduanya disimpan karena keduanya menjawab
-- pertanyaan berbeda: yang pertama untuk mengukur kontradiksi, yang kedua
-- untuk menilai agen itu sendiri di Rencana 3.
--
-- `abstained` dipisah dari suara NEUTRAL karena tidak sama: seorang agen bisa
-- mengembalikan WAIT tanpa abstain - menahan diri dengan alasan - dan itu
-- berbeda dari tidak punya bahan sama sekali.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xau_agent_votes (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    prediction_id BIGINT UNSIGNED NOT NULL,
    role          VARCHAR(32)  NOT NULL,
    suara         VARCHAR(16)  NOT NULL,
    decision      VARCHAR(16)  NOT NULL
        COMMENT 'kosakata dewan apa adanya, termasuk WAIT - bukan keluaran XAU',
    confidence    DECIMAL(6,4) NULL,
    abstained     BOOLEAN      NOT NULL DEFAULT FALSE,

    UNIQUE KEY uq_xau_vote (prediction_id, role),
    KEY idx_xau_vote_role (role, suara),

    CONSTRAINT xau_suara_allowed
        CHECK (suara IN ('AGREE', 'DISAGREE', 'NEUTRAL')),
    -- RESTRICT dengan alasan yang sama seperti `xau_evidence`.
    CONSTRAINT xau_votes_prediction_fk FOREIGN KEY (prediction_id)
        REFERENCES xau_predictions (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
