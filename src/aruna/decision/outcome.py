"""Nasib satu signal, dan apa yang dipelajari darinya (PASAL 14.31, 14.34).

Empat akhir yang mungkin - WIN, LOSS, INVALIDATED, EXPIRED - dan semuanya
dikirim ke Phase 12 dan Phase 11. Yang tidak dikirim tidak dipelajari, dan
sistem yang hanya mempelajari kemenangannya akan yakin pada dirinya sendiri
dengan kecepatan yang mengkhawatirkan.

**LOSS tidak boleh dilunakkan.** PASAL 11.21 dan 6 melarang menghapus,
menyembunyikan, atau mengubah LOSS. Modul ini tidak punya satu pun jalur yang
mengubah hasil sesudah dicatat, dan kalimat yang dicetaknya menyebut LOSS
dengan namanya sendiri.

**FALSE SIGNAL bukan sinonim LOSS.** PASAL 14.34 mendefinisikannya lebih
sempit: arahnya LONG tapi pasar bergerak berlawanan **secara signifikan**.
Sebuah LOSS kecil - stop kena setelah harga bergerak sedikit - adalah biaya
normal dari bertaruh; sebuah gerakan besar ke arah berlawanan berarti bacaannya
memang salah, dan itu pelajaran yang berbeda. Menyamakan keduanya membuat
setiap stop yang kena terlihat seperti kesalahan analisis, dan yang hilang
adalah kemampuan membedakan mana yang benar-benar salah baca.

Ambangnya sama dengan ambang kesempatan yang terlewat di
:mod:`aruna.decision.silence`, dan itu disengaja: gerakan sebesar yang membuat
diamnya ARUNA disebut kehilangan adalah gerakan sebesar yang membuat
pendapatnya disebut salah. Dua arah, satu ukuran.

**Arahnya diorientasikan di sini, bukan oleh pemanggil.** ``move_pct`` adalah
gerak pasar apa adanya - positif berarti harga naik. Yang membalik tandanya
untuk SHORT adalah modul ini. Meminta pemanggil menyerahkan angka yang sudah
diorientasikan adalah mengundang salah tanda, dan salah tanda di sini mengubah
kekalahan besar menjadi kemenangan besar di dalam data yang dipelajari.

**Signal palsu tanpa sebab tidak mengajarkan apa pun.** PASAL 14.34 mendaftar
tujuh sebab yang harus dicari. Kalau belum ketemu, itu dinyatakan - bukan
diisi dengan tebakan, dan bukan dikirim ke Phase 12 seolah-olah sudah
dianalisis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from aruna.decision.lifecycle import State
from aruna.decision.score import Arah
from aruna.decision.silence import GERAK_BERARTI_PCT


class OutcomeError(ValueError):
    """Catatan hasil yang bertentangan dengan dirinya sendiri."""


class Hasil(StrEnum):
    """Empat akhir PASAL 14.31."""

    WIN = "WIN"
    LOSS = "LOSS"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

    @property
    def mark(self) -> str:
        return {
            Hasil.WIN: "🏆",
            Hasil.LOSS: "🔴",
            Hasil.INVALIDATED: "⚠️",
            Hasil.EXPIRED: "⏱",
        }[self]


#: Keadaan akhir yang sah untuk tiap hasil.
#:
#: WIN dan LOSS sama-sama datang dari ``HIT`` - yang membedakannya bukan
#: keadaannya melainkan level mana yang kena. Peta ini menolak gabungan yang
#: mustahil: sebuah WIN yang keadaannya EXPIRED berarti ada dua lapisan yang
#: bercerita berbeda tentang signal yang sama.
KEADAAN: dict[Hasil, State] = {
    Hasil.WIN: State.HIT,
    Hasil.LOSS: State.HIT,
    Hasil.INVALIDATED: State.INVALIDATED,
    Hasil.EXPIRED: State.EXPIRED,
}


class Sebab(StrEnum):
    """Tujuh sebab yang dicari PASAL 14.34."""

    AGENT_SALAH = "agent salah baca"
    REZIM_SALAH = "rezim pasar salah diklasifikasi"
    TIMING_BURUK = "waktu masuk buruk"
    SL_BURUK = "stop loss buruk"
    KEJUTAN_BERITA = "kejutan berita"
    STRATEGI_GAGAL = "strategi gagal"
    MASALAH_DATA = "masalah data"


@dataclass(frozen=True, slots=True)
class Catatan:
    """Satu signal yang sudah selesai, beserta apa yang terjadi padanya."""

    symbol: str
    decision: Arah
    outcome: Hasil
    #: Gerak pasar dari entry sampai saat hasilnya ditentukan, sebagai persen.
    #: **Positif berarti harga naik**, apa pun arah signalnya. ``None`` berarti
    #: belum terukur.
    move_pct: Decimal | None = None
    #: Sebab yang ditemukan analisis (PASAL 14.34). Kosong berarti belum
    #: dianalisis - bukan berarti tidak ada sebabnya.
    causes: tuple[Sebab, ...] = field(default_factory=tuple)
    #: Kalimat akar masalah, seperti "Short-term reversal" di PASAL 14.31.
    note: str = ""

    def __post_init__(self) -> None:
        if self.decision is Arah.NO_SIGNAL:
            raise OutcomeError(
                "NO SIGNAL tidak punya hasil; yang menilai diamnya ARUNA "
                "adalah aruna.decision.silence"
            )
        searah = self.searah_pct
        if searah is None:
            return
        # Gabungan yang mustahil ditolak di sini karena bentuk paling umum dari
        # bug ini adalah salah tanda di lapisan yang mengukur - dan salah tanda
        # yang lolos akan mengajari Phase 12 kebalikan dari kenyataan.
        if self.outcome is Hasil.WIN and searah < 0:
            raise OutcomeError(
                f"{self.symbol} disebut WIN padahal pasar bergerak "
                f"{searah:+.2f}% melawan arahnya"
            )
        if self.outcome is Hasil.LOSS and searah > 0:
            raise OutcomeError(
                f"{self.symbol} disebut LOSS padahal pasar bergerak "
                f"{searah:+.2f}% searah dengannya"
            )

    # ---- arah -----------------------------------------------------------

    @property
    def searah_pct(self) -> Decimal | None:
        """Gerak menurut arah signalnya. Positif berarti signalnya benar."""
        if self.move_pct is None:
            return None
        return self.move_pct if self.decision is Arah.LONG else -self.move_pct

    @property
    def state(self) -> State:
        """Keadaan akhir yang sesuai dengan hasil ini."""
        return KEADAAN[self.outcome]

    # ---- PASAL 14.34 ----------------------------------------------------

    @property
    def false_signal(self) -> bool | None:
        """Apakah ini FALSE SIGNAL menurut PASAL 14.34.

        ``None`` kalau geraknya belum terukur. Bukan ``False``: ketiadaan
        pengukuran bukan bukti bahwa bacaannya benar, dan mengembalikan
        ``False`` akan membuat setiap signal yang belum diukur masuk ke Phase 12
        sebagai signal yang sehat.
        """
        searah = self.searah_pct
        if searah is None:
            return None
        return searah <= -GERAK_BERARTI_PCT

    @property
    def analysed(self) -> bool:
        return bool(self.causes)

    @property
    def needs_analysis(self) -> bool:
        """Signal palsu yang sebabnya belum dicari."""
        return self.false_signal is True and not self.analysed

    # ---- keluaran -------------------------------------------------------

    def line(self) -> str:
        bagian = f"{self.outcome.mark} {self.outcome.value} - {self.symbol} {self.decision.value}"
        searah = self.searah_pct
        if searah is not None:
            bagian += f" ({searah:+.2f}%)"
        return bagian

    def report(self) -> list[str]:
        """Pesan hasil (PASAL 14.31), sebagai baris."""
        baris = [self.line(), ""]
        if self.false_signal is True:
            # Ditaruh sebelum apa pun yang menjelaskan. Sebuah label yang
            # muncul sesudah paragraf pembenaran sudah kehilangan gunanya.
            baris.append("  FALSE SIGNAL - pasar bergerak jauh ke arah sebaliknya")
        elif self.false_signal is None:
            baris.append("  gerak pasar belum terukur")
        if self.note.strip():
            baris.append(f"  Akar masalah: {self.note.strip()}")
        if self.causes:
            baris += ["", "  Sebab yang ditemukan:"]
            baris += [f"    - {s.value}" for s in self.causes]
        elif self.false_signal is True:
            baris += ["", "  Sebab BELUM ditemukan - belum dikirim ke learning."]
        baris += ["", "  Dikirim ke: Phase 12 (learning), Phase 11 (reliability)"]
        return baris

    def learning_payload(self) -> dict[str, object]:
        """Bentuk yang dikirim ke Phase 12 dan Phase 11 (PASAL 14.31).

        Hasilnya dibawa apa adanya. Tidak ada satu pun cabang di sini yang
        membaca ``outcome`` dan menuliskan sesuatu yang lain - itulah bentuk
        teknis dari larangan menyembunyikan LOSS (PASAL 11.21).
        """
        return {
            "symbol": self.symbol,
            "decision": self.decision.value,
            "outcome": self.outcome.value,
            "state": self.state.value,
            "move_pct": self.move_pct,
            "false_signal": self.false_signal,
            "causes": [s.value for s in self.causes],
            "note": self.note.strip(),
        }


def require_analysed(catatan: Catatan) -> Catatan:
    """Tolak mengirim signal palsu yang sebabnya belum dicari (PASAL 14.34).

    Yang ditolak hanya signal palsu. Sebuah LOSS biasa tidak wajib punya sebab -
    kalah adalah bagian dari bertaruh, dan menuntut penjelasan untuk setiap
    kekalahan akan menghasilkan penjelasan yang dikarang.
    """
    if catatan.needs_analysis:
        raise OutcomeError(
            f"{catatan.symbol} adalah FALSE SIGNAL tanpa sebab; mengirimnya ke "
            f"learning tidak mengajarkan apa pun (PASAL 14.34)"
        )
    return catatan


__all__ = [
    "KEADAAN",
    "Catatan",
    "Hasil",
    "OutcomeError",
    "Sebab",
    "require_analysed",
]
