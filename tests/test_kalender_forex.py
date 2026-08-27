"""Kalender valas: akhir pekan bukan data hilang.

``find_candle_gaps`` menghitung slot yang absen. Tanpa kalender, valas
diperlakukan menerus seperti crypto - dan tiap akhir pekan menjadi lubang
sekitar 576 bar M5, yang akan menolak XAU setiap Senin selamanya.

Docstring ``find_candle_gaps`` sendiri yang memperingatkannya: dinding alarm
palsu "lebih buruk daripada tidak ada deteksi sama sekali, karena ia mengajari
operator mengabaikan yang sungguhan".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.clock import FOREX_CALENDAR
from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.data.quality import find_candle_gaps


def _saat(hari: str, jam: int, menit: int = 0) -> datetime:
    """Jumat 2026-08-28 .. Senin 2026-08-31 (2026-08-28 adalah Jumat)."""
    peta = {"jumat": 28, "sabtu": 29, "minggu": 30, "senin": 31}
    return datetime(2026, 8, peta[hari], jam, menit, tzinfo=UTC)


class TestKalenderForex:
    @pytest.mark.parametrize(
        "hari,jam",
        [("sabtu", 0), ("sabtu", 12), ("sabtu", 23), ("minggu", 0), ("minggu", 12)],
    )
    def test_akhir_pekan_tutup(self, hari: str, jam: int) -> None:
        assert FOREX_CALENDAR.is_open(_saat(hari, jam)) is False

    @pytest.mark.parametrize("jam", [0, 8, 12, 20])
    def test_tengah_pekan_buka(self, jam: int) -> None:
        assert FOREX_CALENDAR.is_open(_saat("senin", jam)) is True

    def test_jumat_malam_sudah_tutup(self) -> None:
        assert FOREX_CALENDAR.is_open(_saat("jumat", 12)) is True
        assert FOREX_CALENDAR.is_open(_saat("jumat", 23)) is False

    def test_minggu_malam_sudah_buka_lagi(self) -> None:
        assert FOREX_CALENDAR.is_open(_saat("minggu", 20)) is False
        assert FOREX_CALENDAR.is_open(_saat("minggu", 23)) is True


class TestLubangAkhirPekan:
    def _bar(self, saat: datetime) -> Candle:
        return Candle(
            market=Market.FOREX,
            symbol="XAU/USD",
            interval=Horizon.M5,
            open_time=saat,
            close_time=saat + timedelta(minutes=5),
            open=Decimal(1000),
            high=Decimal(1001),
            low=Decimal(999),
            close=Decimal(1000),
            volume=Decimal(0),
            provenance=Provenance(source="twelvedata"),
        )

    def test_akhir_pekan_bukan_lubang(self) -> None:
        """Bar terakhir Jumat lalu bar pertama saat pasar buka: nol data hilang.

        Sabtu penuh dan Minggu siang - sekitar 570 slot M5 - tidak dihitung
        sama sekali karena pasarnya memang tutup.
        """
        bars = [self._bar(_saat("jumat", 20, 55)), self._bar(_saat("minggu", 22, 0))]
        assert find_candle_gaps(bars) == []

    def test_hanya_jam_buka_yang_dihitung_hilang(self) -> None:
        """Minggu malam SUDAH jam perdagangan, jadi absennya nyata.

        Ini yang membedakan kalender dari sekadar mematikan deteksi: dari
        Jumat sore ke Senin dini hari ada sekitar 600 slot, dan hanya 24 di
        antaranya - dua jam Minggu malam - yang benar-benar hilang.
        """
        bars = [self._bar(_saat("jumat", 20, 55)), self._bar(_saat("senin", 0, 0))]
        lubang = find_candle_gaps(bars)
        assert len(lubang) == 1
        assert lubang[0][2] == 24, "dua jam Minggu malam = 24 slot M5"

    def test_lubang_tengah_pekan_tetap_terdeteksi(self) -> None:
        """Kalender tidak boleh membutakan deteksi di jam perdagangan."""
        bars = [self._bar(_saat("senin", 8, 0)), self._bar(_saat("senin", 9, 0))]
        lubang = find_candle_gaps(bars)
        assert len(lubang) == 1
        _mulai, _selesai, hilang = lubang[0]
        assert hilang == 11, "satu jam M5 = 12 slot; 11 di antaranya hilang"

    def test_crypto_tidak_terpengaruh(self) -> None:
        """Crypto memang menerus; akhir pekannya lubang sungguhan."""
        bars = [
            Candle(
                market=Market.CRYPTO,
                symbol="BTC/USDT",
                interval=Horizon.M5,
                open_time=saat,
                close_time=saat + timedelta(minutes=5),
                open=Decimal(1000),
                high=Decimal(1001),
                low=Decimal(999),
                close=Decimal(1000),
                volume=Decimal(1),
                provenance=Provenance(source="binance-spot"),
            )
            for saat in (_saat("jumat", 20, 55), _saat("senin", 0, 0))
        ]
        lubang = find_candle_gaps(bars)
        assert len(lubang) == 1
        assert lubang[0][2] > 500, "akhir pekan crypto adalah data yang benar-benar hilang"
