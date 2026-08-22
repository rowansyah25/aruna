"""Signal Quality Score dan gerbangnya (PASAL 11.1, 11.13).

**Quality bukan confidence.** Confidence menjawab "seberapa yakin arahnya";
quality menjawab "seberapa layak keseluruhan setup ini menghasilkan signal".
Keduanya bisa berlawanan, dan justru di situ gunanya: council yang sangat
yakin pada arah, di atas satu indikator yang bertahan hidup dari data tipis,
spread lebar, dan berita basi, adalah keyakinan yang tidak ditopang apa pun.
Confidence tidak bisa menangkap itu. Quality bisa.

Tiga hal menentukan apakah angka ini berguna atau justru menyesatkan.

**Faktor yang tidak terukur BUKAN nol, dan bukan netral.** Spot tidak punya
funding rate, open interest, maupun data likuidasi - itu milik perpetual.
Memberi nilai tengah pada ketiganya akan menaikkan skor setiap signal spot
dengan angka yang tidak pernah diukur; memberi nol akan menghukumnya karena
memperdagangkan pasar yang memang tidak punya mekanisme itu. Keduanya salah.
Faktor tak terukur **dikeluarkan dari pembagi** dan dihitung terpisah.

**Karena itu skor selalu dibaca bersama cakupannya.** 91/100 dari tujuh belas
faktor dan 91/100 dari tiga faktor adalah dua pernyataan yang sangat berbeda,
dan tanpa cakupan keduanya tercetak identik. Ada lantai cakupan, dan skor
di bawahnya ditolak apa pun angkanya - bukan karena setup-nya buruk, tapi
karena belum ada cukup yang diperiksa untuk menyebutnya baik.

**Sebagian faktor adalah gerbang, bukan bobot.** Data basi tidak bisa ditebus
oleh struktur yang rapi (PASAL 11.7). Faktor bertanda ``blocking`` yang gagal
menolak signal berapa pun skor totalnya - kalau tidak, cukup menumpuk faktor
bagus untuk melewati data yang tidak boleh dipakai.

**ARUNA MENGANALISIS SAJA.** Skor ini menyaring apa yang layak disebut, bukan
apa yang layak dieksekusi (PASAL 11 pembuka).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Skor minimum agar sebuah kandidat boleh terbit (PASAL 11.13).
MIN_QUALITY = 60

#: Bagian bobot yang harus benar-benar terukur. Skor tinggi dari dua faktor
#: bukan skor tinggi - ia rata-rata dari sampel yang terlalu kecil untuk
#: berarti, dan angka 0-100 di sebelahnya membuatnya terlihat setara dengan
#: skor yang ditopang tujuh belas pengukuran.
MIN_COVERAGE = 0.5


@dataclass(frozen=True, slots=True)
class Factor:
    """Satu faktor. ``score`` ``None`` berarti **tidak terukur**, bukan nol."""

    name: str
    score: float | None
    weight: float = 1.0
    detail: str = ""
    #: Kegagalan faktor ini menolak signal apa pun skor totalnya.
    blocking: bool = False
    #: Apakah faktor ini ikut menghitung SKOR, atau hanya cakupan.
    #:
    #: Pemisahan ini lahir dari skor pertama yang benar-benar terukur: 89/100
    #: untuk council yang terbelah dua lawan lima. Sebabnya delapan dari dua
    #: belas faktor terukur bernilai 1.00, dan semuanya sebenarnya menjawab
    #: "apakah datanya ada dan terbaca" - bukan "apakah setup ini bagus".
    #: Kehadiran data selalu jadi nilai penuh, jadi skornya mengukur
    #: kelengkapan data dan menyebutnya kualitas.
    #:
    #: Lebih buruk lagi: trend, momentum, volume dan volatility SUDAH dinilai
    #: para agent, dan kesimpulan mereka masuk lewat ``agent_agreement`` dan
    #: ``evidence_strength``. Menilainya lagi di sini menghitung bukti yang
    #: sama dua kali, dan yang kedua tidak menilai apa-apa.
    #:
    #: Faktor tak-bernilai tetap dicatat, tetap masuk cakupan, dan tetap bisa
    #: memblokir. Yang hilang hanya kemampuannya menaikkan skor karena datanya
    #: kebetulan ada.
    graded: bool = True

    @property
    def measured(self) -> bool:
        return self.score is not None

    @property
    def counts(self) -> bool:
        """Ikut menghitung skor: terukur DAN bernilai."""
        return self.measured and self.graded

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": None if self.score is None else round(self.score, 3),
            "weight": self.weight,
            "detail": self.detail,
            "blocking": self.blocking,
            "graded": self.graded,
        }


#: Faktor blocking di bawah ini dianggap gagal (PASAL 11.7).
BLOCKING_FLOOR = 0.5


@dataclass(frozen=True, slots=True)
class QualityScore:
    """Skor 0-100 beserta apa yang ada di belakangnya."""

    factors: tuple[Factor, ...] = field(default_factory=tuple)

    @property
    def measured(self) -> tuple[Factor, ...]:
        return tuple(f for f in self.factors if f.measured)

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.factors if not f.measured)

    @property
    def coverage(self) -> float:
        """Bagian bobot yang benar-benar diukur, 0..1."""
        total = sum(f.weight for f in self.factors)
        if total <= 0:
            return 0.0
        return sum(f.weight for f in self.measured) / total

    @property
    def score(self) -> int | None:
        """0-100, atau ``None`` kalau tidak ada satu pun faktor terukur.

        ``None`` dan ``0`` adalah dua jawaban berbeda: yang pertama berarti
        tidak ada yang bisa dinilai, yang kedua berarti sudah dinilai dan
        hasilnya buruk. Mencetak nol untuk keduanya menghapus perbedaan itu.
        """
        dihitung = tuple(f for f in self.factors if f.counts)
        bobot = sum(f.weight for f in dihitung)
        if bobot <= 0:
            return None
        total = sum((f.score or 0.0) * f.weight for f in dihitung)
        return round(total / bobot * 100)

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """Faktor gerbang yang gagal atau tidak terukur (PASAL 11.7).

        Gerbang yang **tidak terukur** juga memblokir. Sebuah pemeriksaan
        kesegaran data yang tidak bisa dijalankan tidak membuktikan datanya
        segar - ia hanya berarti tidak ada yang tahu, dan menerbitkan signal
        atas dasar itu persis yang PASAL 11.7 larang.
        """
        return tuple(
            f.name for f in self.factors
            if f.blocking and (f.score is None or f.score < BLOCKING_FLOOR)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "coverage": round(self.coverage, 3),
            "measured": len(self.measured),
            "total_factors": len(self.factors),
            "unavailable": list(self.unavailable),
            "blocked_by": list(self.blocked_by),
            "factors": [f.to_dict() for f in self.factors],
        }


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Boleh terbit atau tidak, dengan alasan yang bisa dibaca."""

    quality: QualityScore
    passed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            **self.quality.to_dict(),
        }


def gate(
    quality: QualityScore,
    *,
    minimum: int = MIN_QUALITY,
    min_coverage: float = MIN_COVERAGE,
) -> GateVerdict:
    """Putuskan apakah kandidat ini boleh jadi signal (PASAL 11.13).

    Alasan penolakan dikumpulkan semuanya, bukan berhenti di yang pertama.
    Sebuah kandidat yang gagal karena data basi DAN skor rendah DAN cakupan
    tipis adalah kandidat yang berbeda dari yang gagal hanya karena satu di
    antaranya, dan autopsi nanti membaca daftar ini.
    """
    reasons: list[str] = []

    diblokir = quality.blocked_by
    if diblokir:
        reasons.append(f"gerbang gagal: {', '.join(diblokir)}")

    if quality.coverage < min_coverage:
        reasons.append(
            f"cakupan {quality.coverage:.0%} di bawah {min_coverage:.0%} - "
            "terlalu sedikit yang terukur untuk menyebut setup ini baik"
        )

    skor = quality.score
    if skor is None:
        reasons.append("tidak ada faktor yang terukur")
    elif skor < minimum:
        reasons.append(f"quality {skor}/100 di bawah {minimum}")

    return GateVerdict(quality=quality, passed=not reasons, reasons=tuple(reasons))


# ---------------------------------------------------------------------------
# Faktor-faktor
# ---------------------------------------------------------------------------


def _clamp(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def _reading_factor(
    context: Any, names: tuple[str, ...], *, label: str, weight: float
) -> Factor:
    """Faktor dari indikator: terukur kalau ada yang ``reliable``.

    ``TechnicalSnapshot.value`` mengembalikan ``None`` untuk pembacaan yang
    sampelnya kurang - itu memang yang dimaksud "tidak terukur", dan
    dipertahankan apa adanya daripada diganti nol.
    """
    ada = [n for n in names if context.value(n) is not None]
    if not ada:
        return Factor(
            label, None, weight,
            detail=f"tidak ada dari {', '.join(names)}", graded=False,
        )
    return Factor(
        label,
        _clamp(len(ada) / len(names)),
        weight,
        detail=f"{len(ada)}/{len(names)} terbaca: {', '.join(ada)}",
        graded=False,
    )


def data_quality_factor(state: Any) -> Factor:
    """PASAL 11.7. Gerbang, bukan bobot."""
    quality = str(getattr(state, "data_quality", "OK") or "OK")
    if quality != "OK":
        return Factor(
            "data_quality", 0.0, 3.0,
            detail=f"kualitas data {quality}", blocking=True, graded=False,
        )
    if getattr(state, "market_open", None) is False:
        return Factor(
            "data_quality", 0.0, 3.0,
            detail="market tutup", blocking=True, graded=False,
        )
    return Factor(
        "data_quality", 1.0, 3.0, detail="OK", blocking=True, graded=False
    )


def freshness_factor(
    state: Any, as_of: datetime, now: datetime, *, horizon_sec: float
) -> Factor:
    """PASAL 11.7. Umur bukti dibandingkan horizon yang diprediksi.

    Dibandingkan terhadap horizon, bukan terhadap angka detik tetap: bukti
    berumur sepuluh menit sudah basi untuk prediksi 15 menit dan masih sangat
    segar untuk prediksi harian. Ambang tunggal akan salah di salah satu
    ujungnya, dan biasanya di keduanya.
    """
    umur = max(0.0, (now - as_of).total_seconds())
    if horizon_sec <= 0:
        return Factor(
            "freshness", None, 3.0, detail="horizon tidak diketahui",
            blocking=True, graded=False,
        )

    # Feed yang mengaku tertunda menambah umurnya sendiri: keterlambatan yang
    # dideklarasikan adalah bagian dari umur bukti, bukan catatan kaki.
    if not getattr(state, "is_realtime", True):
        umur += float(getattr(state, "declared_delay_sec", 0) or 0)

    rasio = umur / horizon_sec
    skor = _clamp(1.0 - rasio)
    return Factor(
        "freshness", skor, 3.0,
        detail=f"umur bukti {umur:.0f}s dari horizon {horizon_sec:.0f}s",
        blocking=True, graded=False,
    )


def structure_factor(structure: Any) -> Factor:
    """Kedalaman sampel struktur.

    Tidak bernilai. Lima puluh delapan swing bukan setup yang lebih baik
    daripada dua belas swing - ia hanya riwayat yang lebih panjang. Yang
    diukur di sini adalah apakah strukturnya bisa dibaca sama sekali, dan itu
    pertanyaan cakupan.
    """
    if structure is None:
        return Factor(
            "structure", None, 2.0,
            detail="tidak ada analisis struktur", graded=False,
        )
    swings = int(getattr(structure, "confirmed_swings", 0) or 0)
    if not getattr(structure, "reliable", False):
        return Factor(
            "structure", _clamp(swings / 4), 2.0,
            detail=f"{swings} swing terkonfirmasi, butuh 4", graded=False,
        )
    return Factor(
        "structure", _clamp(min(swings, 8) / 8), 2.0,
        detail=f"{swings} swing terkonfirmasi", graded=False,
    )


def regime_factor(regime: Any) -> Factor:
    """Sejelas apa kondisi pasarnya (PASAL 11.3)."""
    if regime is None:
        return Factor("regime_clarity", None, 2.0, detail="regime tidak diklasifikasi")
    tersedia = int(getattr(regime, "evidence_available", 0) or 0)
    dipakai = int(getattr(regime, "evidence_used", 0) or 0)
    if tersedia <= 0:
        return Factor("regime_clarity", None, 2.0, detail="tidak ada bukti regime")
    bagian = dipakai / tersedia
    keyakinan = float(getattr(regime, "confidence", 0.0) or 0.0)
    return Factor(
        "regime_clarity", _clamp((bagian + keyakinan) / 2), 2.0,
        detail=f"{getattr(regime, 'regime', '?')}, bukti {dipakai}/{tersedia}",
    )


def liquidity_factor(state: Any, *, wide_spread_bps: float = 20.0) -> Factor:
    """Spread dan kedalaman buku. Lebar spread memakan target lebih dulu."""
    spread = getattr(state, "spread_bps", None)
    if spread is None:
        return Factor("liquidity", None, 3.0, detail="spread tidak diketahui")
    skor = _clamp(1.0 - float(spread) / wide_spread_bps)
    depth = getattr(state, "bid_depth", None), getattr(state, "ask_depth", None)
    detail = f"spread {float(spread):.1f} bps"
    if all(d is not None for d in depth):
        detail += f", kedalaman {float(depth[0]):.0f}/{float(depth[1]):.0f}"
    return Factor("liquidity", skor, 3.0, detail=detail)


def news_factor(context: Any, *, hours: int = 24) -> Factor:
    """Ada tidaknya berita terkini yang sudah diklasifikasi (PASAL 11)."""
    recent = getattr(context, "recent_news", None)
    if recent is None:
        return Factor(
            "news", None, 1.0, detail="tidak ada layanan berita", graded=False
        )
    items = recent(hours=hours)
    if not items:
        # Nol berita adalah pengukuran yang sah - pasar memang bisa sepi - dan
        # berbeda dari feed yang tidak berjalan. Yang kedua ditangani di atas.
        return Factor(
            "news", 0.0, 1.0,
            detail=f"tidak ada berita {hours} jam", graded=False,
        )
    # Tidak bernilai. Tiga belas berita bukan setup yang lebih baik daripada
    # tiga; ia hanya hari yang lebih ramai. Yang menentukan adalah ISI-nya, dan
    # itu sudah dinilai NewsAgent - yang suaranya masuk lewat agent_agreement.
    return Factor(
        "news", _clamp(min(len(items), 5) / 5), 1.0,
        detail=f"{len(items)} berita dalam {hours} jam", graded=False,
    )


def agreement_factor(split: Any) -> Factor:
    """Seberapa bulat council (PASAL 11.1 "agent agreement").

    Abstain dikeluarkan dari pembagi. Agent yang tidak punya bukti tidak sedang
    menentang, dan menghitungnya sebagai penentang membuat feed yang mati
    terlihat seperti council yang terbelah.
    """
    setuju = len(getattr(split, "setuju", ()) or ())
    kontra = len(getattr(split, "kontra", ()) or ())
    memilih = setuju + kontra
    if memilih == 0:
        return Factor("agent_agreement", None, 4.0, detail="tidak ada yang memilih")
    return Factor(
        "agent_agreement", _clamp(setuju / memilih), 4.0,
        detail=f"{setuju} setuju, {kontra} kontra",
    )


def evidence_factor(opinions: Any, *, target: int = 20) -> Factor:
    """Berapa banyak bukti yang benar-benar ditimbang."""
    opinions = tuple(opinions or ())
    if not opinions:
        return Factor("evidence_strength", None, 2.0, detail="tidak ada opini")
    total = sum(len(getattr(o, "evidence", ()) or ()) for o in opinions)
    return Factor(
        "evidence_strength", _clamp(total / target), 2.0,
        detail=f"{total} bukti dari {len(opinions)} agent",
    )


def reward_risk_factor(
    entry: Any, stop: Any, target: Any, *, good: float = 2.0
) -> Factor:
    if entry is None or stop is None or target is None:
        return Factor("risk_reward", None, 4.0, detail="level belum lengkap")
    risiko = abs(float(entry) - float(stop))
    imbalan = abs(float(target) - float(entry))
    if risiko <= 0:
        # Stop di harga masuk bukan risiko nol; ia rencana yang tidak punya
        # tempat untuk terbukti salah.
        return Factor("risk_reward", 0.0, 4.0, detail="stop sama dengan entry")
    rr = imbalan / risiko
    return Factor("risk_reward", _clamp(rr / good), 4.0, detail=f"R:R {rr:.2f}")


def historical_factor(accuracy: float | None, sample: int, *, needed: int = 25) -> Factor:
    """Rekam jejak, atau ``None`` sampai sampelnya cukup (PASAL 11.4, 11.16).

    Akurasi dari lima prediksi bukan rekam jejak; ia kebisingan yang kebetulan
    punya angka. Sampai ambangnya terlampaui faktor ini tidak terukur, dan
    tidak ikut menaikkan maupun menurunkan skor.
    """
    if accuracy is None or sample < needed:
        return Factor(
            "historical", None, 3.0,
            detail=f"sampel {sample}, butuh {needed}",
        )
    return Factor(
        "historical", _clamp(accuracy), 3.0,
        detail=f"akurasi {accuracy:.0%} dari {sample}",
    )


def futures_factor(name: str, value: Any, *, weight: float = 1.0) -> Factor:
    """Funding, open interest, likuidasi - milik perpetual saja.

    Untuk spot ketiganya memang tidak ada, dan itu **bukan** kekurangan yang
    layak dihukum. Faktor tak terukur keluar dari pembagi, jadi signal spot
    dinilai atas apa yang memang bisa diukur padanya.
    """
    if value is None:
        return Factor(name, None, weight, detail="tidak berlaku untuk pasar ini")
    return Factor(name, _clamp(float(value)), weight, detail=f"{float(value):.3f}")


def anomaly_factor(report: Any) -> Factor:
    """Kondisi abnormal sebagai gerbang (PASAL 11.8).

    Gerbang, bukan bobot: setup yang lahir dari volume lima belas kali garis
    dasarnya tidak menjadi lebih baik karena spread-nya kebetulan sempit.
    Indikatornya dihitung dari garis dasar yang sudah tidak berlaku, dan skor
    tinggi di atas angka-angka itu adalah keyakinan yang dibangun di atas
    pengukuran yang kehilangan artinya.

    ``None`` kalau pemeriksaannya tidak dijalankan sama sekali - dan itu
    memblokir, sama seperti gerbang lain. Tapi laporan yang berjalan dan tidak
    menemukan apa-apa **lulus**, meski sebagian pemeriksaannya tidak bisa
    dilakukan: PASAL 11.8 bertanya "apakah kami mendeteksi sesuatu", bukan
    "buktikan tidak ada apa-apa" (lihat ``aruna.signals.anomaly``).
    """
    if report is None:
        return Factor(
            "anomaly", None, 3.0,
            detail="tidak diperiksa", blocking=True, graded=False,
        )
    if report.detected:
        return Factor(
            "anomaly", 0.0, 3.0,
            detail=report.summary(), blocking=True, graded=False,
        )
    detail = "bersih"
    if report.unchecked:
        detail += f" ({len(report.unchecked)} pemeriksaan tidak bisa dijalankan)"
    return Factor("anomaly", 1.0, 3.0, detail=detail, blocking=True, graded=False)


def score_signal(
    *,
    context: Any,
    split: Any = None,
    opinions: Any = None,
    entry: Any = None,
    stop: Any = None,
    target: Any = None,
    now: datetime,
    horizon_sec: float,
    accuracy: float | None = None,
    sample: int = 0,
    funding: float | None = None,
    open_interest: float | None = None,
    liquidation: float | None = None,
    anomalies: Any = None,
) -> QualityScore:
    """Susun faktor PASAL 11.1 dan gerbang PASAL 11.8 dari bukti yang ada."""
    state = context.state
    return QualityScore(factors=(
        data_quality_factor(state),
        freshness_factor(state, context.as_of, now, horizon_sec=horizon_sec),
        anomaly_factor(anomalies),
        structure_factor(getattr(context, "structure", None)),
        _reading_factor(context, ("macd", "vwap"), label="trend", weight=2.0),
        _reading_factor(context, ("rsi", "momentum"), label="momentum", weight=2.0),
        _reading_factor(
            context, ("volume_trend", "volume_anomaly"), label="volume", weight=1.0
        ),
        _reading_factor(
            context, ("atr", "realised_volatility", "bollinger"),
            label="volatility", weight=1.0,
        ),
        liquidity_factor(state),
        news_factor(context),
        regime_factor(getattr(context, "regime", None)),
        reward_risk_factor(entry, stop, target),
        agreement_factor(split),
        evidence_factor(opinions),
        historical_factor(accuracy, sample),
        futures_factor("funding", funding),
        futures_factor("open_interest", open_interest),
        futures_factor("liquidation", liquidation),
    ))


__all__ = [
    "BLOCKING_FLOOR",
    "MIN_COVERAGE",
    "MIN_QUALITY",
    "Factor",
    "GateVerdict",
    "QualityScore",
    "agreement_factor",
    "data_quality_factor",
    "evidence_factor",
    "freshness_factor",
    "gate",
    "historical_factor",
    "liquidity_factor",
    "news_factor",
    "regime_factor",
    "reward_risk_factor",
    "score_signal",
    "structure_factor",
]
