"""Mesin kerumunan: kohort bereaksi, harga muncul (bagian 16.5, 16.8).

Tiap ronde mengerjakan satu lingkaran umpan balik, dan urutannya yang penting:

1. tiap kohort menghasilkan aliran dari keadaan ronde sebelumnya;
2. aliran bersih menggerakkan harga, dibagi kedalaman yang tersisa;
3. **kedalaman menyusut** sebanding ketidakseimbangan yang baru saja lewat;
4. posisi berungkit yang tersentuh ambangnya ditandai terlempar.

Langkah 3 dan 4 yang membuat modul ini lebih dari penjumlahan. Kedalaman yang
menyusut berarti aliran yang sama menghasilkan gerak yang lebih besar pada ronde
berikutnya - itu **efek orde-dua** bagian 16.8, bukan hiasan. Dan kaskade
likuidasi tidak dijadwalkan di mana pun: ia terjadi ketika gerak yang sudah ada
melempar kohort berungkit, yang aliran paksanya menggerakkan harga lebih jauh,
yang melempar sisanya. Kalau parameternya tidak mendukung, ia tidak terjadi -
dan itu jawaban yang sah.

**Tidak ada satu bilangan acak pun**, dan tidak ada jam. Yang berbeda antar
lintasan adalah :class:`~aruna.scenario.premis.Premis`, yang dieja dalam
kalimat. Alasannya di docstring modul itu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from aruna.scenario.kohort import (
    AMBANG_LIKUIDASI,
    AMBANG_LIKUIDASI_BALIK,
    KOHORT,
    aliran,
)
from aruna.scenario.pemicu import Peristiwa
from aruna.scenario.premis import Premis, kisi

__all__ = [
    "AMBANG_ARAH",
    "AMBANG_SEPI",
    "GUNCANGAN_DASAR",
    "KELUARGA",
    "RONDE",
    "Lintasan",
    "guncangan_dari",
    "invalidasi_terpicu",
    "jalankan",
    "klasifikasi",
    "klasifikasi_jejak",
    "simulasikan_kerumunan",
]


#: Besar guncangan awal, dalam ATR.
#:
#: **Simulasi ini butuh rangsangan, dan pemicunya yang menyediakannya.** Versi
#: pertama mesin ini dimulai dari keadaan netral sempurna - harga nol, gerak
#: nol - dan setiap kohort bereaksi terhadap besaran yang semuanya nol. Hasilnya
#: titik tetap: delapan belas lintasan, semuanya rata di nol, semuanya
#: diklasifikasikan ``Sideways``. Mesinnya benar; pertanyaannya yang salah.
#:
#: Pertanyaan yang benar bukan "pasar mau ke mana" melainkan "harga baru saja
#: menembus - bagaimana kerumunan bereaksi". Guncangan itulah tembusannya.
#:
#: Setengah ATR: pemindai menyebut sebuah break signifikan pada 0,25 ATR, dan
#: `AMBANG_BESAR` menuntut dua kali ambang itu sebelum simulasi dibangunkan
#: sama sekali. Jadi guncangan terkecil yang bisa sampai ke sini memang sekitar
#: setengah ATR.
GUNCANGAN_DASAR = 0.5


#: Berapa ronde satu lintasan berjalan.
#:
#: Dua belas: cukup panjang untuk membiarkan kaskade berkembang melewati
#: beberapa tahap, cukup pendek untuk selesai dalam mikrodetik dan untuk tidak
#: berpura-pura tahu apa yang terjadi jauh di depan. Satu ronde tidak dipetakan
#: ke satuan waktu tertentu, dan itu disengaja - yang dihasilkan mesin ini
#: bentuk lintasan, bukan jadwal.
RONDE = 12

#: Gerak kumulatif, dalam ATR, sebelum sebuah lintasan disebut berarah.
AMBANG_ARAH = 0.6

#: Ayunan maksimum, dalam ATR, di bawah mana lintasan disebut sepi.
AMBANG_SEPI = 0.35

#: Batas gerak per ronde, dalam ATR. Penjaga, bukan dinamika.
#:
#: Bedanya penting dan pernah kulanggar sendiri: pada versi dengan batas 1,5
#: dan kedalaman minimum 0,15, batas ini **menggigit hampir tiap ronde** pada
#: premis ekstrem - sehingga bukan lagi penjaga melainkan penentu bentuk
#: lintasannya. Lintasan yang bentuknya ditentukan oleh penjaga bukan hasil
#: simulasi; ia hasil pemotongan.
#:
#: Penjaga yang benar jarang menggigit. Kalau ia sering menggigit, yang salah
#: dinamikanya - dan yang harus diperbaiki dinamikanya, bukan penjaganya.
_BATAS_GERAK = 0.8

#: Seberapa cepat kedalaman menyusut terhadap ketidakseimbangan.
_SUSUT_KEDALAMAN = 0.22

#: Kedalaman tidak pernah nol - buku yang benar-benar kosong membuat pembagian
#: meledak, dan pasar yang benar-benar tanpa penawaran berhenti diperdagangkan
#: alih-alih bergerak tak hingga.
_KEDALAMAN_MINIMUM = 0.3

#: Berapa bagian kolam posisi berungkit yang terlempar dalam satu ronde.
#:
#: Sepertiga: likuidasi datang bertahap - kelompok harga likuidasi tersebar,
#: bukan menumpuk di satu titik - jadi kaskade butuh beberapa ronde untuk
#: menghabiskan kolamnya. Yang penting bukan angkanya melainkan bahwa kolamnya
#: **habis**: kaskade tanpa batas bahan bakar menghasilkan angka yang tidak
#: berarti apa-apa, dan lintasan pertama yang kuukur berakhir di +12,54 ATR.
_PORSI_KASKADE = 0.34

#: Nama keluarga skenario. **Harus sama persis** dengan yang dipakai
#: `mesin.py`: nama yang meleset membuat bobot sebuah keluarga tidak pernah
#: bertemu skenarionya, dan hasilnya bobot nol tanpa satu pun test merah.
KELUARGA = (
    "Bullish Continuation",
    "Bearish Reversal",
    "False Breakout",
    "High Volatility",
    "Sideways",
    "Liquidation Cascade",
)


@dataclass(frozen=True, slots=True)
class Guncangan:
    """Peristiwa yang membangunkan simulasi, dalam bentuk yang bisa dijalankan.

    ``besar`` bertanda: positif untuk tembusan ke atas, negatif ke bawah. Ini
    **fakta yang teramati**, bukan ramalan arah - harga memang sudah bergerak
    sebelum modul ini dipanggil. Bagian 16.18 melarang Phase 16 menghasilkan
    arah; ia tidak melarangnya membaca arah yang sudah terjadi, dan simulasi
    yang menutup mata terhadapnya akan menyimulasikan pasar yang berbeda dari
    yang ada.

    Lintasan yang lahir darinya tetap boleh berbalik - itu sebabnya
    ``Bearish Reversal`` dan ``False Breakout`` ada sebagai keluarga.
    """

    besar: float
    sebab: str


@dataclass(frozen=True, slots=True)
class Lintasan:
    """Satu jalan yang mungkin, berikut premis yang melahirkannya."""

    premis: Premis
    guncangan: Guncangan
    #: Harga relatif per ronde, dalam ATR, dimulai dari 0,0.
    jejak: tuple[float, ...] = field(default_factory=tuple)
    #: Ronde saat kohort berungkit terlempar, atau ``None``.
    ronde_kaskade: int | None = None

    @property
    def akhir(self) -> float:
        return self.jejak[-1] if self.jejak else 0.0

    @property
    def puncak(self) -> float:
        return max(self.jejak) if self.jejak else 0.0

    @property
    def palung(self) -> float:
        return min(self.jejak) if self.jejak else 0.0

    @property
    def ayunan(self) -> float:
        return self.puncak - self.palung

    @property
    def kaskade(self) -> bool:
        return self.ronde_kaskade is not None


def guncangan_dari(
    pemicu: frozenset[Peristiwa], *, kekuatan: float = 1.0
) -> tuple[Guncangan, ...]:
    """Guncangan yang dijalankan, dari pemicu yang menyala.

    Ketika tidak ada pemicu berarah - perubahan regime sendirian, misalnya -
    **kedua arah dijalankan**. Memilih satu berarti mengarang arah yang tidak
    terbaca dari bukti apa pun, dan itu persis yang bagian 16.18 tutup.
    """
    besar = GUNCANGAN_DASAR * max(0.1, kekuatan)
    naik = Peristiwa.BREAKOUT_BESAR in pemicu
    turun = Peristiwa.BREAKDOWN_BESAR in pemicu

    if naik and not turun:
        return (Guncangan(besar=besar, sebab="tembusan ke atas"),)
    if turun and not naik:
        return (Guncangan(besar=-besar, sebab="tembusan ke bawah"),)

    # Keduanya, atau tidak satu pun: arahnya tidak terbaca, jadi dua-duanya
    # dijalankan dan kerumunan yang memutuskan mana yang bertahan.
    return (
        Guncangan(besar=besar, sebab="guncangan ke atas"),
        Guncangan(besar=-besar, sebab="guncangan ke bawah"),
    )


def jalankan(
    premis: Premis, guncangan: Guncangan, *, susut: float = _SUSUT_KEDALAMAN
) -> Lintasan:
    """Satu lintasan, dua belas ronde, tanpa satu pun bilangan acak.

    Ronde nol adalah guncangannya sendiri: harga sudah bergerak sebelum
    kerumunan sempat bereaksi, dan yang disimulasikan dua belas ronde
    berikutnya adalah reaksi terhadapnya.

    ``susut`` bisa dioper, dan alasannya bukan kemudahan uji melainkan
    keterujian sebuah klaim. Modul ini mengaku menghasilkan efek orde-dua
    bagian 16.8 lewat kedalaman yang menyusut; klaim itu **tidak bisa dibantah**
    kalau lajunya terkunci sebagai konstanta - dan memang tidak terbantah:
    mencabut penyusutannya sama sekali meninggalkan seluruh test hijau.
    Dengan ``susut=0`` sebagai pembanding, klaimnya bisa diadu langsung.
    """
    tanda = 1.0 if guncangan.besar >= 0 else -1.0
    harga = guncangan.besar
    kedalaman = premis.kedalaman_awal
    gerak_terakhir = guncangan.besar
    ekstrem = harga
    # Dua kolam posisi berungkit, dan keduanya terlempar oleh gerak yang
    # BERLAWANAN satu sama lain:
    #
    # * yang melawan guncangan - short pada tembusan ke atas - terlempar kalau
    #   harga terus bergerak menjauh dari titik awal. Kaskade ini mempercepat
    #   arah yang sudah ada.
    # * yang searah guncangan - long yang membeli tembusan itu sendiri -
    #   terlempar kalau harga KEMBALI. Mereka masuk di dekat puncak, jadi yang
    #   melukai mereka adalah retracement, bukan kelanjutan.
    #
    # Versi pertama hanya punya kolam pertama, dan akibatnya tidak ada satu pun
    # lintasan yang berbalik: yang beli di tembusan tidak pernah terjebak, jadi
    # tidak ada bahan bakar untuk pembalikan.
    kolam_lawan = 1.0
    kolam_searah = 1.0
    paksa = 0.0
    ronde_kaskade: int | None = None
    jejak: list[float] = [0.0, harga]

    for ronde in range(RONDE):
        # Ketidakseimbangan yang dilihat pembuat pasar adalah gerak yang baru
        # saja terjadi - ia bereaksi terhadap apa yang sudah lewat, tidak bisa
        # melihat aliran ronde ini sebelum aliran itu ada.
        # Dipisah menurut peran, bukan dijumlah sekaligus. Versi pertama
        # membagi aliran BERSIH dengan kekuatan penyerapan, dan itu cacat
        # struktural: membagi jumlah yang sudah saling meniadakan hanya
        # mengubah BESAR umpan baliknya, tidak pernah TANDANYA. Premis
        # "penyerapan lemah" jadi tidak bisa menghasilkan lintasan yang berlari
        # - peredam menang di setiap premis, dan tiga puluh enam lintasan
        # mendarat di satu keluarga yang sama.
        #
        # Penyerapan adalah sifat pihak yang MENYERAP. Dikenakan pada mereka
        # saja, premisnya bisa memiringkan pasar ke dua arah - dan itulah yang
        # membuat kisi premis berguna alih-alih dekoratif.
        mengejar = 0.0
        meredam = 0.0
        for k in KOHORT:
            nilai = aliran(
                k,
                gerak_terakhir=gerak_terakhir,
                jarak_kumulatif=harga,
                ketidakseimbangan=gerak_terakhir,
                kedalaman=kedalaman,
                dorongan_berita=premis.dorongan_berita,
                paksa=paksa,
            )
            if k.tanda < 0:
                meredam += nilai
            else:
                mengejar += nilai

        bersih = mengejar + meredam * premis.kekuatan_absorpsi

        gerak = bersih / max(kedalaman, _KEDALAMAN_MINIMUM)
        gerak = max(-_BATAS_GERAK, min(_BATAS_GERAK, gerak))

        harga += gerak
        jejak.append(harga)
        gerak_terakhir = gerak

        # Kedalaman menyusut sesudah gerak besar, dan tidak pernah pulih dalam
        # satu lintasan. Ini yang membuat ronde-ronde belakangan lebih ganas
        # daripada ronde-ronde awal pada aliran yang sama.
        kedalaman = max(_KEDALAMAN_MINIMUM, kedalaman - susut * abs(gerak))

        if tanda > 0:
            ekstrem = max(ekstrem, harga)
            lanjut = harga - 0.0
            balik = ekstrem - harga
        else:
            ekstrem = min(ekstrem, harga)
            lanjut = -harga
            balik = harga - ekstrem

        # Aliran paksa ronde ini, dan **hanya** ronde ini. Kolamnya berkurang
        # tiap kali dipakai: posisi berungkit jumlahnya terbatas, dan kaskade
        # yang tidak pernah kehabisan bahan bakar menghasilkan angka yang tidak
        # berarti apa-apa. Terukur pada versi tanpa kolam: satu lintasan
        # berakhir di +12,54 ATR.
        paksa = 0.0
        if kolam_lawan > 0 and lanjut >= AMBANG_LIKUIDASI:
            ambil = min(kolam_lawan, _PORSI_KASKADE)
            kolam_lawan -= ambil
            paksa += tanda * ambil
            if ronde_kaskade is None:
                ronde_kaskade = ronde
        if kolam_searah > 0 and balik >= AMBANG_LIKUIDASI_BALIK:
            ambil = min(kolam_searah, _PORSI_KASKADE)
            kolam_searah -= ambil
            paksa -= tanda * ambil
            if ronde_kaskade is None:
                ronde_kaskade = ronde

    return Lintasan(
        premis=premis,
        guncangan=guncangan,
        jejak=tuple(jejak),
        ronde_kaskade=ronde_kaskade,
    )


def klasifikasi(lintasan: Lintasan) -> str:
    """Keluarga skenario yang bentuk lintasan ini termasuk di dalamnya.

    Urutan pemeriksaan menentukan, dan bukan soal selera:

    * **Kaskade lebih dulu.** Lintasan yang melikuidasi setengah pasar dan
      berakhir naik tetap kaskade; melabelinya "Bullish Continuation"
      menyembunyikan satu-satunya hal yang paling perlu diketahui pembacanya.
    * **Tembusan palsu sebelum arah.** Membedakannya menuntut melihat apakah
      harga pernah naik **lalu** kembali - titik akhirnya sendiri tidak bisa
      membedakannya dari lintasan yang memang tidak ke mana-mana.
    """
    return klasifikasi_jejak(lintasan.jejak, kaskade=lintasan.kaskade)


def klasifikasi_jejak(jejak: tuple[float, ...], *, kaskade: bool = False) -> str:
    """Keluarga sebuah jalan harga, dari bentuknya saja.

    **Dipisah dari :func:`klasifikasi` supaya pasar nyata bisa dinilai dengan
    aturan yang sama persis.** Bagian 16.19 membandingkan skenario dengan hasil
    pasar; kalau yang menilai memakai aturan yang berbeda dari yang
    menghasilkan, evaluasinya mengukur sesuatu yang lain dan angkanya tidak
    mengatakan apa pun tentang mesinnya.

    Menerima ``jejak`` telanjang, bukan :class:`Lintasan`, karena jalan harga
    sungguhan tidak punya premis maupun guncangan - ia cuma punya bentuk.
    """
    if kaskade:
        return "Liquidation Cascade"
    if not jejak:
        return "Sideways"

    akhir = jejak[-1]
    puncak, palung = max(jejak), min(jejak)
    ayunan = puncak - palung

    # Pernah menembus ke satu arah lalu kembali melewati titik awal.
    if puncak >= AMBANG_ARAH and akhir <= 0:
        return "False Breakout"
    if palung <= -AMBANG_ARAH and akhir >= 0:
        return "False Breakout"

    if ayunan >= AMBANG_ARAH * 2 and abs(akhir) < AMBANG_ARAH:
        # Bergerak jauh ke dua arah tanpa menetap di salah satunya.
        return "High Volatility"

    if akhir >= AMBANG_ARAH:
        return "Bullish Continuation"
    if akhir <= -AMBANG_ARAH:
        return "Bearish Reversal"

    if ayunan <= AMBANG_SEPI:
        return "Sideways"

    # Bergerak, tapi tidak cukup jauh untuk disebut berarah dan tidak cukup
    # tenang untuk disebut sepi. Disebut apa adanya alih-alih dipaksa ke salah
    # satu tetangganya.
    return "Sideways"


#: Berapa titik berturut-turut yang berarti "bertahan satu bar penuh".
#:
#: Dua. Satu titik di bawah garis lahir adalah sentuhan; dua berturut-turut
#: adalah harga yang tinggal di sana - dan kalimat invalidasinya memang berbunyi
#: "bertahan satu bar penuh", bukan "menyentuh".
_BERTAHAN = 2

#: Berapa bar di luar rentang sebelum `False Breakout` terbantah.
#:
#: Empat, karena kalimatnya berbunyi "lebih dari tiga bar".
_DI_LUAR_LEBIH_DARI = 4


def _beruntun(jejak: tuple[float, ...], uji, berapa: int) -> bool:
    """Apakah ada ``berapa`` titik berturut-turut yang lolos ``uji``."""
    berjalan = 0
    for x in jejak:
        berjalan = berjalan + 1 if uji(x) else 0
        if berjalan >= berapa:
            return True
    return False


def invalidasi_terpicu(nama: str, jejak: tuple[float, ...]) -> bool | None:
    """Apakah syarat batal skenario ``nama`` benar-benar terjadi.

    **Ini yang membuat bagian 16.19 punya dua kegagalan, bukan satu.** Skenario
    yang salah SESUDAH memperingatkan lewat invalidasinya adalah mesin yang
    bekerja: ia menyebutkan syarat batalnya, syarat itu terjadi, dan pembacanya
    sudah diperingatkan. Skenario yang salah TANPA satu pun syarat batalnya
    terpicu adalah mesin yang meleset dan invalidasinya tidak berguna. Menyatukan
    keduanya menghasilkan satu angka yang membaik ketika skenario berhenti
    menyebutkan syarat batalnya.

    **``None`` berarti tidak bisa diperiksa dari jejak, bukan "tidak terpicu".**
    Tiap keluarga menyebut dua syarat di :func:`~aruna.scenario.mesin._invalidasi`
    dan hanya yang pertama yang berbicara tentang harga; yang kedua menyebut
    volume, kedalaman order book, atau berita - data yang tidak ada di jejak.
    Dua keluarga bahkan syarat pertamanya pun bukan tentang bentuk harga.
    Memulangkan ``False`` untuk keduanya akan mengarang pengukuran.

    Ambangnya dipinjam dari :func:`klasifikasi_jejak`, bukan dibuat baru: syarat
    batal yang memakai garis berbeda dari garis yang mendefinisikan keluarganya
    akan menjawab pertanyaan yang lain.
    """
    if not jejak:
        return None

    akhir = jejak[-1]
    puncak, palung = max(jejak), min(jejak)

    match nama:
        # "harga kembali di bawah area tembusan dan bertahan satu bar penuh"
        case "Bullish Continuation":
            return _beruntun(jejak, lambda x: x <= 0, _BERTAHAN)
        # "harga bertahan di atas area tembusan"
        case "Bearish Reversal":
            return _beruntun(jejak, lambda x: x >= 0, _BERTAHAN)
        # "harga bertahan di luar rentang lebih dari tiga bar"
        case "False Breakout":
            return _beruntun(
                jejak, lambda x: abs(x) >= AMBANG_ARAH, _DI_LUAR_LEBIH_DARI
            )
        # "rentang bar menyempit kembali ke ATR normal"
        case "High Volatility":
            langkah = tuple(abs(b - a) for a, b in pairwise(jejak))
            return bool(langkah) and _beruntun(
                langkah, lambda x: x <= AMBANG_SEPI, _BERTAHAN
            )
        # "rentang melebar melewati batas atas atau bawahnya"
        case "Sideways":
            return (puncak - palung) > AMBANG_SEPI
        # "harga berbalik sebelum menyentuh kelompok likuidasi berikutnya"
        case "Liquidation Cascade":
            return palung <= -AMBANG_ARAH and akhir >= palung + AMBANG_ARAH
        # "harga pada periode berikutnya tetap searah reaksi pertama" - efek
        # orde-dua justru TIDAK terjadi kalau arahnya tidak berubah.
        case "Second-Order Effect":
            awal = jejak[0]
            return (
                abs(awal) > 0
                and abs(akhir) >= AMBANG_ARAH
                and (akhir > 0) == (awal > 0)
            )

    # "News-Driven Reversal" jatuh ke sini: syarat batalnya berbunyi "berita
    # terbantah atau kehilangan dominansi", dan jejak harga tidak memuatnya.
    return None


def simulasikan_kerumunan(
    pemicu: frozenset[Peristiwa], *, kekuatan: float = 1.0
) -> tuple[Lintasan, ...]:
    """Seluruh kisi premis dikali seluruh guncangan, dijalankan.

    Deterministik seluruhnya: pemicu yang sama menghasilkan kisi yang sama, dan
    tiap pasangan premis-guncangan menghasilkan lintasan yang sama.

    ``kekuatan`` adalah severity peristiwanya dibagi ambangnya sendiri - angka
    yang sama yang dipakai pemindai. Tembusan yang dua kali lebih jauh melewati
    ambangnya memberi guncangan dua kali lebih besar, dan kerumunan yang sama
    bereaksi berbeda terhadapnya.
    """
    premis_semua = kisi(pemicu)
    guncangan_semua = guncangan_dari(pemicu, kekuatan=kekuatan)

    return tuple(
        jalankan(p, g) for g in guncangan_semua for p in premis_semua
    )
