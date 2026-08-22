"""NaN dan Infinity dari Yahoo tidak boleh mengakhiri satu pass poll.

Temuan [11] menutup lubang ini di adapter Binance. Kode di
:mod:`aruna.data.idx.yahoo` identik baris demi baris, jadi lubangnya juga
identik: ``Decimal("NaN")`` dan ``Decimal("Infinity")`` adalah konstruksi yang
SAH, tidak melempar apa pun, dan lolos setiap penjaga ``is None`` di hilir.

Yang membuat sisi IDX lebih parah daripada sisi crypto: gerbang kualitas
(:mod:`aruna.data.quality`) sekarang menyaring nilai tidak berhingga untuk
``Quote`` dan ``Candle``, tetapi ``Snapshot`` tidak lewat gerbang itu. Kedua
ledakannya terjadi DI DALAM adapter, sebelum gerbang mana pun:

* ``previous_close > 0`` -> Decimal NaN MELEMPAR ``InvalidOperation`` pada
  perbandingan (berbeda dari float NaN yang menjawab False);
* ``(last - previous_close) / previous_close`` -> Infinity lolos perbandingan
  di atas lalu meledak satu baris kemudian.

Handler per-aset di loop ingest hanya menangkap ``DataSourceUnavailableError``
dan ``ArunaError``, jadi ``decimal.InvalidOperation`` naik melewatinya dan
membatalkan SELURUH pass untuk semua aset - bukan satu simbol yang membawa
field rusak.

Test di sini menempuh jalur hidup: hanya ``_chart`` yang dipalsukan (satu-satunya
bagian yang butuh jaringan), sisanya kode produksi apa adanya. Cabut
``is_finite`` di ``_optional_decimal`` dan test ini merah - sebagian sebagai
``InvalidOperation``, sebagian sebagai nilai beracun yang diterima diam-diam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import Horizon
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.idx.yahoo import YahooIdxProvider, _optional_decimal

#: Jauh di masa lalu supaya setiap bar sudah pasti tutup dan tidak ada test yang
#: bergantung pada jam berapa suite dijalankan.
ANCHOR = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)


def snapshot_payload(**meta: Any) -> dict[str, Any]:
    """Payload chart Yahoo seminimal yang diterima ``fetch_snapshot``."""
    base = {
        "currency": "IDR",
        "fullExchangeName": "Jakarta",
        "regularMarketPrice": "1000",
        "regularMarketTime": int(ANCHOR.timestamp()),
    }
    base.update(meta)
    return {"meta": base, "timestamp": [], "indicators": {"quote": [{}]}}


def candle_payload(closes: list[Any]) -> dict[str, Any]:
    count = len(closes)
    stamps = [int((ANCHOR + timedelta(days=i)).timestamp()) for i in range(count)]
    return {
        "meta": {"currency": "IDR"},
        "timestamp": stamps,
        "indicators": {
            "quote": [
                {
                    "open": ["100"] * count,
                    "high": ["101"] * count,
                    "low": ["99"] * count,
                    "close": closes,
                    "volume": ["1000"] * count,
                }
            ]
        },
    }


def attach(provider: YahooIdxProvider, payload: dict[str, Any]) -> None:
    async def _chart(
        symbol: str, *, interval: str, range_: str
    ) -> tuple[dict[str, Any], float]:
        return payload, 12.0

    provider._chart = _chart  # type: ignore[method-assign]


@pytest.fixture
def provider() -> YahooIdxProvider:
    return YahooIdxProvider(DataSettings(_env_file=None))


class TestSnapshotSelamatDariNilaiTidakBerhingga:
    """Jalur yang TIDAK dilindungi gerbang kualitas."""

    @pytest.mark.parametrize("busuk", ["NaN", "Infinity", "-Infinity"])
    async def test_previous_close_tidak_berhingga_tidak_meledak(
        self, provider: YahooIdxProvider, busuk: str
    ) -> None:
        """Tanpa penjaga: ``InvalidOperation`` keluar dari adapter.

        NaN meledak di ``previous_close > 0``; Infinity lolos perbandingan itu
        lalu meledak di pembagian sesudahnya. Keduanya membatalkan pass poll.
        """
        attach(provider, snapshot_payload(previousClose=busuk))

        snapshot = await provider.fetch_snapshot("BBCA.JK")

        assert snapshot.last_price == Decimal("1000")
        # Tidak dikarang: basis pembandingnya tidak terbaca, jadi perubahan
        # persennya TIDAK dilaporkan, bukan diisi nol (SPEC 4).
        assert snapshot.change_24h_pct is None
        assert snapshot.raw["previousClose"] is None

    async def test_previous_close_nan_menyerahkan_giliran_ke_cadangannya(
        self, provider: YahooIdxProvider
    ) -> None:
        """NaN yang truthy dulu MENANG atas ``chartPreviousClose``.

        ``_optional_decimal(previousClose) or _optional_decimal(chartPreviousClose)``
        - Decimal NaN bernilai True, jadi cadangan yang sehat tidak pernah
        dipakai. Menolaknya memulihkan fallback itu.
        """
        attach(
            provider,
            snapshot_payload(previousClose="NaN", chartPreviousClose="800"),
        )

        snapshot = await provider.fetch_snapshot("BBCA.JK")

        assert snapshot.raw["previousClose"] == "800"
        assert snapshot.change_24h_pct == Decimal("25.00")

    async def test_harga_terakhir_nan_adalah_outage_bernama(
        self, provider: YahooIdxProvider
    ) -> None:
        """Satu simbol ditolak dengan sebab, bukan Snapshot beracun.

        ``DataSourceUnavailableError`` justru yang ditangkap handler per-aset,
        jadi aset lain di pass yang sama tetap jalan.
        """
        attach(provider, snapshot_payload(regularMarketPrice="NaN"))

        with pytest.raises(DataSourceUnavailableError, match="no price"):
            await provider.fetch_snapshot("BBCA.JK")

    async def test_volume_infinity_tidak_tersimpan_sebagai_angka(
        self, provider: YahooIdxProvider
    ) -> None:
        attach(provider, snapshot_payload(regularMarketVolume="Infinity"))

        snapshot = await provider.fetch_snapshot("BBCA.JK")

        assert snapshot.volume_24h is None


class TestQuoteDanCandle:
    async def test_harga_quote_nan_ditolak_sebelum_jadi_quote(
        self, provider: YahooIdxProvider
    ) -> None:
        attach(provider, snapshot_payload(regularMarketPrice="NaN"))

        with pytest.raises(DataSourceUnavailableError, match="no market price"):
            await provider.fetch_quote("BBCA.JK")

    async def test_bar_dengan_close_nan_dibuang_sebagai_lubang(
        self, provider: YahooIdxProvider
    ) -> None:
        """Baris rusak hilang; tetangganya yang sehat tetap sampai.

        Lubang dilaporkan lewat ketiadaan, tidak pernah diisi (SPEC 4).
        """
        attach(provider, candle_payload(["100.5", "NaN", "102.5"]))

        candles = await provider.fetch_candles("BBCA.JK", Horizon.D1, limit=10)

        assert len(candles) == 2
        assert [c.close for c in candles] == [Decimal("100.5"), Decimal("102.5")]


class TestPenjagaTidakKebablasan:
    """Yang ditolak adalah yang tidak berhingga, bukan yang tidak lazim."""

    @pytest.mark.parametrize(
        ("mentah", "diharapkan"),
        [
            ("0", Decimal("0")),
            ("-0", Decimal("-0")),
            ("1e40", Decimal("1E+40")),
            ("63690.92", Decimal("63690.92")),
            ("-15.5", Decimal("-15.5")),
        ],
    )
    def test_angka_berhingga_lewat_utuh(
        self, mentah: str, diharapkan: Decimal
    ) -> None:
        assert _optional_decimal(mentah) == diharapkan

    @pytest.mark.parametrize("busuk", ["NaN", "nan", "Infinity", "-Infinity", "inf"])
    def test_yang_tidak_berhingga_jadi_none(self, busuk: str) -> None:
        assert _optional_decimal(busuk) is None

    def test_yang_bukan_angka_tetap_none(self) -> None:
        assert _optional_decimal("halo") is None
        assert _optional_decimal(None) is None
        assert _optional_decimal("") is None
