"""Dimensi yang membentuk sebuah kondisi pasar (PASAL 15.3, 15.5).

**UNKNOWN bukan nilai; ia ketiadaan nilai.** Dua kondisi yang sama-sama tidak
diketahui bukan dua kondisi yang mirip, dan modul ini menolak memperlakukannya
begitu - lihat :func:`sama`. Tanpa penolakan itu, sidik jari yang membandingkan
tujuh ketiadaan dengan tujuh ketiadaan akan melaporkan kemiripan sempurna
terhadap dua kondisi yang tidak diketahui sama sekali.

Daftarnya dipisah dua dengan sengaja.

:data:`TERSIMPAN` adalah dimensi yang punya kolom historis dan **terukur**
terisi 95-100% pada 8.914 baris ``signal_snapshots`` (2026-08-21): ``regime``,
``risk_level``, ``news_state``, ``direction`` 100%; ``spread_bps`` 99,0%;
``signal_quality`` 95,3%.

:data:`TAK_TERSIMPAN` adalah dimensi yang PASAL 15.5 sebut dan **tidak pernah
ditulis ke database sama sekali** - volatility, volume, momentum, trend, open
interest, funding, structure. ``risk_history`` ada dan kosong. Ketujuhnya
UNKNOWN selamanya untuk rekaman lama; tidak ada backfill yang bisa
menghidupkan data yang tidak pernah ada.

Yang kedua tetap didaftar, bukan dihapus: sebuah dimensi yang hilang dari enum
tidak akan pernah muncul sebagai UNKNOWN di laporan mana pun, dan ketiadaannya
berhenti terlihat. Yang tidak terdaftar tidak pernah ditanyakan.
"""

from __future__ import annotations

from enum import StrEnum

#: Satu-satunya ejaan ketidaktahuan di paket ini. Dua ejaan berarti dua jalur
#: yang harus tetap sepakat, dan yang satu akan diam-diam menang.
UNKNOWN = "UNKNOWN"


class Dimensi(StrEnum):
    """Nilainya data - jangan diterjemahkan."""

    ASSET = "ASSET"
    MARKET = "MARKET"
    TIMEFRAME = "TIMEFRAME"
    #: PASAL 15.12 - ingatan dipisahkan berdasarkan rezim. Rezim adalah dimensi
    #: berbobot berat di sini, dan ``market_memories`` punya indeks
    #: ``idx_memory_regime`` supaya pertanyaan "apa yang terjadi pada rezim
    #: ini" bisa dijawab tanpa memindai seluruh tabel.
    REGIME = "REGIME"
    #: PASAL 15.34 - konteks risiko historis sebagai bukti: apa yang terjadi
    #: pada keputusan yang dibuat saat risikonya setinggi sekarang.
    RISK_LEVEL = "RISK_LEVEL"
    NEWS = "NEWS"
    QUALITY = "QUALITY"
    LIQUIDITY = "LIQUIDITY"
    # PASAL 15.5 menyebut ketujuh ini; tidak satu pun punya kolom historis.
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    MOMENTUM = "MOMENTUM"
    TREND = "TREND"
    OPEN_INTEREST = "OPEN_INTEREST"
    FUNDING = "FUNDING"
    STRUCTURE = "STRUCTURE"


#: Punya kolom historis dan terukur terisi. ``LIQUIDITY`` diturunkan dari
#: ``spread_bps`` (99,0%) - satu-satunya ukuran likuiditas yang benar-benar ada
#: di sejarah, dan yang membuat PASAL 15.17 bisa dijawab sama sekali.
TERSIMPAN: frozenset[Dimensi] = frozenset({
    Dimensi.ASSET,
    Dimensi.MARKET,
    Dimensi.TIMEFRAME,
    Dimensi.REGIME,
    Dimensi.RISK_LEVEL,
    Dimensi.NEWS,
    Dimensi.QUALITY,
    Dimensi.LIQUIDITY,
    # Kelima ini **tidak punya kolom** dan tetap tersedia: dihitung ulang dari
    # candle tersimpan pada bar yang ada saat keputusan itu dibuat - lihat
    # `aruna.memory.teknikal`. Ditambahkan 2026-08-21 sesudah evaluasi PASAL
    # 15.44 melaporkan selisih +3 poin: sidik jari berdimensi delapan tidak
    # cukup membedakan satu kondisi pasar dari yang lain.
    Dimensi.VOLATILITY,
    Dimensi.MOMENTUM,
    Dimensi.VOLUME,
    Dimensi.TREND,
    Dimensi.STRUCTURE,
})

#: Sisanya - **open interest dan funding**. Keduanya data venue perpetual yang
#: tidak pernah disimpan per keputusan dan tidak bisa dihitung ulang dari candle
#: spot. UNKNOWN selamanya sampai ada yang menyimpannya.
#:
#: Dihitung dan bukan diketik ulang: satu daftar yang harus dijaga tetap sepakat
#: dengan enum-nya adalah satu daftar yang suatu saat tidak.
TAK_TERSIMPAN: frozenset[Dimensi] = frozenset(Dimensi) - TERSIMPAN


def diketahui(nilai: object) -> bool:
    """Apakah nilai ini benar-benar terbaca.

    **Nol dihitung diketahui.** ``confidence=0`` berarti council menilai dan
    hasilnya nol, bukan berarti tidak terbaca - kelas kesalahan yang sama
    dengan ``side='FLAT'`` yang truthy, dan dengan ``news_risk: 0.0`` yang
    sempat dilaporkan sebagai lapisan yang hilang.
    """
    if nilai is None:
        return False
    if isinstance(nilai, str):
        teks = nilai.strip()
        return bool(teks) and teks.upper() != UNKNOWN
    return True


def normalkan(nilai: object) -> object | None:
    """Bentuk yang siap dibandingkan, atau ``None`` kalau tidak terbaca.

    Satu-satunya alasan fungsi ini ada: :func:`bandingkan` dipanggil **n kali
    lipat n** sementara sidiknya cuma ada n. Terprofil 2026-08-22 pada 900
    ingatan: 39,9 juta panggilan :func:`diketahui`, dan ``str.upper`` serta
    ``str.strip`` masing-masing **59,5 juta kali** - seluruhnya menormalkan
    teks yang sama berulang-ulang.

    Menormalkan sekali per sidik memindahkan kerja itu dari n kuadrat ke n.

    **Kesepakatannya dengan :func:`diketahui` diuji**, bukan diasumsikan:
    ``normalkan(x) is None`` harus selalu sama dengan ``not diketahui(x)``.
    Dua definisi "terbaca" yang melenceng akan membuat sebagian ingatan
    terbandingkan di satu jalur dan terbuang di jalur lain, tanpa satu pun
    galat.
    """
    if nilai is None:
        return None
    if isinstance(nilai, str):
        teks = nilai.strip()
        if not teks:
            return None
        atas = teks.upper()
        return None if atas == UNKNOWN else atas
    return nilai


def sama_ternormalkan(kiri: object, kanan: object) -> bool:
    """:func:`sama`, untuk dua nilai yang SUDAH lewat :func:`normalkan`.

    Keduanya dijamin bukan ``None`` oleh pemanggilnya, jadi tidak ada
    pemeriksaan "terbaca" yang diulang dan tidak ada ``strip().upper()``
    kedua. Itu tiga perempat dari kerja string di jalur panas.
    """
    if kiri == kanan:
        return True
    return (
        isinstance(kiri, str)
        and isinstance(kanan, str)
        and _serumpun(kiri, kanan)
    )


def sama(a: object, b: object) -> bool:
    """Apakah dua nilai dimensi cocok. ``UNKNOWN`` tidak pernah cocok.

    **Satu kelonggaran, khusus regime.** Baris yang ditulis sebelum taksonomi
    berarah ada menyimpan ``TRENDING`` dan ``BREAKOUT`` tanpa arah; yang baru
    menyimpan ``TRENDING_BULLISH``, ``TRENDING_BEARISH``, ``BREAKDOWN``.
    Terukur 2026-08-21: 9.897 ingatan memuat bentuk lamanya, dan dimensi REGIME
    berbobot 4 - menolak kecocokan lintas generasi berarti membuang seluruh
    korpus itu dalam satu langkah.

    Yang **tidak** dilonggarkan: dua arah yang berlawanan. ``TRENDING_BULLISH``
    tidak pernah cocok dengan ``TRENDING_BEARISH`` - kalau cocok, tidak ada yang
    bertambah dari pemisahan ini dan seluruh perubahannya sia-sia.
    """
    if not diketahui(a) or not diketahui(b):
        return False
    if isinstance(a, str) and isinstance(b, str):
        kiri, kanan = a.strip().upper(), b.strip().upper()
        return kiri == kanan or _serumpun(kiri, kanan)
    return a == b


def _serumpun(a: str, b: str) -> bool:
    """Satu nilai regime kasar melawan turunannya yang berarah.

    Hanya berlaku ketika **salah satunya** adalah bentuk kasar. Dua bentuk
    berarah dibandingkan persis, jadi naik tidak pernah cocok dengan turun.
    """
    from aruna.core.enums import Regime

    try:
        kiri, kanan = Regime(a), Regime(b)
    except ValueError:
        return False
    if kiri.naik is not None and kanan.naik is not None:
        return False
    return kiri.keluarga is kanan.keluarga


__all__ = [
    "TAK_TERSIMPAN",
    "TERSIMPAN",
    "UNKNOWN",
    "Dimensi",
    "diketahui",
    "sama",
]
