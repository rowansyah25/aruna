-- =====================================================================
-- ARUNA AI - Putusan saat horizon XAU habis
--
-- **Lubang yang ditutup:** sebuah sinyal yang horizonnya lewat tanpa target
-- maupun stop tersentuh sebelumnya berakhir dalam DIAM. Operator dikabari
-- saat sinyal terbit, dikabari tiap keadaannya berganti, lalu ditinggal
-- memegang sesuatu tanpa keterangan apa pun tepat di titik keputusan.
--
-- Itu justru keadaan tempat kerugian paling sering dibiarkan tumbuh: bukan
-- saat stop tersentuh - itu jelas - melainkan saat tidak ada yang terjadi dan
-- tidak ada yang mengatakan apa-apa.
--
-- `HORIZON_HABIS` karena itu ditambahkan sebagai keadaan yang sah, dan
-- pesannya SELALU berisi putusan: tahan atau tutup, tidak pernah keduanya
-- dan tidak pernah kosong.
--
-- Dasarnya dua pertanyaan berurutan, keduanya terukur:
--
--   1. level yang jadi alasannya masih terbaca?  tidak -> TUTUP
--      (gagasannya bukan lambat, ia sudah tidak ada)
--   2. arahnya benar?                            tidak -> TUTUP
--      (bertahan pada bacaan yang terbukti meleset adalah menahan kerugian
--       demi terlihat konsisten)
--   selain itu                                        -> TAHAN
--
-- `tahan` disimpan terpisah dari `keadaan` karena ia yang dicari saat menilai
-- kembali: berapa kali ARUNA menyuruh menahan, dan berapa di antaranya
-- ternyata benar. Tanpa kolomnya, pertanyaan itu harus dijawab dengan mengurai
-- teks alasan.
-- =====================================================================

ALTER TABLE xau_kabar
    DROP CHECK xau_kabar_keadaan_allowed;

ALTER TABLE xau_kabar
    ADD CONSTRAINT xau_kabar_keadaan_allowed CHECK (
        keadaan IN ('BERJALAN', 'MENDEKAT_TARGET', 'MENDEKAT_STOP',
                    'TESIS_BATAL', 'HAMPIR_HABIS', 'HORIZON_HABIS')
    );

ALTER TABLE xau_kabar
    ADD COLUMN tahan TINYINT(1) NULL
        COMMENT 'putusan saat HORIZON_HABIS: 1 tahan, 0 tutup; NULL = bukan penutup';
