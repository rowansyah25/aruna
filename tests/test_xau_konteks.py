"""Konteks keputusan XAU: M5, dan yang tidak terukur tetap None."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.enums import DataQuality, Horizon, Market
from aruna.data.models import Candle, Provenance, Snapshot
from aruna.xau.bukti import rakit_bukti
from aruna.xau.konteks import rakit_konteks
from aruna.xau.timeframes import rakit_tumpukan

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)


def _m5(jumlah: int) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = AWAL + timedelta(minutes=5 * i)
        harga = Decimal(1000 + (i if i < jumlah // 2 else jumlah - i))
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 2,
                low=harga - 2,
                close=harga + 1,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


@pytest.fixture
def bukti():
    return rakit_bukti(rakit_tumpukan(_m5(250)))


def _snapshot(**kw) -> Snapshot:
    bawaan = dict(
        market=Market.FOREX,
        symbol="XAU/USD",
        # Sengaja JAUH dari as_of bukti, supaya test yang menukar keduanya gagal.
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        last_price=Decimal("4592.34"),
        provenance=Provenance(source="twelvedata"),
        quality=DataQuality.OK,
    )
    return Snapshot(**{**bawaan, **kw})


class TestRakitKonteks:
    def test_interval_keputusan_adalah_m5(self, bukti) -> None:
        """Spec: keputusan final berasal dari XAUUSD M5."""
        assert rakit_konteks(bukti, _snapshot()).interval is Horizon.M5

    def test_as_of_ikut_bukti_bukan_jam_tarik(self, bukti) -> None:
        """captured_at adalah kapan KITA bertanya; as_of kapan pasar bicara."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.as_of == bukti.as_of

    def test_spread_tak_terukur_tetap_none(self, bukti) -> None:
        """Twelve Data tidak menerbitkan bid/ask - diukur 2026-08-27."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.state.spread_bps is None
        assert ctx.state.bid is None and ctx.state.ask is None

    def test_spread_diteruskan_kalau_venue_menerbitkannya(self, bukti) -> None:
        """Sumber lain kelak boleh punya; jalurnya harus sudah benar sekarang."""
        ctx = rakit_konteks(
            bukti,
            _snapshot(
                bid=Decimal("4592"), ask=Decimal("4593"), spread_bps=Decimal("2.2")
            ),
        )
        assert ctx.state.spread_bps == Decimal("2.2")

    def test_market_adalah_forex(self, bukti) -> None:
        assert rakit_konteks(bukti, _snapshot()).market is Market.FOREX

    def test_technical_yang_dipakai_adalah_m5(self, bukti) -> None:
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.technical is bukti.m5

    def test_volume_nol_tidak_menyamar_jadi_likuiditas(self, bukti) -> None:
        """Valas spot tak punya volume; 0 tidak boleh dibaca sebagai terukur."""
        assert rakit_konteks(bukti, _snapshot()).state.volume_24h is None

    def test_sesi_diteruskan_apa_adanya(self, bukti) -> None:
        """None sekarang berarti belum diukur - Rencana 4 yang mengisinya."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.state.session is None
        assert ctx.state.market_open is None

    def test_sesi_diteruskan_kalau_sudah_ada(self, bukti) -> None:
        ctx = rakit_konteks(bukti, _snapshot(session="LONDON", market_open=True))
        assert ctx.state.session == "LONDON"
        assert ctx.state.market_open is True

    def test_kill_switch_diteruskan(self, bukti) -> None:
        ctx = rakit_konteks(bukti, _snapshot(), trading_allowed=False)
        assert ctx.trading_allowed is False

    def test_sumber_dan_mutu_terbawa(self, bukti) -> None:
        """Keputusan harus bisa ditelusuri ke adapter yang memberi datanya."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.state.source == "twelvedata"
        assert ctx.state.data_quality == "OK"
