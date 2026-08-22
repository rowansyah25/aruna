"""PASAL 15.9: sampel kecil bukan bukti.

Terukur 2026-08-21: seluruh ingatan ARUNA lahir dalam jendela beberapa hari -
``market_memories`` membentang 2026-08-17 sampai 2026-08-20. "126 kasus serupa"
karena itu berarti "126 kali minggu ini", bukan pengalaman bertahun-tahun, dan
setiap keluaran yang dibaca manusia wajib menyebut rentang waktunya.

Tanpa penjaga di berkas ini ARUNA akan menyimpulkan "win rate 84%" dari tiga
kasus, dan angka itu terdengar sama meyakinkannya dengan yang dari seribu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.outcome import (
    KALIMAT_TIDAK_ADA,
    KALIMAT_TIDAK_CUKUP,
    SAMPEL_MINIMUM,
    ringkas,
)
from aruna.memory.record import Hasil, Ingatan, Mutu
from aruna.memory.similarity import Kemiripan

AWAL = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _ingatan(nomor: int, *, arah: str, hasil: Hasil) -> Ingatan:
    return Ingatan(
        signal_id=f"mem{nomor:013d}",
        sidik=Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.ASSET: "BTC/USDT",
            Dimensi.REGIME: "TRENDING",
        }),
        arah=arah,
        hasil=hasil,
        move_pct=Decimal("1.2000") if hasil is Hasil.WIN else Decimal("-0.8000"),
        locked_at=AWAL + timedelta(hours=nomor),
        resolved_at=AWAL + timedelta(hours=nomor, minutes=30),
        model_version="1.0.0+phase10",
        cakupan=95,
        mutu=Mutu.HIGH,
    )


def _mirip(skor: int) -> Kemiripan:
    return Kemiripan(
        skor=skor, cakupan=95,
        cocok=(Dimensi.ASSET, Dimensi.REGIME), beda=(),
        tak_terbaca=(Dimensi.VOLATILITY,),
    )


def _pasangan(n: int, *, arah: str, menang: int, skor: int = 90):
    """``n`` ingatan berarah ``arah``, ``menang`` di antaranya WIN."""
    return [
        (
            _ingatan(i, arah=arah,
                     hasil=Hasil.WIN if i < menang else Hasil.LOSS),
            _mirip(skor - (i % 5)),
        )
        for i in range(n)
    ]


@pytest.fixture
def tiga_kasus():
    return _pasangan(3, arah="BUY", menang=3)


@pytest.fixture
def banyak_kasus():
    return _pasangan(40, arah="BUY", menang=30) + _pasangan(10, arah="SELL", menang=4)


@pytest.fixture
def hanya_long():
    return _pasangan(30, arah="BUY", menang=20)


class TestKecukupan:
    def test_di_bawah_ambang_dinyatakan_tidak_cukup(self, tiga_kasus) -> None:
        hasil = ringkas(tiga_kasus)

        assert not hasil.cukup
        assert hasil.total == 3

    def test_kosong_bukan_nol_persen(self) -> None:
        """Nol kasus serupa bukan "win rate nol" - itu ketiadaan bukti, dan
        melaporkannya sebagai angka akan membuatnya ikut masuk ke keputusan."""
        hasil = ringkas([])

        assert hasil.total == 0
        assert not hasil.cukup
        assert all(v is None for v in hasil.win_rate.values())

    def test_di_atas_ambang_cukup(self, banyak_kasus) -> None:
        assert ringkas(banyak_kasus).cukup

    def test_ambangnya_dua_puluh(self) -> None:
        assert SAMPEL_MINIMUM == 20

    def test_tepat_di_ambang_sudah_cukup(self) -> None:
        """Ambang yang dibaca ">" alih-alih ">=" membuang satu kasus di batas,
        dan bedanya tidak akan pernah terlihat dari luar."""
        assert ringkas(_pasangan(SAMPEL_MINIMUM, arah="BUY", menang=10)).cukup


class TestRingkasannya:
    def test_arah_dipetakan_ke_long_dan_short(self, banyak_kasus) -> None:
        """Ingatan menyimpan ejaan spot - BUY dan SELL. PASAL 15.10 memakai
        LONG dan SHORT. Dua ejaan untuk hal yang sama, dan yang membaca laporan
        harus melihat satu."""
        hasil = ringkas(banyak_kasus)

        assert hasil.per_arah["LONG"] == 40
        assert hasil.per_arah["SHORT"] == 10

    def test_win_rate_per_arah(self, banyak_kasus) -> None:
        hasil = ringkas(banyak_kasus)

        assert hasil.win_rate["LONG"] == 75
        assert hasil.win_rate["SHORT"] == 40

    def test_arah_tanpa_kasus_bukan_nol_persen(self, hanya_long) -> None:
        """Win rate SHORT 0% dibaca sebagai "SHORT selalu kalah". Yang benar:
        tidak ada satu pun kasus SHORT untuk dinilai."""
        hasil = ringkas(hanya_long)

        assert hasil.win_rate["SHORT"] is None
        assert hasil.win_rate["LONG"] is not None

    def test_yang_tidak_berarah_tidak_ikut_win_rate(self) -> None:
        """WAIT adalah 59% dari sejarah ARUNA (terukur). Menghitungnya sebagai
        kekalahan LONG akan menenggelamkan win rate yang sesungguhnya."""
        campur = (
            _pasangan(20, arah="BUY", menang=10)
            + _pasangan(20, arah="WAIT", menang=0)
        )
        hasil = ringkas(campur)

        assert hasil.win_rate["LONG"] == 50
        assert hasil.per_arah.get("LONG") == 20

    def test_rentang_similarity_dilaporkan(self, banyak_kasus) -> None:
        """PASAL 15.9 mencontohkannya sendiri: "Similarity: 80-96%"."""
        rendah, tinggi = ringkas(banyak_kasus).rentang_similarity

        assert rendah <= tinggi
        assert rendah >= 0
        assert tinggi <= 100

    def test_rentang_waktunya_dilaporkan(self, banyak_kasus) -> None:
        """Beberapa hari yang disebut "historis" tanpa tanggalnya terbaca
        seperti bertahun-tahun."""
        rentang = ringkas(banyak_kasus).rentang_waktu

        assert rentang is not None
        assert rentang[0] <= rentang[1]

    def test_kosong_tidak_punya_rentang_waktu(self) -> None:
        assert ringkas([]).rentang_waktu is None


class TestKalimatnya:
    def test_kalimatnya_persis_seperti_pasalnya(self) -> None:
        assert KALIMAT_TIDAK_CUKUP == "INSUFFICIENT HISTORICAL SAMPLE"
        assert KALIMAT_TIDAK_ADA == "NO SIGNIFICANT HISTORICAL MATCH"
