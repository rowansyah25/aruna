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

#: Menit SESUDAH rilis HIGH yang masih dianggap bergejolak.
#:
#: Diukur 2026-08-28 atas 9 peristiwa HIGH terhadap 4.985 bar sebagai garis
#: dasar - rentang bar dibagi ATR saat itu, jadi sebanding lintas jam:
#:
#:     -60..-45  1,2x     -15..-5  1,4x      0..5    2,6x  <- puncak
#:     -45..-30  0,7x      -5..0   1,1x      5..15   1,7x
#:     -30..-15  1,1x                       15..30   1,5x
#:                                          30..60   0,7x  <- sudah normal
#:
#: Gejolaknya pulih tepat di 30 menit, dan angka ini diambil dari sana.
JEDA_SESUDAH_HIGH = 30.0

#: Menit SEBELUM rilis HIGH yang sudah ditutup.
#:
#: **Angka ini TIDAK diukur dengan cara yang sama, dan itu harus dinyatakan.**
#: Tabel di atas menunjukkan gejolak sebelum rilis normal saja (0,7-1,4x) -
#: kalau yang ditanya "seberapa liar saat sinyalnya lahir", tidak ada alasan
#: menutup sama sekali.  Tapi yang menentukan bukan itu: sinyal XAU hidup 48
#: bar M5 = 4 jam, jadi yang lahir menjelang rilis akan MENAHAN spike-nya
#: dengan stop 2,5 ATR - sementara bar rilisnya sendiri median 2,6x garis dasar
#: dan pada 2026-08-28 pukul 14:00 UTC mencapai 57,77 poin = 11 ATR dalam satu
#: bar, ke DUA arah.  Stop mana pun tersapu.
#:
#: 30 menit karena itu simetris dengan sisi sesudahnya, bukan karena terukur.
#: Pengukuran yang benar - MAE 48-bar menurut jarak masuk ke rilis - gagal
#: dijalankan pada 2026-08-28 karena ForexFactory membalas 429, dan pertanyaan
#: itu sengaja ditinggalkan terbuka persis seperti berkas ini dulu meninggalkan
#: gerbangnya terbuka.
JEDA_SEBELUM_HIGH = 30.0


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
    #: Rilis HIGH terdekat di kedua arah, TERPISAH dari `berikutnya`.
    #:
    #: `berikutnya` adalah peristiwa terdekat dampak APA PUN, dan itu tidak
    #: bisa menjawab pertanyaan gerbangnya.  Terlihat di produksi 2026-08-28
    #: pukul 13:40: `berikutnya` menunjuk Chicago PMI (LOW, 5 menit lagi)
    #: sementara pidato Ketua Fed (HIGH) menunggu 20 menit kemudian.  Gerbang
    #: yang membaca `berikutnya.dampak` akan meloloskannya.
    high_berikutnya: PeristiwaEkonomi | None = None
    menit_ke_high: float | None = None
    high_terakhir: PeristiwaEkonomi | None = None
    menit_sejak_high: float | None = None

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

    high_lewat = [p for p in lewat if p.dampak is Dampak.HIGH]
    high_akan = [p for p in akan if p.dampak is Dampak.HIGH]
    high_terakhir = high_lewat[-1] if high_lewat else None
    high_berikutnya = high_akan[0] if high_akan else None

    return KonteksBerita(
        high_berikutnya=high_berikutnya,
        menit_ke_high=(
            _menit(high_berikutnya.saat, sekarang) if high_berikutnya else None
        ),
        high_terakhir=high_terakhir,
        menit_sejak_high=(
            _menit(sekarang, high_terakhir.saat) if high_terakhir else None
        ),
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


def gejolak_rilis(berita: KonteksBerita | None) -> str | None:
    """Kalimat penolakan kalau terlalu dekat rilis HIGH.  ``None`` = boleh.

    **Hanya HIGH yang menutup, dan itu terukur.**  Diukur 2026-08-28, rentang
    bar yang memuat rilis dibagi ATR saat itu, dibanding 4.974 bar biasa:

        garis dasar  0,95 ATR      LOW      1,02 ATR  (n=19)
        HIGH         4,21 ATR      MEDIUM   0,91 ATR  (n=7)

    LOW tidak bisa dibedakan dari bar biasa - jadi menutupnya akan membuang
    peluang tanpa membeli keamanan apa pun.  MEDIUM medianya juga seperti bar
    biasa; ia dibiarkan lewat dan angkanya tetap tercatat, supaya pertanyaannya
    dijawab data kelak dan bukan oleh keyakinan hari ini.

    **Kalender yang tidak terbaca TIDAK menutup, dan itu bukan kelalaian.**
    Menutup saat sumbernya diam akan menjadikan uptime sebuah API gratis
    sebagai sakelar mati - dan ForexFactory memang membalas 429 pada
    2026-08-28.  Yang benar adalah menyatakan gerbangnya TIDAK AKTIF, pola yang
    sama dengan gerbang spread; ``SinyalXau.berita_terukur`` yang membawanya ke
    laporan supaya "lolos" tidak pernah tertukar dengan "tidak diperiksa".
    """
    if berita is None or not berita.terukur:
        return None

    ke = berita.menit_ke_high
    if ke is not None and 0 <= ke <= JEDA_SEBELUM_HIGH:
        judul = berita.high_berikutnya.judul if berita.high_berikutnya else "?"
        return (
            f"{ke:.0f} menit menjelang rilis HIGH ({judul[:40]}); "
            f"bar rilis median 2,6x lebih lebar dari biasa dan sinyal ini "
            f"akan menahannya empat jam"
        )

    sejak = berita.menit_sejak_high
    if sejak is not None and 0 <= sejak <= JEDA_SESUDAH_HIGH:
        judul = berita.high_terakhir.judul if berita.high_terakhir else "?"
        return (
            f"{sejak:.0f} menit sesudah rilis HIGH ({judul[:40]}); "
            f"gejolak baru pulih di {JEDA_SESUDAH_HIGH:.0f} menit"
        )
    return None


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
    "JEDA_SEBELUM_HIGH",
    "JEDA_SESUDAH_HIGH",
    "JENDELA_PADAT",
    "NEGARA_RELEVAN",
    "Dampak",
    "KonteksBerita",
    "PeristiwaEkonomi",
    "gejolak_rilis",
    "ke_utc",
    "ringkas",
]
