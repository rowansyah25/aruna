"""Apa yang dianggap layak dilihat lebih dekat (PASAL 14, 15).

Council itu mahal: tiga ronde, sebelas agent, sanggahan dan rebuttal. Kalau
setiap perubahan harga masuk ke sana, dua hal rusak sekaligus - biayanya, dan
artinya. Sistem yang menganalisis segalanya tidak sedang memilih apa pun.

Jadi tahap pertama bukan analisis. Ia aritmetika murah atas bar yang sudah
tersimpan, dan tugasnya cuma satu: memisahkan yang bergerak dari yang diam.

**Ambangnya diukur, bukan dikarang.** Setiap deteksi di sini membandingkan satu
angka dengan garis dasarnya sendiri - volume terhadap rata-rata volume aset
itu, range terhadap ATR-nya sendiri - bukan dengan konstanta yang berlaku untuk
semua. BTC yang bergerak 1% dan sebuah altcoin yang bergerak 1% bukan peristiwa
yang sama, dan ambang tunggal akan menyamakannya.

**Bukti tidak cukup, jangan mengarang peristiwa.** Kalau bar yang tersimpan
terlalu sedikit untuk membentuk garis dasar, hasilnya adalah tidak ada
peristiwa - bukan peristiwa berseverity nol. Nol yang berarti "tidak bisa
diukur" adalah nol yang dilarang (SPEC 4).

**Yang tidak bisa diukur di sini tidak dipura-purakan.** PASAL 15 juga menyebut
perubahan open interest, anomali funding, dan likuidasi. Ketiganya milik
perpetual dan hanya ada di feed futures; tidak satu pun bisa dihitung dari bar
spot. Jenisnya didefinisikan supaya jalur futures tinggal mengisinya, dan
sampai itu ada, tidak ada kode di sini yang menghasilkannya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any

#: Bar tertutup paling sedikit yang dibutuhkan untuk membentuk garis dasar.
#: Di bawah ini rata-rata dan ATR-nya adalah angka yang dihitung dari terlalu
#: sedikit hal untuk berarti apa pun.
MIN_BASELINE_BARS = 20


class EventKind(StrEnum):
    """Yang dicari pemindai cepat. Nilainya data - jangan diterjemahkan."""

    PRICE_MOVE = "PRICE_MOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    #: Milik perpetual. Didefinisikan supaya jalur futures punya tempat
    #: mengisinya; tidak ada kode spot yang menghasilkannya.
    OPEN_INTEREST_CHANGE = "OPEN_INTEREST_CHANGE"
    FUNDING_ANOMALY = "FUNDING_ANOMALY"
    LIQUIDATION = "LIQUIDATION"
    #: Milik news engine.
    NEWS = "NEWS"


@dataclass(frozen=True, slots=True)
class SignificantEvent:
    """Satu alasan untuk melihat lebih dekat, beserta angka yang melahirkannya.

    ``severity`` adalah kelipatan terhadap garis dasarnya, bukan skor 0..1 yang
    dinormalkan. Kelipatan bisa dibantah - "volume 4,2x rata-rata" adalah
    klaim yang bisa diperiksa - sedangkan skor 0..1 menyembunyikan dari mana
    angkanya datang, dan pembacanya tidak punya cara tahu apakah 0,8 itu besar.
    """

    symbol: str
    kind: EventKind
    #: Berapa kali melewati AMBANGNYA SENDIRI. 1,0 berarti tepat di ambang.
    #:
    #: Bukan kelipatan terhadap garis dasar, dan bedanya penting karena angka
    #: ini yang mengurutkan antrean. Versi pertama menaruh dua besaran berbeda
    #: di sini - volume sebagai kelipatan rata-rata, break sebagai jarak dalam
    #: ATR - dan keduanya tidak sebanding: sebuah break sungguhan bernilai
    #: 0,40 kalah dari lonjakan volume 3,00 yang tepat menyentuh ambangnya.
    #: Yang lebih layak dianalisis justru yang dibuang duluan.
    #:
    #: Dinormalkan ke ambang, keduanya menjawab satu pertanyaan yang sama:
    #: seberapa jauh melewati garis yang kita tetapkan sendiri. Angka mentahnya
    #: tetap ada di ``evidence`` supaya tidak ada yang hilang.
    severity: float
    detail: str
    at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind.value,
            "severity": round(self.severity, 3),
            "detail": self.detail,
            "at": self.at.isoformat(),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ScanThresholds:
    """Garis pemisah bergerak-atau-diam.

    Semuanya kelipatan terhadap garis dasar aset itu sendiri, bukan angka
    mutlak: satu ambang mutlak untuk BTC dan sebuah altcoin akan menyamakan
    dua peristiwa yang tidak sama.
    """

    #: Volume bar terakhir dibanding rata-rata bar sebelumnya.
    volume_spike: float = 3.0
    #: True range bar terakhir dibanding ATR.
    volatility_spike: float = 2.5
    #: Pergerakan bar terakhir dibanding ATR.
    price_move: float = 1.5
    #: Seberapa jauh melewati high/low sebelumnya, dalam kelipatan ATR, sebelum
    #: disebut break. Nol akan menjadikan setiap sentuhan sebuah peristiwa.
    breakout_atr: float = 0.25


def _f(value: Any) -> float | None:
    """Angka yang bisa dihitung, atau ``None``.

    NaN dan tak-hingga ditolak di sini, bukan dibiarkan mengalir: NaN lolos
    setiap perbandingan sebagai False, jadi ambang mana pun akan diam-diam
    tidak pernah terpenuhi dan pemindainya tampak seperti pasar yang tenang.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _true_range(bar: dict[str, Any], previous_close: float | None) -> float | None:
    high, low = _f(bar.get("high")), _f(bar.get("low"))
    if high is None or low is None:
        return None
    spans = [high - low]
    if previous_close is not None:
        spans.append(abs(high - previous_close))
        spans.append(abs(low - previous_close))
    return max(spans)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Hasil satu pemindaian, termasuk ketika tidak ada yang bisa dipindai.

    Ada karena ``scan`` mengembalikan daftar kosong untuk dua keadaan yang
    berlawanan: pasar yang benar-benar diam, dan bukti yang tidak cukup untuk
    membentuk garis dasar. Dari luar keduanya identik, dan menyamakannya adalah
    nol yang berarti "tidak tahu" (SPEC 4).

    Ini ditulis karena docstring ``scan`` menjanjikannya sementara kelasnya
    tidak pernah ada - janji tanpa kode, persis yang dipolisi proyek ini.
    """

    symbol: str
    events: tuple[SignificantEvent, ...]
    #: Bar tertutup yang benar-benar bisa dipakai sebagai garis dasar.
    usable_bars: int
    #: ``False`` berarti pemindaian tidak pernah benar-benar dijalankan.
    scanned: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "scanned": self.scanned,
            "usable_bars": self.usable_bars,
            "reason": self.reason,
            "events": [e.to_dict() for e in self.events],
        }


def scan_symbol(
    symbol: str,
    bars: list[dict[str, Any]],
    *,
    thresholds: ScanThresholds | None = None,
) -> ScanResult:
    """:func:`scan`, tapi memisahkan "diam" dari "tidak bisa diukur"."""
    usable = sum(
        1 for b in bars[:-1] if _f(b.get("close")) is not None
    ) if len(bars) >= 2 else 0
    if usable < MIN_BASELINE_BARS:
        return ScanResult(
            symbol=symbol,
            events=(),
            usable_bars=usable,
            scanned=False,
            reason=(
                f"hanya {usable} bar yang bisa dipakai, garis dasar butuh "
                f"{MIN_BASELINE_BARS}"
            ),
        )
    return ScanResult(
        symbol=symbol,
        events=tuple(scan(symbol, bars, thresholds=thresholds)),
        usable_bars=usable,
        scanned=True,
    )


def scan(
    symbol: str,
    bars: list[dict[str, Any]],
    *,
    thresholds: ScanThresholds | None = None,
) -> list[SignificantEvent]:
    """Peristiwa yang layak dianalisis dalam, dari bar tertutup terbaru.

    ``bars`` diurutkan dari yang paling lama ke yang paling baru - urutan yang
    sama dengan ``MarketDataRepository.candles``. Yang dinilai adalah bar
    TERAKHIR terhadap bar-bar sebelumnya; bar itu sendiri tidak pernah ikut
    membentuk garis dasarnya sendiri, karena membandingkan sesuatu dengan
    dirinya sendiri selalu menghasilkan "biasa".

    Mengembalikan daftar kosong kalau tidak ada yang bergerak, DAN kalau
    buktinya tidak cukup. Keduanya sama-sama "tidak ada peristiwa" di sini;
    yang membedakannya adalah :func:`scan_symbol`, yang membungkus fungsi ini
    dan menyatakan apakah pemindaian benar-benar dijalankan. Pakai itu kalau
    perbedaannya penting - dan pada jalur operator, perbedaannya selalu
    penting (SPEC 4).
    """
    limits = thresholds or ScanThresholds()
    if len(bars) < 2:
        return []

    latest, history = bars[-1], bars[:-1]
    close = _f(latest.get("close"))
    open_ = _f(latest.get("open"))
    at = latest.get("close_time") or latest.get("open_time")
    if close is None or open_ is None or not isinstance(at, datetime):
        return []

    closes = [c for c in (_f(b.get("close")) for b in history) if c is not None]
    volumes = [v for v in (_f(b.get("volume")) for b in history) if v is not None]
    # SATU penjaga garis dasar, bukan dua. Versi pertama juga memeriksa
    # ``len(bars)`` di atas, dan keduanya saling menutupi: mencabut salah satu
    # tidak mengubah perilaku, sehingga tidak ada test yang bisa mengikat
    # keduanya sendiri-sendiri. Redundansi yang tak bisa diisolasi adalah
    # bentuk kode yang membusuk - seseorang menghapus satu karena tampak mati,
    # yang lain menutupinya diam-diam, sampai yang itu ikut dihapus juga.
    #
    # Yang dihitung adalah bar yang BISA DIPAKAI, bukan bar yang ada: sebuah
    # riwayat lima puluh bar yang empat puluh di antaranya tanpa harga tetap
    # bukan garis dasar.
    if len(closes) < MIN_BASELINE_BARS:
        return []

    # ATR atas riwayat, bukan termasuk bar yang sedang dinilai.
    ranges: list[float] = []
    previous = None
    for bar in history:
        span = _true_range(bar, previous)
        if span is not None:
            ranges.append(span)
        previous = _f(bar.get("close"))
    atr = sum(ranges) / len(ranges) if ranges else None

    out: list[SignificantEvent] = []

    def add(
        kind: EventKind,
        measured: float,
        threshold: float,
        detail: str,
        **evidence: Any,
    ) -> None:
        """Severity dinormalkan ke ambangnya sendiri, angka mentah disimpan.

        ``measured / threshold`` adalah satu-satunya bentuk yang bisa
        dibandingkan antar jenis peristiwa - lihat catatan di
        :class:`SignificantEvent`.
        """
        out.append(
            SignificantEvent(
                symbol=symbol,
                kind=kind,
                severity=measured / threshold if threshold > 0 else measured,
                detail=detail,
                at=at,
                evidence={"measured": measured, "threshold": threshold, **evidence},
            )
        )

    # ---- volume ------------------------------------------------------
    if volumes:
        mean_volume = sum(volumes) / len(volumes)
        latest_volume = _f(latest.get("volume"))
        if mean_volume > 0 and latest_volume is not None:
            ratio = latest_volume / mean_volume
            if ratio >= limits.volume_spike:
                add(
                    EventKind.VOLUME_SPIKE,
                    ratio,
                    limits.volume_spike,
                    f"volume {ratio:.1f}x rata-rata {len(volumes)} bar sebelumnya",
                    volume=latest_volume,
                    baseline=mean_volume,
                    bars=len(volumes),
                )

    if atr is None or atr <= 0:
        # Tanpa ATR tidak ada garis dasar untuk gerakan maupun break. Volume di
        # atas tetap sah karena garis dasarnya sendiri.
        return out

    # ---- pergerakan dan volatilitas ----------------------------------
    move = abs(close - open_)
    if move / atr >= limits.price_move:
        direction = "naik" if close > open_ else "turun"
        add(
            EventKind.PRICE_MOVE,
            move / atr,
            limits.price_move,
            f"bar bergerak {direction} {move / atr:.1f}x ATR",
            move=close - open_,
            atr=atr,
        )

    # Close bar TETANGGA, bukan `closes[-1]`. Keduanya sama sampai satu bar
    # riwayat kehilangan harganya: `closes` sudah tersaring, jadi elemen
    # terakhirnya bisa berasal dari dua atau tiga bar sebelumnya, dan true
    # range-nya lalu diukur terhadap tetangga yang salah - diam-diam, karena
    # hasilnya tetap sebuah angka yang masuk akal.
    neighbour_close = _f(history[-1].get("close"))
    span = _true_range(latest, neighbour_close)
    if span is not None and span / atr >= limits.volatility_spike:
        add(
            EventKind.VOLATILITY_SPIKE,
            span / atr,
            limits.volatility_spike,
            f"true range {span / atr:.1f}x ATR",
            true_range=span,
            atr=atr,
        )

    # ---- break -------------------------------------------------------
    highs = [h for h in (_f(b.get("high")) for b in history) if h is not None]
    lows = [low for low in (_f(b.get("low")) for b in history) if low is not None]
    margin = limits.breakout_atr * atr
    if highs and close > max(highs) + margin:
        distance = (close - max(highs)) / atr
        add(
            EventKind.BREAKOUT,
            distance,
            limits.breakout_atr,
            f"tutup {distance:.1f}x ATR di atas high {len(highs)} bar",
            close=close,
            prior_high=max(highs),
            atr=atr,
        )
    if lows and close < min(lows) - margin:
        distance = (min(lows) - close) / atr
        add(
            EventKind.BREAKDOWN,
            distance,
            limits.breakout_atr,
            f"tutup {distance:.1f}x ATR di bawah low {len(lows)} bar",
            close=close,
            prior_low=min(lows),
            atr=atr,
        )

    return out


__all__ = [
    "MIN_BASELINE_BARS",
    "EventKind",
    "ScanResult",
    "ScanThresholds",
    "SignificantEvent",
    "scan",
    "scan_symbol",
]
