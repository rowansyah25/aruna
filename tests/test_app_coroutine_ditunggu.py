"""Setiap coroutine di `app.py` benar-benar ditunggu.

**Bug yang ditemukan 2026-08-24 dari layar operator, bukan dari suite.**

`ArunaApplication._start_telegram` memanggil `self._note_telegram_failure(exc)`
tanpa `await` pada cabang `TelegramError`, sementara cabang `ArunaError` tepat
di bawahnya memakai `await` dengan benar. Fungsinya `async def`, jadi coroutine
yang tidak ditunggu tidak pernah dijadwalkan:

* `log.error("telegram.start_failed")` tidak pernah tercetak
* event `START_FAILED` **tidak pernah ditulis** ke `system_events`
* Python hanya menggumamkan `RuntimeWarning: coroutine was never awaited`

Dan `TelegramError` justru yang dilempar `bot.start()` untuk kegagalan paling
umum, termasuk `Conflict`. Akibatnya pemeriksaan health mencetak "a bot token
is configured but the bot did not start - see the telegram START_FAILED event
for the reason", dan event itu **tidak ada**. Operator diarahkan ke tempat yang
kosong.

Bentuk cacatnya sama dengan keluarga yang berulang di proyek ini: kode yang
benar, ada di tempatnya, dan tidak pernah berjalan. Yang berbeda cuma sebabnya -
satu kata yang hilang, bukan satu baris perakitan yang lupa.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from aruna import app


def _pohon() -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(app)))


def _nama_async(pohon: ast.Module) -> set[str]:
    """Metode `async def` di seluruh modul."""
    return {
        n.name
        for n in ast.walk(pohon)
        if isinstance(n, ast.AsyncFunctionDef)
    }


def _panggilan_self_tanpa_await(pohon: ast.Module, async_names: set[str]) -> list[str]:
    """``self.<coroutine>(...)`` yang tidak berada di dalam ``await``.

    Yang dicari panggilan telanjang - bukan yang dioper ke
    ``asyncio.create_task`` atau ``asyncio.gather``, karena keduanya memang
    menerima coroutine dan menjadwalkannya sendiri.
    """
    ditunggu: set[int] = set()
    dijadwalkan: set[int] = set()

    for n in ast.walk(pohon):
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
            ditunggu.add(id(n.value))
        elif isinstance(n, ast.Call):
            nama = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nama in ("create_task", "gather", "ensure_future", "wait_for"):
                for arg in n.args:
                    if isinstance(arg, ast.Call):
                        dijadwalkan.add(id(arg))

    keluar: list[str] = []
    for n in ast.walk(pohon):
        if not isinstance(n, ast.Call):
            continue
        if id(n) in ditunggu or id(n) in dijadwalkan:
            continue
        f = n.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "self"
            and f.attr in async_names
        ):
            keluar.append(f"baris {n.lineno}: self.{f.attr}(...)")
    return keluar


class TestCoroutineTidakDitinggalkan:
    def test_tidak_ada_panggilan_coroutine_yang_lupa_await(self) -> None:
        """Coroutine yang tidak ditunggu tidak pernah berjalan, dan Python
        hanya menggumamkan RuntimeWarning yang tenggelam di antara ribuan baris
        log. Kegagalannya senyap, dan yang hilang justru catatan kegagalan.
        """
        pohon = _pohon()
        lupa = _panggilan_self_tanpa_await(pohon, _nama_async(pohon))

        assert not lupa, (
            "coroutine dipanggil tanpa `await` di app.py - ia tidak akan "
            "pernah berjalan:\n  " + "\n  ".join(lupa)
        )

    def test_penjaganya_benar_benar_bisa_melihat(self) -> None:
        """Penjaga yang tidak bisa menemukan apa pun hijau selamanya. Ini
        membuktikan ia mengenali bentuk yang dicarinya."""
        contoh = ast.parse(
            "class A:\n"
            "    async def catat(self): ...\n"
            "    async def jalan(self):\n"
            "        self.catat()\n"
        )
        lupa = _panggilan_self_tanpa_await(contoh, _nama_async(contoh))

        assert lupa

    def test_yang_ditunggu_tidak_dituduh(self) -> None:
        contoh = ast.parse(
            "class A:\n"
            "    async def catat(self): ...\n"
            "    async def jalan(self):\n"
            "        await self.catat()\n"
        )

        assert not _panggilan_self_tanpa_await(contoh, _nama_async(contoh))

    def test_yang_dijadwalkan_create_task_tidak_dituduh(self) -> None:
        """`asyncio.create_task` memang menerima coroutine dan menjadwalkannya
        sendiri - menuduhnya akan memaksa `await` yang justru membuat tugas
        latar berhenti menjadi latar."""
        contoh = ast.parse(
            "class A:\n"
            "    async def ulang(self): ...\n"
            "    async def jalan(self):\n"
            "        self.t = asyncio.create_task(self.ulang())\n"
        )

        assert not _panggilan_self_tanpa_await(contoh, _nama_async(contoh))
