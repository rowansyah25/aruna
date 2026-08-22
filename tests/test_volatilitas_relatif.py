"""Volatilitas diukur relatif terhadap kebiasaan asetnya sendiri.

`HIGH_VOLATILITY` terdefinisi, dipilih dengan bobot 2,0, dan **nol baris
memakainya**. Dugaan pertama - ia kalah argmax - salah: `LOW_VOLATILITY`
punya bobot maksimum yang sama (3,5) dan menang 448 kali.

Sebab sesungguhnya, terukur 2026-08-21 atas 7.700 pengamatan per interval:

======== ============ ==================
interval ATR% maks    jumlah >= 3,0%
======== ============ ==================
15m      2,154        **0**
1h       3,024        1
4h       6,435        504
1d       21,079       **6.896 (89,6%)**
======== ============ ==================

`HIGH_VOL_ATR_PCT = 3.0` adalah satu angka mutlak untuk semua timeframe,
sementara ATR% berskala dengan timeframe. Di 15m ia **tidak mungkin
tercapai**; di 1d ia hampir selalu benar. Ambang volatilitas yang tidak
berskala dengan timeframe bukan pendeteksi volatilitas - ia pendeteksi
timeframe.

Sisi sebaliknya sama rusaknya: `LOW_VOL_ATR_PCT = 0.5` sementara median 15m
adalah **0,445**, jadi lebih dari separuh bar 15m otomatis "volatilitas
rendah".

**Kenapa rasio, bukan ambang per-timeframe.** Ambang per-timeframe yang
dipas-paskan ke sebaran hari ini adalah overfitting terhadap enam hari pasar
naik pada dua puluh aset kripto - dan bagian 32 melarangnya dengan kata-kata
yang jelas. Rasio terhadap kebiasaan aset itu sendiri berskala bebas: 1,5
berarti hal yang sama di 15m maupun 1d, di BTC maupun di aset yang baru
terdaftar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aruna.analysis.indicators import atr
from aruna.analysis.series import CandleSeries
from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _seri(rentang: list[float]) -> CandleSeries:
    """Deret dengan tinggi-rendah yang ditentukan, harga tutup tetap 100."""
    bar = []
    for i, r in enumerate(rentang):
        bar.append(
            Candle(
                market=Market.CRYPTO,
                symbol="BTC/USDT",
                interval=Horizon.H1,
                open_time=NOW + timedelta(hours=i),
                close_time=NOW + timedelta(hours=i + 1),
                open=100, high=100 + r / 2, low=100 - r / 2, close=100,
                volume=1000,
                provenance=Provenance(source="uji", server_timestamp=NOW),
            )
        )
    return CandleSeries.from_candles(bar)


class TestRasioTerbaca:
    def test_atr_membawa_rasio_terhadap_kebiasaannya(self) -> None:
        """Tanpa ini, classifier hanya punya angka mutlak - dan angka mutlak
        tidak bisa membedakan '15m yang bergejolak' dari '1d yang tenang'."""
        r = atr(_seri([1.0] * 60))

        assert "atr_relatif" in r.components

    def test_tenang_terus_menghasilkan_rasio_sekitar_satu(self) -> None:
        r = atr(_seri([1.0] * 60))

        assert 0.9 <= r.components["atr_relatif"] <= 1.1

    def test_bergejolak_belakangan_menaikkan_rasio(self) -> None:
        """Lima puluh bar tenang lalu sepuluh bar tiga kali lebih lebar."""
        r = atr(_seri([1.0] * 50 + [3.0] * 10))

        assert r.components["atr_relatif"] > 1.5

    def test_menenang_belakangan_menurunkan_rasio(self) -> None:
        r = atr(_seri([3.0] * 50 + [0.5] * 10))

        assert r.components["atr_relatif"] < 0.7

    def test_rasio_tidak_berubah_saat_skalanya_dikali(self) -> None:
        """Inti kenapa rasio dipilih: ia bebas skala. Aset yang bergerak
        sepuluh kali lebih lebar bukan aset yang sepuluh kali lebih
        bergejolak."""
        kecil = atr(_seri([1.0] * 50 + [3.0] * 10)).components["atr_relatif"]
        besar = atr(_seri([10.0] * 50 + [30.0] * 10)).components["atr_relatif"]

        assert abs(kecil - besar) < 0.01

    def test_deret_pendek_tidak_meledak(self) -> None:
        """Deret yang lebih pendek dari periodenya sudah ditolak lebih dulu;
        yang pas-pasan tidak boleh membagi dengan nol."""
        r = atr(_seri([1.0] * 15))

        assert r.reliable is False or r.components.get("atr_relatif") is not None


class TestClassifierMemakaiRasio:
    def _verdict(self, rentang: list[float]):
        from aruna.analysis.regime import classify_regime
        from aruna.analysis.structure import (
            BreakoutState,
            StructureReport,
            TrendStructure,
        )

        # Struktur sengaja tidak bisa diandalkan: yang diuji di sini hanya
        # suara volatilitas, dan suara struktur berbobot 2,5 akan
        # menenggelamkannya.
        return classify_regime(
            structure=StructureReport(
                trend=TrendStructure.RANGE,
                breakout=BreakoutState.NONE,
                confirmed_swings=0,
            ),
            atr=atr(_seri(rentang)),
        )

    def test_lonjakan_volatilitas_terbaca_high(self) -> None:
        """Yang dulu mustahil di 15m dan 1h: ATR% maksimum di sana 2,15% dan
        3,02%, sementara ambangnya 3,0%."""
        from aruna.core.enums import Regime

        assert self._verdict([1.0] * 50 + [3.0] * 10).regime is Regime.HIGH_VOLATILITY

    def test_menenang_terbaca_low(self) -> None:
        from aruna.core.enums import Regime

        assert self._verdict([3.0] * 50 + [0.5] * 10).regime is Regime.LOW_VOLATILITY

    def test_tenang_terus_bukan_keduanya(self) -> None:
        """Volatilitas yang biasa-biasa saja bukan temuan. Kalau ia terbaca
        sebagai LOW, seluruh pasar yang tenang berhenti bisa dibedakan dari
        pasar yang benar-benar menyempit."""
        from aruna.core.enums import Regime

        r = self._verdict([1.0] * 60).regime
        assert r not in (Regime.HIGH_VOLATILITY, Regime.LOW_VOLATILITY)


class TestAmbangnyaBebasSkala:
    def test_ambang_bukan_persen_harga_lagi(self) -> None:
        """Penjaga terhadap kembalinya angka mutlak.

        `HIGH_VOL_ATR_PCT = 3.0` tidak pernah tercapai di 15m dan hampir selalu
        tercapai di 1d - dan tidak ada satu angka persen yang benar untuk
        keduanya.
        """
        from aruna.analysis import regime as modul

        assert hasattr(modul, "HIGH_VOL_RASIO")
        assert hasattr(modul, "LOW_VOL_RASIO")
        assert modul.HIGH_VOL_RASIO > 1.0
        assert modul.LOW_VOL_RASIO < 1.0
