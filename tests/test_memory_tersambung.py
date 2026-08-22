"""Penjaga penyambungan: modul yang ditulis, diuji, dan tidak pernah dipanggil.

Keluarga cacat ini sudah berkali-kali muncul di proyek ini - delapan modul
``aruna.decision`` yang diam sepanjang Phase 14, mesin korelasi yang ada sejak
Phase 4 dengan tabel nol baris, dan `AdaptiveLearningService` yang hanya belajar
ketika seseorang mengetik perintahnya. Semuanya lolos seluruh unit test.

Berkas ini menguji **pemanggilnya**, bukan yang dipanggil. Dan ia memeriksanya
lewat AST, bukan lewat pencarian teks: ``"korelasi=" in inspect.getsource(app)``
tetap hijau ketika barisnya dikomentari - cacat yang sudah tertangkap sekali di
Phase 14 putaran keempat.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


class _Proyektor:
    def __init__(self) -> None:
        self.dipanggil = 0
        self.futures = 0
        self.sampai: list[datetime] = []

    async def proyeksikan(self, *, sampai: datetime, limit: int = 500) -> int:
        self.dipanggil += 1
        self.sampai.append(sampai)
        return 3

    async def proyeksikan_futures(
        self, *, sampai: datetime, limit: int = 500
    ) -> int:
        self.futures += 1
        return 1


class TestProyektorDiLoop:
    def _loop(self, memory: Any, **ganti: Any):
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop

        return UpkeepLoop(
            refresher=None,
            resolver=None,
            memory=memory,
            settings=UpkeepSettings(enabled=False, **ganti),
        )

    @pytest.mark.asyncio
    async def test_siklus_upkeep_memproyeksikan_ingatan(self) -> None:
        proyektor = _Proyektor()

        await self._loop(proyektor).cycle(now=NOW)

        assert proyektor.dipanggil == 1

    @pytest.mark.asyncio
    async def test_terikat_waktu_siklusnya(self) -> None:
        """PASAL 15.39/15.40 berlaku juga di sini: proyeksi yang membaca
        seluruh sejarah tanpa batas atas tetap bocor lewat pintu ini, meskipun
        pencariannya nanti memakai ``as_of`` yang benar."""
        proyektor = _Proyektor()

        await self._loop(proyektor).cycle(now=NOW)

        assert proyektor.sampai == [NOW]

    @pytest.mark.asyncio
    async def test_ingatan_futures_ikut_diproyeksikan(self) -> None:
        """Ingatan pada 4h berjumlah nol (terukur), jadi konteks futures
        meminjam 1h. Tanpa proyektor ini pinjaman itu **tidak pernah berakhir**
        - dan tidak ada yang akan menyadarinya, karena pesannya tetap keluar."""
        proyektor = _Proyektor()

        await self._loop(proyektor).cycle(now=NOW)

        assert proyektor.futures == 1

    @pytest.mark.asyncio
    async def test_kegagalan_futures_tidak_menghentikan_yang_spot(self) -> None:
        """Dua sumber, dua kegagalan yang berdiri sendiri. Yang satu mati tidak
        boleh menghentikan yang lain - ingatan spot punya 8.366 rekaman dan
        futures baru 182."""
        class _SetengahMeledak:
            def __init__(self) -> None:
                self.spot = 0

            async def proyeksikan(self, *, sampai: datetime, limit: int = 500) -> int:
                self.spot += 1
                return 5

            async def proyeksikan_futures(
                self, *, sampai: datetime, limit: int = 500
            ) -> int:
                raise RuntimeError("futures gagal")

        proyektor = _SetengahMeledak()
        stats = await self._loop(proyektor).cycle(now=NOW)

        assert proyektor.spot == 1
        assert stats.memories == 5
        assert stats.memory_failures == 1

    @pytest.mark.asyncio
    async def test_kegagalannya_tidak_menghentikan_siklus(self) -> None:
        """Ingatan adalah bukti tambahan, bukan syarat hidup. Siklus yang mati
        karenanya berarti candle yang tidak disegarkan dan sinyal yang tidak
        dinilai - kerusakan jauh lebih besar daripada yang dijaganya."""
        class _Meledak:
            async def proyeksikan(self, *, sampai: datetime, limit: int = 500) -> int:
                raise RuntimeError("proyeksi gagal")

        loop = self._loop(_Meledak())
        stats = await loop.cycle(now=NOW)

        assert stats.cycles == 1
        assert stats.memory_failures == 1

    @pytest.mark.asyncio
    async def test_tidak_diulang_sebelum_cadence(self) -> None:
        """Signal baru datang beberapa per jam; memproyeksikan tiap tiga puluh
        detik adalah kueri yang jawabannya kosong berulang-ulang."""
        proyektor = _Proyektor()
        loop = self._loop(proyektor, memory_interval_sec=600.0)

        await loop.cycle(now=NOW)
        await loop.cycle(now=NOW + timedelta(minutes=2))

        assert proyektor.dipanggil == 1

    @pytest.mark.asyncio
    async def test_dijalankan_lagi_sesudah_cadence(self) -> None:
        """Penjaga terhadap test di atas: gerbang yang selalu tertutup lolos
        juga."""
        proyektor = _Proyektor()
        loop = self._loop(proyektor, memory_interval_sec=600.0)

        await loop.cycle(now=NOW)
        await loop.cycle(now=NOW + timedelta(minutes=11))

        assert proyektor.dipanggil == 2

    def test_aplikasi_mengoper_proyektornya_ke_loop(self) -> None:
        """Diperiksa lewat AST: baris yang dikomentari tetap terbaca oleh
        ``in sumber``, dan itu sudah meloloskan satu penjaga di Phase 14."""
        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        panggilan = [
            n for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "UpkeepLoop"
        ]

        assert panggilan, "UpkeepLoop tidak dibangun di app.py"
        assert any(
            k.arg == "memory" for c in panggilan for k in c.keywords
        ), "loop upkeep dibangun tanpa proyektor ingatan"

    def test_aplikasi_membangun_proyektornya(self) -> None:
        """Argumen yang dioper tapi selalu ``None`` adalah rangkaian yang putus
        di tempat yang tidak terlihat dari daftar argumennya."""
        from aruna.app import ArunaApplication
        from aruna.db.repositories.memory import MemoryRepository

        app = object.__new__(ArunaApplication)
        app.db = object()

        assert isinstance(app._build_memory(), MemoryRepository)


def _konteks(pengaruh_str: str = "SUPPORTIVE", total: int = 147, **ganti):
    """``KonteksHistoris`` yang sungguhan, bukan SimpleNamespace.

    Palsu yang bidangnya beda dari objek asli sudah dua kali membuat suite
    hijau di atas bug produksi di proyek ini.
    """
    from decimal import Decimal

    from aruna.memory.context import susun
    from aruna.memory.dimensions import UNKNOWN, Dimensi
    from aruna.memory.fingerprint import Sidik
    from aruna.memory.outcome import ringkas
    from aruna.memory.record import Hasil, Ingatan, Mutu
    from aruna.memory.similarity import Kemiripan

    def _satu(i: int, arah: str, hasil: Hasil):
        dikunci = NOW - timedelta(hours=i + 1)
        return (
            Ingatan(
                signal_id=f"mem{i:013d}",
                sidik=Sidik(nilai={**{d: UNKNOWN for d in Dimensi},
                                   Dimensi.ASSET: "BTCUSDT"}),
                arah=arah, hasil=hasil, move_pct=Decimal("1.0000"),
                locked_at=dikunci, resolved_at=dikunci + timedelta(minutes=30),
                model_version="futures-f5", cakupan=95, mutu=Mutu.HIGH,
            ),
            Kemiripan(skor=90 - (i % 8), cakupan=95, cocok=(Dimensi.ASSET,),
                      beda=(), tak_terbaca=(Dimensi.VOLATILITY,)),
        )

    # Dasar 40%; yang serupa 70% (SUPPORTIVE) atau 10% (CONTRARY).
    dasar = ringkas([_satu(i, "BUY", Hasil.WIN if i < 400 else Hasil.LOSS)
                     for i in range(1000)])
    menang = {"SUPPORTIVE": 0.7, "CONTRARY": 0.1, "NEUTRAL": 0.4}[pengaruh_str]
    cocok = [_satu(i, "BUY", Hasil.WIN if i < total * menang else Hasil.LOSS)
             for i in range(total)]

    return susun(arah_sekarang="LONG", cocok=cocok, dasar=dasar, as_of=NOW,
                 **ganti)


class TestSampaiKeCatatanCouncil:
    def test_catatan_council_bisa_membawanya(self) -> None:
        from tests.test_futures_notify_pasal1426 import note

        n = note(memory=_konteks())

        assert n.memory.ringkasan.total == 147

    def test_kelengkapan_tidak_terganggu(self) -> None:
        """Bidang baru di ``CouncilNote`` tidak boleh mengubah pengukuran
        kelengkapan Phase 14 - PHASE 13 baru saja mencapai 100%."""
        from aruna.futures.service import _kelengkapan_fase

        class _Note:
            pass

        note = _Note()
        note.memory = _konteks()

        laporan = _kelengkapan_fase(context=None, verdict=None, plan=None,
                                    note=note)

        assert "integrasi_pct" in laporan


class TestSampaiKeOperator:
    def test_pesannya_membawa_blok_historis(self) -> None:
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW, note=note(memory=_konteks()))

        assert "HISTORICAL CONTEXT" in teks
        assert "147" in teks

    def test_rentang_waktunya_wajib_disebut(self) -> None:
        """Terukur: korpusnya baru beberapa hari. "147 kasus serupa" tanpa
        tanggalnya terbaca seperti pengalaman bertahun-tahun (PASAL 15.9)."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW, note=note(memory=_konteks()))

        assert "Agu" in teks or "Aug" in teks

    def test_sampel_tipis_mencetak_kalimat_pasalnya(self) -> None:
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert
        from aruna.memory.outcome import KALIMAT_TIDAK_CUKUP

        teks = _alert(FakePlan(), PESAN_NOW,
                      note=note(memory=_konteks(total=3)))

        assert KALIMAT_TIDAK_CUKUP in teks

    def test_tanpa_konteks_tidak_ada_barisnya(self) -> None:
        """§13.26: yang tidak terbaca tidak dicetak sebagai nol kasus."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW, note=note())

        assert "HISTORICAL CONTEXT" not in teks

    def test_tidak_pernah_mengucapkan_probabilitas(self) -> None:
        """PASAL 15.23 dan 15.48, dan §51. Similarity BUKAN peluang profit, dan
        satu kalimat yang salah di sini mengubah bukti menjadi janji."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        for p in ("SUPPORTIVE", "CONTRARY", "NEUTRAL"):
            teks = _alert(
                FakePlan(), PESAN_NOW, note=note(memory=_konteks(p))
            ).lower()
            for terlarang in ("chance", "probability", "peluang profit",
                              "pasti naik", "pasti turun", "pasti profit",
                              "100% win", "dijamin"):
                assert terlarang not in teks

    def test_blok_yang_berlawanan_tetap_dicetak(self) -> None:
        """PASAL 15.20 dan 15.38: memory yang melawan tidak boleh dibuang
        diam-diam. Yang disembunyikan dari operator adalah confirmation bias
        yang dilakukan sistem atas namanya."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW,
                      note=note(memory=_konteks("CONTRARY")))

        assert "CONTRARY" in teks


class TestArahnyaDibacaDariTempatYangBenar:
    """Cacat yang nyaris lolos: ``_arah_note`` membaca ``note.split.decision``,
    dan ``VoteSplit`` **tidak punya bidang itu** - hanya ``setuju``, ``kontra``,
    ``abstain``.

    Akibatnya arah selalu string kosong, dan pengaruh selamanya NEUTRAL: fitur
    yang tersambung, berjalan, tidak pernah error, dan tidak pernah mengatakan
    apa pun. Test lain tidak menangkapnya karena mereka mengoper arah langsung
    ke ``susun``, melewati pembacanya.

    Kelas yang sama sudah dua kali tercatat di proyek ini - ``split`` yang ada
    di ``CouncilNote`` bukan di vonisnya, dan ``note.strategy`` yang ternyata
    ada di ``context``.
    """

    def _bahan(self, menang: int, total: int = 200):
        from decimal import Decimal

        from aruna.futures.service import _BahanIngatan
        from aruna.memory.dimensions import UNKNOWN, Dimensi
        from aruna.memory.fingerprint import Sidik
        from aruna.memory.outcome import ringkas
        from aruna.memory.record import Hasil, Ingatan, Mutu
        from aruna.memory.similarity import bandingkan

        def _satu(i: int, arah: str, hasil: Hasil):
            dikunci = NOW - timedelta(hours=i + 1)
            nilai = {d: UNKNOWN for d in Dimensi}
            nilai[Dimensi.ASSET] = "BTC/USDT"
            nilai[Dimensi.MARKET] = "CRYPTO"
            nilai[Dimensi.TIMEFRAME] = "1h"
            nilai[Dimensi.REGIME] = "TRENDING"
            return Ingatan(
                signal_id=f"mem{i:013d}", sidik=Sidik(nilai=nilai), arah=arah,
                hasil=hasil, move_pct=Decimal("1.0000"), locked_at=dikunci,
                resolved_at=dikunci + timedelta(minutes=30),
                model_version="1.0.0", cakupan=95, mutu=Mutu.HIGH,
            )

        daftar = tuple(
            _satu(i, "BUY", Hasil.WIN if i < menang else Hasil.LOSS)
            for i in range(total)
        )
        # Dasar 30%: yang serupa jauh di atasnya kalau menang besar.
        dasar = ringkas([
            (_satu(i, "BUY", Hasil.WIN if i < 300 else Hasil.LOSS),
             bandingkan(daftar[0].sidik, daftar[0].sidik))
            for i in range(1000)
        ])
        return _BahanIngatan(
            daftar=daftar, dasar=dasar, timeframe="1h", dipinjam=True,
            catatan=("ingatan 1h (belum ada di 4h)",), as_of=NOW,
        )

    def _note(self):
        class _N:
            regime = "TRENDING"
            quality = 70

            def __init__(self) -> None:
                self.risk_readings: dict[str, Any] = {}

        return _N()

    def test_arah_yang_dioper_benar_benar_dipakai(self) -> None:
        """Kalau arahnya tidak sampai, hasilnya NEUTRAL - dan NEUTRAL adalah
        jawaban yang sah, jadi kegagalannya tidak terlihat dari mana pun."""
        from aruna.futures.service import _konteks_historis
        from aruna.memory.context import Pengaruh

        k = _konteks_historis(
            self._bahan(menang=180), self._note(), symbol="BTCUSDT", arah="BUY"
        )

        assert k is not None
        assert k.pengaruh is Pengaruh.SUPPORTIVE

    def test_arah_kosong_menghasilkan_netral(self) -> None:
        """Penjaga terhadap test di atas: memastikan SUPPORTIVE di sana
        benar-benar datang dari arahnya, bukan dari hal lain."""
        from aruna.futures.service import _konteks_historis
        from aruna.memory.context import Pengaruh

        k = _konteks_historis(
            self._bahan(menang=180), self._note(), symbol="BTCUSDT", arah=""
        )

        assert k.pengaruh is Pengaruh.NEUTRAL

    def test_simbol_perpetual_menemukan_ingatan_spot(self) -> None:
        """Jembatan ejaan, diuji lewat jalur yang sungguhan: ingatan mengeja
        ``BTC/USDT``, futures mengoper ``BTCUSDT``, dan nol kecocokan berarti
        fitur yang diam."""
        from aruna.futures.service import _konteks_historis

        k = _konteks_historis(
            self._bahan(menang=180), self._note(), symbol="BTCUSDT", arah="BUY"
        )

        assert k.ringkasan.total > 0

    def test_service_membaca_arah_dari_vonis_bukan_dari_split(self) -> None:
        """``VoteSplit`` tidak punya ``decision``. Pemanggil yang membacanya
        dari sana mengoper string kosong selamanya."""
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "_plan_one":
                sumber = ast.unparse(n)
                assert "split.decision" not in sumber
                assert "arah=" in sumber
                return
        raise AssertionError("_plan_one tidak ada")


class TestLoopFuturesMenerimanya:
    """Cacat terbesar Phase 15, dan hanya pengukuran produksi yang menemukannya.

    Terukur 2026-08-20T21:49Z, sesudah restart dengan seluruh Phase 15 hijau:
    ``memory_pengaruh=UNKNOWN`` pada **keempat puluh** amatan,
    ``memory_kasus=0``. Ingatan tidak menghasilkan apa pun di jalur hidup.

    Sebabnya: ``memory=`` disambungkan ke ``UpkeepLoop``, yang hidup di proses
    ``aruna run``. Keputusan futures dibuat di **proses lain** -
    ``futures-loop`` - dan ``FuturesPlanService`` tidak pernah diberi
    repositori ingatan sama sekali.

    Seluruh test lain hijau karena semuanya mengoper ``_memory`` sendiri.
    Persis keluarga cacat yang Phase 14 habiskan empat putaran untuk menutup.
    """

    def test_loop_futures_menerima_ingatan(self) -> None:
        from aruna import cli

        pohon = ast.parse(inspect.getsource(cli))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_futures_loop"):
                for c in ast.walk(n):
                    if (isinstance(c, ast.Call)
                            and getattr(c.func, "id", None) == "FuturesPlanService"):
                        assert any(k.arg == "memory" for k in c.keywords), (
                            "FuturesPlanService dibangun tanpa ingatan - "
                            "seluruh Phase 15 akan diam di jalur hidup"
                        )
                        return
                raise AssertionError("FuturesPlanService tidak dibangun di _futures_loop")
        raise AssertionError("_futures_loop tidak ada di cli.py")

    def test_service_menyimpan_yang_dioper(self) -> None:
        """Argumen yang diterima lalu dibuang adalah rangkaian yang putus di
        tempat yang tidak terlihat dari daftar argumennya."""
        from aruna.futures.service import FuturesPlanService

        penanda = object()
        service = FuturesPlanService(
            deliberation=None, council=None, store=None, universe=None,
            memory=penanda,
        )

        assert service._memory is penanda


class TestTigaPasalTerakhirTersambung:
    """15.15, 15.16, 15.18 - dibangun terakhir, dan paling mudah jadi kode yang
    tidak punya pembaca."""

    def test_konteks_membawa_ketiganya(self) -> None:
        from aruna.memory.context import susun
        from aruna.memory.lintas import LintasAset
        from aruna.memory.peristiwa import Peristiwa
        from aruna.memory.pola import Pola

        k = susun(
            arah_sekarang="LONG", cocok=[], dasar=_konteks().ringkasan,
            as_of=NOW,
            lintas=LintasAset(sejalan=8, total=10, rezim="TRENDING"),
            pola=Pola(kunci="horizon=1h", dimensi={"horizon": "1h"},
                      sampel=1069, win_rate=0.38, ci=(0.35, 0.41),
                      beats_baseline=True),
            peristiwa=Peristiwa(keadaan="NEGATIVE", menang=17, kalah=56),
        )

        assert k.lintas is not None
        assert k.pola is not None
        assert k.peristiwa is not None

    def test_pesannya_mencetak_ketiganya(self) -> None:
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert
        from aruna.memory.context import susun
        from aruna.memory.lintas import LintasAset
        from aruna.memory.peristiwa import Peristiwa
        from aruna.memory.pola import Pola

        dasar = _konteks()
        k = susun(
            arah_sekarang="LONG", cocok=[], dasar=dasar.ringkasan, as_of=NOW,
            lintas=LintasAset(sejalan=8, total=10, rezim="TRENDING"),
            pola=Pola(kunci="direction=BUY|horizon=1h",
                      dimensi={"horizon": "1h", "direction": "BUY"},
                      sampel=993, win_rate=0.40, ci=(0.37, 0.43),
                      beats_baseline=True),
            peristiwa=Peristiwa(keadaan="NEGATIVE", menang=17, kalah=56),
        )
        teks = _alert(FakePlan(), PESAN_NOW, note=note(memory=k))

        assert "aset kripto" in teks
        assert "Phase 12" in teks
        assert "NEGATIVE" in teks

    def test_yang_tidak_ada_tidak_dicetak(self) -> None:
        """§13.26: ketiganya boleh tidak ada, dan yang tidak ada tidak dicetak
        sebagai nol."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW, note=note(memory=_konteks()))

        assert "aset kripto" not in teks
        assert "Phase 12" not in teks

    def test_service_membaca_ketiganya(self) -> None:
        """Diperiksa lewat AST di dalam fungsinya."""
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_konteks_historis"):
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "baca_lintas" in nama
                assert "cocokkan" in nama
                assert "baca_peristiwa" in nama
                return
        raise AssertionError("_konteks_historis tidak ada")


class TestKondisiSekarangIkutDiperkaya:
    """Perkayaan yang hanya menyentuh ingatan tidak pernah sampai ke keputusan.

    Ingatan punya tiga belas dimensi sesudah 0032; kondisi sekarang masih
    delapan. Kelima dimensi teknikal karena itu selalu "tidak terbaca di satu
    sisi" - dan `bandingkan` mengeluarkannya dari penyebut, tepat seperti
    rancangannya. Hasilnya: kerja seharian yang tidak mengubah satu pun
    keputusan hidup, tanpa satu error pun.
    """

    def test_bahan_membawa_dimensi_teknikal_sekarang(self) -> None:
        from aruna.futures.service import _BahanIngatan

        assert "teknikal" in _BahanIngatan.__slots__

    def test_service_menghitungnya_dari_kandil(self) -> None:
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_bahan_ingatan"):
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "_teknikal_sekarang" in nama
                break
        else:
            raise AssertionError("_bahan_ingatan tidak ada")

        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_teknikal_sekarang"):
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "kandil_sampai" in nama
                assert "dimensi_teknikal" in nama
                return
        raise AssertionError("_teknikal_sekarang tidak ada")

    def test_sidik_sekarang_memakainya(self) -> None:
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_konteks_historis"):
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "dengan" in nama, (
                    "sidik jari kondisi sekarang tidak diperkaya - kelima "
                    "dimensi teknikal tidak akan pernah ikut membandingkan"
                )
                return
        raise AssertionError("_konteks_historis tidak ada")


class TestServiceMenyambungkannya:
    def test_service_memanggil_attach_memory(self) -> None:
        """Diperiksa lewat AST di dalam fungsinya, bukan di seluruh berkas."""
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "_plan_one":
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "attach_memory" in nama
                return
        raise AssertionError("_plan_one tidak ada di service.py")
