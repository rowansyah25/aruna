"""Daur hidup satu keputusan (PASAL 14.22, 14.23, 14.24).

Sebuah signal berhorizon lima belas menit yang masih ditampilkan sebagai aktif
tiga jam kemudian bukan signal - ia sisa. Operator yang membacanya melihat
entry dan stop yang dihitung untuk pasar yang sudah tidak ada.

**State-nya berurutan, dan urutannya ditegakkan.** Sebuah keputusan tidak bisa
melompat dari ``ANALYZING`` langsung ke ``PUBLISHED`` tanpa melewati council
dan gerbangnya; sebuah keputusan yang sudah ``EXPIRED`` tidak bisa kembali
``ACTIVE``. Transisi yang tidak diperbolehkan ditolak di sini alih-alih
menghasilkan keadaan yang tidak pernah dirancang siapa pun.

**PASAL 14.24: sesudah terbit, tidak ada yang boleh berubah.** Bukan arahnya,
bukan entry, bukan stop, bukan target, bukan suara agent. Kalau pasar berubah,
yang dibuat adalah keputusan BARU - dan yang lama ditandai ``INVALIDATED``,
bukan disunting. Sejarah yang bisa disunting bukan sejarah; ia versi terakhir
dari sebuah cerita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class State(StrEnum):
    """PASAL 14.23. ``WAIT`` sengaja tidak ada - ia bukan keadaan akhir."""

    ANALYZING = "ANALYZING"
    CANDIDATE = "CANDIDATE"
    DEBATING = "DEBATING"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    HIT = "HIT"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        """Keadaan yang tidak punya kelanjutan."""
        return self in (State.HIT, State.INVALIDATED, State.EXPIRED)

    @property
    def published(self) -> bool:
        """Sudah sampai ke operator - dan karenanya tidak boleh diubah."""
        return self in (
            State.PUBLISHED, State.ACTIVE, State.HIT,
            State.INVALIDATED, State.EXPIRED,
        )


#: Transisi yang diperbolehkan. Yang tidak tercantum ditolak.
#:
#: Ditulis sebagai peta dan bukan sebagai rangkaian `if` karena bentuk inilah
#: yang bisa dibaca sekaligus: seorang pembaca bisa melihat seluruh jalur yang
#: mungkin tanpa menelusuri cabang, dan sebuah jalur yang hilang terlihat
#: sebagai baris yang tidak ada - bukan sebagai cabang yang lupa ditulis.
TRANSISI: dict[State, frozenset[State]] = {
    State.ANALYZING: frozenset({State.CANDIDATE, State.INVALIDATED}),
    State.CANDIDATE: frozenset({State.DEBATING, State.INVALIDATED}),
    State.DEBATING: frozenset({State.VALIDATED, State.INVALIDATED}),
    # VALIDATED boleh berakhir tanpa terbit: gerbang risiko menahannya, atau
    # keputusannya NO SIGNAL. Keduanya bukan kegagalan.
    State.VALIDATED: frozenset({State.PUBLISHED, State.INVALIDATED, State.EXPIRED}),
    State.PUBLISHED: frozenset({State.ACTIVE, State.INVALIDATED, State.EXPIRED}),
    State.ACTIVE: frozenset({State.HIT, State.INVALIDATED, State.EXPIRED}),
    State.HIT: frozenset(),
    State.INVALIDATED: frozenset(),
    State.EXPIRED: frozenset(),
}


class TransitionError(Exception):
    """Perpindahan keadaan yang tidak diperbolehkan."""


def can_move(dari: State, ke: State) -> bool:
    return ke in TRANSISI.get(dari, frozenset())


def move(dari: State, ke: State) -> State:
    """Pindahkan keadaan, atau tolak dengan menyebut kenapa.

    Menolak dengan pengecualian, bukan dengan mengembalikan keadaan lama:
    sebuah perpindahan yang gagal diam-diam meninggalkan pemanggil yang mengira
    ia berhasil, dan keputusan yang statusnya tidak sesuai kenyataan lebih
    berbahaya daripada keputusan yang gagal berisik.
    """
    if not can_move(dari, ke):
        if dari.terminal:
            raise TransitionError(
                f"{dari.value} adalah keadaan akhir; buat keputusan baru "
                f"alih-alih memindahkannya ke {ke.value} (PASAL 14.24)"
            )
        raise TransitionError(
            f"tidak ada jalur dari {dari.value} ke {ke.value}"
        )
    return ke


#: Horizon yang dikenal, dan berapa lama keputusannya berlaku (PASAL 14.6).
#:
#: Masa berlakunya SAMA dengan horizonnya, bukan kelipatannya. Sebuah keputusan
#: lima belas menit yang masih berlaku satu jam kemudian sedang menilai pasar
#: yang berbeda dari yang dianalisisnya.
HORIZON: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    # PASAL 14.4 menyebut 10m di daftar timeframe yang dianalisis bersamaan.
    # Bukan interval lilin di venue mana pun yang dipakai ARUNA - ia ada di
    # sini supaya urutan timeframe di :mod:`aruna.decision.timeframes` bisa
    # menempatkannya, dan supaya sebuah horizon 10 menit punya masa berlaku.
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


@dataclass(frozen=True, slots=True)
class Umur:
    """Kapan sebuah keputusan lahir, dan sampai kapan ia berlaku."""

    published_at: datetime
    horizon: str

    @property
    def window(self) -> timedelta | None:
        return HORIZON.get(self.horizon)

    @property
    def expires_at(self) -> datetime | None:
        """``None`` kalau horizonnya tidak dikenal.

        Tidak ditebak dengan bawaan: sebuah masa berlaku yang dikarang membuat
        keputusan kedaluwarsa pada waktu yang tidak pernah diputuskan siapa
        pun, dan keputusan yang hidup terlalu lama sama menyesatkannya dengan
        yang mati terlalu cepat.
        """
        w = self.window
        return None if w is None else self.published_at + w

    def expired(self, now: datetime) -> bool:
        batas = self.expires_at
        return False if batas is None else now >= batas

    def line(self, now: datetime) -> str:
        batas = self.expires_at
        if batas is None:
            return f"berlaku: horizon {self.horizon} tidak dikenal"
        if now >= batas:
            return f"KEDALUWARSA sejak {batas:%H:%M} ({self.horizon})"
        sisa = batas - now
        menit = int(sisa.total_seconds() // 60)
        return f"berlaku sampai {batas:%H:%M} (sisa {menit} menit)"


__all__ = [
    "HORIZON",
    "TRANSISI",
    "State",
    "TransitionError",
    "Umur",
    "can_move",
    "move",
]
