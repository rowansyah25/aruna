"""Pengirim laporan harian: sekali sehari, pukul 00:00 WIB (PASAL 2).

Dua hal yang mudah salah, dan keduanya ditutup di sini.

**Harinya hari operator, bukan hari UTC.** ARUNA berjalan dengan jam UTC di
dalam - itu benar dan tidak diubah - tapi "pukul 00:00" di spec adalah tengah
malam di tempat operator berada. Jendela laporan karena itu dihitung dari
tengah malam WIB ke tengah malam WIB. Kalau tidak, laporan "18 Agustus" berisi
tujuh jam terakhir tanggal 17 dan berhenti pukul lima sore.

**Sekali, dan tetap sekali sesudah restart.** Penanda "sudah terkirim" yang
hanya ada di memori akan hilang setiap kali proses mati - dan penjaga proses
memang membuat proses ini mati lalu hidup lagi. Sebuah restart pukul 00:05
akan mengirim laporan kedua untuk tanggal yang sama. Jadi tanggalnya disimpan
di ``app_state``, tempat yang sama yang membuat ``/kill`` bertahan.

**Kalau laporannya gagal terkirim, tanggalnya tidak dicatat.** Menstempel
duluan berarti satu kegagalan jaringan menghapus laporan hari itu selamanya,
tanpa jejak. Menstempel setelah berhasil berarti percobaan berikutnya - tick
lima belas detik kemudian - mencoba lagi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aruna.core.clock import JAKARTA
from aruna.core.logging import get_logger
from aruna.notify.daily import Component, DailyReport, render_daily
from aruna.signals.stabilitas import hitung_pembalikan

log = get_logger("aruna.notify.daily")

#: Satu sumber, lihat :data:`~aruna.core.clock.JAKARTA`.
WIB = JAKARTA

#: Kunci di ``app_state``. Nilainya ``{"date": "YYYY-MM-DD"}``.
DAILY_SENT_KEY = "daily_report_sent"

#: Komponen yang dilaporkan, dan urutannya (PASAL 10). Kunci kiri adalah nama
#: komponen health ARUNA; label dan ikon di kanan mengikuti template operator.
#:
#: Dipetakan secara eksplisit dan tidak diambil apa adanya dari health sweep,
#: karena health sweep memuat komponen internal yang tidak diminta - dan
#: laporan yang isinya berubah setiap kali ada check baru ditambahkan bukan
#: laporan berformat tetap.
COMPONENT_MAP: tuple[tuple[str, str, str], ...] = (
    ("server", "🖥", "Server"),
    ("provider:binance-spot", "🔵", "Binance REST"),
    ("provider:yahoo", "📈", "IDX API"),
    ("news", "📰", "News API"),
    ("upkeep", "🧠", "AI Engine"),
    ("database", "🗄", "Database"),
    ("telegram", "📱", "Telegram"),
)


def day_window(moment: datetime) -> tuple[datetime, datetime]:
    """Jendela hari **yang baru saja selesai**, batasnya tengah malam WIB.

    Dipanggil tepat sesudah tengah malam, jadi yang dilaporkan adalah kemarin -
    hari yang sudah lengkap. Laporan pukul 00:00 yang berisi hari ini akan
    selalu berisi nol menit pertama hari itu.
    """
    tengah_malam = moment.astimezone(WIB).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tengah_malam - timedelta(days=1), tengah_malam


def _duration(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes:02d}m"


@dataclass(slots=True)
class DailyReportService:
    """Menyusun dan mengirim laporan harian."""

    repo: Any
    sender: Any
    state: Any = None
    health: Any = None
    #: Repositori akurasi diam (PASAL 14.32/14.33). Opsional: pemanggil yang
    #: tidak menyediakannya menghasilkan laporan tanpa bagian itu, bukan
    #: laporan yang gagal.
    diam_repo: Any = None
    #: Repositori ingatan pasar (PASAL 15.43). Opsional dengan alasan yang sama
    #: seperti ``diam_repo`` di atas.
    memory_repo: Any = None
    model_version: str = "ARUNA v1.0"
    uptime_seconds: Any = None
    #: Cache di depan ``app_state``, supaya tick biasa tidak menyentuh database.
    _last_date: str | None = None

    # -- kapan --------------------------------------------------------------

    def _can_send(self) -> bool:
        """Apakah ada tujuan pengiriman sekarang?

        Pengirim yang tidak menyediakan ``ready`` dianggap selalu siap - itu
        bentuk pengirim biasa, dan test double tidak perlu ikut menirunya.
        """
        ready = getattr(self.sender, "ready", None)
        return True if ready is None else bool(ready())

    async def due(self, now: datetime) -> bool:
        """Sudah lewat tengah malam WIB, dan hari ini belum dikirim?

        Tidak ada syarat "tepat pukul 00:00". Loop berdetak tiap lima belas
        detik dan bisa saja terlewat - proses restart, tick yang lambat, mesin
        yang tidur. Syaratnya "hari ini belum dikirim", jadi laporan yang
        terlambat tetap terkirim, dan tetap sekali.
        """
        if not self._can_send():
            # Tanpa bot Telegram tidak ada yang menerima laporannya. Ini
            # dibedakan dari gagal kirim: gagal kirim dicatat sebagai peringatan
            # dan dicoba lagi, sementara "tidak ada tujuan" akan mencetak
            # peringatan itu tiap lima belas detik selamanya.
            return False
        hari_ini = now.astimezone(WIB).date().isoformat()
        if self._last_date == hari_ini:
            return False
        if self.state is not None and self._last_date is None:
            simpan = await self.state.get(DAILY_SENT_KEY)
            if simpan and simpan.get("date"):
                self._last_date = str(simpan["date"])
                if self._last_date == hari_ini:
                    return False
        return True

    # -- apa ----------------------------------------------------------------

    async def build(self, now: datetime) -> DailyReport:
        awal, akhir = day_window(now)

        futures = await self.repo.futures(start=awal, end=akhir)
        spot = await self.repo.spot_or_equity(
            market_code="CRYPTO", title="SPOT", icon="💰", start=awal, end=akhir
        )
        saham = await self.repo.spot_or_equity(
            market_code="IDX", title="SAHAM INDONESIA", icon="📈",
            start=awal, end=akhir,
        )
        return DailyReport(
            date=awal,
            markets=(futures, spot, saham),
            silence=await self._silence(awal, akhir, now),
            # PASAL 15.43. Tanpa baris ini `IngatanHarian` dirender dengan
            # benar oleh `render_daily`, testnya hijau, dan bloknya **tidak
            # pernah muncul** - karena tidak ada yang mengisinya.
            memory=await self._memory_harian(awal, akhir),
            # Bagian 18.52. Tanpa baris ini `_pembalikan_lines` dirender dengan
            # benar oleh `render_daily`, testnya hijau, dan bloknya TIDAK
            # PERNAH MUNCUL - persis catatan `memory` di atas, yang ditulis
            # sesudah cacat yang sama terjadi.
            pembalikan=await self._pembalikan(awal, akhir),
            agents=await self.repo.agents(),
            council=await self.repo.council(start=awal, end=akhir),
            correction=await self.repo.correction(
                start=awal, end=akhir, model_version=self.model_version
            ),
            components=self._components(),
            uptime=(
                _duration(self.uptime_seconds())
                if callable(self.uptime_seconds)
                else "-"
            ),
        )

    async def _pembalikan(self, awal: datetime, akhir: datetime):
        """Pembalikan keputusan hari itu (bagian 18.52), atau ``None``.

        ``None`` berarti belum terhitung - dibedakan dari ``(0, 0)``, yang
        berarti sudah dihitung dan memang tidak ada pembalikan. Blok yang
        mencetak "PEMBALIKAN: 0" dari ketiadaan hitungan terbaca persis seperti
        ARUNA yang tidak pernah berubah pikiran.

        Kegagalannya tidak menjatuhkan laporan: satu blok yang hilang jauh
        lebih murah daripada laporan harian yang tidak terkirim sama sekali.
        """
        try:
            baris = await self.repo.pembalikan(start=awal, end=akhir)
        except Exception:
            log.exception("daily.pembalikan_gagal")
            return None
        return hitung_pembalikan(baris)

    async def _memory_harian(self, awal: datetime, akhir: datetime):
        """Keadaan ingatan pasar hari itu (PASAL 15.43), atau ``None``.

        **Gagal menjadi None, bukan menjadi nol** - alasan yang sama persis
        dengan :meth:`_silence`: "0 ingatan" yang lahir dari kueri gagal
        terbaca seperti ARUNA yang tidak pernah mengingat apa pun, dan itu
        kesimpulan yang jauh lebih dramatis daripada kegagalannya sendiri.
        """
        repo = getattr(self, "memory_repo", None)
        if repo is None:
            return None
        try:
            return await repo.ringkas_harian(since=awal, until=akhir)
        except Exception:
            log.exception("daily.memory_failed")
            return None

    async def _silence(self, awal: datetime, akhir: datetime, now: datetime):
        """Akurasi diam hari itu (PASAL 14.32, 14.33), atau ``None``.

        **Gagal menjadi None, bukan menjadi nol.** Sebuah "AKURASI NO SIGNAL:
        0%" yang lahir dari kueri yang gagal terbaca persis seperti ARUNA yang
        selalu salah diam - dan itu kesimpulan yang jauh lebih dramatis
        daripada kegagalannya sendiri. Bagian yang hilang lebih jujur daripada
        bagian yang berbohong.

        Repositorinya opsional: pemanggil lama yang tidak menyediakannya
        menghasilkan laporan tanpa bagian ini, bukan laporan yang gagal.
        """
        repo = getattr(self, "diam_repo", None)
        if repo is None:
            return None
        try:
            from aruna.decision.silence import evaluate

            return evaluate(await repo.diam(start=awal, end=akhir, now=now))
        except Exception:
            log.exception("daily.silence_failed")
            return None

    def _components(self) -> tuple[Component, ...]:
        """Snapshot status, dari health sweep terakhir (PASAL 10).

        Komponen yang tidak ada di sweep dilaporkan sebagai ``UNKNOWN`` dan
        **tidak** hijau. Hijau berarti "sudah diperiksa dan sehat"; memberi
        hijau pada sesuatu yang tidak diperiksa adalah kebohongan yang paling
        mudah dipercaya, karena pembaca memang berharap hijau.
        """
        report = self.health() if callable(self.health) else self.health
        by_name = {}
        if report is not None:
            by_name = {c.name: c for c in getattr(report, "components", ())}

        out: list[Component] = []
        for name, icon, label in COMPONENT_MAP:
            component = by_name.get(name)
            if component is None:
                out.append(Component(label, icon, "UNKNOWN", healthy=False))
                continue
            status = component.status
            out.append(
                Component(
                    label,
                    icon,
                    "HEALTHY" if status.is_operational else status.value,
                    healthy=bool(status.is_operational),
                )
            )
        return tuple(out)

    # -- kirim --------------------------------------------------------------

    async def run(self, now: datetime) -> bool:
        """Kirim kalau memang waktunya. Mengembalikan True kalau terkirim."""
        if not await self.due(now):
            return False

        report = await self.build(now)
        terkirim = await self.sender.send(render_daily(report))
        if not terkirim:
            # Sengaja TIDAK distempel. Menstempel duluan akan menghapus laporan
            # hari itu selamanya karena satu kegagalan jaringan, dan tidak akan
            # ada yang tahu - laporan yang hilang tidak meninggalkan jejak.
            log.warning("daily.undelivered")
            return False

        hari_ini = now.astimezone(WIB).date().isoformat()
        self._last_date = hari_ini
        if self.state is not None:
            await self.state.set(
                DAILY_SENT_KEY, {"date": hari_ini}, actor="aruna-daily"
            )
        log.info(
            "daily.sent",
            date=hari_ini,
            total=report.overall.total,
            win=report.overall.win,
            loss=report.overall.loss,
        )
        return True


__all__ = [
    "COMPONENT_MAP",
    "DAILY_SENT_KEY",
    "WIB",
    "DailyReportService",
    "day_window",
]
