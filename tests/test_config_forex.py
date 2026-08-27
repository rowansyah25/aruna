"""Konfigurasi provider forex, dan batas basi yang masuk akal untuk M5."""

from __future__ import annotations

from aruna.core.config import DataSettings, ProviderSettings
from aruna.core.enums import Market
from aruna.data.quality import QualityGate


def _gate() -> QualityGate:
    return QualityGate(DataSettings(_env_file=None), source="uji")


class TestProviderForex:
    def test_forex_provider_punya_default(self) -> None:
        assert ProviderSettings(_env_file=None).forex_provider == "twelvedata"

    def test_api_key_disembunyikan_dari_repr(self) -> None:
        """Kunci API tidak boleh bocor ke log lewat repr."""
        settings = ProviderSettings(_env_file=None, forex_provider_api_key="rahasia123")
        assert "rahasia123" not in repr(settings)
        assert settings.forex_provider_api_key.get_secret_value() == "rahasia123"

    def test_forex_kosong_menonaktifkan(self) -> None:
        settings = ProviderSettings(_env_file=None, forex_provider="")
        assert settings.configured["forex"] is False

    def test_configured_menyebut_forex(self) -> None:
        assert ProviderSettings(_env_file=None).configured["forex"] is True


class TestBatasBasiForex:
    def test_forex_lebih_longgar_dari_crypto(self) -> None:
        """Ambang crypto akan menandai SETIAP bacaan XAU basi.

        Crypto dinilai pada 60 detik karena tick-nya menerus. Bar M5 baru
        tutup tiap 300 detik, jadi menilai valas dengan angka itu berarti
        seluruh datanya ditolak - persis kegagalan yang docstring
        ``QualityGate`` sebut untuk feed saham.
        """
        gate = _gate()
        assert gate.staleness_limit(Market.FOREX) > gate.staleness_limit(Market.CRYPTO)

    def test_forex_memuat_dua_bar_m5(self) -> None:
        """Satu bar M5 = 300 detik.

        Batasnya harus menampung dua bar penuh - satu bar yang baru tutup plus
        satu yang tertunda - tapi tidak sampai tiga, karena di M5 harga tiga bar
        lalu sudah beda dunia.
        """
        batas = _gate().staleness_limit(Market.FOREX)
        assert 600 <= batas < 900

    def test_batas_publik_sama_dengan_yang_dipakai_menolak(self) -> None:
        """Pelapor dan penolak harus menyebut angka yang sama, bukan salinannya."""
        gate = _gate()
        for market in Market:
            assert gate.staleness_limit(market) == gate._staleness_limit(market)
