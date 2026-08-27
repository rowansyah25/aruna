"""Geometri XAU: stop dari volatilitas, target dari struktur, RR yang jujur.

**Kenapa target diambil dari struktur dan bukan dari kelipatan ATR.**  Kalau
target dikarang sebagai `n x ATR`, RR-nya menjadi konstanta yang saya pilih
sendiri - dan gerbang RR tidak akan pernah menyala sekali pun, sambil terlihat
bekerja. Diambil dari level yang benar-benar disentuh harga berkali-kali, RR
berubah tiap keadaan dan gerbangnya punya gigi.

Data ujinya zigzag, bukan naik-lalu-turun. Diukur lewat probe: deret
naik-lalu-turun menghasilkan NOL support dan NOL resistance (satu swing
terkonfirmasi), jadi seluruh test di berkas ini akan hijau tanpa pernah
menyentuh sebuah level.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.enums import Decision, Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.bukti import rakit_bukti
from aruna.xau.geometri import MIN_TARGET_ATR, STOP_ATR, rakit_geometri
from aruna.xau.timeframes import rakit_tumpukan

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
PROV = Provenance(source="twelvedata")


def _bikin(harga_fn, jumlah: int = 250) -> list[Candle]:
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = AWAL + timedelta(minutes=5 * i)
        h = Decimal(str(round(harga_fn(i), 2)))
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=h,
                high=h + 2,
                low=h - 2,
                close=h,
                volume=Decimal(0),
                provenance=PROV,
                is_closed=True,
            )
        )
    return keluar


@pytest.fixture
def bukti():
    """Zigzag rata: support ~968 (6 sentuhan), resistance ~1032 (5 sentuhan)."""
    return rakit_bukti(rakit_tumpukan(_bikin(lambda i: 1000 + 30 * math.sin(i / 8))))


@pytest.fixture
def bukti_tanpa_resistance():
    """Zigzag menanjak: hanya support; tidak ada level di atas harga."""
    return rakit_bukti(
        rakit_tumpukan(_bikin(lambda i: 1000 + i * 0.1 + 25 * math.sin(i / 8)))
    )


@pytest.fixture
def bukti_tipis():
    """Bahan terlalu sedikit: ATR ada tapi struktur belum terbentuk."""
    return rakit_bukti(rakit_tumpukan(_bikin(lambda i: 1000 + i * 0.5, jumlah=30)))


class TestArah:
    def test_buy_stop_di_bawah_target_di_atas(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        assert geo.stop < geo.entry < geo.target

    def test_sell_stop_di_atas_target_di_bawah(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.SELL, Decimal("991"))
        assert geo.target < geo.entry < geo.stop

    def test_arah_bukan_arah_ditolak(self, bukti) -> None:
        with pytest.raises(ValueError, match="arah"):
            rakit_geometri(bukti, Decision.NO_SIGNAL, Decimal("991"))


class TestSumberAngka:
    def test_target_adalah_level_struktur_bukan_kelipatan_atr(self, bukti) -> None:
        """Inti berkas ini: kalau target dikarang, gerbang RR jadi teater."""
        resistance = [
            lvl for lvl in bukti.m5.structure.resistance if lvl.price > 991
        ]
        assert resistance, "prasyarat data uji: harus ada resistance di atas harga"
        terdekat = min(resistance, key=lambda lvl: lvl.price)
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        assert geo.target == Decimal(str(terdekat.price))

    def test_sentuhan_target_dibawa_sebagai_bukti_kekuatan(self, bukti) -> None:
        """Level yang disentuh enam kali bukan level yang disentuh sekali."""
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        assert geo.sentuhan_target >= 2

    def test_stop_dari_atr(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        assert geo.jarak_stop == (STOP_ATR * geo.atr)

    def test_level_terdekat_yang_dipilih(self, bukti) -> None:
        """Target yang lebih jauh memberi RR lebih bagus di atas kertas, dan
        lebih kecil kemungkinannya tercapai."""
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        di_atas = [
            Decimal(str(lvl.price))
            for lvl in bukti.m5.structure.resistance
            if lvl.price > 991
        ]
        assert geo.target == min(di_atas)


class TestRR:
    def test_rr_dihitung_dari_jarak_sebenarnya(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        diharapkan = float(geo.jarak_target / geo.jarak_stop)
        assert abs(geo.rr - diharapkan) < 1e-9

    def test_rr_buruk_saat_target_dekat(self, bukti) -> None:
        """Gerbangnya punya gigi: entry tepat di bawah resistance = RR jelek.

        Kalau test ini tidak bisa dibuat merah, berarti RR-nya konstanta.
        """
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("1031"))
        assert geo.rr < 1.0

    def test_target_atr_dilaporkan_untuk_lantai_dua_atr(self, bukti) -> None:
        """Pelajaran futures: satu ATR adalah pergerakan khas, jadi
        menargetkannya berarti menargetkan hasil imbang terukur terburuk."""
        dekat = rakit_geometri(bukti, Decision.BUY, Decimal("1031"))
        assert dekat.target_atr < MIN_TARGET_ATR

        jauh = rakit_geometri(bukti, Decision.BUY, Decimal("991"))
        assert jauh.target_atr >= MIN_TARGET_ATR


class TestTidakTerukur:
    def test_tanpa_level_di_arah_tujuan_tidak_ada_geometri(
        self, bukti_tanpa_resistance
    ) -> None:
        """Tanpa level, ke mana harga akan pergi TIDAK DIKETAHUI.

        Menambalnya dengan kelipatan ATR akan mengarang sebuah target dan
        membuat RR-nya selalu lulus.
        """
        assert (
            rakit_geometri(bukti_tanpa_resistance, Decision.BUY, Decimal("1017"))
            is None
        )

    def test_struktur_belum_terbentuk_tidak_ada_geometri(self, bukti_tipis) -> None:
        assert rakit_geometri(bukti_tipis, Decision.BUY, Decimal("1010")) is None

    def test_harga_di_luar_seluruh_level_tidak_ada_geometri(self, bukti) -> None:
        """Harga di atas semua resistance: tak ada target untuk BUY."""
        assert rakit_geometri(bukti, Decision.BUY, Decimal("2000")) is None
