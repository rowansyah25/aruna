"""Batas MiroFish: ada, kosong, dan tidak bisa mengeksekusi apa pun.

Berkas ini lahir untuk Phase 15 bagian 29 - *"JANGAN mengimplementasikan
MiroFish pada phase ini"* - dan bertahan ke Phase 16 karena bagian 16.16
menuntut hal yang sama dengan kata-kata yang lebih tegas: MiroFish **TIDAK**
vote LONG, vote SHORT, execute trades, override Risk, override Quality,
override Master Orchestrator, atau execute Binance orders.

Yang berubah cuma tempatnya: `aruna.scenario` sekarang paket, dan batasnya ada
di `aruna.scenario.adapter`. Penjaganya tidak boleh padam sedetik pun saat
berpindah - itu sebabnya ia dipindahkan bersama, bukan ditulis ulang nanti.

**MiroFish sendiri tidak ada.** Dicari 2026-08-22: tidak ada pustaka, tidak ada
konfigurasi, tidak ada dokumen, dan tidak ada kemampuan LLM apa pun di ARUNA.
Yang diuji di sini adalah bahwa batasnya benar - bukan bahwa mesinnya bekerja.
"""

from __future__ import annotations

import ast
import inspect

from aruna.scenario import adapter
from aruna.scenario.adapter import (
    HasilAdapter,
    ScenarioEngineInterface,
    StatusSimulasi,
)


class TestAntarmukanyaAda:
    def test_protokolnya_bisa_diimpor(self) -> None:
        assert ScenarioEngineInterface is not None

    def test_protokol_bukan_kelas_konkret(self) -> None:
        """Kelas konkret akan menggoda seseorang membuat instansnya dan
        bertanya-tanya kenapa tidak melakukan apa-apa."""
        from typing import Protocol

        assert Protocol in ScenarioEngineInterface.__mro__

    def test_menjanjikan_simulasi_bukan_perintah(self) -> None:
        metode = {
            n for n, _ in inspect.getmembers(
                ScenarioEngineInterface, inspect.isfunction
            )
            if not n.startswith("_")
        }

        assert "simulasikan" in metode


class TestTidakAdaJalurEksekusi:
    """Bagian 16.16 dan 16.20."""

    def test_tidak_ada_metode_yang_mengeksekusi(self) -> None:
        terlarang = {
            "execute", "place_order", "submit", "trade", "buy", "sell",
            "close_position", "open_position", "set_leverage", "transfer",
            "eksekusi", "kirim_order", "vote",
        }
        metode = {
            n for n, _ in inspect.getmembers(
                ScenarioEngineInterface, inspect.isfunction
            )
        }

        assert not (metode & terlarang)

    def test_modulnya_tidak_mengimpor_apa_pun_yang_bisa_memesan(self) -> None:
        """Adapter yang tidak mengeksekusi apa-apa tapi mengimpor adapter venue
        sudah setengah jalan ke sana.

        AST, bukan pencarian teks - dan alasannya sama persis dengan
        `test_tidak_menyentuh_arah_keputusan` di bawah: docstring modulnya
        MENGUTIP larangan bagian 16.16 ("execute Binance orders"), jadi
        pencarian teks gagal justru karena modulnya menjelaskan dengan benar
        apa yang tidak boleh ia lakukan.

        Impor lokal di dalam fungsi ikut terjaring: ``ast.walk`` tidak peduli
        kedalamannya, dan impor yang disembunyikan di dalam metode adalah cara
        paling wajar seseorang menyelundupkan jalur eksekusi.
        """
        pohon = ast.parse(inspect.getsource(adapter))
        modul: set[str] = set()
        for simpul in ast.walk(pohon):
            if isinstance(simpul, ast.Import):
                modul |= {a.name for a in simpul.names}
            elif isinstance(simpul, ast.ImportFrom):
                modul.add(simpul.module or "")

        akar = {m.split(".")[0].lower() for m in modul}
        assert not (akar & {
            "binance", "ccxt", "requests", "httpx", "aiohttp", "urllib",
            "socket", "subprocess",
        }), akar

    def test_tidak_menyentuh_arah_keputusan(self) -> None:
        """Bagian 16.18. AST, bukan pencarian teks: docstring modulnya
        MENJELASKAN bahwa ia tidak boleh vote LONG, dan pencarian teks akan
        tersandung pada penjelasannya sendiri."""
        pohon = ast.parse(inspect.getsource(adapter))
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
        nama |= {n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)}
        nama |= {
            a.name for n in ast.walk(pohon)
            if isinstance(n, ast.ImportFrom) for a in n.names
        }

        assert not (nama & {"Decision", "direction", "decision", "arah"})

    def test_tidak_ada_implementasi_di_fase_ini(self) -> None:
        """Yang dilarang adalah **mengimplementasikan** protokolnya - bukan
        memiliki tipe data. `HasilAdapter` adalah bentuk jawabannya, dan bentuk
        tanpa mesin tidak mensimulasikan apa pun."""
        pelaksana = [
            n for n, obj in inspect.getmembers(adapter, inspect.isclass)
            if obj.__module__ == adapter.__name__
            and n != "ScenarioEngineInterface"
            and hasattr(obj, "simulasikan")
        ]

        assert pelaksana == [], pelaksana


class TestDegradedDanTimeout:
    """Bagian 16.12 dan 16.13."""

    def test_degraded_dibedakan_dari_timeout(self) -> None:
        """"MiroFish belum dipasang" dan "MiroFish sedang bermasalah" adalah
        dua keadaan yang menuntut tindakan berbeda."""
        assert StatusSimulasi.DEGRADED is not StatusSimulasi.TIMEOUT

    def test_hanya_ok_yang_terpakai(self) -> None:
        """Bagian 16.13: jangan menggunakan hasil yang sudah stale. Hasil
        sebagian dari simulasi yang kehabisan waktu terlihat seperti bukti dan
        bukan bukti."""
        assert HasilAdapter(status=StatusSimulasi.OK).terpakai
        for buruk in (
            StatusSimulasi.DEGRADED, StatusSimulasi.TIMEOUT, StatusSimulasi.GAGAL
        ):
            assert not HasilAdapter(status=buruk).terpakai

    def test_degraded_tetap_memulangkan_hasil_bukan_melempar(self) -> None:
        """Bagian 16.12: MiroFish yang gagal tidak menghentikan ARUNA."""
        hasil = HasilAdapter(
            status=StatusSimulasi.DEGRADED, catatan="mesin belum dipasang"
        )

        assert hasil.skenario == ()
        assert hasil.catatan

    def test_timeoutnya_lebih_pendek_dari_horizon_terpendek(self) -> None:
        """Horizon terpendek ARUNA lima belas menit. Simulasi yang memakan
        sepertiga menit sudah menggambarkan pasar yang berbeda dari yang
        memicunya."""
        from aruna.scenario.adapter import TIMEOUT_DETIK

        assert 0 < TIMEOUT_DETIK <= 60
