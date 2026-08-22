"""Kapan ARUNA boleh berbicara lagi (PASAL 14.35, 14.36, 14.37).

Tiga pasal, satu pertanyaan: sebuah pendapat sudah terbit untuk aset ini -
apakah yang baru ini layak dikirim, atau ia pengulangan?

**Pengulangan tanpa sebab yang disebutkan tidak dikirim.** PASAL 14.37 menulis
bentuk kegagalannya apa adanya: ``LONG`` ``LONG`` ``LONG`` setiap beberapa
detik. Yang rusak bukan hanya ketenangan operator - notifikasi yang berbunyi
terus berhenti dibaca, dan yang hilang pertama justru signal yang berbeda dari
sebelumnya. Perlindungan spam adalah perlindungan terhadap perhatian.

**Enam sebab yang sah, dan hanya itu** (PASAL 14.37): setup baru, perubahan
pasar besar, pembatalan, pembalikan arah, horizon baru, strategi baru.

**Sebab yang bisa diperiksa, diperiksa.** Horizon dan strategi dibandingkan
dengan yang tersimpan, bukan dipercaya dari pemanggil. Perlindungan duplikat
yang bisa dilewati dengan menyebut alasan yang tidak benar bukan perlindungan;
ia formulir. Klaim yang terbukti tidak benar **ditolak keras**, bukan diabaikan
diam-diam - pemanggil yang alasannya dibuang tanpa kabar akan terus mengirimnya.

**Pembalikan menuntut lebih banyak, bukan lebih sedikit.** PASAL 14.35: ARUNA
tidak boleh LONG lalu beberapa detik kemudian SHORT tanpa perubahan pasar yang
berarti. PASAL 14.36 menyebut bentuk lengkapnya - keputusan lama, keputusan
baru, alasan, bukti baru, signal lama INVALIDATED, signal baru CREATED - dan
menutupnya dengan *"Jangan mengubah signal lama."*

Satu-satunya perubahan yang boleh menyentuh signal lama adalah **keadaannya**,
dan itu pun lewat :func:`aruna.decision.lifecycle.move`, yang akan menolak
kalau signal itu sudah berakhir. Sebuah signal yang sudah EXPIRED tidak bisa
dibatalkan - tidak ada lagi yang bisa dibatalkan darinya.

Modul ini tidak menyimpan apa pun dan tidak tahu tentang aset. Pemanggil yang
memberi tahu signal terakhir untuk aset yang sedang dinilai; mencampur dua aset
di sini akan membuat BTC membungkam ETH.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from aruna.decision.explanation import Alasan, check_text
from aruna.decision.lifecycle import State, move
from aruna.decision.score import Arah


class ConsistencyError(ValueError):
    """Pengiriman yang melanggar PASAL 14.35 - 14.37."""


class Pemicu(StrEnum):
    """Enam sebab sah untuk signal baru (PASAL 14.37)."""

    SETUP_BARU = "setup baru"
    PERUBAHAN_BESAR = "perubahan pasar besar"
    PEMBATALAN = "syarat pembatalan terpenuhi"
    BALIK_ARAH = "pembalikan arah"
    HORIZON_BARU = "horizon baru"
    STRATEGI_BARU = "strategi baru"


#: Pemicu yang kebenarannya bisa dibuktikan dari data yang ada di sini.
#:
#: Sisanya - setup baru, perubahan pasar besar, pembatalan - adalah penilaian
#: lapisan di atas, dan modul ini tidak berpura-pura bisa memeriksanya.
TERBUKTI: frozenset[Pemicu] = frozenset({
    Pemicu.HORIZON_BARU,
    Pemicu.STRATEGI_BARU,
    Pemicu.BALIK_ARAH,
})


class Tindakan(StrEnum):
    """Apa yang terjadi pada kandidat ini."""

    DIAM = "TIDAK DIKIRIM"
    KIRIM = "KIRIM SIGNAL BARU"
    BALIK = "KIRIM PEMBALIKAN"


@dataclass(frozen=True, slots=True)
class Terakhir:
    """Signal terakhir yang sudah terbit untuk aset yang sedang dinilai."""

    decision: Arah
    horizon: str
    strategy: str
    state: State

    @property
    def live(self) -> bool:
        """Masih berlaku - dan karenanya masih bisa diduplikasi.

        Signal yang sudah HIT, INVALIDATED, atau EXPIRED tidak membungkam
        apa pun: pendapat berikutnya bukan pengulangan, ia pendapat berikutnya.
        """
        return self.state in (State.PUBLISHED, State.ACTIVE)


@dataclass(frozen=True, slots=True)
class Pembalikan:
    """Perubahan arah, beserta apa yang membenarkannya (PASAL 14.36)."""

    previous: Arah
    new: Arah
    reason: str
    #: Bukti yang belum ada saat keputusan lama dibuat.
    #:
    #: Satu sudah cukup di sini. Signal barunya tetap butuh
    #: :class:`aruna.decision.explanation.Penjelasan` sendiri dengan dua sumber
    #: berbeda, jadi menuntutnya dua kali hanya memindahkan pekerjaan yang sama.
    new_evidence: tuple[Alasan, ...]

    def __post_init__(self) -> None:
        if self.previous is Arah.NO_SIGNAL or self.new is Arah.NO_SIGNAL:
            raise ConsistencyError(
                "pembalikan adalah LONG ke SHORT atau sebaliknya; NO SIGNAL "
                "bukan arah yang bisa dibalik"
            )
        if self.previous is self.new:
            raise ConsistencyError(
                f"{self.previous.value} ke {self.new.value} bukan pembalikan"
            )
        check_text(self.reason, label="alasan pembalikan")
        if not self.new_evidence:
            raise ConsistencyError(
                "pembalikan tanpa bukti baru melanggar PASAL 14.35: perubahan "
                "arah harus dijelaskan oleh data yang belum ada sebelumnya"
            )

    def retire(self, state: State) -> State:
        """Keadaan baru signal lama: INVALIDATED (PASAL 14.36).

        Lewat :func:`move`, bukan penugasan langsung - yang berarti signal yang
        sudah berakhir akan menolak dibatalkan. Tidak ada lagi yang bisa
        dibatalkan darinya, dan menandainya ulang akan menulis ulang sejarah.
        """
        return move(state, State.INVALIDATED)

    def report(self) -> list[str]:
        baris = [
            "🔄 PEMBALIKAN ARAH",
            "",
            f"  Keputusan lama : {self.previous.mark} {self.previous.value}",
            f"  Keputusan baru : {self.new.mark} {self.new.value}",
            f"  Alasan         : {self.reason.strip()}",
            "",
            "  Bukti baru:",
        ]
        baris += [f"    + {a.line()}" for a in self.new_evidence]
        # PASAL 14.36 menutup dengan "Jangan mengubah signal lama." Dinyatakan
        # di pesannya sendiri supaya pembaca tahu yang lama tidak disunting.
        baris += [
            "",
            "  Signal lama : INVALIDATED (tidak disunting)",
            "  Signal baru : DIBUAT",
        ]
        return baris


@dataclass(frozen=True, slots=True)
class Putusan:
    """Kirim, diam, atau balik - beserta sebabnya."""

    action: Tindakan
    reason: str
    triggers: tuple[Pemicu, ...] = field(default_factory=tuple)
    reversal: Pembalikan | None = None

    @property
    def sends(self) -> bool:
        return self.action is not Tindakan.DIAM

    def line(self) -> str:
        if not self.triggers:
            return f"{self.action.value} - {self.reason}"
        sebab = ", ".join(p.value for p in self.triggers)
        return f"{self.action.value} - {self.reason} ({sebab})"


def evaluate(
    candidate: Arah,
    *,
    horizon: str,
    strategy: str,
    previous: Terakhir | None = None,
    triggers: Iterable[Pemicu] = (),
    reversal: Pembalikan | None = None,
) -> Putusan:
    """Apakah kandidat ini layak dikirim (PASAL 14.35 - 14.37).

    ``triggers`` adalah sebab yang **diklaim** pemanggil. Yang bisa dibuktikan
    dari sini diperiksa; yang terbukti salah membatalkan panggilan.
    """
    diklaim = frozenset(triggers)

    if candidate is Arah.NO_SIGNAL:
        # ARUNA tidak mengumumkan ketiadaan pendapat baru. Kalau setup lama
        # memang sudah runtuh, yang membatalkannya adalah syarat pembatalannya
        # sendiri (PASAL 14.21), bukan kehadiran kandidat tanpa arah.
        return Putusan(Tindakan.DIAM, "tidak ada arah baru untuk dikirim")

    if previous is None or not previous.live:
        # Hanya BALIK_ARAH yang bisa dinyatakan salah di sini: tidak ada arah
        # lama yang bisa dibalik. Horizon dan strategi tidak diperiksa karena
        # tidak ada yang masih berlaku untuk dibandingkan - dan menolak klaim
        # yang tidak bisa dibuktikan salah akan menolak yang benar juga.
        _tolak_klaim_palsu(
            diklaim & {Pemicu.BALIK_ARAH}, set(), "belum ada signal aktif"
        )
        return Putusan(
            Tindakan.KIRIM,
            "belum ada signal aktif untuk aset ini",
            tuple(sorted(diklaim, key=lambda p: p.value)),
        )

    berubah_arah = candidate is not previous.decision
    nyata: set[Pemicu] = set()
    if berubah_arah:
        nyata.add(Pemicu.BALIK_ARAH)
    if horizon != previous.horizon:
        nyata.add(Pemicu.HORIZON_BARU)
    if strategy != previous.strategy:
        nyata.add(Pemicu.STRATEGI_BARU)

    _tolak_klaim_palsu(diklaim & TERBUKTI, nyata, "signal aktif")

    if berubah_arah:
        if reversal is None:
            raise ConsistencyError(
                f"{previous.decision.value} berubah menjadi {candidate.value} "
                f"tanpa alasan pembalikan (PASAL 14.35)"
            )
        if reversal.previous is not previous.decision or reversal.new is not candidate:
            raise ConsistencyError(
                f"catatan pembalikan berbunyi {reversal.previous.value} ke "
                f"{reversal.new.value}, tapi yang terjadi "
                f"{previous.decision.value} ke {candidate.value}"
            )
        return Putusan(
            Tindakan.BALIK,
            "arah berubah dan perubahannya dijelaskan",
            tuple(sorted(nyata | diklaim, key=lambda p: p.value)),
            reversal=reversal,
        )

    sebab = (nyata | diklaim) - {Pemicu.BALIK_ARAH}
    if not sebab:
        return Putusan(
            Tindakan.DIAM,
            "arah, horizon, dan strategi sama dengan signal yang masih "
            "berlaku, dan tidak ada perubahan yang disebutkan (PASAL 14.37)",
        )
    return Putusan(
        Tindakan.KIRIM,
        "ada perubahan yang membenarkan signal baru",
        tuple(sorted(sebab, key=lambda p: p.value)),
    )


def _tolak_klaim_palsu(
    diklaim: frozenset[Pemicu], nyata: set[Pemicu], konteks: str
) -> None:
    """Tolak sebab yang bisa diperiksa dan ternyata tidak benar.

    Ditolak keras, bukan dibuang diam-diam: pemanggil yang klaimnya hilang
    tanpa kabar akan terus mengirimkannya, dan perlindungan duplikat yang bisa
    dilewati dengan satu kata yang tidak benar bukan perlindungan.
    """
    palsu = sorted(diklaim - nyata, key=lambda p: p.value)
    if palsu:
        raise ConsistencyError(
            f"{palsu[0].value!r} disebut sebagai sebab, tapi tidak terjadi "
            f"({konteks})"
        )


__all__ = [
    "TERBUKTI",
    "ConsistencyError",
    "Pembalikan",
    "Pemicu",
    "Putusan",
    "Terakhir",
    "Tindakan",
    "evaluate",
]
