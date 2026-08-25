"""The upkeep loop: refresh candles, then score what is due.

Modelled on :class:`~aruna.futures.scheduler.FuturesScheduler`, and built
around the same three properties, for the same reasons:

**A failed cycle does not end the run.** An unattended loop that dies on the
first network hiccup is worse than no loop, because the operator believes it is
still watching and a frozen series looks exactly like a quiet market.

**The two phases fail separately.** A refresh that cannot reach the venue must
not cancel the resolution pass behind it: predictions whose candles are already
stored are still worth scoring while the venue is down. Each phase carries its
own guard.

**Refresh runs before resolve, structurally.** Not as an efficiency - as the
SPEC 22 ordering. Resolution reads candles, marks the last observation it can
find as final, and that number becomes the recorded outcome and the paper
trade's exit price. Scoring against a series that has not caught up records a
mid-horizon price as a final result, permanently, because a scored prediction
may not be edited afterwards. Making the order a property of one cycle in one
task means it cannot be undone by a scheduling accident.

**Shutdown waits for the cycle in flight.** ``stop()`` used to cancel the task
outright, which cut a resolution pass between its writes: a signal could be left
RESOLVED with no paper trade, or carry a ``paper_results`` row while still
LOCKED - and neither state is retried, because ``due()`` only returns LOCKED and
``record_outcome`` is not idempotent. Cancelling was therefore a way to corrupt
a scored prediction on every SIGINT, permanently (SPEC 22). The task is asked to
finish first and only cancelled if it overruns the grace.

The quote poll stays in its own task. A slow candle pull must never delay the
five-second price loop, and serialising the two would do exactly that.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from time import monotonic
from typing import Any

from aruna.core.clock import idx_active, isoformat, now_utc
from aruna.core.config import UpkeepSettings
from aruna.core.enums import Horizon, Market, horizons_for_market
from aruna.core.logging import get_logger
from aruna.health import heartbeat
from aruna.scanner import AnalysisQueue
from aruna.upkeep.candles import CandleRefresher, bar_start

log = get_logger("aruna.upkeep")

#: Bound on the stored error list, mirroring LoopStats: an unattended week of
#: failures must not grow without limit.
MAX_ERRORS = 200

#: Berapa siklus terakhir yang durasinya disimpan untuk menghitung anggaran
#: "macet". Cukup panjang supaya satu siklus berat tidak menggeser mediannya,
#: cukup pendek supaya mesin yang berubah kecepatan diikuti dalam hitungan
#: menit, bukan hari.
CYCLE_WINDOW = 50

#: Berapa pasangan ``(market, horizon)`` yang dikunci dalam SATU siklus.
#:
#: Dua. Dalam keadaan mantap ia hampir tidak pernah menggigit - batas bar jarang
#: berbarengan, dan yang jatuh tempo bersamaan biasanya satu atau dua. Ia
#: berdiri untuk **start dingin**: ``_locked_bar`` kosong sesudah restart,
#: sehingga seluruh pasangan jatuh tempo sekaligus dan siklus pertama harus
#: menggelar council untuk semuanya.
#:
#: Terukur 2026-08-22 sesudah restart: siklus pertama tidak selesai selama lima
#: menit, dan `upkeep` dilaporkan DOWN karena melewati batas enam puluh detik.
#: Dengan jatah ini, delapan pasangan terkuras dalam sekitar dua menit - dan bar
#: terpendek lima belas menit, jadi tidak ada satu pun yang kehilangan barnya.
BATAS_KUNCI_PER_SIKLUS = 2

#: Jeda antar sapuan penilaian skenario (bagian 16.19), detik.
#:
#: Lima belas menit, sama dengan bar yang dipindai. Skenario baru bisa dinilai
#: sesudah dua belas bar berlalu, jadi menyapu lebih sering hanya mengulangi
#: kueri yang jawabannya kosong - dan menyapu lebih jarang membuat tunggakan
#: menumpuk lebih cepat daripada :data:`~aruna.upkeep.skenario_nilai.
#: BATAS_PER_SAPUAN` bisa menguranginya.
SKENARIO_NILAI_INTERVAL_SEC = 900.0

#: How long ``stop()`` lets the cycle in flight finish before cancelling it.
#:
#: A resolution pass writes samples, outcome, status and paper trade one after
#: another, so a cancel landing between two of those writes leaves a scored
#: prediction half-recorded - and a scored prediction may not be edited
#: afterwards (SPEC 22). Thirty seconds is roughly what a full ``resolve_limit``
#: batch costs against a healthy database, which is the longest a shutdown
#: should ever have to wait for correctness.
STOP_GRACE_SEC = 30.0

#: Jarak minimum antara dua putaran pembelajaran adaptif (PASAL 12.27).
#:
#: Sehari. Sejarahnya bertambah beberapa prediksi per jam sementara satu
#: putaran membaca seluruhnya dan menulis ratusan baris hasil - menjalankannya
#: lebih sering menghabiskan waktu untuk menghitung ulang jawaban yang sama.
#:
#: Dijaga dengan jam monotonik lewat `last_learning_at` yang diisi `moment`
#: siklus, bukan dengan jam dinding: koreksi jam tidak boleh membuat ARUNA
#: melewatkan satu hari pembelajaran atau mengulanginya sepuluh kali.
LEARNING_INTERVAL = timedelta(days=1)

#: Berapa ingatan diproyeksikan per lintasan (PASAL 15.2).
#:
#: Lima ratus tiap sepuluh menit adalah 72.000 sehari - jauh di atas laju ARUNA
#: yang beberapa ribu signal sehari, jadi antreannya tidak pernah menumpuk.
#: Tetap dibatasi: lintasan pertama pada database yang belum pernah
#: diproyeksikan akan menyisipkan puluhan ribu baris kalau dibiarkan, dan itu
#: satu siklus upkeep yang berhenti berdetak selama beberapa menit.
MEMORY_BATCH = 500


def _result_row(
    signal: Any,
    outcome: Any,
    trade_result: str | None = None,
    economics: tuple | None = None,
) -> dict[str, Any]:
    """Satu prediksi yang sudah diskor, siap dirender.

    ``votes`` diisi belakangan oleh :meth:`UpkeepLoop._attach_votes`, yang
    membacanya dari ``council_votes``.

    ``trade_result`` datang dari paper trade dan bukan dari kelas outcome.
    Keduanya menjawab pertanyaan berbeda: kelas outcome mengatakan **bagaimana**
    prediksinya meleset, hasil trade mengatakan **menang atau kalah**. Selama
    yang pertama dipakai untuk keduanya, tidak ada kekalahan yang pernah
    tercetak sebagai LOSS.
    """
    return {
        "symbol": signal.symbol,
        "decision": signal.direction,
        "outcome_class": outcome.outcome_class.value,
        "signal_id": signal.signal_id,
        "entry": signal.reference_price,
        "target": signal.target_price,
        "trigger": "TARGET HIT" if getattr(outcome, "target_reached", False) else None,
        "trade_result": trade_result,
        "model_version": getattr(signal, "model_version", None),
        # Kotor, biaya, bersih. Tanpa bertiga ini, "arahnya benar" dan "LOSS"
        # berdiri bersebelahan tanpa ada yang menjelaskan keduanya bisa benar
        # sekaligus - lihat `render_result`.
        "economics": economics,
    }


def _signal_row(signal: Any) -> dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "decision": signal.direction,
        "confidence": signal.confidence,
        "entry": signal.reference_price,
        "target": signal.target_price,
        "timeframe": signal.horizon.value,
        # Versi model yang menghasilkan keputusan ini. Sudah tersimpan di
        # `signal_snapshots` sejak lama; yang belum ada hanya menyebutkannya
        # kepada pembacanya. Rekam jejak yang mencampur beberapa versi mengukur
        # rata-rata dari hal-hal yang berbeda.
        "model_version": getattr(signal, "model_version", None),
    }


@dataclass(slots=True)
class UpkeepStats:
    """What the loop did, kept whether or not it produced anything.

    Every counter here has to be able to reach the operator, because the line
    ``aruna upkeep`` prints is :meth:`summary` and nothing else. A number that
    is tallied carefully and then left out of that line is not an accounting
    detail: ``refresh_failures`` was counted on every failed pass while the
    only failure figure on screen was ``failed_cycles``, which stays at zero by
    construction - ``cycle()`` catches both phases itself. Fifty cycles in which
    every single provider call failed and no candle was stored printed
    "0 siklus gagal".
    """

    started_at: datetime
    cycles: int = 0
    failed_cycles: int = 0
    refresh_failures: int = 0
    resolve_failures: int = 0
    candles: int = 0
    requests: int = 0
    resolved: int = 0
    awaiting_candles: int = 0
    no_prices: int = 0
    #: Signals whose horizon has no stored interval to sample from. Kept apart
    #: from ``awaiting_candles`` because the two mean opposite things: one is a
    #: wait, the other is a wait that will never end without an operator
    #: changing something, and it is the one that has to be said out loud.
    unavailable_interval: int = 0

    # ---- the last pass, kept apart from the run totals ------------------
    #
    #: The three fields above are run totals, and a run total of these is a
    #: count of *observations*, not of signals: the same unscoreable prediction
    #: is re-read every pass, so three signals over twelve passes make
    #: ``unavailable_interval`` thirty-six. That number is a fair measure of
    #: wasted work and a false one for the queue, and it was being spoken as
    #: the queue - "10 sinyal jatuh tempo ... 36 di antaranya", thirty-six of
    #: ten. Anything that describes what is in the queue *now* reads these
    #: instead, because only the last pass looked at the queue as it is.
    last_awaiting_candles: int = 0
    last_no_prices: int = 0
    last_unavailable_interval: int = 0
    #: False until a resolution pass has actually returned. Without it the
    #: three zeroes above are indistinguishable from "the last pass found
    #: nothing wrong", which is the difference between a measurement and a
    #: question nobody asked (SPEC 4).
    resolve_pass_seen: bool = False
    #: Berita yang masuk, dan pass yang gagal (PASAL 11). Sebelum fase ini ada,
    #: berita hanya datang lewat perintah manual: 280 item berumur enam puluh
    #: jam dibaca council sebagai konteks sekarang.
    news_items: int = 0
    news_failures: int = 0
    last_news_at: datetime | None = None
    news_enabled: bool = True

    daily_reports: int = 0
    daily_failures: int = 0
    last_daily_at: datetime | None = None

    research_digests: int = 0
    research_failures: int = 0
    last_research_at: datetime | None = None

    screenings: int = 0
    screening_failures: int = 0
    last_screening_at: datetime | None = None

    learning_runs: int = 0
    learning_failures: int = 0
    last_learning_at: datetime | None = None

    #: Pasangan korelasi yang tersimpan, dan lintasan yang gagal (PASAL 14.41).
    #: Dihitung terpisah dari ``news`` karena keduanya diam dengan cara yang
    #: sama - nol - dan hanya angka sendiri yang bisa membedakan "pasarnya
    #: sepi" dari "fasenya tidak pernah dipanggil".
    correlation_pairs: int = 0
    correlation_failures: int = 0
    last_correlation_at: datetime | None = None
    correlation_enabled: bool = True

    #: Ingatan pasar yang diproyeksikan, dan lintasan yang gagal (PASAL 15.2).
    memories: int = 0
    memory_failures: int = 0
    last_memory_at: datetime | None = None
    memory_enabled: bool = True

    #: Baris yang benar-benar dibuang pembersih retensi, dan lintasan yang
    #: gagal (bagian 25-26). Dihitung dengan alasan yang sama seperti korelasi:
    #: pembersih yang membuang nol baris karena tidak ada yang kedaluwarsa dan
    #: pembersih yang tidak pernah dipanggil sama-sama melaporkan nol.
    retensi_dibuang: int = 0
    retensi_failures: int = 0
    last_retensi_at: datetime | None = None
    retensi_enabled: bool = True

    #: Timeframe yang penilaian PASAL 15.44-nya berhasil dihitung, dan lintasan
    #: yang gagal.
    manfaat_dinilai: int = 0
    manfaat_failures: int = 0
    last_manfaat_at: datetime | None = None
    manfaat_enabled: bool = True

    #: Fase simulasi berpemicu (bagian 16.17).
    #:
    #: ``scenario_menyala`` nol adalah keadaan normal - bagian 16.2 justru
    #: melarang simulasi di tiap scan. Yang dibedakan olehnya adalah nol karena
    #: pasarnya tenang dan nol karena fasenya tidak pernah tersambung, dan
    #: ``last_scenario_at`` yang tetap ``None`` menandai yang kedua.
    scenario_menyala: int = 0
    scenario_disimpan: int = 0
    scenario_failures: int = 0
    last_scenario_at: datetime | None = None
    scenario_enabled: bool = True

    #: Penilaian bagian 16.19. ``last_skenario_nilai_at`` yang tetap ``None``
    #: berarti fasenya tidak pernah dipanggil - yang berbeda dari dipanggil dan
    #: tidak menemukan apa pun untuk dinilai.
    skenario_dinilai: int = 0
    skenario_nilai_failures: int = 0
    last_skenario_nilai_at: datetime | None = None

    #: Fase router Phase 17 (bagian 17.19). ``last_router_at`` yang tetap
    #: ``None`` berarti fasenya tidak pernah dipanggil - yang berbeda dari
    #: dipanggil dan tidak memilih siapa pun.
    #:
    #: Bedanya bukan akademis di sini: NONE adalah keluaran yang **wajar** bagi
    #: router ini, jadi `router_terpilih` nol tidak membuktikan apa pun sendiri.
    #: Yang membedakan rusak dari sehat adalah `router_dipertimbangkan`.
    router_dipertimbangkan: int = 0
    router_terpilih: int = 0
    router_failures: int = 0
    last_router_at: datetime | None = None

    #: Prediksi yang ditinjau ulang untuk kalibrasi dan reliability (SPEC 29,
    #: 30), dan lintasan yang gagal.
    #: Berapa kali penguncian ditunda karena candle bar itu belum tiba.
    #: Nol prediksi karena candle-nya terlambat dan nol prediksi karena pasarnya
    #: diam adalah dua hal yang sangat berbeda.
    lock_menunggu_candle: int = 0

    #: Berapa pasangan (market, horizon) yang digeser ke siklus berikutnya oleh
    #: `BATAS_KUNCI_PER_SIKLUS`. Dibedakan dari `lock_menunggu_candle`: yang itu
    #: menunggu **bukti**, yang ini menunggu **giliran**.
    lock_ditunda: int = 0

    review_ditinjau: int = 0
    review_failures: int = 0
    last_review_at: datetime | None = None
    review_enabled: bool = True

    results_announced: int = 0
    result_failures: int = 0
    signals_announced: int = 0

    #: Pemindaian yang benar-benar berjalan, dan yang tidak bisa - dua keadaan
    #: berbeda yang keduanya menghasilkan nol peristiwa. Menyatukannya membuat
    #: "pasar diam" tidak bisa dibedakan dari "bar tidak cukup" (SPEC 4).
    scanned: int = 0
    unscannable: int = 0
    events: int = 0
    scan_failures: int = 0
    last_scan_at: datetime | None = None

    #: Predictions frozen by this run (SPEC 20), and passes that could not.
    #: Without these the loop could convene the council on schedule, fail every
    #: time, and still print a line that reads like a healthy system - the same
    #: blind spot the refresh and resolve counters exist to close.
    locked: int = 0
    #: WAIT and NO_SIGNAL verdicts, frozen too (SPEC 28 needs them to judge
    #: whether standing aside was right). Counted apart from ``locked``
    #: because only the directional ones can ever become a paper trade, and a
    #: run that produced nothing but WAITs would otherwise read as a growing
    #: sample while the win/loss record stayed exactly where it was.
    locked_non_directional: int = 0
    lock_failures: int = 0
    #: Resolution passes where every signal looked at was unscoreable. The
    #: queue is ordered by due time, so an unscoreable head of queue is re-read
    #: every pass and can starve everything behind it; counting the condition is
    #: what makes that visible instead of looking like an idle system.
    clogged_passes: int = 0
    #: ``(market, interval)`` pairs lost to the per-cycle request ceiling,
    #: summed over passes. Deferred work is invisible in ``candles`` - it looks
    #: exactly like having nothing to do.
    deferred: int = 0
    #: Markets the last pass skipped because their exchange was shut. Replaced
    #: each pass rather than accumulated: IDX is closed most of the day, so a
    #: running total would name it for ever and mean nothing. This is the
    #: difference between "0 candle disegarkan" meaning "nothing was owed" and
    #: it meaning "nothing was asked".
    last_skipped_markets: list[str] = field(default_factory=list)
    #: Markets the last pass skipped because they had no refresh set at all -
    #: a configuration fault. Never merged with the line above: health names
    #: the reason, and "bursa tutup" said of a 24/7 market is a cause that
    #: cannot be true (SPEC 49).
    last_skipped_no_intervals: list[str] = field(default_factory=list)
    #: Whether the resolution phase was switched on for this run. Recorded so
    #: :meth:`summary` cannot print "0 sinyal dinilai" - a measurement - when
    #: the truth is that nothing was ever asked (SPEC 49).
    resolve_enabled: bool = True
    #: Same reasoning for the locking phase: "0 prediksi dikunci" has to be
    #: separable from "locking was never switched on".
    lock_enabled: bool = True
    #: Kapan siklus terakhir SELESAI - bukan kapan ia mulai. Bedanya satu
    #: durasi siklus penuh, dan penjaga kesehatan membandingkannya dengan
    #: anggaran yang lebih pendek dari itu.
    last_cycle_at: datetime | None = None
    last_resolve_at: datetime | None = None
    #: Durasi beberapa siklus terakhir, dalam detik.
    #:
    #: **Ada supaya anggaran "macet" bisa diturunkan dari apa yang siklus ini
    #: benar-benar makan, bukan dari tebakan yang dituliskan sekali.** Anggaran
    #: lama `tick_sec * 4` mengandaikan satu siklus jauh lebih murah daripada
    #: satu tick; terukur 2026-08-25, satu siklus memakan p50 64 detik terhadap
    #: tick 15 detik - jadi "empat tick" bahkan bukan satu siklus.
    cycle_seconds: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def catat_durasi(self, detik: float) -> None:
        """Simpan durasi siklus, buang yang paling lama.

        Jendelanya bergerak supaya anggaran mengikuti mesin apa adanya: mesin
        yang jadi lebih lambat melebarkan anggarannya sendiri, dan mesin yang
        jadi lebih cepat mengetatkannya - tanpa siapa pun menyunting konstanta.
        """
        self.cycle_seconds.append(detik)
        if len(self.cycle_seconds) > CYCLE_WINDOW:
            del self.cycle_seconds[0]

    @property
    def durasi_khas(self) -> float | None:
        """Durasi siklus yang khas, atau ``None`` kalau belum ada yang selesai.

        ``None`` berarti belum terukur, dan itu harus tetap bisa dibedakan dari
        nol - pemanggilnya yang memutuskan apa yang dipakai sebagai pengganti.
        """
        if not self.cycle_seconds:
            return None
        return median(self.cycle_seconds)

    def note_error(self, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(message[:200])

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": isoformat(self.started_at),
            "last_cycle_at": (
                isoformat(self.last_cycle_at) if self.last_cycle_at else None
            ),
            "last_resolve_at": (
                isoformat(self.last_resolve_at) if self.last_resolve_at else None
            ),
            "cycles": self.cycles,
            "failed_cycles": self.failed_cycles,
            "refresh_failures": self.refresh_failures,
            "resolve_failures": self.resolve_failures,
            "candles": self.candles,
            "requests": self.requests,
            "resolved": self.resolved,
            "awaiting_candles": self.awaiting_candles,
            "no_prices": self.no_prices,
            "unavailable_interval": self.unavailable_interval,
            "last_awaiting_candles": self.last_awaiting_candles,
            "last_no_prices": self.last_no_prices,
            "last_unavailable_interval": self.last_unavailable_interval,
            "resolve_pass_seen": self.resolve_pass_seen,
            "news_items": self.news_items,
            "news_failures": self.news_failures,
            "news_enabled": self.news_enabled,
            "scanned": self.scanned,
            "unscannable": self.unscannable,
            "events": self.events,
            "scan_failures": self.scan_failures,
            "locked": self.locked,
            "lock_failures": self.lock_failures,
            "lock_enabled": self.lock_enabled,
            "clogged_passes": self.clogged_passes,
            "deferred": self.deferred,
            "last_skipped_markets": list(self.last_skipped_markets),
            "resolve_enabled": self.resolve_enabled,
            "errors": list(self.errors),
        }

    def summary(self) -> str:
        """One line for the operator. Indonesian; the log keys stay English.

        The failure figures are printed unconditionally, and all three of them.
        A zero that means "nothing went wrong" and a zero that means "this is
        not the counter anything was written to" look identical on screen, and
        the second kind is what let a completely dead loop announce itself as
        healthy.
        """
        if not self.cycles:
            return "belum ada siklus upkeep yang selesai"
        parts = [
            f"{self.cycles} siklus",
            f"{self.candles} candle disegarkan",
            f"{self.requests} request",
        ]
        if self.news_enabled:
            parts.append(f"{self.news_items} berita masuk")
        else:
            parts.append("news dimatikan lewat ARUNA_UPKEEP_NEWS_ENABLED=false")
        if self.scanned or self.unscannable or self.events:
            # "Dipindai" dan "tidak bisa dipindai" disebut terpisah: keduanya
            # menghasilkan nol peristiwa, dan menyatukannya membuat riwayat bar
            # yang terlalu pendek terbaca sebagai pasar yang tenang.
            bagian = [f"{self.scanned} dipindai"]
            if self.unscannable:
                bagian.append(f"{self.unscannable} bar belum cukup")
            bagian.append(f"{self.events} peristiwa")
            parts.append(", ".join(bagian))
        if self.lock_enabled:
            # Both figures, always. Only the directional ones can become a
            # paper trade, so a run of nothing but WAITs has to be readable as
            # exactly that rather than as a sample that is growing.
            parts.append(
                f"{self.locked} prediksi berarah dikunci, "
                f"{self.locked_non_directional} WAIT/NO_SIGNAL dicatat"
            )
        else:
            parts.append("penguncian dimatikan lewat ARUNA_UPKEEP_LOCK_ENABLED=false")
        if self.resolve_enabled:
            parts.append(f"{self.resolved} sinyal dinilai")
        else:
            parts.append("resolusi dimatikan lewat ARUNA_UPKEEP_RESOLVE_ENABLED=false")
        # Labelled "putaran terakhir", and taken from the last-pass fields.
        # The run totals count observations rather than signals - the same
        # stuck prediction is re-read every pass - so printing them here reads
        # as a queue that keeps growing while nothing has changed.
        if self.resolve_pass_seen and (
            self.last_awaiting_candles
            or self.last_unavailable_interval
            or self.last_no_prices
        ):
            queue = []
            if self.last_awaiting_candles:
                queue.append(f"{self.last_awaiting_candles} menunggu candle menyusul")
            if self.last_unavailable_interval:
                queue.append(
                    f"{self.last_unavailable_interval} tanpa interval yang bisa "
                    "disampel"
                )
            if self.last_no_prices:
                queue.append(f"{self.last_no_prices} tanpa harga tersimpan")
            parts.append("putaran terakhir: " + ", ".join(queue))
        if self.clogged_passes:
            parts.append(f"{self.clogged_passes} putaran resolusi tersumbat")
        if self.deferred:
            parts.append(f"{self.deferred} interval ditunda karena batas request")
        if self.last_skipped_markets:
            parts.append(
                "putaran terakhir melewati "
                + ", ".join(self.last_skipped_markets)
                + " karena bursa tutup"
            )
        parts.append(
            f"{self.refresh_failures} refresh gagal, "
            f"{self.news_failures} news gagal, "
            f"{self.lock_failures} penguncian gagal, "
            f"{self.resolve_failures} resolusi gagal, "
            f"{self.failed_cycles} siklus gagal"
        )
        return ", ".join(parts)


class UpkeepLoop:
    """One task, two phases, on a timer.

    ``resolver`` is duck-typed rather than annotated: all this needs is
    ``resolve_due(reference=..., limit=...)``, and requiring a concrete class
    would make the loop untestable without a database behind it.

    **``resolver`` dan ``locker`` bawaannya ``None`` sejak 2026-08-25.** Sampai
    hari itu keduanya wajib dan selalu diisi ``SignalService`` - satu servis
    yang mengunci prediksi spot dan menilai hasilnya. Jalur spot dicabut atas
    keputusan operator, dan tidak ada penggantinya: rencana futures dinilai
    ``FuturesScheduler``, bukan loop ini. Loop tetap menyegarkan candle,
    menjalankan router, skenario, ingatan, korelasi dan laporan harian.
    """

    def __init__(
        self,
        *,
        refresher: CandleRefresher,
        resolver: Any = None,
        settings: UpkeepSettings,
        stats: UpkeepStats | None = None,
        locker: Any = None,
        scanner: Any = None,
        queue: Any = None,
        news: Any = None,
        daily: Any = None,
        learning: Any = None,
        #: Penyegar korelasi (PASAL 14.41). ``None`` mematikan fasenya - dan
        #: dengan itu, satu-satunya yang pernah mengisi tabel ``correlations``
        #: kembali menjadi perintah CLI yang diketik manusia.
        korelasi: Any = None,
        #: Proyektor ingatan pasar (PASAL 15.2). ``None`` mematikan fasenya -
        #: dan dengan itu, ingatan ARUNA berhenti di titik terakhir seseorang
        #: menjalankannya dengan tangan.
        memory: Any = None,
        #: Pembersih retensi (bagian 25-26). ``None`` mematikan fasenya - dan
        #: dengan itu basis data kembali tumbuh selamanya, yang adalah keadaan
        #: yang audit 2026-08-21 temukan: 506 MB dan nol retention.
        retensi: Any = None,
        #: Penilai PASAL 15.44 (:class:`aruna.upkeep.manfaat.PenilaiManfaat`).
        #: ``None`` mematikan fasenya - dan dengan itu gerbang per timeframe
        #: tidak pernah menutup, karena putusannya tidak pernah ditulis.
        manfaat: Any = None,
        #: Fase simulasi berpemicu (:class:`aruna.upkeep.skenario.
        #: PenyimulasiSkenario`, bagian 16.17). ``None`` mematikan fasenya - dan
        #: dengan itu Phase 16 menjadi kode yang benar, diuji, dan tidak pernah
        #: dipanggil: cacat yang berulang di proyek ini dan yang penjaganya ada
        #: di `tests/test_scenario_terpasang.py`.
        scenario: Any = None,
        #: Penilai bagian 16.19 (:class:`aruna.upkeep.skenario_nilai.
        #: PenilaiSkenario`). ``None`` mematikan fasenya - dan dengan itu
        #: skenario tersimpan dengan ``hasil`` NULL selamanya, yang persis
        #: keadaannya sampai baris ini ada.
        scenario_nilai: Any = None,
        #: Fase router Phase 17 (:class:`aruna.upkeep.router.FaseRouter`,
        #: bagian 17.19). ``None`` mematikan fasenya - dan dengan itu seluruh
        #: paket `aruna.router` menjadi kode yang benar, diuji, diekspor, dan
        #: tidak pernah dipanggil. Penjaganya di `tests/test_router_terpasang`.
        router: Any = None,
        #: `LearningService` untuk kalibrasi dan reliability (SPEC 29, 30).
        #: ``None`` mematikan fasenya - dan dengan itu keduanya berhenti di
        #: pengukuran terakhir yang seseorang ketik dengan tangan.
        review: Any = None,
        #: Yang menerima sejarah baru sesudah tiap pengukuran. Tanpa keduanya,
        #: pengukuran dihitung lalu dibuang: council tetap memakai angka dari
        #: saat proses menyala.
        review_council: Any = None,
        review_signals: Any = None,
        #: ``app_state``, untuk mencatat perubahan parameter otomatis
        #: (bagian 23). ``None`` mematikan pencatatannya - dan dengan itu
        #: kalibrasi kembali menimpa dirinya tanpa jejak dan tanpa jalan
        #: kembali.
        review_state: Any = None,
        research: Any = None,
        screening: Any = None,
        results: Any = None,
        signals: Any = None,
        #: Penyimpan ``app_state`` untuk denyut. ``None`` mematikan denyutnya -
        #: dan dengan itu, kemampuan mengukur waktu mati sama sekali.
        heartbeat_state: Any = None,
    ) -> None:
        self._refresher = refresher
        self._resolver = resolver
        self._locker = locker
        self._settings = settings
        self._stats = stats or UpkeepStats(started_at=now_utc())
        # Recorded on the statistics, not read from settings at print time, so
        # the one line the operator sees can tell "nothing was due" apart from
        # "nothing was ever asked" without holding a reference to the config.
        self._stats.resolve_enabled = settings.resolve_enabled
        self._stats.lock_enabled = settings.lock_enabled and locker is not None
        #: The bar each ``(market, horizon)`` was last locked for. One
        #: prediction per horizon per bar is the whole cadence, and this is
        #: what enforces it.
        self._locked_bar: dict[tuple[Market, Horizon], datetime] = {}
        #: Bar terakhir yang candle-nya benar-benar diambil, per
        #: ``(pasar, horizon)``. Gerbang untuk penguncian - lihat
        #: :meth:`_bukti_siap`.
        self._refreshed_bar: dict[tuple[Market, Horizon], datetime] = {}
        #: Bar terakhir yang penundaannya sudah dikatakan, per pasangan. Lihat
        #: :meth:`_catat_menunggu_candle`.
        self._menunggu_candle: dict[tuple[Market, Horizon], datetime | None] = {}
        #: Pasangan (pasar, horizon) yang sudah dikeluhkan sekali. Lihat
        #: :meth:`_note_horizon_not_offered`.
        self._horizon_not_offered: set[tuple[Market, Horizon]] = set()
        self._scanner = scanner
        self._news = news
        self._daily = daily
        self._learning = learning
        self._korelasi = korelasi
        self._memory = memory
        self._retensi = retensi
        self._manfaat = manfaat
        #: Apakah stempel penilaian terakhir sudah dibaca dari `app_state`.
        #: Sekali per proses - lihat `_manfaat_due_now`.
        self._manfaat_dimuat = False
        #: Titik awal antrean kunci, diputar tiap kali jatahnya menggigit.
        self._putaran_kunci = 0
        self._scenario = scenario
        self._scenario_nilai = scenario_nilai
        self._router = router
        self._review = review
        self._review_council = review_council
        self._review_signals = review_signals
        self._review_state = review_state
        self._research = research
        self._screening = screening
        self._results = results
        self._signals = signals
        self._heartbeat = heartbeat_state
        self._stats.news_enabled = settings.news_enabled and news is not None
        self._stats.correlation_enabled = (
            settings.correlation_enabled and korelasi is not None
        )
        self._stats.memory_enabled = settings.memory_enabled and memory is not None
        self._stats.retensi_enabled = (
            settings.retensi_enabled and retensi is not None
        )
        self._stats.manfaat_enabled = (
            settings.manfaat_enabled and manfaat is not None
        )
        self._stats.review_enabled = settings.review_enabled and review is not None
        self._stats.scenario_enabled = (
            settings.scenario_enabled and scenario is not None
        )
        # Dibangun sendiri kalau tidak dioper, supaya `_scan` tidak pernah
        # menghadapi antrean yang None - cabang yang hanya bisa dicapai lewat
        # perakitan yang salah adalah cabang yang tidak pernah diuji.
        self._queue = queue if queue is not None else AnalysisQueue()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # ---- accessors ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def stats(self) -> UpkeepStats:
        return self._stats

    @property
    def settings(self) -> UpkeepSettings:
        return self._settings

    @property
    def refresher(self) -> CandleRefresher:
        return self._refresher

    # ---- one cycle ------------------------------------------------------

    async def cycle(self, *, now: datetime | None = None) -> UpkeepStats:
        """Refresh candles first, then resolve.

        Each phase has its own ``try``/``except``: a failed refresh must not
        cancel the resolution pass, because signals whose candles are already
        stored deserve scoring even while a venue is unreachable.
        """
        moment = now or now_utc()
        mulai = monotonic()
        stats = self._stats

        try:
            result = await self._refresher.refresh(now=moment)
        except Exception as exc:
            log.exception("upkeep.refresh_failed")
            stats.refresh_failures += 1
            stats.note_error(f"refresh: {type(exc).__name__}: {exc}")
            # Cleared here too, not only in the `else` below. The docstring on
            # the field promises "replaced each pass", and leaving it alone on
            # the failure path broke that promise in the way that matters: an
            # exchange skipped last night stayed named for every later pass
            # that threw, so a DOWN verdict raised six cycles into an open
            # session still blamed a venue that had since opened.
            stats.last_skipped_markets = []
            stats.last_skipped_no_intervals = []
        else:
            stats.candles += result.candles
            stats.requests += result.requests
            stats.deferred += len(result.deferred)
            # Replaced, not appended: this is the state of the last pass, and a
            # pass that refreshed nothing has to be able to say why.
            stats.last_skipped_markets = [
                market.value for market in result.skipped_closed
            ]
            stats.last_skipped_no_intervals = [
                market.value for market in result.skipped_no_intervals
            ]
            # Catat bar yang candle-nya BENAR-BENAR diambil. Ini gerbang untuk
            # penguncian: `result.refreshed` saja, bukan `deferred` - yang
            # ditunda berarti candle-nya tidak pernah tiba, dan mencatatnya
            # sebagai siap akan membuka gerbang di atas bukti yang tidak ada.
            for pasar, interval in result.refreshed:
                self._refreshed_bar[(pasar, interval)] = bar_start(
                    moment, interval, market=pasar
                )

            for problem in result.failures:
                stats.refresh_failures += 1
                stats.note_error(f"refresh: {problem}")
            if result.refreshed or result.deferred or result.failures:
                log.info(
                    "upkeep.refreshed",
                    intervals=[f"{m.value}:{i.value}" for m, i in result.refreshed],
                    deferred=[f"{m.value}:{i.value}" for m, i in result.deferred],
                    candles=result.candles,
                    requests=result.requests,
                    failures=len(result.failures),
                )

        # News before scanning and before locking, because the council reads it
        # as evidence. Refreshing it after the verdict would mean every council
        # deliberates on the previous cycle's world.
        if self._news_due_now(moment):
            await self._ingest_news(moment)

        # Scanning sits between refreshing and scoring, because it reads the
        # bars this cycle just stored and must not wait a whole cycle to see
        # them.
        if self._scanner is not None:
            await self._scan(moment)

        if self._resolve_due_now(moment):
            await self._resolve(moment)

        # Locking comes last, and that order is deliberate. Scoring frees the
        # queue; locking adds to it. A tick that runs out of time part-way
        # through should leave less work behind than it found, not more - the
        # same reasoning `FuturesScheduler.tick` gives for scoring before
        # planning. It also means a prediction is convened against candles this
        # very cycle refreshed.
        if self._lock_enabled():
            await self._lock(moment)

        # Paling akhir, dan sengaja. Laporan harian merangkum hari yang sudah
        # lewat, jadi urutannya tidak mengubah isinya - tapi kalau ia jatuh di
        # awal, satu kegagalan di sana akan menunda penyegaran candle dan
        # penilaian sinyal, yaitu pekerjaan yang benar-benar sensitif waktu.
        if self._daily is not None:
            await self._send_daily(moment)

        # Sesudah laporan harian, dan dengan alasan yang sama: merangkum
        # catatan yang sudah ada, tidak sensitif waktu, dan tidak boleh
        # menunda pekerjaan yang sensitif waktu kalau ia gagal.
        if self._research is not None:
            await self._send_research(moment)

        # Pembelajaran adaptif (PASAL 12.27), sekali sehari.
        #
        # Di sini, bersama pekerjaan harian lain, dan bukan tiap siklus: ia
        # membaca seluruh sejarah dan menulis ratusan baris hasil, sementara
        # sejarahnya sendiri bertambah beberapa prediksi per jam. Menjalankannya
        # tiap menit menghabiskan waktu untuk menghitung ulang jawaban yang
        # sama.
        #
        # **Sebelum baris ini ada, seluruh Phase 12 diam di produksi.**
        # `AdaptiveLearningService` hanya dipanggil dari `cli.py`, jadi ia
        # belajar tepat ketika seseorang mengetik perintahnya - dan
        # `Strategist` yang membaca tabelnya membaca hasil dari entah kapan.
        # Kode yang benar, diuji, diekspor, dan tidak pernah dilewati.
        if self._learning is not None:
            await self._run_learning(moment)

        # Korelasi pasangan (PASAL 14.41), sejam sekali.
        #
        # Di sini bersama pekerjaan turunan lain, dan dengan alasan yang sama:
        # ia membaca bar yang siklus ini sendiri sudah segarkan, tidak sensitif
        # waktu, dan kegagalannya tidak boleh menunda penyegaran candle maupun
        # penilaian sinyal.
        #
        # **Sebelum baris ini ada, korelasi diam sepenuhnya di produksi.**
        # ``build_matrix`` hanya dipanggil dari ``cli.py``, jadi ia menghitung
        # tepat ketika seseorang mengetik perintahnya - dan tidak seorang pun
        # pernah mengetiknya: tabelnya nol baris, dan empat puluh amatan
        # berturut-turut melaporkan CORRELATION_RISK hilang.
        if self._korelasi_due_now(moment):
            await self._refresh_korelasi(moment)

        # Ingatan pasar (PASAL 15.2), sepuluh menit sekali.
        #
        # Sesudah `_resolve` di atas dengan sengaja: yang diproyeksikan adalah
        # signal yang hasilnya sudah final, dan penilaian itulah yang
        # membuatnya final. Menjalankannya lebih dulu berarti tiap ingatan
        # tertinggal satu siklus dari hasilnya sendiri.
        if self._memory_due_now(moment):
            await self._proyeksikan_memory(moment)

        # Pembersih retensi (bagian 25-26), sehari sekali.
        #
        # Sesudah proyeksi ingatan dengan sengaja, dan bukan sekadar urutan
        # yang enak dibaca: proyeksi membaca candle untuk menghitung dimensi
        # teknikalnya. Membersihkan lebih dulu berarti sapuan hari itu bisa
        # membuang bar yang proyeksi menit berikutnya butuhkan, dan ingatan
        # yang lahir sesudahnya kehilangan dimensinya tanpa satu pun error.
        if self._retensi_due_now(moment):
            await self._sapu_retensi(moment)

        # Penilaian PASAL 15.44, sehari sekali.
        #
        # SESUDAH proyeksi ingatan, dengan alasan yang berbeda dari retensi:
        # ingatan yang lahir pada siklus ini adalah bahan penilaiannya. Menilai
        # lebih dulu berarti putusan hari ini dibuat tanpa ingatan hari ini -
        # dan pada korpus yang baru berumur beberapa hari, itu bukan selisih
        # kecil.
        if await self._manfaat_due_now(moment):
            await self._nilai_manfaat(moment)

        # Bagian 16.19. Sesudah pasarnya bergerak, skenario yang horizonnya
        # lewat dinilai terhadap apa yang benar-benar terjadi.
        #
        # Tanpa fase ini `aruna.scenario.evaluasi` adalah modul yang ditulis,
        # diuji, diekspor, dan tidak pernah dipanggil - dan skenario tersimpan
        # dengan `hasil` NULL selamanya. Itu keadaannya sampai baris ini ada.
        if self._skenario_nilai_due_now(moment):
            await self._nilai_skenario(moment)

        # Kalibrasi dan reliability (SPEC 29, 30), sehari sekali.
        #
        # Terukur 2026-08-21: `calibration_snapshots` berisi tiga baris,
        # terakhir 2026-08-15. `learning.review()` punya tepat satu pemanggil
        # di seluruh kode - perintah `aruna learn` - dan tidak seorang pun
        # mengetiknya sejak itu. Sementara verdict yang tersimpan berbunyi
        # "OVERCONFIDENT in 35-50%, 50-65%, 65-80%, 80-96%".
        if self._review_due_now(moment):
            await self._review_pembelajaran(moment)

        # Screening pra-pembukaan IDX. Servicenya sendiri yang memutuskan
        # apakah sekarang jendelanya, jadi memanggilnya tiap tick aman - dan
        # tick inilah satu-satunya yang pasti berdetak selama ARUNA hidup.
        if self._screening is not None:
            await self._send_screening(moment)

        # Denyut, paling akhir dan tiap siklus.
        #
        # Di sini karena loop inilah satu-satunya yang pasti berdetak selama
        # ARUNA hidup - alasan yang sama dengan laporan harian di atas. Dan
        # **paling akhir** dengan sengaja: denyut yang ditulis di awal siklus
        # akan mengaku hidup pada siklus yang kemudian gagal seluruhnya.
        #
        # Yang dijaganya bukan uptime melainkan pembacaan: tanpa jejak ini,
        # tidak ada satu pun catatan tentang berapa lama ARUNA mati, dan diam
        # yang panjang tidak bisa dibedakan dari pasar yang sepi.
        if self._heartbeat is not None:
            await self._beat(moment)

        stats.cycles += 1
        # **Waktu SELESAI, bukan waktu mulai.** `moment` diambil di awal siklus
        # dan dioper ke setiap fase sebagai "as of" - benar untuk mereka, salah
        # untuk pertanyaan "kapan loop terakhir menyelesaikan sesuatu".
        #
        # Sebelumnya `moment` yang disimpan di sini, jadi umur yang dibaca
        # penjaga kesehatan adalah durasi siklus LALU ditambah waktu berjalan
        # siklus SEKARANG - dobel hitung. Terukur 2026-08-25 atas 401 siklus
        # sehat: penjaganya melanggar batasnya sendiri pada 100% siklus.
        #
        # Durasinya diukur dengan jam monotonik, bukan selisih jam dinding,
        # supaya penyetelan jam atau `now=` di test tidak menghasilkan durasi
        # negatif atau raksasa.
        durasi = max(0.0, monotonic() - mulai)
        stats.catat_durasi(durasi)
        stats.last_cycle_at = moment + timedelta(seconds=durasi)
        return stats

    async def _beat(self, moment: datetime) -> None:
        """Tulis denyut. Kegagalannya diisolasi seperti fase lain.

        Denyut yang gagal berarti satu laporan waktu mati yang terlalu panjang
        nanti; siklus yang ikut mati berarti candle yang tidak disegarkan.
        """
        try:
            await heartbeat.beat(self._heartbeat, moment)
        except Exception:
            log.exception("upkeep.heartbeat_failed")

    async def _send_daily(self, moment: datetime) -> None:
        """Kirim laporan harian kalau memang waktunya.

        Kegagalannya diisolasi seperti fase lain. Sebuah laporan yang gagal
        tidak boleh menghentikan siklus: yang hilang hanya satu pesan, dan yang
        akan hilang kalau siklusnya ikut mati adalah penyegaran candle dan
        penilaian sinyal.
        """
        try:
            if await self._daily.run(moment):
                self._stats.daily_reports += 1
                self._stats.last_daily_at = moment
        except Exception as exc:
            log.exception("upkeep.daily_failed")
            self._stats.daily_failures += 1
            self._stats.note_error(f"daily: {type(exc).__name__}: {exc}")

    async def _run_learning(self, moment: datetime) -> None:
        """Jalankan satu putaran pembelajaran, paling sering sekali sehari.

        Jendelanya dijaga di sini dan bukan di dalam service supaya service-nya
        tetap bisa dipanggil kapan saja lewat ``aruna learn`` - operator yang
        ingin melihat hasil terbaru sekarang tidak seharusnya menunggu jam
        tertentu.

        Kegagalannya diisolasi seperti fase lain. Pembelajaran yang gagal
        berarti angka yang membeku satu hari; siklus yang ikut mati berarti
        candle yang tidak disegarkan dan sinyal yang tidak dinilai.
        """
        terakhir = self._stats.last_learning_at
        if terakhir is not None and moment - terakhir < LEARNING_INTERVAL:
            return
        try:
            hasil = await self._learning.run(now=moment)
            self._stats.learning_runs += 1
            self._stats.last_learning_at = moment
            log.info(
                "upkeep.learning",
                observations=hasil.observations,
                patterns=hasil.stored_patterns,
            )
        except Exception as exc:
            log.exception("upkeep.learning_failed")
            self._stats.learning_failures += 1
            self._stats.note_error(f"learning: {type(exc).__name__}: {exc}")

    async def _send_screening(self, moment: datetime) -> None:
        """Kirim screening pra-pembukaan IDX kalau memang jendelanya.

        Kegagalannya diisolasi seperti fase lain: satu pemindaian yang gagal
        tidak boleh menghentikan penyegaran candle atau penilaian sinyal, yang
        justru sensitif waktu di jam yang sama.
        """
        try:
            if await self._screening.run(moment):
                self._stats.screenings += 1
                self._stats.last_screening_at = moment
        except Exception as exc:
            log.exception("upkeep.screening_failed")
            self._stats.screening_failures += 1
            self._stats.note_error(f"screening: {type(exc).__name__}: {exc}")

    async def _send_research(self, moment: datetime) -> None:
        """Kirim pertanyaan riset dan proposal yang menunggu keputusan.

        PASAL 11.16: yang dikirim adalah **pertanyaan**, bukan usulan
        perubahan. ARUNA membaca kekalahannya sendiri dan berhenti di situ;
        yang memutuskan apakah sebuah pertanyaan layak jadi proposal adalah
        orang.

        Kegagalannya diisolasi seperti fase lain. Analisis kekalahan membaca
        banyak baris, dan satu kueri yang gagal tidak boleh menghentikan
        penyegaran candle atau penilaian sinyal.
        """
        try:
            if await self._research.run(moment):
                self._stats.research_digests += 1
                self._stats.last_research_at = moment
        except Exception as exc:
            log.exception("upkeep.research_failed")
            self._stats.research_failures += 1
            self._stats.note_error(f"research: {type(exc).__name__}: {exc}")

    def _news_due_now(self, moment: datetime) -> bool:
        if not self._settings.news_enabled or self._news is None:
            return False
        last = self._stats.last_news_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.news_interval_sec

    async def _ingest_news(self, moment: datetime) -> None:
        """Tarik berita baru (PASAL 11).

        Sebelum fase ini ada, ``NewsService`` dibangun di ``app.py``, ditutup
        di ``shutdown``, dan tidak pernah dijalankan di antara keduanya - tidak
        ada metode ``start`` di kelasnya dan tidak ada pemanggil ``ingest``
        selain perintah CLI. Terukur saat ditemukan: 280 item, terakhir
        diambil enam puluh jam sebelumnya, sementara ``NewsAgent`` terus
        membacanya sebagai konteks sekarang.

        Itu lebih buruk daripada tidak ada berita sama sekali. Bukti yang
        hilang kelihatan; bukti basi tidak - ia masuk ke council dengan bobot
        penuh dan tidak seorang pun tahu umurnya (SPEC 4).

        Stempel waktunya dipasang pada PERCOBAAN, bukan keberhasilan: feed yang
        terus gagal harus tetap pada cadence-nya sendiri, bukan dicoba ulang
        tiap tick.
        """
        stats = self._stats
        stats.last_news_at = moment
        try:
            result = await self._news.ingest()
        except Exception as exc:
            log.exception("upkeep.news_failed")
            stats.news_failures += 1
            stats.note_error(f"news: {type(exc).__name__}: {exc}")
            return

        stored = getattr(result, "stored", 0)
        stats.news_items += stored
        for problem in getattr(result, "failures", []):
            stats.news_failures += 1
            stats.note_error(f"news: {problem}")

        # Dicatat SETIAP percobaan, termasuk yang tidak menyimpan apa-apa.
        #
        # Versi pertama fase ini hanya mencatat kalau ada yang tersimpan atau
        # gagal - dan begitu feed RSS sepi, fase yang berjalan tepat waktu tiap
        # lima menit menjadi tidak terbedakan dari fase yang tidak pernah
        # dipanggil sama sekali. Keduanya menghasilkan log yang persis sama:
        # kosong. Itu cacat yang sama persis dengan yang dibereskan fase ini
        # (berita basi dibaca sebagai berita sekarang), cuma pindah satu lapis
        # ke atas.
        #
        # Satu baris tiap lima menit tidak ada harganya. Tidak bisa menjawab
        # "apakah berita jalan?" ada harganya.
        log.info(
            "upkeep.news",
            fetched=getattr(result, "fetched", 0),
            stored=stored,
            duplicates=getattr(result, "duplicates", 0),
            linked=getattr(result, "linked", 0),
            failures=len(getattr(result, "failures", [])),
        )

    def _korelasi_due_now(self, moment: datetime) -> bool:
        if not self._settings.correlation_enabled or self._korelasi is None:
            return False
        last = self._stats.last_correlation_at
        if last is None:
            return True
        return (
            moment - last
        ).total_seconds() >= self._settings.correlation_interval_sec

    async def _refresh_korelasi(self, moment: datetime) -> None:
        """Hitung ulang korelasi pasangan (PASAL 14.41).

        Stempel waktunya dipasang pada PERCOBAAN, bukan keberhasilan - alasan
        yang sama seperti :meth:`_ingest_news`: lintasan yang terus gagal harus
        tetap pada cadence-nya sendiri, bukan dicoba ulang tiap tick.
        """
        stats = self._stats
        stats.last_correlation_at = moment
        try:
            hasil = await self._korelasi.refresh(now=moment)
        except Exception as exc:
            log.exception("upkeep.korelasi_failed")
            stats.correlation_failures += 1
            stats.note_error(f"korelasi: {type(exc).__name__}: {exc}")
            return

        pairs = sum(getattr(h, "stored", 0) for h in hasil)
        stats.correlation_pairs += pairs
        # Dicatat SETIAP percobaan, termasuk yang tidak menyimpan apa-apa: satu
        # pasar yang kehabisan bar menghasilkan nol, dan nol yang tidak dicatat
        # tidak bisa dibedakan dari fase yang tidak pernah dipanggil.
        log.info(
            "upkeep.korelasi",
            pasar=len(hasil),
            pairs=pairs,
            dilewati=sum(len(getattr(h, "dilewati", ())) for h in hasil),
        )

    def _memory_due_now(self, moment: datetime) -> bool:
        if not self._settings.memory_enabled or self._memory is None:
            return False
        last = self._stats.last_memory_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.memory_interval_sec

    async def _proyeksikan_memory(self, moment: datetime) -> None:
        """Bangun ingatan baru dari signal yang hasilnya sudah final (PASAL 15.2).

        **Terikat ``sampai=moment``**, dan bukan kehati-hatian umum: proyeksi
        yang membaca seluruh sejarah tanpa batas atas tetap membocorkan masa
        depan (PASAL 15.39/15.40), meskipun pencariannya nanti memakai ``as_of``
        yang benar.

        Stempel waktunya dipasang pada PERCOBAAN, bukan keberhasilan - alasan
        yang sama seperti :meth:`_ingest_news`.
        """
        stats = self._stats
        stats.last_memory_at = moment
        try:
            tersisip = await self._memory.proyeksikan(
                sampai=moment, limit=MEMORY_BATCH
            )
        except Exception as exc:
            log.exception("upkeep.memory_failed")
            stats.memory_failures += 1
            stats.note_error(f"memory: {type(exc).__name__}: {exc}")
            return

        # Jalur futures, di lintasan yang sama. Sumbernya berbeda tabel dan
        # berbeda ejaan simbol, jadi proyektornya sendiri - tapi cadence dan
        # penjaga kegagalannya sama, dan memisahkan fasenya berarti dua hal
        # yang bisa diam sendiri-sendiri.
        futures = 0
        try:
            futures = await self._memory.proyeksikan_futures(
                sampai=moment, limit=MEMORY_BATCH
            )
        except Exception as exc:
            log.exception("upkeep.memory_futures_failed")
            stats.memory_failures += 1
            stats.note_error(f"memory futures: {type(exc).__name__}: {exc}")

        stats.memories += tersisip + futures
        # Dicatat SETIAP percobaan, termasuk yang nol. Sesudah proyeksi pertama
        # selesai, nol adalah keadaan normal - dan nol yang tidak dicatat tidak
        # bisa dibedakan dari fase yang tidak pernah dipanggil.
        log.info("upkeep.memory", tersisip=tersisip, futures=futures)

    def _retensi_due_now(self, moment: datetime) -> bool:
        if not self._settings.retensi_enabled or self._retensi is None:
            return False
        last = self._stats.last_retensi_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.retensi_interval_sec

    async def _sapu_retensi(self, moment: datetime) -> None:
        """Buang baris yang sudah melewati umurnya (bagian 25-26).

        Stempel waktunya dipasang pada PERCOBAAN, bukan keberhasilan - alasan
        yang sama seperti :meth:`_refresh_korelasi`: sapuan yang terus gagal
        harus tetap pada cadence hariannya, bukan dicoba ulang tiap tick
        terhadap basis data yang sedang bermasalah.
        """
        stats = self._stats
        stats.last_retensi_at = moment
        try:
            hasil = await self._retensi.sapu(
                now=moment, batas_total=self._settings.retensi_batas_sapuan
            )
        except Exception as exc:
            log.exception("upkeep.retensi_failed")
            stats.retensi_failures += 1
            stats.note_error(f"retensi: {type(exc).__name__}: {exc}")
            return

        total = sum(hasil.values())
        stats.retensi_dibuang += total
        # Dicatat setiap sapuan, termasuk yang nol: sesudah tunggakan pertama
        # habis, nol adalah keadaan normal - dan nol yang tidak dicatat tidak
        # bisa dibedakan dari fase yang tidak pernah dipanggil.
        log.info(
            "upkeep.retensi",
            dibuang=total,
            per_tabel={k: v for k, v in hasil.items() if v},
        )

    def _skenario_nilai_due_now(self, moment: datetime) -> bool:
        if self._scenario_nilai is None:
            return False
        last = self._stats.last_skenario_nilai_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= SKENARIO_NILAI_INTERVAL_SEC

    async def _nilai_skenario(self, moment: datetime) -> None:
        """Nilai skenario yang horizonnya sudah lewat (bagian 16.19).

        Stempel pada PERCOBAAN, bukan keberhasilan - disiplin yang sama dengan
        fase periodik lain di sini. Sapuan yang terus gagal tidak boleh dicoba
        ulang tiap tick.
        """
        stats = self._stats
        stats.last_skenario_nilai_at = moment
        try:
            hasil = await self._scenario_nilai.nilai(now=moment)
        except Exception as exc:
            log.exception("upkeep.skenario_nilai_failed")
            stats.skenario_nilai_failures += 1
            stats.note_error(f"skenario nilai: {type(exc).__name__}: {exc}")
            return

        stats.skenario_dinilai += int(hasil.get("dinilai", 0))

    async def _manfaat_due_now(self, moment: datetime) -> bool:
        """Apakah penilaian PASAL 15.44 jatuh tempo.

        ``async`` karena jawabannya tidak ada di memori proses ini. Stempel
        penilaian terakhir hidup di ``app_state``, dan sebelum ia dibaca,
        ``last_manfaat_at`` yang ``None`` berarti "proses ini belum pernah
        menilai" - bukan "belum pernah dinilai".

        Bedanya terukur 2026-08-22: sapuan yang seharusnya sehari sekali
        berjalan lagi di **tiap restart**, dan pada hari dengan belasan restart
        ia dibayar belasan kali. Tiap satu menahan siklus pertama - yang sudah
        paling berat karena seluruh horizon jatuh tempo bersamaan.

        Dibaca **sekali**, bukan tiap siklus: sesudah pembacaan pertama,
        ``last_manfaat_at`` di memori sudah menjadi sumber yang benar, dan
        kueri per siklus untuk jawaban yang tidak berubah adalah biaya tanpa
        imbalan.
        """
        if not self._settings.manfaat_enabled or self._manfaat is None:
            return False

        if not self._manfaat_dimuat:
            self._manfaat_dimuat = True
            pembaca = getattr(self._manfaat, "terakhir_dinilai", None)
            if pembaca is not None:
                tersimpan = await pembaca()
                if tersimpan is not None:
                    self._stats.last_manfaat_at = tersimpan
                    log.info("upkeep.manfaat_dimuat", terakhir=isoformat(tersimpan))

        last = self._stats.last_manfaat_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.manfaat_interval_sec

    async def _nilai_manfaat(self, moment: datetime) -> None:
        """Nilai apakah ingatan membantu, per timeframe (PASAL 15.44).

        Stempel pada PERCOBAAN, bukan keberhasilan - alasan yang sama seperti
        korelasi dan retensi: sapuan kuadratik yang terus gagal tidak boleh
        dicoba ulang tiap tick.
        """
        stats = self._stats
        stats.last_manfaat_at = moment
        try:
            hasil = await self._manfaat.nilai(now=moment)
        except Exception as exc:
            log.exception("upkeep.manfaat_failed")
            stats.manfaat_failures += 1
            stats.note_error(f"manfaat: {type(exc).__name__}: {exc}")
            return

        stats.manfaat_dinilai = len(hasil)
        # Dicatat setiap penilaian, termasuk yang kosong, dan **per timeframe
        # dengan putusannya**: satu angka gabungan akan menyembunyikan justru
        # yang paling perlu dilihat, yaitu timeframe mana yang gerbangnya
        # tertutup dan sejak kapan.
        log.info(
            "upkeep.manfaat",
            dinilai=len(hasil),
            dipakai=sorted(tf for tf, m in hasil.items() if m.dipakai),
            digerbangi=sorted(tf for tf, m in hasil.items() if not m.dipakai),
        )

    def _review_due_now(self, moment: datetime) -> bool:
        if not self._settings.review_enabled or self._review is None:
            return False
        last = self._stats.last_review_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.review_interval_sec

    async def _review_pembelajaran(self, moment: datetime) -> None:
        """Ukur ulang kalibrasi dan reliability, lalu **pakai lagi** (SPEC 29, 30).

        Dua langkah, dan yang kedua yang paling mudah hilang.
        :meth:`ArunaApplication._load_measured_history` hanya berjalan saat
        proses menyala; mengukur ulang tiap hari tanpa menerapkannya kembali
        berarti council memakai angka dari saat start sampai proses dimatikan -
        pengukuran yang dihitung lalu dibuang.

        Stempel pada PERCOBAAN, bukan keberhasilan - alasan yang sama seperti
        fase harian lain di sini.
        """
        stats = self._stats
        stats.last_review_at = moment
        try:
            hasil = await self._review.review(
                limit=self._settings.review_limit, persist=True
            )
        except Exception as exc:
            log.exception("upkeep.review_failed")
            stats.review_failures += 1
            stats.note_error(f"review: {type(exc).__name__}: {exc}")
            return

        ditinjau = int(getattr(hasil, "reviewed", 0) or 0)
        stats.review_ditinjau = ditinjau

        diterapkan = False
        try:
            sejarah = await self._review.measured_history()
            for penerima in (self._review_council, self._review_signals):
                if penerima is not None:
                    penerima.use_history(sejarah)
            diterapkan = True
            await self._catat_perubahan_kalibrasi(sejarah, moment)
        except Exception as exc:
            log.exception("upkeep.review_apply_failed")
            stats.review_failures += 1
            stats.note_error(f"review apply: {type(exc).__name__}: {exc}")

        log.info(
            "upkeep.review", ditinjau=ditinjau, diterapkan=diterapkan
        )

    async def _catat_perubahan_kalibrasi(
        self, sejarah: Any, moment: datetime
    ) -> None:
        """Catat kalibrasi yang baru diterapkan, kalau ia berubah (bagian 23).

        Kalibrasi adalah satu-satunya parameter yang benar-benar berubah
        sendiri dan sampai ke keputusan: sejak 2026-08-21 angkanya menentukan
        keyakinan yang diterbitkan. Sebelum ini ia menimpa dirinya tiap hari
        tanpa catatan apa yang hilang dan tanpa jalan kembali.

        Hanya dicatat saat **berubah**. Baris identik tiap hari akan mengubur
        perubahan yang sesungguhnya di antara lima puluh baris yang tidak
        mengatakan apa-apa - dan riwayatnya berbatas.

        Kegagalan di sini tidak boleh menjatuhkan fase review: catatan yang
        hilang lebih murah daripada kalibrasi yang tidak pernah diterapkan.
        """
        if self._review_state is None:
            return
        try:
            from aruna.governance.rollback import (
                KUNCI_STATE,
                PerubahanParameter,
                catat,
                dari_json,
                ke_json,
                terakhir,
            )

            laporan = getattr(sejarah, "calibration_report", None)
            if laporan is None:
                return
            nilai = laporan.verdict

            riwayat = dari_json(await self._review_state.get(KUNCI_STATE))
            sebelumnya = terakhir(riwayat, "kalibrasi")
            if sebelumnya is not None and sebelumnya.baru == nilai:
                return

            brier = getattr(laporan, "brier", None)
            riwayat = catat(
                riwayat,
                PerubahanParameter(
                    nama="kalibrasi",
                    lama=sebelumnya.baru if sebelumnya else "(belum pernah diukur)",
                    baru=nilai,
                    alasan=(
                        f"diukur ulang dari {laporan.total} klaim terbitan"
                        + (f", brier {brier}" if brier is not None else "")
                    ),
                    pemicu="upkeep.review harian",
                    pada=moment,
                ),
            )
            await self._review_state.set(
                KUNCI_STATE, ke_json(riwayat), actor="upkeep.review"
            )
            log.info("upkeep.kalibrasi_berubah", detail=riwayat[-1].ringkas())
        except Exception:
            log.exception("upkeep.catat_kalibrasi_failed")

    async def _scan(self, moment: datetime) -> None:
        """Jalankan pemindai cepat dan antrekan yang bergerak (PASAL 14, 15).

        **Pemindai ini TIDAK menggerakkan council, dan itu disengaja.**

        Dua alternatifnya masing-masing merusak sesuatu. Kalau peristiwa
        menggantikan cadence bar, prediksi hanya lahir saat pasar bergerak -
        sampelnya condong ke periode ramai dan catatan menang-kalah berhenti
        sebanding lintas waktu, padahal mengukur itulah guna seluruh sistem
        ini. Kalau ia berjalan mendampingi, council digelar dua kali untuk
        keadaan yang sama, dan penjaga sekali-per-bar tidak menangkapnya karena
        pemicunya datang dari jalur lain.

        Jadi yang dikerjakannya adalah yang memang diminta PASAL 14: memisahkan
        yang bergerak dari yang diam, lalu mengantrekannya menurut seberapa
        jauh melewati ambangnya.

        **Yang jujur soal nilainya hari ini:** dengan lima simbol, memilih
        tidak menghemat apa pun - council tetap digelar untuk semuanya tiap
        bar. Yang benar-benar didapat sekarang adalah catatan APA yang bergerak
        dan KAPAN, terukur dan terbaca operator. Penghematannya baru nyata saat
        universe tumbuh, dan mengklaimnya sekarang berarti menjanjikan sesuatu
        yang tidak terjadi (SPEC 4).
        """
        stats = self._stats
        stats.last_scan_at = moment
        try:
            results = await self._scanner.scan(moment)
        except Exception as exc:
            log.exception("upkeep.scan_failed")
            stats.scan_failures += 1
            stats.note_error(f"scan: {type(exc).__name__}: {exc}")
            return

        scanned = sum(1 for r in results if r.scanned)
        stats.scanned += scanned
        stats.unscannable += len(results) - scanned

        for result in results:
            for event in result.events:
                if self._queue.offer(event):
                    stats.events += 1

        if stats.events or len(results) != scanned:
            log.info(
                "upkeep.scanned",
                symbols=len(results),
                scanned=scanned,
                unscannable=len(results) - scanned,
                queued=len(self._queue),
                dropped=self._queue.stats.dropped_full,
            )

        await self._simulasi_skenario(results, moment)
        await self._jalankan_router(results, moment)

    async def _jalankan_router(self, results: list[Any], moment: datetime) -> None:
        """Fase router Phase 17 (bagian 17.19).

        Dipanggil dari :meth:`_scan` dengan ``results`` yang sama, dan bukan
        dari :meth:`cycle`. Router hanya boleh memilih untuk aset yang
        BENAR-BENAR dipindai siklus ini: batas umur bacaan dihitung dalam bar
        horizonnya sendiri, jadi jendela 1d membentang delapan hari dan cukup
        untuk menghidupkan kembali aset yang sudah lama berhenti dipindai.
        Terukur 2026-08-23: 31 simbol punya bacaan "segar" sementara yang
        dipindai dua puluh.

        Kegagalannya tidak pernah menjatuhkan siklus - fase ini menghasilkan
        bukti, bukan keputusan.
        """
        if self._router is None:
            return

        stats = self._stats
        stats.last_router_at = moment
        try:
            hasil = await self._router.jalankan(results, now=moment)
        except Exception as exc:
            log.exception("upkeep.router_failed")
            stats.router_failures += 1
            stats.note_error(f"router: {type(exc).__name__}: {exc}")
            return

        stats.router_dipertimbangkan += hasil.dipertimbangkan
        stats.router_terpilih += hasil.terpilih

    async def _simulasi_skenario(self, results: list[Any], moment: datetime) -> None:
        """Fase simulasi berpemicu (bagian 16.17).

        Dipanggil dari :meth:`_scan` dan bukan dari :meth:`cycle`, karena bagian
        16.17 menaruh MIROFISH TRIGGER tepat sesudah EVENT DETECTOR - dan
        ``results`` **adalah** keluaran pendeteksi peristiwa. Memanggilnya dari
        `cycle` berarti memindai dua kali atau menyimpan hasilnya di bidang
        instans, dan keduanya kerja tambahan untuk urutan yang lebih buruk.

        Kegagalannya tidak pernah menjatuhkan siklus: fase ini menghasilkan
        bukti, bukan keputusan, dan siklus yang sama juga menghasilkan keputusan
        sungguhan.
        """
        if self._scenario is None:
            return

        stats = self._stats
        stats.last_scenario_at = moment
        try:
            hasil = await self._scenario.jalankan(results, now=moment)
        except Exception as exc:
            log.exception("upkeep.scenario_failed")
            stats.scenario_failures += 1
            stats.note_error(f"scenario: {type(exc).__name__}: {exc}")
            return

        stats.scenario_menyala += hasil.menyala
        stats.scenario_disimpan += hasil.disimpan

    def _lock_enabled(self) -> bool:
        return bool(self._settings.lock_enabled and self._locker is not None)

    def _horizons_due(self, moment: datetime) -> list[tuple[Market, Horizon]]:
        """The ``(market, horizon)`` pairs whose bar has turned over.

        One prediction per horizon per bar. The bar boundary is the cadence -
        not a timer - so a 15m horizon is locked once per 15m bar however often
        the loop ticks, and a restart mid-bar cannot lock a second prediction
        for a bar that already has one *in this process*.

        Across processes it can: ``_locked_bar`` lives in memory, so a restart
        re-locks the bar in progress. That is a duplicate prediction, not a
        corrupted one - both are frozen against real evidence and both are
        scored - and the alternative, reading the last locked bar back from the
        database on every tick, buys a rare deduplication with a query per
        horizon per tick. Stated rather than hidden: restarts inflate the
        sample slightly, and the inflation is visible as two snapshots sharing
        a bar.
        """
        due: list[tuple[Market, Horizon]] = []
        for market in self._settings.lock_market_set:
            # IDX berhenti total saat bursanya tutup, dan mulai lagi tiga puluh
            # menit sebelum bel pembuka (lihat `idx_active`).
            #
            # Terukur sebelum baris ini ada: prediksi IDX dikunci pukul 23:37
            # WIB - tujuh setengah jam sesudah bursa tutup. Horizon 1h yang
            # dimulai di situ berakhir pukul 00:37, seluruhnya di dalam bursa
            # yang tutup, jadi tidak ada satu pun bar yang bisa jatuh di
            # dalamnya. Prediksinya lahir sudah tidak bisa diskor.
            #
            # Council-nya sendiri sebenarnya sudah tahu: `notrade` memblokir
            # dengan MARKET_HALT saat `market_open` bernilai False. Tapi itu
            # berjalan SESUDAH seluruh deliberasi, jadi ongkosnya sudah
            # dikeluarkan - dan baris yang tersimpan tetap ada.
            if market is Market.IDX and not idx_active(moment):
                continue
            tersedia = horizons_for_market(market)
            for horizon in self._settings.lock_horizon_set:
                # Perkalian silang dua daftar yang harus cocok, dan sampai baris
                # ini ada, tidak ada yang mencocokkannya.
                #
                # Terukur di database: 22 prediksi IDX 15m dan 33 prediksi IDX
                # 1h tersimpan, sementara `horizons_for_market(IDX)` dimulai
                # dari 1d. Keduanya lolos karena `lock_horizon_set` dipakai apa
                # adanya untuk setiap pasar.
                #
                # Yang membuatnya diam adalah bahwa akibatnya muncul di tempat
                # lain: `refresh_intervals` menurunkan interval yang dijaga dari
                # `horizons_for_market`, jadi IDX menjaga (15m, 1h, 1d) dan
                # tidak pernah menjaga 1m. Sebuah prediksi IDX 15m hanya bisa
                # disampel dari 1m. Hasilnya 88 prediksi IDX terkunci selamanya,
                # tidak satu pun pernah menjadi WIN maupun LOSS - tanpa error,
                # tanpa kegagalan, hanya catatan yang tidak pernah bertambah.
                if horizon not in tersedia:
                    self._note_horizon_not_offered(market, horizon)
                    continue
                bar = bar_start(moment, horizon, market=market)
                if self._locked_bar.get((market, horizon)) != bar:
                    due.append((market, horizon))
        return due

    def _bagi_jatah_kunci(
        self, due: list[tuple[Market, Horizon]]
    ) -> tuple[list[tuple[Market, Horizon]], list[tuple[Market, Horizon]]]:
        """Yang dikunci siklus ini, dan yang menunggu siklus berikutnya.

        **Kenapa dibagi.** Biaya sebuah pasangan sebanding jumlah simbolnya -
        council digelar untuk tiap satu. Saat proses baru menyala,
        ``_locked_bar`` kosong sehingga **seluruh** pasangan jatuh tempo
        sekaligus, dan siklus pertama menanggung semuanya.

        Terukur 2026-08-22 sesudah restart: siklus pertama tidak selesai selama
        lima menit, dan pemeriksa kesehatan melaporkan ``upkeep: DOWN - siklus
        terakhir 5 menit lalu, lebih lama dari batas 60 detik``. Prosesnya
        sendiri sehat - ia cuma sedang mengerjakan segalanya sekaligus.

        Menunda **tidak** membuat buktinya basi: yang tertunda dikunci beberapa
        puluh detik kemudian di dalam bar yang sama, dengan candle yang sama
        yang `_bukti_siap` sudah tuntut ada. Bar berikutnya masih jauh - delapan
        pasangan terkuras dalam sekitar dua menit, sementara bar terpendek lima
        belas menit.

        **Diputar tiap siklus.** Tanpa itu, satu pasar yang kuncinya terus gagal
        akan selalu berada di depan antrean dan memakan seluruh jatah, sehingga
        pasar lain tidak pernah kebagian - kelaparan yang tidak menghasilkan
        satu pun galat.
        """
        if len(due) <= BATAS_KUNCI_PER_SIKLUS:
            return due, []

        putar = self._putaran_kunci % len(due)
        self._putaran_kunci += 1
        urut = due[putar:] + due[:putar]
        return urut[:BATAS_KUNCI_PER_SIKLUS], urut[BATAS_KUNCI_PER_SIKLUS:]

    def _siap_atau_ditunda(
        self, market: Market, horizon: Horizon, moment: datetime
    ) -> bool:
        """Siap dikunci; kalau tidak, katakan sekali dan tunda."""
        if self._bukti_siap(market, horizon, moment):
            return True
        self._catat_menunggu_candle(market, horizon)
        return False

    def _bukti_siap(
        self, market: Market, horizon: Horizon, moment: datetime
    ) -> bool:
        """Apakah candle bar ini sudah benar-benar diambil.

        **Kenapa gerbang ini ada.** Terukur di log produksi 2026-08-21::

            18:00:15.663  upkeep.locked      <- kunci menyala
            18:00:32.832  upkeep.refreshed   CRYPTO:15m  <- bar tiba 17 detik kemudian

        Kunci dan refresh berada di siklus berbeda, dan pemeriksa-jatuh-temponya
        tidak sepakat kapan batas bar lewat - jadi keputusan dibuat di atas bar
        yang tutup satu bar sebelumnya padahal yang terbaru tersedia beberapa
        detik kemudian.

        Ongkosnya terukur pada akurasi BUY dibanding garis dasar horizonnya:
        bukti bar terbaru **+7,2** poin di 15m dan **+8,9** di 1h; bukti satu
        bar lalu **-4,9** di keduanya. Dan yang basi adalah mayoritasnya -
        1.476 dari 2.070 di 15m.

        Bukan kebocoran: seluruh ``as_of`` jatuh tepat di batas bar, dan yang
        segar dikunci 19-45 detik SESUDAH batas itu.

        **Konsekuensi yang disengaja:** bar yang candle-nya tidak pernah tiba
        tidak menghasilkan prediksi sama sekali. Itu mengurangi jumlah sampel,
        dan itu lebih baik daripada prediksi yang terukur berkinerja negatif.
        """
        return self._refreshed_bar.get((market, horizon)) == bar_start(
            moment, horizon, market=market
        )

    def _catat_menunggu_candle(self, market: Market, horizon: Horizon) -> None:
        """Sekali per pasangan per bar, bukan tiap tick.

        Loop berdetak tiap lima belas detik; peringatan yang berulang di situ
        menenggelamkan dirinya sendiri. Tapi tetap dikatakan - nol prediksi
        karena candle-nya tak pernah tiba dan nol prediksi karena pasarnya diam
        adalah dua hal yang sangat berbeda.
        """
        kunci = (market, horizon)
        bar = self._refreshed_bar.get(kunci)
        if self._menunggu_candle.get(kunci) == bar:
            return
        self._menunggu_candle[kunci] = bar
        self._stats.lock_menunggu_candle += 1
        log.info(
            "upkeep.lock_menunggu_candle",
            market=market.value,
            horizon=horizon.value,
            detail=(
                "penguncian ditunda sampai candle bar ini tiba - bukti satu bar "
                "lalu terukur berkinerja negatif"
            ),
        )

    def _note_horizon_not_offered(self, market: Market, horizon: Horizon) -> None:
        """Katakan sekali, bukan tiap tick.

        Sekali per pasangan seumur proses. Loop berdetak tiap lima belas detik;
        peringatan yang berulang di situ akan menenggelamkan dirinya sendiri,
        dan itu kegagalan yang sama seperti banjir DUPLICATE.

        Tapi tetap **dikatakan**. Operator yang menulis 15m di
        ``lock_horizon_set`` sambil mengaktifkan IDX sedang meminta sesuatu yang
        tidak akan pernah terjadi, dan ia harus tahu itu dari satu baris log -
        bukan dari prediksi yang tidak pernah punya hasil.
        """
        kunci = (market, horizon)
        if kunci in self._horizon_not_offered:
            return
        self._horizon_not_offered.add(kunci)
        log.warning(
            "upkeep.horizon_not_offered",
            market=market.value,
            horizon=horizon.value,
            detail=(
                f"{horizon.value} tidak termasuk horizon {market.value}; "
                "prediksi pada horizon ini tidak akan pernah bisa diskor "
                "karena interval samplingnya tidak ikut dijaga"
            ),
        )

    async def _lock(self, moment: datetime) -> None:
        """Convene the council and freeze each verdict (SPEC 10, 20).

        Nothing here executes anything: a locked prediction is a paper record
        (SPEC 46). No order is placed, no leverage is changed, no funds move.
        """
        stats = self._stats
        # Bukti dulu, baru keputusan. Yang barnya sudah berganti tapi candle-nya
        # belum tiba ditunda - sengaja TIDAK ditandai, pola yang sama dengan
        # penanganan kegagalan di bawah: dicoba lagi tick berikutnya, bukan
        # hilang sampai bar berganti.
        due = [
            pasangan
            for pasangan in self._horizons_due(moment)
            if self._siap_atau_ditunda(*pasangan, moment)
        ]
        if not due:
            return

        due, tertunda = self._bagi_jatah_kunci(due)
        if tertunda:
            # Yang tertunda **tidak** ditandai, jadi ia tetap jatuh tempo dan
            # terambil siklus berikutnya - pola yang sama dengan penanganan
            # kegagalan di bawah.
            stats.lock_ditunda += len(tertunda)
            log.info(
                "upkeep.kunci_ditunda",
                dijalankan=[f"{m.value}:{h.value}" for m, h in due],
                ditunda=[f"{m.value}:{h.value}" for m, h in tertunda],
                batas=BATAS_KUNCI_PER_SIKLUS,
            )

        by_market: dict[Market, list[Horizon]] = {}
        for market, horizon in due:
            by_market.setdefault(market, []).append(horizon)

        for market, horizons in by_market.items():
            try:
                result = await self._locker.lock_signals(market, tuple(horizons))
            except Exception as exc:
                # The bar is deliberately NOT marked: a failed attempt has to be
                # retried on the next tick, or one unreachable venue costs the
                # whole bar. It settles by itself when the bar turns over.
                log.exception("upkeep.lock_failed", market=market.value)
                stats.lock_failures += 1
                stats.note_error(f"lock {market.value}: {type(exc).__name__}: {exc}")
                continue

            for horizon in horizons:
                self._locked_bar[(market, horizon)] = bar_start(
                    moment, horizon, market=market
                )
            locked = getattr(result, "locked", 0)
            non_directional = getattr(result, "recorded_non_directional", 0)
            stats.locked += locked
            stats.locked_non_directional += non_directional
            for problem in getattr(result, "failures", []):
                stats.lock_failures += 1
                stats.note_error(f"lock {market.value}: {problem}")
            log.info(
                "upkeep.locked",
                market=market.value,
                horizons=[h.value for h in horizons],
                locked=locked,
                non_directional=non_directional,
                skipped=getattr(result, "skipped", 0),
                failures=len(getattr(result, "failures", [])),
            )
            await self._announce_signals(result, moment)

    async def _announce_signals(self, result: Any, moment: datetime) -> None:
        """Dorong prediksi yang benar-benar dipublikasikan (PASAL 12A).

        Sumbernya ``published``, bukan ``signals``. Sebuah call berarah yang
        ARUNA sendiri putuskan untuk tidak dipublikasikan - bukti terlalu tua,
        confidence di bawah lantai - tetap tersimpan sebagai catatan, dan
        mendorongnya ke ponsel operator akan membatalkan keputusan menahan diri
        itu di satu-satunya tempat yang dibaca orang.
        """
        if self._signals is None:
            return
        published = getattr(result, "published", None) or []
        if not published:
            return
        try:
            terkirim = await self._signals.announce(
                [_signal_row(s) for s in published], now=moment
            )
            self._stats.signals_announced += terkirim
        except Exception as exc:
            log.exception("upkeep.signal_announce_failed")
            self._stats.note_error(f"signal push: {type(exc).__name__}: {exc}")

    async def _announce_results(self, result: Any, moment: datetime) -> None:
        """Beri tahu operator hasilnya, bukan cuma pendapatnya (PASAL 11, 12).

        Sebelum ini, skoring berhenti di database. Operator diberi tahu setiap
        kali ARUNA berpendapat dan tidak pernah diberi tahu saat ARUNA salah -
        track record yang isinya hanya bagian yang enak dibaca.

        Kegagalannya diisolasi: hasil yang tidak terkirim tidak boleh
        menghentikan pass resolusi, karena yang tercatat di database adalah
        buktinya dan pesan hanyalah salinannya.
        """
        if self._results is None:
            return
        pasangan = getattr(result, "scored", None) or []
        if not pasangan:
            return
        try:
            trades = getattr(result, "trades", None) or {}
            ekonomi = getattr(result, "economics", None) or {}
            baris = [
                _result_row(
                    signal,
                    outcome,
                    trades.get(signal.signal_id),
                    ekonomi.get(signal.signal_id),
                )
                for signal, outcome in pasangan
            ]
            await self._attach_votes(baris)
            await self._attach_published(baris)
            terkirim = await self._results.announce(baris, now=moment)
            self._stats.results_announced += terkirim
        except Exception as exc:
            log.exception("upkeep.result_announce_failed")
            self._stats.result_failures += 1
            self._stats.note_error(f"result: {type(exc).__name__}: {exc}")

    async def _attach_votes(self, baris: list[dict[str, Any]]) -> None:
        """Isi ``votes`` dari ``council_votes``, kalau tersimpan.

        Tanpa ini pesan hasil selalu berbunyi "Tidak ada catatan pemilihan untuk
        prediksi ini" - kalimat yang benar saat ditulis dan berhenti benar
        begitu suara agent mulai disimpan.

        Kegagalan pencarian **tidak** menggagalkan pesannya. Yang hilang kalau
        query ini gagal adalah satu blok keterangan; yang hilang kalau seluruh
        pesannya batal adalah kabar bahwa ARUNA salah, dan itu justru bagian
        yang paling tidak boleh hilang.
        """
        pencari = getattr(self._resolver, "votes_for", None)
        if pencari is None:
            return
        for row in baris:
            try:
                split = await pencari(str(row["signal_id"]))
            except Exception:
                log.exception("upkeep.votes_lookup_failed", signal_id=row["signal_id"])
                continue
            if split is not None:
                row["votes"] = split

    async def _attach_published(self, baris: list[dict[str, Any]]) -> None:
        """Tandai tiap baris: prediksinya pernah diumumkan, atau tidak.

        Operator melaporkannya begini: "belum ada signal, tiba-tiba result
        semua". Ia benar, dan angkanya membenarkannya - dalam dua belas jam, 73
        prediksi berarah diskor tanpa pernah dipublikasikan lawan 28 yang
        dipublikasikan. Prediksi yang ditahan karena bukti basi, cooldown atau
        duplikat tidak pernah sampai ke layar; hasilnya sampai.

        Yang ditambahkan di sini hanya keterangannya. Keputusan mengirim atau
        tidak ada di :class:`~aruna.notify.result.ResultNotifier`, yang juga
        yang mencatat berapa yang diredam.

        Kegagalan pencarian tidak menggagalkan pesannya - baris tanpa
        keterangan tetap dikirim. Lihat alasan arah kegagalannya di
        :meth:`~aruna.signals.service.SignalService.published_ids`.
        """
        pencari = getattr(self._resolver, "published_ids", None)
        if pencari is None or not baris:
            return
        try:
            terbit = await pencari([str(r["signal_id"]) for r in baris])
        except Exception:
            log.exception("upkeep.published_lookup_failed")
            return
        for row in baris:
            row["published"] = str(row["signal_id"]) in terbit

    def _resolve_due_now(self, moment: datetime) -> bool:
        if not self._settings.resolve_enabled or self._resolver is None:
            return False
        last = self._stats.last_resolve_at
        if last is None:
            return True
        return (moment - last).total_seconds() >= self._settings.resolve_interval_sec

    async def _resolve(self, moment: datetime) -> None:
        stats = self._stats
        limit = self._settings.resolve_limit
        # Stamped on the attempt, not on success. A resolution pass that keeps
        # failing - an unreachable database, say - must stay on its own cadence
        # rather than being retried every tick.
        stats.last_resolve_at = moment
        try:
            result = await self._resolver.resolve_due(reference=moment, limit=limit)
        except Exception as exc:
            log.exception("upkeep.resolve_failed")
            stats.resolve_failures += 1
            stats.note_error(f"resolve: {type(exc).__name__}: {exc}")
            return

        stats.resolved += getattr(result, "resolved", 0)
        await self._announce_results(result, moment)
        awaiting = getattr(result, "awaiting_candles", 0)
        no_prices = getattr(result, "no_prices", 0)
        # The third unscoreable category, and the only permanent one. Left
        # unread, a batch that was 100% unavailable_interval produced
        # awaiting=0, no_prices=0, resolved=0 - indistinguishable from an idle
        # system, while health went on promising the backlog would drain.
        unavailable = getattr(result, "unavailable_interval", 0)
        stats.awaiting_candles += awaiting
        stats.no_prices += no_prices
        stats.unavailable_interval += unavailable
        # Replaced, not added to. These describe the queue as this pass found
        # it; adding them would make the same stuck signal count again on every
        # pass, which is exactly the arithmetic that produced "36 di antaranya"
        # about ten due signals.
        stats.last_awaiting_candles = awaiting
        stats.last_no_prices = no_prices
        stats.last_unavailable_interval = unavailable
        stats.resolve_pass_seen = True
        for problem in getattr(result, "failures", []):
            stats.resolve_failures += 1
            stats.note_error(f"resolve: {problem}")

        # A full batch that scored nothing means the head of the queue is stuck,
        # and `due()` orders by due time - so the same signals come back next
        # pass and everything behind them waits. Said out loud, because an
        # unreported version of this looks identical to having nothing to do.
        if awaiting + no_prices + unavailable >= limit:
            stats.clogged_passes += 1
            log.warning(
                "upkeep.resolve_clogged",
                limit=limit,
                awaiting_candles=awaiting,
                no_prices=no_prices,
                unavailable_interval=unavailable,
                detail=(
                    "the whole batch was unscoreable; older signals behind it "
                    "cannot be reached until the head of the queue clears, and "
                    "the unavailable_interval share never clears on its own"
                ),
            )
        elif getattr(result, "resolved", 0):
            log.info(
                "upkeep.resolved",
                resolved=result.resolved,
                awaiting_candles=awaiting,
                no_prices=no_prices,
                unavailable_interval=unavailable,
            )

    # ---- driving it -----------------------------------------------------

    async def run_until(
        self,
        until: datetime,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> UpkeepStats:
        """Cycle on a timer until ``until``.

        ``sleep`` and ``now`` are injectable so the loop can be exercised
        without waiting on real time - the same arrangement
        ``FuturesScheduler.run_until`` uses, and the reason its schedule is
        testable at all.
        """
        rest = sleep or asyncio.sleep
        clock = now or now_utc

        while clock() < until:
            moment = clock()
            try:
                await self.cycle(now=moment)
            except Exception as exc:
                # Deliberately broad. cycle() already guards each phase, so
                # arriving here means something outside them broke; the run
                # still has to survive it.
                self._stats.failed_cycles += 1
                self._stats.note_error(f"cycle: {type(exc).__name__}: {exc}")
                log.exception("upkeep.cycle_failed")

            remaining = (until - clock()).total_seconds()
            if remaining <= 0:
                break
            await rest(min(self._settings.tick_sec, remaining))

        return self._stats

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="aruna-upkeep")
        log.info(
            "upkeep.started",
            tick_sec=self._settings.tick_sec,
            resolve_enabled=self._settings.resolve_enabled,
            resolve_interval_sec=self._settings.resolve_interval_sec,
            resolve_limit=self._settings.resolve_limit,
        )

    async def stop(self, *, grace_sec: float | None = None) -> None:
        """Ask the loop to finish, and only cancel it if it will not.

        ``_stopping`` is what ``_loop()`` already watches, so setting it ends
        the run after the cycle in flight - which is the point. Cancelling
        first, as this used to, tears a resolution pass apart between its
        writes: ``_resolve_one`` records samples, then the outcome, then the
        RESOLVED status, then the paper trade, and a cancel between any two of
        those leaves a prediction that nothing will ever retry (``due()``
        returns only LOCKED rows) and that SPEC 22 forbids editing. It cost one
        corrupted signal per SIGINT, at the head of the queue.

        The cancel is still there for the case the grace was written for: a
        cycle wedged on a socket that never times out. A shutdown that hangs
        for ever is its own failure.
        """
        if self._task is None:
            return
        grace = STOP_GRACE_SEC if grace_sec is None else grace_sec
        self._stopping.set()
        try:
            # wait_for cancels the task itself once the grace runs out, and
            # waits for that cancellation to be delivered - so the timeout path
            # still leaves nothing running behind us.
            await asyncio.wait_for(self._task, timeout=grace)
        except TimeoutError:
            log.warning(
                "upkeep.stop_forced",
                grace_sec=grace,
                detail=(
                    "the cycle in flight did not finish within the grace and "
                    "was cancelled; a resolution pass cut mid-write leaves a "
                    "signal half-recorded"
                ),
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            # The task's own failure, surfaced when we awaited it. cycle()
            # guards both phases, so arriving here is the loop scaffolding
            # breaking - worth a traceback, never worth blocking shutdown.
            log.exception("upkeep.stop_failed")
        finally:
            self._task = None
        log.info("upkeep.stopped", **{
            k: v for k, v in self._stats.to_dict().items() if k != "errors"
        })

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats.failed_cycles += 1
                self._stats.note_error(f"cycle: {type(exc).__name__}: {exc}")
                log.exception("upkeep.cycle_failed")
            try:
                # Waiting on the stop event rather than sleeping means shutdown
                # does not have to sit out a full tick.
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.tick_sec
                )
            except TimeoutError:
                continue


__all__ = ["MAX_ERRORS", "STOP_GRACE_SEC", "UpkeepLoop", "UpkeepStats"]
