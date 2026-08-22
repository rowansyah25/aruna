"""Di timeframe mana ingatan benar-benar layak diberi bobot (PASAL 15.44).

Terukur 2026-08-21 atas 9.698 ingatan, dengan disiplin ``as_of`` penuh:

===== =============== ============== ======= ==============
tf    SUPPORTIVE      CONTRARY       selisih putusan
===== =============== ============== ======= ==============
15m   54% dari 186    40% dari 103   +14     membantu
1h    58% dari 159    65% dari 43    -7      tidak membantu
===== =============== ============== ======= ==============

Dan 1h adalah yang dipinjam jalur keputusan langsung lewat
:func:`aruna.memory.lookup.horizon_ingatan`. Sebelum modul ini ada, ARUNA
memberi bobot pada bukti yang evaluasinya sendiri bilang tidak menambah
apa-apa - persis yang PASAL 15.44 larang: *jangan memaksakan penggunaan
memory*.

**Yang digerbangi bobotnya, bukan tampilannya.** Kasus serupa tetap dicetak ke
operator (PASAL 15.20, 15.38); menyembunyikan bukti yang bertentangan adalah
confirmation bias yang dilakukan sistem atas nama operator. Yang berhenti
hanyalah pengaruhnya terhadap keputusan.

**Diam berarti belum terbukti, bukan terbukti baik.** Timeframe yang sampelnya
belum cukup tidak diberi bobot - kalau sebaliknya, setiap timeframe baru mulai
hidupnya dengan bobot penuh atas bukti yang belum pernah diuji, dan gerbang ini
hanya akan menutup sesudah kerusakannya terjadi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aruna.core.clock import isoformat
from aruna.db.types import as_utc
from aruna.memory.evaluasi import SAMPEL_SISI, Evaluasi

__all__ = ["KUNCI_STATE", "Manfaat", "dari_json", "ke_json"]


#: Kunci di ``app_state``.
#:
#: Di sana, bukan di memori proses, karena yang **memakai** penilaian ini
#: adalah ``futures-loop`` sementara yang **menghitungnya** adalah loop upkeep
#: di ``aruna run`` - dua proses. Cache dalam proses akan membuat gerbangnya
#: diam-diam terbuka di sisi yang justru mengambil keputusan; itu persis
#: kesalahan yang sempat membuat ingatan tersambung ke proses yang salah.
KUNCI_STATE = "memory_manfaat"


@dataclass(frozen=True, slots=True)
class Manfaat:
    """Putusan untuk satu timeframe, berikut angka yang mendasarinya."""

    timeframe: str
    evaluasi: Evaluasi
    dinilai_pada: datetime
    #: Berapa keputusan yang benar-benar bisa dinilai saat penilaian ini dibuat.
    dinilai_dari: int

    @property
    def dipakai(self) -> bool:
        """Hanya yang terbukti membantu.

        Bukan "yang tidak terbukti berlawanan": selisih -7 bukan ``terbalik``
        (belum mencapai -10) tapi juga bukan ``membantu``, dan memberi bobot
        pada yang tidak menambah apa-apa adalah memaksakan penggunaan memory.
        """
        return self.evaluasi.membantu

    def alasan(self) -> str:
        """Satu baris untuk operator dan untuk ``catatan`` konteks.

        Menyebut angkanya, bukan cuma putusannya: seseorang yang melihat
        pengaruh ingatan dimatikan harus bisa tahu atas dasar apa tanpa
        menjalankan ulang evaluasinya.
        """
        if not self.evaluasi.cukup:
            return (
                f"{self.timeframe}: kontribusi memory belum bisa dinilai "
                f"(SUPPORTIVE {self.evaluasi.mendukung_total}, CONTRARY "
                f"{self.evaluasi.melawan_total}; butuh {SAMPEL_SISI} tiap sisi)"
            )
        return f"{self.timeframe}: {self.evaluasi.ringkas()}"

    def ke_dict(self) -> dict[str, Any]:
        e = self.evaluasi
        return {
            "timeframe": self.timeframe,
            "mendukung_menang": e.mendukung_menang,
            "mendukung_kalah": e.mendukung_kalah,
            "melawan_menang": e.melawan_menang,
            "melawan_kalah": e.melawan_kalah,
            "dinilai_pada": isoformat(self.dinilai_pada),
            "dinilai_dari": self.dinilai_dari,
        }


def _waktu(nilai: Any) -> datetime:
    """Stempel waktu dari `app_state`, selalu sadar-zona.

    `app_state` menyimpan JSON, jadi yang kembali adalah string ISO - bukan
    `datetime`. Menyerahkannya langsung ke `as_utc` meledak, dan `datetime`
    naif yang lolos akan membuat perbandingan umur penilaian salah diam-diam.
    """
    if isinstance(nilai, datetime):
        return as_utc(nilai)
    return as_utc(datetime.fromisoformat(str(nilai)))


def ke_json(manfaat: dict[str, Manfaat]) -> dict[str, Any]:
    return {tf: m.ke_dict() for tf, m in manfaat.items()}


def dari_json(mentah: Any) -> dict[str, Manfaat]:
    """Baca kembali dari ``app_state``, memaafkan bentuk yang tidak dikenal.

    ``app_state`` yang kosong, ditulis versi lama, atau rusak sebagian tidak
    boleh menjatuhkan tick futures - dan gerbang yang meledak saat penilaiannya
    hilang lebih buruk daripada gerbang yang tertutup.
    """
    if not isinstance(mentah, dict):
        return {}
    keluar: dict[str, Manfaat] = {}
    for tf, isi in mentah.items():
        if not isinstance(isi, dict):
            continue
        try:
            keluar[str(tf)] = Manfaat(
                timeframe=str(isi["timeframe"]),
                evaluasi=Evaluasi(
                    mendukung_menang=int(isi["mendukung_menang"]),
                    mendukung_kalah=int(isi["mendukung_kalah"]),
                    melawan_menang=int(isi["melawan_menang"]),
                    melawan_kalah=int(isi["melawan_kalah"]),
                ),
                dinilai_pada=_waktu(isi["dinilai_pada"]),
                dinilai_dari=int(isi["dinilai_dari"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return keluar
