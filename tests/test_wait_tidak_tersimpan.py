"""`WAIT` tidak boleh tersimpan sebagai keputusan (bagian 27).

Finalizer yang benar dan tidak dipanggil adalah kegagalan yang paling sering
terjadi di repo ini. Test di sini menjalankan `Council.convene` yang sungguhan
dan memeriksa keputusan yang keluar, bukan keberadaan fungsinya.
"""

from __future__ import annotations

import ast
import inspect
from textwrap import dedent

from aruna.core.enums import Decision
from aruna.decision.finalizer import FINAL


class TestPerilakuSungguhan:
    """Menjalankan council yang sungguhan.

    Penjaga AST di bawah TIDAK cukup, dan itu terbukti: mengomentari satu baris
    penugasan membuat finalizer tetap dipanggil dan hasilnya dibuang - seluruh
    penjaga AST tetap hijau di atas keputusan yang masih `WAIT`. Test yang
    tidak menggigit tidak menguji apa pun.
    """

    def _verdict(self, closes):
        from tests.test_council import _context

        from aruna.council.session import Council

        return Council().convene(_context(closes))

    def test_pasar_datar_tidak_menghasilkan_wait(self) -> None:
        """`FLAT` adalah keadaan yang dulu menghasilkan `WAIT`: tidak ada sisi
        berarah yang bertahan sesudah ditimbang."""
        from tests.test_council import FLAT

        verdict = self._verdict(FLAT)

        assert verdict.decision is not Decision.WAIT
        assert verdict.decision in FINAL

    def test_apa_pun_pasarnya_keputusannya_salah_satu_dari_tiga(self) -> None:
        from tests.test_council import FLAT, RISING

        for closes in (FLAT, RISING):
            assert self._verdict(closes).decision in FINAL

    def test_sebabnya_dicatat_saat_diam(self) -> None:
        """Meruntuhkan dua keputusan menjadi satu menghapus keterangan kecuali
        keterangannya pindah - dan `notes` adalah tempatnya."""
        from tests.test_council import FLAT

        verdict = self._verdict(FLAT)
        if verdict.decision is Decision.NO_SIGNAL:
            assert any("keputusan final" in n for n in verdict.notes), verdict.notes

    def test_judge_internal_boleh_tetap_wait(self) -> None:
        """Bagian 25: uncertainty internal sah. Yang difinalkan hanya keputusan
        verdict, bukan kesimpulan judge di dalamnya."""
        from tests.test_council import FLAT

        verdict = self._verdict(FLAT)

        # Judge boleh WAIT; verdict tidak boleh.
        assert verdict.judgement is not None
        assert verdict.decision is not Decision.WAIT


class TestTerpasangDiCouncil:
    def test_convene_memanggil_finalizer(self) -> None:
        from aruna.council.session import Council

        pohon = ast.parse(dedent(inspect.getsource(Council.convene)))
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "finalkan" in dipanggil

    def test_dipanggil_sesudah_kedua_gerbang(self) -> None:
        """Finalizer harus melihat hasil veto DAN no-trade. Dipanggil lebih
        dulu, ia memfinalkan keputusan yang belum diblokir - dan blokirnya
        kemudian menulis `WAIT` kembali ke atasnya."""
        from aruna.council.session import Council

        pohon = ast.parse(dedent(inspect.getsource(Council.convene)))
        baris = {}
        for n in ast.walk(pohon):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                baris.setdefault(n.func.id, n.lineno)

        assert baris["evaluate_no_trade"] < baris["finalkan"]

    def test_veto_dan_no_trade_dioper(self) -> None:
        """Tanpa keduanya, seluruh NO SIGNAL akan tercatat sebagai
        TIDAK_ADA_SETUP - termasuk yang sebenarnya diblokir veto."""
        from aruna.council.session import Council

        pohon = ast.parse(dedent(inspect.getsource(Council.convene)))
        kata = {
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "finalkan"
            for k in n.keywords
        }

        assert kata == {"diblokir_veto", "diblokir_no_trade"}


class TestKosakataPenyimpanan:
    def test_peta_publik_tetap_lengkap(self) -> None:
        """`PUBLIC_DECISION` sengaja memetakan SETIAP anggota enum, termasuk
        `WAIT` yang tidak lagi difinalkan - ia masih suara agent, dan
        `agreed_with_council` membandingkan lewat peta ini."""
        from aruna.notify.verdict import PUBLIC_DECISION

        for d in Decision:
            assert d in PUBLIC_DECISION

    def test_ketiga_final_memetakan_ke_tiga_kata(self) -> None:
        from aruna.notify.verdict import LONG, NO_SIGNAL, PUBLIC_DECISION, SHORT

        assert {PUBLIC_DECISION[d] for d in FINAL} == {LONG, SHORT, NO_SIGNAL}


class TestSuaraAgentTidakDisentuh:
    def test_agent_masih_boleh_wait(self) -> None:
        """Bagian 25 mengizinkan uncertainty internal. Kalau `WAIT` dibuang
        dari kosakata agent, tiap agent dipaksa berpihak tiap tick."""
        from aruna.core.enums import UNRESTRICTED_AGENT_DECISIONS

        assert Decision.WAIT in UNRESTRICTED_AGENT_DECISIONS

    def test_judge_masih_boleh_menyimpulkan_wait(self) -> None:
        """`judge_decisions` adalah catatan analisis internal, bukan keputusan
        final. Memfinalkannya di sana akan menghapus bedanya antara 'judge
        tidak menemukan sisi' dan 'veto memblokir'."""
        from aruna.council import judge

        sumber = inspect.getsource(judge)

        assert "Decision.WAIT" in sumber
