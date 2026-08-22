"""PASAL 14.41: DAILY RISK BUDGET, satu-satunya masukan yang tidak punya sumber.

Terukur 2026-08-21: ``DAILY_RISK_BUDGET`` hilang pada keempat puluh amatan, dan
sebabnya berbeda dari ``CORRELATION_RISK`` yang tetangganya. Korelasi punya
mesin, tabel, dan pembaca - yang tidak ada cuma yang menjalankannya. Jatah
risiko harian **tidak punya apa-apa**: ``risk_budget`` tidak muncul di kode,
tidak di config, tidak di database.

Batasnya karena itu ditetapkan operator - 3% equity, 2026-08-21 - dan bukan
disimpulkan dari apa pun. §13.26 melarang mengarang risk score, position size,
dan angka risiko lain; sebuah plafon harian yang dipilih penulis kode adalah
persis itu, cuma terdengar lebih resmi.

Yang **terpakai** bukan pilihan siapa pun: ia dijumlahkan dari rencana yang
benar-benar terbit hari ini, dari tabel yang sudah menyimpannya.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)
EQUITY = Decimal("10000")


class TestHitungannya:
    """Aritmetika murni: tanpa I/O, tanpa database, tanpa tebakan."""

    def test_batas_lahir_dari_equity_dan_persen(self) -> None:
        from aruna.futures.risk import jatah_harian

        j = jatah_harian(equity=EQUITY, terpakai=Decimal(0))

        assert j.batas == Decimal("300.00")  # 3% dari 10.000

    def test_sisa_berkurang_oleh_yang_sudah_dipertaruhkan(self) -> None:
        from aruna.futures.risk import jatah_harian

        j = jatah_harian(equity=EQUITY, terpakai=Decimal("120"))

        assert j.sisa == Decimal("180.00")
        assert not j.terlampaui

    def test_yang_melampaui_disebut_bukan_dipotong_diam_diam(self) -> None:
        """ARUNA tidak menahan rencana karena jatahnya habis - ia melapor.
        Sisa yang dijepit ke nol tanpa penanda akan membuat hari yang kelewatan
        terbaca persis seperti hari yang pas habis."""
        from aruna.futures.risk import jatah_harian

        j = jatah_harian(equity=EQUITY, terpakai=Decimal("400"))

        assert j.sisa == Decimal("0.00")
        assert j.terlampaui

    def test_persen_yang_dipakai_bisa_dibaca(self) -> None:
        from aruna.futures.risk import jatah_harian

        j = jatah_harian(equity=EQUITY, terpakai=Decimal("150"))

        assert j.pct_terpakai == Decimal("50.0")

    def test_angkanya_dipendekkan_ke_sen(self) -> None:
        """Terukur di produksi 2026-08-21: ``49.998650000000000000000000`` -
        dua puluh empat angka di belakang koma untuk USDT yang dinilai terhadap
        batas tiga ratus. Bukan salah hitung, tapi tetap cacat, dan kelas yang
        sama dengan ``move_pct`` 28 digit dan jejak PASAL 14.30 yang pernah
        6.000 karakter: angka yang benar dalam bentuk yang tidak bisa dibaca."""
        from aruna.futures.risk import jatah_harian

        j = jatah_harian(
            equity=EQUITY, terpakai=Decimal("49.998650000000000000000000")
        )

        assert j.terpakai == Decimal("50.00")
        assert "49.99865" not in j.ringkas()

    def test_equity_bukan_angka_positif_ditolak(self) -> None:
        """§13.26: jatah yang dihitung dari equity nol adalah angka yang
        dikarang dari ketiadaan, dan ia akan terbaca sebagai 'jatah habis'."""
        from aruna.futures.risk import jatah_harian

        with pytest.raises(ValueError):
            jatah_harian(equity=Decimal(0), terpakai=Decimal(0))

    def test_persennya_tidak_melebihi_pagar_per_ide_kali_sepuluh(self) -> None:
        """Pagar yang sama semangatnya dengan ``MAX_RISK_PCT``: sebuah plafon
        harian yang longgar bukan plafon."""
        from aruna.futures.risk import DAILY_RISK_PCT, MAX_RISK_PCT

        assert Decimal(0) < DAILY_RISK_PCT <= MAX_RISK_PCT * 10


class TestYangTerpakaiDibacaDariRencana:
    class _DBPalsu:
        def __init__(self, row: dict[str, Any] | None) -> None:
            self.row = row
            self.sql = ""
            self.args: tuple = ()

        async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
            self.sql = sql
            self.args = args
            return self.row

    @pytest.mark.asyncio
    async def test_menjumlahkan_risiko_rencana_yang_terbit(self) -> None:
        from aruna.db.repositories.futures import FuturesRepository

        db = self._DBPalsu({"terpakai": Decimal("150.5")})
        repo = FuturesRepository(db)

        assert await repo.risiko_terpakai_since(NOW) == Decimal("150.5")

    @pytest.mark.asyncio
    async def test_hanya_rencana_bervonis_plan_yang_dihitung(self) -> None:
        """WAIT dan REFUSED tidak mempertaruhkan apa pun. Ikut menghitungnya
        akan membuat jatah habis pada hari ARUNA paling banyak menolak."""
        from aruna.db.repositories.futures import FuturesRepository
        from aruna.futures.plan import PlanVerdict

        db = self._DBPalsu({"terpakai": Decimal("10")})
        await FuturesRepository(db).risiko_terpakai_since(NOW)

        # Keduanya, dan bukan salah satu: nilainya boleh ada di daftar argumen
        # tanpa satu pun baris tersaring olehnya - versi pertama test ini hijau
        # persis pada kueri yang menjumlahkan seluruh vonis.
        assert "verdict = %s" in db.sql
        assert PlanVerdict.PLAN.value in db.args

    @pytest.mark.asyncio
    async def test_hanya_yang_benar_benar_sampai_ke_operator_dihitung(self) -> None:
        """Terukur 2026-08-21, dan tidak akan ditemukan satu test pun:
        menjumlahkan baris ``futures_plans`` memberi 3.099 USDT untuk 2026-08-20
        terhadap jatah 300 - 1033%. Bukan jatah yang jebol; rencana yang sama
        disusun ulang tiap lima belas menit, jadi satu ide dihitung sebelas kali.
        55 baris PLAN hari itu berasal dari 5 simbol, dan **satu** yang benar-
        benar terkirim.

        Yang dipertaruhkan operator adalah yang ia lihat. Penahan duplikat
        (PASAL 14.35-14.37) sudah memastikan setup yang sama tidak dikirim dua
        kali, jadi menghitung dari jejak kirim menghitung tiap ide sekali."""
        from aruna.db.repositories.futures import FuturesRepository

        db = self._DBPalsu({"terpakai": Decimal("50")})
        await FuturesRepository(db).risiko_terpakai_since(NOW)

        assert "futures_plan_delivery" in db.sql
        assert "pushed_at >= %s" in db.sql

    @pytest.mark.asyncio
    async def test_hari_tanpa_rencana_adalah_nol_bukan_unknown(self) -> None:
        """Bedanya nyata: nol berarti ARUNA belum mempertaruhkan apa pun hari
        ini - sebuah pengukuran. UNKNOWN berarti tidak ada yang tahu."""
        from aruna.db.repositories.futures import FuturesRepository

        db = self._DBPalsu({"terpakai": None})

        assert await FuturesRepository(db).risiko_terpakai_since(NOW) == Decimal(0)


class TestSampaiKeKeputusan:
    def _jatah(self, terpakai: str = "150"):
        from aruna.futures.risk import jatah_harian

        return jatah_harian(equity=EQUITY, terpakai=Decimal(terpakai))

    def test_kelengkapan_menyebut_daily_risk_budget_hadir(self) -> None:
        from aruna.decision.integration import Masukan
        from aruna.futures.service import _kelengkapan_fase

        class _Note:
            pass

        note = _Note()
        note.risk_budget = self._jatah()

        laporan = _kelengkapan_fase(context=None, verdict=None, plan=None, note=note)

        assert Masukan.DAILY_RISK_BUDGET.value not in laporan["integrasi_hilang"]

    def test_tanpa_jatah_tetap_dilaporkan_hilang(self) -> None:
        """Penjaga terhadap test di atas: sebuah pemeriksaan yang hijau dengan
        dan tanpa jatahnya tidak memeriksa jatahnya."""
        from aruna.decision.integration import Masukan
        from aruna.futures.service import _kelengkapan_fase

        laporan = _kelengkapan_fase(
            context=None, verdict=None, plan=None, note=None
        )

        assert Masukan.DAILY_RISK_BUDGET.value in laporan["integrasi_hilang"]

    def test_catatan_council_bisa_membawanya(self) -> None:
        from tests.test_futures_notify_pasal1426 import note

        n = note(risk_budget=self._jatah())

        assert n.risk_budget.batas == Decimal("300.00")


class TestSampaiKeOperator:
    def _jatah(self, terpakai: str):
        from aruna.futures.risk import jatah_harian

        return jatah_harian(equity=EQUITY, terpakai=Decimal(terpakai))

    def test_pesannya_membawa_jatah_hari_ini(self) -> None:
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(
            FakePlan(), PESAN_NOW, note=note(risk_budget=self._jatah("150"))
        )

        assert "JATAH RISIKO HARI INI:" in teks
        assert "50" in teks

    def test_yang_terlampaui_dikatakan_bukan_dibulatkan(self) -> None:
        """§11.21 melarang menyembunyikan kerugian; sebuah hari yang melewati
        jatahnya adalah hal yang sama satu lapis lebih awal."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(
            FakePlan(), PESAN_NOW, note=note(risk_budget=self._jatah("400"))
        )

        assert "TERLAMPAUI" in teks

    def test_tanpa_jatah_tidak_ada_barisnya(self) -> None:
        """§13.26: yang tidak terbaca tidak dicetak sebagai nol - nol berarti
        ARUNA belum mempertaruhkan apa pun, dan itu kalimat yang berbeda."""
        from tests.test_futures_notify_pasal1426 import NOW as PESAN_NOW
        from tests.test_futures_notify_pasal1426 import FakePlan, note

        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), PESAN_NOW, note=note())

        assert "JATAH RISIKO HARI INI:" not in teks


class TestDiJalurHidup:
    def test_service_menghitung_jatah_dan_menitipkannya(self) -> None:
        """Diperiksa lewat AST, bukan pencarian teks: baris yang dikomentari
        tetap terbaca oleh ``in sumber`` - cacat yang baru saja tertangkap di
        penyambungan korelasi."""
        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))

        def _panggilan(nama: str) -> set[str]:
            for n in ast.walk(pohon):
                if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == nama:
                    return {
                        getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                        for c in ast.walk(n)
                        if isinstance(c, ast.Call)
                    }
            raise AssertionError(f"{nama} tidak ada di service.py")

        assert "_jatah_hari_ini" in _panggilan("plan")
        assert "attach_jatah" in _panggilan("_plan_one")
        # Dan **tidak** di dalam `_plan_one`: di situ ia berarti satu kueri per
        # simbol dengan jawaban yang sama dua puluh kali.
        assert "_jatah_hari_ini" not in _panggilan("_plan_one")
        assert "risiko_terpakai_since" in _panggilan("_jatah_hari_ini")

    @pytest.mark.asyncio
    async def test_dihitung_sekali_per_tick_bukan_per_simbol(self) -> None:
        """Dua puluh simbol berarti dua puluh kueri yang jawabannya sama."""
        from aruna.futures.service import FuturesPlanService

        class _Store:
            def __init__(self) -> None:
                self.kueri = 0

            async def risiko_terpakai_since(self, since: Any) -> Decimal:
                self.kueri += 1
                return Decimal("120")

        store = _Store()
        service = object.__new__(FuturesPlanService)
        service._store = store

        jatah = await service._jatah_hari_ini(EQUITY, NOW)

        assert store.kueri == 1
        assert jatah.terpakai == Decimal("120")

    @pytest.mark.asyncio
    async def test_kegagalan_pembacaannya_tidak_menjatuhkan_rencana(self) -> None:
        """Satu baris keterangan yang hilang jauh lebih murah daripada rencana
        yang membawa entry dan stop."""
        from aruna.futures.service import FuturesPlanService

        class _Meledak:
            async def risiko_terpakai_since(self, since: Any) -> Decimal:
                raise RuntimeError("database mati")

        service = object.__new__(FuturesPlanService)
        service._store = _Meledak()

        assert await service._jatah_hari_ini(EQUITY, NOW) is None
