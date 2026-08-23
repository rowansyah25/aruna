"""Pertanyaan riset ARUNA, didorong ke operator (PASAL 11.16).

Operator meminta ARUNA memberi tahu setiap kali ia "meminta pembaruan model",
lengkap dengan usulannya, untuk disetujui atau ditolak.

**Yang dikirim adalah pertanyaan, bukan usulan.** PASAL 11.16 melarang ARUNA
melakukan modifikasi model otomatis, dan larangan itu ditulis operator sendiri.
Sistem ini dibangun menurutnya: ``aruna proposals`` mencetak "proposals are
written by a person against a research question; ARUNA does not author changes
to itself". Sebuah sistem yang mengusulkan perubahan atas dirinya sendiri akan
condong mengusulkan perubahan yang membuat angkanya terlihat lebih baik, dan
tidak ada yang bisa membedakan itu dari perbaikan.

Bertanya bukan mengubah. ARUNA membaca kekalahannya sendiri, menemukan pola,
lalu mengangkat pertanyaan - dan berhenti di situ. Yang memutuskan apakah
pertanyaan itu layak jadi proposal adalah orang.

**Dua hal dikirim, dan keduanya perlu tindakan berbeda:**

* **pertanyaan baru** - bahan pertimbangan, tidak menuntut jawaban hari ini;
* **proposal yang menunggu keputusan** - menuntut ``/approve`` atau
  ``/reject``, dan akan terus menunggu sampai salah satunya diberikan.

Sekali sehari, bukan tiap siklus. Analisis kekalahan bergerak dalam hitungan
hari; mengirimnya tiap lima belas detik akan mengubah kabar yang perlu
dipikirkan menjadi kebisingan yang dilewati - kegagalan yang sama yang sudah
tiga kali ditemukan di sistem ini.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.core.clock import JAKARTA
from aruna.core.logging import get_logger

log = get_logger("aruna.notify.research")

#: Satu sumber, lihat :data:`~aruna.core.clock.JAKARTA`.
WIB = JAKARTA

#: Kunci di ``app_state``. Nilainya ``{"date": "YYYY-MM-DD"}``.
RESEARCH_SENT_KEY = "research_digest_sent"

#: Status proposal yang menunggu keputusan orang.
#:
#: Diturunkan dari :class:`~aruna.governance.proposal.ProposalStatus`, bukan
#: diketik sebagai teks bebas. Versi pertama daftar ini memuat ``"SUBMITTED"``
#: - status yang tidak pernah ada di enum itu. Ia tidak berbahaya, karena nama
#: yang tidak ada tidak pernah cocok dengan apa pun; ia hanya berbohong dengan
#: tenang tentang apa yang diperiksa, dan daftar seperti itu tidak bisa gagal
#: dengan berisik.
#:
#: ``VALIDATED`` ikut karena ``ready_for_approval`` memang menerimanya: bukti
#: sudah dibandingkan dengan aturan berjalan, jadi ``/approve`` akan bekerja.
#: ``DRAFT`` dan ``SHADOWED`` tidak - keduanya belum punya bukti untuk
#: diputuskan, dan mengingatkan operator tentangnya berarti memintanya
#: memutuskan sesuatu yang belum bisa diputuskan.
MENUNGGU_KEPUTUSAN: tuple[str, ...] = ("AWAITING_APPROVAL", "VALIDATED")

#: Paling banyak sekian pertanyaan per pesan. Sisanya disebut jumlahnya dan
#: dibaca lewat ``/research`` - daftar yang lebih panjang dari satu layar tidak
#: dibaca lebih banyak, hanya digulir lebih cepat.
MAX_PERTANYAAN = 5


def render_digest(
    pertanyaan: list[Any], menunggu: list[dict[str, Any]], *, now: datetime | None = None
) -> str:
    """Satu pesan: apa yang ARUNA tanyakan, dan apa yang menunggu keputusanmu."""
    from aruna.core.clock import wib

    lines = ["🔬 ARUNA - PERTANYAAN DARI CATATANNYA SENDIRI", ""]

    if pertanyaan:
        lines.append(f"PERTANYAAN BARU: {len(pertanyaan)}")
        lines.append("")
        for q in pertanyaan[:MAX_PERTANYAAN]:
            lines.append(f"- {getattr(q, 'question', '?')}")
            for bukti in tuple(getattr(q, "evidence", ()) or ())[:2]:
                lines.append(f"    {bukti}")
        sisa = len(pertanyaan) - MAX_PERTANYAAN
        if sisa > 0:
            lines.append(f"  ({sisa} pertanyaan lagi - kirim /research)")
    else:
        lines.append("PERTANYAAN BARU: tidak ada")

    lines += ["", "MENUNGGU KEPUTUSAN ANDA:"]
    if menunggu:
        for row in menunggu:
            lines.append(f"- {row.get('proposal_key')}  [{row.get('status')}]")
            lines.append(f"    {row.get('title')}")
        lines += [
            "",
            "Kirim /approve <key> atau /reject <key>.",
        ]
    else:
        lines.append("tidak ada proposal yang menunggu")

    lines += [
        "",
        "ARUNA tidak mengubah dirinya sendiri (PASAL 11.16). Yang di atas",
        "adalah pertanyaan dari catatannya, bukan usulan perubahan - dan",
        "proposal hanya ditulis dan diputuskan oleh Anda.",
    ]
    if now is not None:
        lines += ["", wib(now)]
    return "\n".join(lines)


@dataclass(slots=True)
class ResearchNotifier:
    """Menjalankan riset sekali sehari dan mengabarkan yang baru."""

    governance: Any
    store: Any
    sender: Any
    state: Any = None
    #: Tanggal WIB terakhir yang sudah dikirim.
    _last_date: str | None = None
    #: Kunci pertanyaan yang sudah pernah dikabarkan.
    _seen: set[str] = field(default_factory=set)

    def _can_send(self) -> bool:
        ready = getattr(self.sender, "ready", None)
        return True if ready is None else bool(ready())

    async def due(self, now: datetime) -> bool:
        """Hari WIB ini belum dikirim?

        Penanda disimpan di ``app_state`` supaya bertahan melewati restart.
        Tanpa itu, penjaga proses - yang memang menyalakan ulang ARUNA - akan
        membuat pesan ini datang pada setiap kelahiran ulang, dan kabar yang
        datang tiap restart berhenti dibaca sebagai kabar.
        """
        if not self._can_send():
            return False
        hari_ini = now.astimezone(WIB).date().isoformat()
        if self._last_date == hari_ini:
            return False
        if self.state is not None and self._last_date is None:
            simpan = await self.state.get(RESEARCH_SENT_KEY)
            if simpan and simpan.get("date"):
                self._last_date = str(simpan["date"])
                if self._last_date == hari_ini:
                    return False
        return True

    async def run(self, now: datetime) -> bool:
        """Jalankan riset, kirim yang baru. True kalau ada yang dikirim."""
        if not await self.due(now):
            return False

        hasil = await self.governance.research()
        semua = list(getattr(hasil, "questions", None) or [])
        baru = [q for q in semua if getattr(q, "key", None) not in self._seen]

        menunggu = [
            row
            for row in await self.store.proposals(limit=50)
            if str(row.get("status", "")).upper() in MENUNGGU_KEPUTUSAN
        ]

        if not baru and not menunggu:
            # Tidak ada kabar bukan kabar. Tanggalnya **tetap** distempel:
            # tanpa itu, hari yang sepi akan membuat riset dijalankan ulang
            # pada setiap siklus sepanjang hari itu, dan analisis kekalahan
            # bukan kueri yang murah.
            await self._stamp(now)
            log.info("research.quiet", questions=len(semua))
            return False

        teks = render_digest(baru, menunggu, now=now)
        if not await self.sender.send(teks):
            # Tidak distempel: pesan yang gagal terkirim harus dicoba lagi.
            log.warning("research.undelivered")
            return False

        self._seen.update(
            str(q.key) for q in baru if getattr(q, "key", None) is not None
        )
        await self._stamp(now)
        log.info(
            "research.sent", questions=len(baru), awaiting_decision=len(menunggu)
        )
        return True

    async def _stamp(self, now: datetime) -> None:
        hari_ini = now.astimezone(WIB).date().isoformat()
        self._last_date = hari_ini
        if self.state is not None:
            await self.state.set(
                RESEARCH_SENT_KEY, {"date": hari_ini}, actor="aruna-research"
            )


__all__ = [
    "MAX_PERTANYAAN",
    "MENUNGGU_KEPUTUSAN",
    "RESEARCH_SENT_KEY",
    "WIB",
    "ResearchNotifier",
    "render_digest",
]
