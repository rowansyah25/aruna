"""Sapuan harian tidak boleh diulang tiap restart.

**Terukur 2026-08-22.** `UpkeepStats.last_manfaat_at` hidup di memori proses,
jadi tiap kali ARUNA menyala ia ``None`` - dan ``None`` diperlakukan sebagai
"belum pernah dinilai". Sapuan yang intervalnya 86.400 detik karena itu berjalan
lagi di siklus pertama setiap restart. Pada hari dengan belasan restart, sapuan
harian dibayar belasan kali.

Biayanya bukan cuma CPU. Siklus pertama adalah siklus terberat - seluruh horizon
jatuh tempo bersamaan karena `_locked_bar` juga kosong - dan menambahkan sapuan
satu setengah menit di atasnya membuat `upkeep` dilaporkan DOWN selama lima
menit sesudah tiap restart.

Jawabannya **sudah tersimpan sejak dulu**: tiap `Manfaat` membawa
``dinilai_pada`` dan seluruhnya ditulis ke `app_state`. Yang tidak ada cuma
pembacanya.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.memory.evaluasi import Evaluasi
from aruna.memory.manfaat import KUNCI_STATE, Manfaat, ke_json
from aruna.upkeep.manfaat import PenilaiManfaat

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _manfaat(tf: str, pada: datetime) -> Manfaat:
    return Manfaat(
        timeframe=tf,
        evaluasi=Evaluasi(
            mendukung_menang=60,
            mendukung_kalah=40,
            melawan_menang=40,
            melawan_kalah=60,
        ),
        dinilai_pada=pada,
        dinilai_dari=1000,
    )


class _StatePalsu:
    """Bidangnya sama dengan `AppStateRepository`: `get` memulangkan dict atau
    ``None``, `set` menulis. Double yang bentuknya lebih longgar dari objek
    aslinya membuat suite hijau di atas bug produksi."""

    def __init__(self, isi=None, meledak: bool = False) -> None:
        self.isi = isi
        self.meledak = meledak
        self.dibaca: list[str] = []
        self.ditulis: list[str] = []

    async def get(self, kunci: str):
        self.dibaca.append(kunci)
        if self.meledak:
            raise RuntimeError("app_state tak terbaca")
        return self.isi

    async def set(self, kunci, nilai, *, actor):
        self.ditulis.append(actor)


class _MemoryPalsu:
    async def ingatan_berarah(self, *, timeframe, as_of, limit):
        return []


def _penilai(state) -> PenilaiManfaat:
    return PenilaiManfaat(memory=_MemoryPalsu(), app_state=state)


@pytest.mark.asyncio
class TestStempelDibacaKembali:
    async def test_stempel_terbaru_dipulangkan(self) -> None:
        """Yang dipakai stempel TERBARU dari seluruh timeframe. Yang terlama
        akan membuat sapuan berjalan lagi terlalu cepat."""
        state = _StatePalsu(
            ke_json({
                "15m": _manfaat("15m", NOW - timedelta(hours=5)),
                "1h": _manfaat("1h", NOW - timedelta(hours=1)),
                "1d": _manfaat("1d", NOW - timedelta(hours=9)),
            })
        )

        assert await _penilai(state).terakhir_dinilai() == NOW - timedelta(hours=1)

    async def test_membaca_kunci_yang_sama_dengan_yang_ditulis(self) -> None:
        """Pembaca dan penulis yang memakai kunci berbeda menghasilkan sistem
        yang menulis dengan benar, membaca dengan benar, dan tidak pernah
        menemukan apa pun - tanpa satu pun galat."""
        state = _StatePalsu(None)
        await _penilai(state).terakhir_dinilai()

        assert state.dibaca == [KUNCI_STATE]

    async def test_state_kosong_memulangkan_none(self) -> None:
        assert await _penilai(_StatePalsu(None)).terakhir_dinilai() is None

    async def test_state_rusak_memulangkan_none(self) -> None:
        """`app_state` yang ditulis versi lama atau rusak sebagian tidak boleh
        menjatuhkan siklus - dan "tidak bisa dibaca" menuntut hal yang sama
        dengan "belum pernah dinilai": nilai sekarang."""
        assert await _penilai(_StatePalsu({"15m": {"bentuk": "asing"}})).terakhir_dinilai() is None

    async def test_basis_data_meledak_tidak_melempar(self) -> None:
        """Penilaian ini bukan syarat ARUNA berjalan."""
        assert await _penilai(_StatePalsu(meledak=True)).terakhir_dinilai() is None


class _ManfaatPalsu:
    def __init__(self, terakhir) -> None:
        self._terakhir = terakhir
        self.dibaca = 0
        self.dinilai = 0

    async def terakhir_dinilai(self):
        self.dibaca += 1
        return self._terakhir

    async def nilai(self, *, now):
        self.dinilai += 1
        return {}


def _loop(manfaat, interval: float = 86_400.0):
    from aruna.core.config import UpkeepSettings
    from aruna.upkeep.loop import UpkeepLoop

    return UpkeepLoop(
        refresher=None,
        resolver=None,
        locker=None,
        settings=UpkeepSettings(
            manfaat_enabled=True,
            manfaat_interval_sec=interval,
            lock_enabled=False,
            resolve_enabled=False,
            news_enabled=False,
        ),
        manfaat=manfaat,
    )


@pytest.mark.asyncio
class TestRestartTidakMengulang:
    async def test_penilaian_baru_menunda_sapuan(self) -> None:
        """Inti seluruh perbaikan."""
        m = _ManfaatPalsu(NOW - timedelta(minutes=30))

        assert not await _loop(m)._manfaat_due_now(NOW)

    async def test_penilaian_lama_tetap_menjalankannya(self) -> None:
        """Penjaga harus melepaskan. Kalau tidak, sapuan berhenti berjalan
        selamanya sesudah penilaian pertama."""
        m = _ManfaatPalsu(NOW - timedelta(days=2))

        assert await _loop(m)._manfaat_due_now(NOW)

    async def test_belum_pernah_dinilai_tetap_menjalankannya(self) -> None:
        m = _ManfaatPalsu(None)

        assert await _loop(m)._manfaat_due_now(NOW)

    async def test_dibaca_sekali_saja(self) -> None:
        """Sesudah pembacaan pertama, memori proses sudah menjadi sumber yang
        benar. Kueri per siklus untuk jawaban yang tidak berubah adalah biaya
        tanpa imbalan."""
        m = _ManfaatPalsu(NOW - timedelta(minutes=30))
        loop = _loop(m)

        for _ in range(5):
            await loop._manfaat_due_now(NOW)

        assert m.dibaca == 1

    async def test_pembaca_yang_tidak_ada_tidak_meledak(self) -> None:
        """Penilai yang dioper test lama tidak punya `terakhir_dinilai`.
        Perbaikan yang meledak atas double lama akan membuat seluruh suite
        merah karena alasan yang tidak ada hubungannya."""

        class _Lawas:
            async def nilai(self, *, now):
                return {}

        assert await _loop(_Lawas())._manfaat_due_now(NOW)
