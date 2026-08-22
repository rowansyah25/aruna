"""Pembersih retensi benar-benar dipanggil loop upkeep (bagian 25).

Kegagalan yang paling sering terjadi di repo ini bukan kode yang salah
melainkan kode yang benar dan tidak pernah dipanggil: `AdaptiveLearningService`
hidup berbulan-bulan tanpa penggeraknya, `korelasi` menghasilkan nol baris
sementara empat puluh amatan melaporkan CORRELATION_RISK hilang, dan proyektor
ingatan sempat tersambung ke proses yang salah.

Test di sini menjalankan `cycle()` yang sungguhan.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from textwrap import dedent
from typing import Any

import pytest

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


class _PembersihPalsu:
    def __init__(self) -> None:
        self.panggilan: list[dict[str, Any]] = []
        self.hasil: dict[str, int] = {"market_snapshots": 7}

    async def sapu(self, *, now: datetime, batas_total: int) -> dict[str, int]:
        self.panggilan.append({"now": now, "batas_total": batas_total})
        return dict(self.hasil)


class _PembersihMeledak:
    async def sapu(self, *, now: datetime, batas_total: int) -> dict[str, int]:
        raise RuntimeError("basis data sedang tidak enak badan")


def _loop(pembersih: Any):
    """Loop yang hanya fase retensinya hidup.

    Dibangun lewat `__new__` supaya tick ini tidak menarik seluruh ARUNA -
    tapi setiap bidang yang fase ini baca dipasang dari objek yang sungguhan,
    bukan dikarang.
    """
    from aruna.core.config import UpkeepSettings
    from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

    loop = UpkeepLoop.__new__(UpkeepLoop)
    loop._settings = UpkeepSettings()
    loop._stats = UpkeepStats(started_at=NOW)
    loop._retensi = pembersih
    return loop


class TestFasenyaDipanggil:
    @pytest.mark.asyncio
    async def test_sapuan_pertama_langsung_jalan(self) -> None:
        """Tanpa stempel sebelumnya, tidak ada dasar untuk menunggu sehari."""
        pembersih = _PembersihPalsu()
        loop = _loop(pembersih)

        assert loop._retensi_due_now(NOW)
        await loop._sapu_retensi(NOW)

        assert len(pembersih.panggilan) == 1
        assert pembersih.panggilan[0]["now"] == NOW

    @pytest.mark.asyncio
    async def test_tidak_diulang_sebelum_cadence_lewat(self) -> None:
        """Setiap sapuan memindai indeks tabel terbesar di basis data;
        mengulanginya tiap tick adalah beban tanpa jawaban baru."""
        loop = _loop(_PembersihPalsu())
        await loop._sapu_retensi(NOW)

        assert not loop._retensi_due_now(NOW + timedelta(hours=1))
        assert loop._retensi_due_now(NOW + timedelta(days=1, seconds=1))

    @pytest.mark.asyncio
    async def test_mati_kalau_pembersihnya_tidak_dioper(self) -> None:
        """`None` harus benar-benar mematikan fasenya, bukan meledak."""
        loop = _loop(None)

        assert not loop._retensi_due_now(NOW)

    @pytest.mark.asyncio
    async def test_batas_sapuan_diteruskan_dari_setelan(self) -> None:
        """Bagian 26: cleanup harus punya limit. Limit yang ada di setelan tapi
        tidak diteruskan sama saja dengan tidak ada limit."""
        from aruna.core.config import UpkeepSettings

        pembersih = _PembersihPalsu()
        loop = _loop(pembersih)
        await loop._sapu_retensi(NOW)

        assert (
            pembersih.panggilan[0]["batas_total"]
            == UpkeepSettings().retensi_batas_sapuan
        )


class TestKegagalanTidakMenjatuhkanSiklus:
    @pytest.mark.asyncio
    async def test_pembersih_yang_meledak_dicatat_bukan_dilempar(self) -> None:
        """Satu fase yang gagal tidak boleh membawa serta laporan harian,
        proyeksi ingatan, dan denyut yang berjalan sesudahnya."""
        loop = _loop(_PembersihMeledak())

        await loop._sapu_retensi(NOW)

        assert loop._stats.retensi_failures == 1
        assert loop._stats.errors

    @pytest.mark.asyncio
    async def test_cadence_tetap_maju_meski_gagal(self) -> None:
        """Stempel pada percobaan, bukan keberhasilan: sapuan yang terus gagal
        harus tetap sehari sekali, bukan dicoba ulang tiap tick terhadap basis
        data yang sedang bermasalah."""
        loop = _loop(_PembersihMeledak())

        await loop._sapu_retensi(NOW)

        assert loop._stats.last_retensi_at == NOW
        assert not loop._retensi_due_now(NOW + timedelta(hours=1))


class TestTerpasangDiSiklus:
    def test_cycle_memanggil_fase_retensi(self) -> None:
        """Penjaga AST, bukan pencarian teks: pencarian teks cocok juga dengan
        baris yang sudah dikomentari, dan itu sudah pernah membuat test di repo
        ini hijau di atas fase yang mati."""
        from aruna.upkeep.loop import UpkeepLoop

        pohon = ast.parse(dedent(inspect.getsource(UpkeepLoop.cycle)))
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "_retensi_due_now" in dipanggil
        assert "_sapu_retensi" in dipanggil

    def test_retensi_sesudah_proyeksi_ingatan(self) -> None:
        """Proyeksi ingatan membaca candle untuk menghitung dimensi
        teknikalnya. Membersihkan lebih dulu berarti sapuan hari itu bisa
        membuang bar yang proyeksi menit berikutnya butuhkan, dan ingatan yang
        lahir sesudahnya kehilangan dimensinya tanpa satu pun error.
        """
        from aruna.upkeep.loop import UpkeepLoop

        pohon = ast.parse(dedent(inspect.getsource(UpkeepLoop.cycle)))
        urut = [
            (n.func.attr, n.lineno)
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("_proyeksikan_memory", "_sapu_retensi")
        ]
        posisi = dict(urut)

        assert posisi["_proyeksikan_memory"] < posisi["_sapu_retensi"]


class TestTerangkaiDiProduksi:
    """Fase yang benar dan tidak dirangkai adalah kegagalan yang sudah terjadi
    di repo ini: `memory=` sempat dioper ke proses yang salah, dan produksi
    melaporkan `memory_pengaruh=UNKNOWN` pada keempat puluh amatan sementara
    seluruh testnya hijau."""

    def test_app_mengoper_retensi_ke_loop(self) -> None:
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

        assert "retensi" in kata

    def test_pembangunnya_memakai_rencana_bawaan(self) -> None:
        """Rencana yang disusun ulang di lapisan perangkaian bisa menyimpang
        dari `DILINDUNGI` tanpa satu pun test menyentuhnya."""
        from aruna import app as modul

        pohon = ast.parse(
            dedent(inspect.getsource(modul.ArunaApplication._build_retensi))
        )
        kata = [
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            for k in n.keywords
        ]

        assert "rencana" not in kata
