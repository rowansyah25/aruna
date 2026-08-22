-- =====================================================================
-- ARUNA AI - crypto pindah ke Binance spot, USDT only; market_ticks dibuang
--
-- Dua hal terjadi di file yang sama karena keduanya menyentuh baris yang
-- sama: pasangan crypto IDR yang dihapus adalah pemilik sebagian besar baris
-- market_ticks, dan menjalankan keduanya terpisah berarti CASCADE menyalin
-- pekerjaan yang sebentar lagi di-DROP.
--
-- MIGRASI INI MENGHAPUS DATA DAN TIDAK BISA DIBATALKAN.
-- Cadangan diambil sebelum ini: backup/aruna_sebelum_binance_2026-08-17.sql.
-- Operator memutuskan riwayat IDR dihapus seluruhnya, dengan sadar, setelah
-- konsekuensinya dijelaskan. Yang di bawah ini adalah catatan APA yang
-- dihapus, supaya keputusan itu bisa diaudit - bukan permintaan izin ulang.
--
-- ---------------------------------------------------------------------
-- BAGIAN 1 - market_ticks (PASAL 26)
-- ---------------------------------------------------------------------
-- PASAL 26: SQL adalah LONG-TERM ANALYSIS MEMORY, bukan pita rekaman setiap
-- observasi. market_ticks adalah pita rekaman itu: 76.567 baris.
--
-- Yang membuat penghapusannya murah, dan ini diperiksa bukan diasumsikan:
-- tabel ini WRITE-ONLY. Dua satu-satunya pembacanya di kode - latest_tick()
-- dan quality_breakdown() di MarketDataRepository - punya NOL pemanggil di
-- seluruh src/ maupun tests/. Tidak ada satu pun analisis yang pernah
-- membaca kembali satu baris pun dari 76.567 itu.
--
-- Yang TIDAK hilang: verdict kualitas per observasi masih dihitung gate
-- untuk setiap observasi, masih tersimpan di baris market_snapshots, dan
-- penolakan masih menulis QUALITY_REJECTED ke provider_events. Yang hilang
-- hanya salinan per-tick-nya.
--
-- Yang JUJUR harus disebut: membuang tabel ini TIDAK membuat ARUNA patuh
-- PASAL 26 sepenuhnya. market_snapshots masih ditulis satu baris per poll
-- per aset tanpa sampling (55.035 baris untuk crypto IDR saja). Bedanya ia
-- PUNYA pembaca hidup - Telegram /crypto, /btc, dan health check - jadi
-- membuangnya adalah keputusan tentang pembaca-pembaca itu, bukan efek
-- samping migrasi ini. Itu tetap gap. Ia dilaporkan, tidak ditambal diam.
--
-- ---------------------------------------------------------------------
-- BAGIAN 2 - riwayat crypto IDR (PASAL 6, 33)
-- ---------------------------------------------------------------------
-- Sumber crypto sekarang Binance spot, yang tidak melisting satu pun pasangan
-- ini terhadap rupiah. Jadi BTC/IDR dkk bukan sekadar tidak diinginkan: tidak
-- ada adapter yang bisa mengambilnya lagi. Membiarkan barisnya berarti lima
-- aset yang tampak terkonfigurasi dan selamanya mengembalikan nol.
--
-- Urutan DELETE di bawah bukan gaya penulisan. signal_snapshots memakai FK
-- ON DELETE RESTRICT ke assets, dan delapan tabel memakai RESTRICT ke
-- signal_snapshots. Menghapus assets lebih dulu GAGAL di tengah jalan, dan
-- migrasi MySQL tidak atomik - DDL commit implisit - jadi kegagalan di tengah
-- meninggalkan setengah skema. Anak dulu, induk terakhir.
--
-- ---------------------------------------------------------------------
-- BAGIAN 3 - jebakan yang tidak berbunyi
-- ---------------------------------------------------------------------
-- assets sudah berisi KEDUA set: id 1-5 (BTC/USDT..XRP/USDT, enabled=0) dan
-- id 17-21 (pasangan IDR, enabled=1). UNIQUE KEY (market_code, symbol)
-- berarti `aruna seed` meng-UPDATE baris 1-5, tidak membuat baris baru - dan
-- UniverseRepository.upsert_asset DENGAN SENGAJA tidak menyentuh kolom
-- enabled, supaya aset yang dimatikan operator tidak hidup lagi sendiri.
--
-- Akibatnya, tanpa UPDATE eksplisit di akhir file ini: hapus yang IDR, seed
-- yang USDT, semuanya melaporkan sukses, dan crypto punya NOL aset aktif.
-- Gejala satu-satunya adalah satu baris "no enabled assets for CRYPTO; run:
-- aruna seed" - saran yang tidak akan pernah menolong, karena seed sudah
-- dijalankan dan memang berhasil. Itu kegagalan senyap, bukan crash.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. market_ticks. Didahulukan supaya CASCADE dari assets tidak perlu
--    menghapus 76.567 baris di tabel yang detik berikutnya di-DROP.
-- ---------------------------------------------------------------------
DROP TABLE market_ticks;


-- ---------------------------------------------------------------------
-- 2. futures_plans: SENGAJA TIDAK DISENTUH. Dicatat, bukan diperbaiki.
--
--    229 dari 292 plan menunjuk council_session_id milik aset IDR, dan
--    futures_plans TIDAK punya foreign key ke sana. CASCADE di langkah 7
--    menghapus sesi-sesi itu dan meninggalkan 229 id yang menunjuk ke baris
--    yang tidak ada lagi.
--
--    Versi pertama migrasi ini mencoba merapikannya: NULL-kan tautannya dan
--    tulis alasannya ke caveats. Percobaan itu DITOLAK database, dan
--    penolakannya benar - trigger futures_plans_no_update:
--
--      "futures_plans is append-only: an issued plan cannot change
--       (FUTURES SPEC 47) - issue a new one"
--
--    Plan yang sudah diterbitkan tidak boleh berubah, titik. Menonaktifkan
--    penjaga itu untuk satu migrasi yang "rapi" berarti menyatakan penjaga
--    itu bisa ditawar, dan penjaga yang bisa ditawar tidak menjaga apa pun.
--    Plan-nya sendiri juga masih catatan yang BENAR: symbol BTCUSDT /
--    ETHUSDT / SOLUSDT, entry/stop/target dalam USDT terhadap harga mark.
--    Yang rusak cuma tautan ke debat.
--
--    Jadi konsekuensinya diterima dan ditulis di sini, bukan disembunyikan:
--    untuk 229 plan itu, council_session_id terisi tapi sesinya tidak ada.
--    Pembaca yang menelusuri plan -> debat akan menemukan NOL, dan tidak
--    bisa membedakannya dari "debatnya memang tidak pernah disimpan". Tidak
--    ada pembaca seperti itu hari ini - tidak satu pun query di
--    db/repositories/futures.py men-JOIN kolom ini ke council_sessions -
--    tapi PASAL 25 (loss autopsy) adalah pembaca seperti itu kalau
--    dibangun. Yang membangunnya harus membaca paragraf ini lebih dulu:
--    id 1..229 yang menggantung di sini dihapus pada 2026-08-17, sengaja.
-- ---------------------------------------------------------------------


-- ---------------------------------------------------------------------
-- 3. Anak-anak signal_snapshots (FK RESTRICT). Jumlah terukur sebelum
--    migrasi: counterfactuals 0, ghost_signals 0, loss_autopsies 0,
--    outcome_snapshots 451, paper_results 111, paper_trades 11,
--    replay_checks 16, signals 156.
--
--    paper_results dan paper_trades ikut hilang, dan itu memang yang
--    diputuskan. Sekalian dicatat: sisa-sisanya pun tidak akan sebanding
--    dengan hasil baru - biaya crypto turun dari 0,30% jadi 0,10% per sisi
--    bersamaan dengan migrasi ini, jadi PnL lama dan baru dihitung dengan
--    skedul biaya yang berbeda.
-- ---------------------------------------------------------------------
DELETE x FROM counterfactuals x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM ghost_signals x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM loss_autopsies x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM outcome_snapshots x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM paper_results x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM paper_trades x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM replay_checks x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

DELETE x FROM signals x
    JOIN signal_snapshots ss ON ss.signal_id = x.signal_id
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';


-- ---------------------------------------------------------------------
-- 4. signal_snapshots itu sendiri (156 baris).
--
--    Ini menembus penjaga SPEC 20, dan itu harus dinyatakan terang-terangan,
--    bukan dilakukan diam-diam.
--
--    signal_snapshots dilindungi dua trigger: no_update ("a locked prediction
--    cannot be changed") dan no_delete ("a locked prediction cannot be
--    deleted"). Keduanya ada supaya kode aplikasi TIDAK PERNAH bisa menghapus
--    atau menyunting prediksi yang sudah dikunci - itu premis seluruh sistem.
--
--    Yang dilakukan di sini, dan batasnya:
--
--      * HANYA no_delete yang dicabut. no_update TIDAK disentuh sama sekali,
--        jadi jaminan "prediksi terkunci tidak bisa DIUBAH" tidak pernah
--        bolong satu detik pun. Yang bolong hanya "tidak bisa DIHAPUS", dan
--        hanya selama satu statement DELETE di bawah.
--      * Trigger-nya dipasang kembali PERSIS seperti aslinya di 0008, dengan
--        MESSAGE_TEXT yang sama huruf per huruf. Kalau statement pemasangan
--        ulang itu gagal, migrasi berhenti dan tidak tercatat - dan operator
--        harus tahu bahwa database saat itu berada dalam keadaan tanpa
--        penjaga hapus sampai diperbaiki tangan.
--
--    Kenapa boleh sama sekali: operator memutuskan riwayat IDR dihapus
--    seluruhnya, sadar, sesudah konsekuensinya dijelaskan, dengan cadangan
--    sudah diambil. Migrasi ber-checksum dan forward-only adalah satu-satunya
--    jalur di proyek ini yang bisa membawa keputusan seperti itu sambil
--    meninggalkan jejak. Kode aplikasi tetap tidak bisa, dan tidak boleh
--    bisa.
--
--    Apa yang hilang, supaya tidak ada yang mengira ini gratis: 156 prediksi
--    terkunci beserta seluruh jejak penilaiannya. Kalibrasi, autopsi, dan
--    setiap statistik menang/kalah crypto sekarang mulai dari nol sampel.
--    Nol di laporan crypto sesudah ini berarti "belum ada", bukan "bersih".
-- ---------------------------------------------------------------------
DROP TRIGGER signal_snapshots_no_delete;

DELETE ss FROM signal_snapshots ss
    JOIN assets a ON a.id = ss.asset_id
WHERE a.market_code = 'CRYPTO' AND a.quote_asset = 'IDR';

CREATE TRIGGER signal_snapshots_no_delete
    BEFORE DELETE ON signal_snapshots
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'signal_snapshots is append-only: a locked prediction cannot be deleted (SPEC 20)';


-- ---------------------------------------------------------------------
-- 5. correlations menyimpan simbol sebagai string TANPA foreign key, jadi
--    20 baris 'BTC/IDR'-'ETH/IDR' akan selamat dari penghapusan asetnya dan
--    bercampur dengan baris USDT baru di setiap pembacaan yang hanya
--    memfilter market_code='CRYPTO'. Dibersihkan eksplisit.
-- ---------------------------------------------------------------------
DELETE FROM correlations
WHERE market_code = 'CRYPTO'
  AND (left_symbol LIKE '%/IDR' OR right_symbol LIKE '%/IDR');


-- ---------------------------------------------------------------------
-- 6. provider_events dari feed crypto lama (1 baris; sisa 21.647 baris di
--    tabel ini milik yahoo/IDX dan tidak disentuh).
--
--    Nama venue lama sengaja ditulis di sini. Ia dilarang di src/ dan
--    tests/, tapi file ini adalah catatan penghapusannya - migrasi yang
--    tidak menyebut apa yang dihapusnya bukan catatan, hanya perintah.
-- ---------------------------------------------------------------------
DELETE FROM provider_events
WHERE market_code = 'CRYPTO' AND provider = 'indodax';


-- ---------------------------------------------------------------------
-- 7. Aset IDR-nya. CASCADE dari sini menghapus: candles (31.568),
--    market_snapshots (55.035), council_sessions (110, yang meng-CASCADE
--    lagi ke agent_objections / agent_rebuttals / judge_decisions /
--    veto_events), deliberations (6), regimes (20), technical_snapshots
--    (20), volume_snapshots (20), news_asset_links (28), fundamentals (0).
-- ---------------------------------------------------------------------
DELETE FROM assets
WHERE market_code = 'CRYPTO' AND quote_asset = 'IDR';


-- ---------------------------------------------------------------------
-- 8. Dan hidupkan yang USDT. Lihat BAGIAN 3 di kepala file: tanpa baris ini
--    seluruh migrasi berhasil dan crypto tetap mati.
--
--    Ditulis sebagai UPDATE di sini, bukan sebagai perubahan kontrak
--    upsert_asset, karena kontrak itu benar: seed TIDAK boleh menghidupkan
--    kembali aset yang sengaja dimatikan operator. Yang butuh pengecualian
--    adalah peristiwa satu kali ini, dan tempat peristiwa satu kali adalah
--    migrasi.
-- ---------------------------------------------------------------------
UPDATE assets
SET enabled = 1
WHERE market_code = 'CRYPTO' AND quote_asset = 'USDT';
