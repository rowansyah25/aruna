"""Pola yang sudah teridentifikasi, dibaca - bukan dihitung ulang (PASAL 15.16).

**PASAL 15.33 memisahkan keduanya dengan tegas: Phase 12 MENEMUKAN pola, Phase
15 MENGINGATNYA.** Menghitung ulang di sini akan menghasilkan dua katalog pola
yang bisa berselisih, dan tidak ada yang tahu mana yang dijalankan - kesalahan
yang sama persis dengan "council kedua" yang rencana Phase 14 tolak dengan
alasan itu.

Jadi modul ini tidak menghitung apa pun. Ia mencocokkan kondisi sekarang
dengan katalog ``discovered_patterns`` yang sudah ada, dan memilih **satu** -
yang paling spesifik.

Dua penyaring, dan keduanya berasal dari pengukuran 2026-08-21:

* **Hanya yang mengalahkan baseline.** Dari 368 pola, hanya **57** yang
  ``beats_baseline``. Sebuah pola yang tidak lebih baik daripada tebakan dasar
  bukan temuan - ia derau yang sudah diberi nama, dan namanya membuatnya
  terdengar seperti temuan.
* **Hanya yang sampelnya cukup.** Alasan yang sama dengan
  :data:`aruna.memory.outcome.SAMPEL_MINIMUM`.

Dan pasalnya menutup dengan kalimatnya sendiri: *"Pattern Memory bukan
prediction otomatis."* Tidak ada bidang di sini yang bisa dibaca sebagai arah.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aruna.memory.lookup import simbol_pasar

#: Sampel minimum sebuah pola sebelum ia boleh dibaca sebagai konteks.
#:
#: Lima puluh, lebih tinggi daripada ambang kasus serupa: sebuah pola adalah
#: klaim tentang **keteraturan**, dan keteraturan yang hanya terlihat pada dua
#: puluh kejadian belum bisa dibedakan dari kebetulan.
SAMPEL_POLA = 50

#: Dimensi pola -> cara membacanya dari kondisi sekarang. Dieja supaya kunci
#: baru di Phase 12 tidak diam-diam dianggap cocok karena tidak dikenali.
_DIMENSI = ("symbol", "horizon", "direction")


@dataclass(frozen=True, slots=True)
class Pola:
    """Satu temuan Phase 12, sebagaimana adanya.

    Tidak punya bidang prediksi, dan tidak akan pernah punya - PASAL 15.16.
    """

    kunci: str
    dimensi: dict[str, str]
    sampel: int
    win_rate: float
    ci: tuple[float, float]
    beats_baseline: bool

    @property
    def kekhususan(self) -> int:
        """Berapa dimensi yang pola ini sebut. Lebih banyak = lebih tepat."""
        return len(self.dimensi)

    def ringkas(self) -> str:
        """Satu baris untuk operator.

        Menyebut jumlah sampel dan sumbernya: win rate tanpa sampel adalah
        angka yang tidak bisa dinilai, dan tanpa sumbernya operator tidak tahu
        itu temuan Phase 12 - bukan hitungan yang baru saja dikarang.
        """
        return (
            f"{self.kunci} — {round(self.win_rate * 100)}% dari "
            f"{self.sampel} kasus (Phase 12)"
        )


def dari_baris(row: dict[str, Any]) -> Pola | None:
    """Satu baris ``discovered_patterns`` menjadi :class:`Pola`.

    Memulangkan ``None`` kalau kolom ``dimensions`` tidak bisa dibaca: kolom
    yang formatnya berubah adalah kegagalan pembacaan, bukan alasan untuk
    menjatuhkan seluruh keputusan yang membawanya.
    """
    try:
        dimensi = json.loads(str(row.get("dimensions") or "{}"))
        if not isinstance(dimensi, dict):
            return None
        return Pola(
            kunci=str(row.get("pattern_key") or ""),
            dimensi={str(k): str(v) for k, v in dimensi.items()},
            sampel=int(row.get("sample_size") or 0),
            win_rate=float(row.get("win_rate") or 0.0),
            ci=(float(row.get("ci_low") or 0.0), float(row.get("ci_high") or 0.0)),
            beats_baseline=bool(row.get("beats_baseline")),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def cocokkan(
    katalog: Sequence[Pola], *, symbol: object, timeframe: object, arah: object
) -> Pola | None:
    """Pola paling spesifik yang menerangkan kondisi sekarang, atau ``None``.

    Sebuah pola cocok kalau **setiap** dimensi yang ia sebut sesuai dengan
    kondisi sekarang. Dimensi yang tidak ia sebut tidak menghalangi: pola
    ``horizon=1h`` memang berlaku untuk simbol apa pun di 1h - itu artinya.

    Yang paling spesifik menang meskipun sampelnya lebih kecil: pola tiga
    dimensi menerangkan kondisi ini lebih tepat daripada pola satu dimensi,
    dan ambang :data:`SAMPEL_POLA` yang menjaga agar "lebih tepat" tidak
    berarti "hampir tidak pernah terjadi".
    """
    sekarang = {
        "symbol": simbol_pasar(symbol),
        "horizon": str(getattr(timeframe, "value", timeframe) or ""),
        "direction": str(getattr(arah, "value", arah) or "").upper(),
    }

    cocok = [
        p for p in katalog
        if p.beats_baseline
        and p.sampel >= SAMPEL_POLA
        and p.dimensi
        and all(
            k in _DIMENSI and str(v).upper() == sekarang.get(k, "").upper()
            for k, v in p.dimensi.items()
        )
    ]
    if not cocok:
        return None
    # Paling spesifik dulu; pada kekhususan sama, sampel terbesar menang.
    return max(cocok, key=lambda p: (p.kekhususan, p.sampel))


__all__ = ["SAMPEL_POLA", "Pola", "cocokkan", "dari_baris"]
