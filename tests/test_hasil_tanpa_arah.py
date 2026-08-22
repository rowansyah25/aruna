"""Keputusan tanpa arah bukan kekalahan (PASAL 15.24).

Terukur 2026-08-21 pada 9.877 ingatan produksi:

    WAIT       LOSS       5627
    NO_SIGNAL  LOSS        176

5.803 dari 9.877 ingatan - **59%** - adalah keputusan yang tidak punya arah,
dicatat sebagai kalah. Jumlahnya cocok tepat dengan 5.803 baris `NO_POSITION`
di `paper_results`.

Akibatnya berlapis:

* win rate ingatan terbaca 17,9% padahal akurasi arah sesungguhnya 44,5%;
* `Ringkasan.win_rate` yang dipakai `susun()` dihitung di atas 59% label palsu,
  jadi seluruh pengaruh SUPPORTIVE/CONTRARY bersandar pada dasar yang salah;
* `hitung_per_timeframe` menghitung ingatan "yang bisa mengajari" termasuk
  yang tidak mengajari apa pun.

**Bug yang sama sudah ditemukan dan diperbaiki di sisi futures.** Catatan di
`memory.py` masih tertulis: ``EXPIRED`` menjadi ``NEUTRAL``, bukan ``LOSS``,
karena menghitungnya sebagai kalah membuat win rate futures terlihat 2%. Sisi
spot tidak pernah ikut diperbaiki.
"""

from __future__ import annotations

import pytest

from aruna.db.repositories.memory import _hasil_dari
from aruna.memory.record import Hasil


class TestTanpaArah:
    @pytest.mark.parametrize("arah", ["WAIT", "NO_SIGNAL", "", None])
    def test_bukan_kalah(self, arah) -> None:
        """Keputusan untuk tidak mengambil sikap tidak bisa benar atau salah.

        `favourable=0` di sini berarti "harga tidak naik", bukan "ARUNA salah"
        - tidak ada yang diklaim untuk dibantah.
        """
        hasil = _hasil_dari({"direction": arah, "favourable": 0})

        assert hasil is Hasil.NEUTRAL

    def test_tetap_neutral_meski_favourable(self) -> None:
        """Dan tidak bisa menang juga. Harga yang kebetulan naik sesudah WAIT
        bukan kemenangan - kalau dihitung begitu, ARUNA belajar bahwa diam
        adalah strategi yang menang."""
        hasil = _hasil_dari({"direction": "WAIT", "favourable": 1})

        assert hasil is Hasil.NEUTRAL


class TestBerarah:
    def test_arah_benar_menang(self) -> None:
        assert _hasil_dari({"direction": "BUY", "favourable": 1}) is Hasil.WIN

    def test_arah_salah_kalah(self) -> None:
        assert _hasil_dari({"direction": "SELL", "favourable": 0}) is Hasil.LOSS

    @pytest.mark.parametrize("arah", ["LONG", "SHORT", "buy", " Sell "])
    def test_ejaan_futures_dan_spasi_ikut_terbaca(self, arah) -> None:
        """`market_memories` memuat kedua ejaan: `BUY/SELL` dari spot dan
        `LONG/SHORT` dari futures. Ejaan yang tidak dikenal akan diam-diam
        jatuh ke NEUTRAL dan menghapus separuh korpus dari penilaian."""
        assert _hasil_dari({"direction": arah, "favourable": 1}) in (
            Hasil.WIN, Hasil.LOSS,
        )

    def test_tanpa_nilai_tetap_unknown(self) -> None:
        """Yang tidak terukur bukan yang kalah - alasan yang sudah tertulis di
        modulnya, dan tidak berubah."""
        assert _hasil_dari({"direction": "BUY", "favourable": None}) is Hasil.UNKNOWN


class TestDampakYangDiukur:
    def test_wait_tidak_menyeret_win_rate(self) -> None:
        """Rekonstruksi angka produksi: 1.726 menang dan 2.156 kalah di antara
        keputusan berarah, ditambah 5.803 WAIT.

        Dengan WAIT sebagai LOSS, win rate terbaca 17,9%. Sebagai NEUTRAL, ia
        44,5% - dan 44,5% itulah yang cocok dengan `direction_correct` di
        `paper_results`.
        """
        baris = (
            [{"direction": "BUY", "favourable": 1}] * 1726
            + [{"direction": "BUY", "favourable": 0}] * 2156
            + [{"direction": "WAIT", "favourable": 0}] * 5803
        )
        hasil = [_hasil_dari(r) for r in baris]

        menang = sum(h is Hasil.WIN for h in hasil)
        kalah = sum(h is Hasil.LOSS for h in hasil)
        netral = sum(h is Hasil.NEUTRAL for h in hasil)

        assert netral == 5803
        assert round(menang / (menang + kalah) * 100, 1) == 44.5
