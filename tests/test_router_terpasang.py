"""Apakah Phase 17 benar-benar dipanggil (bagian 17.19, 17.53).

**Cacat yang dijaga di sini sudah muncul enam kali di proyek ini**, dan tiap
kali ditemukan ulang lewat audit manual, bukan lewat test:
`AdaptiveLearningService` yang cuma jalan lewat perintah manual, pembersih
retensi yang lengkap dan tidak pernah menyapu, penilai PASAL 15.44 yang
menghitung putusan yang tidak pernah ditulis, `aruna.scenario.evaluasi` dengan
nol pemanggil, `Putusan.diinvalidasi` yang dihitung lalu dibuang, dan
`AnalysisService` yang membuat tabel `regimes` berhenti terisi sejak
2026-08-14. **Semuanya lulus test unitnya.**

Phase 17 adalah kandidat berikutnya yang paling jelas: tujuh modul yang seluruh
testnya hijau sementara tidak satu pun dipanggil siapa pun. Berkas ini dan
`test_router_fase` yang mengubahnya.

Penjaganya berbasis AST dan bukan pencarian teks, dengan alasan yang terbukti
di berkas ini sendiri: komentar di `app.py` **menyebut** `router=` untuk
menjelaskan kenapa barisnya ada, jadi pencarian teks akan lulus atas komentar
yang menerangkan baris yang sudah dihapus.
"""

from __future__ import annotations

import ast
import inspect
import textwrap


def _pohon(modul: object) -> ast.Module:
    """AST sebuah modul, kelas, atau metode.

    ``textwrap.dedent`` bukan kerapian: sumber sebuah METODE datang dengan
    indentasi kelasnya, dan `ast.parse` menolaknya dengan `IndentationError`.
    Test yang tidak mendedent akan MERAH atas alasan yang tidak ada
    hubungannya dengan apa yang ia jaga.
    """
    return ast.parse(textwrap.dedent(inspect.getsource(modul)))


def _kwarg_ke(pohon: ast.Module, fungsi: str, nama: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == fungsi
        and any(kw.arg == nama for kw in n.keywords)
        for n in ast.walk(pohon)
    )


class TestTersambungKeLoop:
    def test_loop_menerima_router(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        assert "router" in inspect.signature(UpkeepLoop.__init__).parameters

    def test_app_punya_pembangunnya(self) -> None:
        from aruna.app import ArunaApplication

        assert hasattr(ArunaApplication, "_build_router")

    def test_router_dioper_ke_upkeeploop_bukan_ke_sembarang_panggilan(self) -> None:
        """`router=` yang dioper ke fungsi lain akan lolos pemeriksaan yang
        lebih longgar. Yang dituntut adalah ia sampai ke `UpkeepLoop`."""
        from aruna import app

        assert _kwarg_ke(_pohon(app), "UpkeepLoop", "router")

    def test_pembangunnya_benar_benar_membangun_sesuatu(self) -> None:
        """`_build_router` yang selalu memulangkan `None` akan meloloskan
        seluruh test di atas - parameternya dioper, nilainya kosong, dan
        fasenya diam. Bentuk cacat yang sama, satu lapis lebih dalam."""
        from aruna.app import ArunaApplication

        pohon = _pohon(ArunaApplication._build_router)
        dibangun = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "FaseRouter" in dibangun
        assert "RouterRepository" in dibangun

    def test_loop_benar_benar_memanggil_fasenya(self) -> None:
        """Parameter yang diterima lalu disimpan tanpa pernah dipakai adalah
        bentuk cacat yang sama, satu lapis lebih dalam lagi."""
        from aruna.upkeep.loop import UpkeepLoop

        pohon = _pohon(UpkeepLoop._jalankan_router)
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "jalankan" in dipanggil

    def test_fasenya_dipanggil_dari_scan(self) -> None:
        """Router hanya boleh memilih untuk aset yang BENAR-BENAR dipindai
        siklus ini, jadi ia harus melihat `results` - bukan membaca ulang
        daftar aset sendiri.

        Terukur 2026-08-23 kenapa itu penting: batas umur bacaan dihitung dalam
        bar horizonnya sendiri, jadi jendela 1d membentang delapan HARI. 31
        simbol punya bacaan "segar" sementara yang dipindai dua puluh, dan
        sebelas sisanya akan menghasilkan sebelas baris NONE tiap siklus.
        """
        from aruna.upkeep.loop import UpkeepLoop

        assert "_jalankan_router" in inspect.getsource(UpkeepLoop._scan)


class TestSeluruhRantaiTask1SampaiTask7Terpakai:
    """**Yang paling mudah luput.** Fase bisa tersambung dan tetap memanggil
    hanya separuh dari yang dibangun. Tiap nama di bawah lahir di task yang
    berbeda; kalau salah satunya tidak muncul di sini, task itu kode mati yang
    terlihat sehat."""

    def test_fase_memanggil_setiap_tahap(self) -> None:
        from aruna.upkeep import router

        pohon = _pohon(router)
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        } | {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        wajib = {
            "susun_peta": "Task 1 - peta rezim multi-timeframe",
            "stabilitas": "Task 2 - stabilitas rezim",
            "performa_rezim": "Task 3 - slice per rezim yang menolak turunan",
            "nilai": "Task 4 - skor kecocokan",
            "kandidat_layak": "Task 5 - saringan status",
            "pilih": "Task 5 - champion, challenger, NONE",
            "simpan": "Task 7 - penyimpanan",
            "peta_rezim": "Task 8 - pembacaan sumber",
            "riwayat_15m": "Task 8 - riwayat untuk stabilitas",
            "lolos_gerbang": "Task 9 - gerbang risiko sesudah peringkat",
            "risiko_terakhir": "Task 9 - tingkat risiko yang tersimpan",
            "dari_tersimpan": "Task 9 - terjemahan kosakata risiko, satu tempat",
            "kenapa_berganti": "Task 10 - peralihan dicatat, bukan disimpulkan",
            "pilihan_terakhir": "Task 10 - pilihan sebelumnya untuk dibandingkan",
        }
        hilang = {k: v for k, v in wajib.items() if k not in dipanggil}

        assert not hilang, (
            "dibangun tapi tidak dipanggil fase router:\n"
            + "\n".join(f"  {k:<16} {v}" for k, v in sorted(hilang.items()))
        )


class TestStatistiknyaMembedakanDiamDariMati:
    def test_loop_mencatat_dipertimbangkan_bukan_cuma_terpilih(self) -> None:
        """**NONE adalah keluaran yang WAJAR bagi router ini** - 1.860 dari
        9.437 bacaan berlabel UNCERTAIN, dan tiap aset yang cuma punya satu
        horizon segar berkeyakinan di bawah ambang.

        Jadi `router_terpilih == 0` tidak membuktikan apa pun sendiri. Yang
        membedakan fase yang menolak dari fase yang mati adalah
        `router_dipertimbangkan`, dan tanpa bidang itu keduanya terlihat sama
        persis dari luar.
        """
        from datetime import UTC, datetime

        from aruna.upkeep.loop import UpkeepStats

        stats = UpkeepStats(started_at=datetime(2026, 8, 23, tzinfo=UTC))

        assert stats.router_dipertimbangkan == 0
        assert stats.router_terpilih == 0
        assert stats.last_router_at is None
