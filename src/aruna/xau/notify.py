"""Kirim sinyal XAU ke operator.

**Hanya sinyal berarah yang dikirim.**  ``NO SIGNAL`` tersimpan lengkap dengan
sebabnya di basis data - itu catatannya - tapi tidak dikirim.  XAU memutuskan
288 kali sehari dan hampir semuanya diam; mengabarkan tiap diam akan mengubur
yang satu-satunya berarti.

**Pesannya menyebut apa yang TIDAK diukur.**  Gerbang spread tidak aktif -
Twelve Data tidak menerbitkan bid/ask - dan pesan yang diam soal itu membiarkan
operator mengira seluruh gerbang lulus.  Sebuah laporan yang menyembunyikan
lubangnya lebih berbahaya daripada lubang itu sendiri.

**ANALIS SAJA.**  Tidak ada order, tidak ada ukuran posisi, tidak ada leverage
maupun margin.  Angka yang dikirim adalah entry, stop, dan target sebagai
ANALISIS arah - bukan instruksi eksekusi, dan pesannya mengatakannya.
"""

from __future__ import annotations

from typing import Any

from aruna.core.logging import get_logger
from aruna.xau.keputusan import SinyalXau

log = get_logger(__name__)


def _angka(nilai: Any, digit: int = 2) -> str:
    return "-" if nilai is None else f"{float(nilai):,.{digit}f}"


def susun_pesan(
    sinyal: SinyalXau,
    *,
    as_of: Any,
    sesi: str | None = None,
    regime: Any = None,
    dolar: Any = None,
    berita: Any = None,
    versi_model: str = "",
) -> str:
    """Satu pesan untuk satu sinyal berarah."""
    geo = sinyal.geometri
    rekap = sinyal.rekap

    baris = [
        f"XAU/USD M5 — {sinyal.keputusan.value}",
        "",
        f"harga   {_angka(geo.entry if geo else None)}",
        f"stop    {_angka(geo.stop if geo else None)}",
        f"target  {_angka(geo.target if geo else None)}"
        + (f"  ({geo.sentuhan_target}x disentuh)" if geo else ""),
        f"RR      {_angka(geo.rr if geo else None)}"
        + (f"  target {_angka(geo.target_atr, 2)} ATR" if geo else ""),
        "",
        "— dasar —",
        f"bar     {as_of}",
        f"sesi    {sesi or 'tidak diukur'}",
    ]

    if regime is not None:
        baris.append(
            f"rezim   {regime.regime.value} ({regime.confidence}) "
            f"— bukti {regime.evidence_used}/{regime.evidence_available}"
        )
    if rekap is not None:
        baris.append(
            f"suara   {rekap.setuju} setuju / {rekap.menentang} menentang / "
            f"{rekap.netral} netral"
        )
        baris.append(
            f"kontra  {_angka(rekap.kontradiksi, 2)}"
            + ("  (berbobot keandalan)" if rekap.berbobot else "")
        )
    if sinyal.confidence is not None:
        baris.append(f"yakin   {_angka(sinyal.confidence, 2)}")

    if dolar is not None and dolar.terukur:
        baris.append(
            f"dolar   {dolar.simbol} r={_angka(dolar.korelasi, 3)} "
            f"atas {dolar.sampel} return"
        )
    if berita is not None and berita.terukur:
        if berita.berikutnya is not None:
            baris.append(
                f"rilis   {_angka(berita.menit_ke_berikutnya, 0)} menit lagi: "
                f"{berita.berikutnya.judul[:38]} ({berita.berikutnya.dampak.value})"
            )
        baris.append(f"padat   {berita.dampak_tinggi_24j} peristiwa HIGH dalam 24 jam")

    baris += [
        "",
        "— yang TIDAK diukur —",
        "spread  gerbang TIDAK AKTIF — venue tidak menerbitkan bid/ask",
    ]
    if berita is None or not berita.terukur:
        baris.append("berita  kalender tidak terbaca siklus ini")

    baris += [
        "",
        f"model   {versi_model}" if versi_model else "",
        "ARUNA menganalisa saja. Ini bukan instruksi eksekusi:",
        "tidak ada order, ukuran posisi, leverage, maupun margin.",
    ]
    return "\n".join(b for b in baris if b != "" or True).strip()


async def kirim_sinyal(sender: Any, pesan: str) -> bool:
    """Kirim, dan jangan pernah menjatuhkan loop karenanya.

    Barisnya sudah tersimpan sebelum ini dipanggil - catatan itu yang jadi
    kebenaran.  Kehilangan pesannya adalah kegagalan yang lebih kecil daripada
    kehilangan loop yang menghasilkannya.
    """
    if sender is None or not sender.configured:
        log.info("xau.notify_dilewati", sebab="pengirim tidak terkonfigurasi")
        return False
    terkirim = await sender.send(pesan)
    log.info("xau.notify", terkirim=terkirim)
    return terkirim


__all__ = ["kirim_sinyal", "susun_pesan"]
