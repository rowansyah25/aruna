"""Boleh tidaknya data XAU dipakai menilai - dan kalau tidak, kenapa.

Spec menetapkan empat keadaan yang wajib menghasilkan ``NO SIGNAL``: data
basi, data hilang, data invalid, dan timestamp tidak konsisten.  Ketiga yang
pertama sudah punya pengukurnya di :mod:`aruna.data.quality`; berkas ini tidak
menulis ulang logika itu, ia menerjemahkan hasilnya jadi satu kalimat yang bisa
dibaca operator dan disimpan di kolom alasan.

**Alasannya yang penting, bukan sekadar penolakannya.**  "Tidak ada sinyal
karena memang tak ada setup" dan "tidak ada sinyal karena feed mati" terlihat
sama persis dari luar - yang pertama normal, yang kedua kerusakan.  Tanpa
kalimat sebab di sini, laporan "XAU diam hari ini" tidak bisa dibantah.

**``Kelayakan.layak = True`` bukan sinyal.**  Ia berarti bahannya cukup untuk
DINILAI.  Arahnya diputuskan dewan di rencana berikutnya, jadi
:attr:`Kelayakan.keputusan` di sini selalu ``NO_SIGNAL`` - kelayakan hanya
sanggup menolak, tidak pernah menaikkan apa pun jadi BUY atau SELL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aruna.core.enums import Decision
from aruna.data.quality import QualityGate, find_candle_gaps
from aruna.xau.timeframes import TumpukanTimeframe


@dataclass(frozen=True, slots=True)
class Kelayakan:
    """Hasil pemeriksaan bahan.  ``alasan`` terisi hanya saat menolak."""

    layak: bool
    alasan: str | None = None

    @property
    def keputusan(self) -> Decision:
        """Selalu ``NO_SIGNAL``.

        Kelayakan hanya bisa MENOLAK.  Yang menaikkan sesuatu jadi BUY atau
        SELL adalah dewan, bukan pemeriksa data - dan menaruh keputusan arah di
        sini akan membuat "bahannya cukup" tak bisa dibedakan dari "ada setup".
        """
        return Decision.NO_SIGNAL


def periksa_kelayakan(
    tumpukan: TumpukanTimeframe,
    gate: QualityGate,
    *,
    sekarang: datetime,
) -> Kelayakan:
    """Periksa berurutan, berhenti di penolakan pertama.

    Urutannya disengaja: yang paling murah dan paling menentukan lebih dulu.
    Tidak ada gunanya melaporkan timeframe yang kurang kalau barnya sendiri
    rusak.
    """
    if not tumpukan.m5:
        return Kelayakan(False, "tidak ada bar M5 sama sekali")

    for candle in tumpukan.m5:
        verdict = gate.evaluate_candle(candle)
        if verdict.blocks_signal:
            return Kelayakan(
                False,
                f"bar M5 {candle.open_time:%Y-%m-%d %H:%M} invalid: {verdict}",
            )

    # Akhir pekan valas sudah disaring FOREX_CALENDAR di find_candle_gaps, jadi
    # yang sampai ke sini adalah slot yang venue-nya memang sedang berdagang.
    lubang = find_candle_gaps(tumpukan.m5)
    if lubang:
        mulai, selesai, jumlah = lubang[0]
        return Kelayakan(
            False,
            f"lubang {jumlah} bar M5 antara {mulai:%d/%m %H:%M} dan {selesai:%d/%m %H:%M}",
        )

    terakhir = tumpukan.m5[-1]
    umur = (sekarang - terakhir.close_time).total_seconds()
    batas = gate.staleness_limit(terakhir.market)
    if umur > batas:
        return Kelayakan(
            False,
            f"bar M5 terakhir basi: {umur:.0f} detik, batas {batas:.0f}",
        )

    kurang = tumpukan.kurang()
    if kurang:
        nama = ", ".join(tf.value for tf in kurang)
        return Kelayakan(False, f"timeframe belum cukup bahannya: {nama}")

    return Kelayakan(True)


__all__ = ["Kelayakan", "periksa_kelayakan"]
