"""PASAL 15.43-15.45: apa yang ingatan sumbangkan, dan apa yang belum bisa dinilai.

PASAL 15.44 meminta perbandingan **keputusan dengan memory** melawan
**keputusan tanpa memory**. Perbandingan itu butuh keputusan yang cukup banyak
untuk dibandingkan - dan terukur 2026-08-21, jalur futures baru punya **17**
hasil yang benar-benar menang atau kalah dari 182 rencana yang diresolusi.

Menghitung "memory contribution low" dari tujuh belas kasus akan menghasilkan
angka percaya diri tanpa dasar - persis yang PASAL 15.44 coba cegah. Jadi yang
dibangun di sini adalah **bahannya**, dan penolakan yang eksplisit untuk
menghitung skornya sebelum sampelnya ada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aruna.memory.harian import (
    SAMPEL_PERBANDINGAN,
    IngatanHarian,
    bisa_dibandingkan,
)

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


class TestLaporanHarian:
    def _harian(self, **ganti: Any) -> IngatanHarian:
        dasar: dict[str, Any] = {
            "baru": 182,
            "total": 8548,
            "per_timeframe": {"15m": 5377, "1h": 2189, "1d": 800, "4h": 182},
            "per_mutu": {"HIGH": 8366, "MEDIUM": 182},
            "bisa_mengajari": 8383,
            "rentang": (datetime(2026, 8, 17, tzinfo=UTC), NOW),
        }
        dasar.update(ganti)
        return IngatanHarian(**dasar)

    def test_menyebut_ingatan_baru(self) -> None:
        baris = "\n".join(self._harian().report())

        assert "MARKET MEMORY" in baris
        assert "182" in baris

    def test_menyebut_yang_bisa_mengajari_terpisah(self) -> None:
        """Delapan ribu ingatan yang 165 di antaranya kedaluwarsa bukan delapan
        ribu pelajaran. Angka yang tidak memisahkan keduanya membuat korpus
        terdengar lebih tebal daripada yang bisa dipakai."""
        baris = "\n".join(self._harian().report())

        assert "8383" in baris or "8.383" in baris

    def test_menyebut_rentang_waktunya(self) -> None:
        """Sama seperti blok pesan futures: korpus beberapa hari yang disebut
        "historis" tanpa tanggalnya terbaca seperti bertahun-tahun."""
        baris = "\n".join(self._harian().report())

        assert "17" in baris

    def test_tidak_menampilkan_seluruh_ingatan(self) -> None:
        """PASAL 15.43: tidak perlu menampilkan seluruh memory. Blok yang
        panjang berhenti dibaca, dan yang berhenti dibaca sama saja dengan
        tidak ada."""
        baris = self._harian().report()

        assert len(baris) <= 20

    def test_evaluasinya_yang_berbicara_kalau_sudah_ada(self) -> None:
        """PASAL 15.44 meminta ARUNA **mendeteksi** kontribusi rendah, bukan
        diam. Terukur 2026-08-21: selisih +3 poin - memory belum menambah apa
        pun, dan itu yang harus terbaca operator."""
        from aruna.memory.evaluasi import Evaluasi

        harian = self._harian(evaluasi=Evaluasi(
            mendukung_menang=95, mendukung_kalah=125,
            melawan_menang=83, melawan_kalah=122,
        ))
        baris = "\n".join(harian.report())

        assert "tidak menambah" in baris
        assert "43%" in baris

    def test_evaluasi_yang_menemukan_manfaat_juga_dicetak(self) -> None:
        """Penjaga terhadap test di atas: blok yang hanya bisa mengatakan
        "tidak membantu" tidak sedang melaporkan apa pun."""
        from aruna.memory.evaluasi import Evaluasi

        harian = self._harian(evaluasi=Evaluasi(
            mendukung_menang=70, mendukung_kalah=30,
            melawan_menang=30, melawan_kalah=70,
        ))

        assert "membantu" in "\n".join(harian.report())

    def test_menyatakan_perbandingannya_belum_bisa(self) -> None:
        """PASAL 15.44. Diam soal ini akan terbaca seolah perbandingannya sudah
        dilakukan dan hasilnya biasa saja."""
        baris = "\n".join(self._harian().report())

        assert "belum" in baris.lower()

    def test_kosong_tidak_meledak(self) -> None:
        harian = IngatanHarian(
            baru=0, total=0, per_timeframe={}, per_mutu={},
            bisa_mengajari=0, rentang=None,
        )

        assert harian.report()


class TestSampaiKeLaporanHarian:
    def test_blok_ingatan_dicetak_kalau_ada(self) -> None:
        from aruna.notify.daily import DailyReport, render_daily

        laporan = DailyReport(
            date=NOW,
            markets=(),
            memory=IngatanHarian(
                baru=182, total=8548, per_timeframe={"1h": 2189},
                per_mutu={"HIGH": 8366}, bisa_mengajari=8383,
                rentang=(datetime(2026, 8, 17, tzinfo=UTC), NOW),
            ),
        )

        assert "MARKET MEMORY" in render_daily(laporan)

    def test_tanpa_ingatan_tidak_ada_bloknya(self) -> None:
        """``None`` berarti belum terhitung, dan "0 ingatan" yang lahir dari
        ketiadaan hitungan terbaca persis seperti ARUNA yang tidak pernah
        mengingat apa pun - alasan yang sama dengan blok diam PASAL 14.32."""
        from aruna.notify.daily import DailyReport, render_daily

        assert "MARKET MEMORY" not in render_daily(
            DailyReport(date=NOW, markets=())
        )


class TestAdaYangMengisinya:
    """Blok yang dirender dan tidak pernah diisi tidak pernah muncul.

    Cacat ini nyaris lolos: ``IngatanHarian`` dibangun, ``render_daily``
    mencetaknya, testnya hijau - dan ``daily_service`` tidak punya satu baris
    pun yang mengisi ``DailyReport.memory``. Blok yang tidak pernah muncul
    tidak akan pernah error, tidak akan pernah tercatat di log, dan tidak akan
    pernah ada yang menyadarinya.
    """

    @pytest.mark.asyncio
    async def test_repositori_meringkas_ingatan_hari_itu(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        class _DB:
            def __init__(self) -> None:
                self.sql: list[str] = []

            async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
                self.sql.append(sql)
                if "timeframe" in sql:
                    return [{"k": "1h", "n": 2189}, {"k": "15m", "n": 5377}]
                return [{"k": "HIGH", "n": 8366}]

            async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
                self.sql.append(sql)
                return {
                    "baru": 182, "total": 8548, "mengajari": 8383,
                    "awal": NOW, "akhir": NOW,
                }

        harian = await MemoryRepository(_DB()).ringkas_harian(
            since=NOW, until=NOW
        )

        assert harian.baru == 182
        assert harian.bisa_mengajari == 8383
        assert harian.per_timeframe["1h"] == 2189

    @pytest.mark.asyncio
    async def test_kegagalannya_menghilangkan_bloknya_bukan_menolkannya(self) -> None:
        """Alasan yang sama persis dengan blok diam PASAL 14.32: "0 ingatan"
        yang lahir dari kueri gagal terbaca seperti ARUNA yang tidak pernah
        mengingat apa pun. Bagian yang hilang lebih jujur daripada bagian yang
        berbohong."""
        from aruna.notify.daily_service import DailyReportService

        class _Meledak:
            async def ringkas_harian(self, **kw: Any) -> Any:
                raise RuntimeError("database mati")

        svc = object.__new__(DailyReportService)
        svc.memory_repo = _Meledak()

        assert await svc._memory_harian(NOW, NOW) is None

    def test_layanan_harian_mengisinya(self) -> None:
        """Diperiksa lewat AST: fungsi yang ada tapi tidak dipanggil adalah
        cacat yang sudah berkali-kali muncul di proyek ini."""
        import ast
        import inspect

        from aruna.notify import daily_service

        pohon = ast.parse(inspect.getsource(daily_service))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "DailyReport"):
                assert any(k.arg == "memory" for k in n.keywords), (
                    "DailyReport dibangun tanpa ingatan - bloknya tidak akan "
                    "pernah muncul"
                )
                return
        raise AssertionError("DailyReport tidak dibangun di daily_service.py")


class TestPenolakanPerbandingan:
    def test_di_bawah_ambang_menolak(self) -> None:
        """Tujuh belas hasil futures. Menghitung skor kontribusi darinya adalah
        angka percaya diri tanpa dasar."""
        assert not bisa_dibandingkan(17)

    def test_di_atas_ambang_boleh(self) -> None:
        assert bisa_dibandingkan(SAMPEL_PERBANDINGAN)

    def test_ambangnya_jauh_di_atas_ambang_sampel_biasa(self) -> None:
        """Membandingkan DUA populasi menuntut lebih banyak daripada meringkas
        satu: tiap sisi butuh sampelnya sendiri, dan selisih di antara keduanya
        butuh ruang untuk terlihat di atas derau."""
        from aruna.memory.outcome import SAMPEL_MINIMUM

        assert SAMPEL_PERBANDINGAN >= SAMPEL_MINIMUM * 5


class TestJejakDiPengamatan:
    def test_amatan_membawa_kontribusi_ingatan(self) -> None:
        """PASAL 15.41 dan 15.45: tiap signal harus bisa menjawab memory mana
        yang dipakai dan seberapa besar sumbangannya. ``decision.observed``
        sudah satu baris per simbol per tick - itu tempatnya, bukan baris log
        kedua yang harus dijaga tetap sepakat."""
        from tests.test_memory_tersambung import _konteks

        from aruna.futures.service import _jejak_memory

        jejak = _jejak_memory(_konteks("SUPPORTIVE"))

        assert jejak["memory_pengaruh"] == "SUPPORTIVE"
        assert jejak["memory_kontribusi"] > 0
        assert jejak["memory_kasus"] == 147

    def test_tanpa_ingatan_tetap_melaporkan_ketiadaannya(self) -> None:
        """Bidang yang hilang saat ingatan tidak terbaca membuat "tidak ada
        ingatan" tidak bisa dibedakan dari "fasenya tidak jalan" - keluarga
        cacat yang sama dengan `upkeep.news` yang dulu hanya dicatat saat ada
        isinya."""
        from aruna.futures.service import _jejak_memory

        jejak = _jejak_memory(None)

        assert jejak["memory_pengaruh"] == "UNKNOWN"
        assert jejak["memory_kasus"] == 0

    def test_observe_decision_ikut_mencatatnya(self) -> None:
        """Diperiksa lewat AST: fungsi yang ada tapi tidak dipanggil adalah
        cacat yang sudah berkali-kali muncul di proyek ini."""
        import ast
        import inspect

        from aruna.futures import service

        pohon = ast.parse(inspect.getsource(service))
        for n in ast.walk(pohon):
            if isinstance(n, ast.FunctionDef) and n.name == "observe_decision":
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "_jejak_memory" in nama
                return
        raise AssertionError("observe_decision tidak ada di service.py")
