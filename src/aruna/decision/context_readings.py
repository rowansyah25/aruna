"""Dari analisis Phase 3 dan council ke komponen Decision Score (PASAL 14.16).

:mod:`aruna.decision.score` sengaja tidak tahu apa pun tentang RSI, struktur,
atau suara agent - ia hanya menjumlahkan bukti berarah. Penerjemahannya ada di
sini, dan itu tempat yang benar untuk satu alasan: **yang tahu apakah sebuah
angka mendukung LONG atau SHORT adalah lapisan yang mengukurnya.** Menebak arah
di dalam penjumlah akan sesekali membalik tanda pada komponen berbobot terbesar.

Empat keputusan yang menentukan apakah terjemahannya jujur:

**1. Yang tidak berarah tidak dipaksa berarah.** ``RETEST`` dan ``REJECTION``
adalah keadaan tanpa arah bawaan - sebuah retest bisa mendahului lanjutan
maupun pembalikan. Keduanya tidak dipetakan sama sekali, dan komponennya
tercatat tidak terukur. Memilih salah satu arah untuknya akan menghasilkan
angka yang selalu masuk akal dan separuh waktunya terbalik.

**2. Volume adalah konfirmasi, bukan arah.** Volume yang naik tidak mengatakan
harga akan naik; ia mengatakan gerakan yang sedang terjadi didukung. Jadi
tandanya diambil dari trennya, dan besarnya dari volume. Kalau trennya tidak
diketahui, volume tidak menyumbang apa pun - bukan menyumbang nol.

**3. RSI dipakai untuk momentum karena ia sudah terbatas.** Nilainya 0..100
dengan 50 sebagai netral, jadi pemetaannya tidak butuh skala karangan di luar
konvensi 30/70 yang sudah dipakai seluruh sistem ini. Momentum persen butuh
"berapa persen yang dianggap penuh", dan angka itu akan menjadi tebakan yang
menyamar sebagai kalibrasi.

**4. Risiko dan berita masuk sebagai potongan yang SUDAH dihitung Phase 13.**
Tidak diturunkan ulang di sini. Dua tempat yang menghitung risiko dari bahan
yang sama adalah dua tempat yang harus tetap sepakat, dan mereka tidak akan.
"""

from __future__ import annotations

from typing import Any

from aruna.decision.score import Arah

#: Arah tren struktur. ``UNDETERMINED`` sengaja tidak ada - ia berarti
#: penganalisisnya tidak bisa memutuskan, dan itu bukan nol.
TREN: dict[str, float] = {
    "UPTREND": 1.0,
    "DOWNTREND": -1.0,
    #: Range adalah pengukuran: tidak ada tren. Berbeda dari tidak tahu.
    "RANGE": 0.0,
}

#: Keadaan penembusan.
#:
#: Penembusan palsu dibalik tandanya dengan sengaja: sebuah penembusan ke atas
#: yang gagal adalah bukti bearish, bukan bukti bullish yang lemah. Itu
#: perbedaan yang paling sering hilang ketika keadaan ini dipetakan menurut
#: namanya.
#:
#: ``RETEST`` dan ``REJECTION`` tidak ada di sini - lihat catatan modul.
TEMBUS: dict[str, float] = {
    "BREAKOUT_UP": 1.0,
    "BREAKOUT_DOWN": -1.0,
    "FALSE_BREAKOUT_UP": -1.0,
    "FALSE_BREAKOUT_DOWN": 1.0,
    "NONE": 0.0,
}

#: Jarak RSI dari 50 yang dihitung sebagai momentum penuh.
#:
#: Dua puluh, yang menempatkan 70 dan 30 - dua ambang konvensional yang sudah
#: dipakai seluruh sistem ini - tepat di ujungnya. Bukan hasil kalibrasi, dan
#: tidak disebut begitu.
RSI_PENUH = 20.0

#: Kenaikan volume, dalam persen, yang dihitung sebagai konfirmasi penuh.
#:
#: Lima puluh persen di atas jendela sebelumnya. Angka pilihan, bukan hasil
#: pengukuran - dan satu-satunya angka pilihan di modul ini yang tidak punya
#: konvensi untuk bersandar.
VOLUME_PENUH = 50.0


def _jepit(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _nilai(readings: Any, nama: str) -> float | None:
    """Nilai satu pembacaan Phase 3, atau ``None`` kalau tidak bisa dipakai."""
    if not readings:
        return None
    r = readings.get(nama) if hasattr(readings, "get") else None
    if r is None:
        return None
    if not getattr(r, "usable", True):
        return None
    v = getattr(r, "value", None)
    return None if v is None else float(v)


def _nama(x: Any) -> str:
    return str(getattr(x, "value", x) or "")


def readings_from_analysis(
    *,
    structure: Any = None,
    readings: Any = None,
    decision: Arah = Arah.NO_SIGNAL,
    split: Any = None,
    risk_score: float | None = None,
    news_risk: float | None = None,
) -> dict[str, float]:
    """Komponen Decision Score dari bahan yang sudah dihitung lapisan lain.

    Hanya kunci yang **benar-benar terukur** yang dikembalikan. Yang tidak
    terukur tidak muncul sama sekali, dan :func:`aruna.decision.score.score`
    menghitungnya sebagai tidak terukur - bukan sebagai nol.
    """
    keluar: dict[str, float] = {}

    tren: float | None = None
    if structure is not None:
        tren = TREN.get(_nama(getattr(structure, "trend", None)))
        if tren is not None:
            keluar["trend"] = tren
        tembus = TEMBUS.get(_nama(getattr(structure, "breakout", None)))
        if tembus is not None:
            keluar["structure"] = tembus

    rsi = _nilai(readings, "rsi")
    if rsi is not None:
        keluar["momentum"] = _jepit((rsi - 50.0) / RSI_PENUH)

    # Volume hanya berarti bersama arahnya. Tanpa tren yang diketahui ia tidak
    # menyumbang - lihat catatan modul.
    vt = _nilai(readings, "volume_trend")
    if vt is not None and tren is not None and tren != 0.0:
        keluar["volume"] = _jepit(vt / VOLUME_PENUH) * (1.0 if tren > 0 else -1.0)

    sepakat = _kesepakatan(split, decision)
    if sepakat is not None:
        keluar["agreement"] = sepakat

    if risk_score is not None:
        keluar["risk"] = max(0.0, min(1.0, risk_score / 100.0))
    if news_risk is not None:
        keluar["news"] = max(0.0, min(1.0, news_risk / 100.0))

    return keluar


def _kesepakatan(split: Any, decision: Arah) -> float | None:
    """Seberapa bulat council, dengan tanda mengikuti keputusannya.

    ``split`` menghitung setuju dan kontra **terhadap keputusan council**,
    bukan terhadap LONG. Jadi tandanya harus diambil dari keputusannya - dan
    lupa melakukan itu akan membuat council yang bulat pada SHORT menyumbang
    poin untuk LONG.
    """
    if split is None or decision is Arah.NO_SIGNAL:
        return None
    setuju = len(getattr(split, "setuju", ()) or ())
    kontra = len(getattr(split, "kontra", ()) or ())
    total = setuju + kontra
    if total == 0:
        # Abstain tidak dihitung: agent yang tidak punya bukti tidak sedang
        # setuju maupun menolak, dan council yang seluruhnya abstain bukan
        # council yang sepakat.
        return None
    besar = (setuju - kontra) / total
    return besar if decision is Arah.LONG else -besar


__all__ = [
    "RSI_PENUH",
    "TEMBUS",
    "TREN",
    "VOLUME_PENUH",
    "readings_from_analysis",
]
