"""Gerak pasar sesudah ARUNA memilih diam (PASAL 14.32, 14.33).

:mod:`aruna.decision.silence` sudah bisa menilai sekumpulan keputusan NO SIGNAL
sejak lama, dan tidak pernah dipanggil sekali pun - karena bahannya tidak ada.
``Diam.move_pct`` menuntut harga sesudah horizon lewat, dan signal yang ditahan
**tidak pernah diresolusi**: mesin outcome hanya menyentuh yang terbit. Modul
ini yang menyusun bahan itu.

**Yang berbahaya di sini bukan angka yang hilang, melainkan angka yang salah.**
"Diam ARUNA benar 90%" adalah kalimat yang sangat meyakinkan dan sangat sulit
dibantah, dan PASAL 14.33 justru melarang memakainya untuk menurunkan ambang.
Karena itu tiap jalur yang tidak bisa dihitung memulangkan ``None`` - BELUM BISA
DINILAI - dan bukan nol, dan barisnya **tetap muncul di daftar**. Membuangnya
akan mengecilkan penyebut, dan akurasi diam naik setiap kali ada yang tidak bisa
diukur - persis arah kesalahan yang paling menguntungkan ARUNA.

**Gerak terjauh, bukan gerak terakhir.** Pasar yang naik delapan persen lalu
kembali ke titik awal menawarkan kesempatan yang sungguhan; menilainya dari
harga penutup akan melaporkan diam yang benar atas gerakan yang jelas ada.

**Intervalnya tidak disaring, dan itu disengaja.** Ekstrem dari bar 1m dan bar
15m atas periode yang sama menghasilkan angka yang sama - bar besar hanya
merangkum bar kecil - jadi mengambil semuanya memberi jawaban yang benar apa pun
interval yang kebetulan tersimpan untuk aset itu. Menyaring satu interval berarti
aset yang tidak punya interval itu diam-diam menghasilkan "tidak terukur".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.db.types import to_mysql_datetime
from aruna.decision.silence import Diam

#: Skala persen yang dilaporkan. Dua angka di belakang koma sudah jauh lebih
#: halus daripada ambang dua persen yang menilainya.
_SKALA = Decimal("0.01")


def _desimal(nilai: Any) -> Decimal | None:
    if nilai is None:
        return None
    try:
        return Decimal(str(nilai))
    except Exception:  # noqa: BLE001 - baris rusak tidak menjatuhkan laporan
        return None


def _gerak(
    acuan: Any, tertinggi: Any, terendah: Any
) -> Decimal | None:
    """Gerak terjauh dari harga acuan, bertanda. ``None`` kalau tak terukur."""
    ref = _desimal(acuan)
    atas = _desimal(tertinggi)
    bawah = _desimal(terendah)
    if ref is None or atas is None or bawah is None:
        return None
    if ref <= 0:
        # Harga nol atau negatif tidak mungkin, dan pembagiannya menjatuhkan
        # seluruh laporan harian - bukan cuma satu barisnya.
        return None
    naik = (atas - ref) / ref * 100
    turun = (bawah - ref) / ref * 100
    terjauh = naik if abs(naik) >= abs(turun) else turun
    return terjauh.quantize(_SKALA)


class DiamRepository:
    """Membaca keputusan NO SIGNAL beserta gerak sesudahnya. Tidak menulis."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def diam(
        self, *, start: datetime, end: datetime, now: datetime
    ) -> tuple[Diam, ...]:
        """Keputusan yang ditahan pada jendela ini, beserta gerak pasarnya.

        ``now`` diberikan pemanggil dan tidak dibaca dari jam sistem: yang
        menentukan sebuah horizon sudah lewat atau belum adalah waktu yang sama
        dengan yang dipakai seluruh laporan itu, bukan waktu saat baris ini
        kebetulan dieksekusi.
        """
        rows = await self._db.fetch(
            """
            SELECT g.signal_id            AS signal_id,
                   g.symbol               AS symbol,
                   g.withheld_reason      AS withheld_reason,
                   g.resolves_at          AS resolves_at,
                   s.reference_price      AS reference_price,
                   MAX(c.high)            AS tertinggi,
                   MIN(c.low)             AS terendah,
                   COUNT(c.id)            AS bar
            FROM signals g
            JOIN signal_snapshots s ON s.signal_id = g.signal_id
            LEFT JOIN candles c
                   ON c.asset_id = g.asset_id
                  AND c.is_closed = TRUE
                  AND c.open_time >= g.locked_at
                  AND c.close_time <= g.resolves_at
            WHERE g.published = FALSE
              AND g.locked_at >= %s AND g.locked_at < %s
            GROUP BY g.signal_id, g.symbol, g.withheld_reason,
                     g.resolves_at, s.reference_price
            ORDER BY g.locked_at
            """,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        )

        keluar: list[Diam] = []
        for row in rows or ():
            selesai = row.get("resolves_at")
            lewat = selesai is not None and _sudah_lewat(selesai, now)
            gerak = (
                _gerak(
                    row.get("reference_price"),
                    row.get("tertinggi"),
                    row.get("terendah"),
                )
                if lewat
                else None
            )
            keluar.append(
                Diam(
                    symbol=str(row.get("symbol") or ""),
                    reason=str(row.get("withheld_reason") or ""),
                    move_pct=gerak,
                )
            )
        return tuple(keluar)


def _sudah_lewat(selesai: Any, now: datetime) -> bool:
    """Apakah horizonnya sudah habis pada ``now``.

    MySQL memulangkan ``datetime`` tanpa zona; ``now`` membawa UTC. Menyandingkan
    keduanya apa adanya melempar ``TypeError`` dan menjatuhkan laporan harian,
    jadi yang polos dibaca sebagai UTC - sama dengan cara seluruh sistem ini
    menyimpannya.
    """
    if not isinstance(selesai, datetime):
        return False
    if selesai.tzinfo is None:
        selesai = selesai.replace(tzinfo=now.tzinfo)
    return selesai <= now


__all__ = ["DiamRepository"]
