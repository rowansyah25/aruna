"""Urutan yang harus dilewati sebuah keputusan (PASAL 14.3).

Empat belas langkah, dan urutannya bukan hiasan. PASAL 14.3 menutupnya dengan
satu kalimat yang menyebut dua langkah dengan namanya: *"Tidak boleh melewati
data validation atau risk validation."*

**Kenapa urutan, bukan sekadar kelengkapan.** Daftar periksa di
:mod:`aruna.decision.audit` menjawab pertanyaan lain - *apakah semuanya sudah
dinilai* - dan ia menjawabnya tepat sebelum kirim. Modul ini menjawab *apakah
penilaiannya berdiri di atas yang seharusnya*. Sebuah analisis multi-timeframe
yang dijalankan sebelum kesegaran datanya diperiksa menghasilkan angka yang
rapi tentang harga yang mungkin sudah basi; council yang memutuskan sebelum
rezimnya diklasifikasi berdebat tanpa tahu pasar macam apa yang sedang
dibacanya. Keduanya menghasilkan keluaran yang terlihat lengkap.

**Langkah boleh tidak ada; ia tidak boleh terbalik.** Sebagian langkah memang
tidak selalu tersedia - strategi historis butuh sampel yang belum tentu ada,
invalidation butuh level struktur yang tidak selalu ditemukan. Itu keadaan yang
sah dan dilaporkan sebagai ketiadaan. Yang tidak pernah sah adalah langkah yang
dikerjakan **sebelum** langkah yang menjadi dasarnya.

**Tiga langkah tidak boleh hilang, apa pun alasannya.** Dua disebut PASAL 14.3
sendiri - keabsahan dan kesegaran data - dan yang ketiga adalah analisis risiko.
Data yang tidak diperiksa membuat seluruh menara di atasnya menjadi aritmetika
tentang angka yang salah; risiko yang tidak dinilai membuat keputusan yang
benar arahnya tetap bisa menghabiskan modal.

Risk/reward tidak masuk daftar wajib meskipun ia juga tentang risiko: ia butuh
entry, stop, dan target yang belum tentu ada pada keputusan NO SIGNAL, dan
gerbang yang menuntutnya di setiap jalur akan menolak justru jalur yang paling
sering benar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Tahap(StrEnum):
    """Empat belas langkah PASAL 14.3, dalam urutan yang tertulis di sana."""

    DATA_VALIDITY = "keabsahan data"
    DATA_FRESHNESS = "kesegaran data"
    MARKET_REGIME = "rezim pasar"
    MTF = "analisis multi-timeframe"
    AGENTS = "analisis agent"
    PROTEST = "protes agent"
    COUNCIL = "suara council"
    QUALITY = "signal quality"
    STRATEGY = "performa strategi historis"
    RISK = "analisis risiko"
    RR = "risk/reward"
    INVALIDATION = "syarat pembatalan"
    HORIZON = "horizon keputusan"
    FINAL = "keputusan final"


#: Urutan resmi, sebagai daftar - dan indeksnya yang menentukan "sebelum".
URUTAN: tuple[Tahap, ...] = tuple(Tahap)

_POSISI: dict[Tahap, int] = {t: i for i, t in enumerate(URUTAN)}

#: Langkah yang tidak boleh hilang. Lihat catatan modul untuk kenapa ketiganya,
#: dan kenapa risk/reward tidak ikut.
WAJIB: frozenset[Tahap] = frozenset({
    Tahap.DATA_VALIDITY,
    Tahap.DATA_FRESHNESS,
    Tahap.RISK,
})


class HierarchyError(ValueError):
    """Langkah yang dikerjakan di luar urutannya."""


@dataclass(frozen=True, slots=True)
class Jalur:
    """Langkah-langkah yang sudah dilewati satu keputusan."""

    done: tuple[Tahap, ...] = field(default_factory=tuple)

    def advance(self, tahap: Tahap) -> Jalur:
        """Catat satu langkah selesai, atau tolak dengan menyebut kenapa.

        Mengembalikan jalur **baru** alih-alih mengubah yang ini: sebuah jalur
        yang bisa disunting di tempat berarti jejaknya bisa berubah sesudah
        keputusannya dibaca, dan jejak yang bisa berubah bukan jejak.
        """
        if tahap in self.done:
            raise HierarchyError(
                f"{tahap.value} dikerjakan dua kali; satu keputusan melewati "
                f"tiap langkah sekali, dan pengulangan berarti alurnya berputar"
            )
        if self.done and _POSISI[tahap] < _POSISI[self.done[-1]]:
            raise HierarchyError(
                f"{tahap.value} dikerjakan sesudah {self.done[-1].value}, "
                f"padahal ia mendahuluinya (PASAL 14.3)"
            )
        if tahap is Tahap.FINAL and self.missing_mandatory:
            hilang = ", ".join(t.value for t in self.missing_mandatory)
            raise HierarchyError(
                f"keputusan final tanpa {hilang}; PASAL 14.3 melarang "
                f"melewatinya"
            )
        return Jalur(done=(*self.done, tahap))

    @property
    def missing_mandatory(self) -> tuple[Tahap, ...]:
        """Langkah wajib yang belum dilewati, dalam urutan resminya."""
        return tuple(t for t in URUTAN if t in WAJIB and t not in self.done)

    @property
    def skipped(self) -> tuple[Tahap, ...]:
        """Langkah yang dilewati - sah, tapi disebut.

        Hanya sampai langkah terakhir yang dikerjakan. Langkah yang memang
        belum tiba gilirannya bukan langkah yang dilewati, dan menghitungnya
        sebagai lewat akan membuat setiap jalur yang sedang berjalan terlihat
        penuh lubang.
        """
        if not self.done:
            return ()
        batas = _POSISI[self.done[-1]]
        return tuple(
            t for t in URUTAN if _POSISI[t] < batas and t not in self.done
        )

    @property
    def may_decide(self) -> bool:
        return not self.missing_mandatory

    def line(self) -> str:
        if not self.done:
            return "belum satu langkah pun dilewati"
        kurang = self.missing_mandatory
        if kurang:
            return (
                f"{len(self.done)}/{len(URUTAN)} langkah - "
                f"TIDAK BOLEH memutuskan tanpa "
                f"{', '.join(t.value for t in kurang)}"
            )
        return f"{len(self.done)}/{len(URUTAN)} langkah, semua yang wajib ada"

    def report(self) -> list[str]:
        baris = ["🪜 URUTAN KEPUTUSAN", "", f"  {self.line()}", ""]
        for t in URUTAN:
            if t in self.done:
                tanda = "✓"
            elif t in WAJIB:
                tanda = "✗"
            else:
                tanda = "·"
            baris.append(f"  [{tanda}] {t.value}")
        if self.skipped:
            baris += ["", "  Dilewati (boleh, tapi disebut):"]
            baris += [f"    · {t.value}" for t in self.skipped]
        return baris


@dataclass(frozen=True, slots=True)
class Pengamat:
    """Perekam yang **tidak pernah menolak** - untuk dipakai di jalur hidup.

    :class:`Jalur` melempar pengecualian pada langkah yang terbalik, dan itu
    perilaku yang benar untuk penjaga. Tapi sebuah penjaga yang baru dipasang
    di sistem yang sudah berjalan akan menghentikan produksi karena asumsi
    penulisnya tentang urutan nyata, bukan karena ada yang rusak.

    Jadi pengamat ini mencatat pelanggarannya sebagai **temuan** dan terus
    berjalan. Ia dipakai untuk mengukur dulu: seberapa sering urutannya
    dilanggar sungguhan, dan langkah mana yang ternyata tidak pernah ada.
    Sesudah angkanya ada, barulah masuk akal memutuskan apakah ia layak
    menjadi gerbang.

    Ini bukan pelonggaran diam-diam: yang dilonggarkan adalah **kapan**
    penjaganya menyela, bukan **apa** yang dianggap salah. Keduanya memakai
    aturan yang sama persis - :meth:`Jalur.advance`.
    """

    jalur: Jalur = field(default_factory=Jalur)
    findings: tuple[str, ...] = field(default_factory=tuple)

    def advance(self, tahap: Tahap) -> Pengamat:
        try:
            return Pengamat(self.jalur.advance(tahap), self.findings)
        except HierarchyError as exc:
            return Pengamat(self.jalur, (*self.findings, str(exc)))

    def note(self, tahap: Tahap, *, done: bool) -> Pengamat:
        """Catat langkah kalau memang dikerjakan; lewati kalau tidak.

        Bentuk yang paling sering dibutuhkan pemanggil: ia tahu apakah sebuah
        lapisan berjalan, dan tidak seharusnya menulis ``if`` untuk tiap
        langkah.
        """
        return self.advance(tahap) if done else self

    @property
    def clean(self) -> bool:
        return not self.findings


__all__ = [
    "URUTAN",
    "WAJIB",
    "HierarchyError",
    "Jalur",
    "Pengamat",
    "Tahap",
]
