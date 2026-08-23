"""Seberapa cocok sebuah strategi dengan rezim sekarang (bagian 17.14 - 17.15).

**Skornya bukan probabilitas profit**, dan bagian 17.4 menyatakannya. Ia
peringkat relatif di antara kandidat pada satu titik waktu - dua strategi
berskor 80 tidak berarti keduanya menang delapan dari sepuluh kali.

Empat hal masuk, dan tiap satu masuk dengan cara yang berbeda:

* **Kecocokan rezim** menambah atau mengurangi. Ini satu-satunya yang bisa
  menaikkan skor tanpa bukti historis sama sekali - dan memang harus, karena
  sesudah Task 3 seluruh slice per-rezim memulangkan ``None`` sampai baris
  berlabel router cukup banyak.
* **Keyakinan rezim dan stabilitasnya** MENSKALAKAN, tidak menambah. Rezim yang
  benar tapi tidak yakin bukan bukti yang lebih kuat daripada rezim yang salah;
  ia bukti yang lebih lemah atas hal yang sama, jadi ia menarik skor kembali ke
  netral alih-alih menggesernya.
* **Performa historis** menambah atau mengurangi, tapi hanya sesudah lolos
  gerbang sampel. Bagian 17.23 mencontohkannya sendiri: 95% dari delapan sampel
  tidak boleh mengalahkan 82% dari seribu dua ratus.
* **Risiko** hanya MENURUNKAN, tidak pernah menaikkan (bagian 17.21). Drawdown
  yang dangkal bukan prestasi - ia ketiadaan bukti bahaya, dan memberinya poin
  akan menghadiahi strategi yang belum diuji keadaan buruk.

Risiko masuk **dua kali** di Phase 17, dan keputusan itu milik operator
(2026-08-23). Di sini ia menahan strategi berisiko ekstrem NAIK ke champion; di
`putusan.lolos_gerbang` ia menahan rencana berisiko ekstrem TERBIT walau
strateginya wajar. Pertanyaannya berbeda, jadi keduanya berdiri.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.governance.proposal import MIN_VALIDATION_SAMPLE
from aruna.risk.score import RiskLevel, categorise
from aruna.router.label import SlicePerforma
from aruna.router.rezim import PetaRezim

__all__ = [
    "NETRAL",
    "Kecocokan",
    "nilai",
]


#: Skor sebuah strategi yang rezimnya tidak cocok maupun tidak bertentangan.
#:
#: Lima puluh, dan itu titik acuannya bukan nilai tengah yang kebetulan. Skor
#: di atasnya berarti ada alasan memilihnya; di bawahnya berarti ada alasan
#: menghindarinya. Strategi tanpa ``preferred_regimes`` - bentuk `Conservative`
#: di bagian 17.2 - berhenti persis di sini, dan itu jawaban yang benar.
NETRAL = 50

#: Berapa skor yang ditambahkan ketika rezim sekarang ada di preferensinya.
_COCOK = 25

#: Berapa yang dikurangi ketika TIDAK ada. Lebih kecil daripada :data:`_COCOK`,
#: dan asimetrinya disengaja: `preferred_regimes` menyatakan di mana sebuah
#: strategi DIHARAPKAN bekerja, bukan di mana ia dilarang. Menghukum seberat
#: memberi hadiah akan membuat katalog yang preferensinya ditulis sempit
#: terlihat lebih buruk daripada yang ditulis longgar - padahal yang berbeda
#: cuma cara menulisnya.
_TIDAK_COCOK = 20

#: Rentang yang bisa digeser performa historis, dalam poin skor.
#:
#: Empat puluh berarti win rate 100% menambah dua puluh dan 0% mengurangi dua
#: puluh - sebanding dengan kecocokan rezim, tidak melebihinya. Router yang
#: membiarkan performa mendominasi akan memilih strategi yang dulu menang di
#: pasar yang sudah tidak ada, dan bagian 17.57 justru menutup itu.
_RENTANG_PERFORMA = 40

#: Potongan skor menurut tingkat risiko (bagian 17.21).
#:
#: **Kelima tingkat dieja, termasuk yang potongannya nol.** Versi pertama cuma
#: memuat HIGH dan MEDIUM, dan akibatnya ``VERY_HIGH`` - tingkat paling
#: berbahaya di PASAL 13.2 - jatuh ke bawaan nol dan tidak dipotong sama
#: sekali. Peta yang tidak lengkap atas sebuah enum akan selalu diam pada
#: anggota yang lupa ditulis, dan diamnya terlihat seperti keputusan.
#:
#: ``UNKNOWN`` sengaja nol dan bukan potongan besar: ia ketiadaan penilaian,
#: bukan bahaya. Menghukumnya berarti menghukum strategi yang risikonya belum
#: sempat diukur - dan itu tiap strategi baru.
_POTONGAN_RISIKO: dict[RiskLevel, int] = {
    RiskLevel.VERY_HIGH: 35,
    RiskLevel.HIGH: 25,
    RiskLevel.MEDIUM: 10,
    RiskLevel.LOW: 0,
    RiskLevel.VERY_LOW: 0,
    RiskLevel.UNKNOWN: 0,
}


@dataclass(frozen=True, slots=True)
class Kecocokan:
    """Nilai satu strategi terhadap rezim sekarang, berikut sebabnya."""

    kode: str
    skor: int
    #: Bagian 17.6 melarang kesimpulan tanpa alasan, dan skor adalah
    #: kesimpulan. Tiap kalimat menyebut ANGKANYA, bukan cuma namanya.
    alasan: tuple[str, ...] = field(default_factory=tuple)
    sampel: int = 0
    #: ``None`` berarti drawdown-nya belum terukur, bukan bahwa ia aman.
    risiko: RiskLevel | None = None


def nilai(
    strategi: Any,
    *,
    peta: PetaRezim,
    performa: SlicePerforma | None,
    stabil: float | None,
) -> Kecocokan:
    """Skor kecocokan satu strategi. Tidak pernah melempar.

    ``stabil`` boleh ``None`` - :func:`~aruna.router.rezim.stabilitas`
    memulangkannya untuk riwayat yang terlalu pendek. Belum bisa diukur bukan
    sama dengan sangat tidak stabil, dan menyamakannya akan menghukum tiap aset
    yang baru dipantau.
    """
    alasan: list[str] = []
    skor = NETRAL

    if peta.primary is None:
        alasan.append("rezim belum terbaca - tidak ada yang bisa dicocokkan")
    elif not strategi.preferred_regimes:
        alasan.append("strategi tanpa preferensi rezim - cocok di mana pun")
    elif peta.primary in strategi.preferred_regimes:
        skor += _COCOK
        alasan.append(f"rezim {peta.primary} ada di preferensi strategi ini")
    else:
        skor -= _TIDAK_COCOK
        alasan.append(
            f"rezim {peta.primary} BUKAN preferensi strategi ini "
            f"({', '.join(strategi.preferred_regimes)})"
        )

    # Keyakinan dan stabilitas MENSKALAKAN jarak dari netral, tidak menambah.
    # Yang diskalakan selisihnya, bukan skornya, supaya keyakinan rendah
    # menarik kembali ke netral alih-alih menuju nol - dan netral memang
    # jawaban yang benar ketika buktinya lemah.
    if peta.primary is not None and skor != NETRAL:
        skala = (peta.primary_confidence / 100) * (
            1.0 if stabil is None else stabil / 100
        )
        skor = NETRAL + round((skor - NETRAL) * skala)
        alasan.append(
            f"diskalakan keyakinan rezim {peta.primary_confidence:.0f}%"
            + ("" if stabil is None else f" dan stabilitas {stabil:.0f}%")
        )

    if performa is not None:
        if performa.sample_size >= MIN_VALIDATION_SAMPLE:
            skor += round((performa.win_rate - 0.5) * _RENTANG_PERFORMA)
            alasan.append(
                f"win rate {performa.win_rate:.1%} atas "
                f"{performa.sample_size} sampel"
            )
        else:
            alasan.append(
                f"{performa.sample_size} sampel di bawah "
                f"{MIN_VALIDATION_SAMPLE} - win rate TIDAK dihitung"
            )
    else:
        alasan.append("performa per rezim belum bisa dijawab")

    risiko = None
    if performa is not None and performa.max_drawdown is not None:
        risiko = categorise(abs(float(performa.max_drawdown)) * 100)
        potong = _POTONGAN_RISIKO.get(risiko, 0)
        if potong:
            skor -= potong
            alasan.append(
                f"drawdown historis {performa.max_drawdown:.0%} - risiko "
                f"{risiko.value}, skor dipotong {potong}"
            )

    return Kecocokan(
        kode=strategi.code,
        skor=max(0, min(100, skor)),
        alasan=tuple(alasan),
        sampel=performa.sample_size if performa is not None else 0,
        risiko=risiko,
    )
