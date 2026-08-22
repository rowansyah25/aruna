-- =====================================================================
-- ARUNA AI - jejak pengiriman rencana futures, DI TABELNYA SENDIRI
--
-- **Memperbaiki 0029, yang salah tempat.**
--
-- Migrasi 0029 menambahkan `pushed_at` dan `telegram_message_id` ke
-- `futures_plans` dan menulisnya lewat `UPDATE`. Setiap `UPDATE` ditolak:
--
--     ERROR 1644: futures_plans is append-only: an issued plan cannot change
--                 (FUTURES SPEC 47) - issue a new one
--
-- Trigger `futures_plans_no_update` dari migrasi 0015 menolak tanpa syarat -
-- ia tidak memeriksa kolom mana yang berubah, dan memang tidak seharusnya.
-- Immutability tabel itu bukan kehati-hatian umum: `FuturesRepository.verify`
-- membuktikan baris yang dinilai adalah baris yang diterbitkan, dan seluruh
-- penilaian hasil bergantung padanya. Sebuah kolom yang boleh berubah di tabel
-- itu bertentangan dengan alasan tabel itu ada.
--
-- Akibat kesalahan itu di produksi: rencana tetap terkirim, pengirimannya tidak
-- pernah tercatat, `pushed_message_ids` selalu kosong, dan seluruh hasil
-- tertahan penjaganya - persis kegagalan yang 0029 dimaksudkan memperbaiki.
--
-- **Bentuk yang benar: tabel terpisah, hanya sisip.** Rencananya tetap beku;
-- jejak pengirimannya hidup di sebelahnya. Satu baris per rencana yang
-- benar-benar berangkat.
--
-- `signals` tidak punya trigger seperti ini, jadi 0028 memang sah di sana.
-- Dua jalur, dua bentuk - dan bedanya disebut supaya tidak terlihat seperti
-- ketidakkonsistenan yang lupa dirapikan.
--
-- Kolom dari 0029 dibuang. Semuanya NULL: tidak satu pun `UPDATE` pernah
-- berhasil, jadi tidak ada data yang hilang. Membiarkannya akan meninggalkan
-- dua kolom yang terlihat berarti dan tidak pernah terisi - bentuk paling
-- halus dari kode yang menyesatkan pembaca berikutnya.
-- =====================================================================

CREATE TABLE futures_plan_delivery (
    signal_id           CHAR(16)    NOT NULL,
    pushed_at           DATETIME(6) NOT NULL
                        COMMENT 'kapan rencana ini benar-benar terkirim ke Telegram',
    telegram_message_id BIGINT      NULL
                        COMMENT 'id pesannya, supaya hasilnya bisa membalas; NULL = terkirim tanpa id tercatat',

    PRIMARY KEY (signal_id),
    CONSTRAINT futures_delivery_plan_fk FOREIGN KEY (signal_id)
        REFERENCES futures_plans (signal_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Kunci utamanya `signal_id`: satu rencana terkirim satu kali. Pengiriman
-- kedua untuk rencana yang sama adalah pengulangan, dan `INSERT IGNORE` di
-- repositori membuatnya tidak merusak apa pun - jejak yang pertama yang
-- berlaku, karena itu yang benar-benar dilihat operator.

ALTER TABLE futures_plans
    DROP INDEX idx_futures_plans_pushed,
    DROP COLUMN pushed_at,
    DROP COLUMN telegram_message_id;
