"""Jejak audit satu keputusan (PASAL 14.30).

*"Setiap final decision harus dapat direkonstruksi."* Itu ujian yang lebih
keras daripada "harus dicatat": sebuah baris log yang menyimpan hasilnya tanpa
menyimpan apa yang menghasilkannya tidak bisa direkonstruksi, dan keputusan
yang tidak bisa direkonstruksi tidak bisa dipelajari, dibantah, atau
diperbaiki. Ia hanya bisa dipercaya atau tidak.

**Kolom kosong bukan kolom yang terisi "tidak ada".** Perbedaannya kecil di
layar dan besar di makna: kolom yang blank berarti *tidak ada yang tahu apakah
lapisan itu berjalan*, sedangkan "tidak ada protes" adalah hasil pengamatan.
Jejak ini menolak yang pertama dan menerima yang kedua, dan tidak akan pernah
menerjemahkan satu menjadi yang lain.

**Yang wajib bergantung pada keputusannya.** Sebuah NO SIGNAL tidak punya
entry, stop, target, syarat pembatalan, atau masa berlaku - tidak ada yang
dimasuki dan tidak ada yang bisa runtuh. Menuntut lima kolom itu pada setiap
baris akan memaksa lapisan di atasnya mengisinya dengan sesuatu, dan sesuatu
yang diisi supaya lolos adalah karangan (PASAL 13.26).

**Sidik jarinya membuat PASAL 14.24 punya gigi.** Larangan menyunting signal
yang sudah terbit tidak bisa ditegakkan oleh niat baik; ia butuh sesuatu yang
**berubah kalau isinya berubah**. :attr:`Rekaman.fingerprint` adalah itu -
bukan kunci, bukan tanda tangan, hanya ringkasan yang tidak mungkin tetap sama
setelah satu angka digeser.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from aruna.decision.score import Arah


class Jejak(StrEnum):
    """Dua puluh tiga kolom PASAL 14.30, dalam urutan yang tertulis di sana."""

    SIGNAL_ID = "signal id"
    TIMESTAMP = "waktu"
    ASSET = "aset"
    MARKET = "pasar"
    TIMEFRAMES = "timeframe"
    REGIME = "rezim pasar"
    AGENT_VOTES = "suara agent"
    # Keempat baris di bawah ini adalah **bukti** bahwa PASAL 14.9 (debate),
    # PASAL 14.10 (protest), PASAL 14.11 (veto), dan PASAL 14.12 (council)
    # benar-benar dipakai keputusan final - bukan dibangun ulang di Phase 14.
    #
    # Mekanismenya sendiri lahir di Phase 5 dan Phase 6; yang Phase 14 minta
    # adalah menyatakan bahwa keputusan memakainya dan membuktikan itu terjadi.
    # Membangunnya lagi akan menghasilkan council kedua yang tidak sepakat
    # dengan yang pertama - dan jejak inilah tempat pembuktiannya.
    AGENT_ARGUMENTS = "argumen agent"
    PROTESTS = "protes"
    VETO = "veto"
    COUNCIL_DECISION = "keputusan council"
    SIGNAL_QUALITY = "signal quality"
    CONFIDENCE = "confidence"
    RISK_SCORE = "risk score"
    STRATEGY = "strategi"
    MODEL_VERSION = "versi model"
    DECISION_SCORE = "decision score"
    FINAL_DECISION = "keputusan final"
    ENTRY = "entry"
    SL = "stop loss"
    TP = "take profit"
    INVALIDATION = "syarat pembatalan"
    EXPIRATION = "masa berlaku"


#: Kolom yang hanya berlaku untuk keputusan berarah.
#:
#: NO SIGNAL tidak punya kelimanya, dan menuntutnya akan memaksa lapisan di
#: atasnya mengarang - lihat catatan modul.
BERARAH: frozenset[Jejak] = frozenset({
    Jejak.ENTRY,
    Jejak.SL,
    Jejak.TP,
    Jejak.INVALIDATION,
    Jejak.EXPIRATION,
})

#: Kolom yang wajib ada di setiap keputusan, apa pun arahnya.
SELALU: tuple[Jejak, ...] = tuple(j for j in Jejak if j not in BERARAH)


class TrailError(ValueError):
    """Jejak yang tidak bisa dipakai merekonstruksi keputusannya."""


def required_fields(decision: Arah) -> tuple[Jejak, ...]:
    """Kolom yang wajib terisi untuk sebuah keputusan."""
    if decision is Arah.NO_SIGNAL:
        return SELALU
    return tuple(Jejak)


@dataclass(frozen=True, slots=True)
class Rekaman:
    """Satu baris jejak audit, beku sejak dibuat."""

    decision: Arah
    #: Nilai per kolom, sudah menjadi teks. Disimpan sebagai teks karena
    #: itulah bentuk yang bisa diringkas menjadi sidik jari yang stabil -
    #: sebuah Decimal dan float yang "sama" tidak menghasilkan byte yang sama.
    values: tuple[tuple[Jejak, str], ...]

    @property
    def isi(self) -> dict[Jejak, str]:
        return dict(self.values)

    @property
    def missing(self) -> tuple[Jejak, ...]:
        """Kolom wajib yang kosong atau tidak ada.

        Kosong berarti benar-benar kosong. "tidak ada", "-", "UNKNOWN" adalah
        pengamatan dan diterima; yang ditolak adalah ketiadaan catatan.
        """
        isi = self.isi
        return tuple(
            j for j in required_fields(self.decision)
            if not isi.get(j, "").strip()
        )

    @property
    def reconstructable(self) -> bool:
        """PASAL 14.30 dalam satu properti."""
        return not self.missing

    @property
    def fingerprint(self) -> str:
        """Ringkasan isi, untuk menegakkan PASAL 14.24.

        Diambil dari seluruh kolom dalam urutan enum-nya, bukan dari urutan
        pemanggil - dua rekaman dengan isi sama harus menghasilkan sidik jari
        sama meskipun disusun dengan urutan berbeda.
        """
        isi = self.isi
        kanonik = "\n".join(
            f"{j.name}={isi.get(j, '')}" for j in Jejak
        )
        kanonik = f"{self.decision.value}\n{kanonik}"
        return hashlib.sha256(kanonik.encode("utf-8")).hexdigest()

    def unchanged_since(self, fingerprint: str) -> bool:
        """Apakah isinya masih sama dengan saat sidik jari itu diambil."""
        return self.fingerprint == fingerprint

    def report(self) -> list[str]:
        isi = self.isi
        baris = ["🗄 JEJAK KEPUTUSAN", ""]
        for j in required_fields(self.decision):
            nilai = isi.get(j, "").strip()
            baris.append(f"  {j.value:<20} {nilai or '(KOSONG)'}")
        if self.missing:
            baris += ["", "  TIDAK BISA DIREKONSTRUKSI - kolom kosong:"]
            baris += [f"    ✗ {j.value}" for j in self.missing]
        baris += ["", f"  sidik jari: {self.fingerprint[:16]}"]
        return baris


def record(decision: Arah, values: Mapping[Jejak, str]) -> Rekaman:
    """Susun satu baris jejak.

    Kolom yang tidak dikenal diabaikan; kolom yang dikenal disimpan apa adanya,
    termasuk yang kosong - :attr:`Rekaman.missing` yang menghakiminya, bukan
    fungsi ini. Menolak di sini akan membuat rekaman setengah jadi mustahil
    dibuat, dan rekaman setengah jadi justru bentuk yang perlu dilihat ketika
    sebuah lapisan gagal melapor.
    """
    return Rekaman(
        decision=decision,
        values=tuple((j, str(values[j])) for j in Jejak if j in values),
    )


def require_reconstructable(rec: Rekaman) -> Rekaman:
    """Tolak jejak yang tidak lengkap, dengan menyebut kolomnya."""
    if not rec.reconstructable:
        hilang = ", ".join(j.value for j in rec.missing)
        raise TrailError(
            f"keputusan tidak bisa direkonstruksi; kolom kosong: {hilang} "
            f"(PASAL 14.30)"
        )
    return rec


__all__ = [
    "BERARAH",
    "SELALU",
    "Jejak",
    "Rekaman",
    "TrailError",
    "record",
    "require_reconstructable",
    "required_fields",
]
