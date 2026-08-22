"""Daftar periksa sebelum terbit (PASAL 14.18, 14.25).

PASAL 14.25 menuliskan empat belas butir yang harus dicentang sebelum apa pun
sampai ke Telegram, dan menutupnya dengan satu kalimat: *"Jika semua PASS:
PUBLISH."* PASAL 14.18 adalah gerbang yang sama, lebih awal: *"Jika salah satu
komponen penting gagal: NO SIGNAL."*

**Butir yang tidak pernah dinilai bukan butir yang lulus.** Ini seluruh isi
modul ini. Daftar periksa yang memperlakukan "tidak tahu" sebagai "aman" akan
meloloskan justru signal yang paling sedikit diketahui tentangnya - dan ia akan
melakukannya dengan tampilan yang meyakinkan, karena semua barisnya bercentang.
Karena itu :class:`Nilai` punya tiga keadaan, bukan dua, dan hanya satu di
antaranya yang meloloskan.

**GAGAL dan BELUM DINILAI dilaporkan terpisah.** Keduanya menahan terbit,
tetapi menuntut tindakan yang berbeda dari operator: yang satu berarti ada
sesuatu yang salah pada setup-nya, yang lain berarti ada lapisan yang tidak
berjalan. Meleburnya menjadi satu daftar "tidak lolos" menyembunyikan
perbedaan itu, dan lapisan yang mati diam-diam akan terbaca sebagai pasar yang
sedang jelek.

**Daftarnya lengkap, bukan sebagian.** Kunci yang tidak diberikan pemanggil
menjadi ``UNKNOWN`` - tidak dilewati. Sebuah butir yang hilang dari daftar
periksa adalah butir yang tidak pernah menahan apa pun.

**JANGAN pasang ini sebagai gerbang kirim sekarang. Terukur.**

Pada 2026-08-19, dua puluh simbol dinilai lewat :func:`aruna.decision.observe
.amati` di produksi. Hasilnya **0 dari 20 boleh terbit**, dan sebabnya bukan
setup yang jelek:

* ``MTF`` - **selalu** kosong. Jalur futures merencanakan satu horizon;
  analisis lintas timeframe serentak (PASAL 14.4) belum ada di jalur itu.
* ``STRATEGY`` - **selalu** kosong. Phase 12 berjalan harian dan hasilnya
  tidak mengalir ke penyusun rencana.
* ``QUALITY`` dan ``INVALIDATION`` - 13 dari 20.
* ``RR`` - kosong pada seluruh dua puluh, tapi jendela pengukurannya tidak
  memuat satu pun PLAN; pada rencana yang benar-benar layak, angkanya ada.
  Jangan membaca ini sebagai lapisan yang hilang.

Yang **tidak** pernah hilang: keabsahan data, kesegaran data, dan analisis
risiko - tiga butir yang PASAL 14.3 larang dilewati. Dan nol pelanggaran
urutan dari dua puluh simbol.

Artinya gerbang ini akan membungkam ARUNA sepenuhnya, dan yang terlihat di
layar operator bukan "tiga lapisan belum dibangun" melainkan pasar yang tidak
pernah menawarkan apa pun. Daftar periksanya benar; yang belum siap adalah
sistem yang diperiksanya.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from aruna.decision.score import Arah


class Butir(StrEnum):
    """Empat belas butir PASAL 14.25, dalam urutan yang tertulis di sana.

    PASAL 14.18 menyebut sebelas di antaranya; tiga sisanya - strategi
    historis, risk/reward, dan expiration - ditambahkan PASAL 14.25 tepat
    sebelum pengiriman. Yang dipakai di sini daftar yang lebih panjang: sebuah
    gerbang yang memeriksa lebih sedikit daripada yang diminta pasal terakhir
    sebelum Telegram akan meloloskan hal yang justru diminta diperiksa terakhir.
    """

    DATA = "data valid"
    FRESHNESS = "data segar"
    REGIME = "rezim pasar"
    MTF = "analisis multi-timeframe"
    AGENTS = "analisis agent"
    PROTEST = "protes"
    COUNCIL = "council"
    QUALITY = "signal quality"
    STRATEGY = "strategi historis"
    RISK = "analisis risiko"
    RR = "risk/reward"
    INVALIDATION = "syarat pembatalan"
    EXPIRATION = "masa berlaku"
    HORIZON = "horizon keputusan"


#: Butir yang hanya berlaku untuk keputusan berarah.
#:
#: Alasannya sama persis dengan :data:`aruna.decision.trail.BERARAH`, dan
#: terukur di jalur hidup: dari dua puluh simbol, tujuh yang punya arah
#: **semuanya** punya syarat pembatalan, dan tiga belas yang tidak punya arah
#: **tidak satu pun** - korelasi sempurna. Sebuah keputusan tanpa arah tidak
#: punya entry untuk dijadikan pangkal risk/reward, tidak punya tesis yang bisa
#: runtuh, dan tidak punya signal yang bisa kedaluwarsa.
#:
#: Menuntut ketiganya dari setiap keputusan berarti menuntut lapisan di atasnya
#: mengarang - dan karangan yang lolos daftar periksa lebih berbahaya daripada
#: butir yang jujur diakui tidak berlaku (PASAL 13.26).
BERARAH: frozenset[Butir] = frozenset({
    Butir.RR,
    Butir.INVALIDATION,
    Butir.EXPIRATION,
})


class Nilai(StrEnum):
    """Tiga keadaan, dan hanya satu yang meloloskan."""

    PASS = "LULUS"
    FAIL = "GAGAL"
    #: Bukan lulus. Lihat catatan modul.
    UNKNOWN = "BELUM DINILAI"

    @property
    def mark(self) -> str:
        return {Nilai.PASS: "✓", Nilai.FAIL: "✗", Nilai.UNKNOWN: "?"}[self]


@dataclass(frozen=True, slots=True)
class Audit:
    """Hasil daftar periksa, lengkap empat belas butir."""

    values: tuple[tuple[Butir, Nilai], ...]
    #: Butir yang tidak berlaku untuk keputusan ini - lihat :data:`BERARAH`.
    #:
    #: Dipisahkan dari yang lulus dan dari yang belum dinilai, karena ia bukan
    #: keduanya: tidak ada yang gagal, dan tidak ada yang belum dikerjakan.
    #: Pertanyaannya sendiri yang tidak punya makna.
    inapplicable: tuple[Butir, ...] = field(default_factory=tuple)

    @property
    def failures(self) -> tuple[Butir, ...]:
        return tuple(b for b, n in self.values if n is Nilai.FAIL)

    @property
    def unknowns(self) -> tuple[Butir, ...]:
        return tuple(
            b for b, n in self.values
            if n is Nilai.UNKNOWN and b not in self.inapplicable
        )

    @property
    def may_publish(self) -> bool:
        """*"Jika semua PASS: PUBLISH."* Tidak ada bentuk lain.

        Butir yang tidak berlaku tidak dihitung - tapi butir yang berlaku dan
        **gagal** tetap menahan, termasuk kalau ia kebetulan ada di
        :data:`BERARAH`. Yang dilonggarkan adalah pertanyaan yang tidak punya
        makna, bukan jawaban yang buruk.
        """
        return all(
            n is Nilai.PASS or (b in self.inapplicable and n is Nilai.UNKNOWN)
            for b, n in self.values
        )

    def verdict(self, decision: Arah) -> Arah:
        """Keputusan sesudah gerbang (PASAL 14.18).

        Gerbang ini hanya bisa **menahan**. Ia tidak pernah mengubah LONG
        menjadi SHORT, dan tidak pernah menaikkan NO SIGNAL menjadi arah -
        sebuah daftar periksa yang lulus tidak menciptakan bukti yang tidak ada.
        """
        return decision if self.may_publish else Arah.NO_SIGNAL

    def line(self) -> str:
        if self.may_publish:
            return f"✅ LOLOS AUDIT - {len(self.values)}/{len(self.values)} butir"
        bagian: list[str] = []
        if self.failures:
            bagian.append(f"{len(self.failures)} gagal")
        if self.unknowns:
            bagian.append(f"{len(self.unknowns)} belum dinilai")
        return f"⛔ TIDAK DITERBITKAN - {', '.join(bagian)}"

    def report(self) -> list[str]:
        """Blok PRE-SIGNAL AUDIT (PASAL 14.25), sebagai baris."""
        baris = ["🔍 AUDIT SEBELUM KIRIM", "", f"  {self.line()}", ""]
        baris += [f"  [{n.mark}] {b.value}" for b, n in self.values]
        if self.failures:
            baris += ["", "  Yang gagal:"]
            baris += [f"    ✗ {b.value}" for b in self.failures]
        if self.unknowns:
            # Dipisahkan dari yang gagal: ini bukan setup yang jelek, ini
            # lapisan yang tidak berjalan.
            baris += ["", "  Yang belum dinilai (bukan berarti aman):"]
            baris += [f"    ? {b.value}" for b in self.unknowns]
        if self.inapplicable:
            baris += ["", "  Tidak berlaku untuk keputusan tanpa arah:"]
            baris += [f"    - {b.value}" for b in self.inapplicable]
        return baris


def audit(
    hasil: Mapping[Butir, bool | None], *, directional: bool = True
) -> Audit:
    """Susun daftar periksa dari apa pun yang sudah diketahui.

    ``True`` lulus, ``False`` gagal, ``None`` **dan kunci yang tidak ada** sama
    saja: belum dinilai. Kesamaan itu disengaja - pemanggil yang lupa mengisi
    sebuah butir dan pemanggil yang tahu butirnya tidak terukur sama-sama tidak
    boleh menerbitkan apa pun.

    ``directional=False`` untuk keputusan tanpa arah: butir di :data:`BERARAH`
    berhenti berlaku. Bawaannya ``True`` supaya pemanggil yang tidak menyebut
    apa-apa mendapat daftar periksa yang paling ketat - sebuah kelonggaran yang
    aktif secara bawaan adalah kelonggaran yang menyebar tanpa ada yang memilih.
    """
    return Audit(
        values=tuple(
            (
                b,
                Nilai.UNKNOWN
                if hasil.get(b) is None
                else (Nilai.PASS if hasil[b] else Nilai.FAIL),
            )
            for b in Butir
        ),
        inapplicable=() if directional else tuple(
            b for b in Butir if b in BERARAH
        ),
    )


__all__ = ["BERARAH", "Audit", "Butir", "Nilai", "audit"]
