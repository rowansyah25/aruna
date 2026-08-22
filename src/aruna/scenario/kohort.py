"""Siapa saja yang ada di pasar, dan bagaimana masing-masing bereaksi.

Ini bagian "kerumunan" dari mesin kerumunan. Idenya tua dan bukan milik siapa
pun: banyak pelaku sederhana yang saling bereaksi menghasilkan perilaku yang
tidak dimiliki satu pun di antaranya. Kaskade likuidasi tidak ditulis sebagai
aturan di sini - ia **muncul** ketika kohort berungkit terlempar dan aliran
paksanya menggerakkan harga cukup jauh untuk melempar sisanya.

**Kohort, bukan agent satu per satu.** Menyimulasikan sepuluh ribu pelaku
individual menghabiskan waktu untuk ketelitian yang tidak ada: yang menentukan
gerak harga adalah aliran bersih tiap kelompok, bukan identitas anggotanya.
Enam kohort selesai dalam mikrodetik dan menghasilkan bentuk yang sama.

**Pangsa dan reaktivitas di bawah adalah kebijakan, bukan pengukuran**, dan
ditulis begitu supaya tidak ada yang mengutipnya sebagai temuan. Yang bisa
dipertahankan bukan angkanya melainkan **urutan dan tandanya**: penyedia
likuiditas melawan arah, pengikut tren searah, pemegang hampir tidak bergerak.
Angka yang salah menggeser bobot; tanda yang salah membalik seluruh
kesimpulannya.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AMBANG_LIKUIDASI",
    "AMBANG_LIKUIDASI_BALIK",
    "KOHORT",
    "SATURASI",
    "Kohort",
    "aliran",
]


#: Rangsangan terbesar yang masih menambah reaksi sebuah kohort, dalam ATR.
#:
#: **Modal tiap kelompok terbatas.** Pedagang tren tidak membeli dua belas kali
#: lebih keras karena harga naik dua belas ATR - pada suatu titik ia sudah
#: sepenuhnya masuk, dan rangsangan tambahan tidak menghasilkan aliran
#: tambahan. Tanpa batas ini lingkaran umpan baliknya tidak punya titik jenuh:
#: terukur pada versi tanpa saturasi, satu lintasan berakhir di +12,54 ATR -
#: angka yang bukan ramalan melainkan bug yang terlihat seperti tren kuat.
SATURASI = 1.2


#: Seberapa jauh harga harus bergerak melawan posisi berungkit sebelum ia
#: terlempar, dalam kelipatan ATR.
#:
#: Dua: pada ungkit yang lazim dipakai ritel di perpetual, jarak ke harga
#: likuidasi memang beberapa kali rentang harian. **Kebijakan, bukan
#: pengukuran** - yang dipertahankan adalah bahwa ambangnya ADA dan dilewati
#: lebih dulu oleh sebagian, bukan oleh semua sekaligus.
AMBANG_LIKUIDASI = 2.0

#: Retracement dari titik ekstrem sebelum yang MEMBELI tembusan terlempar.
#:
#: Setengah dari :data:`AMBANG_LIKUIDASI`, dan asimetrinya disengaja: yang
#: short sebelum tembusan sudah punya ruang, sedangkan yang mengejar tembusan
#: masuk di harga terburuknya. Yang masuk belakangan terlempar lebih dulu, dan
#: itulah yang mengubah tembusan yang gagal menjadi pembalikan alih-alih
#: sekadar peluruhan.
AMBANG_LIKUIDASI_BALIK = 1.0


@dataclass(frozen=True, slots=True)
class Kohort:
    """Satu kelompok pelaku dengan satu cara bereaksi.

    ``tanda`` adalah bidang yang paling menentukan di seluruh berkas ini, dan
    satu-satunya yang tidak boleh salah: ``+1`` mengejar, ``-1`` melawan.
    Membalikkannya pada :data:`PEMBUAT_PASAR` mengubah pasar yang meredam
    menjadi pasar yang memperkuat, dan tiap lintasan akan meledak.
    """

    nama: str
    #: Pangsa aliran pasar. Seluruh kohort menjumlah 1,0.
    pangsa: float
    #: Seberapa kuat bereaksi terhadap rangsangannya.
    reaktivitas: float
    #: ``+1`` searah rangsangan, ``-1`` melawan.
    tanda: int
    #: Berapa bagian reaksinya yang datang dari gerak KUMULATIF, bukan dari
    #: gerak ronde terakhir.
    #:
    #: Nol berarti kohort ini hanya melihat tick terakhir - dan kohort yang
    #: hanya melihat tick terakhir tidak bisa menghasilkan tren, karena gerak
    #: ronde terakhir selalu meluruh. Terukur pada versi pertama: momentum nol
    #: di seluruh roster menghasilkan 28 dari 36 lintasan `Sideways` dan nol
    #: kaskade, apa pun premisnya.
    #:
    #: Pedagang tren sungguhan tidak begitu. Yang dilihatnya "harga sudah naik
    #: dua ATR dan masih naik", dan itu yang membuat tren mempertahankan diri -
    #: refleksivitas yang justru dituntut efek orde-dua bagian 16.8.
    momentum: float = 0.0
    #: Bergerak hanya setelah ambang likuidasi terlewati.
    berungkit: bool = False
    #: Bereaksi terhadap dorongan berita, bukan terhadap harga.
    digerakkan_berita: bool = False


#: Roster tetap. Enam kohort, dan tiap satu punya alasan berada di sini.
KOHORT: tuple[Kohort, ...] = (
    # Mengejar gerak terakhir. Kohort inilah yang membuat tembusan berlanjut.
    Kohort(nama="pengikut_tren", pangsa=0.26, reaktivitas=0.9, tanda=1, momentum=0.55),
    # Memudarkan jarak dari harga awal. Kohort inilah yang membuat tembusan
    # gagal, dan tanpanya tiap lintasan hanya punya satu arah.
    #
    # Reaktivitas kedua peredam di bawah **disetel supaya penyerapan NETRAL
    # benar-benar netral**: pada rangsangan satuan, jumlah peredam kira-kira
    # sama dengan jumlah pengejar. Itu bukan kosmetik. Kalau peredam menang
    # pada premis netral, premis "penyerapan lemah" cuma menghasilkan
    # peluruhan yang lebih lambat - tidak pernah lintasan yang berlari - dan
    # seluruh kisi premis mendarat di satu keluarga yang sama. Terukur pada
    # versi pertama: 36 lintasan, 34 di antaranya `Sideways`.
    Kohort(nama="pembalik", pangsa=0.22, reaktivitas=0.45, tanda=-1),
    # Menyerap ketidakseimbangan. Peredam pasar - dan peredamnya melemah persis
    # ketika kedalaman menipis, yang membuat pasar tenang dan pasar panik
    # berperilaku berbeda terhadap aliran yang sama.
    Kohort(nama="pembuat_pasar", pangsa=0.24, reaktivitas=0.55, tanda=-1),
    # Diam sampai terlempar, lalu menjual apa pun harganya. Sumber kaskade.
    Kohort(
        nama="berungkit", pangsa=0.14, reaktivitas=1.6, tanda=1, berungkit=True
    ),
    # Hampir tidak bereaksi. Ada karena tanpanya seluruh pasar reaktif, dan
    # pasar yang seluruhnya reaktif tidak pernah menghasilkan rentang sepi.
    Kohort(nama="pemegang", pangsa=0.08, reaktivitas=0.08, tanda=1),
    # Bereaksi terhadap berita, bukan terhadap harga. Diam kalau tidak ada.
    Kohort(
        nama="pemburu_berita",
        pangsa=0.06,
        reaktivitas=1.2,
        tanda=1,
        digerakkan_berita=True,
    ),
)


def _jenuh(nilai: float) -> float:
    """Rangsangan, dibatasi :data:`SATURASI`.

    Pemotongan keras, bukan tangen hiperbolik. Keduanya menjenuhkan; yang ini
    bisa dihitung di kepala saat membaca lintasannya, dan lengkungan halus
    tidak membeli apa pun di sini karena yang dihasilkan mesin ini bentuk
    lintasan, bukan harga.
    """
    return max(-SATURASI, min(SATURASI, nilai))


def aliran(
    kohort: Kohort,
    *,
    gerak_terakhir: float,
    jarak_kumulatif: float,
    ketidakseimbangan: float,
    kedalaman: float,
    dorongan_berita: float,
    paksa: float,
) -> float:
    """Aliran bersih satu kohort pada satu ronde.

    Positif menekan harga naik, negatif menekan turun. Tiap cabang di bawah
    menjawab satu pertanyaan berbeda, dan itu sebabnya kohortnya tidak bisa
    disatukan menjadi satu rumus:

    * pengikut tren bertanya "apa yang baru saja terjadi";
    * pembalik bertanya "seberapa jauh kita dari titik awal";
    * pembuat pasar bertanya "seberapa timpang aliran sekarang";
    * berungkit tidak bertanya apa-apa sampai ia terlempar.
    """
    if kohort.digerakkan_berita:
        return kohort.pangsa * kohort.reaktivitas * kohort.tanda * dorongan_berita

    if kohort.berungkit:
        # ``paksa`` bertanda, dan besarnya sudah menyusut menurut berapa posisi
        # yang tersisa untuk dilikuidasi - lihat `kerumunan.jalankan`. Nol
        # berarti belum ada yang terlempar, atau kolamnya sudah habis.
        #
        # Diam pada nol bukan karena kohort ini tidak punya pendapat, melainkan
        # karena posisinya belum tersentuh. Itulah yang membuat kaskade datang
        # mendadak alih-alih menumpuk perlahan.
        return kohort.pangsa * kohort.reaktivitas * paksa

    if kohort.nama == "pembuat_pasar":
        # Peredam. Kekuatannya sebanding dengan kedalaman yang tersisa: pada
        # buku yang menipis, penyedia likuiditas mundur - dan mundurnya mereka
        # yang membuat gerak berikutnya lebih besar dari aliran yang sama.
        return (
            kohort.pangsa
            * kohort.reaktivitas
            * kohort.tanda
            * ketidakseimbangan
            * kedalaman
        )

    if kohort.tanda < 0:
        # **Tidak dijenuhkan, dan asimetri ini yang membuat tren berhenti
        # sendiri.** Modal pengejar habis - pada suatu titik ia sudah
        # sepenuhnya masuk. Modal yang memudarkan tidak: makin jauh harga dari
        # titik awal, makin besar peluang yang dilihatnya, dan makin banyak
        # yang bersedia melawan.
        #
        # Tanpa asimetri ini lingkaran umpan baliknya tidak punya rem apa pun
        # di ujung atas: pengejar yang jenuh tetap mengalahkan peredam yang
        # ikut jenuh, selamanya, dan lintasan berlari sampai batas penjaga.
        return kohort.pangsa * kohort.reaktivitas * kohort.tanda * jarak_kumulatif

    # Gerak terakhir DAN arah yang sudah terbentuk. Suku kedua yang membuat
    # tren bisa mempertahankan diri; tanpanya reaksinya meluruh bersama gerak
    # ronde terakhir dan tidak ada tembusan yang pernah berlanjut.
    #
    # Kedua suku ini bertarung di variabel yang sama dengan `pembalik`, dan
    # itu disengaja: tarik-menarik antara yang mengejar jarak dan yang
    # memudarkannya persis yang menentukan sebuah tembusan berlari atau gagal.
    rangsangan = _jenuh(gerak_terakhir + kohort.momentum * jarak_kumulatif)
    return kohort.pangsa * kohort.reaktivitas * kohort.tanda * rangsangan
