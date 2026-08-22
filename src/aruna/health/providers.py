"""Health probes for market data providers (SPEC 5).

A provider is DOWN when unreachable and DEGRADED when it answers but its data
cannot be trusted - a stalled feed that keeps returning 200 is the failure mode
that silently poisons everything downstream, so it must not read as healthy.
"""

from __future__ import annotations

import asyncio

from aruna.core.enums import HealthStatus
from aruna.data.ingest import MarketIngestor
from aruna.health.models import ComponentHealth

#: Porsi observasi yang ditolak, pada sapuan terakhir, sebelum vonisnya
#: DEGRADED.
#:
#: Separuh. Alasannya sama dengan ambang aliran WebSocket: satu observasi gagal
#: dari sebelas adalah fakta tentang pasar yang sedang buka-tutup, sementara
#: sebelas dari sebelas adalah fakta tentang feed yang rusak. Menyamakan
#: keduanya membuat operator belajar mengabaikan warnanya.
#:
#: Yang di bawah ambang tetap DISEBUTKAN di pesannya - diredam vonisnya, bukan
#: disembunyikan angkanya.
DEGRADED_REJECTION_SHARE = 0.5


class ProviderCheck:
    """One market data provider."""

    def __init__(self, ingestor: MarketIngestor, *, timeout: float = 15.0) -> None:
        self._ingestor = ingestor
        self._timeout = timeout
        self.name = f"provider:{ingestor.provider.name}"
        #: Penghitung penolakan pada sapuan sebelumnya. Lihat :meth:`_terkini`.
        self._sebelumnya: dict[str, int] = {}

    def _terkini(self, total: dict[str, int]) -> tuple[int, int]:
        """Penolakan **sejak sapuan terakhir**, bukan sejak proses menyala.

        Mengembalikan (ditolak, seluruh observasi) untuk jendela itu.

        **Cacat yang memicu metode ini.** ``QualityGate.rejection_counts()``
        menjumlahkan seluruh penolakan seumur proses dan tidak pernah direset;
        pemeriksa ini memperlakukan angka bukan-nol apa pun sebagai "sedang
        bermasalah". Akibatnya sebelas observasi yang gagal SATU KALI membuat
        ``provider:yahoo`` DEGRADED selamanya - terukur: proses yang berjalan
        melaporkan "11 observation(s) failed quality checks" berjam-jam
        sesudahnya, sementara proses baru pada menit yang sama melaporkan UP
        dengan nol penolakan.

        Data IDX-nya sendiri tidak pernah berhenti mengalir. Yang rusak hanya
        laporannya - dan pemeriksa kesehatan yang tidak bisa pulih mengajari
        pembacanya mengabaikan warnanya, yang membuat peringatan berikutnya
        ikut terabaikan. Keluarga cacat yang sama dengan aliran WebSocket.

        Totalnya tetap dilaporkan di ``details``: "berapa sejak nyala" adalah
        angka yang sah, ia hanya bukan jawaban atas "apakah sekarang sehat".
        """
        delta = {
            kunci: total.get(kunci, 0) - self._sebelumnya.get(kunci, 0)
            for kunci in set(total) | set(self._sebelumnya)
        }
        self._sebelumnya = dict(total)
        # Selisih negatif berarti penghitungnya direset di bawah sana -
        # diabaikan, bukan dijadikan angka negatif yang merusak porsinya.
        naik = {k: v for k, v in delta.items() if v > 0}
        ditolak = sum(v for k, v in naik.items() if k != "OK")
        return ditolak, sum(naik.values())

    async def check(self) -> ComponentHealth:
        provider = self._ingestor.provider
        capabilities = provider.capabilities
        details = capabilities.describe()

        try:
            async with asyncio.timeout(self._timeout):
                status = await provider.status()
        except TimeoutError:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"no response within {self._timeout:.0f}s",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001 - a probe must never propagate
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"{type(exc).__name__}: {exc}",
                details=details,
            )

        if not status.reachable:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=status.detail or "unreachable",
                details=details,
            )

        gate = self._ingestor.gate
        rejections = gate.rejection_counts()
        skew = gate.observed_clock_skew_sec()
        details |= {
            "quality_rejections": rejections,
            "clock_skew_sec": round(skew, 1) if skew is not None else None,
        }

        message = status.detail or "reachable"
        if not capabilities.is_realtime:
            # Stated on every sweep so nobody reads this component as live.
            message += f" (DELAYED ~{capabilities.expected_delay_sec // 60}m)"

        ditolak, diamati = self._terkini(rejections)
        details["rejected_since_last_check"] = ditolak
        details["observed_since_last_check"] = diamati

        status_value = HealthStatus.UP
        if diamati and ditolak:
            porsi = ditolak / diamati
            if porsi >= DEGRADED_REJECTION_SHARE:
                status_value = HealthStatus.DEGRADED
                message += (
                    f"; {ditolak} dari {diamati} observasi terakhir gagal "
                    f"quality check ({porsi:.0%})"
                )
            else:
                # Dikatakan, tapi tidak dijadikan vonis. Satu observasi gagal
                # dari sebelas adalah hal biasa saat pasar buka-tutup; membuat
                # itu DEGRADED akan menyalakan lampu merah tiap pagi sampai
                # tidak ada yang melihatnya lagi.
                message += f"; {ditolak} dari {diamati} observasi terakhir ditolak"

        return ComponentHealth(
            name=self.name,
            status=status_value,
            message=message,
            latency_ms=status.latency_ms,
            details=details,
        )


__all__ = ["ProviderCheck"]
