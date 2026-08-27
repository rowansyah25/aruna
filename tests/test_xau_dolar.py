"""Proksi dolar sebagai bukti - dan penjaga terhadap angka yang menyesatkan."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.dolar import (
    MIN_RETURN,
    SIMBOL_PROKSI,
    hitung_bukti_dolar,
)

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
PROV = Provenance(source="twelvedata")


def _deret(harga_fn, jumlah: int = 300, *, simbol: str = "XAU/USD",
           lompati: set[int] | None = None) -> list[Candle]:
    keluar: list[Candle] = []
    for i in range(jumlah):
        if lompati and i in lompati:
            continue
        buka = AWAL + timedelta(minutes=5 * i)
        h = Decimal(str(round(harga_fn(i), 5)))
        keluar.append(
            Candle(
                market=Market.FOREX, symbol=simbol, interval=Horizon.M5,
                open_time=buka, close_time=buka + timedelta(minutes=5),
                open=h, high=h, low=h, close=h,
                volume=Decimal(0), provenance=PROV, is_closed=True,
            )
        )
    return keluar


class TestArahHubungan:
    def test_bergerak_bersama_positif(self) -> None:
        xau = _deret(lambda i: 1000 + 10 * math.sin(i / 7))
        eur = _deret(lambda i: 1.1 + 0.01 * math.sin(i / 7), simbol=SIMBOL_PROKSI)
        b = hitung_bukti_dolar(xau, eur)
        assert b.korelasi > 0.9

    def test_berlawanan_negatif(self) -> None:
        xau = _deret(lambda i: 1000 + 10 * math.sin(i / 7))
        eur = _deret(lambda i: 1.1 - 0.01 * math.sin(i / 7), simbol=SIMBOL_PROKSI)
        b = hitung_bukti_dolar(xau, eur)
        assert b.korelasi < -0.9

    def test_tanda_tidak_diasumsikan(self) -> None:
        """Spec melarang "DXY naik = pasti SELL". Modul ini tidak pernah
        menetapkan tanda - ia mengukurnya, dan keduanya bisa keluar."""
        naik = hitung_bukti_dolar(
            _deret(lambda i: 1000 + 10 * math.sin(i / 7)),
            _deret(lambda i: 1.1 + 0.01 * math.sin(i / 7), simbol=SIMBOL_PROKSI),
        )
        turun = hitung_bukti_dolar(
            _deret(lambda i: 1000 + 10 * math.sin(i / 7)),
            _deret(lambda i: 1.1 - 0.01 * math.sin(i / 7), simbol=SIMBOL_PROKSI),
        )
        assert naik.korelasi > 0 > turun.korelasi


class TestReturnBukanHarga:
    def test_dua_deret_menanjak_tidak_otomatis_berkorelasi_penuh(self) -> None:
        """Inti berkas ini.

        Diukur di data sungguhan: korelasi HARGA 0,879 sementara korelasi
        RETURN 0,348. Yang pertama spurious - keduanya sekadar sama-sama
        menanjak selama tujuh belas hari. Memakainya akan membuat bukti ini
        terlihat empat kali lebih kuat daripada sebenarnya.
        """
        # Sama-sama menanjak, tapi getaran hariannya TIDAK berhubungan.
        xau = _deret(lambda i: 1000 + i * 0.5 + 8 * math.sin(i / 5))
        eur = _deret(
            lambda i: 1.1 + i * 0.0005 + 0.008 * math.cos(i / 3.3),
            simbol=SIMBOL_PROKSI,
        )
        b = hitung_bukti_dolar(xau, eur)
        assert abs(b.korelasi) < 0.5, (
            f"r={b.korelasi}: tren bersama bocor ke korelasi; ia harus dihitung "
            "atas return, bukan harga"
        )


class TestPenyejajaranWaktu:
    def test_hanya_bar_yang_waktunya_sama_dipakai(self) -> None:
        """Korelasi atas bar yang bergeser satu langkah mengukur hubungan
        yang tidak ada."""
        xau = _deret(lambda i: 1000 + 10 * math.sin(i / 7))
        eur = _deret(
            lambda i: 1.1 + 0.01 * math.sin(i / 7),
            simbol=SIMBOL_PROKSI,
            lompati={5, 6, 7, 8, 9},
        )
        b = hitung_bukti_dolar(xau, eur)
        assert b.sampel <= len(eur)
        assert b.korelasi > 0.9, "penyejajaran gagal; bar bolong merusak hasilnya"

    def test_tanpa_bar_yang_cocok_tidak_terukur(self) -> None:
        xau = _deret(lambda i: 1000 + i)
        eur = _deret(
            lambda i: 1.1, jumlah=300, simbol=SIMBOL_PROKSI,
            lompati=set(range(300)),
        )
        b = hitung_bukti_dolar(xau, eur)
        assert b.korelasi is None
        assert b.terukur is False


class TestTidakTerukur:
    def test_sampel_terlalu_kecil_tidak_terukur(self) -> None:
        """Korelasi dari dua puluh return bukan korelasi yang lemah -
        ia korelasi yang tidak diukur."""
        xau = _deret(lambda i: 1000 + i, jumlah=MIN_RETURN - 10)
        eur = _deret(lambda i: 1.1 + i * 0.001, jumlah=MIN_RETURN - 10,
                     simbol=SIMBOL_PROKSI)
        b = hitung_bukti_dolar(xau, eur)
        assert b.korelasi is None
        assert b.sampel == 0

    def test_proksi_datar_tidak_terukur_bukan_nol(self) -> None:
        """Deret tanpa ragam tak punya korelasi; nol akan berbohong."""
        xau = _deret(lambda i: 1000 + 10 * math.sin(i / 7))
        eur = _deret(lambda i: 1.1, simbol=SIMBOL_PROKSI)
        b = hitung_bukti_dolar(xau, eur)
        assert b.korelasi is None

    def test_deret_kosong_tidak_menjatuhkan(self) -> None:
        b = hitung_bukti_dolar(_deret(lambda i: 1000 + i), [])
        assert b.korelasi is None
        assert b.gerak_pct is None


class TestPenamaanJujur:
    def test_simbolnya_disebut_apa_adanya(self) -> None:
        """Ia BUKAN DXY, dan tidak boleh ada nama yang berpura-pura begitu.

        DXY, DX=F, US10Y, dan TNX semuanya 404 di venue ini - diukur
        2026-08-28. Dan `USDX` yang ADA justru SGI Enhanced Core ETF, bukan
        indeks dolar: simbolnya resolve, artinya salah.
        """
        assert SIMBOL_PROKSI == "EUR/USD"
        b = hitung_bukti_dolar(
            _deret(lambda i: 1000 + i),
            _deret(lambda i: 1.1 + i * 0.001, simbol=SIMBOL_PROKSI),
        )
        assert b.simbol == "EUR/USD"

    def test_modul_tidak_menyebut_dirinya_dxy(self) -> None:
        from pathlib import Path

        sumber = (
            Path(__file__).resolve().parent.parent
            / "src" / "aruna" / "xau" / "dolar.py"
        ).read_text(encoding="utf-8")
        # Boleh disebut di komentar sebagai penjelasan, tapi tak boleh jadi
        # nama yang dipakai kode.
        for terlarang in ("SIMBOL_DXY", "dxy =", "def dxy", "class Dxy"):
            assert terlarang not in sumber
