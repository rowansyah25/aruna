"""Memilih strategi dari sejarah, dan tahu kapan harus diam (PASAL 12.6).

Spec-nya menyebut alurnya:

    MARKET REGIME -> ASSET -> TIMEFRAME -> CURRENT CONDITIONS
    -> HISTORICAL MATCH -> STRATEGY PERFORMANCE -> AGENT SPECIALIZATION
    -> COUNCIL -> FINAL ANALYSIS

dan satu kalimat yang membentuk seluruh modul ini:

    ARUNA tidak boleh memilih strategy hanya berdasarkan win rate.

Tujuh hal harus ikut dipertimbangkan - sample size, performa terkini,
stabilitas, drawdown, rezim, kalibrasi, dan performa out-of-sample - dan tiap
satunya adalah cara sebuah win rate tinggi bisa berbohong:

* **sample size** - 3 dari 3 adalah 100% dan bukan apa-apa;
* **performa terkini** - strategi yang bagus tahun lalu dan buruk bulan ini
  punya rata-rata yang menyembunyikan keduanya;
* **stabilitas** - menang 60% setiap minggu berbeda dari menang 100% seminggu
  dan 20% tiga minggu, dan rata-ratanya sama;
* **drawdown** - win rate tidak pernah menyebut seberapa dalam lubangnya;
* **rezim** - strategi diukur pada pasar yang mungkin sudah tidak ada lagi;
* **kalibrasi** - keyakinan yang tidak ditopang hasil (PASAL 12.18);
* **out-of-sample** - satu-satunya angka yang tidak bisa dioptimalkan
  belakangan.

**Hak untuk diam adalah fiturnya, bukan kegagalannya.**

Saat modul ini ditulis, seluruh sejarah ARUNA berumur tiga hari dan irisan
strategi terbesar berisi 31 sample. Pemilih yang tetap memilih pada data
sebesar itu akan memandu keputusan hidup dengan kebetulan. Jadi
:func:`select` mengembalikan ``abstain`` kecuali seluruh tujuh pertimbangan
terpenuhi - dan pada data sekarang, ia akan hampir selalu abstain.

Itu membuat modul ini aman dirangkai hari ini: ia hanya bisa menambah
keterangan, tidak pernah mengurangi. Council yang tidak menerima pilihan
berjalan persis seperti sebelum modul ini ada.

**Yang TIDAK dilakukan modul ini.** Ia tidak mengubah bobot agent, tidak
memveto signal, tidak menyaring kandidat, dan tidak mengubah satu pun ambang.
PASAL 11.16 dan 12.26 melarangnya. Yang dihasilkannya adalah sepotong bukti
yang dibaca council seperti bukti lain - dan bukti yang salah bisa dibantah
council, sementara bobot yang salah tidak bisa dibantah siapa pun.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from aruna.learning.evidence import Evidence, EvidenceLevel

#: Drawdown maksimum yang masih boleh dimiliki strategi terpilih, relatif
#: terhadap keuntungan bersihnya.
#:
#: Satu berarti "lubangnya tidak boleh lebih dalam daripada seluruh yang
#: pernah dihasilkannya". Strategi yang melewatinya mungkin tetap
#: menguntungkan; ia hanya tidak boleh DIPILIHKAN kepada operator sebagai yang
#: paling sesuai, karena jalan menuju keuntungan itu melewati lubang yang
#: lebih besar dari keuntungannya.
MAX_DRAWDOWN_RATIO = 1.0

#: Selisih win rate antar periode yang masih dianggap stabil.
#:
#: Dua puluh poin persentase. Di atas itu, "rata-ratanya 60%" menggambarkan
#: sesuatu yang tidak pernah benar-benar terjadi di satu periode pun.
MAX_INSTABILITY = 0.20

#: Berapa periode terpisah yang harus ada sebelum stabilitas bisa dinilai.
#:
#: Tiga. Dua periode hanya bisa menunjukkan perbedaan, bukan kecenderungan -
#: dan sebuah pemilih yang menyimpulkan stabilitas dari dua titik akan
#: memanggil setiap garis lurus sebagai tren.
MIN_PERIODS = 3

#: Kesalahan kalibrasi maksimum. Di atasnya, confidence yang dilaporkan
#: strategi ini tidak menggambarkan hasilnya (PASAL 12.18).
MAX_CALIBRATION_ERROR = 0.20


class Refusal(StrEnum):
    """Kenapa tidak ada strategi yang dipilih. Selalu disebut, tidak pernah
    diam-diam."""

    NO_CANDIDATES = "tidak ada strategi yang cocok dengan rezim ini"
    INSUFFICIENT_SAMPLE = "sample belum cukup untuk memilih"
    NOT_BETTER_THAN_AVERAGE = "tidak ada yang terbukti lebih baik dari rata-rata"
    UNSTABLE = "performanya tidak stabil antar periode"
    DRAWDOWN_TOO_DEEP = "lubangnya lebih dalam daripada hasilnya"
    POORLY_CALIBRATED = "keyakinannya tidak ditopang hasil"
    NO_OUT_OF_SAMPLE = "belum diuji pada data di luar pengembangannya"
    REGIME_UNKNOWN = "rezim pasar saat ini tidak terbaca"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Satu strategi beserta seluruh angka yang menentukan nasibnya.

    Semua bidang opsional bernilai ``None`` ketika **belum diukur**, dan itu
    berbeda dari nol. Sebuah pemilih yang memperlakukan "belum diukur" sebagai
    "aman" akan memilih justru strategi yang paling sedikit diketahui.
    """

    code: str
    evidence: Evidence
    #: Win rate per periode terpisah, urut waktu. Dipakai menilai stabilitas
    #: dan performa terkini.
    per_period: tuple[float, ...] = field(default_factory=tuple)
    net_pnl: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    calibration_error: float | None = None
    out_of_sample: Evidence | None = None
    regimes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def recent(self) -> float | None:
        """Win rate periode terakhir, atau None kalau belum ada periodenya."""
        return self.per_period[-1] if self.per_period else None

    @property
    def instability(self) -> float | None:
        """Sebaran win rate antar periode. None kalau periodenya terlalu sedikit."""
        if len(self.per_period) < MIN_PERIODS:
            return None
        return statistics.pstdev(self.per_period)

    @property
    def drawdown_ratio(self) -> float | None:
        """Kedalaman lubang dibanding hasil bersihnya.

        None ketika bersihnya nol atau negatif: sebuah strategi yang tidak
        menghasilkan apa-apa tidak punya rasio yang berarti, dan membaginya
        akan menghasilkan angka besar yang terbaca seperti pengukuran.
        """
        if self.net_pnl <= 0:
            return None
        return float(self.max_drawdown / self.net_pnl)


@dataclass(frozen=True, slots=True)
class Selection:
    """Hasil satu pemilihan. Bisa berisi strategi, bisa berisi alasan diam."""

    strategy: str | None = None
    evidence: Evidence | None = None
    #: Kenapa yang lain tidak terpilih - tiap kandidat dan alasannya. Disimpan
    #: supaya "kenapa bukan yang itu" bisa dijawab tanpa menjalankan ulang.
    rejected: tuple[tuple[str, Refusal], ...] = field(default_factory=tuple)
    refusal: Refusal | None = None

    @property
    def abstained(self) -> bool:
        return self.strategy is None

    def line(self) -> str:
        """Satu baris untuk operator dan untuk council."""
        if self.abstained:
            alasan = self.refusal.value if self.refusal else "tidak dipilih"
            return f"Tidak ada strategi yang dipilih: {alasan}."
        bukti = self.evidence.label() if self.evidence else "tanpa bukti"
        return (
            f"Strategi yang historis paling sesuai: {self.strategy} "
            f"({bukti}). Ini keterangan, bukan perintah."
        )


def _fails(c: Candidate, baseline: float | None) -> Refusal | None:
    """Alasan pertama kandidat ini gugur, atau None kalau lolos semuanya.

    Urutannya disengaja: yang paling murah dan paling sering menggugurkan
    diperiksa lebih dulu, supaya alasan yang dilaporkan adalah yang paling
    mendasar. Sebuah strategi bersample 3 yang juga tidak stabil sebaiknya
    dilaporkan sebagai "sample belum cukup" - stabilitas dari tiga sample
    bukan temuan.
    """
    if not c.evidence.conclusive:
        return Refusal.INSUFFICIENT_SAMPLE
    if baseline is not None and not c.evidence.beats(baseline):
        return Refusal.NOT_BETTER_THAN_AVERAGE

    ketidakstabilan = c.instability
    if ketidakstabilan is None:
        # Belum cukup periode untuk menilai stabilitas. Bukan lolos - tidak
        # diukur bukan aman (PASAL 4).
        return Refusal.UNSTABLE
    if ketidakstabilan > MAX_INSTABILITY:
        return Refusal.UNSTABLE

    rasio = c.drawdown_ratio
    if rasio is None or rasio > MAX_DRAWDOWN_RATIO:
        return Refusal.DRAWDOWN_TOO_DEEP

    if c.calibration_error is None or c.calibration_error > MAX_CALIBRATION_ERROR:
        return Refusal.POORLY_CALIBRATED

    if c.out_of_sample is None or not c.out_of_sample.conclusive:
        return Refusal.NO_OUT_OF_SAMPLE
    if baseline is not None and not c.out_of_sample.beats(baseline):
        return Refusal.NO_OUT_OF_SAMPLE

    return None


def select(
    candidates: Iterable[Candidate],
    *,
    regime: str | None,
    baseline: float | None,
) -> Selection:
    """Pilih strategi yang paling sesuai, atau nyatakan kenapa tidak ada.

    ``baseline`` adalah win rate keseluruhan ARUNA. Sebuah strategi harus
    mengalahkannya dengan SELURUH selangnya, bukan dengan titik tengahnya -
    lihat :meth:`~aruna.learning.evidence.Evidence.beats`.

    Diurutkan menurut batas bawah selang, bukan menurut win rate. Dua strategi
    yang sama-sama menang 70% tapi satu dari 40 sample dan satu dari 400 bukan
    dua strategi yang sama baiknya, dan mengurutkan menurut titik tengah
    memperlakukannya begitu.
    """
    if regime is None:
        return Selection(refusal=Refusal.REGIME_UNKNOWN)

    cocok = [
        c for c in candidates
        if not c.regimes or regime.upper() in {r.upper() for r in c.regimes}
    ]
    if not cocok:
        return Selection(refusal=Refusal.NO_CANDIDATES)

    ditolak: list[tuple[str, Refusal]] = []
    lolos: list[Candidate] = []
    for c in cocok:
        alasan = _fails(c, baseline)
        if alasan is None:
            lolos.append(c)
        else:
            ditolak.append((c.code, alasan))

    if not lolos:
        # Alasan yang paling sering muncul dilaporkan sebagai alasan utama:
        # ia yang menggambarkan keadaan datanya, bukan keanehan satu kandidat.
        urutan = [a for _, a in ditolak]
        utama = max(set(urutan), key=urutan.count)
        return Selection(refusal=utama, rejected=tuple(ditolak))

    terbaik = max(lolos, key=lambda c: c.evidence.interval[0])
    return Selection(
        strategy=terbaik.code,
        evidence=terbaik.evidence,
        rejected=tuple(ditolak),
    )


def candidates_from(
    slices: Sequence[object],
    *,
    periods: dict[str, tuple[float, ...]] | None = None,
    calibration: dict[str, float] | None = None,
    out_of_sample: dict[str, Evidence] | None = None,
    regimes: dict[str, tuple[str, ...]] | None = None,
) -> tuple[Candidate, ...]:
    """Bangun kandidat dari irisan performa strategi.

    Angka yang belum tersedia dibiarkan ``None`` dan BUKAN diisi bawaan yang
    ramah. Konsekuensinya: selama kalibrasi dan out-of-sample per strategi
    belum diukur, tidak ada kandidat yang akan pernah lolos - dan itu perilaku
    yang benar, bukan cacat. Sebuah pemilih yang mengisi kekosongan dengan
    nilai yang meloloskan akan memilih strategi berdasarkan angka yang tidak
    ada.
    """
    hasil: list[Candidate] = []
    for s in slices:
        kode = getattr(s, "strategy_code", None)
        bukti = getattr(s, "evidence", None)
        if kode is None or bukti is None:
            continue
        hasil.append(
            Candidate(
                code=kode,
                evidence=bukti,
                per_period=(periods or {}).get(kode, ()),
                net_pnl=Decimal(str(getattr(s, "net_pnl", 0) or 0)),
                max_drawdown=Decimal(str(getattr(s, "max_drawdown", 0) or 0)),
                calibration_error=(calibration or {}).get(kode),
                out_of_sample=(out_of_sample or {}).get(kode),
                regimes=(regimes or {}).get(kode, ()),
            )
        )
    return tuple(hasil)


__all__ = [
    "MAX_CALIBRATION_ERROR",
    "MAX_DRAWDOWN_RATIO",
    "MAX_INSTABILITY",
    "MIN_PERIODS",
    "Candidate",
    "EvidenceLevel",
    "Refusal",
    "Selection",
    "candidates_from",
    "select",
]
