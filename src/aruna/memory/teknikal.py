"""Lima dimensi yang PASAL 15.5 minta, dihitung ulang dari candle tersimpan.

**Ini akar masalah Phase 15, dan ini perbaikannya.** Evaluasi retrospektif
2026-08-21 atas 1.671 keputusan melaporkan selisih **+3 poin** antara
SUPPORTIVE dan CONTRARY - derau. Sidik jarinya hanya punya delapan dimensi, dan
tujuh yang pasalnya sebut - volatility, volume, momentum, trend, open interest,
funding, structure - semuanya ``UNKNOWN`` karena tidak satu pun pernah ditulis
ke database.

Lima di antaranya ternyata **tidak perlu ditulis**: ``realised_volatility``,
``momentum``, ``volume_anomaly``, dan ``analyse_structure`` semuanya berjalan
atas :class:`~aruna.analysis.series.CandleSeries`, dan candle-nya tersimpan
sejak Juli. Dihitung ulang pada bar yang tersedia **saat keputusan itu dibuat**,
kelimanya lahir tanpa satu pun kolom baru - dan korpus 8.548 yang sudah ada
ikut terisi, bukan hanya ingatan yang akan datang.

Dua sisanya - **funding** dan **open interest** - data venue perpetual, dan
keduanya ternyata juga sudah ada: ``futures_plans.funding_cost_pct`` terisi
pada 192 baris, dan ``BinanceFuturesProvider`` punya ``open_interest()``
**dan** ``open_interest_history()`` yang terimplementasi penuh, masuk
allowlist, dan tidak pernah disimpan ke mana pun. Kelas cacat yang sama dengan
backtest yang dihitung lalu dibuang.

Keduanya hanya berlaku untuk ingatan **futures**: jalur spot tidak punya
kontrak perpetual, jadi di sana keduanya memang ``UNKNOWN`` - dan itu keadaan,
bukan kekurangan.

**Ambangnya diturunkan dari tercile korpus**, diukur 2026-08-21 atas 900
jendela di dua puluh aset kripto 15m. Ambang yang dipilih penulis kode adalah
ambang yang dipilih demi tampilannya; tercile membagi data menjadi tiga
kelompok yang benar-benar sama besar, dan itu yang membuat dimensi ini punya
daya beda.
"""

from __future__ import annotations

from typing import Any

from aruna.memory.dimensions import UNKNOWN, Dimensi

#: Tercile ``realised_volatility`` (persen). n=900, 2026-08-21.
VOLATILITAS_P33 = 0.161
VOLATILITAS_P67 = 0.300

#: Tercile ``momentum`` (persen perubahan 10 bar). Perhatikan p33 **negatif**:
#: separuh lebih pengamatan bergerak turun, dan ambang nol akan menyebut
#: sepertiga korpus "positif" hanya karena nol kebetulan bukan tengahnya.
MOMENTUM_P33 = -0.105
MOMENTUM_P67 = 0.405

#: Tercile ``volume_anomaly`` (rasio terhadap rata-rata).
VOLUME_P33 = 0.501
VOLUME_P67 = 1.046

#: Pola struktur yang dikenali. ``UNDETERMINED`` sengaja tidak ada di sini -
#: ia berarti swing-nya belum cukup untuk disimpulkan, dan itu ketiadaan
#: bacaan, bukan bacaan "datar".
_STRUKTUR = frozenset({"UPTREND", "DOWNTREND", "RANGE"})


def _angka(nilai: object) -> float | None:
    if nilai is None:
        return None
    try:
        return float(nilai)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def band_volatilitas(nilai: object) -> str:
    """Band volatilitas terwujud, atau ``UNKNOWN``."""
    angka = _angka(nilai)
    if angka is None:
        return UNKNOWN
    angka = abs(angka)
    if angka < VOLATILITAS_P33:
        return "LOW"
    if angka >= VOLATILITAS_P67:
        return "HIGH"
    return "MEDIUM"


def band_momentum(nilai: object) -> str:
    """Band momentum, atau ``UNKNOWN``. **Nol dihitung terbaca.**"""
    angka = _angka(nilai)
    if angka is None:
        return UNKNOWN
    if angka < MOMENTUM_P33:
        return "NEGATIVE"
    if angka >= MOMENTUM_P67:
        return "POSITIVE"
    return "FLAT"


def band_volume(nilai: object) -> str:
    """Band volume relatif, atau ``UNKNOWN``."""
    angka = _angka(nilai)
    if angka is None:
        return UNKNOWN
    if angka < VOLUME_P33:
        return "LOW"
    if angka >= VOLUME_P67:
        return "HIGH"
    return "NORMAL"


#: Ambang "tidak berubah" untuk open interest, dalam persen.
#:
#: Setengah persen: di bawah itu perubahannya sebanding dengan derau pembulatan
#: dan pergantian kontrak, dan menyebutnya "naik" akan membuat hampir setiap
#: bacaan punya arah.
OI_FLAT_PCT = 0.5


def band_funding(pct: object) -> str:
    """Arah biaya funding, atau ``UNKNOWN``.

    **Nol adalah bacaan.** Terukur 2026-08-21: 129 dari 192 baris
    ``funding_cost_pct`` berisi tepat nol - itu berarti biayanya terhitung dan
    hasilnya nol, bukan berarti tidak terbaca. Kelas kesalahan yang sama dengan
    ``confidence=0`` dan ``side='FLAT'``.
    """
    angka = _angka(pct)
    if angka is None:
        return UNKNOWN
    if angka > 0:
        return "POSITIVE"
    if angka < 0:
        return "NEGATIVE"
    return "FLAT"


def band_open_interest(sekarang: object, sebelumnya: object) -> str:
    """Arah perubahan open interest, atau ``UNKNOWN``.

    **Arahnya, bukan nilainya.** PASAL 15.5 mencontohkannya sendiri - "OI:
    Increasing" - dan itu memang satu-satunya bentuk yang sebanding antar aset:
    open interest BTC dan DOGE berbeda ribuan kali, jadi ambang mutlak apa pun
    akan menyebut yang satu besar dan yang lain nol selamanya.

    Satu bacaan tanpa pembanding menghasilkan ``UNKNOWN``, bukan ``FLAT``:
    "tidak berubah" dan "tidak ada yang tahu" adalah dua keadaan berbeda.
    """
    kini, lalu = _angka(sekarang), _angka(sebelumnya)
    if kini is None or lalu is None or lalu == 0:
        return UNKNOWN
    ubah = (kini - lalu) / abs(lalu) * 100.0
    if ubah > OI_FLAT_PCT:
        return "RISING"
    if ubah < -OI_FLAT_PCT:
        return "FALLING"
    return "FLAT"


def band_struktur(nilai: object) -> str:
    """Pola struktur harga, atau ``UNKNOWN``.

    ``UNDETERMINED`` dari lapisan struktur menjadi ``UNKNOWN``: ia berarti
    swing-nya belum cukup untuk disimpulkan, dan memperlakukannya sebagai
    "datar" akan mencampur dua keadaan yang berbeda.
    """
    if nilai is None:
        return UNKNOWN
    teks = str(getattr(nilai, "value", nilai)).strip().upper()
    return teks if teks in _STRUKTUR else UNKNOWN


#: Kelima dimensi yang modul ini isi. Dieja sekali supaya "kosong" dan "terisi"
#: tidak bisa berbeda isinya.
DIMENSI_TEKNIKAL = (
    Dimensi.VOLATILITY, Dimensi.MOMENTUM, Dimensi.VOLUME,
    Dimensi.TREND, Dimensi.STRUCTURE,
)


def kosong_teknikal() -> dict[Dimensi, str]:
    return dict.fromkeys(DIMENSI_TEKNIKAL, UNKNOWN)


def dimensi_dari_bacaan(
    *,
    volatility: object = None,
    momentum: object = None,
    volume: object = None,
    structure: object = None,
) -> dict[Dimensi, str]:
    """Lima dimensi dari empat bacaan yang **sudah** dihitung.

    Dipisahkan dari :func:`dimensi_teknikal` pada 2026-08-24 supaya jalur spot
    bisa memakainya tanpa kueri candle kedua. Konteks keputusan sudah membawa
    ``realised_volatility``, ``momentum``, ``volume_anomaly`` dan strukturnya -
    menghitungnya lagi dari 200 bar per simbol per horizon adalah kueri yang
    jawabannya sudah ada di tangan.

    **Pemetaannya satu tempat, dan ini tempatnya.** Kalau jalur spot memetakan
    angka ke band sendiri, dua ingatan dengan volatilitas yang sama bisa
    tercatat LOW di satu jalur dan MEDIUM di jalur lain - dan kemiripan yang
    dihitung di antara keduanya membandingkan dua hal yang kebetulan bernama
    sama.

    **TREND dan STRUCTURE dari sumber yang berbeda dengan sengaja.** STRUCTURE
    adalah urutan swing (higher-high / lower-low); TREND adalah arah momentum
    yang sama diringkas sebagai naik/turun/datar. Keduanya sering sejalan dan
    kadang tidak - dan justru ketidaksejalanan itu yang menerangkan sesuatu.
    """
    hasil = kosong_teknikal()
    hasil[Dimensi.VOLATILITY] = band_volatilitas(volatility)
    hasil[Dimensi.MOMENTUM] = band_momentum(momentum)
    hasil[Dimensi.VOLUME] = band_volume(volume)
    hasil[Dimensi.STRUCTURE] = band_struktur(structure)
    # TREND: arah, bukan besarnya. Momentum yang FLAT tetap punya arah nol -
    # dan itu keterangan, bukan ketiadaan.
    angka = _angka(momentum)
    hasil[Dimensi.TREND] = (
        UNKNOWN if angka is None
        else "BULLISH" if angka > 0
        else "BEARISH" if angka < 0
        else "SIDEWAYS"
    )
    return hasil


def dimensi_teknikal(series: Any) -> dict[Dimensi, str]:
    """Lima dimensi teknikal dari satu seri candle.

    ``series`` boleh ``None`` - hasilnya seluruhnya ``UNKNOWN``, bukan
    pengecualian: sebuah ingatan tanpa candle yang tersedia tetap ingatan yang
    sah, hanya lebih tipis.

    Menghitung keempat bacaannya lalu menyerahkan pemetaannya ke
    :func:`dimensi_dari_bacaan` - satu tempat yang memutuskan angka mana jadi
    band mana.
    """
    if series is None:
        return kosong_teknikal()

    try:
        from aruna.analysis import indicators as ind
        from aruna.analysis.structure import analyse_structure

        def _baca(reading: Any) -> object:
            return reading.value if getattr(reading, "available", False) else None

        return dimensi_dari_bacaan(
            volatility=_baca(ind.realised_volatility(series)),
            momentum=_baca(ind.momentum(series)),
            volume=_baca(ind.volume_anomaly(series)),
            structure=getattr(analyse_structure(series), "trend", None),
        )
    except Exception:  # noqa: BLE001 - ingatan yang lebih tipis, bukan gagal
        return kosong_teknikal()


__all__ = [
    "DIMENSI_TEKNIKAL",
    "MOMENTUM_P33",
    "MOMENTUM_P67",
    "OI_FLAT_PCT",
    "VOLATILITAS_P33",
    "VOLATILITAS_P67",
    "VOLUME_P33",
    "VOLUME_P67",
    "band_funding",
    "band_momentum",
    "band_open_interest",
    "band_struktur",
    "band_volatilitas",
    "band_volume",
    "dimensi_dari_bacaan",
    "dimensi_teknikal",
    "kosong_teknikal",
]
