"""Daftar perpetual datang dari konfigurasi, bukan dari bawaan argumen CLI.

``ARUNA.bat`` memanggil ``supervise`` tanpa argumen sama sekali. Selama daftar
simbolnya adalah bawaan argumen, bawaan itu selalu menang - dan mengubah
cakupan berarti mengedit kode, bukan konfigurasi.

Simbolnya sendiri diverifikasi ke bursa, bukan ditulis dari ingatan. Keduanya
berbeda: ``MATIC`` sudah diganti ``POL``, dan ``TON`` tidak lolos pemeriksaan
TRADING+PERPETUAL. Simbol yang salah tidak gagal dengan berisik - ia muncul
sebagai penarikan candle yang kosong berhari-hari, yang terlihat persis seperti
pasar yang sepi.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from aruna.core.config import UpkeepSettings
from aruna.core.enums import Market

AKAR = pathlib.Path(__file__).resolve().parent.parent


def _upkeep(**overrides) -> UpkeepSettings:
    return UpkeepSettings(_env_file=None, **overrides)


class TestDaftarDariKonfigurasi:
    def test_sembilan_belas_perpetual(self) -> None:
        """Dua puluh sampai 2026-08-25, lalu BTCUSDT dikeluarkan.

        Bukan karena pendapat tentang BTC - karena aritmetika bursa. Langkah
        kuantitas terkecilnya 0,001 BTC, dan pada harga $78.840 itu bernilai
        $78,84. Dengan stop khas 3%, posisi terkecil yang mungkin ada
        mempertaruhkan $2,37, jadi ia baru muat di akun $118 pada risiko 2% -
        di atas ekuitas yang dikonfigurasi.
        """
        assert len(_upkeep().futures_symbol_list) == 19

    def test_btc_dikeluarkan_selama_ekuitasnya_belum_cukup(self) -> None:
        """Dipasangkan dengan ekuitasnya, supaya keduanya tidak bisa berselisih
        diam-diam: kalau ekuitas dinaikkan melewati $118, test ini yang
        mengingatkan bahwa BTCUSDT boleh kembali."""
        s = _upkeep()
        assert "BTCUSDT" not in s.futures_symbol_list
        assert s.futures_equity < 118, (
            "ekuitas sudah melewati $118 - BTCUSDT bisa di-size lagi, jadi "
            "keputusan mengeluarkannya perlu ditinjau ulang"
        )

    def test_dipisah_koma_dan_dibersihkan(self) -> None:
        s = _upkeep(futures_symbols=" btcusdt , ethusdt ,, ")
        assert s.futures_symbol_list == ("BTCUSDT", "ETHUSDT")

    def test_daftar_kosong_menghasilkan_tuple_kosong(self) -> None:
        """Bukan tuple berisi satu string kosong, yang akan diteruskan ke loop
        sebagai simbol bernama ''."""
        assert _upkeep(futures_symbols="  ").futures_symbol_list == ()

    def test_semuanya_pair_usdt(self) -> None:
        """PASAL 33: crypto hanya pair USDT."""
        for simbol in _upkeep().futures_symbol_list:
            assert simbol.endswith("USDT"), simbol

    def test_tidak_memuat_nama_yang_sudah_diganti(self) -> None:
        """``MATIC`` diganti ``POL`` di bursa. Nama lama tidak akan pernah
        mengembalikan candle."""
        daftar = _upkeep().futures_symbol_list
        assert "MATICUSDT" not in daftar
        assert "POLUSDT" in daftar

    def test_tidak_ada_yang_kembar(self) -> None:
        daftar = _upkeep().futures_symbol_list
        assert len(set(daftar)) == len(daftar)


class TestSupervisorMemakainya:
    def test_bawaan_argumennya_kosong(self) -> None:
        """Bawaan berisi simbol tidak bisa dibedakan dari pilihan operator, dan
        akan selalu menang atas konfigurasi - diam-diam."""
        from aruna.cli import build_parser

        args = build_parser().parse_args(["supervise"])
        assert args.symbols is None

    def test_argumen_eksplisit_tetap_menang(self) -> None:
        from aruna.cli import build_parser

        args = build_parser().parse_args(["supervise", "--symbols", "BTCUSDT"])
        assert args.symbols == "BTCUSDT"

    def test_perintahnya_membaca_konfigurasi_saat_argumen_kosong(self) -> None:
        import inspect

        from aruna import cli

        sumber = inspect.getsource(cli.cmd_supervise)
        assert "args.symbols or settings.upkeep.futures_symbols" in sumber


class TestBerkasUniverse:
    """``config/universe.json`` menimpa daftar bawaan. Kalau ia ada tapi salah
    bentuk, seeding gagal - dan seeding yang gagal berarti tidak ada aset."""

    def _muat(self) -> list[dict]:
        berkas = AKAR / "config" / "universe.json"
        if not berkas.is_file():
            pytest.skip("config/universe.json tidak ada di lingkungan ini")
        return json.loads(berkas.read_text(encoding="utf-8"))

    def test_bisa_dibaca_lapisan_seed(self) -> None:
        from aruna.seed.universe import load_universe

        specs = load_universe(AKAR / "config" / "universe.json")
        assert len(specs) == len(self._muat())

    def test_kelas_asetnya_yang_diterima_skema(self) -> None:
        """Skemanya membatasi kolom ini lewat CHECK constraint, dan "CRYPTO" -
        yang terlihat masuk akal - bukan salah satu nilainya. Versi pertama
        berkas ini memakainya, dan seeding ditolak database."""
        diizinkan = {"CRYPTO_SPOT", "CRYPTO_PERP", "IDX_EQUITY"}
        for baris in self._muat():
            assert baris["asset_class"] in diizinkan, baris["symbol"]

    def test_crypto_semuanya_usdt(self) -> None:
        from aruna.data.crypto.symbols import split_canonical

        for baris in self._muat():
            if baris["market"] != Market.CRYPTO.value:
                continue
            # Melempar kalau quote-nya bukan USDT (PASAL 33).
            split_canonical(baris["symbol"])

    def test_idx_ikut_terbawa(self) -> None:
        """Berkas ini menimpa seluruh daftar bawaan, jadi menghilangkan IDX di
        sini akan menghapus sebelas saham dari universe."""
        pasar = [b["market"] for b in self._muat()]
        assert pasar.count(Market.IDX.value) == 11

    def test_setiap_perpetual_punya_pasangan_spot(self) -> None:
        """Rekam jejak futures dan spot hanya bisa dibandingkan kalau keduanya
        menonton aset yang sama."""
        from aruna.data.crypto.symbols import to_venue_symbol

        spot = {
            to_venue_symbol(b["symbol"])
            for b in self._muat()
            if b["market"] == Market.CRYPTO.value
        }
        for simbol in _upkeep().futures_symbol_list:
            assert simbol in spot, simbol
