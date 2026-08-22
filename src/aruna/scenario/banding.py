"""Membandingkan skenario satu sama lain (bagian 16.9, 16.10).

Bagian 16.9 mengeja empat hal yang dinilai atas **seluruh** skenario, bukan atas
yang terbaik saja: dominansi, konflik, risiko, kerapuhan. Kata "seluruh" itu
yang menanggung bebannya.

**Kenapa bukan yang terbaik saja.** Mengambil skenario berbobot tertinggi dan
melaporkannya adalah cara paling cepat mengubah bukti menjadi keputusan - dan
bagian 16.18 melarang persis itu. Skenario 40/35/25 dan skenario 80/12/8 punya
pemenang yang sama, tapi yang pertama adalah pasar yang tidak bisa dibaca dan
yang kedua adalah pasar yang jelas. Melaporkan keduanya sebagai "Bullish
Continuation menang" membuang satu-satunya hal yang membedakannya.

**Dominansi tipis dilaporkan sebagai konflik, bukan sebagai pemenang.** Ini
kelanjutan langsung dari alasan di atas. Selisih bobot yang lebih kecil dari
:data:`AMBANG_DOMINAN` berarti mesin ini tidak sedang menunjuk apa pun, dan
mengatakannya lebih berguna daripada menunjuk yang kebetulan unggul dua angka.

**Kerapuhan** (bagian 16.10) sudah dihitung tiap
:class:`~aruna.scenario.models.Skenario` dari jumlah syarat invalidasinya; yang
dikerjakan di sini adalah merangkumnya - berapa banyak yang berdiri di atas satu
benang, dan apakah yang paling berbobot termasuk di antaranya. Yang terakhir
paling penting: skenario dominan yang rapuh adalah keadaan paling menyesatkan
yang bisa dihasilkan mesin ini.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aruna.scenario.models import CATATAN_BOBOT, Kerapuhan, Skenario

__all__ = [
    "AMBANG_DOMINAN",
    "Perbandingan",
    "bandingkan",
]


#: Selisih bobot minimal sebelum sebuah skenario disebut dominan.
#:
#: **Kebijakan, bukan pengukuran.** Bobotnya menjumlah seratus dan tiga skenario
#: wajib membaginya, jadi bagian rata adalah sekitar tiga puluh tiga. Sepuluh
#: angka di atas pesaing terdekat berarti unggul sekitar sepertiga dari bagian
#: rata itu - cukup untuk tidak disebut seri, jauh dari cukup untuk disebut
#: kepastian. Kalau kelak terukur bahwa skenario yang unggul lima angka pun
#: sudah informatif, ubah di sini.
AMBANG_DOMINAN = 10


@dataclass(frozen=True, slots=True)
class Perbandingan:
    """Rangkuman atas seluruh skenario, bukan atas pemenangnya.

    Tidak punya bidang ``pemenang`` dan itu disengaja: nama bidang semacam itu
    akan dibaca sebagai rekomendasi oleh pembaca berikutnya, dan bagian 16.18
    menyerahkan keputusan sepenuhnya ke Phase 14. Yang ada di sini
    ``teratas`` - fakta tentang bobot, bukan anjuran.
    """

    #: Skenario dengan bobot tertinggi. ``None`` kalau tidak ada skenario.
    teratas: Skenario | None
    #: Selisih bobot antara teratas dan pesaing terdekatnya.
    jarak: int
    #: ``True`` ketika jaraknya belum mencapai :data:`AMBANG_DOMINAN`.
    konflik: bool
    #: Risiko tertinggi di antara SELURUH skenario - bukan risiko yang teratas.
    risiko: str
    #: Kerapuhan seluruh himpunan.
    kerapuhan: Kerapuhan
    #: Berapa skenario yang runtuh oleh satu syarat.
    jumlah_rapuh: int
    #: Apakah yang paling berbobot justru yang rapuh.
    teratas_rapuh: bool
    jumlah: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "teratas": self.teratas.nama if self.teratas else None,
            "jarak": self.jarak,
            "konflik": self.konflik,
            "risiko": self.risiko,
            "kerapuhan": self.kerapuhan.value,
            "jumlah_rapuh": self.jumlah_rapuh,
            "teratas_rapuh": self.teratas_rapuh,
            "jumlah": self.jumlah,
            "bobot_catatan": CATATAN_BOBOT,
        }


#: Urutan keparahan, dipakai untuk mengambil yang tertinggi.
_URUTAN_RISIKO = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def bandingkan(skenario: tuple[Skenario, ...]) -> Perbandingan:
    """Rangkuman atas seluruh himpunan (bagian 16.9).

    Himpunan kosong menghasilkan perbandingan kosong, bukan galat: bagian 16.12
    menuntut siklus tetap berjalan saat simulasi tidak menghasilkan apa-apa, dan
    lemparan di sini akan menjatuhkannya.
    """
    if not skenario:
        return Perbandingan(
            teratas=None,
            jarak=0,
            konflik=False,
            risiko="UNKNOWN",
            kerapuhan=Kerapuhan.KOKOH,
            jumlah_rapuh=0,
            teratas_rapuh=False,
            jumlah=0,
        )

    # Diurut dengan nama sebagai pemecah seri, bukan urutan datang: dua
    # skenario berbobot sama akan bertukar tempat menurut urutan mesin
    # menghasilkannya, dan `teratas` yang berubah-ubah membuat evaluasi
    # bagian 16.19 mengukur dua hal berbeda di bawah satu nama.
    urut = sorted(skenario, key=lambda s: (-s.bobot, s.nama))
    teratas = urut[0]
    jarak = teratas.bobot - urut[1].bobot if len(urut) > 1 else teratas.bobot

    rapuh = [s for s in skenario if s.kerapuhan is Kerapuhan.RAPUH]

    return Perbandingan(
        teratas=teratas,
        jarak=jarak,
        konflik=jarak < AMBANG_DOMINAN,
        # Risiko tertinggi di antara SELURUH skenario. Melaporkan risiko yang
        # teratas saja menyembunyikan skenario berisiko HIGH yang kebetulan
        # berbobot rendah - dan bobot rendah bukan alasan mengabaikan risiko,
        # karena bobot di sini bukan probabilitas (bagian 16.6).
        risiko=max(
            (s.risiko for s in skenario),
            key=lambda r: _URUTAN_RISIKO.get(r, -1),
        ),
        # Himpunan disebut RAPUH kalau ADA yang rapuh di dalamnya, bukan kalau
        # mayoritas rapuh: satu skenario bergantung-satu-benang sudah cukup
        # untuk membuat pembacaan atas himpunan itu menyesatkan.
        kerapuhan=Kerapuhan.RAPUH if rapuh else Kerapuhan.KOKOH,
        jumlah_rapuh=len(rapuh),
        teratas_rapuh=teratas.kerapuhan is Kerapuhan.RAPUH,
        jumlah=len(skenario),
    )
