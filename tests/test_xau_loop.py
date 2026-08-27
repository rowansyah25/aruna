"""Satu siklus keputusan XAU, ujung ke ujung tanpa jaringan dan tanpa MySQL.

Berdiri di atas provider palsu yang mengembalikan candle sungguhan dan
repositori palsu yang merekam apa yang akan ditulis. Yang diuji adalah RANTAI -
bahwa tiap tahap benar-benar memanggil tahap berikutnya, dan bahwa penolakan di
tengah tetap tersimpan.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import Decision, Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.models import Candle, Provenance
from aruna.data.quality import QualityGate
from aruna.xau.loop import BAR_DIBUTUHKAN, satu_tick

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
PROV = Provenance(source="twelvedata")


def _candles(jumlah: int = 250, *, mulai: datetime = AWAL) -> list[Candle]:
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = mulai + timedelta(minutes=5 * i)
        h = Decimal(str(round(1000 + 30 * math.sin(i / 8), 2)))
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=h,
                high=h + 2,
                low=h - 2,
                close=h,
                volume=Decimal(0),
                provenance=PROV,
                is_closed=True,
            )
        )
    return keluar


class ProviderPalsu:
    def __init__(self, candles, *, gagal: str | None = None) -> None:
        self._candles = candles
        self._gagal = gagal
        self.panggilan = 0
        self.limit_diminta: list[int] = []

    async def fetch_candles(self, symbol, interval, *, limit=500, **kw):
        self.panggilan += 1
        self.limit_diminta.append(limit)
        if self._gagal:
            raise DataSourceUnavailableError(self._gagal)
        return self._candles

    async def fetch_quote(self, symbol):  # pragma: no cover - harus tak terpanggil
        raise AssertionError(
            "loop memanggil /quote; harganya harus datang dari bar terakhir"
        )


class RepoPalsu:
    def __init__(self) -> None:
        self.disimpan: list[dict] = []

    async def simpan(self, sinyal, *, as_of, decided_at, symbol="XAU/USD"):
        self.disimpan.append(
            {"sinyal": sinyal, "as_of": as_of, "decided_at": decided_at}
        )
        return len(self.disimpan)


def _gate() -> QualityGate:
    return QualityGate(DataSettings(_env_file=None), source="twelvedata")


SEKARANG = AWAL + timedelta(minutes=5 * 250)


class TestSatuTick:
    async def test_menghasilkan_keputusan_dan_menyimpannya(self) -> None:
        repo = RepoPalsu()
        hasil = await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert hasil.menilai is True
        assert hasil.sinyal.keputusan in (
            Decision.BUY,
            Decision.SELL,
            Decision.NO_SIGNAL,
        )
        assert len(repo.disimpan) == 1

    async def test_kosakata_keluaran_tidak_pernah_wait(self) -> None:
        hasil = await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG
        )
        assert hasil.sinyal.keputusan is not Decision.WAIT

    async def test_harga_keputusan_dari_bar_bukan_quote(self) -> None:
        """Quote diambil SESUDAH bar tutup; harganya lebih baru dari buktinya.

        ``ProviderPalsu.fetch_quote`` melempar kalau dipanggil, jadi test ini
        merah begitu loop kembali memakai quote.
        """
        candles = _candles()
        repo = RepoPalsu()
        await satu_tick(
            ProviderPalsu(candles), _gate(), sekarang=SEKARANG, repo=repo
        )
        sinyal = repo.disimpan[0]["sinyal"]
        if sinyal.geometri is not None:
            assert sinyal.geometri.entry == candles[-1].close

    async def test_satu_kredit_per_tick(self) -> None:
        """288 kredit/hari, bukan 576 - quote kedua akan melipatgandakannya."""
        provider = ProviderPalsu(_candles())
        await satu_tick(provider, _gate(), sekarang=SEKARANG)
        assert provider.panggilan == 1

    async def test_menarik_bar_yang_cukup_untuk_h4(self) -> None:
        provider = ProviderPalsu(_candles())
        await satu_tick(provider, _gate(), sekarang=SEKARANG)
        assert provider.limit_diminta == [BAR_DIBUTUHKAN]


class TestPenolakanTetapTersimpan:
    async def test_data_basi_tersimpan_sebagai_no_signal(self) -> None:
        repo = RepoPalsu()
        hasil = await satu_tick(
            ProviderPalsu(_candles()),
            _gate(),
            sekarang=SEKARANG + timedelta(hours=3),
            repo=repo,
        )
        assert hasil.sinyal.keputusan is Decision.NO_SIGNAL
        assert "basi" in hasil.sinyal.alasan.lower()
        assert len(repo.disimpan) == 1, "penolakan harus tetap tersimpan"

    async def test_bahan_kurang_tersimpan_sebagai_no_signal(self) -> None:
        repo = RepoPalsu()
        sedikit = _candles(20)
        hasil = await satu_tick(
            ProviderPalsu(sedikit),
            _gate(),
            sekarang=sedikit[-1].close_time,
            repo=repo,
        )
        assert hasil.sinyal.keputusan is Decision.NO_SIGNAL
        assert len(repo.disimpan) == 1


class TestKegagalanTarikBukanPenilaian:
    async def test_venue_gagal_tidak_menulis_baris(self) -> None:
        """NO SIGNAL berarti ARUNA menilai lalu diam.

        Venue yang tidak menjawab bukan penilaian; menyimpannya sebagai
        keputusan akan mencemari statistik "seberapa sering XAU diam" dengan
        menit-menit ketika ARUNA tidak sempat bertanya sama sekali.
        """
        repo = RepoPalsu()
        hasil = await satu_tick(
            ProviderPalsu([], gagal="jatah kredit habis"),
            _gate(),
            sekarang=SEKARANG,
            repo=repo,
        )
        assert hasil.menilai is False
        assert "kredit" in hasil.alasan_lewat
        assert repo.disimpan == [], "kegagalan tarik tidak boleh jadi baris keputusan"

    async def test_venue_kosong_juga_dilewati(self) -> None:
        repo = RepoPalsu()
        hasil = await satu_tick(
            ProviderPalsu([]), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert hasil.menilai is False
        assert repo.disimpan == []


class TestTanpaRepo:
    async def test_berjalan_tanpa_basis_data(self) -> None:
        """Loop harus bisa diuji dan dijalankan kering tanpa MySQL."""
        hasil = await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG
        )
        assert hasil.menilai is True
        assert hasil.prediction_id is None


class TestSupervisor:
    def test_tiga_proses_dijaga(self) -> None:
        """Menambah proses ketiga adalah persis momen salah satu yang lama
        bisa hilang tanpa seorang pun menyadarinya sampai futures diam sehari."""
        from aruna.supervisor import default_children

        nama = [c.name for c in default_children("BTCUSDT", hours=24.0)]
        assert "aruna-run" in nama
        assert "futures-loop" in nama
        assert "xau-loop" in nama

    def test_xau_loop_tidak_menyentuh_argumen_futures(self) -> None:
        from aruna.supervisor import default_children

        anak = {c.name: c.args for c in default_children("BTCUSDT", hours=24.0)}
        assert "--equity" not in anak["xau-loop"]
        assert "--risk" not in anak["xau-loop"]
        assert "--equity" in anak["futures-loop"], "argumen futures harus utuh"
