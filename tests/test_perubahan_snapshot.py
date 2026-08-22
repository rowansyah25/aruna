"""Snapshot hanya disimpan kalau keadaannya benar-benar berubah (bagian 4-6).

Terukur 2026-08-21 di basis data produksi: `market_snapshots` berisi 422.172
baris dan 286 MB - 62% dari seluruh database - dan tumbuh **2.877 baris per
jam, sekitar 69.048 sehari**. Dari jumlah itu **60.227 baris redundan secara
isi**: harga, bid, ask, dan volume identik dengan baris lain untuk aset yang
sama.

Yang lebih menentukan daripada angka redundansi: sejarah tabel itu **tidak
punya satu pun pembaca**. Ketiga pemanggilnya - `agents/service.py`, bot
Telegram, dan permukaan pasar - semuanya membaca baris TERBARU per simbol.
Ratusan ribu baris disimpan; satu baris per simbol yang pernah dibaca.

Yang dijaga berkas ini bukan penghematannya melainkan **batasnya**. Pasar yang
benar-benar diam harus tetap meninggalkan jejak, supaya "tidak ada baris"
karena pasar diam tidak bisa disalahbaca sebagai "tidak ada baris" karena ARUNA
berhenti melihat. Itu `JEDA_WAJIB_DETIK`, dan test yang menjaganya ada di
bawah.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from aruna.core.enums import DataQuality, Market
from aruna.data.models import Provenance, Snapshot
from aruna.data.perubahan import (
    AMBANG_HARGA_PCT,
    AMBANG_SPREAD_BPS,
    AMBANG_VOLUME_PCT,
    JEDA_WAJIB_DETIK,
    Perubahan,
    layak_simpan,
)

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _snap(**kwargs: Any) -> Snapshot:
    """`Snapshot` sungguhan dengan bidang yang disalin dari baris produksi."""
    base: dict[str, Any] = {
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "captured_at": NOW,
        "last_price": Decimal("100"),
        "provenance": Provenance(source="test", server_timestamp=NOW),
        "bid": Decimal("99.9"),
        "ask": Decimal("100.1"),
        "spread_bps": Decimal("20"),
        "volume_24h": Decimal("1000"),
        "session": "OPEN",
        "market_open": True,
        "quality": DataQuality.OK,
    }
    return Snapshot(**(base | kwargs))


class TestYangSelaluDisimpan:
    def test_snapshot_pertama_selalu_disimpan(self) -> None:
        """Tidak ada pembanding berarti tidak ada dasar untuk melewatinya."""
        simpan, sebab = layak_simpan(_snap(), None, sejak_detik=0.0)

        assert simpan
        assert Perubahan.PERTAMA in sebab

    def test_pasar_diam_tetap_meninggalkan_jejak(self) -> None:
        """Batas yang menahan seluruh optimasi ini agar tidak menghapus
        informasi.

        Tanpa ini, "nol baris selama dua jam" punya dua arti yang tidak bisa
        dibedakan: pasar yang tidak bergerak, dan ARUNA yang berhenti melihat.
        Yang kedua adalah kegagalan yang harus terlihat.
        """
        simpan, sebab = layak_simpan(_snap(), _snap(), sejak_detik=JEDA_WAJIB_DETIK)

        assert simpan
        assert Perubahan.WAKTU in sebab

    def test_mutu_yang_berubah_selalu_disimpan(self) -> None:
        """Data yang tiba-tiba jelek adalah peristiwa, bukan pengulangan.

        Bagian 5 menyebut important risk event sebagai yang wajib disimpan, dan
        umpan yang basi persis itu - apalagi karena harganya justru **tidak**
        bergerak saat umpan mati, sehingga gerbang harga tidak akan
        menangkapnya.
        """
        simpan, sebab = layak_simpan(
            _snap(quality=DataQuality.STALE), _snap(quality=DataQuality.OK),
            sejak_detik=5.0,
        )

        assert simpan
        assert Perubahan.MUTU in sebab

    def test_bel_buka_dan_bel_tutup_disimpan(self) -> None:
        """Seluruh laporan harian IDX bersandar pada dua peristiwa ini."""
        simpan, sebab = layak_simpan(
            _snap(market_open=True), _snap(market_open=False), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.SESI in sebab

    def test_pergantian_sesi_disimpan(self) -> None:
        simpan, sebab = layak_simpan(
            _snap(session="CLOSED"), _snap(session="OPEN"), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.SESI in sebab


class TestYangDisimpanKarenaBergerak:
    def test_harga_melewati_ambang_disimpan(self) -> None:
        lewat = Decimal("100") * (1 + Decimal(str(AMBANG_HARGA_PCT)) / 100)
        simpan, sebab = layak_simpan(
            _snap(last_price=lewat + Decimal("0.01")), _snap(last_price=Decimal("100")),
            sejak_detik=5.0,
        )

        assert simpan
        assert Perubahan.HARGA in sebab

    def test_lonjakan_volume_disimpan(self) -> None:
        lewat = Decimal("1000") * (1 + Decimal(str(AMBANG_VOLUME_PCT)) / 100)
        simpan, sebab = layak_simpan(
            _snap(volume_24h=lewat + Decimal("1")), _snap(volume_24h=Decimal("1000")),
            sejak_detik=5.0,
        )

        assert simpan
        assert Perubahan.VOLUME in sebab

    def test_spread_melebar_disimpan(self) -> None:
        """Spread yang melebar adalah likuiditas yang menguap - dan itu
        keterangan risiko, bukan derau harga."""
        simpan, sebab = layak_simpan(
            _snap(spread_bps=Decimal("20") + Decimal(str(AMBANG_SPREAD_BPS)) + 1),
            _snap(spread_bps=Decimal("20")),
            sejak_detik=5.0,
        )

        assert simpan
        assert Perubahan.SPREAD in sebab

    def test_semua_sebab_disebut_bukan_yang_pertama_saja(self) -> None:
        """Satu sebab yang menutupi sebab lain membuat log tidak bisa menjawab
        kenapa sebuah baris ada - dan itu satu-satunya cara memeriksa gerbang
        ini di produksi."""
        simpan, sebab = layak_simpan(
            _snap(last_price=Decimal("200"), volume_24h=Decimal("5000")),
            _snap(last_price=Decimal("100"), volume_24h=Decimal("1000")),
            sejak_detik=5.0,
        )

        assert simpan
        assert {Perubahan.HARGA, Perubahan.VOLUME} <= sebab


class TestYangDilewati:
    def test_keadaan_identik_tidak_disimpan(self) -> None:
        simpan, sebab = layak_simpan(_snap(), _snap(), sejak_detik=5.0)

        assert not simpan
        assert not sebab

    def test_harga_bergetar_di_bawah_ambang_tidak_disimpan(self) -> None:
        """Ini yang 60.227 baris itu."""
        simpan, sebab = layak_simpan(
            _snap(last_price=Decimal("100.01")), _snap(last_price=Decimal("100.00")),
            sejak_detik=5.0,
        )

        assert not simpan
        assert not sebab

    def test_volume_merambat_di_bawah_ambang_tidak_disimpan(self) -> None:
        """`volume_24h` selalu naik sedikit demi sedikit sepanjang hari. Tanpa
        ambang, kolom ini sendirian membuat setiap snapshot tampak berubah dan
        gerbangnya tidak menahan apa pun."""
        simpan, sebab = layak_simpan(
            _snap(volume_24h=Decimal("1001")), _snap(volume_24h=Decimal("1000")),
            sejak_detik=5.0,
        )

        assert not simpan
        assert not sebab


class TestBatasAman:
    def test_harga_nol_tidak_membagi_dengan_nol(self) -> None:
        """Harga nol berarti umpan rusak, dan gerbang yang meledak di sana akan
        menjatuhkan seluruh lintasan poll."""
        simpan, sebab = layak_simpan(
            _snap(last_price=Decimal("5")), _snap(last_price=Decimal("0")),
            sejak_detik=5.0,
        )

        assert simpan
        assert Perubahan.HARGA in sebab

    def test_bidang_kosong_di_kedua_sisi_bukan_perubahan(self) -> None:
        """Provider IDX tidak mengirim `spread_bps`. Kalau `None` dibaca sebagai
        perubahan, gerbangnya lolos di setiap poll untuk seluruh pasar itu."""
        simpan, sebab = layak_simpan(
            _snap(spread_bps=None, volume_24h=None),
            _snap(spread_bps=None, volume_24h=None),
            sejak_detik=5.0,
        )

        assert not simpan
        assert not sebab

    def test_bidang_yang_baru_muncul_adalah_perubahan(self) -> None:
        """Sebaliknya: bidang yang tadinya kosong lalu terisi memang keterangan
        baru."""
        simpan, sebab = layak_simpan(
            _snap(spread_bps=Decimal("20")), _snap(spread_bps=None), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.SPREAD in sebab

    def test_jeda_wajib_tidak_menutupi_sebab_yang_sesungguhnya(self) -> None:
        """Kalau harga bergerak DAN jeda wajib lewat, keduanya harus tercatat -
        kalau tidak, log akan bilang "disimpan karena waktu" untuk baris yang
        sesungguhnya disimpan karena pasar bergerak."""
        _, sebab = layak_simpan(
            _snap(last_price=Decimal("200")), _snap(last_price=Decimal("100")),
            sejak_detik=JEDA_WAJIB_DETIK + 10,
        )

        assert {Perubahan.HARGA, Perubahan.WAKTU} <= sebab
