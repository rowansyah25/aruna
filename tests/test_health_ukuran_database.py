"""Ukuran basis data sebagai komponen health (bagian 27-28 spec).

Sebelum ini tidak ada satu pun yang mengawasi pertumbuhan. Audit 2026-08-21
menemukan basis data 506 MB yang bertambah 69.048 baris snapshot sehari, tanpa
retention dan tanpa peringatan - dan yang membuatnya terlihat cuma seseorang
kebetulan mengetik kueri `information_schema`.

Komponen sendiri, bukan tempelan pada `DatabaseCheck`, karena pertanyaannya
berbeda: `DatabaseCheck` menjawab "bisa dihubungi?", ini menjawab "muat berapa
lama lagi?". Peringatan pertumbuhan yang muncul sebagai masalah koneksi akan
disalahbaca, dan sebaliknya.
"""

from __future__ import annotations

from typing import Any

import pytest

from aruna.core.enums import HealthStatus
from aruna.health.ukuran import JEDA_UKUR_DETIK, UkuranDatabaseCheck


class _DB:
    """Basis data yang memulangkan ukuran yang ditentukan test."""

    def __init__(self, *, mb: float = 100.0, terbesar: str = "market_snapshots",
                 terbesar_mb: float = 50.0, terhubung: bool = True) -> None:
        self.is_connected = terhubung
        self._mb = mb
        self._terbesar = terbesar
        self._terbesar_mb = terbesar_mb
        self.kueri = 0

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.kueri += 1
        if "table_name" in sql:
            return {"t": self._terbesar, "mb": self._terbesar_mb}
        return {"mb": self._mb}


class _DBMeledak:
    is_connected = True

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        from aruna.core.errors import DatabaseError

        raise DatabaseError("information_schema tidak bisa dibaca")


class TestAmbang:
    @pytest.mark.asyncio
    async def test_di_bawah_ambang_sehat(self) -> None:
        cek = UkuranDatabaseCheck(_DB(mb=100.0), peringatan_mb=1000, kritis_mb=2000)

        hasil = await cek.check()

        assert hasil.status is HealthStatus.UP
        assert hasil.details["total_mb"] == 100.0

    @pytest.mark.asyncio
    async def test_melewati_ambang_peringatan_degraded(self) -> None:
        cek = UkuranDatabaseCheck(_DB(mb=1500.0), peringatan_mb=1000, kritis_mb=2000)

        hasil = await cek.check()

        assert hasil.status is HealthStatus.DEGRADED
        assert "1000" in hasil.message or "1,000" in hasil.message

    @pytest.mark.asyncio
    async def test_melewati_ambang_kritis_down(self) -> None:
        """Bagian 28. DOWN, bukan DEGRADED: basis data yang penuh menghentikan
        seluruh sistem, dan peringatan yang terlalu lembut akan diabaikan
        sampai itu terjadi."""
        cek = UkuranDatabaseCheck(_DB(mb=2500.0), peringatan_mb=1000, kritis_mb=2000)

        hasil = await cek.check()

        assert hasil.status is HealthStatus.DOWN

    @pytest.mark.asyncio
    async def test_menyebut_tabel_terbesar(self) -> None:
        """Peringatan yang cuma bilang "basis data besar" menyuruh operator
        mengulang seluruh audit ini dari nol."""
        cek = UkuranDatabaseCheck(
            _DB(mb=1500.0, terbesar="council_votes", terbesar_mb=900.0),
            peringatan_mb=1000, kritis_mb=2000,
        )

        hasil = await cek.check()

        assert hasil.details["terbesar"] == "council_votes"
        assert hasil.details["terbesar_mb"] == 900.0
        assert "council_votes" in hasil.message


class TestOngkosnya:
    @pytest.mark.asyncio
    async def test_hasilnya_disimpan_antar_sapuan(self) -> None:
        """Health menyapu tiap 30 detik; kueri `information_schema` memindai
        metadata seluruh 52 tabel. Menjalankannya tiap sapuan berarti membayar
        pemindaian itu 2.880 kali sehari untuk angka yang bergerak dalam
        satuan jam."""
        db = _DB()
        cek = UkuranDatabaseCheck(db)

        await cek.check(sekarang=1000.0)
        await cek.check(sekarang=1000.0 + JEDA_UKUR_DETIK - 1)

        assert db.kueri == 2  # satu untuk total, satu untuk tabel terbesar

    @pytest.mark.asyncio
    async def test_diukur_lagi_sesudah_jedanya(self) -> None:
        db = _DB()
        cek = UkuranDatabaseCheck(db)

        await cek.check(sekarang=1000.0)
        await cek.check(sekarang=1000.0 + JEDA_UKUR_DETIK)

        assert db.kueri == 4

    @pytest.mark.asyncio
    async def test_status_tetap_dilaporkan_dari_simpanan(self) -> None:
        """Simpanan yang membuat komponennya melaporkan UNKNOWN di antara
        pengukuran akan membuat health berkedip tiap 30 detik."""
        cek = UkuranDatabaseCheck(_DB(mb=1500.0), peringatan_mb=1000, kritis_mb=2000)

        await cek.check(sekarang=1000.0)
        hasil = await cek.check(sekarang=1001.0)

        assert hasil.status is HealthStatus.DEGRADED
        assert hasil.details["total_mb"] == 1500.0


class TestKegagalan:
    @pytest.mark.asyncio
    async def test_pool_tertutup_bukan_kegagalan_ukuran(self) -> None:
        """`DatabaseCheck` sudah meneriakkan koneksi yang mati. Meneriakkannya
        dua kali membuat satu kegagalan tampak seperti dua."""
        cek = UkuranDatabaseCheck(_DB(terhubung=False))

        hasil = await cek.check()

        assert hasil.status is HealthStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_kueri_gagal_tidak_melempar(self) -> None:
        """Satu komponen yang melempar akan menggugurkan seluruh sapuan health
        dan membuat komponen sisanya tidak diketahui."""
        cek = UkuranDatabaseCheck(_DBMeledak())

        hasil = await cek.check()

        assert hasil.status is HealthStatus.UNKNOWN
        assert "information_schema" in hasil.message


class TestNamanya:
    def test_namanya_bukan_database(self) -> None:
        """Dua komponen bernama sama akan saling menimpa di peta transisi
        `HealthMonitor`, dan salah satunya berhenti pernah dilaporkan."""
        from aruna.health.checks import DatabaseCheck

        assert UkuranDatabaseCheck.name != DatabaseCheck.name

    def test_memenuhi_protokol_health_check(self) -> None:
        """`HealthMonitor` memanggil `.check()` tanpa argumen. Tanda tangan
        yang mewajibkan `sekarang` akan meledak di sapuan pertama - dan
        seluruh test di atas mengopernya, jadi tidak ada yang menangkapnya."""
        import inspect

        sig = inspect.signature(UkuranDatabaseCheck.check)
        for nama, p in sig.parameters.items():
            if nama == "self":
                continue
            assert p.default is not inspect.Parameter.empty, nama


class TestTerangkaiDiProduksi:
    def test_app_mendaftarkannya_sebagai_komponen(self) -> None:
        """Komponen yang benar dan tidak didaftarkan tidak pernah menyapu -
        kegagalan yang sudah berulang di repo ini."""
        import ast
        import inspect
        from textwrap import dedent

        from aruna import app as modul

        pohon = ast.parse(
            dedent(inspect.getsource(modul.ArunaApplication._start_health_monitor))
        )
        dibangun = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "UkuranDatabaseCheck" in dibangun

    def test_ambangnya_dari_setelan_bukan_angka_tertanam(self) -> None:
        """Ambang yang ditanam di lapisan perangkaian tidak bisa diubah
        operator tanpa menyunting kode."""
        import ast
        import inspect
        from textwrap import dedent

        from aruna import app as modul

        pohon = ast.parse(
            dedent(inspect.getsource(modul.ArunaApplication._start_health_monitor))
        )
        for n in ast.walk(pohon):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                continue
            if n.func.id != "UkuranDatabaseCheck":
                continue
            for k in n.keywords:
                assert not isinstance(k.value, ast.Constant), k.arg
