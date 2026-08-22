"""Menilai skenario terhadap apa yang benar-benar terjadi (bagian 16.19).

Bagian 16.19 minta tiga putusan - Scenario Correct, Scenario Incorrect, Scenario
Partially Valid - lalu menutup dengan satu larangan yang menentukan bentuk
seluruh modul ini: *"Jangan langsung mengubah model hanya karena satu simulation
failure."*

**Dua kegagalan yang berbeda, dinilai terpisah.** Skenario bisa gagal dengan dua
cara yang menuntut tindakan berlawanan:

* **Invalidasinya terpicu.** Skenario mengatakan "batal kalau harga kembali di
  bawah resistance", dan harga kembali di bawah resistance. Ini simulasi yang
  **bekerja**: ia menyebutkan syarat batalnya, syarat itu terjadi, dan pembacanya
  sudah diperingatkan. Menghitungnya sebagai kesalahan menghukum satu-satunya
  hal yang membuat skenario bisa dipercaya.
* **Perkembangannya tidak terjadi.** Invalidasinya tidak terpicu, tapi yang
  digambarkan juga tidak muncul. Ini simulasi yang meleset.

Menyatukan keduanya menghasilkan satu angka yang naik ketika skenario berhenti
menyebutkan syarat batalnya - persis arah yang salah.

**Ambang sampel.** Tidak ada angka akurasi yang keluar sebelum
:data:`MINIMUM_DINILAI` skenario tuntas. Alasannya sama dengan PASAL 15.44:
angka dari sepuluh kasus mengukur keberuntungan, dan keberuntungan itu masuk ke
keputusan tentang apakah seluruh mesin dipakai. Ambangnya dipinjam dari sana
alih-alih dipilih ulang.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aruna.scenario.models import HasilSkenario, Skenario

__all__ = [
    "MINIMUM_DINILAI",
    "MINIMUM_TITIK",
    "Akurasi",
    "Putusan",
    "nilai_dari_pasar",
    "nilai_satu",
    "ringkas",
]


#: Berapa skenario harus tuntas sebelum ada angka akurasi yang dilaporkan.
#:
#: Dipinjam dari :data:`aruna.upkeep.manfaat.MINIMUM_TERSEDIA`, dan bukan
#: kebetulan: pertanyaannya identik - berapa kasus sebelum sebuah angka berhenti
#: mengukur keberuntungan. Dua ambang berbeda untuk pertanyaan yang sama akan
#: melenceng, dan yang melenceng akan dibela oleh angkanya sendiri.
def _minimum() -> int:
    from aruna.upkeep.manfaat import MINIMUM_TERSEDIA

    return MINIMUM_TERSEDIA


MINIMUM_DINILAI = _minimum()


@dataclass(frozen=True, slots=True)
class Putusan:
    """Nasib satu skenario, berikut sebabnya."""

    scenario_id: str
    hasil: HasilSkenario
    #: ``True`` kalau syarat invalidasinya benar-benar terjadi. Dibedakan dari
    #: ``hasil`` karena keduanya menjawab pertanyaan yang berbeda: yang ini
    #: "apakah skenarionya jujur", yang itu "apakah ia benar".
    #:
    #: ``None`` berarti **tidak bisa diperiksa**, bukan "tidak terpicu". Syarat
    #: batal yang berbunyi "berita terbantah" tidak bisa dijawab jejak harga,
    #: dan memulangkan ``False`` untuknya akan mengarang pengukuran - lihat
    #: :func:`~aruna.scenario.kerumunan.invalidasi_terpicu`.
    diinvalidasi: bool | None
    alasan: str

    @property
    def gagal_jujur(self) -> bool:
        """Salah, tapi sudah menyebutkan syarat batalnya sebelumnya.

        Ini bukan penghalusan. Skenario yang salah dan memperingatkan, dan
        skenario yang salah tanpa peringatan, menuntut tindakan berbeda: yang
        pertama mesinnya bekerja, yang kedua mesinnya meleset.

        ``None`` tidak dihitung jujur. Yang tidak diperiksa tidak boleh dibaca
        sebagai yang lulus pemeriksaan.
        """
        return self.diinvalidasi is True and self.hasil is HasilSkenario.SALAH


@dataclass(frozen=True, slots=True)
class Akurasi:
    """Rangkuman atas banyak putusan. Angkanya ``None`` sampai sampelnya cukup."""

    dinilai: int
    benar: int
    salah: int
    sebagian: int
    #: Berapa yang salah **setelah** memperingatkan lewat invalidasinya.
    diinvalidasi: int
    #: Berapa yang syarat batalnya tidak bisa diperiksa dari jejak harga.
    #:
    #: Dilaporkan berdampingan, bukan dilipat ke :attr:`diinvalidasi`: penyebut
    #: yang memuat skenario yang tidak diperiksa membuat "berapa persen yang
    #: memperingatkan" terlihat kecil karena alasan yang salah.
    tak_terperiksa: int = 0
    versi: str = "UNKNOWN"

    @property
    def cukup_sampel(self) -> bool:
        return self.dinilai >= MINIMUM_DINILAI

    @property
    def akurasi(self) -> float | None:
        """Bagian yang BENAR, atau ``None`` kalau sampelnya belum cukup.

        ``None`` dan bukan ``0.0``: SPEC 4 melarang menyamakan "belum tahu"
        dengan "buruk", dan nol yang dilaporkan akan dibaca sebagai mesin yang
        tidak pernah benar.
        """
        if not self.cukup_sampel:
            return None
        return self.benar / self.dinilai

    @property
    def akurasi_longgar(self) -> float | None:
        """BENAR ditambah SEBAGIAN. ``None`` dengan alasan yang sama.

        Dilaporkan berdampingan dengan :attr:`akurasi`, bukan menggantikannya:
        satu angka yang menghitung SEBAGIAN sebagai benar akan selalu terlihat
        lebih baik, dan yang memilih angka mana yang dikutip adalah orang yang
        sedang membela mesinnya.
        """
        if not self.cukup_sampel:
            return None
        return (self.benar + self.sebagian) / self.dinilai

    def to_dict(self) -> dict[str, Any]:
        return {
            "versi": self.versi,
            "dinilai": self.dinilai,
            "benar": self.benar,
            "salah": self.salah,
            "sebagian": self.sebagian,
            "diinvalidasi": self.diinvalidasi,
            "tak_terperiksa": self.tak_terperiksa,
            "cukup_sampel": self.cukup_sampel,
            "minimum": MINIMUM_DINILAI,
            "akurasi": self.akurasi,
            "akurasi_longgar": self.akurasi_longgar,
        }


def nilai_satu(
    skenario: Skenario,
    *,
    invalidasi_terpicu: tuple[str, ...] = (),
    perkembangan_terjadi: tuple[bool, ...] = (),
    horizon_selesai: bool = True,
) -> Putusan:
    """Nasib satu skenario terhadap pasar yang sudah bergerak.

    ``invalidasi_terpicu`` berisi syarat mana yang benar-benar terjadi -
    subhimpunan dari ``skenario.invalidasi.syarat``. ``perkembangan_terjadi``
    sejajar dengan ``skenario.perkembangan``, satu boolean per langkah rantai.

    Keduanya dioper, tidak dihitung di sini: menentukan apakah "volume bertahan
    di atas rata-rata" terjadi menuntut candle, dan modul yang menilai tidak
    boleh juga menjadi modul yang mengambil data - yang kedua membuat penilaian
    tidak bisa diuji tanpa basis data.
    """
    if not horizon_selesai:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.BELUM,
            diinvalidasi=False,
            alasan="horizon belum selesai",
        )

    if invalidasi_terpicu:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.SALAH,
            diinvalidasi=True,
            alasan=f"invalidasi terpicu: {', '.join(invalidasi_terpicu)}",
        )

    if not perkembangan_terjadi:
        # Tidak ada satu langkah pun yang bisa diperiksa. Ini bukan skenario
        # yang salah - ini penilaian yang tidak punya bahan, dan menyebutnya
        # SALAH menghukum simulasi karena datanya yang hilang.
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.BELUM,
            diinvalidasi=False,
            alasan="tidak ada langkah perkembangan yang bisa diperiksa",
        )

    terjadi = sum(1 for x in perkembangan_terjadi if x)
    total = len(perkembangan_terjadi)

    if terjadi == total:
        hasil, alasan = HasilSkenario.BENAR, f"{terjadi}/{total} langkah terjadi"
    elif terjadi == 0:
        hasil, alasan = HasilSkenario.SALAH, "tidak ada langkah yang terjadi"
    else:
        # Bagian 16.19 menyebut Scenario Partially Valid, dan rantai
        # konsekuensi (bagian 16.8) memang bisa benar separuh: dua langkah
        # pertama terjadi, yang ketiga tidak. Memaksanya menjadi benar-atau-
        # salah membuang keterangan yang paling berguna tentang di mana
        # rantainya putus.
        hasil, alasan = (
            HasilSkenario.SEBAGIAN,
            f"{terjadi}/{total} langkah terjadi",
        )

    return Putusan(
        scenario_id=skenario.scenario_id,
        hasil=hasil,
        diinvalidasi=False,
        alasan=alasan,
    )


#: Berapa titik harga minimal sebelum sebuah jalan layak diklasifikasikan.
#:
#: Empat: titik awal ditambah tiga bar. Di bawah itu `ayunan` dan `akhir`
#: mengukur satu-dua kebetulan, dan sebuah skenario yang dinilai dari dua bar
#: dihukum karena waktu belum berjalan - persis yang `BELUM` ada untuk
#: mencegahnya.
MINIMUM_TITIK = 4

#: Arah yang tersirat oleh tiap keluarga skenario.
#:
#: **Ini pembacaan, bukan penghasilan.** Bagian 16.18 melarang Phase 16
#: menghasilkan arah, dan tidak satu pun `Skenario` punya bidang arah. Yang
#: dilakukan di sini menilai sesudah faktanya: sebuah keluarga bernama "Bullish
#: Continuation" membuat klaim yang bisa meleset ke atas atau ke bawah, dan
#: menolak membaca klaim itu berarti menolak menilainya sama sekali.
#:
#: Nol berarti keluarga itu mengklaim **bentuk**, bukan arah. Bagi mereka tidak
#: ada `SEBAGIAN`: bentuknya terjadi atau tidak.
_ARAH_KELUARGA = {
    "Bullish Continuation": 1,
    "Bearish Reversal": -1,
    "False Breakout": 0,
    "High Volatility": 0,
    "Sideways": 0,
    "Liquidation Cascade": 0,
}


def nilai_dari_pasar(
    skenario: Skenario,
    *,
    jejak: tuple[float, ...],
    horizon_selesai: bool = True,
) -> Putusan:
    """Nilai satu skenario terhadap jalan harga yang **benar-benar terjadi**.

    ``jejak`` adalah harga per bar dalam satuan ATR, relatif terhadap harga saat
    skenarionya lahir - bentuk yang sama persis dengan yang dihasilkan mesin
    kerumunan. Ia dioper, tidak diambil di sini: modul ini tidak boleh punya
    kueri, supaya angkanya bisa diuji tanpa basis data.

    **Dinilai dengan klasifikator yang sama** yang menghasilkan skenarionya
    (:func:`~aruna.scenario.kerumunan.klasifikasi_jejak`). Kalau yang menilai
    memakai aturan berbeda dari yang menghasilkan, evaluasinya mengukur sesuatu
    yang lain dan angkanya tidak mengatakan apa pun tentang mesinnya.

    Tiga putusan bagian 16.19, dan garisnya:

    * **BENAR** - pasar mendarat di keluarga yang sama.
    * **SEBAGIAN** - keluarganya berbeda, tapi arah yang keluarga skenario
      klaim tetap terjadi. Hanya berlaku bagi dua keluarga yang memang
      mengklaim arah; sisanya mengklaim bentuk, dan bentuk terjadi atau tidak.
    * **SALAH** - selain itu.
    """
    from aruna.scenario.kerumunan import invalidasi_terpicu, klasifikasi_jejak

    if not horizon_selesai:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.BELUM,
            diinvalidasi=False,
            alasan="horizon belum selesai",
        )

    if len(jejak) < MINIMUM_TITIK:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.BELUM,
            diinvalidasi=False,
            alasan=f"baru {len(jejak)} titik harga, butuh {MINIMUM_TITIK}",
        )

    nyata = klasifikasi_jejak(jejak)
    akhir = jejak[-1]

    # Diperiksa untuk SETIAP putusan, bukan cuma yang SALAH. Skenario yang
    # benar sementara syarat batalnya juga terpicu adalah skenario yang
    # invalidasinya terlalu longgar - dan itu hanya terlihat kalau angkanya ada
    # pada yang BENAR juga.
    batal = invalidasi_terpicu(skenario.nama, jejak)

    if nyata == skenario.nama:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.BENAR,
            diinvalidasi=batal,
            alasan=f"pasar mendarat di {nyata}",
        )

    diklaim = _ARAH_KELUARGA.get(skenario.nama, 0)
    if diklaim and ((akhir > 0) == (diklaim > 0)) and abs(akhir) > 0:
        return Putusan(
            scenario_id=skenario.scenario_id,
            hasil=HasilSkenario.SEBAGIAN,
            diinvalidasi=batal,
            alasan=f"arahnya benar tapi bentuknya {nyata}, bukan {skenario.nama}",
        )

    peringatan = (
        "syarat batalnya terpicu"
        if batal
        else "tanpa satu pun syarat batalnya terpicu"
        if batal is False
        else "syarat batalnya tidak bisa diperiksa dari jejak"
    )
    return Putusan(
        scenario_id=skenario.scenario_id,
        hasil=HasilSkenario.SALAH,
        diinvalidasi=batal,
        alasan=f"pasar mendarat di {nyata}, bukan {skenario.nama}; {peringatan}",
    )


def ringkas(putusan: tuple[Putusan, ...], *, versi: str = "UNKNOWN") -> Akurasi:
    """Rangkum banyak putusan menjadi satu akurasi.

    ``BELUM`` tidak ikut dihitung, dan itu menentukan: skenario yang horizonnya
    belum lewat bukan skenario yang salah, dan memasukkannya ke penyebut membuat
    akurasi turun setiap kali simulasi baru dijalankan.
    """
    final = [p for p in putusan if p.hasil is not HasilSkenario.BELUM]

    return Akurasi(
        dinilai=len(final),
        benar=sum(1 for p in final if p.hasil is HasilSkenario.BENAR),
        salah=sum(1 for p in final if p.hasil is HasilSkenario.SALAH),
        sebagian=sum(1 for p in final if p.hasil is HasilSkenario.SEBAGIAN),
        diinvalidasi=sum(1 for p in final if p.diinvalidasi is True),
        tak_terperiksa=sum(1 for p in final if p.diinvalidasi is None),
        versi=versi,
    )
