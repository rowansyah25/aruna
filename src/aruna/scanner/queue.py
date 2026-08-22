"""Antrean antara pemindai dan AI (PASAL 38, 39).

Dua kecepatan yang tidak boleh saling menyandera. Data pasar tiba dalam
milidetik; satu sesi council makan detik. Menyambungkan keduanya langsung
berarti AI yang lambat menahan pembacaan data - dan yang mati bukan analisisnya,
melainkan koneksinya. PASAL 38 memisahkan keduanya, dan antrean ini adalah
sekatnya.

Antrean yang tidak berbatas bukan sekat, ia cuma penundaan: ia menerima
segalanya, tumbuh, lalu mati kehabisan memori pada saat pasar paling ramai -
tepat ketika ia paling dibutuhkan. Jadi antrean ini **berbatas**, dan yang
terjadi saat penuh dinyatakan, bukan diserahkan pada nasib.

Tiga aturan yang membentuknya, dan ketiganya PASAL 39:

**Penggabungan sebelum pembuangan.** Satu simbol yang menembus level tiga kali
dalam sepuluh detik adalah satu keadaan, bukan tiga. Yang lama ditimpa yang
baru pada kunci ``(symbol, kind)`` yang sama, jadi kedalaman antrean mengukur
berapa banyak KEADAAN yang menunggu, bukan berapa banyak pesan yang lewat.

**Yang terbaru diprioritaskan.** Kalau tetap harus ada yang dibuang, yang
dibuang adalah yang paling tua. Harga sepuluh detik lalu yang belum sempat
dianalisis tidak akan menjadi lebih berguna dengan menunggu; ia hanya akan
menjadi lebih salah.

**Yang dibuang dihitung.** Antrean yang membuang diam-diam membuat sistem
terlihat seperti pasar yang sepi. Angka buangannya adalah satu-satunya cara
operator tahu bahwa ambangnya terlalu longgar atau AI-nya terlalu lambat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aruna.core.logging import get_logger
from aruna.scanner.events import EventKind, SignificantEvent

log = get_logger("aruna.scanner.queue")

#: Berapa keadaan yang boleh menunggu analisis. Dihitung dari keadaan, bukan
#: pesan, karena penggabungan sudah menyatukan pesan berulang: lima aset kali
#: lima jenis peristiwa sudah 25, jadi angka ini memberi ruang beberapa kali
#: lipat sebelum apa pun dibuang.
DEFAULT_MAX_DEPTH = 100


@dataclass(slots=True)
class QueueStats:
    """Apa yang terjadi pada antrean, dalam angka yang bisa dibantah."""

    accepted: int = 0
    coalesced: int = 0
    dropped_full: int = 0
    delivered: int = 0
    #: Kedalaman tertinggi yang pernah tercapai. Kedalaman sekarang bisa nol
    #: justru sesudah insiden, jadi puncaknya yang memberi tahu apakah sekatnya
    #: pernah tertekan.
    peak_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "coalesced": self.coalesced,
            "dropped_full": self.dropped_full,
            "delivered": self.delivered,
            "peak_depth": self.peak_depth,
        }

    def summary(self) -> str:
        parts = [
            f"{self.accepted} diterima",
            f"{self.coalesced} digabung",
            f"{self.delivered} diteruskan",
        ]
        if self.dropped_full:
            parts.append(
                f"{self.dropped_full} dibuang karena antrean penuh - "
                "ambang terlalu longgar atau analisis terlalu lambat"
            )
        parts.append(f"puncak kedalaman {self.peak_depth}")
        return ", ".join(parts)


class AnalysisQueue:
    """Berbatas, menggabungkan, dan mengaku saat membuang.

    Bukan :class:`asyncio.Queue`: yang dibutuhkan di sini bukan FIFO melainkan
    sebuah PETA keadaan terkini per ``(symbol, kind)``. Antrean FIFO akan
    mengantre tiga breakout dari satu simbol sebagai tiga pekerjaan, lalu
    menganalisis harga yang sudah dua kali digantikan.
    """

    def __init__(self, *, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        if max_depth < 1:
            raise ValueError("max_depth harus minimal 1")
        self._max_depth = max_depth
        self._pending: dict[tuple[str, EventKind], SignificantEvent] = {}
        self.stats = QueueStats()

    def __len__(self) -> int:
        return len(self._pending)

    @property
    def max_depth(self) -> int:
        return self._max_depth

    def offer(self, event: SignificantEvent) -> bool:
        """Tawarkan satu peristiwa. ``False`` berarti dibuang.

        Penggabungan diperiksa SEBELUM batas kedalaman, karena menggantikan
        keadaan yang sudah antre tidak menambah beban apa pun - menolaknya
        justru akan membuat antrean penuh berisi harga basi sementara harga
        terbaru dibuang.
        """
        key = (event.symbol, event.kind)
        existing = self._pending.get(key)
        if existing is not None:
            if event.at < existing.at:
                # Datang terlambat dan lebih tua. Diabaikan, bukan menimpa -
                # menimpanya akan memundurkan keadaan.
                return False
            self._pending[key] = event
            self.stats.coalesced += 1
            return True

        if len(self._pending) >= self._max_depth:
            oldest_key = min(self._pending, key=lambda k: self._pending[k].at)
            if self._pending[oldest_key].at >= event.at:
                # Yang menunggu semuanya lebih baru. Yang datang inilah yang
                # paling tua, jadi ia yang dibuang.
                self.stats.dropped_full += 1
                self._warn_full(event)
                return False
            dropped = self._pending.pop(oldest_key)
            self.stats.dropped_full += 1
            self._warn_full(dropped)

        self._pending[key] = event
        self.stats.accepted += 1
        self.stats.peak_depth = max(self.stats.peak_depth, len(self._pending))
        return True

    def _warn_full(self, dropped: SignificantEvent) -> None:
        """``dropped`` adalah yang BENAR-BENAR dibuang, bukan yang baru datang.

        Versi pertama selalu mencatat peristiwa yang tiba, padahal pada jalur
        yang umum justru yang lama yang dibuang - jadi operator membaca nama
        simbol yang sebenarnya diterima. Menyebut korban yang salah pada baris
        yang seluruh gunanya adalah menyebut korban (SPEC 49).
        """
        log.warning(
            "scanner.queue_full",
            depth=len(self._pending),
            max_depth=self._max_depth,
            symbol=dropped.symbol,
            kind=dropped.kind.value,
            dropped_total=self.stats.dropped_full,
            detail=(
                "analysis cannot keep up with the scanner; the oldest state is "
                "discarded because a stale price does not improve by waiting"
            ),
        )

    def drain(self, limit: int | None = None) -> list[SignificantEvent]:
        """Ambil pekerjaan berikutnya, paling mendesak dulu.

        Urutannya severity menurun, lalu yang terbaru - dua sifat berbeda yang
        keduanya penting: severity memilih apa yang paling layak dianalisis,
        dan waktu memutuskan seri di antara yang sama layaknya.
        """
        if not self._pending:
            return []
        ordered = sorted(
            self._pending.values(), key=lambda e: (-e.severity, -e.at.timestamp())
        )
        taken = ordered if limit is None else ordered[:limit]
        for event in taken:
            self._pending.pop((event.symbol, event.kind), None)
        self.stats.delivered += len(taken)
        return taken

    def state(self) -> dict[str, Any]:
        return {
            "depth": len(self._pending),
            "max_depth": self._max_depth,
            **self.stats.to_dict(),
        }


__all__ = ["DEFAULT_MAX_DEPTH", "AnalysisQueue", "QueueStats"]
