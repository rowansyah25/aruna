"""Jangan mengulang diri, dan jangan balas dendam (PASAL 11.5, 11.6).

Dua penjaga yang gunanya berlawanan arah dan kegagalannya sama-sama mahal.

**Duplikat (11.6).** Satu setup yang bertahan empat jam pada bar 15 menit
lolos enam belas kali. Enam belas pesan tentang satu gagasan bukan enam belas
kali lebih berguna - ia melatih pembacanya mengabaikan notifikasi ARUNA, tepat
sebelum yang ketujuh belas ternyata penting.

**Cooldown sesudah kalah (11.5).** Sesudah stop kena, harga yang bergerak
sedikit ke arah semula membuat setiap indikator berbunyi lagi. Signal
berikutnya yang lahir dari situ bukan analisis baru; ia analisis yang sama
yang baru saja terbukti salah, diterbitkan ulang karena pasar bergerak.

Tiga sifat yang membentuk keduanya.

**Penindasan selalu punya alasan tertulis.** Penjaga yang diam-diam menelan
signal yang sebenarnya baru lebih berbahaya daripada kebisingan yang
dicegahnya: yang hilang tidak meninggalkan jejak, dan tidak ada yang bisa
menemukan bahwa ia hilang. Setiap penolakan di sini mengembalikan alasannya,
dan alasan itu ikut tersimpan di ``signals.withheld_reason``.

**Ambangnya relatif terhadap harga, bukan angka mutlak.** Selisih lima puluh
dolar besar untuk XRP dan tidak terlihat untuk BTC.

**Cooldown diukur dalam satuan horizon, bukan jam.** Prediksi 15 menit yang
dibungkam empat jam kehilangan enam belas peluang karena satu kekalahan; itu
bukan kehati-hatian, itu kelumpuhan.

**Dan cooldown bisa dilangkahi.** PASAL 11.5 menyebutnya eksplisit: perubahan
pasar yang sangat signifikan tidak boleh terhalang. Yang dihitung signifikan
di sini hanya hal yang bisa diamati - arah berbalik, atau rezim pasar berganti
- bukan skor kesignifikanan yang dikarang, yang akan berubah menjadi jalan
pintas untuk melewati penjaga ini setiap kali angkanya kebetulan cocok.

**ARUNA MENGANALISIS SAJA.** Yang ditahan di sini adalah pesan dan catatan,
bukan order - tidak ada order yang pernah dikirim (PASAL 11 pembuka).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

#: Perubahan harga di bawah ini dianggap setup yang sama (PASAL 11.6).
#: Relatif terhadap harga acuan, bukan angka mutlak.
MATERIAL_MOVE_PCT = 0.5

#: Panjang cooldown dasar, dalam satuan horizon (PASAL 11.5).
BASE_COOLDOWN_HORIZONS = 1.0

#: Batas atas, supaya satu kekalahan besar tidak membungkam sebuah simbol
#: sepanjang hari. Kehati-hatian yang tidak punya ujung adalah kelumpuhan.
MAX_COOLDOWN_HORIZONS = 4.0

#: Kerugian sebesar ini (persen) menghasilkan cooldown maksimum.
SEVERE_LOSS_PCT = 3.0


@dataclass(frozen=True, slots=True)
class Repeat:
    """Apakah kandidat ini pengulangan, dan kenapa."""

    duplicate: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"duplicate": self.duplicate, "reasons": list(self.reasons)}


def _pct_move(before: Any, after: Any) -> float | None:
    """Perubahan relatif terhadap harga sebelumnya, dalam persen."""
    if before is None or after is None:
        return None
    try:
        lama = float(before)
        baru = float(after)
    except (TypeError, ValueError):
        return None
    if lama == 0:
        return None
    return abs(baru - lama) / abs(lama) * 100


def is_duplicate(
    previous: Any,
    candidate: Any,
    *,
    material_pct: float = MATERIAL_MOVE_PCT,
    structure_changed: bool = False,
) -> Repeat:
    """Apakah kandidat ini mengulang prediksi yang masih berjalan (PASAL 11.6).

    ``previous`` adalah prediksi terpublikasi yang **masih terbuka** untuk
    simbol dan horizon yang sama. Kalau tidak ada, tidak ada yang bisa
    diulang - dan itu penting: penjaga yang menganggap ketiadaan sebagai
    "sama" akan membungkam simbol itu selamanya.
    """
    if previous is None:
        return Repeat(False, ("tidak ada prediksi terbuka untuk dibandingkan",))

    alasan: list[str] = []

    arah_lama = getattr(previous, "direction", None)
    arah_baru = getattr(candidate, "direction", None)
    if arah_lama != arah_baru:
        return Repeat(False, (f"arah berubah: {arah_lama} -> {arah_baru}",))

    if structure_changed:
        return Repeat(False, ("struktur pasar berubah",))

    for label, lama, baru in (
        ("entry", getattr(previous, "reference_price", None),
         getattr(candidate, "reference_price", None)),
        ("target", getattr(previous, "target_price", None),
         getattr(candidate, "target_price", None)),
        ("stop", getattr(previous, "stop_price", None),
         getattr(candidate, "stop_price", None)),
    ):
        gerak = _pct_move(lama, baru)
        if gerak is None:
            continue
        if gerak >= material_pct:
            return Repeat(False, (f"{label} bergeser {gerak:.2f}%",))
        alasan.append(f"{label} bergeser {gerak:.2f}%")

    if not alasan:
        # Tidak satu pun level bisa dibandingkan. Itu BUKAN bukti kesamaan,
        # dan memperlakukannya begitu akan menindas signal yang mungkin
        # sangat berbeda hanya karena harganya tidak tersimpan.
        return Repeat(False, ("tidak ada level yang bisa dibandingkan",))

    alasan.insert(0, f"arah sama ({arah_baru})")
    return Repeat(True, tuple(alasan))


@dataclass(frozen=True, slots=True)
class Cooldown:
    """Sampai kapan simbol ini diistirahatkan, dan kenapa."""

    until: datetime | None
    reason: str = ""
    #: Horizon yang dipakai menghitung, untuk dibaca manusia.
    horizons: float = 0.0

    def active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def to_dict(self) -> dict[str, Any]:
        return {
            "until": self.until.isoformat() if self.until else None,
            "reason": self.reason,
            "horizons": round(self.horizons, 2),
        }


def cooldown_after_loss(
    *,
    lost_at: datetime | None,
    horizon_sec: float,
    loss_pct: float | None = None,
    volatility: float | None = None,
) -> Cooldown:
    """Berapa lama menunggu sesudah kalah (PASAL 11.5).

    Diukur dalam satuan horizon, bukan jam: prediksi 15 menit yang dibungkam
    empat jam kehilangan enam belas peluang karena satu kekalahan.

    Dua hal memperpanjangnya, keduanya terukur:

    * **Beratnya kerugian.** Stop yang terlewat jauh berarti pasar bergerak
      lebih keras daripada yang diperhitungkan, dan pandangan yang sama
      kemungkinan besar masih salah beberapa saat lagi.
    * **Volatilitas.** Pasar yang bergerak liar menghasilkan sinyal palsu lebih
      cepat, jadi jeda yang sama memberi lebih sedikit perlindungan.

    Ada batas atas. Kehati-hatian yang tidak punya ujung adalah kelumpuhan.
    """
    if lost_at is None or horizon_sec <= 0:
        return Cooldown(None, "tidak ada kekalahan tercatat")

    faktor = BASE_COOLDOWN_HORIZONS
    catatan = [f"dasar {BASE_COOLDOWN_HORIZONS:g} horizon"]

    if loss_pct is not None and loss_pct > 0:
        berat = min(loss_pct / SEVERE_LOSS_PCT, 1.0)
        faktor += berat * (MAX_COOLDOWN_HORIZONS - BASE_COOLDOWN_HORIZONS)
        catatan.append(f"rugi {loss_pct:.2f}%")

    if volatility is not None and volatility > 1.0:
        faktor *= min(float(volatility), 2.0)
        catatan.append(f"volatilitas {float(volatility):.2f}x")

    faktor = min(faktor, MAX_COOLDOWN_HORIZONS)
    return Cooldown(
        until=lost_at + timedelta(seconds=horizon_sec * faktor),
        reason=", ".join(catatan),
        horizons=faktor,
    )


def cooldown_overridden(
    *,
    lost_direction: Any,
    candidate_direction: Any,
    lost_regime: Any = None,
    candidate_regime: Any = None,
) -> tuple[bool, str]:
    """Apakah pasar berubah cukup untuk melangkahi cooldown (PASAL 11.5).

    Hanya dua hal yang dihitung, dan keduanya diskret serta bisa diamati:

    * **Arah berbalik.** Pasar baru saja membuktikan pandangan lama salah;
      pandangan sebaliknya adalah informasi baru, bukan pengulangan.
    * **Rezim berganti.** Setup yang gagal di pasar menyamping adalah setup
      yang berbeda dari setup di pasar yang baru saja mulai tren.

    Sengaja **tidak** ada "skor kesignifikanan". Angka semacam itu akan
    berubah menjadi jalan pintas untuk melangkahi penjaga ini setiap kali ia
    kebetulan cocok, dan tidak ada yang bisa membantahnya karena tidak ada
    yang bisa memeriksanya.
    """
    if lost_direction != candidate_direction:
        return True, f"arah berbalik: {lost_direction} -> {candidate_direction}"
    if (
        lost_regime is not None
        and candidate_regime is not None
        and lost_regime != candidate_regime
    ):
        return True, f"rezim berganti: {lost_regime} -> {candidate_regime}"
    return False, ""


__all__ = [
    "BASE_COOLDOWN_HORIZONS",
    "MATERIAL_MOVE_PCT",
    "MAX_COOLDOWN_HORIZONS",
    "SEVERE_LOSS_PCT",
    "Cooldown",
    "Repeat",
    "cooldown_after_loss",
    "cooldown_overridden",
    "is_duplicate",
]
