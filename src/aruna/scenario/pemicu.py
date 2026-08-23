"""Kapan simulasi dibangunkan (bagian 16.2).

Bagian 16.2 dibuka dengan larangan, bukan dengan daftar: *"JANGAN menjalankan
MiroFish pada setiap market scan."* Daftar tiga belas peristiwanya adalah
pengecualian terhadap larangan itu - jadi keadaan bawaan modul ini adalah
**diam**, dan tiap pemicu harus membuktikan dirinya.

**Tidak ada satu angka baru pun di sini.** Bagian 32 Phase 15 melarang ambang
yang dipas-paskan ke data, dan ini persis tempat ambang karangan akan tumbuh:
tiga belas peristiwa, masing-masing menggoda satu konstanta yang "kelihatan
pas". Semua yang dipakai di bawah sudah hidup di tempat lain, sudah diukur, dan
sudah menanggung akibatnya di jalur produksi:

============================  =================================================
Pemicu                        Ambang yang dipinjam
============================  =================================================
volume ekstrem                :data:`~aruna.analysis.regime.ANOMALY_VOLUME_RATIO`
volatilitas abnormal          :data:`~aruna.analysis.regime.HIGH_VOL_RASIO` (lewat regime)
anomali funding               :data:`~aruna.futures.funding.EXTREME_RATE`
anomali open interest         :data:`~aruna.futures.openinterest.SIGNIFICANT_PCT`
selisih pendapat antar-agent  :data:`~aruna.council.protest.HIGH_DISAGREEMENT`
ketidakpastian tinggi         :data:`~aruna.signals.quality.MIN_QUALITY`
breakout / breakdown besar    ambang pemindai sendiri, dikali :data:`AMBANG_BESAR`
============================  =================================================

Satu-satunya angka yang lahir di berkas ini adalah :data:`AMBANG_BESAR`, dan ia
sengaja **bukan** hasil pengukuran - lihat catatannya.

**Ketiga belas pemicu punya sumber data sejak 2026-08-23.** Dua yang terakhir -
``KONFLIK_LINTAS_PASAR`` dan ``LONJAKAN_LIKUIDASI`` - tidak dihidupkan dengan
menunggu sumber yang tidak ada, melainkan dengan menemukan bacaan yang datanya
sudah tersimpan: aset yang bergerak melawan kohortnya, dan gerak keras yang
dibarengi open interest menyusut. Keduanya ditulis lengkap di anggotanya masing-
masing supaya tidak ada yang mengira bacaan harfiahnya sudah terpenuhi.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from aruna.analysis.regime import ANOMALY_VOLUME_RATIO
from aruna.core.enums import Regime
from aruna.council.protest import HIGH_DISAGREEMENT
from aruna.futures.funding import EXTREME_RATE
from aruna.futures.openinterest import SIGNIFICANT_PCT
from aruna.scanner.events import EventKind, SignificantEvent
from aruna.signals.quality import MIN_QUALITY

__all__ = [
    "AMBANG_BESAR",
    "AMBANG_SELISIH_TAJAM",
    "MINIMUM_ORDE_DUA",
    "TANPA_SUMBER_DATA",
    "KonteksPemicu",
    "Peristiwa",
    "deteksi",
    "layak_simulasi",
]


#: Berapa kali melewati ambang pemindai sebelum sebuah break disebut *besar*.
#:
#: Bagian 16.2 minta "major breakout", bukan "breakout". Pemindai sudah menarik
#: garisnya sendiri di 0,25 ATR dan menormalkan tiap peristiwa ke garis itu
#: (:class:`~aruna.scanner.events.SignificantEvent`), jadi yang dibutuhkan di
#: sini cuma pengali terhadap garis yang sudah ada - bukan ambang kedua yang
#: hidup sendiri dan bisa melenceng dari yang pertama.
#:
#: **Dua, dan angka ini kebijakan, bukan pengukuran.** Ia tidak dicocokkan ke
#: hasil apa pun - dicocokkan ke hasil justru yang dilarang bagian 32 Phase 15.
#: Ia menyatakan satu hal yang bisa dibantah dengan kalimat: dua kali melewati
#: garis yang sudah menyebut sesuatu "signifikan" itu "besar". Kalau kelak
#: terukur bahwa simulasi berguna pada 1,5x, ubah di sini - satu tempat.
AMBANG_BESAR = 2.0

#: Selisih pendapat yang disebut **tajam** oleh bagian 16.2.
#:
#: Dua kali :data:`~aruna.council.protest.HIGH_DISAGREEMENT`, dan penggandanya
#: yang perlu dijelaskan - bukan angkanya.
#:
#: Versi pertama memakai `HIGH_DISAGREEMENT` apa adanya, dengan alasan yang
#: benar tapi untuk pertanyaan yang salah: ia memang ambang terukur milik
#: council, tapi ia menjawab "kapan ronde adversarial layak digelar", bukan
#: "kapan selisihnya luar biasa". Terukur 2026-08-22 atas 2.527 sesi dalam dua
#: puluh empat jam: median 0,29, dan ambang 0,40 menyaring **37%** sesi. Sepertiga
#: dari semua keputusan bukan "strong disagreement" - dan pemicu yang menyala
#: sesering itu membatalkan bagian 16.2 alih-alih memenuhinya.
#:
#: Digandakan, bukan diganti angka lepas: satu sumber tetap satu sumber, dan
#: kalau council menggeser ambangnya, yang ini ikut bergeser. Pada data yang
#: sama, dua kali ambang itu menyaring 11% sesi.
AMBANG_SELISIH_TAJAM = HIGH_DISAGREEMENT * 2

#: Berapa pemicu lain yang harus menyala bersamaan sebelum efek orde-dua
#: dianggap mungkin (bagian 16.2 butir terakhir, bagian 16.8).
#:
#: Dua: efek orde-dua adalah akibat dari akibat, dan satu peristiwa tunggal
#: belum punya yang kedua untuk berinteraksi dengannya.
MINIMUM_ORDE_DUA = 2


class Peristiwa(StrEnum):
    """Tiga belas pemicu bagian 16.2, satu per butir dan tidak lebih.

    Nilainya data yang akan tersimpan; jangan diterjemahkan lagi setelah ini.
    Nama bagian 16.2 aslinya ditulis di tiap komentar supaya daftar ini bisa
    diadu langsung dengan spec-nya tanpa menebak padanan.
    """

    #: major news
    BERITA_BESAR = "BERITA_BESAR"
    #: major breakout
    BREAKOUT_BESAR = "BREAKOUT_BESAR"
    #: major breakdown
    BREAKDOWN_BESAR = "BREAKDOWN_BESAR"
    #: abnormal volatility
    VOLATILITAS_ABNORMAL = "VOLATILITAS_ABNORMAL"
    #: extreme volume
    VOLUME_EKSTREM = "VOLUME_EKSTREM"
    #: funding anomaly
    ANOMALI_FUNDING = "ANOMALI_FUNDING"
    #: open interest anomaly
    ANOMALI_OPEN_INTEREST = "ANOMALI_OPEN_INTEREST"
    #: liquidation spike, dibaca sebagai **penutupan paksa**: gerak harga yang
    #: keras bersamaan dengan open interest yang MENYUSUT.
    #:
    #: Bacaan harfiahnya - daftar order likuidasi dari venue - tidak tersedia:
    #: Binance menarik endpoint REST-nya, dan stream ``forceOrder`` di jaringan
    #: ini menerima koneksi tanpa mengirim satu pun pesan. Menunggu bacaan itu
    #: berarti membiarkan pemicunya mati selamanya.
    #:
    #: Yang dipakai bacaan yang datanya ADA dan maknanya sama. Uang baru MEMBUKA
    #: posisi, uang yang lari MENUTUPNYA - jadi gerak besar yang dibarengi OI
    #: turun berarti yang menggerakkannya adalah posisi yang keluar. ARUNA sudah
    #: memakai pembacaan itu di
    #: :data:`~aruna.futures.openinterest.EXHAUSTION`; di sini ia dibaca oleh
    #: pemicu, bukan ditemukan ulang.
    #:
    #: Dua arah: long yang terlempar saat harga jatuh, dan short yang tertekan
    #: saat harga melesat. Keduanya penutupan paksa, dan memilih satu berarti
    #: menyelundupkan arah ke dalam pemicu.
    LONJAKAN_LIKUIDASI = "LONJAKAN_LIKUIDASI"
    #: major market regime change
    PERUBAHAN_REGIME = "PERUBAHAN_REGIME"
    #: strong disagreement antar-agent
    SELISIH_PENDAPAT_TAJAM = "SELISIH_PENDAPAT_TAJAM"
    #: uncertainty tinggi
    KETIDAKPASTIAN_TINGGI = "KETIDAKPASTIAN_TINGGI"
    #: cross-market conflict, dibaca sebagai **aset yang bergerak melawan
    #: kohortnya**.
    #:
    #: Bacaan harfiahnya - CRYPTO melawan IDX pada satu titik waktu - hampir
    #: tidak pernah tersedia: IDX tutup saat sebagian besar pemindaian crypto
    #: berjalan, jadi kedua pasar jarang punya bacaan yang sezaman. Menunggu
    #: bacaan itu berarti membiarkan pemicunya mati selamanya.
    #:
    #: Yang dipakai bacaan yang datanya ADA dan maknanya sama: sebuah aset yang
    #: menembus ke bawah sementara kohortnya menembus ke atas sedang berkonflik
    #: dengan pasarnya. Perbedaan kedua bacaan ini ditulis di sini supaya tidak
    #: ada yang mengira yang pertama sudah terpenuhi.
    KONFLIK_LINTAS_PASAR = "KONFLIK_LINTAS_PASAR"
    #: event dengan potensi second-order effect
    EFEK_ORDE_DUA = "EFEK_ORDE_DUA"


#: Pemicu yang buktinya belum dikumpulkan. Diuji sebagai mati, bukan dihapus.
#:
#: **Kosong sejak 2026-08-23.** Ketiga belas pemicu bagian 16.2 punya sumber
#: data. Yang terakhir keluar adalah ``LONJAKAN_LIKUIDASI``, dan caranya sama
#: dengan ``KONFLIK_LINTAS_PASAR`` sehari sebelumnya: bukan dengan menunggu
#: sumber yang ditarik venue, melainkan dengan menemukan bacaan yang datanya
#: SUDAH ada di tangan.
#:
#: Binance menarik endpoint REST likuidasinya dan stream ``forceOrder`` di
#: jaringan ini menerima koneksi tanpa mengirim data. Tapi likuidasi punya
#: sidik jari yang terbaca dari dua deret yang sudah disimpan: gerak harga yang
#: keras bersamaan dengan open interest yang MENYUSUT. Uang baru membuka posisi;
#: uang yang lari menutupnya.
#:
#: Daftar ini dibiarkan berdiri walau kosong. Menghapusnya menghilangkan tempat
#: bertanya "apakah masih ada pemicu tanpa sumber", dan pertanyaan itu perlu
#: punya jawaban yang bisa diperiksa - bukan disimpulkan dari ketiadaan.
TANPA_SUMBER_DATA: frozenset[Peristiwa] = frozenset()


@dataclass(frozen=True, slots=True)
class KonteksPemicu:
    """Bacaan yang sudah dihitung di tempat lain, dikumpulkan apa adanya.

    Tiap bidang boleh kosong, dan kosong berarti **tidak diukur** - bukan nol.
    Bedanya menentukan: ``funding_rate=None`` pada aset spot adalah "pasar ini
    tidak punya funding", dan memaksanya menjadi ``0`` akan membuat tiap saham
    IDX terbaca sebagai funding yang sangat normal.
    """

    peristiwa_pindai: tuple[SignificantEvent, ...] = field(default_factory=tuple)
    regime_sekarang: Regime | None = None
    regime_sebelumnya: Regime | None = None
    disagreement: float | None = None
    #: Skor :class:`~aruna.signals.quality.QualityScore`, 0-100.
    mutu: int | None = None
    #: Berapa item berita tervalidasi yang menyentuh aset ini.
    berita_penting: int = 0
    funding_rate: Decimal | None = None
    #: Perubahan open interest dalam persen.
    perubahan_oi_pct: Decimal | None = None
    #: Arah yang sedang ditempuh KOHORTNYA: ``+1`` naik, ``-1`` turun,
    #: ``None`` kalau tidak ada mayoritas yang jelas.
    #:
    #: Dihitung dari seluruh aset yang dipindai pada siklus yang sama - lihat
    #: :func:`~aruna.upkeep.skenario._arah_kohort`. ``None`` bukan nol: pasar
    #: yang tidak punya arah mayoritas tidak bisa dilawan siapa pun.
    arah_kohort: int | None = None


def _terukur(peristiwa: SignificantEvent) -> float | None:
    """Angka mentahnya, bukan ``severity``.

    ``severity`` sudah dibagi ambang pemindai; membandingkannya lagi dengan
    :data:`ANOMALY_VOLUME_RATIO` akan mengadu rasio dengan rasio-dari-rasio.
    """
    nilai = peristiwa.evidence.get("measured")
    return float(nilai) if isinstance(nilai, int | float) else None


def _regime_berubah(sebelum: Regime | None, sekarang: Regime | None) -> bool:
    """Perubahan yang *besar*, bukan sekadar berbeda (bagian 16.2).

    ``TRENDING`` lama dan ``TRENDING_BULLISH`` baru adalah baris dari dua
    generasi taksonomi, bukan pasar yang berubah - lihat
    :attr:`~aruna.core.enums.Regime.keluarga`. Menghitungnya sebagai perubahan
    akan menyalakan pemicu ini pada tiap aset yang ingatannya ditulis sebelum
    taksonomi berarah masuk.

    Yang dihitung ada dua: keluarganya berpindah (TRENDING ke RANGING), atau
    arahnya membalik (bullish ke bearish) - dan yang kedua justru tak terlihat
    oleh keluarga, karena keduanya serumpun ``TRENDING``.

    **``UNCERTAIN`` diperlakukan sama dengan ``None``**, dan itu bukan
    pengulangan penyaring di
    :data:`~aruna.db.repositories.konteks_pemicu.TIDAK_TERBACA`. Repositori
    merapatkan RIWAYAT - yang tidak bisa dilakukan fungsi murni ini. Yang
    dijaga di sini adalah pemanggil lain: ``deteksi`` dan :class:`KonteksPemicu`
    keduanya publik, dan tanpa baris ini "RANGING lalu tidak tahu" tetap
    menyala sebagai perpindahan keluarga - karena ``UNCERTAIN.keluarga`` adalah
    dirinya sendiri.
    """
    if sebelum is None or sekarang is None:
        return False
    if Regime.UNCERTAIN in (sebelum, sekarang):
        return False
    if sebelum.keluarga is not sekarang.keluarga:
        return True
    return (
        sebelum.naik is not None
        and sekarang.naik is not None
        and sebelum.naik is not sekarang.naik
    )


def deteksi(konteks: KonteksPemicu) -> frozenset[Peristiwa]:
    """Pemicu yang menyala. Kosong pada pemindaian biasa - itu jalur normalnya.

    Bagian 16.2: *normal scan* mendapat ARUNA STANDARD ANALYSIS, dan hanya
    *event penting* yang menambahkan simulasi mendalam.
    """
    nyala: set[Peristiwa] = set()

    arah_sendiri = 0
    for e in konteks.peristiwa_pindai:
        if e.kind is EventKind.BREAKOUT:
            arah_sendiri = 1
        elif e.kind is EventKind.BREAKDOWN:
            arah_sendiri = -1

        if e.kind is EventKind.BREAKOUT and e.severity >= AMBANG_BESAR:
            nyala.add(Peristiwa.BREAKOUT_BESAR)
        elif e.kind is EventKind.BREAKDOWN and e.severity >= AMBANG_BESAR:
            nyala.add(Peristiwa.BREAKDOWN_BESAR)
        elif e.kind is EventKind.VOLUME_SPIKE:
            terukur = _terukur(e)
            if terukur is not None and terukur >= ANOMALY_VOLUME_RATIO:
                nyala.add(Peristiwa.VOLUME_EKSTREM)
        elif e.kind is EventKind.VOLATILITY_SPIKE:
            # Ambang pemindai (2,5x ATR) sudah lebih ketat daripada
            # HIGH_VOL_RASIO (1,5x). Peristiwanya ada berarti sudah lewat.
            nyala.add(Peristiwa.VOLATILITAS_ABNORMAL)
        elif e.kind is EventKind.NEWS:
            nyala.add(Peristiwa.BERITA_BESAR)

    if konteks.regime_sekarang is Regime.HIGH_VOLATILITY:
        nyala.add(Peristiwa.VOLATILITAS_ABNORMAL)

    if _regime_berubah(konteks.regime_sebelumnya, konteks.regime_sekarang):
        nyala.add(Peristiwa.PERUBAHAN_REGIME)

    if konteks.berita_penting > 0:
        nyala.add(Peristiwa.BERITA_BESAR)

    if (
        konteks.disagreement is not None
        and konteks.disagreement >= AMBANG_SELISIH_TAJAM
    ):
        nyala.add(Peristiwa.SELISIH_PENDAPAT_TAJAM)

    if konteks.mutu is not None and konteks.mutu < MIN_QUALITY:
        nyala.add(Peristiwa.KETIDAKPASTIAN_TINGGI)

    if konteks.funding_rate is not None and abs(konteks.funding_rate) >= EXTREME_RATE:
        nyala.add(Peristiwa.ANOMALI_FUNDING)

    if (
        konteks.perubahan_oi_pct is not None
        and abs(konteks.perubahan_oi_pct) >= SIGNIFICANT_PCT
    ):
        nyala.add(Peristiwa.ANOMALI_OPEN_INTEREST)

    # Penutupan PAKSA: harga bergerak keras sementara open interest MENYUSUT.
    #
    # Uang baru membuka posisi; uang yang lari menutupnya. Gerak besar yang
    # dibarengi OI turun berarti yang menggerakkannya adalah posisi yang keluar,
    # bukan posisi yang masuk - dan posisi yang keluar saat harga melawannya
    # adalah likuidasi. ARUNA sudah memakai pembacaan ini di
    # :data:`~aruna.futures.openinterest.EXHAUSTION`; yang di sini bukan konsep
    # baru, cuma konsep yang sama dibaca oleh pemicu.
    #
    # **Dua arah, bukan satu.** ``LONG_LIQUIDATION`` dan ``SHORT_COVERING``
    # keduanya penutupan paksa - yang pertama long terlempar saat harga jatuh,
    # yang kedua short tertekan saat harga melesat. Memilih satu berarti
    # menyelundupkan arah ke dalam pemicu, dan bagian 16.18 menutup itu.
    #
    # Ambangnya dipinjam dari pertanyaan yang SAMA - ``SIGNIFICANT_PCT`` memang
    # berarti "pergeseran nyata pada berapa posisi yang terbuka". Tidak ada
    # angka baru yang dikarang di sini.
    if (
        arah_sendiri
        and konteks.perubahan_oi_pct is not None
        and konteks.perubahan_oi_pct <= -SIGNIFICANT_PCT
    ):
        nyala.add(Peristiwa.LONJAKAN_LIKUIDASI)

    # Bergerak melawan kohortnya. Butuh keduanya: arah aset ini sendiri, DAN
    # arah mayoritas yang jelas untuk dilawan. Tanpa yang kedua tidak ada
    # konflik - cuma satu aset yang bergerak di pasar yang tidak ke mana-mana.
    if (
        arah_sendiri
        and konteks.arah_kohort
        and arah_sendiri != konteks.arah_kohort
    ):
        nyala.add(Peristiwa.KONFLIK_LINTAS_PASAR)

    # Diturunkan, dan sengaja terakhir: efek orde-dua adalah akibat dari
    # akibat, jadi ia tidak punya bukti sendiri - yang dimilikinya adalah
    # kehadiran beberapa pemicu lain sekaligus (bagian 16.8).
    if len(nyala) >= MINIMUM_ORDE_DUA:
        nyala.add(Peristiwa.EFEK_ORDE_DUA)

    return frozenset(nyala)


def layak_simulasi(peristiwa: Sequence[Peristiwa] | frozenset[Peristiwa]) -> bool:
    """Apakah simulasi mendalam dijalankan.

    Satu pemicu sudah cukup: daftar bagian 16.2 **sudah** merupakan saringannya
    - tiga belas keadaan yang menurut spec pantas mendapat perhatian lebih.
    Menambah saringan kedua di sini berarti menolak keadaan yang spec-nya sudah
    terima, dengan alasan yang tidak ada di spec mana pun.
    """
    return bool(peristiwa)
