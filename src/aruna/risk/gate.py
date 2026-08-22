"""Gerbang risiko: boleh dikirim, dikirim dengan peringatan, atau ditahan.

PASAL 13.19-13.21, dan satu kalimat yang menentukan seluruh modul ini:

    Signal Quality tinggi TIDAK otomatis berarti trade layak dilakukan.

Contoh spec-nya eksplisit - quality 94/100, confidence 91%, risk score 87/100,
keputusannya ``NO SIGNAL``. Jadi kualitas dan risiko dinilai **terpisah**, dan
yang satu tidak boleh membeli izin dari yang lain. Kalau keduanya digabung
menjadi satu angka lebih dulu, setup berkualitas tinggi akan selalu bisa
menutupi risikonya sendiri, dan gerbang ini tidak akan pernah menahan apa pun.

**Ia menahan pengiriman, bukan eksekusi.** ARUNA tidak punya akses eksekusi
(PASAL 13.1), jadi "ditahan" di sini berarti satu pesan tidak dikirim -
bukan satu order dibatalkan. Yang ditahan tetap tersimpan, tetap masuk hitungan
win rate, tetap terbaca lewat ``/today``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.risk.score import Penilaian, RiskLevel


class Keputusan(StrEnum):
    """Apa yang boleh terjadi pada signal ini."""

    KIRIM = "KIRIM"
    KIRIM_DENGAN_PERINGATAN = "KIRIM DENGAN PERINGATAN"
    TAHAN = "TAHAN"


#: Apakah risiko yang tidak bisa dinilai menahan pengiriman.
#:
#: **False, dan itu diputuskan oleh pengukuran - bukan oleh prinsip.**
#:
#: Nilainya semula True, dengan alasan yang masih benar: risiko yang tidak bisa
#: dinilai bukan risiko rendah, dan mengirimnya berarti menyerahkan penilaian
#: yang ARUNA sendiri tidak sanggup lakukan kepada pembacanya.
#:
#: Lalu diukur pada jalur yang sungguhan, sebelum dipasang. Satu tick dua puluh
#: simbol: cakupan 22-36%, dan **dua puluh dari dua puluh** akan ditahan. Bukan
#: karena risikonya tinggi - karena rencana yang berakhir REFUSED atau WAIT
#: tidak pernah menghitung likuidasi, stop, atau R:R, jadi tidak ada yang bisa
#: dinilai. Memasangnya berarti Telegram yang sunyi total, dan sunyi yang tidak
#: menjelaskan dirinya lebih buruk daripada peringatan yang jujur.
#:
#: **Yang TIDAK ikut dilonggarkan, dan itu intinya:** veto faktor tunggal tetap
#: menahan walau cakupannya tipis - likuidasi yang terlalu dekat terlihat dari
#: satu faktor saja - dan skor VERY_HIGH tetap menahan. Jadi perlindungan yang
#: sesungguhnya tetap menyala; yang dilepas hanya menahan karena tidak tahu.
#:
#: Kembalikan ke True begitu cakupan pada rencana aktif konsisten di atas 60%.
#: Sampai saat itu, ini bukan kompromi prinsip - ini menolak menegakkan aturan
#: dengan alat yang belum bisa mengukur.
TAHAN_KALAU_TIDAK_BISA_DINILAI = False


@dataclass(frozen=True, slots=True)
class Vonis:
    """Keputusan gerbang, beserta alasan yang bisa dibaca operator."""

    keputusan: Keputusan
    alasan: str
    risk: Penilaian

    @property
    def boleh_kirim(self) -> bool:
        return self.keputusan is not Keputusan.TAHAN

    @property
    def perlu_peringatan(self) -> bool:
        return self.keputusan is Keputusan.KIRIM_DENGAN_PERINGATAN

    def line(self) -> str:
        return f"{self.keputusan.value}: {self.alasan}"


def evaluate(
    risk: Penilaian,
    *,
    tahan_kalau_unknown: bool = TAHAN_KALAU_TIDAK_BISA_DINILAI,
) -> Vonis:
    """Putuskan nasib satu signal dari penilaian risikonya.

    **Kualitas signal sengaja BUKAN parameter.** Ia tidak boleh masuk ke sini:
    begitu ia bisa dibaca, godaan untuk membiarkan kualitas 94 melunakkan
    risiko 87 menjadi satu baris kode yang terlihat masuk akal - dan PASAL
    13.21 ada justru untuk melarang baris itu. Gerbang ini hanya tahu risiko,
    dan ketidaktahuannya adalah fiturnya.

    Urutannya menentukan alasan mana yang dilaporkan, dan yang paling mendasar
    diperiksa lebih dulu: sebuah rencana yang diveto TIDAK perlu juga
    dijelaskan bahwa skornya tinggi.
    """
    if risk.vetoed:
        return Vonis(
            Keputusan.TAHAN,
            f"faktor yang membatalkan: {risk.vetoes[0]}",
            risk,
        )

    if not risk.usable:
        if tahan_kalau_unknown:
            return Vonis(
                Keputusan.TAHAN,
                f"risiko tidak bisa dinilai - hanya {risk.coverage:.0%} "
                "faktor terukur",
                risk,
            )
        return Vonis(
            Keputusan.KIRIM_DENGAN_PERINGATAN,
            "risiko tidak bisa dinilai; angka di bawah bukan penilaian risiko",
            risk,
        )

    if risk.level is RiskLevel.VERY_HIGH:
        return Vonis(
            Keputusan.TAHAN,
            f"risiko sangat tinggi ({risk.score:.0f}/100)",
            risk,
        )

    if risk.level is RiskLevel.HIGH:
        # PASAL 13.24: signal valid dengan risiko tinggi BOLEH dikirim, asal
        # ia dikirim sebagai signal berisiko tinggi - bukan sebagai signal
        # biasa yang kebetulan angkanya besar.
        return Vonis(
            Keputusan.KIRIM_DENGAN_PERINGATAN,
            f"risiko tinggi ({risk.score:.0f}/100)",
            risk,
        )

    return Vonis(
        Keputusan.KIRIM,
        f"risiko {risk.level.value.lower()} ({risk.score:.0f}/100)",
        risk,
    )


__all__ = ["TAHAN_KALAU_TIDAK_BISA_DINILAI", "Keputusan", "Vonis", "evaluate"]
