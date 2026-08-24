"""Phase 16 dan 17 benar-benar sampai ke skor mutu (bagian 18.14, 18.15).

**Celah 1, dan satu-satunya yang mengubah arsitektur.** Sampai 2026-08-24,
keduanya berjalan sebagai PENGAMAT: menulis `scenario_evidence` dan
`router_pilihan` yang tak seorang pun di jalur keputusan baca. Diverifikasi
lewat impor - tidak ada satu berkas pun di `signals/`, `council/`, atau
`agents/` yang menyentuh `aruna.router` maupun `aruna.scenario`.

Penjaganya berbasis AST dan bukan pencarian teks, dengan alasan yang terbukti
di berkas ini sendiri: komentar di `app.py` **menyebut** `router=` dan
`scenario=` untuk menerangkan kenapa barisnya ada, jadi pencarian teks akan
lulus atas komentar yang menjelaskan baris yang sudah dihapus.
"""

from __future__ import annotations

import ast
import inspect
import textwrap


def _pohon(modul: object) -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(modul)))


def _kwarg_ke(pohon: ast.Module, fungsi: str, nama: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == fungsi
        and any(kw.arg == nama for kw in n.keywords)
        for n in ast.walk(pohon)
    )


class TestSambunganKeJalurKeputusan:
    def test_app_mengoper_router_dan_scenario(self) -> None:
        from aruna import app

        pohon = _pohon(app)

        assert _kwarg_ke(pohon, "DeliberationService", "router")
        assert _kwarg_ke(pohon, "DeliberationService", "scenario")

    def test_konteks_membawa_keduanya(self) -> None:
        """`DecisionContext` menyebut dirinya "kolam bukti yang beku dan
        lengkap untuk satu keputusan". Bukti yang dioper lewat jalur samping
        membuat klaim itu tidak benar - dan membuat replay Phase 9 menilai
        keputusan dengan bahan yang tidak tercatat di konteksnya."""
        import dataclasses

        from aruna.agents.context import DecisionContext

        bidang = {f.name for f in dataclasses.fields(DecisionContext)}

        assert {"router", "scenario"} <= bidang

    def test_deliberation_benar_benar_membacanya(self) -> None:
        """Parameter yang diterima lalu disimpan tanpa pernah dipakai adalah
        bentuk cacat yang sama, satu lapis lebih dalam."""
        from aruna.agents.service import DeliberationService

        pohon = _pohon(DeliberationService)
        dipakai = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "_baca" in dipakai

    def test_konteks_diisi_bukan_dibiarkan_kosong(self) -> None:
        from aruna.agents.service import DeliberationService

        pohon = _pohon(DeliberationService)
        dioper = {
            kw.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "DecisionContext"
            for kw in n.keywords
        }

        assert {"router", "scenario"} <= dioper

    def test_skor_mutu_menyusun_keduanya(self) -> None:
        """Faktor yang benar tapi tidak pernah disusun ke dalam skor adalah
        cacat yang sudah enam kali muncul di proyek ini."""
        from aruna.signals.quality import score_signal

        pohon = _pohon(score_signal)
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert {"strategy_factor", "scenario_factor"} <= dipanggil


class TestPembacanyaMenolakLookAhead:
    """Bagian 18.40, dan ini yang paling mudah salah."""

    def test_router_hanya_membaca_yang_sebelum_as_of(self) -> None:
        """Pilihan router yang dicatat SESUDAH keputusannya dibuat tidak
        menjelaskan keputusan itu. Memakainya membuat replay menilai keputusan
        dengan bahan yang belum ada saat ia dibuat."""
        from aruna.db.repositories.router import RouterRepository

        sumber = inspect.getsource(RouterRepository.untuk_keputusan)

        assert "dipilih_pada <= %s" in sumber

    def test_skenario_hanya_membaca_yang_sebelum_as_of(self) -> None:
        from aruna.db.repositories.scenario import ScenarioRepository

        sumber = inspect.getsource(ScenarioRepository.untuk_keputusan)

        assert "dibuat_pada <= %s" in sumber

    def test_skenario_tidak_mencampur_dua_simulasi(self) -> None:
        """Bobot skenario bersifat RELATIF terhadap simulasi yang sama - lihat
        `CATATAN_BOBOT`. Mencampur dua simulasi menghasilkan bobot yang tidak
        berarti apa-apa, dan `scenario_factor` menimbang dengan bobot itu."""
        from aruna.db.repositories.scenario import ScenarioRepository

        sumber = inspect.getsource(ScenarioRepository.untuk_keputusan)

        assert "MAX(dibuat_pada)" in sumber
        assert "dibuat_pada = %s" in sumber
