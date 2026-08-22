"""Katalog strategi dan daur hidupnya (PASAL 12.7, 12.15).

Enam strategi dasar, tiap satunya menyatakan kondisi apa yang membuatnya
relevan dan rezim mana yang cocok. Katalog ini adalah **kosakata**, bukan
mesin: ia tidak memutuskan apa pun sendiri. Gunanya adalah membuat pertanyaan
"strategi mana yang berhasil" bisa ditanyakan sama sekali - tanpa nama untuk
apa yang sedang dilakukan, performa hanya bisa diukur per simbol, dan pelajaran
yang berlaku lintas simbol tidak akan pernah terlihat.

**Strategi tidak pernah dihapus (PASAL 12.15).** Yang berubah hanya statusnya.
Sebuah strategi yang dihapus membawa serta seluruh sejarah kekalahannya, dan
katalog yang hanya berisi strategi yang masih dipakai akan selalu terlihat
seperti kumpulan ide bagus - yang adalah definisi cherry picking (PASAL 11.21).

**Pemetaan ke strategi bersifat menurunkan, bukan menebak.** ``classify``
menyimpulkan strategi dari rezim dan arah yang SUDAH tersimpan pada prediksi.
Ia tidak membaca ulang pasar dan tidak menyimpulkan apa pun yang belum tercatat
saat prediksi dikunci - itu akan menjadi look-ahead, dan performa yang
dihitung darinya akan selalu terlihat lebih baik daripada yang sebenarnya
(SPEC 24).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class StrategyStatus(StrEnum):
    """Daur hidup satu strategi. Tidak ada nilai yang berarti 'dihapus'."""

    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    UNDER_REVIEW = "UNDER_REVIEW"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class Strategy:
    """Satu entri katalog."""

    code: str
    name: str
    description: str
    conditions: tuple[str, ...]
    preferred_regimes: tuple[str, ...]
    preferred_horizons: tuple[str, ...]
    status: StrategyStatus = StrategyStatus.ACTIVE
    status_reason: str | None = None

    def to_row(self, *, model_version: str, now: datetime) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "conditions": list(self.conditions),
            "preferred_regimes": list(self.preferred_regimes),
            "preferred_horizons": list(self.preferred_horizons),
            "status": self.status.value,
            "status_reason": self.status_reason,
            "model_version": model_version,
            "created_at": now,
            "updated_at": now,
        }


#: Katalog dasar. Kodenya stabil - performa historis dikunci padanya, jadi
#: mengganti kode berarti memutuskan sebuah strategi dari sejarahnya sendiri.
CATALOG: tuple[Strategy, ...] = (
    Strategy(
        code="STR-001",
        name="Trend Continuation",
        description=(
            "Ikut arah tren yang sudah berjalan, masuk pada koreksi dangkal. "
            "Bertaruh bahwa yang sedang bergerak akan terus bergerak."
        ),
        conditions=(
            "tren terbaca jelas pada timeframe yang dianalisis",
            "arah signal searah dengan tren",
        ),
        preferred_regimes=("TRENDING",),
        preferred_horizons=("1h", "4h", "1d"),
    ),
    Strategy(
        code="STR-002",
        name="Breakout",
        description=(
            "Masuk saat harga menembus batas rentang yang sudah lama bertahan. "
            "Rapuh terhadap tembusan palsu, yang jauh lebih sering daripada "
            "yang terlihat sesudah kejadian."
        ),
        conditions=(
            "harga menembus batas struktur",
            "rezim terbaca BREAKOUT",
        ),
        preferred_regimes=("BREAKOUT",),
        preferred_horizons=("15m", "1h", "4h"),
    ),
    Strategy(
        code="STR-003",
        name="Reversal",
        description=(
            "Melawan gerak yang sedang berjalan, bertaruh ia sudah habis. "
            "Strategi dengan rasio hadiah tertinggi dan tingkat keliru "
            "tertinggi; menuntut bukti pembalikan, bukan sekadar gerak yang "
            "terasa terlalu jauh."
        ),
        conditions=(
            "rezim terbaca REVERSAL",
            "arah signal berlawanan dengan gerak sebelumnya",
        ),
        preferred_regimes=("REVERSAL",),
        preferred_horizons=("1h", "4h", "1d"),
    ),
    Strategy(
        code="STR-004",
        name="Range",
        description=(
            "Berdagang di antara dua batas yang bertahan: beli di bawah, jual "
            "di atas. Berhenti bekerja pada hari rentangnya ditembus, dan hari "
            "itu tidak mengumumkan dirinya."
        ),
        conditions=(
            "rezim terbaca RANGING atau LOW_VOLATILITY",
            "harga berada dekat salah satu batas rentang",
        ),
        preferred_regimes=("RANGING", "LOW_VOLATILITY"),
        preferred_horizons=("15m", "1h"),
    ),
    Strategy(
        code="STR-005",
        name="Momentum",
        description=(
            "Ikut percepatan gerak, bukan arahnya saja. Paling rapuh terhadap "
            "biaya: geraknya harus melebihi ongkos bolak-balik sebelum ada "
            "gunanya sama sekali."
        ),
        conditions=(
            "gerak harga sedang mempercepat",
            "volume menopang geraknya",
        ),
        preferred_regimes=("TRENDING", "BREAKOUT"),
        preferred_horizons=("15m", "1h"),
    ),
    Strategy(
        code="STR-006",
        name="News Reaction",
        description=(
            "Menanggapi berita yang mengubah harga. Paling sulit dinilai: "
            "harga sering sudah bergerak sebelum beritanya terbaca, dan "
            "performa yang diukur tanpa memisahkan keduanya mengukur "
            "kecepatan feed, bukan kualitas analisis."
        ),
        conditions=(
            "ada berita berdampak pada aset ini",
            "rezim boleh apa saja",
        ),
        preferred_regimes=(),
        preferred_horizons=("15m", "1h"),
    ),
)

#: Dipakai ketika sebuah prediksi tidak cocok dengan satu pun strategi di atas.
#:
#: Ada sebagai entri sungguhan, bukan sebagai None, supaya performanya ikut
#: terukur. Kalau sebagian besar prediksi ARUNA jatuh ke sini, itu fakta
#: penting tentang katalognya - dan fakta itu hilang kalau yang tak terpetakan
#: diam-diam dibuang dari hitungan.
UNMAPPED = Strategy(
    code="STR-000",
    name="Tidak terpetakan",
    description=(
        "Prediksi yang rezimnya tidak cocok dengan satu pun strategi katalog. "
        "Bukan strategi; sebuah penampung yang keberadaannya mengukur seberapa "
        "lengkap katalog ini."
    ),
    conditions=("tidak ada strategi katalog yang cocok",),
    preferred_regimes=(),
    preferred_horizons=(),
    status=StrategyStatus.UNDER_REVIEW,
    status_reason=(
        "penampung, bukan strategi yang dipilih siapa pun; besarnya mengukur "
        "kelengkapan katalog"
    ),
)

ALL: tuple[Strategy, ...] = (*CATALOG, UNMAPPED)

def _by_regime() -> dict[str, str]:
    """Rezim -> strategi yang memilikinya. **Yang pertama menang.**

    Beberapa strategi menyukai rezim yang sama: BREAKOUT disukai Breakout
    (STR-002) dan Momentum (STR-005); TRENDING disukai Trend Continuation
    (STR-001) dan Momentum. Ambiguitas itu nyata dan harus diselesaikan oleh
    maksud, bukan oleh kebetulan.

    Versi pertama fungsi ini adalah satu dict comprehension, dan di sana yang
    TERAKHIR menang - jadi BREAKOUT jatuh ke Momentum karena STR-005 kebetulan
    ditulis di bawah STR-002 dalam katalog. Tidak ada yang memutuskan itu, dan
    tidak ada yang akan menyadarinya; performa Breakout akan tercatat sebagai
    performa Momentum selamanya.

    Sekarang urutan katalog ADALAH urutan kepemilikan, dinyatakan begitu, dan
    ada test yang memakukan hasil tiap rezim.
    """
    peta: dict[str, str] = {}
    for s in CATALOG:
        for regime in s.preferred_regimes:
            peta.setdefault(regime, s.code)
    return peta


_BY_REGIME: dict[str, str] = _by_regime()


def classify(regime: str | None, *, horizon: str | None = None) -> str:
    """Strategi mana yang paling sesuai dengan rezim yang TERCATAT.

    Memakai rezim yang tersimpan pada prediksi, bukan pembacaan baru terhadap
    pasar. Membaca ulang berarti mengukur performa masa lalu dengan pengetahuan
    yang belum ada saat keputusannya diambil.

    ``horizon`` ikut dipertimbangkan hanya untuk memisahkan dua strategi yang
    berbagi rezim yang sama; ia tidak pernah menimpa rezim.
    """
    if regime is None:
        return UNMAPPED.code
    kode = _BY_REGIME.get(regime.upper())
    if kode is None:
        return UNMAPPED.code
    # Momentum dan Trend Continuation sama-sama menyukai TRENDING; yang
    # membedakannya adalah horizon. Bukan aturan yang dalam, dan disebut
    # dangkal di sini supaya tidak ada yang mengira ia analisis.
    if regime.upper() == "TRENDING" and horizon in ("15m", "5m", "1m"):
        return "STR-005"
    return kode


def by_code(code: str) -> Strategy | None:
    return next((s for s in ALL if s.code == code), None)


__all__ = [
    "ALL",
    "CATALOG",
    "UNMAPPED",
    "Strategy",
    "StrategyStatus",
    "by_code",
    "classify",
]
