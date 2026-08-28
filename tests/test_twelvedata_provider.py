"""Adapter Twelve Data untuk XAU/USD (Rencana 1, Task 3).

Tidak ada jaringan di berkas ini. Dua lapis, dan yang dipilih menentukan apa
yang sanggup dilihat sebuah tes:

* :func:`buat_provider` menukar **hanya soketnya**: ``TwelveDataForexProvider``
  membangun ``HttpFetcher`` aslinya sendiri persis seperti di produksi, jadi
  loop percobaan ulang dan himpunan status yang diuji adalah yang benar-benar
  dikirim. Tiap permintaan yang sampai ke bawah dicatat - jumlah permintaan
  itulah inti tes 429, dan angka itu tidak ada di lapisan mana pun selain ini.

* Tes yang tidak menyentuh HTTP sama sekali (jatah kredit, kemampuan) berdiri
  langsung di atas objeknya.

Pelajaran ini dipinjam dari ``test_binance_spot.py``: di sana tes rate-limit
pernah berjalan di atas fetcher palsu yang tidak punya loop ulang, sehingga
``len(calls) == 1`` benar tentang palsuannya dan salah tentang kode yang
dikirim. Sifat yang tinggal di ``HttpFetcher`` hanya bisa diuji lewat
``HttpFetcher``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from aruna.core.config import DataSettings, ProviderSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.forex.budget import KreditHarian
from aruna.data.forex.twelvedata import (
    MAX_BAR_PER_PERMINTAAN,
    SOURCE,
    TwelveDataForexProvider,
)

SAAT = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def buat_provider(
    handler: Any, *, api_key: str = "kunci-uji"
) -> tuple[TwelveDataForexProvider, list[httpx.Request]]:
    """Tumpukan transport asli dengan soket palsu di bawahnya."""
    terkirim: list[httpx.Request] = []

    def catat(request: httpx.Request) -> httpx.Response:
        terkirim.append(request)
        return handler(request)

    provider = TwelveDataForexProvider(
        DataSettings(_env_file=None),
        api_key=api_key,
        transport=httpx.MockTransport(catat),
    )
    return provider, terkirim


def balas(payload: dict, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=json.dumps(payload))

    return handler


def _bar(waktu: str, harga: str) -> dict[str, str]:
    return {
        "datetime": waktu,
        "open": harga,
        "high": harga,
        "low": harga,
        "close": harga,
    }


# ---------------------------------------------------------------------------
# Jatah kredit
# ---------------------------------------------------------------------------


class TestKreditHarian:
    def test_jatah_menit_membatasi(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        for _ in range(8):
            assert budget.minta(SAAT) is True
        assert budget.minta(SAAT) is False, "kredit ke-9 dalam menit sama harus ditolak"

    def test_menit_berikutnya_memulihkan(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        for _ in range(8):
            budget.minta(SAAT)
        assert budget.minta(SAAT + timedelta(minutes=1)) is True

    def test_jatah_harian_membatasi(self) -> None:
        budget = KreditHarian(per_hari=10, per_menit=8)
        diterima = sum(
            1 for i in range(20) if budget.minta(SAAT + timedelta(minutes=i))
        )
        assert diterima == 10

    def test_hari_berikutnya_memulihkan(self) -> None:
        budget = KreditHarian(per_hari=2, per_menit=8)
        budget.minta(SAAT)
        budget.minta(SAAT)
        assert budget.minta(SAAT) is False
        assert budget.minta(SAAT + timedelta(days=1)) is True

    def test_sisa_dilaporkan(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        budget.minta(SAAT)
        assert budget.sisa(SAAT) == 799

    def test_waktu_mundur_tidak_menambah_jatah(self) -> None:
        """Jam yang terkoreksi mundur tidak boleh jadi celah kredit gratis.

        Penghitungnya membandingkan menit, bukan mengurangkan: menit yang
        BERBEDA menggulung, dan menit sebelumnya tetap berbeda.
        """
        budget = KreditHarian(per_hari=2, per_menit=8)
        budget.minta(SAAT)
        budget.minta(SAAT)
        assert budget.sisa(SAAT) == 0
        assert budget.minta(SAAT - timedelta(minutes=1)) is False


# ---------------------------------------------------------------------------
# Kemampuan yang dinyatakan
# ---------------------------------------------------------------------------


class TestKemampuan:
    def test_hanya_m5_yang_didukung(self) -> None:
        """M15/H1/H4 dirakit lokal; adapter tidak boleh diam-diam meresample."""
        provider, _ = buat_provider(balas({}))
        assert provider.capabilities.supported_intervals == (Horizon.M5,)

    def test_market_adalah_forex(self) -> None:
        provider, _ = buat_provider(balas({}))
        assert provider.market is Market.FOREX

    def test_simbol_kanonik_jadi_bentuk_venue(self) -> None:
        provider, _ = buat_provider(balas({}))
        assert provider.provider_symbol("xau/usd") == "XAU/USD"

    def test_keterbatasan_volume_dinyatakan(self) -> None:
        """Valas spot tidak punya volume; itu harus tertulis, bukan tersirat."""
        provider, _ = buat_provider(balas({}))
        gabung = " ".join(provider.capabilities.limitations).lower()
        assert "volume" in gabung

    def test_kredensial_dinyatakan_wajib(self) -> None:
        provider, _ = buat_provider(balas({}))
        assert provider.capabilities.requires_credentials is True


# ---------------------------------------------------------------------------
# Penarikan candle
# ---------------------------------------------------------------------------


class TestFetchCandles:
    async def test_interval_selain_m5_ditolak(self) -> None:
        provider, terkirim = buat_provider(balas({}))
        with pytest.raises(ValueError, match="5m"):
            await provider.fetch_candles("XAU/USD", Horizon.H1)
        assert terkirim == [], "penolakan interval tidak boleh menembak venue"

    async def test_bar_dikembalikan_terlama_dulu(self) -> None:
        """Twelve Data mengirim terbaru dulu; kontrak ABC minta sebaliknya."""
        provider, _ = buat_provider(
            balas(
                {
                    "status": "ok",
                    "values": [
                        _bar("2026-08-27 10:05:00", "2"),
                        _bar("2026-08-27 10:00:00", "1"),
                    ],
                }
            )
        )

        candles = await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert [c.open for c in candles] == [Decimal("1"), Decimal("2")]
        assert candles[0].open_time < candles[1].open_time

    async def test_close_time_satu_bar_setelah_open(self) -> None:
        provider, _ = buat_provider(
            balas({"status": "ok", "values": [_bar("2026-08-27 10:00:00", "1")]})
        )
        candle = (await provider.fetch_candles("XAU/USD", Horizon.M5))[0]
        assert candle.close_time - candle.open_time == timedelta(minutes=5)
        assert candle.open_time.tzinfo is not None, "timestamp harus sadar zona waktu"

    async def test_bar_yang_belum_tutup_ditandai_terbuka(self) -> None:
        """Cacat data nyata, terukur di produksi 2026-08-28.

        Twelve Data mengembalikan bar yang SEDANG BERJALAN sebagai nilai
        terbaru. Menandainya tutup membuat tiap keputusan berdiri di atas bar
        yang high, low, dan close-nya masih akan berubah - dan seluruh mesin
        di hilir mempercayai penanda itu.
        """
        from aruna.core.clock import now_utc

        depan = now_utc() + timedelta(minutes=30)
        provider, _ = buat_provider(
            balas(
                {
                    "status": "ok",
                    "values": [_bar(depan.strftime("%Y-%m-%d %H:%M:%S"), "1")],
                }
            )
        )
        candle = (await provider.fetch_candles("XAU/USD", Horizon.M5))[0]
        assert candle.is_closed is False

    async def test_bar_lama_ditandai_tutup(self) -> None:
        provider, _ = buat_provider(
            balas({"status": "ok", "values": [_bar("2026-08-27 10:00:00", "1")]})
        )
        candle = (await provider.fetch_candles("XAU/USD", Horizon.M5))[0]
        assert candle.is_closed is True

    async def test_volume_nol_dan_turunannya_none(self) -> None:
        """Valas spot tak punya volume: 0 yang dinyatakan, bukan angka karangan."""
        provider, _ = buat_provider(
            balas({"status": "ok", "values": [_bar("2026-08-27 10:00:00", "1")]})
        )
        candle = (await provider.fetch_candles("XAU/USD", Horizon.M5))[0]

        assert candle.volume == Decimal(0)
        assert candle.quote_volume is None
        assert candle.trade_count is None

    async def test_galat_venue_jadi_data_source_unavailable(self) -> None:
        provider, _ = buat_provider(
            balas({"status": "error", "code": 429, "message": "API credits exceeded"})
        )
        with pytest.raises(DataSourceUnavailableError, match="429"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

    async def test_bar_rusak_menolak_alih_alih_menambal(self) -> None:
        """Bar tanpa harga penutupan tidak boleh diisi dari bar sebelahnya."""
        provider, _ = buat_provider(
            balas(
                {
                    "status": "ok",
                    "values": [{"datetime": "2026-08-27 10:00:00", "open": "1"}],
                }
            )
        )
        with pytest.raises(DataSourceUnavailableError, match="tidak terbaca"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

    async def test_kredit_habis_menolak_sebelum_menembak(self) -> None:
        """Jatah dijaga di sisi kita, bukan ditunggu sampai venue marah."""
        provider, terkirim = buat_provider(balas({"status": "ok", "values": []}))
        provider._budget = KreditHarian(per_hari=0, per_menit=8)

        with pytest.raises(DataSourceUnavailableError, match="kredit"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert terkirim == [], "kredit habis harus menolak TANPA menembak venue"

    async def test_permintaan_membawa_apikey_dan_interval(self) -> None:
        provider, terkirim = buat_provider(
            balas({"status": "ok", "values": []}), api_key="rahasia"
        )
        await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert len(terkirim) == 1
        params = terkirim[0].url.params
        assert params["apikey"] == "rahasia"
        assert params["interval"] == "5min"
        assert params["symbol"] == "XAU/USD"

    async def test_limit_tidak_melebihi_batas_endpoint(self) -> None:
        """5000 adalah batas endpoint; meminta lebih akan ditolak venue."""
        provider, terkirim = buat_provider(balas({"status": "ok", "values": []}))
        await provider.fetch_candles("XAU/USD", Horizon.M5, limit=99_999)

        assert terkirim[0].url.params["outputsize"] == str(MAX_BAR_PER_PERMINTAAN)


# ---------------------------------------------------------------------------
# Lapisan HTTP - hanya bisa diuji lewat HttpFetcher yang asli
# ---------------------------------------------------------------------------


class TestLapisanHttp:
    async def test_429_tidak_diulang(self) -> None:
        """Mengulang 429 menghabiskan tepat kredit yang venue keluhkan.

        Berdiri di atas ``HttpFetcher`` yang asli: himpunan status yang diulang
        adalah sifat MILIK fetcher, jadi fetcher palsu akan menyetujui angka
        apa pun yang tes ini tuliskan.
        """
        provider, terkirim = buat_provider(balas({"message": "rate limited"}, 429))

        with pytest.raises(DataSourceUnavailableError, match="429"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert len(terkirim) == 1, (
            f"429 diulang {len(terkirim)} kali; percobaan ulang harus dimatikan "
            "supaya 429 pertama sampai utuh ke penjaga kredit"
        )

    async def test_500_masih_diulang(self) -> None:
        """Hanya 429 yang dikecualikan; galat server sementara tetap layak ulang."""
        provider, terkirim = buat_provider(balas({"message": "boom"}, 500))

        with pytest.raises(DataSourceUnavailableError, match="500"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert len(terkirim) > 1, "500 sementara seharusnya diulang, bukan menyerah"

    async def test_non_json_jadi_pesan_yang_menyebut_sumbernya(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>maintenance</html>")

        provider, _ = buat_provider(handler)

        with pytest.raises(DataSourceUnavailableError, match="non-JSON"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)


# ---------------------------------------------------------------------------
# Tersambung ke jalur produksi
# ---------------------------------------------------------------------------


class TestTersambungKeJalurProduksi:
    """Adapter yang ditulis, diuji, dan diekspor tapi tidak pernah dirangkai
    adalah cacat berulang di proyek ini. Kelas ini berdiri di
    ``build_providers`` - fungsi yang benar-benar dipanggil ``app.py`` - bukan
    di konstruktor adapter."""

    def test_terdaftar_untuk_forex(self) -> None:
        from aruna.data.registry import available

        assert available(Market.FOREX) == (SOURCE,)

    def test_dibangun_lewat_jalur_yang_dipakai_app(self) -> None:
        from aruna.data.registry import build_providers

        dipilih = build_providers(
            ProviderSettings(_env_file=None, forex_provider_api_key="kunci"),
            DataSettings(_env_file=None),
            (Market.FOREX,),
        )
        assert isinstance(dipilih[Market.FOREX], TwelveDataForexProvider)

    def test_kunci_api_benar_benar_sampai_ke_adapter(self) -> None:
        """Kunci hidup di ProviderSettings; pabrik cuma menerima DataSettings.

        Sebelum ``build_providers`` mengopernya, kredensial wajib tidak punya
        jalur sama sekali menuju adapter - dan kegagalannya akan muncul
        berjam-jam kemudian sebagai galat autentikasi yang terbaca seperti
        venue mati.
        """
        from aruna.data.registry import build_providers

        dipilih = build_providers(
            ProviderSettings(_env_file=None, forex_provider_api_key="kunci-rahasia"),
            DataSettings(_env_file=None),
            (Market.FOREX,),
        )
        assert dipilih[Market.FOREX]._api_key == "kunci-rahasia"

    def test_tanpa_kunci_gagal_saat_startup_bukan_berjam_jam_kemudian(self) -> None:
        from aruna.core.errors import ConfigError
        from aruna.data.registry import build_providers

        with pytest.raises(ConfigError, match="requires an API key"):
            build_providers(
                ProviderSettings(_env_file=None, forex_provider_api_key=""),
                DataSettings(_env_file=None),
                (Market.FOREX,),
            )

    def test_forex_kosong_absen_bukan_diganti_feed_lain(self) -> None:
        from aruna.data.registry import build_providers

        dipilih = build_providers(
            ProviderSettings(_env_file=None, forex_provider=""),
            DataSettings(_env_file=None),
            (Market.FOREX,),
        )
        assert dipilih == {}

    def test_adapter_lama_tidak_terganggu_oleh_parameter_kunci(self) -> None:
        """Binance dan Yahoo tidak punya kredensial; pembungkusnya harus
        menelan kunci itu tanpa meneruskannya."""
        from aruna.data.registry import build_providers

        dipilih = build_providers(
            ProviderSettings(_env_file=None),
            DataSettings(_env_file=None),
            (Market.CRYPTO, Market.IDX),
        )
        assert set(dipilih) == {Market.CRYPTO, Market.IDX}
