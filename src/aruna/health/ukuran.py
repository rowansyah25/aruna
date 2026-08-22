"""Berapa lama lagi basis data ini muat (bagian 27-28 spec).

Sampai 2026-08-21 tidak ada satu pun yang mengawasi pertumbuhan. Audit hari itu
menemukan basis data 506 MB yang bertambah 69.048 baris snapshot sehari, tanpa
retention dan tanpa peringatan - dan satu-satunya yang membuatnya terlihat
adalah seseorang kebetulan mengetik kueri `information_schema`.

**Komponen sendiri, bukan tempelan pada `DatabaseCheck`.** Pertanyaannya
berbeda: yang itu menjawab "bisa dihubungi?", yang ini "muat berapa lama
lagi?". Peringatan pertumbuhan yang muncul sebagai masalah koneksi akan
disalahbaca, dan sebaliknya - dan keduanya punya cadence yang berbeda jauh.
"""

from __future__ import annotations

from typing import Any, Protocol

from aruna.core.clock import monotonic
from aruna.core.enums import HealthStatus
from aruna.core.errors import DatabaseError
from aruna.health.models import ComponentHealth

__all__ = ["JEDA_UKUR_DETIK", "KRITIS_MB", "PERINGATAN_MB", "UkuranDatabaseCheck"]


#: Jarak antar pengukuran sesungguhnya, dalam detik.
#:
#: Satu jam. Health menyapu tiap tiga puluh detik dan kueri di sini memindai
#: metadata seluruh 52 tabel; menjalankannya tiap sapuan berarti membayar
#: pemindaian itu 2.880 kali sehari untuk angka yang bergerak dalam satuan jam.
JEDA_UKUR_DETIK = 3600.0

#: Ambang bawaan, dalam MB.
#:
#: Dasar angkanya: sesudah gerbang perubahan dan retensi, proyeksi mantap
#: ARUNA ada di sekitar 400-500 MB. Dua kali lipatnya berarti sesuatu tumbuh
#: dengan cara yang tidak dirancang - dan itulah yang layak dilaporkan, bukan
#: ukuran mutlak yang kebetulan besar.
PERINGATAN_MB = 1_000.0
KRITIS_MB = 2_000.0


class _DB(Protocol):
    is_connected: bool

    async def fetchrow(self, sql: str, *args: Any) -> Any: ...


class UkuranDatabaseCheck:
    """Ukuran total dan tabel terbesarnya, diukur sejam sekali."""

    name = "database_size"

    def __init__(
        self,
        db: _DB,
        *,
        peringatan_mb: float = PERINGATAN_MB,
        kritis_mb: float = KRITIS_MB,
    ) -> None:
        self._db = db
        self._peringatan = peringatan_mb
        self._kritis = kritis_mb
        self._terakhir: float | None = None
        self._simpanan: ComponentHealth | None = None

    async def check(self, *, sekarang: float | None = None) -> ComponentHealth:
        saat = monotonic() if sekarang is None else sekarang

        # Koneksi yang mati sudah diteriakkan `DatabaseCheck`. Meneriakkannya
        # dua kali membuat satu kegagalan tampak seperti dua, dan operator
        # belajar mengabaikan yang kedua.
        if not self._db.is_connected:
            return self._tak_diketahui("pool basis data belum terbuka")

        if (
            self._simpanan is not None
            and self._terakhir is not None
            and saat - self._terakhir < JEDA_UKUR_DETIK
        ):
            return self._simpanan

        try:
            total = await self._db.fetchrow(
                "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 1) mb "
                "FROM information_schema.TABLES WHERE table_schema = DATABASE()"
            )
            besar = await self._db.fetchrow(
                "SELECT table_name t, "
                "       ROUND((data_length + index_length) / 1024 / 1024, 1) mb "
                "FROM information_schema.TABLES WHERE table_schema = DATABASE() "
                "ORDER BY (data_length + index_length) DESC LIMIT 1"
            )
        except DatabaseError as exc:
            return self._tak_diketahui(str(exc))

        self._terakhir = saat
        self._simpanan = self._nilai(
            total_mb=float(total["mb"] or 0.0),
            terbesar=str(besar["t"]) if besar else "",
            terbesar_mb=float(besar["mb"] or 0.0) if besar else 0.0,
        )
        return self._simpanan

    def _nilai(
        self, *, total_mb: float, terbesar: str, terbesar_mb: float
    ) -> ComponentHealth:
        details = {
            "total_mb": total_mb,
            "terbesar": terbesar,
            "terbesar_mb": terbesar_mb,
            "peringatan_mb": self._peringatan,
            "kritis_mb": self._kritis,
        }
        # Tabel terbesarnya ikut disebut di pesannya, bukan hanya di details:
        # peringatan yang cuma bilang "basis data besar" menyuruh operator
        # mengulang seluruh audit dari nol untuk mencari tahu apa yang tumbuh.
        asal = f" ({terbesar} {terbesar_mb:,.1f} MB)" if terbesar else ""

        if total_mb >= self._kritis:
            # DOWN, bukan DEGRADED: basis data yang penuh menghentikan seluruh
            # sistem, dan peringatan yang terlalu lembut akan diabaikan sampai
            # itu terjadi.
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"basis data {total_mb:,.1f} MB, melewati ambang kritis "
                    f"{self._kritis:,.0f} MB{asal}"
                ),
                details=details,
            )
        if total_mb >= self._peringatan:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                message=(
                    f"basis data {total_mb:,.1f} MB, melewati ambang "
                    f"{self._peringatan:,.0f} MB{asal}"
                ),
                details=details,
            )
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UP,
            message=f"{total_mb:,.1f} MB{asal}",
            details=details,
        )

    def _tak_diketahui(self, pesan: str) -> ComponentHealth:
        """UNKNOWN, bukan DOWN.

        Tidak bisa mengukur bukan berarti terlalu besar, dan melaporkannya
        sebagai DOWN akan menarik seluruh laporan health ke bawah karena
        sebuah kueri metadata gagal.
        """
        return ComponentHealth(
            name=self.name, status=HealthStatus.UNKNOWN, message=pesan
        )
