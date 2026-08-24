"""Batas dan peringatan atas keyakinan (bagian 18.22 - 18.24, 18.41).

**Confidence bukan certainty** (bagian 18.2). Keyakinan 85% tidak boleh dibaca
"85% pasti profit"; ia menyatakan seberapa jauh bukti yang tersedia mendukung
kesimpulan, dan tidak lebih.

Modul ini menegakkan tiga hal yang selama ini tidak dijaga siapa pun:

* **Langit-langit** (bagian 18.23) - keyakinan tidak boleh melampaui mutu bukti
  yang menopangnya. Sinyal 95% di atas rezim berkeyakinan 42% mungkin sebelum
  ini.
* **Peringatan keyakinan palsu** (bagian 18.22) - keyakinan tinggi DENGAN bukti
  lemah DAN risiko tinggi adalah satu keadaan, bukan tiga angka yang kebetulan
  berdekatan. Ketiganya sudah terukur terpisah; tidak ada yang menyilangkannya.
* **Nama untuk angkanya** (bagian 18.41) - laporan yang menyebut "54" tanpa
  menyebut artinya memaksa tiap pembacanya mengingat ambangnya sendiri.

**Yang TIDAK ada di sini: lantai keyakinan** (bagian 18.24). Spec memintanya -
"keyakinan tidak boleh jatuh tidak masuk akal hanya karena satu agent berbeda" -
tapi yang menurunkan keyakinan di ARUNA bukan ketidaksetujuan satu agent
melainkan kalibrator yang diukur dari hasil nyata. Memasang lantai di atasnya
berarti menolak pengukuran ketika hasilnya tidak disukai, dan itu justru
kebalikan dari bagian 18.18. Kalau suatu hari ada jalur yang benar-benar
menjatuhkan keyakinan karena satu suara, lantainya dipasang DI SANA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "AMBANG_MUTU",
    "Peringatan",
    "PitaMutu",
    "langit_langit",
    "periksa_keyakinan",
    "pita",
]


class PitaMutu(StrEnum):
    """Nama untuk skor mutu (bagian 18.41)."""

    EXCELLENT = "EXCELLENT"
    HIGH = "HIGH"
    GOOD = "GOOD"
    MODERATE = "MODERATE"
    LOW = "LOW"
    POOR = "POOR"


#: Batas bawah tiap pita, dari yang tertinggi.
#:
#: Angkanya persis contoh bagian 18.41. **Kebijakan, bukan pengukuran**, dan
#: ditulis begitu supaya tidak ada yang mengutipnya sebagai temuan - yang bisa
#: dipertahankan urutannya, bukan jaraknya.
#:
#: Satu titik yang bukan selera: batas ``MODERATE`` adalah 60, sama dengan
#: :data:`~aruna.signals.quality.MIN_QUALITY`. Pita yang lulus gerbang dan pita
#: yang bernama layak harus berpindah bersama; kalau tidak, laporan akan
#: menyebut sebuah sinyal "GOOD" sambil gerbangnya menolaknya.
AMBANG_MUTU: tuple[tuple[int, PitaMutu], ...] = (
    (90, PitaMutu.EXCELLENT),
    (80, PitaMutu.HIGH),
    (70, PitaMutu.GOOD),
    (60, PitaMutu.MODERATE),
    (50, PitaMutu.LOW),
)


def pita(skor: float | None) -> PitaMutu | None:
    """Nama pita untuk sebuah skor mutu, atau ``None`` kalau tak terukur.

    ``None`` bukan ``POOR``: skor yang tidak bisa dihitung dan skor yang
    dihitung lalu jelek adalah dua hal yang sangat berbeda, dan menyamakannya
    membuat tiap sinyal yang datanya kurang terlihat buruk alih-alih terlihat
    belum bisa dinilai.
    """
    if skor is None:
        return None
    for batas, nama in AMBANG_MUTU:
        if skor >= batas:
            return nama
    return PitaMutu.POOR


class Peringatan(StrEnum):
    """Apa yang salah dengan keyakinan yang diajukan."""

    #: Bagian 18.22: keyakinan tinggi + bukti lemah + risiko tinggi.
    KEYAKINAN_PALSU = "KEYAKINAN_PALSU"
    #: Bagian 18.23: keyakinan melampaui mutu bukti yang menopangnya.
    MELAMPAUI_LANGIT_LANGIT = "MELAMPAUI_LANGIT_LANGIT"


#: Di atas berapa sebuah keyakinan disebut "tinggi" untuk bagian 18.22.
#:
#: Delapan puluh, dan itu **dipinjam dari pita** :attr:`PitaMutu.HIGH` -
#: pertanyaannya sama: mulai dari mana sebuah angka mengaku kuat. Menuliskan
#: ambang kedua di sini berarti dua angka yang bisa melenceng.
_KEYAKINAN_TINGGI = 80.0

#: Di bawah berapa bukti disebut lemah untuk bagian 18.22.
#:
#: Lima puluh sembilan - tepat di bawah :data:`~aruna.signals.quality.
#: MIN_QUALITY`. Bukti yang lulus gerbang tidak boleh disebut lemah oleh
#: peringatan ini; kalau ia lulus, keluhannya milik gerbang, bukan milik sini.
_BUKTI_LEMAH = 59.0

#: Di atas berapa risiko disebut tinggi untuk bagian 18.22.
#:
#: Delapan puluh, sama dengan batas ``VERY_HIGH`` di
#: :func:`~aruna.risk.score.categorise`. Dipinjam, bukan diketik ulang.
_RISIKO_TINGGI = 80.0


@dataclass(frozen=True, slots=True)
class Putusan:
    """Keyakinan sesudah dibatasi, berikut apa yang membatasinya."""

    keyakinan: float
    #: Batas atas yang berlaku, atau ``None`` kalau tidak ada yang membatasi.
    batas: float | None = None
    peringatan: tuple[Peringatan, ...] = field(default_factory=tuple)
    alasan: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dipotong(self) -> bool:
        return self.batas is not None and self.keyakinan >= self.batas


def langit_langit(
    *, mutu: float | None, keyakinan_rezim: float | None
) -> tuple[float | None, str]:
    """Batas atas keyakinan yang boleh diajukan (bagian 18.23).

    Dua penopang, dan yang **terendah** yang mengikat: keyakinan tidak bisa
    lebih kuat daripada penopangnya yang paling lemah.

    ``None`` berarti tidak ada yang bisa membatasi - bukan berarti bebas.
    Keduanya tak terukur adalah keadaan yang jarang dan pantas terlihat apa
    adanya, bukan disamarkan sebagai batas nol.
    """
    penopang: list[tuple[float, str]] = []
    if mutu is not None:
        penopang.append((mutu, f"mutu keputusan {mutu:.0f}"))
    if keyakinan_rezim is not None:
        penopang.append((keyakinan_rezim, f"keyakinan rezim {keyakinan_rezim:.0f}%"))
    if not penopang:
        return None, "tidak ada penopang yang terukur"
    batas, sebab = min(penopang, key=lambda p: p[0])
    return batas, sebab


def periksa_keyakinan(
    keyakinan: float,
    *,
    mutu: float | None = None,
    keyakinan_rezim: float | None = None,
    risiko: float | None = None,
) -> Putusan:
    """Batasi keyakinan dan sebutkan apa yang membatasinya.

    **Tidak pernah menaikkan.** Modul ini hanya bisa menahan; keyakinan yang
    naik karena pemeriksaan mutu berarti pemeriksaannya menjadi sumber
    keyakinan, dan itu lingkaran.
    """
    peringatan: list[Peringatan] = []
    alasan: list[str] = []

    batas, sebab = langit_langit(mutu=mutu, keyakinan_rezim=keyakinan_rezim)
    keluar = keyakinan
    if batas is not None and keyakinan > batas:
        peringatan.append(Peringatan.MELAMPAUI_LANGIT_LANGIT)
        alasan.append(
            f"keyakinan {keyakinan:.0f}% dibatasi {batas:.0f}% oleh {sebab}"
        )
        keluar = batas

    # Bagian 18.22. Ketiganya HARUS bersamaan - itu yang membuatnya satu
    # keadaan alih-alih tiga angka yang kebetulan berdekatan. Keyakinan tinggi
    # dengan bukti kuat wajar; bukti lemah dengan keyakinan rendah jujur;
    # risiko tinggi dengan keduanya baik adalah keputusan sadar.
    if (
        keyakinan >= _KEYAKINAN_TINGGI
        and mutu is not None
        and mutu <= _BUKTI_LEMAH
        and risiko is not None
        and risiko >= _RISIKO_TINGGI
    ):
        peringatan.append(Peringatan.KEYAKINAN_PALSU)
        alasan.append(
            f"keyakinan {keyakinan:.0f}% atas bukti {mutu:.0f} dengan risiko "
            f"{risiko:.0f} - keyakinan palsu"
        )

    return Putusan(
        keyakinan=round(keluar, 3),
        batas=batas,
        peringatan=tuple(peringatan),
        alasan=tuple(alasan),
    )
