"""Koreksi diri modul XAU - berjalan sendiri, tanpa persetujuan.

Operator memutuskan 2026-08-28: koreksi tiap sekian sinyal, langsung berlaku.
Yang membuat keputusan itu aman bukan kehati-hatian melainkan **apa yang
dikoreksi**.

**Bobot agen, bukan ambang gerbang.**  Menyetel ``MIN_RR`` atau
``MAX_KONTRADIKSI`` terhadap hasilnya sendiri adalah cara tercepat menaikkan
win rate di atas kertas tanpa satu pun keputusan membaik: gerbang yang
dilonggarkan sampai hanya meloloskan yang kebetulan menang akan terlihat
sempurna dan tidak meramalkan apa pun.  Spec menyebutnya overfitting dan
melarangnya.  Bobot agen berbeda - ia diukur terhadap **garis dasar pasar pada
baris yang sama**, jadi agen yang selalu bilang BUY di pasar yang naik 60%
waktu tidak mendapat pujian atas keberuntungan yang bisa dihitung.

**Mesinnya dipakai ulang, bukan ditulis kedua kalinya.**
:func:`~aruna.learning.reliability.build_reliability` sudah menghitung akurasi,
edge terhadap garis dasar, ambang sampel minimum, dan batas multiplier.  Dua
implementasi keandalan akan menghasilkan dua angka yang tidak bisa
dibandingkan, dan yang salah tidak akan pernah ketahuan.

**Sampel kurang tetap dicatat.**  Sebuah putaran yang berhenti karena bahannya
tipis ditulis dengan ``diterapkan=False`` dan alasannya.  Tanpa itu, "belum
cukup bahan" dan "tidak pernah dijalankan" terlihat sama persis - dan yang
kedua adalah kerusakan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aruna.core.enums import AgentRole
from aruna.core.logging import get_logger
from aruna.learning.reliability import ReliabilityReport, build_reliability

log = get_logger(__name__)

#: Berapa hasil terselesaikan sebelum putaran koreksi berikutnya.
#:
#: Sepuluh, dan angkanya bukan selera.  Di bawah itu satu hasil menggeser
#: akurasi lebih dari sepuluh persen, jadi bobotnya akan berayun mengikuti
#: derau - dan ayunan itu sendiri yang lalu dipelajari.  Jauh di atas itu,
#: koreksi datang terlalu lambat untuk berguna.
#:
#: Ini gerbang FREKUENSI, bukan gerbang kecukupan: yang menentukan sebuah
#: bobot layak dipakai adalah `MIN_RELIABILITY_SAMPLE` di dalam
#: `build_reliability`, dan ambang itu berlaku per agen.
KOREKSI_TIAP = 10


@dataclass(frozen=True, slots=True)
class HasilKoreksi:
    """Satu putaran koreksi, berhasil maupun tidak."""

    versi: str
    versi_sebelumnya: str | None
    dipicu_oleh: int
    sampel: int
    garis_dasar: float | None
    bobot: dict[str, float]
    diterapkan: bool
    alasan: str | None = None

    def ringkas(self) -> str:
        if not self.diterapkan:
            return f"{self.versi}: tidak diterapkan - {self.alasan}"
        naik = sum(1 for b in self.bobot.values() if b > 1.0)
        turun = sum(1 for b in self.bobot.values() if b < 1.0)
        return (
            f"{self.versi}: {len(self.bobot)} agen diukur atas {self.sampel} suara, "
            f"{naik} naik / {turun} turun, garis dasar {self.garis_dasar}"
        )


def perlu_koreksi(hasil_terselesaikan: int, terakhir_dipicu: int) -> bool:
    """``True`` saat hitungan hasil melewati kelipatan :data:`KOREKSI_TIAP`.

    Dibandingkan terhadap pemicu TERAKHIR, bukan terhadap sisa bagi.  Sisa bagi
    akan memicu berkali-kali selama hitungannya tidak bertambah - dan hitungan
    memang tidak bertambah di antara dua hasil.
    """
    if hasil_terselesaikan < KOREKSI_TIAP:
        return False
    return hasil_terselesaikan // KOREKSI_TIAP > terakhir_dipicu // KOREKSI_TIAP


def _versi(putaran: int) -> str:
    return f"xau-m5-{putaran}"


def hitung_koreksi(
    baris: list[dict[str, Any]],
    *,
    putaran: int,
    dipicu_oleh: int,
    versi_sebelumnya: str | None = None,
) -> HasilKoreksi:
    """Jalankan satu putaran.  Tidak menyentuh basis data.

    ``baris`` berbentuk seperti yang :func:`build_reliability` minta:
    ``agent``, ``agent_decision``, ``council_decision``, ``direction_correct``.
    """
    laporan: ReliabilityReport = build_reliability(baris)
    terukur = laporan.measured

    bobot = {
        r.role.value: r.multiplier
        for r in terukur
        if r.multiplier is not None
    }

    if not bobot:
        return HasilKoreksi(
            versi=_versi(putaran),
            versi_sebelumnya=versi_sebelumnya,
            dipicu_oleh=dipicu_oleh,
            sampel=len(baris),
            garis_dasar=laporan.pasar_naik,
            bobot={},
            diterapkan=False,
            alasan=(
                f"tidak ada agen yang cukup sampelnya dari {len(baris)} suara "
                "berarah yang punya hasil"
            ),
        )

    return HasilKoreksi(
        versi=_versi(putaran),
        versi_sebelumnya=versi_sebelumnya,
        dipicu_oleh=dipicu_oleh,
        sampel=len(baris),
        garis_dasar=laporan.pasar_naik,
        bobot=bobot,
        diterapkan=True,
    )


def bobot_yang_berlaku(terakhir: dict[str, Any] | None) -> dict[str, float]:
    """Bobot dari putaran koreksi terakhir yang BENAR-BENAR diterapkan.

    Putaran yang sampelnya kurang tetap tercatat dengan ``diterapkan=False``,
    dan bobotnya tidak boleh dipakai - itu sebabnya barisnya ditulis: supaya
    kegagalan terlihat tanpa ikut berlaku.

    Kosong berarti belum ada keandalan terukur, dan modul XAU memperlakukan
    tiap suara sama berat.  Itu keadaan yang jujur, bukan kekurangan yang perlu
    ditambal dengan 1,0 di mana-mana.
    """
    if not terakhir or not terakhir.get("diterapkan"):
        return {}
    mentah = terakhir.get("bobot")
    if isinstance(mentah, str):
        import json

        try:
            mentah = json.loads(mentah)
        except ValueError:
            return {}
    if not isinstance(mentah, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in mentah.items()
        if isinstance(v, (int, float))
    }


# Sebuah `terapkan_bobot` pernah berdiri di sini, mengalikan keyakinan tiap
# agen lalu memulangkan petanya. Ia dihapus, bukan didaftarkan sebagai sengaja
# menganggur: pembobotannya pindah ke `aruna.xau.suara.rekap`, yang butuh
# jumlah terbobotnya - bukan petanya - untuk menimbang kontradiksi. Dua tempat
# yang mengalikan hal yang sama adalah dua tempat yang bisa berselisih.


__all__ = [
    "KOREKSI_TIAP",
    "HasilKoreksi",
    "bobot_yang_berlaku",
    "hitung_koreksi",
    "perlu_koreksi",
]
