"""Tujuh keyakinan, dipisah dan tidak dilebur (bagian 18.17, 18.45).

Bagian 18.17 melarang satu hal dengan tegas: *"Jangan menggabungkan semua
menjadi satu angka tanpa penjelasan."* Sampai modul ini ada, itulah persis yang
dilakukan pesan futures - satu baris ``CONFIDENCE: 81%``, dan pembacanya tidak
punya cara apa pun untuk tahu bahwa angka itu hanya menjawab **satu** dari tujuh
pertanyaan yang berbeda.

**Perbedaannya menentukan.** Keyakinan council 81% di atas rezim yang tidak
terbaca dan skenario yang rapuh bukan keadaan yang sama dengan keyakinan
council 81% di atas keduanya yang kuat - tapi keduanya mencetak baris yang
identik. Yang hilang bukan detail; yang hilang adalah satu-satunya keterangan
yang membedakan setup yang layak dari setup yang kebetulan diyakini.

Tujuh angka itu **sudah dihitung seluruhnya** sebelum modul ini ada. Council
menghitung yang pertama, :func:`~aruna.decision.score.score` yang kedua, dan
lima sisanya adalah faktor di dalam :class:`~aruna.signals.quality.QualityScore`
yang sama yang menghasilkan ``Decision Quality``. Yang tidak ada bukan
pengukurannya - melainkan yang menyebutkannya kepada pembacanya. Itu cacat yang
sama untuk ketujuh kalinya di proyek ini: dihitung, diuji, lalu dibuang di
langkah terakhir menjadi satu ``float``.

**Tidak ada satu pun angka baru di sini.** Modul ini tidak menilai apa pun; ia
memilih, memberi nama, dan memberi satuan. Sebuah "keyakinan gabungan" yang
dihitung di sini akan menjadi angka kedelapan - persis yang bagian 18.17 larang,
hanya dengan lebih banyak langkah.

``None`` dicetak sebagai **TIDAK TERUKUR**, bukan sebagai nol. Faktor yang tidak
bisa diukur bukan faktor yang bernilai buruk, dan "Strategy Confidence: 0/100"
adalah tuduhan terhadap router yang sebenarnya tidak pernah ditanya.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Yang dicetak ketika angkanya tidak ada. Lihat docstring modul.
TIDAK_TERUKUR = "TIDAK TERUKUR"


@dataclass(frozen=True, slots=True)
class Terpisah:
    """Satu keyakinan, dengan nama dan satuannya sendiri.

    ``nilai`` sudah berbentuk teks karena ketujuhnya **tidak sesatuan**, dan
    itu bukan kekurangan yang perlu dirapikan. Keyakinan council adalah persen;
    Decision Score adalah angka berarah yang bisa negatif dan yang sengaja
    disertai bantahan "bukan peluang profit"; sisanya skor 0-100. Memaksa
    ketujuhnya menjadi satu satuan supaya kolomnya rapi akan mengubah salah
    satunya menjadi sesuatu yang bukan dirinya - dan yang paling mudah berubah
    adalah Decision Score, satu-satunya yang punya tanda.
    """

    nama: str
    nilai: str | None
    sumber: str

    @property
    def terukur(self) -> bool:
        return self.nilai is not None

    def baris(self, lebar: int = 22) -> str:
        return f"{self.nama:<{lebar}} {self.nilai or TIDAK_TERUKUR}"


#: Nama faktor :class:`~aruna.signals.quality.Factor` untuk lima dari tujuh.
#:
#: Dipetakan lewat nama faktor, bukan lewat posisi di dalam tuple: urutan
#: ``score_signal`` sudah berubah dua kali sejak PASAL 11.1 ditulis, dan
#: pembacaan berbasis indeks akan diam-diam mengambil faktor tetangga.
_DARI_FAKTOR: dict[str, tuple[str, str]] = {
    "Scenario Confidence": ("scenario", "Phase 16"),
    "Strategy Confidence": ("strategy", "Phase 17"),
    "Regime Confidence": ("regime_clarity", "Phase 17"),
    "Data Confidence": ("data_quality", "Phase 2"),
}

#: Ketujuh nama bagian 18.17, dalam urutan yang disebut di sana.
TUJUH: tuple[str, ...] = (
    "Signal Confidence",
    "Decision Confidence",
    "Scenario Confidence",
    "Strategy Confidence",
    "Regime Confidence",
    "Data Confidence",
    "Decision Quality",
)


def _faktor(mutu: Any, nama: str) -> Any:
    for f in getattr(mutu, "factors", ()) or ():
        if getattr(f, "name", None) == nama:
            return f
    return None


def _skor_faktor(mutu: Any, nama: str) -> str | None:
    """Satu faktor sebagai ``NN/100``, atau ``None`` kalau tak terukur."""
    f = _faktor(mutu, nama)
    nilai = getattr(f, "score", None) if f is not None else None
    return None if nilai is None else f"{float(nilai) * 100:.0f}/100"


def pisahkan(
    *,
    mutu: Any = None,
    confidence: float | None = None,
    decision: str = "",
) -> tuple[Terpisah, ...]:
    """Ketujuh keyakinan bagian 18.17, masing-masing dari sumbernya sendiri.

    ``mutu`` adalah :class:`~aruna.signals.quality.QualityScore` yang sudah
    dihitung untuk keputusan ini - **bukan** dihitung ulang di sini. Dua
    penilai mutu untuk satu keputusan adalah dua angka yang harus tetap
    sepakat, dan mereka tidak akan.

    ``decision`` diterima sebagai teks yang sudah jadi, karena pemanggilnya
    sudah memilikinya dalam bentuk itu dan karena bentuk itu membawa bantahan
    yang wajib menyertainya (PASAL 14.16). Teks kosong berarti tidak terukur.
    """
    skor = getattr(mutu, "score", None) if mutu is not None else None
    keluar = [
        Terpisah(
            "Signal Confidence",
            None if confidence is None else f"{float(confidence) * 100:.0f}%",
            "council Phase 14",
        ),
        Terpisah(
            "Decision Confidence",
            decision or None,
            "Decision Score PASAL 14.16",
        ),
    ]
    keluar += [
        Terpisah(nama, _skor_faktor(mutu, kunci), sumber)
        for nama, (kunci, sumber) in _DARI_FAKTOR.items()
    ]
    keluar.append(
        Terpisah(
            "Decision Quality",
            None if skor is None else f"{int(skor)}/100",
            "Phase 18",
        )
    )
    return tuple(keluar)


def render_terpisah(
    daftar: tuple[Terpisah, ...], *, indent: str = "  "
) -> list[str]:
    """Blok KEYAKINAN, satu baris per keyakinan.

    Yang tidak terukur **tetap dicetak**. Menyembunyikannya akan membuat pesan
    yang kehilangan Phase 17 terbaca persis seperti pesan yang tidak pernah
    punya Phase 17 - dan operator tidak akan pernah tahu bedanya. Itu cara
    paling halus sebuah lapisan yang mati menjadi tidak terlihat.
    """
    if not daftar:
        return []
    lebar = max(len(t.nama) for t in daftar) + 2
    return [
        "",
        f"{indent}KEYAKINAN (dipisah, tidak dilebur):",
        *[f"{indent}  {t.baris(lebar)}" for t in daftar],
    ]


__all__ = [
    "TIDAK_TERUKUR",
    "TUJUH",
    "Terpisah",
    "pisahkan",
    "render_terpisah",
]
