"""Bagian 16.19 benar-benar berjalan, dan menilai dengan aturan yang sama.

**Ditemukan 2026-08-22.** `aruna.scenario.evaluasi` ditulis, diuji, diekspor -
dan punya **nol pemanggil** di seluruh `src/`. Begitu juga `belum_dinilai`,
`catat_hasil`, dan `ringkas_akurasi` di repositorinya. Tiap skenario tersimpan
dengan ``hasil`` NULL selamanya, dan angka akurasi yang seluruh pasalnya tuntut
tidak pernah ada.

Cacat yang sama sudah muncul tiga kali di proyek ini: `AdaptiveLearningService`
yang hanya berjalan lewat perintah manual, pembersih retensi yang lengkap dan
tidak pernah menyapu, penilai PASAL 15.44 yang menghitung putusan yang tidak
pernah ditulis. Semuanya lulus test unitnya.

Dua hal yang dijaga di sini, dan yang kedua tidak kalah penting:

* fasenya **dipanggil** - dari `app.py`, lewat loop, sampai ke repositori;
* ia menilai dengan **klasifikator yang sama** yang menghasilkan skenarionya.
  Aturan yang berbeda antara menghasilkan dan menilai membuat angkanya mengukur
  sesuatu yang lain, dan angka itu tidak mengatakan apa pun tentang mesinnya.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from aruna.scenario.evaluasi import MINIMUM_TITIK, nilai_dari_pasar
from aruna.scenario.kerumunan import AMBANG_ARAH, klasifikasi_jejak
from aruna.scenario.models import HasilSkenario, Invalidasi, Skenario
from aruna.upkeep.skenario_nilai import BAR_HORIZON, PenilaiSkenario

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _skenario(nama: str) -> Skenario:
    return Skenario(
        scenario_id=f"s-{nama}",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama=nama,
        deskripsi="",
        kondisi_awal=(),
        pemicu="",
        perkembangan=(),
        invalidasi=Invalidasi(syarat=("s",)),
        risiko="MEDIUM",
        keyakinan=0.5,
        bobot=50,
        bukti=(),
        versi_simulasi="internal-2",
    )


NAIK = (0.0, 0.3, 0.6, 0.9, 1.2)
TURUN = (0.0, -0.3, -0.6, -0.9, -1.2)
PALSU = (0.0, 0.5, 1.0, 0.4, -0.1)
SEPI = (0.0, 0.05, -0.02, 0.03, 0.01)


class TestAturanYangSama:
    """Klasifikator yang berbeda antara menghasilkan dan menilai membuat
    angkanya mengukur sesuatu yang lain."""

    def test_memakai_klasifikasi_jejak(self) -> None:
        """Penjaga AST. Sebuah salinan aturan di modul evaluasi akan melenceng
        dari mesinnya tanpa satu pun test merah."""
        from aruna.scenario import evaluasi

        pohon = ast.parse(inspect.getsource(evaluasi.nilai_dari_pasar).lstrip())
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "klasifikasi_jejak" in dipanggil

    @pytest.mark.parametrize(
        ("jejak", "keluarga"),
        [
            (NAIK, "Bullish Continuation"),
            (TURUN, "Bearish Reversal"),
            (PALSU, "False Breakout"),
            (SEPI, "Sideways"),
        ],
    )
    def test_jejak_uji_memang_keluarga_itu(self, jejak, keluarga) -> None:
        """Kalau data ujinya sendiri salah kelas, seluruh test di bawah menguji
        hal yang berbeda dari yang tertulis di namanya."""
        assert klasifikasi_jejak(jejak) == keluarga


class TestTigaPutusan:
    def test_keluarga_cocok_berarti_benar(self) -> None:
        p = nilai_dari_pasar(_skenario("Bullish Continuation"), jejak=NAIK)

        assert p.hasil is HasilSkenario.BENAR

    def test_keluarga_berlawanan_berarti_salah(self) -> None:
        p = nilai_dari_pasar(_skenario("Bullish Continuation"), jejak=TURUN)

        assert p.hasil is HasilSkenario.SALAH

    def test_arah_benar_bentuk_salah_berarti_sebagian(self) -> None:
        """Naik lalu kembali di atas nol: bukan `Bullish Continuation`, tapi
        arah yang diklaim keluarga itu tetap terjadi."""
        setengah = (0.0, 0.2, 0.5, 0.3, 0.25)
        assert klasifikasi_jejak(setengah) != "Bullish Continuation"

        p = nilai_dari_pasar(_skenario("Bullish Continuation"), jejak=setengah)

        assert p.hasil is HasilSkenario.SEBAGIAN

    def test_keluarga_bentuk_tidak_punya_sebagian(self) -> None:
        """`Sideways` mengklaim bentuk, bukan arah - dan bentuk terjadi atau
        tidak. `SEBAGIAN` di sini akan memberi nilai separuh untuk klaim yang
        tidak pernah dibuat."""
        p = nilai_dari_pasar(_skenario("Sideways"), jejak=NAIK)

        assert p.hasil is HasilSkenario.SALAH

    def test_alasannya_menyebut_keduanya(self) -> None:
        p = nilai_dari_pasar(_skenario("Sideways"), jejak=NAIK)

        assert "Sideways" in p.alasan
        assert "Bullish Continuation" in p.alasan


class TestBelumBukanSalah:
    def test_horizon_belum_selesai(self) -> None:
        p = nilai_dari_pasar(
            _skenario("Bullish Continuation"), jejak=NAIK, horizon_selesai=False
        )

        assert p.hasil is HasilSkenario.BELUM

    def test_titik_terlalu_sedikit(self) -> None:
        """Skenario yang dinilai dari dua bar dihukum karena waktu belum
        berjalan - persis yang `BELUM` ada untuk mencegahnya."""
        p = nilai_dari_pasar(_skenario("Bullish Continuation"), jejak=(0.0, 0.9))

        assert p.hasil is HasilSkenario.BELUM
        assert str(MINIMUM_TITIK) in p.alasan

    def test_jejak_kosong(self) -> None:
        p = nilai_dari_pasar(_skenario("Sideways"), jejak=())

        assert p.hasil is HasilSkenario.BELUM


class _Repo:
    def __init__(self, baris=None, gagal_tulis: bool = False) -> None:
        self.baris = baris or []
        self.gagal_tulis = gagal_tulis
        self.dicatat: list[tuple[str, HasilSkenario]] = []
        #: Bendera syarat-batal per skenario, terpisah dari `dicatat` supaya
        #: test lama tetap membaca bentuk yang sama.
        self.batal: list[bool | None] = []
        self.diminta = 0
        self.diringkas = 0
        self.peringatan_diminta = 0
        self.ringkasan: list[dict] = []
        self.peringatan: list[dict] = []

    async def belum_dinilai(self, *, sampai, limit):
        self.diminta += 1
        self.sampai = sampai
        return list(self.baris)

    async def catat_hasil(self, scenario_id, hasil, *, pada, diinvalidasi=None):
        # Tanda tangannya HARUS sama dengan `ScenarioRepository.catat_hasil`.
        # Double yang bidangnya berbeda dari objek asli membuat suite hijau di
        # atas bug produksi - dan `diinvalidasi` yang hilang di sini akan
        # menyembunyikan persis sambungan yang sedang diuji.
        if self.gagal_tulis:
            raise RuntimeError("database jatuh")
        self.dicatat.append((scenario_id, hasil))
        self.batal.append(diinvalidasi)
        return True

    # Kedua metode di bawah ADA di `ScenarioRepository`, dan tanpanya jalur
    # pelaporan menabrak AttributeError yang ditelan `log.exception` - hijau di
    # test, diam di produksi. Double yang bidangnya kurang dari objek aslinya
    # tidak menguji apa pun tentang jalur yang memakainya.
    async def ringkas_per_simulasi(self):
        self.diringkas += 1
        return list(self.ringkasan)

    async def ringkas_peringatan(self):
        self.peringatan_diminta += 1
        return list(self.peringatan)


class _Universe:
    def __init__(self, id_aset: int | None = 7) -> None:
        self.id_aset = id_aset
        self.dicari = 0

    async def find(self, market, symbol):
        self.dicari += 1
        if self.id_aset is None:
            return None
        return type("A", (), {"id": self.id_aset})()


class _MarketData:
    """Bar 15m: dua puluh bar sebelum lahir, lalu bar sesudahnya."""

    def __init__(self, sesudah: list[float], *, rentang: float = 1.0) -> None:
        self._sesudah = sesudah
        self._rentang = rentang

    async def candles_between(self, asset_id, interval, *, mulai, sampai, limit=500):
        self.diminta = (mulai, sampai)
        keluar = []
        harga = 100.0
        for i in range(20):
            t = NOW - timedelta(minutes=15 * (20 - i))
            keluar.append({
                "close_time": t,
                "open": harga, "high": harga + self._rentang / 2,
                "low": harga - self._rentang / 2, "close": harga,
            })
        for i, delta in enumerate(self._sesudah, start=1):
            t = NOW + timedelta(minutes=15 * i)
            nilai = harga + delta
            keluar.append({
                "close_time": t,
                "open": nilai, "high": nilai, "low": nilai, "close": nilai,
            })
        return keluar


def _baris(nama: str = "Bullish Continuation") -> dict:
    return {
        "scenario_id": "s-1",
        "market_code": "CRYPTO",
        "asset": "BTC/USDT",
        "dibuat_pada": NOW,
        "nama": nama,
        "bobot": 50,
        "versi_simulasi": "internal-2",
    }


def _penilai(repo, sesudah, universe=None) -> PenilaiSkenario:
    return PenilaiSkenario(
        repo=repo,
        market_data=_MarketData(sesudah),
        universe=universe or _Universe(),
    )


@pytest.mark.asyncio
class TestSapuanMenulisHasil:
    async def test_skenario_benar_dicatat(self) -> None:
        repo = _Repo([_baris("Bullish Continuation")])
        # Naik dua ATR: jelas `Bullish Continuation`.
        hasil = await _penilai(repo, [0.5, 1.0, 1.5, 2.0]).nilai(now=NOW)

        assert hasil["dinilai"] == 1
        assert repo.dicatat == [("s-1", HasilSkenario.BENAR)]

    async def test_syarat_batal_sampai_ke_repositori(self) -> None:
        """**Sambungan yang paling sering putus di repo ini.** Bagian 16.19
        menuntut dua kegagalan dinilai terpisah, dan sebelum 2026-08-23
        `nilai_dari_pasar` menuliskan `diinvalidasi=False` di keenam jalurnya
        sementara `catat_hasil` tidak menerimanya sama sekali. Jadi pembedaannya
        benar di `evaluasi.py`, benar di skema, dan tidak pernah bertemu.

        Jejaknya menembus naik lalu jatuh dan BERTAHAN di bawah garis lahir -
        persis syarat batal `Bullish Continuation`.
        """
        repo = _Repo([_baris("Bullish Continuation")])
        await _penilai(repo, [0.8, -0.4, -0.8, -1.2]).nilai(now=NOW)

        assert repo.dicatat == [("s-1", HasilSkenario.SALAH)]
        assert repo.batal == [True]

    async def test_peringatan_dilaporkan_bukan_berhenti_di_basis_data(
        self,
    ) -> None:
        """Bagian 16.19 menutup dengan "Gunakan untuk evaluasi", dan angka yang
        tidak sampai ke siapa pun tidak dipakai siapa pun. `ringkas_akurasi`
        sudah ada sejak awal dan tidak pernah punya satu pun pemanggil - cacat
        yang persis sama."""
        repo = _Repo([_baris("Bullish Continuation")])
        repo.peringatan = [{
            "versi_simulasi": "internal-2",
            "salah": 10,
            "memperingatkan": 6,
            "diam": 2,
            "tak_terperiksa": 2,
        }]
        await _penilai(repo, [0.8, -0.4, -0.8, -1.2]).nilai(now=NOW)

        assert repo.peringatan_diminta == 1

    async def test_semua_tak_terperiksa_tidak_membagi_nol(self) -> None:
        """**Bug produksi, 2026-08-23, satu menit sesudah kolomnya dipasang.**
        Seluruh 928 baris SALAH yang sudah ada dinilai oleh kode yang belum
        memeriksa syarat batalnya, jadi semuanya NULL dan yang bisa diperiksa
        berjumlah nol. `ZeroDivisionError` menjatuhkan seluruh sapuan penilaian.

        Penyebut nol di sini bukan kesalahan pemanggil - ia keadaan yang sah
        yang berarti "belum ada yang bisa diperiksa".
        """
        repo = _Repo([_baris("Bullish Continuation")])
        repo.peringatan = [{
            "versi_simulasi": "internal-2",
            "salah": 928,
            "memperingatkan": 0,
            "diam": 0,
            "tak_terperiksa": 928,
        }]

        hasil = await _penilai(repo, [0.8, -0.4, -0.8, -1.2]).nilai(now=NOW)

        assert hasil["gagal"] == 0
        assert repo.peringatan_diminta == 1

    async def test_yang_tak_bisa_diperiksa_tersimpan_sebagai_none(self) -> None:
        """`News-Driven Reversal` batal kalau "berita terbantah" - dan itu tidak
        ada di jejak harga. `None`, bukan `False`."""
        repo = _Repo([_baris("News-Driven Reversal")])
        await _penilai(repo, [0.5, 1.0, 1.5, 2.0]).nilai(now=NOW)

        assert repo.batal == [None]

    async def test_skenario_salah_juga_dicatat(self) -> None:
        """Yang salah **harus** tercatat. Menilai hanya yang benar menghasilkan
        akurasi seratus persen atas mesin apa pun."""
        repo = _Repo([_baris("Bullish Continuation")])
        await _penilai(repo, [-0.5, -1.0, -1.5, -2.0]).nilai(now=NOW)

        assert repo.dicatat == [("s-1", HasilSkenario.SALAH)]

    async def test_candle_belum_cukup_tidak_dicatat(self) -> None:
        """`BELUM` tidak pernah ditulis - menuliskannya mengeluarkan baris itu
        dari antrean selamanya."""
        repo = _Repo([_baris()])
        hasil = await _penilai(repo, [0.5]).nilai(now=NOW)

        assert repo.dicatat == []
        assert hasil["belum"] == 1

    async def test_candle_diambil_di_sekitar_kelahirannya(self) -> None:
        """**Bug produksi, 2026-08-22.** Versi pertama mengambil bar TERBARU,
        jadi skenario berumur tiga belas jam mendapat jendela yang mulai empat
        jam sesudah ia lahir - empat puluh dari empat puluh dilaporkan belum
        bisa dinilai, dan tunggakannya tidak akan pernah terkuras.
        """
        repo = _Repo([_baris()])
        pasar = _MarketData([0.5, 1.0, 1.5, 2.0])
        p = PenilaiSkenario(repo=repo, market_data=pasar, universe=_Universe())

        await p.nilai(now=NOW + timedelta(hours=13))

        mulai, sampai = pasar.diminta
        assert mulai < NOW, "jendela harus mencakup bar SEBELUM kelahirannya"
        assert sampai > NOW, "jendela harus mencakup bar SESUDAH kelahirannya"

    async def test_hanya_yang_horizonnya_lewat_yang_diminta(self) -> None:
        """Batas waktunya harus mundur setidaknya selama horizonnya, atau
        skenario dinilai sebelum pasarnya sempat bergerak."""
        repo = _Repo([])
        await _penilai(repo, []).nilai(now=NOW)

        assert repo.sampai <= NOW - timedelta(minutes=15 * BAR_HORIZON)


@pytest.mark.asyncio
class TestTidakMenjatuhkanSiklus:
    async def test_antrean_gagal_tidak_melempar(self) -> None:
        class _Rusak(_Repo):
            async def belum_dinilai(self, *, sampai, limit):
                raise RuntimeError("database jatuh")

        hasil = await _penilai(_Rusak(), []).nilai(now=NOW)

        assert hasil["gagal"] == 1

    async def test_tulis_gagal_tidak_melempar(self) -> None:
        repo = _Repo([_baris()], gagal_tulis=True)
        hasil = await _penilai(repo, [0.5, 1.0, 1.5, 2.0]).nilai(now=NOW)

        assert hasil["gagal"] == 1

    async def test_aset_tak_dikenal_dilewati(self) -> None:
        repo = _Repo([_baris()])
        hasil = await _penilai(repo, [0.5, 1.0, 1.5, 2.0], _Universe(None)).nilai(
            now=NOW
        )

        assert hasil["belum"] == 1
        assert repo.dicatat == []

    async def test_id_aset_dicari_sekali(self) -> None:
        """Empat puluh skenario dari segelintir simbol yang sama; mencarinya
        berulang adalah kueri yang jawabannya tidak berubah."""
        uni = _Universe()
        repo = _Repo([_baris(), _baris(), _baris()])
        await _penilai(repo, [0.5, 1.0, 1.5, 2.0], uni).nilai(now=NOW)

        assert uni.dicari == 1


@pytest.mark.asyncio
class TestAkurasiDilaporkan:
    """**Dua cacat sekaligus, ditemukan 2026-08-22.**

    `ringkas_akurasi` ada sejak awal dan **tidak pernah punya satu pun
    pemanggil** - penilaian berhenti di basis data, dan bagian 16.19 menutup
    dengan "Gunakan untuk evaluasi". Angka yang tidak sampai ke siapa pun tidak
    dipakai siapa pun.

    Dan yang akan dilaporkannya menyesatkan. Tiap simulasi menghasilkan
    beberapa skenario dan hanya satu keluarga yang terjadi, jadi "pangsa
    skenario yang BENAR" dibatasi ``1/N``. Terukur: `internal-1` melaporkan
    **22,9%** dengan batas atas struktural **33,3%** - terlihat seperti mutu
    tanpa menjadi mutu.

    Ukuran yang berarti: **cakupan** (keluarga yang terjadi ada di antara
    skenarionya - menguji kosakata) dan **teratas** (yang berbobot tertinggi
    ternyata benar - menguji pembobotan). Pada data yang sama, teratas
    `internal-1` adalah **0 dari 163**.
    """

    class _RepoRingkas(_Repo):
        def __init__(self, baris, ringkas) -> None:
            super().__init__(baris)
            self._ringkas = ringkas
            self.diminta_ringkas = 0

        async def ringkas_per_simulasi(self):
            self.diminta_ringkas += 1
            return self._ringkas

    async def test_dilaporkan_sesudah_ada_yang_dinilai(self) -> None:
        repo = self._RepoRingkas(
            [_baris()],
            [{"versi_simulasi": "internal-2", "simulasi": 300, "cakupan": 250,
              "teratas": 120}],
        )
        await _penilai(repo, [0.5, 1.0, 1.5, 2.0]).nilai(now=NOW)

        assert repo.diminta_ringkas == 1

    async def test_tidak_dilaporkan_kalau_tak_ada_yang_baru(self) -> None:
        """Melaporkan angka yang sama tiap sapuan membuat log berhenti
        dibaca."""
        repo = self._RepoRingkas([], [])
        await _penilai(repo, []).nilai(now=NOW)

        assert repo.diminta_ringkas == 0

    async def test_ringkas_gagal_tidak_menjatuhkan_sapuan(self) -> None:
        class _Rusak(_Repo):
            async def ringkas_per_simulasi(self):
                raise RuntimeError("database jatuh")

        repo = _Rusak([_baris()])
        hasil = await _penilai(repo, [0.5, 1.0, 1.5, 2.0]).nilai(now=NOW)

        assert hasil["dinilai"] == 1


class TestBagianDitahanSampaiCukup:
    def test_pecahannya_selalu_ditulis(self) -> None:
        """"7/17" tidak boleh terbaca sebagai "41%" oleh mata yang buru-buru."""
        from aruna.upkeep.skenario_nilai import _bagian

        assert "7/17" in _bagian(7, 17, cukup=False)
        assert "7/17" in _bagian(7, 17, cukup=True)

    def test_persennya_ditahan_sampai_sampelnya_cukup(self) -> None:
        from aruna.upkeep.skenario_nilai import _bagian

        assert "%" not in _bagian(7, 17, cukup=False)
        assert "ditahan" in _bagian(7, 17, cukup=False)

    def test_persennya_keluar_saat_cukup(self) -> None:
        from aruna.upkeep.skenario_nilai import _bagian

        assert "50.0%" in _bagian(100, 200, cukup=True)

    def test_nol_bukan_kosong(self) -> None:
        """Nol yang hilang dari log terbaca sebagai tidak diukur."""
        from aruna.upkeep.skenario_nilai import _bagian

        assert "0/163" in _bagian(0, 163, cukup=True)


class TestUkuranYangBerarti:
    """Penjaga atas kueri ringkasnya sendiri."""

    def test_mengelompokkan_per_simulasi_bukan_per_skenario(self) -> None:
        """Pangsa per-skenario dibatasi 1/N dan tidak mengukur mutu. Yang harus
        dihitung: per simulasi."""
        import inspect

        from aruna.db.repositories.scenario import ScenarioRepository

        sql = inspect.getsource(ScenarioRepository.ringkas_per_simulasi)

        assert "asset, dibuat_pada" in sql, "harus dikelompokkan per simulasi"
        assert "ROW_NUMBER" in sql, "peringkat bobot dibutuhkan untuk `teratas`"

    def test_teratas_memakai_pemecah_seri_yang_pasti(self) -> None:
        """Dua skenario berbobot sama akan bertukar peringkat antar jalan, dan
        `teratas` yang berubah-ubah mengukur dua hal di bawah satu nama."""
        import inspect

        from aruna.db.repositories.scenario import ScenarioRepository

        sql = inspect.getsource(ScenarioRepository.ringkas_per_simulasi)

        assert "bobot DESC, nama ASC" in sql


class TestBenarBenarTerpasang:
    """Bug aslinya bukan logika yang salah - melainkan logika yang benar dan
    tidak pernah dipanggil."""

    def test_loop_menerima_penilainya(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        assert "scenario_nilai" in inspect.signature(UpkeepLoop.__init__).parameters

    def test_app_mengoper_ke_upkeeploop(self) -> None:
        """AST, dan menuntut ia sampai ke `UpkeepLoop` - bukan sekadar muncul
        sebagai kata kunci di suatu panggilan."""
        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        for n in ast.walk(pohon):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "UpkeepLoop"
                and any(kw.arg == "scenario_nilai" for kw in n.keywords)
            ):
                return

        pytest.fail("scenario_nilai tidak sampai ke UpkeepLoop")

    def test_loop_memanggil_nilai(self) -> None:
        from aruna.upkeep import loop

        sumber = inspect.getsource(loop.UpkeepLoop._nilai_skenario)

        assert "_scenario_nilai.nilai" in sumber

    def test_fasenya_dipanggil_dari_cycle(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        sumber = inspect.getsource(UpkeepLoop.cycle)

        assert "_nilai_skenario" in sumber

    def test_stats_membedakan_nol_dari_mati(self) -> None:
        from aruna.upkeep.loop import UpkeepStats

        s = UpkeepStats(started_at=NOW)

        assert s.last_skenario_nilai_at is None
        assert s.skenario_dinilai == 0


def test_ambang_arah_konsisten_dengan_data_uji() -> None:
    """Jejak uji di atas dipilih relatif terhadap ambangnya. Kalau ambangnya
    berubah, test ini yang memberi tahu - bukan kegagalan yang membingungkan
    di tempat lain."""
    assert NAIK[-1] > AMBANG_ARAH
    assert TURUN[-1] < -AMBANG_ARAH
