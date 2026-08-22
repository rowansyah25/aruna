"""Bentuk kondisi pasar yang bisa dibandingkan (PASAL 15.4, 15.5).

**Ini bukan hash, dan tidak boleh menjadi hash.**
``signal_snapshots.fingerprint`` sudah ada, berisi SHA-256, dan menjawab
pertanyaan yang berbeda: *"apakah ini signal yang sama?"*. Ia dipakai
``FuturesRepository.verify()`` untuk membuktikan baris yang dinilai adalah
baris yang diterbitkan, dan menimpanya akan merusak itu.

PASAL 15.5 menuntut yang menjawab *"apakah ini pasar yang mirip?"* - dan sebuah
hash tidak bisa: dua kondisi yang nyaris identik menghasilkan hash yang sama
sekali berbeda. Yang dibutuhkan adalah daftar dimensi yang bisa dibandingkan
satu per satu, dan itu yang ada di sini.

**Kenapa band, bukan angka.** ``signal_quality`` 0-100 dan ``spread_bps``
pecahan tidak akan pernah sama persis antara dua kondisi; kemiripan yang
dihitung dari kesamaan persis pada angka kontinu selalu nol, dan yang dihitung
dari selisih menuntut skala yang dipilih seseorang. Band membuat
perbandingannya jujur dan bisa dijelaskan kepada operator: "kualitas tinggi
bertemu kualitas tinggi", bukan "57 vs 61 = 93% mirip".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from aruna.memory.dimensions import UNKNOWN, Dimensi, diketahui, normalkan

#: Ambang band kualitas. Dipilih terhadap sebaran terukur 2026-08-21:
#: ``signal_quality`` terisi 95,3% pada 8.914 baris, dan nilai yang muncul
#: berkumpul di lima puluhan.
QUALITY_LOW = 40
QUALITY_HIGH = 70

#: Ambang band likuiditas, dalam basis point. Terukur di produksi: pasangan
#: besar CRYPTO berada di bawah 1 bps; puluhan bps berarti buku tipis.
SPREAD_TIGHT = 5
SPREAD_WIDE = 50

#: Bentuk ``news_state`` yang benar-benar tersimpan, disalin dari produksi:
#: ``"1 item(s): 0+ / 0- / 1 unreadable"``.
_POLA_NEWS = re.compile(
    r"(\d+)\s*\+\s*/\s*(\d+)\s*-\s*/\s*(\d+)\s*unreadable", re.IGNORECASE
)

#: Bentuk **dominan** di sejarah, dan yang paling mudah terlewat: 5.980 dari
#: 8.914 baris (67%, terukur 2026-08-21). Versi pertama modul ini hanya
#: mengenali pola di atas - karena itu satu-satunya baris contoh yang dibaca -
#: dan dua pertiga korpus jatuh ke UNKNOWN tanpa satu pun test merah.
_TANPA_BERITA = "NO_RECENT_NEWS"


def band_kualitas(nilai: object) -> str:
    """Band kualitas signal, atau ``UNKNOWN``.

    Yang tidak terbaca **bukan** ``LOW``: menganggapnya begitu akan membuat
    setiap rekaman lama tanpa quality terlihat sebagai setup buruk - sebuah
    kesimpulan yang tidak pernah diukur siapa pun.
    """
    if not diketahui(nilai):
        return UNKNOWN
    try:
        angka = float(nilai)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return UNKNOWN
    if angka < QUALITY_LOW:
        return "LOW"
    if angka >= QUALITY_HIGH:
        return "HIGH"
    return "MEDIUM"


def band_likuiditas(spread_bps: object) -> str:
    """Band likuiditas dari spread (PASAL 15.17), atau ``UNKNOWN``.

    ``spread_bps`` terisi 99,0% di sejarah dan merupakan satu-satunya ukuran
    likuiditas yang benar-benar tersimpan - depth dan volume tidak pernah
    ditulis per signal.
    """
    if not diketahui(spread_bps):
        return UNKNOWN
    try:
        angka = Decimal(str(spread_bps))
    except (InvalidOperation, TypeError, ValueError):
        return UNKNOWN
    if angka < SPREAD_TIGHT:
        return "TIGHT"
    if angka >= SPREAD_WIDE:
        return "WIDE"
    return "NORMAL"


def band_news(news_state: object) -> str:
    """Arah berita dari ``news_state``, atau ``UNKNOWN``.

    ``UNREADABLE`` **tidak** dilebur ke ``NEUTRAL``: netral berarti beritanya
    dibaca dan tidak condong ke mana pun; unreadable berarti beritanya ada dan
    tidak ada yang tahu isinya. Meleburnya membuat hari yang datanya rusak
    terbaca persis seperti hari yang tenang.

    Format yang tidak dikenali menghasilkan ``UNKNOWN``, bukan ``NEUTRAL`` -
    format yang berubah adalah kegagalan pembacaan, dan menerjemahkannya jadi
    nilai yang sah akan menyembunyikannya (§13.26).
    """
    if not diketahui(news_state):
        return UNKNOWN
    teks = str(news_state).strip()
    if teks.upper() == _TANPA_BERITA:
        # Lapisan berita berjalan dan tidak menemukan apa-apa. Bukan UNKNOWN
        # (tidak ada yang memeriksa) dan bukan NEUTRAL (ada berita yang saling
        # mengimbangi) - tiga keadaan berbeda, dan meleburnya membuat hari sepi
        # tidak bisa dibedakan dari hari yang beritanya tidak terbaca.
        return "NO_NEWS"
    cocok = _POLA_NEWS.search(teks)
    if not cocok:
        return UNKNOWN
    positif, negatif, tak_terbaca = (int(g) for g in cocok.groups())
    if positif > negatif:
        return "POSITIVE"
    if negatif > positif:
        return "NEGATIVE"
    # Seimbang. Kalau yang seimbang itu nol-nol dan seluruh isinya tak terbaca,
    # yang jujur adalah mengatakan tidak terbaca.
    if positif == 0 and negatif == 0 and tak_terbaca > 0:
        return "UNREADABLE"
    return "NEUTRAL"


@dataclass(frozen=True, slots=True)
class Sidik:
    """Kondisi pasar sebagai daftar dimensi yang bisa dibandingkan.

    Setiap anggota :class:`~aruna.memory.dimensions.Dimensi` selalu punya
    nilainya - yang tidak terbaca berisi ``UNKNOWN``, bukan hilang dari peta.
    Kunci yang hilang memaksa tiap pembaca menulis ``.get(d, UNKNOWN)``
    sendiri, dan yang lupa akan meledak jauh dari sini.
    """

    nilai: Mapping[Dimensi, str]
    #: ``nilai`` yang sudah dinormalkan sekali, ``None`` untuk yang tidak
    #: terbaca. Dihitung saat konstruksi, bukan saat perbandingan.
    #:
    #: :func:`~aruna.memory.similarity.bandingkan` dipanggil n kali lipat n
    #: sementara sidiknya cuma ada n. Terprofil 2026-08-22: 59,5 juta
    #: ``str.upper`` dan 59,5 juta ``str.strip`` untuk menormalkan teks yang
    #: sama berulang-ulang.
    #:
    #: ``compare=False`` supaya kesamaan dua :class:`Sidik` tetap ditentukan
    #: ``nilai`` saja - bidang turunan yang ikut menentukan kesamaan adalah
    #: dua sumber kebenaran untuk satu pertanyaan.
    normal: Mapping[Dimensi, object] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "normal",
            {d: normalkan(v) for d, v in self.nilai.items()},
        )

    @classmethod
    def dari_snapshot(cls, row: Mapping[str, Any]) -> Sidik:
        """Dari satu baris ``signal_snapshots``."""
        return cls._susun(
            symbol=row.get("symbol"),
            market=row.get("market_code"),
            timeframe=row.get("horizon_code"),
            regime=row.get("regime"),
            risk_level=row.get("risk_level"),
            news=row.get("news_state"),
            quality=row.get("signal_quality"),
            spread_bps=row.get("spread_bps"),
        )

    @classmethod
    def dari_konteks(
        cls,
        *,
        symbol: object,
        market: object,
        timeframe: object,
        regime: object,
        risk_level: object,
        news: object,
        quality: object,
        spread_bps: object,
    ) -> Sidik:
        """Dari kondisi pasar sekarang.

        Lewat pembangun yang sama dengan :meth:`dari_snapshot` dengan sengaja:
        dua jalur yang menghasilkan bentuk berbeda akan membandingkan dua hal
        yang tidak sebanding, dan skornya tetap keluar tanpa ada yang melihat.
        """
        return cls._susun(
            symbol=symbol, market=market, timeframe=timeframe, regime=regime,
            risk_level=risk_level, news=news, quality=quality,
            spread_bps=spread_bps,
        )

    @classmethod
    def _susun(
        cls,
        *,
        symbol: object,
        market: object,
        timeframe: object,
        regime: object,
        risk_level: object,
        news: object,
        quality: object,
        spread_bps: object,
    ) -> Sidik:
        def teks(nilai: object) -> str:
            return str(nilai).strip() if diketahui(nilai) else UNKNOWN

        nilai: dict[Dimensi, str] = {d: UNKNOWN for d in Dimensi}
        nilai[Dimensi.ASSET] = teks(symbol)
        nilai[Dimensi.MARKET] = teks(market)
        nilai[Dimensi.TIMEFRAME] = teks(timeframe)
        nilai[Dimensi.REGIME] = teks(regime)
        nilai[Dimensi.RISK_LEVEL] = teks(risk_level)
        nilai[Dimensi.NEWS] = band_news(news)
        nilai[Dimensi.QUALITY] = band_kualitas(quality)
        nilai[Dimensi.LIQUIDITY] = band_likuiditas(spread_bps)
        return cls(nilai=nilai)

    def dengan(self, tambahan: Mapping[Dimensi, str]) -> Sidik:
        """Sidik jari baru dengan dimensi tambahan terisi (PASAL 15.5).

        **Yang ``UNKNOWN`` tidak menimpa yang sudah terbaca.** Perkayaan yang
        gagal - candle-nya tidak tersedia, barnya kurang - tidak boleh
        mengosongkan dimensi yang sudah ada; ia hanya tidak menambah apa-apa.

        Memulangkan sidik jari **baru**: yang lama beku, dan menyuntingnya di
        tempat akan mengubah sidik jari yang mungkin sudah dipakai
        perbandingan lain.
        """
        nilai = dict(self.nilai)
        for d, v in tambahan.items():
            if diketahui(v):
                nilai[d] = str(v)
        return Sidik(nilai=nilai)

    def diketahui(self) -> frozenset[Dimensi]:
        """Dimensi yang benar-benar terbaca pada kondisi ini."""
        return frozenset(d for d, v in self.nilai.items() if diketahui(v))


__all__ = [
    "QUALITY_HIGH",
    "QUALITY_LOW",
    "SPREAD_TIGHT",
    "SPREAD_WIDE",
    "Sidik",
    "band_kualitas",
    "band_likuiditas",
    "band_news",
]
