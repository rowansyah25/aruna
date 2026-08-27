"""Kalender ekonomi sebagai bukti untuk XAU - netral sumber.

**Keamanan timestamp di sini bukan aturan yang ditegakkan, melainkan bentuk.**
Spec menuntut "actual hanya tersedia setelah release time".  :func:`ringkas`
menyaring seluruh peristiwa terhadap ``sekarang`` sebelum apa pun dibaca: yang
belum rilis tidak pernah menyerahkan ``actual``-nya, bahkan kalau sumbernya
sudah memuatnya karena jam sistem kita meleset.  Sebuah kebocoran masa depan
karena itu harus melewati dua kesalahan sekaligus, bukan satu.

**Dua sumber, karena masing-masing punya lubang yang ditutup yang lain.**
Diukur 2026-08-28:

* ForexFactory memberi jadwal, tingkat dampak, forecast, dan previous - tanpa
  kunci sama sekali.  Tapi ``actual`` **tidak ada di sana**: nol dari 71
  peristiwa memuatnya, dan bidangnya bahkan tidak eksis, termasuk pada 50
  peristiwa yang sudah lewat.
* FRED menerbitkan ``actual`` resmi, dan menerbitkannya memang baru sesudah
  rilis - jadi syarat spec dipenuhi sumbernya sendiri, bukan oleh janji kita.
  Ia tidak punya forecast konsensus.

Keduanya gratis.  Yang pertama tanpa pendaftaran, yang kedua butuh kunci
gratis dari fred.stlouisfed.org.

**Ini bukti, bukan gerbang.**  Tidak ada aturan "jangan sinyal menjelang NFP"
di sini.  Jarak ke peristiwa berdampak tinggi direkam bersama tiap keputusan
supaya pertanyaan "apakah sinyal XAU lebih buruk menjelang rilis" dijawab data
kelak - bukan dijawab keyakinan hari ini.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

#: Negara yang relevan untuk XAU/USD.  ``All`` dipakai ForexFactory untuk
#: peristiwa lintas-negara (mis. pertemuan G20).
NEGARA_RELEVAN: frozenset[str] = frozenset({"USD", "All"})

#: Jendela untuk menghitung kepadatan peristiwa berdampak tinggi.
JENDELA_PADAT = timedelta(hours=24)


class Dampak(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    #: Sumber tidak menyatakan dampaknya.  Bukan LOW - tidak diukur.
    TIDAK_DINYATAKAN = "TIDAK_DINYATAKAN"


@dataclass(frozen=True, slots=True)
class PeristiwaEkonomi:
    """Satu peristiwa terjadwal.  ``saat`` selalu UTC."""

    judul: str
    negara: str
    saat: datetime
    dampak: Dampak
    sumber: str
    forecast: str | None = None
    previous: str | None = None
    #: ``None`` sampai rilis.  Lihat :meth:`actual_pada`.
    actual: str | None = None

    def sudah_rilis(self, sekarang: datetime) -> bool:
        return self.saat <= sekarang

    def actual_pada(self, sekarang: datetime) -> str | None:
        """``actual`` hanya kalau peristiwanya SUDAH rilis pada ``sekarang``.

        Penjaga terakhir terhadap kebocoran masa depan.  Sebuah sumber yang
        keliru memuat ``actual`` untuk peristiwa yang belum terjadi - atau jam
        kita yang meleset - tidak cukup untuk membocorkannya; keduanya harus
        salah bersamaan.
        """
        return self.actual if self.sudah_rilis(sekarang) else None


@dataclass(frozen=True, slots=True)
class KonteksBerita:
    """Keadaan kalender pada satu keputusan."""

    berikutnya: PeristiwaEkonomi | None = None
    menit_ke_berikutnya: float | None = None
    terakhir: PeristiwaEkonomi | None = None
    menit_sejak_terakhir: float | None = None
    dampak_tinggi_24j: int = 0
    #: Sumber yang benar-benar menjawab.  Kosong berarti tidak ada kalender -
    #: keadaan yang berbeda dari "tidak ada peristiwa".
    sumber: tuple[str, ...] = ()

    @property
    def terukur(self) -> bool:
        return bool(self.sumber)


def _menit(a: datetime, b: datetime) -> float:
    return round((a - b).total_seconds() / 60, 1)


def ringkas(
    peristiwa: list[PeristiwaEkonomi],
    *,
    sekarang: datetime,
    negara: frozenset[str] = NEGARA_RELEVAN,
) -> KonteksBerita:
    """Ringkas kalender pada ``sekarang``, tanpa melihat masa depan.

    Peristiwa dibelah tegas terhadap ``sekarang``: yang sudah rilis boleh
    dibaca isinya, yang belum hanya boleh dilihat JADWAL-nya.  Itu yang
    membuat ringkasan ini aman dipakai sebagai fitur.
    """
    if not peristiwa:
        return KonteksBerita()

    relevan = [p for p in peristiwa if p.negara in negara]
    sumber = tuple(sorted({p.sumber for p in peristiwa}))
    if not relevan:
        return KonteksBerita(sumber=sumber)

    lewat = sorted(
        (p for p in relevan if p.sudah_rilis(sekarang)), key=lambda p: p.saat
    )
    akan = sorted(
        (p for p in relevan if not p.sudah_rilis(sekarang)), key=lambda p: p.saat
    )

    terakhir = lewat[-1] if lewat else None
    berikutnya = akan[0] if akan else None

    return KonteksBerita(
        berikutnya=berikutnya,
        menit_ke_berikutnya=(
            _menit(berikutnya.saat, sekarang) if berikutnya else None
        ),
        terakhir=terakhir,
        menit_sejak_terakhir=(
            _menit(sekarang, terakhir.saat) if terakhir else None
        ),
        # Dihitung pada jendela yang MELINGKUPI sekarang, bukan hanya ke depan:
        # sebuah rilis besar sejam lalu masih menggerakkan harga.
        dampak_tinggi_24j=sum(
            1
            for p in relevan
            if p.dampak is Dampak.HIGH
            and abs(p.saat - sekarang) <= JENDELA_PADAT
        ),
        sumber=sumber,
    )


def ke_utc(nilai: str) -> datetime:
    """Parse stempel waktu ISO berzona menjadi UTC.

    ForexFactory memberi offset (``-04:00``), bukan UTC. Menyimpannya apa
    adanya membuat perbandingan dengan ``as_of`` bar - yang UTC - meleset
    empat jam tanpa satu pun error.
    """
    saat = datetime.fromisoformat(nilai)
    if saat.tzinfo is None:
        saat = saat.replace(tzinfo=UTC)
    return saat.astimezone(UTC)


__all__ = [
    "JENDELA_PADAT",
    "NEGARA_RELEVAN",
    "Dampak",
    "KonteksBerita",
    "PeristiwaEkonomi",
    "ke_utc",
    "ringkas",
]
