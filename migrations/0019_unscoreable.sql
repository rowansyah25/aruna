-- =====================================================================
-- ARUNA AI - status untuk prediksi yang tidak bisa dinilai
--
-- `signals.status` hanya mengenal LOCKED / RESOLVED / SUPERSEDED /
-- INVALIDATED, jadi sebuah prediksi yang jatuh temponya sudah lewat tapi
-- tidak punya satu pun candle di dalam horizonnya tidak punya tempat untuk
-- disebut. Ia tinggal LOCKED - dan LOCKED berarti "menunggu".
--
-- Diukur pada database ini: 88 prediksi IDX dikunci hari SABTU, dengan nol
-- bar tertutup di dalam horizon mana pun, di interval mana pun. Bursa tutup
-- sepanjang hidup setiap prediksi itu. Tidak ada refresh yang bisa
-- mengubahnya, karena jendelanya sudah tertutup dan berlalu.
--
-- Akibatnya bukan sekadar kosmetik. `due()` mengurutkan berdasarkan
-- resolves_at, jadi kedelapan puluh delapan itu duduk permanen di KEPALA
-- antrean, dibaca ulang tiap putaran, dan menahan prediksi di belakangnya.
-- Mereka juga dihitung sebagai `awaiting_candles`, yang membuat health
-- menjanjikan antrean akan terkuras - janji tentang masa depan yang tidak
-- akan ditepati.
--
-- UNSCOREABLE BUKAN HASIL. Tidak ada outcome, tidak ada paper result, tidak
-- ada menang atau kalah. Ia menyatakan satu hal saja: pertanyaannya tidak
-- bisa dijawab dari data yang ada, dan alasannya disimpan di
-- `withheld_reason` supaya bisa diaudit - bukan dibuang diam-diam.
--
-- Statusnya dipilih HANYA lewat kondisi yang terukur (lihat
-- `_prices_during`: setiap seri sampling yang punya baris sudah melewati
-- akhir horizon, dan tidak satu pun bar jatuh di dalamnya). Ia tidak boleh
-- jadi cara menyingkirkan prediksi yang merepotkan.
-- =====================================================================

ALTER TABLE signals
    DROP CHECK signals_status_allowed;

ALTER TABLE signals
    ADD CONSTRAINT signals_status_allowed CHECK (
        status IN ('LOCKED', 'RESOLVED', 'SUPERSEDED', 'INVALIDATED', 'UNSCOREABLE')
    );
