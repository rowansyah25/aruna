"""Apakah Phase 16 benar-benar dipanggil (bagian 16.17).

Berkas ini menjaga satu cacat yang sudah berulang tiga kali di proyek ini:
kode yang benar, diuji, diekspor, dan tidak pernah dipanggil.
`AdaptiveLearningService` berjalan hanya saat seseorang mengetik `aruna learn`.
Pembersih retensi lengkap dan tidak pernah menyapu. Penilai PASAL 15.44
menghitung putusan yang tidak pernah ditulis. Ketiganya lulus seluruh testnya.

Penjaganya berbasis AST dan bukan pencarian teks, dengan alasan yang sudah
terbukti di berkas ini sendiri: komentar di `app.py` **menyebut** `scenario=`
untuk menjelaskan kenapa barisnya ada, dan pencarian teks akan lulus atas
komentar yang menjelaskan baris yang sudah dihapus.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime

import pytest

from aruna.scanner.events import EventKind, ScanResult, SignificantEvent
from aruna.scenario.pemicu import Peristiwa
from aruna.upkeep.skenario import (
    BATAS_PER_SIKLUS,
    MINIMUM_KOHORT,
    PenyimulasiSkenario,
    _arah_kohort,
    _kondisi,
)

NOW = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)


def _event(kind: EventKind, severity: float, measured: float) -> SignificantEvent:
    return SignificantEvent(
        symbol="BTCUSDT",
        kind=kind,
        severity=severity,
        detail=f"{kind.value} terukur {measured}",
        at=NOW,
        evidence={"measured": measured, "threshold": measured / severity},
    )


def _hasil(symbol: str, *events: SignificantEvent, scanned: bool = True) -> ScanResult:
    return ScanResult(
        symbol=symbol, events=tuple(events), usable_bars=50, scanned=scanned
    )


def _tembusan(symbol: str = "BTCUSDT", severity: float = 3.0) -> ScanResult:
    return _hasil(symbol, _event(EventKind.BREAKOUT, severity, 1.0))


def _terjun(symbol: str = "BTCUSDT", severity: float = 3.0) -> ScanResult:
    return _hasil(symbol, _event(EventKind.BREAKDOWN, severity, 1.0))


class TestArahKohort:
    """Fase ini satu-satunya yang memegang seluruh aset sekaligus.

    Deteksi pemicu bekerja per aset, jadi arah kohort hanya bisa lahir di sini.
    Kalau fungsi ini salah, `KONFLIK_LINTAS_PASAR` diam tanpa satu pun galat.
    """

    def test_mayoritas_naik(self) -> None:
        arah = _arah_kohort(
            [_tembusan("A"), _tembusan("B"), _tembusan("C"), _terjun("D")]
        )

        assert arah == 1

    def test_mayoritas_turun(self) -> None:
        arah = _arah_kohort(
            [_terjun("A"), _terjun("B"), _terjun("C"), _tembusan("D")]
        )

        assert arah == -1

    def test_seri_tidak_punya_arah(self) -> None:
        """Separuh naik separuh turun adalah pasar yang terbelah. Memilih salah
        satunya berarti mengarang mayoritas."""
        arah = _arah_kohort(
            [_tembusan("A"), _tembusan("B"), _terjun("C"), _terjun("D")]
        )

        assert arah is None

    def test_di_bawah_minimum_tidak_punya_arah(self) -> None:
        """Dua aset bukan pasar. Tanpa lantai ini satu aset yang bergerak
        melawan satu tetangganya sudah cukup untuk menyalakan konflik."""
        arah = _arah_kohort([_tembusan("A"), _tembusan("B")])

        assert arah is None
        assert MINIMUM_KOHORT == 3

    def test_yang_tak_terpindai_tidak_ikut_menghitung(self) -> None:
        """`scanned=False` berarti buktinya tidak cukup. Aset seperti itu tidak
        boleh menyumbang suara ke arah kohort."""
        hasil = [
            _tembusan("A"),
            _tembusan("B"),
            _hasil("C", _event(EventKind.BREAKOUT, 3.0, 1.0), scanned=False),
        ]

        assert _arah_kohort(hasil) is None

    def test_satu_aset_menyumbang_satu_suara(self) -> None:
        """Aset yang menembus lalu terjun di bar yang sama tidak boleh
        menyumbang dua suara berlawanan."""
        bimbang = _hasil(
            "A",
            _event(EventKind.BREAKOUT, 3.0, 1.0),
            _event(EventKind.BREAKDOWN, 3.0, 1.0),
        )

        assert _arah_kohort([bimbang, _tembusan("B"), _tembusan("C")]) == 1

    def test_pasar_sepi_tidak_bisa_dikonfliki(self) -> None:
        """Lonjakan volume tanpa arah bukan arah. `None`, bukan nol."""
        sepi = [
            _hasil(s, _event(EventKind.VOLUME_SPIKE, 1.4, 9.9))
            for s in ("A", "B", "C", "D")
        ]

        assert _arah_kohort(sepi) is None


class _RepoPalsu:
    def __init__(self) -> None:
        self.dipanggil: list[tuple[int, str]] = []

    async def simpan(self, skenario, *, sumber="INTERNAL") -> int:
        self.dipanggil.append((len(skenario), sumber))
        return len(skenario)


class TestTersambungKeLoop:
    """Yang menahan cacat berulangnya."""

    def test_loop_menerima_scenario(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        assert "scenario" in inspect.signature(UpkeepLoop.__init__).parameters

    def test_app_benar_benar_mengoper_scenario(self) -> None:
        """AST atas argumen kata kunci yang sungguh dioper. Komentar di
        `app.py` menyebut `scenario=` untuk menjelaskan kenapa barisnya ada -
        pencarian teks akan lulus atas komentar yang menjelaskan baris yang
        sudah dihapus."""
        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        dioper = {
            kw.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            for kw in n.keywords
        }

        assert "scenario" in dioper

    def test_app_punya_pembangunnya(self) -> None:
        from aruna.app import ArunaApplication

        assert hasattr(ArunaApplication, "_build_scenario")

    def test_scenario_dioper_ke_upkeeploop_bukan_ke_sembarang_panggilan(self) -> None:
        """`scenario=` yang dioper ke fungsi lain akan lolos test di atas.
        Yang dituntut adalah ia sampai ke `UpkeepLoop`."""
        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        sampai = False
        for n in ast.walk(pohon):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "UpkeepLoop"
                and any(kw.arg == "scenario" for kw in n.keywords)
            ):
                sampai = True

        assert sampai

    def test_loop_benar_benar_memanggil_penyimulasinya(self) -> None:
        """Parameter yang diterima lalu disimpan tanpa pernah dipakai adalah
        bentuk cacat yang sama, satu lapis lebih dalam."""
        from aruna.upkeep import loop

        pohon = ast.parse(inspect.getsource(loop))
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "jalankan" in dipanggil

    def test_fasenya_dipanggil_dari_scan(self) -> None:
        """Bagian 16.17 menaruh MIROFISH TRIGGER tepat sesudah EVENT DETECTOR,
        dan `results` adalah keluaran pendeteksi peristiwa."""
        from aruna.upkeep.loop import UpkeepLoop

        sumber = inspect.getsource(UpkeepLoop._scan)

        assert "_simulasi_skenario" in sumber


@pytest.mark.asyncio
class TestBerjalanHanyaSaatPemicuMenyala:
    """Bagian 16.2: JANGAN menjalankan simulasi pada setiap market scan."""

    async def test_pemindaian_sepi_tidak_menyimulasikan(self) -> None:
        repo = _RepoPalsu()
        hasil = await PenyimulasiSkenario(repo=repo).jalankan(
            [_hasil("BTCUSDT"), _hasil("ETHUSDT")], now=NOW
        )

        assert hasil.menyala == 0
        assert repo.dipanggil == []

    async def test_breakout_kecil_tidak_menyimulasikan(self) -> None:
        """Pemindai menyalakan BREAKOUT di 0,25 ATR; bagian 16.2 minta
        *major* breakout."""
        repo = _RepoPalsu()
        hasil = await PenyimulasiSkenario(repo=repo).jalankan(
            [_tembusan(severity=1.2)], now=NOW
        )

        assert hasil.menyala == 0

    async def test_breakout_besar_menyimulasikan(self) -> None:
        repo = _RepoPalsu()
        hasil = await PenyimulasiSkenario(repo=repo).jalankan(
            [_tembusan()], now=NOW
        )

        assert hasil.menyala == 1
        assert hasil.disimpan >= 3

    async def test_melawan_kohort_menyimulasikan_walau_tembusannya_kecil(
        self,
    ) -> None:
        """Sambungan antara `_arah_kohort` dan `deteksi`.

        Keempat aset di bawah menembus terlalu kecil untuk menyalakan apa pun
        sendirian - satu-satunya yang bisa menyalakan simulasi di sini adalah
        arah kohort yang dihitung dari keempatnya sekaligus. Tanpa sambungan
        itu, `_arah_kohort` akan tetap benar dan `deteksi` akan tetap benar
        sementara pemicunya diam selamanya.
        """
        repo = _RepoPalsu()
        hasil = await PenyimulasiSkenario(repo=repo).jalankan(
            [
                _tembusan("BTCUSDT", severity=1.2),
                _tembusan("ETHUSDT", severity=1.2),
                _tembusan("SOLUSDT", severity=1.2),
                _terjun("ADAUSDT", severity=1.2),
            ],
            now=NOW,
        )

        assert hasil.dipertimbangkan == 4
        assert hasil.menyala == 1
        # Menyala saja tidak cukup: `susun_pertanyaan` menolak pertanyaan tanpa
        # kondisi, jadi pemicu yang menyala tanpa kalimatnya berakhir sebagai
        # "masukan ditolak" di log dan nol baris di tabel.
        assert hasil.disimpan >= 3

    async def test_konflik_kohort_menyebut_alasannya(self) -> None:
        """Bukti yang tersimpan harus mengatakan MENGAPA ia disimulasikan.
        "BREAKDOWN terukur 1.0" tidak menyebut kohort sama sekali."""
        kondisi = _kondisi(
            _terjun("ADAUSDT", severity=1.2),
            frozenset({Peristiwa.KONFLIK_LINTAS_PASAR}),
            None,
            arah_kohort=1,
        )

        assert any("melawan kohortnya" in k for k in kondisi)

    async def test_kohort_searah_tidak_menyimulasikan(self) -> None:
        """Kendali untuk test di atas: hapus perlawanannya, dan tidak ada yang
        tersisa untuk menyalakan simulasi."""
        hasil = await PenyimulasiSkenario().jalankan(
            [
                _tembusan("BTCUSDT", severity=1.2),
                _tembusan("ETHUSDT", severity=1.2),
                _tembusan("SOLUSDT", severity=1.2),
                _tembusan("ADAUSDT", severity=1.2),
            ],
            now=NOW,
        )

        assert hasil.dipertimbangkan == 4
        assert hasil.menyala == 0

    async def test_yang_tidak_terpindai_dilewati(self) -> None:
        """`scanned=False` berarti buktinya tidak cukup untuk membentuk garis
        dasar - bukan pasar yang tenang. Menyimulasikannya berarti membangun
        skenario di atas ketidaktahuan."""
        hasil = await PenyimulasiSkenario().jalankan(
            [_hasil("BTCUSDT", _event(EventKind.BREAKOUT, 3.0, 1.0), scanned=False)],
            now=NOW,
        )

        assert hasil.dipertimbangkan == 0
        assert hasil.menyala == 0


@pytest.mark.asyncio
class TestSatuSimulasiPerBar:
    """**Lahir dari bug produksi, 2026-08-22.**

    Pemindai berjalan tiap siklus dan menilai bar tertutup yang sama sampai bar
    berikutnya datang. Satu tembusan AVAX/USDT karena itu tersimpan **empat
    kali** - `scenario_id` berbeda karena stempel detiknya berbeda, jadi
    `INSERT IGNORE` tidak menahannya, dan jumlah bobot yang seharusnya seratus
    terbaca empat ratus.

    Ini mode kegagalan yang sama persis dengan `market_snapshots`, yang tumbuh
    jadi 62% basis data karena tiap amatan ditulis apa adanya. Seluruh test yang
    kutulis sebelumnya lolos: masing-masing memanggil `jalankan` sekali, dan
    satu panggilan tidak pernah bisa menunjukkan pengulangan.
    """

    async def test_siklus_kedua_pada_bar_sama_tidak_menyimpan_lagi(self) -> None:
        repo = _RepoPalsu()
        p = PenyimulasiSkenario(repo=repo)
        hasil = _tembusan()

        pertama = await p.jalankan([hasil], now=NOW)
        kedua = await p.jalankan([hasil], now=NOW)

        assert pertama.disimpan > 0
        assert kedua.disimpan == 0

    async def test_pengulangannya_dicatat_bukan_disembunyikan(self) -> None:
        """Nol tersimpan karena barnya sudah disimulasikan dan nol tersimpan
        karena pemicunya tidak menyala terlihat sama dari luar."""
        p = PenyimulasiSkenario()
        hasil = _tembusan()

        await p.jalankan([hasil], now=NOW)
        kedua = await p.jalankan([hasil], now=NOW)

        assert kedua.menyala == 1
        assert kedua.sudah_disimulasikan == 1

    async def test_bar_baru_disimulasikan_lagi(self) -> None:
        """Penjaganya harus melepaskan saat barnya berganti - kalau tidak, satu
        aset berhenti disimulasikan selamanya sesudah tembusan pertamanya.

        Barnya diambil dari **waktu siklus**, bukan dari stempel peristiwanya.
        Sejak regime, mutu, dan selisih pendapat ikut menyalakan pemicu, sebuah
        aset bisa menyala tanpa satu pun peristiwa pemindai - dan kunci yang
        diturunkan dari stempel peristiwa tidak punya nilai untuk dipakai.
        """
        from datetime import timedelta

        repo = _RepoPalsu()
        p = PenyimulasiSkenario(repo=repo)

        await p.jalankan([_tembusan()], now=NOW)
        kedua = await p.jalankan([_tembusan()], now=NOW + timedelta(minutes=15))

        assert kedua.disimpan > 0

    async def test_aset_lain_tidak_ikut_terkunci(self) -> None:
        """Kuncinya per simbol. Satu kunci global akan membuat tembusan BTC
        membungkam simulasi ETH pada bar yang sama."""
        repo = _RepoPalsu()
        p = PenyimulasiSkenario(repo=repo)

        await p.jalankan([_tembusan("BTCUSDT")], now=NOW)
        lain = await p.jalankan([_tembusan("ETHUSDT")], now=NOW)

        assert lain.disimpan > 0

    async def test_simulasi_yang_gagal_tidak_dicoba_ulang_tiap_siklus(self) -> None:
        """Distempel pada percobaan, bukan keberhasilan - disiplin yang sama
        dengan fase harian di `upkeep/loop.py`."""

        class _RepoRusak:
            def __init__(self) -> None:
                self.percobaan = 0

            async def simpan(self, skenario, *, sumber="INTERNAL"):
                self.percobaan += 1
                raise RuntimeError("database jatuh")

        repo = _RepoRusak()
        p = PenyimulasiSkenario(repo=repo)
        hasil = _tembusan()

        await p.jalankan([hasil], now=NOW)
        sesudah_pertama = repo.percobaan
        await p.jalankan([hasil], now=NOW)

        assert repo.percobaan == sesudah_pertama


@pytest.mark.asyncio
class TestBatasSumberDaya:
    """Bagian 16.14."""

    async def test_batas_per_siklus_menggigit(self) -> None:
        banyak = [
            _tembusan(f"SYM{i}USDT", severity=3.0 + i)
            for i in range(BATAS_PER_SIKLUS + 4)
        ]

        hasil = await PenyimulasiSkenario().jalankan(banyak, now=NOW)

        assert hasil.menyala == len(banyak)
        assert hasil.ditunda == 4
        assert len(hasil.bukti) == BATAS_PER_SIKLUS

    async def test_yang_tersisih_yang_peristiwanya_paling_lemah(self) -> None:
        """Bukan yang namanya paling belakang di abjad: urutan abjad tidak
        menyatakan apa pun tentang mana yang paling layak disimulasikan."""
        banyak = [
            _tembusan("AAAUSDT", severity=99.0),
            *[
                _tembusan(f"SYM{i}USDT", severity=3.0)
                for i in range(BATAS_PER_SIKLUS)
            ],
        ]

        hasil = await PenyimulasiSkenario().jalankan(banyak, now=NOW)

        assert "AAAUSDT" in {b.asset for b in hasil.bukti}

    async def test_berurutan_bukan_serentak(self) -> None:
        """Lima simulasi serentak yang masing-masing menulis delapan baris
        adalah persis lonjakan SQL yang bagian 16.14 larang.

        AST, dan ini kali ketiga alasan yang sama muncul di Phase 16: komentar
        di `jalankan` MENJELASKAN kenapa ia tidak memakai `gather`, jadi
        pencarian teks gagal justru karena penjelasannya benar.
        """
        import textwrap

        pohon = ast.parse(
            textwrap.dedent(inspect.getsource(PenyimulasiSkenario.jalankan))
        )
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        } | {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert not (dipanggil & {"gather", "TaskGroup", "create_task", "as_completed"})

    async def test_internal_dan_eksternal_disimpan_terpisah(self) -> None:
        """Hasil dua mesin yang dinilai dalam satu angka akurasi tidak
        mengatakan apa pun tentang keduanya (bagian 16.19)."""
        repo = _RepoPalsu()
        await PenyimulasiSkenario(repo=repo).jalankan([_tembusan()], now=NOW)

        assert all(sumber == "INTERNAL" for _, sumber in repo.dipanggil)


@pytest.mark.asyncio
class TestTidakMenjatuhkanSiklus:
    async def test_repo_yang_meledak_tidak_melempar(self) -> None:
        """Fase ini menghasilkan bukti, bukan keputusan. Siklus yang sama juga
        menghasilkan keputusan sungguhan, dan itu yang tidak boleh jatuh."""

        class _RepoRusak:
            async def simpan(self, skenario, *, sumber="INTERNAL"):
                raise RuntimeError("database jatuh")

        hasil = await PenyimulasiSkenario(repo=_RepoRusak()).jalankan(
            [_tembusan()], now=NOW
        )

        assert hasil.menyala == 1
        assert hasil.disimpan == 0

    async def test_tanpa_repo_buktinya_tetap_dihasilkan(self) -> None:
        hasil = await PenyimulasiSkenario().jalankan([_tembusan()], now=NOW)

        assert len(hasil.bukti) == 1
        assert hasil.disimpan == 0

    async def test_mesin_eksternal_absen_berarti_degraded(self) -> None:
        """Bagian 16.12: MiroFish tidak ada, dan jalur DEGRADED-nya dijalankan
        tiap siklus alih-alih disimpan sebagai cabang yang belum pernah
        diambil."""
        from aruna.scenario.adapter import StatusSimulasi

        hasil = await PenyimulasiSkenario().jalankan([_tembusan()], now=NOW)

        assert hasil.bukti[0].status_eksternal is StatusSimulasi.DEGRADED


@pytest.mark.asyncio
class TestNolDicatat:
    async def test_dipertimbangkan_dihitung_walau_nol_menyala(self) -> None:
        """Nol karena tidak ada peristiwa dan nol karena fasenya tidak pernah
        dipanggil terlihat sama dari luar. `dipertimbangkan` yang bukan nol
        membedakannya."""
        hasil = await PenyimulasiSkenario().jalankan(
            [_hasil("BTCUSDT"), _hasil("ETHUSDT")], now=NOW
        )

        assert hasil.dipertimbangkan == 2
        assert hasil.menyala == 0


class TestStatsMembedakanNolDariMati:
    """Kelas terpisah karena keduanya sinkron: `@pytest.mark.asyncio` pada
    fungsi biasa diterima diam-diam oleh pytest lalu diperingatkan, dan
    peringatan yang dibiarkan menumpuk berhenti dibaca."""

    def test_stats_punya_bidangnya(self) -> None:
        from aruna.upkeep.loop import UpkeepStats

        s = UpkeepStats(started_at=NOW)

        for bidang in (
            "scenario_menyala", "scenario_disimpan", "scenario_failures",
            "last_scenario_at", "scenario_enabled",
        ):
            assert hasattr(s, bidang), bidang

    def test_last_scenario_at_menandai_fase_yang_tak_pernah_jalan(self) -> None:
        """`None` berarti fasenya tidak pernah dipanggil sama sekali - yang
        berbeda dari dipanggil dan tidak menemukan apa-apa."""
        from aruna.upkeep.loop import UpkeepStats

        assert UpkeepStats(started_at=NOW).last_scenario_at is None
