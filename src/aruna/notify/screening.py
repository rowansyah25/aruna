"""Screening pra-pembukaan IDX (diminta operator).

Tiga puluh menit sebelum bel, ARUNA memindai seluruh saham IDX dan mengabarkan
apa yang berubah sejak penutupan kemarin.

**Ini bukan ramalan, dan pesannya mengatakannya.** Operator memintanya "untuk
tahu apa yang akan terjadi di market". Yang bisa diberikan ARUNA bukan itu -
tidak ada yang tahu arah pembukaan, dan PASAL 51 melarang mengklaimnya. Yang
bisa diberikan adalah apa yang **sudah** terjadi selagi bursa tutup: bar
penutupan kemarin dibandingkan dengan garis dasarnya sendiri, dan berita yang
terbit semalam.

Perbedaan itu bukan kehati-hatian berlebihan. Sebuah daftar yang berjudul "apa
yang akan terjadi" akan dibaca sebagai prediksi bahkan ketika isinya identik,
dan pembacanya akan bertindak atas dasar yang tidak pernah dijanjikan siapa pun.

**Yang diam ikut disebut.** Daftar yang hanya memuat yang bergerak tidak bisa
dibedakan dari pemindaian yang setengah gagal - dan "tidak ada yang bergerak"
adalah kabar, bukan ketiadaan kabar (SPEC 4).

Sekali per hari bursa, ditandai di ``app_state`` supaya restart di tengah
jendela tidak mengirimnya dua kali.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aruna.core.logging import get_logger

log = get_logger("aruna.notify.screening")

WIB = ZoneInfo("Asia/Jakarta")

#: Kunci di ``app_state``. Nilainya ``{"date": "YYYY-MM-DD"}``.
SCREENING_SENT_KEY = "idx_screening_sent"

#: Paling banyak sekian simbol yang bergerak ditampilkan penuh. Sisanya
#: disebut jumlahnya - daftar yang lebih panjang dari satu layar tidak dibaca
#: lebih banyak, hanya digulir lebih cepat.
MAX_SIMBOL = 8

#: Paling banyak sekian berita. Berita pra-pembukaan yang panjang mendorong
#: bagian harga keluar layar, dan bagian harga yang terukur.
MAX_BERITA = 5


def render_screening(
    hasil: list[Any],
    berita: list[Any] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """Satu pesan: apa yang berubah selagi bursa tutup."""
    from aruna.core.clock import wib

    bergerak = [r for r in hasil if getattr(r, "events", ())]
    diam = [r for r in hasil if getattr(r, "scanned", True) and not r.events]
    gagal = [r for r in hasil if not getattr(r, "scanned", True)]

    lines = ["🔎 ARUNA - SCREENING PRA-PEMBUKAAN IDX", ""]
    if now is not None:
        lines += [wib(now), ""]

    if bergerak:
        lines.append(f"YANG BERGERAK SEJAK PENUTUPAN KEMARIN: {len(bergerak)}")
        lines.append("")
        # Yang paling jauh melewati ambangnya lebih dulu. Peringkatnya memakai
        # severity yang sudah dinormalkan ke ambang masing-masing, jadi lonjakan
        # volume dan break bisa dibandingkan tanpa salah satunya menang hanya
        # karena satuannya lebih besar.
        for r in sorted(
            bergerak,
            key=lambda x: max((e.severity for e in x.events), default=0.0),
            reverse=True,
        )[:MAX_SIMBOL]:
            lines.append(r.symbol)
            for e in r.events:
                lines.append(f"  {e.kind.value}  {e.severity:.2f}x ambang")
                lines.append(f"    {e.detail}")
            lines.append("")
        sisa = len(bergerak) - MAX_SIMBOL
        if sisa > 0:
            lines.append(f"({sisa} simbol lagi tidak ditampilkan di sini)")
            lines.append("")
    else:
        lines += ["TIDAK ADA YANG BERGERAK melewati ambangnya.", ""]

    if berita:
        lines.append("BERITA YANG MASUK SEMALAM:")
        for item in berita[:MAX_BERITA]:
            # Baris berita datang sebagai dict dari repository, bukan objek.
            # Versi pertama blok ini memakai `getattr`, yang pada dict selalu
            # gagal dan mencetak judul kosong tanpa satu pun error.
            baris = item if isinstance(item, dict) else vars(item)
            judul = baris.get("title") or baris.get("headline") or "(tanpa judul)"
            penting = baris.get("importance") or ""
            lines.append(f"  [{penting}] {judul}" if penting else f"  {judul}")
        lines.append("")

    if diam:
        # Disebut namanya, bukan hanya jumlahnya: "BBRI diam" adalah kabar yang
        # bisa dipakai, "12 diam" hanya angka.
        lines.append("TIDAK BERGERAK: " + ", ".join(r.symbol for r in diam))
        lines.append("")

    if gagal:
        # Gagal dibaca bukan sama dengan diam, dan menggabungkannya akan
        # membuat pemindaian yang separuh rusak terbaca seperti pasar yang
        # tenang.
        lines.append("TIDAK BISA DIBACA:")
        for r in gagal:
            lines.append(f"  {r.symbol}: {getattr(r, 'reason', 'tidak diketahui')}")
        lines.append("")

    lines += [
        "Ini BUKAN ramalan. Yang di atas adalah apa yang SUDAH terjadi sejak",
        "penutupan kemarin - bukan apa yang akan terjadi hari ini. ARUNA tidak",
        "tahu arah pembukaan, dan tidak ada di sini yang mengklaim tahu.",
        "",
        "ARUNA ANALYST ONLY",
        "EXECUTION: USER",
    ]
    return "\n".join(lines)


@dataclass(slots=True)
class PreOpenScreening:
    """Memindai IDX sekali di jendela pemanasan, lalu mengabarkannya."""

    scanner: Any
    sender: Any
    state: Any = None
    news: Any = None
    #: Tanggal WIB terakhir yang sudah dikirim.
    _last_date: str | None = None
    #: Diisi supaya `slots=True` tidak menolak atribut yang ditambahkan test.
    _unused: tuple[()] = field(default_factory=tuple)

    def _can_send(self) -> bool:
        ready = getattr(self.sender, "ready", None)
        return True if ready is None else bool(ready())

    async def due(self, now: datetime) -> bool:
        """Di jendela pemanasan, hari ini, dan belum dikirim?

        Jendelanya adalah :func:`~aruna.core.clock.idx_active` yang belum buka:
        aktif, tapi bursanya belum mencocokkan order. Itu tepat tiga puluh menit
        sebelum bel, dan ia diturunkan dari kalender - bukan dari jam yang
        diketik di sini, yang akan berbeda pendapat begitu bursa mengubah jam
        bukanya.
        """
        from aruna.core.clock import IDX_CALENDAR, idx_active

        if not self._can_send():
            return False
        if not idx_active(now) or IDX_CALENDAR.is_open(now):
            return False

        hari_ini = now.astimezone(WIB).date().isoformat()
        if self._last_date == hari_ini:
            return False
        if self.state is not None and self._last_date is None:
            simpan = await self.state.get(SCREENING_SENT_KEY)
            if simpan and simpan.get("date"):
                self._last_date = str(simpan["date"])
                if self._last_date == hari_ini:
                    return False
        return True

    async def run(self, now: datetime) -> bool:
        """Pindai dan kirim kalau memang waktunya. True kalau terkirim."""
        if not await self.due(now):
            return False

        hasil = await self.scanner.scan(now)
        berita = await self._berita(now)
        teks = render_screening(list(hasil), berita, now=now)

        if not await self.sender.send(teks):
            # Tidak distempel: tick berikutnya masih di dalam jendela dan boleh
            # mencoba lagi.
            log.warning("screening.undelivered")
            return False

        hari_ini = now.astimezone(WIB).date().isoformat()
        self._last_date = hari_ini
        if self.state is not None:
            await self.state.set(
                SCREENING_SENT_KEY, {"date": hari_ini}, actor="aruna-screening"
            )
        log.info(
            "screening.sent",
            symbols=len(hasil),
            moving=sum(1 for r in hasil if getattr(r, "events", ())),
        )
        return True

    async def _berita(self, now: datetime) -> list[Any]:
        """Berita IDX terbaru, kalau sumbernya ada.

        Ditapis ke jendela sejak penutupan kemarin **di sini**, bukan lewat
        parameter kueri: ``NewsRepository.recent`` tidak punya ``since`` - ia
        menerima ``limit``, ``market`` dan ``min_importance``. Versi pertama
        metode ini mengoper ``since=`` dan tertangkap ``except`` di bawah, jadi
        blok beritanya tidak akan pernah muncul dan tidak ada yang gagal dengan
        berisik.

        Kegagalan di sini **tidak** membatalkan pesannya. Bagian harga adalah
        yang terukur; berita adalah tambahan, dan menukar seluruh kabar dengan
        satu kueri yang gagal adalah pertukaran yang salah arah.
        """
        if self.news is None:
            return []
        try:
            from aruna.core.enums import Market

            ambil = getattr(self.news, "recent", None)
            if ambil is None:
                return []
            rows = await ambil(limit=MAX_BERITA * 4, market=Market.IDX)
        except Exception:
            log.exception("screening.news_unavailable")
            return []

        batas = now - timedelta(days=1)
        segar = []
        for row in rows:
            terbit = row.get("published_at") or row.get("fetched_at")
            if terbit is None or terbit >= batas:
                segar.append(row)
        return segar[:MAX_BERITA]


__all__ = [
    "MAX_BERITA",
    "MAX_SIMBOL",
    "SCREENING_SENT_KEY",
    "WIB",
    "PreOpenScreening",
    "render_screening",
]
