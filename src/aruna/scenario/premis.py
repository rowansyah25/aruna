"""Asumsi apa yang divariasikan antar lintasan (dan kenapa bukan angka acak).

Simulasi yang menghasilkan banyak masa depan harus punya sesuatu yang berbeda
di tiap jalannya. Cara termudah adalah bilangan acak. Cara itu ditolak di sini,
dua kali, karena dua alasan yang berbeda:

**Bisa diulang.** Bagian 16.19 menilai skenario terhadap hasil pasar. Mesin yang
jawabannya berbeda tiap jalan membuat penilaian itu mustahil: skenario yang
salah minggu ini tidak bisa dibedakan dari skenario lain yang kebetulan muncul.
Generator berbenih memang menyelesaikan ini - tapi tidak menyelesaikan yang
kedua.

**Bisa dibantah.** Lintasan yang lahir dari benih 4172 tidak bisa didebat. Yang
lahir dari premis *"penyerapan lemah, kedalaman tipis, tanpa dorongan berita"*
bisa: pembacanya boleh mengatakan penyerapannya sebenarnya kuat, dan itu
percakapan tentang pasar. Premisnya kemudian mengalir langsung ke
``kondisi_awal`` dan ``invalidasi`` skenarionya - syarat pembatalnya **adalah**
premis yang terbantah.

**Digerbangi bukti.** Premis dorongan berita hanya divariasikan kalau berita
memang menyala; kedalaman tipis hanya kalau volatilitas atau volume menyala.
Memvariasikan asumsi yang tidak punya bukti adalah karangan berformat - persis
yang bagian 16.5 larang lewat "skenario tambahan hanya kalau relevan".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.scenario.pemicu import Peristiwa

__all__ = [
    "MINIMUM_LINTASAN",
    "Absorpsi",
    "Dorongan",
    "Kedalaman",
    "Premis",
    "kisi",
]


#: Lintasan paling sedikit yang harus dihasilkan kisi mana pun.
#:
#: Tiga: satu lintasan bukan simulasi melainkan satu ramalan, dan dua hanya bisa
#: mengatakan "naik atau turun" - yang sudah diketahui tanpa menyimulasikan apa
#: pun. Tiga adalah jumlah terkecil yang bisa menghasilkan "naik, turun, atau
#: tidak keduanya", dan itu juga jumlah minimum skenario bagian 16.5.
MINIMUM_LINTASAN = 3


class Absorpsi(StrEnum):
    """Apakah sisi lawan menyerap tekanan di area tembusan.

    Ini pertanyaan yang menentukan pada tiap tembusan, dan satu-satunya yang
    tidak bisa dijawab dari data yang sudah ada - jawabannya baru terlihat
    beberapa bar kemudian. Karena itu ia divariasikan, selalu.
    """

    KUAT = "KUAT"
    NETRAL = "NETRAL"
    LEMAH = "LEMAH"


class Kedalaman(StrEnum):
    """Seberapa tebal buku order yang menahan aliran."""

    NORMAL = "NORMAL"
    TIPIS = "TIPIS"


class Dorongan(StrEnum):
    """Arah dorongan berita, kalau ada beritanya."""

    TIDAK_ADA = "TIDAK_ADA"
    POSITIF = "POSITIF"
    NEGATIF = "NEGATIF"


#: Pengali kekuatan penyerapan pihak lawan.
_NILAI_ABSORPSI = {Absorpsi.KUAT: 1.35, Absorpsi.NETRAL: 1.0, Absorpsi.LEMAH: 0.6}

#: Kedalaman buku pada ronde pertama, 1,0 = normal.
_NILAI_KEDALAMAN = {Kedalaman.NORMAL: 1.0, Kedalaman.TIPIS: 0.55}

#: Besar dan arah dorongan berita.
_NILAI_DORONGAN = {
    Dorongan.TIDAK_ADA: 0.0,
    Dorongan.POSITIF: 0.5,
    Dorongan.NEGATIF: -0.5,
}


@dataclass(frozen=True, slots=True)
class Premis:
    """Satu himpunan asumsi, dan kalimat yang menjelaskannya."""

    absorpsi: Absorpsi
    kedalaman: Kedalaman
    dorongan: Dorongan

    @property
    def kekuatan_absorpsi(self) -> float:
        return _NILAI_ABSORPSI[self.absorpsi]

    @property
    def kedalaman_awal(self) -> float:
        return _NILAI_KEDALAMAN[self.kedalaman]

    @property
    def dorongan_berita(self) -> float:
        return _NILAI_DORONGAN[self.dorongan]

    @property
    def kalimat(self) -> str:
        """Premisnya dalam bahasa manusia.

        Ikut ke ``kondisi_awal`` skenario. Skenario yang tidak menyebut asumsi
        yang melahirkannya tidak bisa dibantah - dan yang tidak bisa dibantah
        bukan bukti melainkan pendapat berformat.
        """
        bagian = [
            f"penyerapan {self.absorpsi.value.lower()}",
            f"kedalaman buku {self.kedalaman.value.lower()}",
        ]
        if self.dorongan is not Dorongan.TIDAK_ADA:
            bagian.append(f"dorongan berita {self.dorongan.value.lower()}")
        return ", ".join(bagian)

    @property
    def pembatal(self) -> str:
        """Syarat yang membatalkan lintasan ini (bagian 16.11).

        Diturunkan dari premisnya, bukan ditulis terpisah: syarat pembatal
        sebuah skenario **adalah** premis yang terbantah, dan menuliskannya dua
        kali membuat keduanya bisa melenceng.
        """
        lawan = {
            Absorpsi.KUAT: "penyerapan ternyata lemah",
            Absorpsi.LEMAH: "penyerapan ternyata kuat",
            Absorpsi.NETRAL: "penyerapan bergerak tajam ke salah satu sisi",
        }[self.absorpsi]
        if self.kedalaman is Kedalaman.TIPIS:
            lawan += ", atau kedalaman buku pulih"
        return lawan


def kisi(pemicu: frozenset[Peristiwa]) -> tuple[Premis, ...]:
    """Premis yang dijalankan, digerbangi oleh bukti yang ada.

    Kisi tetap, bukan sampel. Jumlahnya tumbuh hanya ketika pemicunya
    membenarkan - dan itu membuat pasar yang tenang disimulasikan lebih murah
    daripada pasar yang bergejolak, tanpa satu pun batas buatan.
    """
    kedalaman = [Kedalaman.NORMAL]
    if (
        Peristiwa.VOLATILITAS_ABNORMAL in pemicu
        or Peristiwa.VOLUME_EKSTREM in pemicu
        or Peristiwa.LONJAKAN_LIKUIDASI in pemicu
    ):
        # Buku yang menipis butuh bukti. Menganggapnya tipis tanpa tanda apa pun
        # membuat tiap pasar terlihat rapuh.
        kedalaman.append(Kedalaman.TIPIS)

    dorongan = [Dorongan.TIDAK_ADA]
    if Peristiwa.BERITA_BESAR in pemicu:
        # Kedua arah, dan itu disengaja: berita yang diketahui ADA belum tentu
        # diketahui BAIK. Memvariasikan satu arah saja adalah tebakan arah yang
        # menyamar sebagai simulasi (bagian 16.18).
        dorongan.extend([Dorongan.POSITIF, Dorongan.NEGATIF])

    keluar = [
        Premis(absorpsi=a, kedalaman=k, dorongan=d)
        for a in Absorpsi
        for k in kedalaman
        for d in dorongan
    ]

    # Kisi terkecil sudah tiga (tiga nilai Absorpsi), jadi cabang ini tidak
    # pernah diambil hari ini. Ia tetap ada sebagai penjaga: kalau kelak
    # `Absorpsi` menyusut, satu lintasan akan lolos sebagai "simulasi" tanpa
    # satu pun test lain yang menyadarinya.
    if len(keluar) < MINIMUM_LINTASAN:  # pragma: no cover - lihat catatan
        raise ValueError(
            f"kisi menghasilkan {len(keluar)} lintasan, di bawah "
            f"{MINIMUM_LINTASAN}: satu lintasan bukan simulasi melainkan satu "
            f"ramalan"
        )

    return tuple(keluar)
