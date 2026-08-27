"""FOREX dibuka dengan sengaja; ejaan lain tetap mati di depan.

Forex dicabut dari spec lama dan dijaga empat lapis. Modul XAUUSD M5
membukanya kembali - tapi hanya dalam satu ejaan. Berkas ini yang menjaga
bahwa "dibuka" tidak diam-diam berarti "dibuka lebar".
"""

from __future__ import annotations

import pytest

from aruna.core.enums import FORBIDDEN_MARKETS, Market, parse_market


class TestForexDibuka:
    def test_forex_adalah_anggota_market(self) -> None:
        assert Market.FOREX.value == "FOREX"

    @pytest.mark.parametrize("ejaan", ["FOREX", "forex", "  Forex  "])
    def test_ejaan_kanonik_diterima(self, ejaan: str) -> None:
        assert parse_market(ejaan) is Market.FOREX

    @pytest.mark.parametrize("alias", ["FX", "CURRENCY", "FOREIGN_EXCHANGE", "fx"])
    def test_alias_tetap_ditolak(self, alias: str) -> None:
        """Penjaga dipersempit, bukan dirobohkan: salah ketik tetap mati.

        Dicocokkan ke ``write FOREX``, bukan sekadar ``FOREX``: begitu FOREX
        jadi market sah ia muncul di daftar "Valid markets" pada SETIAP pesan
        galat, jadi mencocokkan namanya saja akan hijau bahkan kalau cabang
        alias ini terhapus seluruhnya.
        """
        with pytest.raises(ValueError, match="write FOREX"):
            parse_market(alias)

    def test_forex_bukan_lagi_kata_terlarang(self) -> None:
        assert "FOREX" not in FORBIDDEN_MARKETS

    def test_alias_masih_terdaftar_terlarang(self) -> None:
        assert {"FX", "CURRENCY", "FOREIGN_EXCHANGE"} <= FORBIDDEN_MARKETS

    def test_market_tak_dikenal_tetap_ditolak(self) -> None:
        """Membuka satu market tidak boleh melonggarkan parsernya."""
        with pytest.raises(ValueError, match="unknown market"):
            parse_market("SAHAM_US")
