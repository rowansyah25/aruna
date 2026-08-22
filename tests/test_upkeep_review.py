"""Kalibrasi dan reliability diukur sendiri, lalu benar-benar dipakai lagi.

Terukur 2026-08-21: `calibration_snapshots` berisi **3 baris**, terakhir
diukur **2026-08-15** - enam hari sebelumnya. `learning.review()` punya tepat
satu pemanggil di seluruh kode, yaitu perintah CLI `aruna learn`, dan tidak
seorang pun mengetiknya sejak itu.

Keluarga cacat yang sama dengan `korelasi`, `memory`, `retensi`, dan
`manfaat`: kode yang benar, teruji, dan hanya berjalan saat seseorang
mengetik perintahnya.

**Dan satu langkah lagi yang mudah hilang.** `_load_measured_history` hanya
berjalan saat start. Mengukur ulang tiap hari tanpa menerapkannya kembali
berarti council memakai angka dari saat proses menyala sampai proses itu
dimatikan - pengukuran yang dihitung lalu dibuang, persis cacat yang sudah
berulang di repo ini.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from textwrap import dedent
from typing import Any

import pytest

NOW = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)


class _Hasil:
    reviewed = 42


class _Pembelajaran:
    def __init__(self) -> None:
        self.panggilan: list[dict[str, Any]] = []
        self.diterapkan = 0

    async def review(self, *, limit: int, persist: bool) -> _Hasil:
        self.panggilan.append({"limit": limit, "persist": persist})
        return _Hasil()

    async def measured_history(self) -> str:
        self.diterapkan += 1
        return "sejarah-baru"


class _Meledak:
    async def review(self, *, limit: int, persist: bool) -> _Hasil:
        raise RuntimeError("tabel outcome sedang dikunci")


class _Penerima:
    def __init__(self) -> None:
        self.dipakai: list[Any] = []

    def use_history(self, history: Any) -> None:
        self.dipakai.append(history)


def _loop(pembelajaran: Any, *, council: Any = None, signals: Any = None):
    from aruna.core.config import UpkeepSettings
    from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

    loop = UpkeepLoop.__new__(UpkeepLoop)
    loop._settings = UpkeepSettings()
    loop._stats = UpkeepStats(started_at=NOW)
    loop._review = pembelajaran
    loop._review_council = council
    loop._review_signals = signals
    return loop


class TestFasenyaDipanggil:
    @pytest.mark.asyncio
    async def test_sapuan_pertama_langsung_jalan(self) -> None:
        p = _Pembelajaran()
        loop = _loop(p)

        assert loop._review_due_now(NOW)
        await loop._review_pembelajaran(NOW)

        assert len(p.panggilan) == 1

    @pytest.mark.asyncio
    async def test_menyimpan_hasilnya(self) -> None:
        """`persist=False` akan mengukur lalu membuang - tabelnya tetap berhenti
        di 2026-08-15."""
        p = _Pembelajaran()
        await _loop(p)._review_pembelajaran(NOW)

        assert p.panggilan[0]["persist"] is True

    @pytest.mark.asyncio
    async def test_tidak_diulang_sebelum_cadence(self) -> None:
        loop = _loop(_Pembelajaran())
        await loop._review_pembelajaran(NOW)

        assert not loop._review_due_now(NOW + timedelta(hours=1))
        assert loop._review_due_now(NOW + timedelta(days=1, seconds=1))

    @pytest.mark.asyncio
    async def test_mati_tanpa_pembelajaran(self) -> None:
        assert not _loop(None)._review_due_now(NOW)


class TestHasilnyaDipakaiLagi:
    """Bagian yang paling mudah hilang: mengukur ulang tanpa menerapkan."""

    @pytest.mark.asyncio
    async def test_sejarah_baru_diserahkan_ke_council(self) -> None:
        council, signals = _Penerima(), _Penerima()
        p = _Pembelajaran()

        await _loop(p, council=council, signals=signals)._review_pembelajaran(NOW)

        assert council.dipakai == ["sejarah-baru"]
        assert signals.dipakai == ["sejarah-baru"]

    @pytest.mark.asyncio
    async def test_tanpa_penerima_tidak_meledak(self) -> None:
        """`aruna run` tanpa council tetap harus bisa mengukur."""
        p = _Pembelajaran()
        await _loop(p)._review_pembelajaran(NOW)

        assert p.diterapkan == 1


class TestKegagalanTidakMenjatuhkanSiklus:
    @pytest.mark.asyncio
    async def test_gagal_dicatat_bukan_dilempar(self) -> None:
        loop = _loop(_Meledak())

        await loop._review_pembelajaran(NOW)

        assert loop._stats.review_failures == 1
        assert loop._stats.errors

    @pytest.mark.asyncio
    async def test_cadence_maju_meski_gagal(self) -> None:
        loop = _loop(_Meledak())
        await loop._review_pembelajaran(NOW)

        assert loop._stats.last_review_at == NOW


class TestTerpasang:
    def test_cycle_memanggilnya(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        pohon = ast.parse(dedent(inspect.getsource(UpkeepLoop.cycle)))
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "_review_due_now" in dipanggil
        assert "_review_pembelajaran" in dipanggil

    def test_app_merangkainya(self) -> None:
        from aruna import app as modul

        pohon = ast.parse(
            dedent(inspect.getsource(modul.ArunaApplication._start_upkeep))
        )
        kata = {
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "UpkeepLoop"
            for k in n.keywords
        }

        assert "review" in kata
        # Penerimanya juga, kalau tidak pengukurannya tidak pernah dipakai lagi.
        assert "review_council" in kata
        assert "review_signals" in kata
