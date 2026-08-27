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
        self.tertunda: list[dict] = []
        self.hasil: list[dict] = []
        self.sejak_diminta = None

    async def perlu_dinilai(self, *, sejak):
        self.sejak_diminta = sejak
        return self.tertunda

    async def simpan_hasil(self, hasil, keputusan):
        self.hasil.append({"hasil": hasil, "keputusan": keputusan})
        return len(self.hasil)

    async def simpan(
        self, sinyal, *, as_of, decided_at, symbol="XAU/USD", bukti=None,
        regime=None, dolar=None,
    ):
        self.dolar_terakhir = dolar
        self.disimpan.append(
            {
                "sinyal": sinyal,
                "as_of": as_of,
                "decided_at": decided_at,
                "bukti": bukti,
                "regime": regime,
            }
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


class TestPenilaianTersambung:
    """Penilai yang ditulis tapi tak pernah dipanggil = `xau_results` kosong
    selamanya, dan Rencana 3 tidak punya bahan apa pun."""

    async def test_sinyal_lama_dinilai_tiap_tick(self) -> None:
        candles = _candles()
        repo = RepoPalsu()
        # Prediksi BUY dari 60 bar lalu: horizon 48 bar sudah tuntas.
        repo.tertunda = [
            {
                "id": 7,
                "keputusan": "BUY",
                "as_of": candles[-60].open_time,
                "entry": Decimal("1000"),
                "stop": Decimal("900"),
                "target": Decimal("1100"),
                "atr": Decimal("5"),
                "sentuhan_target": 4,
            }
        ]
        await satu_tick(
            ProviderPalsu(candles), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert len(repo.hasil) == 1, "penilai tidak terpanggil dari loop"
        assert repo.hasil[0]["keputusan"] == "BUY"

    async def test_jalur_dimulai_SESUDAH_bar_keputusan(self) -> None:
        """Memasukkan bar keputusannya sendiri berarti menilai sinyal dengan
        harga yang sudah diketahui saat ia dibuat - look-ahead terbalik."""
        candles = _candles()
        repo = RepoPalsu()
        repo.tertunda = [
            {
                "id": 7,
                "keputusan": "BUY",
                "as_of": candles[-60].open_time,
                "entry": Decimal("1000"),
                "stop": Decimal("900"),
                "target": Decimal("1100"),
                "atr": Decimal("5"),
                "sentuhan_target": 4,
            }
        ]
        await satu_tick(
            ProviderPalsu(candles), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert repo.sejak_diminta == candles[0].open_time

    async def test_horizon_belum_tuntas_dilewati_bukan_dinilai(self) -> None:
        """Dinilai sekarang = tiap sinyal yang masih berjalan dihitung gagal."""
        candles = _candles()
        repo = RepoPalsu()
        repo.tertunda = [
            {
                "id": 8,
                "keputusan": "BUY",
                "as_of": candles[-10].open_time,  # baru 10 bar, horizon 48
                "entry": Decimal("1000"),
                "stop": Decimal("900"),
                "target": Decimal("1100"),
                "atr": Decimal("5"),
                "sentuhan_target": 4,
            }
        ]
        await satu_tick(
            ProviderPalsu(candles), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert repo.hasil == []

    async def test_tanpa_repo_tidak_menilai_apa_apa(self) -> None:
        hasil = await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG
        )
        assert hasil.menilai is True


class TestBuktiIkutTersimpan:
    """Diukur di produksi 2026-08-27: `xau_evidence` NOL baris sesudah dua
    keputusan. Tabelnya ada dan loop tidak pernah mengoper isinya - persis
    "tabel ada bukan berarti terisi"."""

    async def test_rezim_ikut_tersimpan(self) -> None:
        """Gerbang UNKNOWN_REGIME memblokir 17,4% keputusan - diukur atas 17
        hari, 396 jendela M5. Tanpa kolomnya, angka itu tak pernah bisa
        disandingkan dengan hasil keputusannya."""
        repo = RepoPalsu()
        await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG, repo=repo
        )
        regime = repo.disimpan[0]["regime"]
        assert regime is not None
        assert regime.regime.value
        assert regime.evidence_available > 0

    async def test_bacaan_indikator_ikut(self) -> None:
        repo = RepoPalsu()
        await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG, repo=repo
        )
        bukti = repo.disimpan[0]["bukti"]
        assert bukti, "bukti tidak dioper; xau_evidence akan selamanya kosong"
        assert "5m" in bukti
        assert "atr" in bukti["5m"]

    async def test_bacaan_membawa_sample_size_dan_required(self) -> None:
        """Indikator yang bahannya kurang BUKAN indikator yang nilainya kecil."""
        repo = RepoPalsu()
        await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG, repo=repo
        )
        nilai, sample_size, required = repo.disimpan[0]["bukti"]["5m"]["atr"]
        assert nilai is not None
        assert sample_size > 0
        assert required > 0

    async def test_timeframe_besar_ikut(self) -> None:
        repo = RepoPalsu()
        await satu_tick(
            ProviderPalsu(_candles()), _gate(), sekarang=SEKARANG, repo=repo
        )
        assert set(repo.disimpan[0]["bukti"]) >= {"5m", "15m", "1h"}


class TestBarBelumBerganti:
    """Kunci unik `(setup_id, as_of)` dilanggar kalau satu bar dinilai dua kali.

    Bukan kasus langka: tiap restart supervisor memulai loop dari nol di tengah
    bar yang sedang berjalan, dan drift jadwal apa pun menghasilkan dua tick
    dalam satu jendela 300 detik. Akibatnya galat basis data, loop mati,
    supervisor menyalakan ulang - dan itu crash loop yang menyalakan dirinya
    sendiri setiap lima menit.
    """

    async def test_bar_sama_dilewati_tanpa_menulis(self) -> None:
        candles = _candles()
        repo = RepoPalsu()
        provider = ProviderPalsu(candles)

        pertama = await satu_tick(
            provider, _gate(), sekarang=SEKARANG, repo=repo
        )
        kedua = await satu_tick(
            provider,
            _gate(),
            sekarang=SEKARANG + timedelta(seconds=30),
            repo=repo,
            as_of_terakhir=candles[-1].close_time,
        )

        assert pertama.menilai is True
        assert kedua.menilai is False
        assert "belum berganti" in kedua.alasan_lewat
        assert len(repo.disimpan) == 1, "satu bar tidak boleh menghasilkan dua baris"

    async def test_bar_baru_dinilai_lagi(self) -> None:
        candles = _candles()
        repo = RepoPalsu()
        hasil = await satu_tick(
            ProviderPalsu(candles),
            _gate(),
            sekarang=SEKARANG,
            repo=repo,
            as_of_terakhir=candles[-1].close_time - timedelta(minutes=5),
        )
        assert hasil.menilai is True
        assert len(repo.disimpan) == 1

    async def test_as_of_dikembalikan_supaya_pemanggil_bisa_mengingatnya(self) -> None:
        candles = _candles()
        hasil = await satu_tick(
            ProviderPalsu(candles), _gate(), sekarang=SEKARANG
        )
        assert hasil.as_of == candles[-1].close_time


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
