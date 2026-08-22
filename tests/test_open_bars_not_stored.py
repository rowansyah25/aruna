"""Bar yang belum tutup tidak pernah masuk tabel candle (SPEC 24).

Bar terbuka masih berubah sesudah dibaca, jadi memakainya sebagai bukti yang
sudah selesai adalah look-ahead. ``is_closed`` sudah lama ada dan gap detection
sudah lama menyaringnya - tapi barisnya tetap DITULIS, dan setiap pembaca yang
lupa menyaring memakainya sebagai bar biasa.

Terukur saat ditemukan: 462 bar belum tutup tersimpan, 242 di antaranya 15m
IDX. Stempel waktunya adalah detik pengambilannya - ``02:05:35`` alih-alih
``02:00:00`` - jadi ia menempati posisi "bar terbaru" pada setiap kueri yang
mengurutkan menurut ``open_time``, dengan volume nol.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance


def _candle(menit: int, *, closed: bool) -> Candle:
    saat = datetime(2026, 8, 19, 2, menit, tzinfo=UTC)
    return Candle(
        market=Market.IDX,
        symbol="BBCA",
        interval=Horizon.M15,
        open_time=saat,
        close_time=saat.replace(second=59),
        open=Decimal("6300"),
        high=Decimal("6350"),
        low=Decimal("6290"),
        close=Decimal("6325"),
        volume=Decimal("100") if closed else Decimal("0"),
        provenance=Provenance(source="fake", provider_timestamp=saat),
        is_closed=closed,
    )


class _Store:
    def __init__(self) -> None:
        self.ditulis: list[Candle] = []

    async def upsert_candles(self, asset_id: int, candles: list[Candle]) -> int:
        self.ditulis.extend(candles)
        return len(candles)

    async def record_provider_event(self, **kwargs) -> int:
        return 1


class _Provider:
    name = "fake"
    market = Market.IDX

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    @property
    def capabilities(self):
        from aruna.data.provider import ProviderCapabilities, Transport

        return ProviderCapabilities(
            name=self.name, market=Market.IDX, transport=Transport.POLL,
            is_realtime=False, expected_delay_sec=900,
            supports_order_book=False, supported_intervals=(Horizon.M15,),
            max_candles_per_request=100, requires_credentials=False,
            regulatory_note="test double",
        )

    async def fetch_candles(self, symbol, interval, *, limit):
        return list(self._candles)


class _Aset:
    id = 1
    symbol = "BBCA"


class _Universe:
    async def assets(self, *, market, enabled_only=True):
        return [_Aset()]


def _ingestor(candles: list[Candle]) -> tuple:
    from aruna.core.config import DataSettings
    from aruna.data.ingest import MarketIngestor

    store = _Store()
    return MarketIngestor(
        provider=_Provider(candles),
        universe=_Universe(),
        store=store,
        settings=DataSettings(_env_file=None),
    ), store


class TestBarBerjalanTidakDisimpan:
    @pytest.mark.asyncio
    async def test_yang_belum_tutup_disaring(self) -> None:
        ing, store = _ingestor(
            [_candle(0, closed=True), _candle(15, closed=False)]
        )

        await ing.backfill((Horizon.M15,), quiet=True, detect_gaps=False)

        assert len(store.ditulis) == 1
        assert all(c.is_closed for c in store.ditulis)

    @pytest.mark.asyncio
    async def test_yang_tertutup_tetap_masuk_utuh(self) -> None:
        """Peredamnya menyaring satu hal saja; ia tidak boleh ikut membuang
        bar yang sah."""
        ing, store = _ingestor([_candle(m, closed=True) for m in (0, 15, 30)])

        hasil = await ing.backfill((Horizon.M15,), quiet=True, detect_gaps=False)

        assert len(store.ditulis) == 3
        assert hasil.candles == 3

    @pytest.mark.asyncio
    async def test_semua_terbuka_berbunyi_bukan_diam(self, monkeypatch) -> None:
        """Seluruh tarikan berisi bar berjalan bukan 'tidak ada yang baru' - ia
        bisa berarti provider menandai semuanya salah, dan diam di situ membuat
        seri berhenti tumbuh tanpa satu pun tanda."""
        from aruna.data import ingest as modul

        keluar: list[str] = []
        monkeypatch.setattr(
            modul.log, "warning", lambda e, **k: keluar.append(e)
        )
        ing, store = _ingestor([_candle(0, closed=False)])

        await ing.backfill((Horizon.M15,), quiet=True, detect_gaps=False)

        assert store.ditulis == []
        assert "ingest.all_bars_open" in keluar, keluar

    @pytest.mark.asyncio
    async def test_sebagian_terbuka_tidak_berbunyi_keras(self, monkeypatch) -> None:
        """Satu bar berjalan di ujung tarikan adalah keadaan NORMAL - venue
        selalu punya bar berjalan. Membuatnya warning akan berbunyi tiap
        menit."""
        from aruna.data import ingest as modul

        keluar: list[str] = []
        monkeypatch.setattr(
            modul.log, "warning", lambda e, **k: keluar.append(e)
        )
        ing, _ = _ingestor([_candle(0, closed=True), _candle(15, closed=False)])

        await ing.backfill((Horizon.M15,), quiet=True, detect_gaps=False)

        assert "ingest.all_bars_open" not in keluar

    @pytest.mark.asyncio
    async def test_gap_dihitung_dari_bar_tertutup_saja(self) -> None:
        """Sudah begitu sebelumnya, dan harus tetap begitu: bar berjalan di
        ujung bukan lubang."""
        import inspect

        from aruna.data.ingest import MarketIngestor

        sumber = inspect.getsource(MarketIngestor._store_backfill)
        assert "find_candle_gaps(tertutup)" in sumber
