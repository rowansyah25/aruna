"""Signal service: council verdict -> locked prediction -> outcome.

Two entry points:

* :meth:`SignalService.lock_signals` runs the council across horizons and
  freezes the results (SPEC 10, 20).
* :meth:`SignalService.resolve_due` scores predictions whose horizon has
  elapsed, against stored prices (SPEC 22, 23).

They are separate on purpose. SPEC 21 requires the prediction to be published
*before* the outcome is known, and a single method that predicted and scored in
one pass would make that guarantee impossible to inspect.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from aruna.agents.service import DeliberationService
from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.council.session import Council
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.signals import SignalRepository
from aruna.db.types import as_utc
from aruna.signals.anomaly import detect as detect_anomalies
from aruna.signals.keyakinan import periksa_keyakinan, pita
from aruna.signals.lock import build_signal, should_lock, verify_integrity
from aruna.signals.models import LockedSignal, SignalStatus
from aruna.signals.multihorizon import MultiHorizonView, build_view
from aruna.signals.outcome import (
    MIN_OBSERVATIONS,
    STORED_INTERVALS,
    build_samples,
    is_resolvable,
    resolve,
    sampling_intervals,
)
from aruna.signals.paper import DEFAULT_CAPITAL as PAPER_CAPITAL
from aruna.signals.paper import close_trade, default_capital, open_trade
from aruna.signals.quality import MIN_QUALITY, score_signal
from aruna.signals.quality import gate as quality_gate
from aruna.signals.repetition import (
    cooldown_after_loss,
    cooldown_overridden,
    is_duplicate,
)
from aruna.signals.stabilitas import perlu_konfirmasi
from aruna.signals.withheld import PERLU_PERHATIAN, Withheld
from aruna.signals.withheld import classify as classify_withheld
from aruna.upkeep.candles import refresh_intervals

log = get_logger("aruna.signals")

#: Notional per simulated position now depends on the market, because the two
#: markets no longer share a quote currency - see
#: :data:`aruna.signals.paper.DEFAULT_CAPITAL`. Re-exported here because
#: ``aruna.signals.service.DEFAULT_CAPITAL`` was the public name.
DEFAULT_CAPITAL = PAPER_CAPITAL

#: Bars to read when looking for observations inside a horizon. Generous: a 1d
#: prediction sampled from 1h bars needs 24, and the window is filtered after.
SAMPLE_WINDOW = 400

#: Said once per resolution run, not once per signal. An IDX horizon runs on the
#: wall clock, so a "1d" prediction made on a Friday has seen far less market
#: than one made on a Tuesday - and nothing here deducts the closure.
#:
#: Indonesian because it is operator prose: ``aruna resolve`` prints every entry
#: of :attr:`ResolveResult.notes` straight to the screen (cli.py). Log event
#: names and structlog fields stay English; this is neither.
IDX_CLOCK_CAVEAT = (
    "horizon IDX diukur dengan jam dinding; penutupan malam dan akhir pekan "
    "tidak dikurangkan dari waktu yang sudah berjalan"
)

#: MySQL ``ER_DUP_ENTRY``. Named here because :meth:`_record_outcome_once` has
#: to tell "this signal was already scored by a pass that was interrupted"
#: apart from "the database is unwell", and those two demand opposite actions.
_MYSQL_DUPLICATE_ENTRY = 1062

#: How stale a sampling series may be before resolution refuses to score from
#: it, as a multiple of that interval's own length. Three leaves room for one
#: missed refresh plus one retry.
#:
#: A constant, not a setting: this is the SPEC 22 integrity rule, not an
#: operational lever. :func:`~aruna.signals.outcome.build_samples` marks the
#: *last available* observation ``is_final``, and :func:`resolve` reads that
#: one as the actual move, the final price, and the paper trade's exit. So
#: scoring a 1d prediction while the 1h series stops a quarter of the way
#: through its horizon records a quarter-point price as the outcome - and six
#: 1h bars clear ``MIN_OBSERVATIONS`` without difficulty. SPEC 22 forbids
#: editing a scored prediction afterwards, so that damage is permanent.
#:
#: Refreshing before resolving is not enough on its own, because the catch-up
#: can fail. The guard has to be per signal.
CANDLE_FRESHNESS_FACTOR = 3.0


def maintained_intervals(market: Market) -> tuple[Horizon, ...]:
    """Intervals something actually keeps current for ``market``.

    Asked of :func:`~aruna.upkeep.candles.refresh_intervals` - the refresher's
    own derivation - rather than restated here. A second list would be free to
    drift out of step, and the symptom of that drift is not an error message: it
    is a prediction held back for ever waiting on a series nothing is writing.

    ``STORED_INTERVALS`` stands in for the provider's capability list because
    resolution has no provider to ask. That makes this the *widest* set the
    refresher could keep current - a provider that serves fewer, or an
    ``ARUNA_UPKEEP_CANDLE_INTERVALS`` override that narrows it, can only make
    the real set smaller. Erring wide is the safe direction: over-stating what
    is maintained can only make :meth:`SignalService._prices_during` keep
    waiting, while under-stating it would let a prediction be scored from a
    coarse endpoint while the finer bars it should have used were still on their
    way - and SPEC 22 forbids correcting that afterwards.
    """
    return refresh_intervals(market, STORED_INTERVALS)


def _pct(sebelum: Any, sesudah: Any) -> float | None:
    """Perubahan harga dalam persen, atau ``None`` kalau tak bisa dihitung.

    ``None`` MENAHAN pembalikan, bukan meloloskannya - lihat
    :func:`~aruna.signals.stabilitas.perlu_konfirmasi`. Sebuah pembalikan yang
    tidak bisa dibuktikan terkonfirmasi tidak boleh lewat hanya karena
    pengukurannya gagal.
    """
    try:
        awal = Decimal(str(sebelum))
        akhir = Decimal(str(sesudah))
    except (TypeError, ValueError, ArithmeticError):
        return None
    if awal == 0:
        return None
    return float((akhir - awal) / awal * 100)


def _nama_pita(skor: Any) -> str | None:
    """Nama pita mutu untuk sebuah skor (bagian 18.41), atau ``None``.

    ``None`` ketika skornya tak terukur - dan itu berbeda dari ``POOR``. Skor
    yang tidak bisa dihitung dan skor yang dihitung lalu jelek menuntut
    tindakan yang berbeda.
    """
    nama = pita(None if skor is None else float(skor))
    return None if nama is None else nama.value


def _bangun_kalibrator(history: Any) -> Any:
    """Kalibrator dari laporan yang sudah diukur (bagian 9), atau yang kosong.

    Kosong ketika belum ada laporan - dan kalibrator kosong tidak menyesuaikan
    apa pun. Itu perilaku yang benar untuk sistem yang belum pernah mengukur
    dirinya: diam berarti belum diukur, bukan sudah benar.
    """
    from aruna.learning.kalibrator import Kalibrator

    laporan = getattr(history, "calibration_report", None) if history else None
    return Kalibrator(laporan)


def _already_scored(exc: BaseException) -> bool:
    """True when the write failed because ``paper_results`` already has the row.

    :class:`aruna.db.pool.Database` flattens every driver failure into a
    :class:`DatabaseError` carrying a message and the offending SQL, so the
    error number is not on the exception itself. It survives as ``__cause__``
    today, and the message carries "Duplicate entry" either way - both are
    checked, because a future ``raise ... from None`` would silently re-arm the
    permanent wedge this guards against.

    The table name is required as well as the duplicate marker. Every failure on
    this statement mentions ``paper_results`` (``_describe`` appends the SQL), so
    the table alone proves nothing; without the duplicate marker a lost
    connection would be read as "already scored" and the signal would be flipped
    to RESOLVED with no outcome row at all - worse than the fault being handled.
    """
    text = str(exc).lower()
    if "paper_results" not in text:
        return False
    cause = exc.__cause__
    args = getattr(cause, "args", ())
    if args and args[0] == _MYSQL_DUPLICATE_ENTRY:
        return True
    return "duplicate entry" in text


class PriceWindow(NamedTuple):
    """Observations for one signal, and why there are none when there are none.

    ``blocked_by`` separates the two silences that look alike in a counter:
    ``"stale_candles"`` means the data has not arrived *yet*, and
    ``"interval_unavailable"`` means no later refresh can change the answer -
    either because ARUNA stores no interval this horizon could be sampled from,
    or because the only series still holding it back is one nothing keeps
    current.

    Both of those are storage decisions, and neither is a statement about what
    a venue publishes. This used to read "no provider serves any interval this
    horizon could be sampled from", which was true when it was written and
    stopped being true at PASAL 5: binance-spot serves 3m, ``aruna providers``
    prints it, and a 3m horizon still lands in this counter. The behaviour was
    right and the reason attached to it was not, which is the failure SPEC 49
    names - and the reason is the part an operator acts on.

    ``detail`` carries what was *measured* on the paths where nothing blocked
    and there were still no observations, so the counter is not the only thing
    the operator gets. English: it goes to the log, not to the screen.
    """

    prices: list[tuple[datetime, Decimal]]
    interval: Horizon | None
    blocked_by: str | None
    detail: str | None = None


#: Bar yang dibaca untuk memeriksa anomali (PASAL 11.8).
#:
#: Garis dasarnya dihitung dari semua kecuali bar terakhir, jadi angka ini
#: adalah panjang garis dasar plus satu. Cukup panjang supaya satu bar ramai
#: tidak mengangkat rata-ratanya, cukup pendek supaya garis dasarnya masih
#: menggambarkan pasar yang sama.
ANOMALY_BASELINE_BARS = 30


class _AsSignal:
    """Baris database dibaca lewat atribut, seperti ``LockedSignal``.

    ``is_duplicate`` sengaja tidak tahu apa-apa soal database - ia menerima dua
    hal yang punya ``direction``, ``reference_price`` dan ``target_price``.
    Adaptor tipis ini yang menyambungkannya, bukan sebaliknya.
    """

    __slots__ = ("_row",)

    def __init__(self, row: dict) -> None:
        self._row = row

    def __getattr__(self, name: str):
        return self._row.get(name)


@dataclass(slots=True)
class LockResult:
    locked: int = 0
    recorded_non_directional: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)
    signals: list[LockedSignal] = field(default_factory=list)
    published: list[LockedSignal] = field(default_factory=list)
    #: Directional calls the system declined to publish, each with the reason it
    #: was declined. A count alone would let a run withheld entirely for stale
    #: data look the same as a genuinely quiet market.
    withheld: list[tuple[LockedSignal, str]] = field(default_factory=list)
    views: list[MultiHorizonView] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"locked={self.locked}",
            f"wait_recorded={self.recorded_non_directional}",
        ]
        if self.withheld:
            parts.append(f"withheld={len(self.withheld)}")
        if self.skipped:
            parts.append(f"skipped={self.skipped}")
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


@dataclass(slots=True)
class ResolveResult:
    resolved: int = 0
    not_due: int = 0
    no_prices: int = 0
    #: The share of ``no_prices`` that is already known to be permanent: every
    #: sampling series that has any rows at all runs *past* the end of the
    #: horizon, and not one of its candles falls inside it. Bars are appended
    #: going forward, so no later refresh can put one in a window that is closed
    #: and in the past. A subset of ``no_prices``, never added to it - the same
    #: prediction must not be counted twice.
    no_bars_in_window: int = 0
    #: Of those, the ones whose lifecycle was closed as UNSCOREABLE on this
    #: pass. Kept apart from ``no_bars_in_window`` because the second is a
    #: measurement and this is an action taken because of it - and an action
    #: that silently failed would otherwise look identical to one that worked.
    unscoreable: int = 0
    #: Scored from the horizon's own interval because nothing finer was stored -
    #: an endpoint, not a path. Reported because it limits what the SPEC 23
    #: class can mean.
    coarsely_sampled: int = 0
    #: Held back because a sampling series *the refresher keeps current* is
    #: stale: the data has not caught up yet. These stay LOCKED and will be
    #: scoreable once it does.
    awaiting_candles: int = 0
    #: Held back by something no amount of waiting fixes: either no provider
    #: serves any interval this horizon could be sampled from, or the only
    #: series still behind is one nothing refreshes. Counted apart from the
    #: above on purpose - one is a wait, the other is a wait that will never
    #: end.
    unavailable_interval: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    #: ``(signal, outcome)`` untuk tiap prediksi yang baru diskor.
    #:
    #: ``outcomes`` saja tidak cukup untuk memberi tahu operator: ia memuat
    #: hasilnya tanpa simbol, tanpa entry, tanpa target. Sebelum daftar ini
    #: ada, hasil skoring berhenti di database - ARUNA mengumumkan
    #: pendapatnya dan diam soal apakah pendapat itu benar.
    scored: list = field(default_factory=list)
    #: ``signal_id`` -> hasil paper trade (``WIN``/``LOSS``/``BREAKEVEN``).
    #:
    #: Inilah satu-satunya tempat menang dan kalah benar-benar diputuskan:
    #: dari harga masuk, harga keluar, dan ongkosnya. Kelas outcome SPEC 23
    #: menjawab pertanyaan lain - **bagaimana** prediksinya meleset - dan tidak
    #: satu pun nilainya berarti "kalah".
    #:
    #: Sebelum peta ini ada, pesan hasil memakai kelas outcome untuk keduanya.
    #: Akibatnya asimetri yang menyanjung: setiap kemenangan terkirim sebagai
    #: 🟢 WIN, setiap kekalahan sebagai 🟡 dengan kalimat - dan spec-nya
    #: meminta kalah dikirim dengan cara yang sama persis dengan menang.
    trades: dict[str, str] = field(default_factory=dict)
    #: ``signal_id`` -> (kotor, biaya, bersih) dari paper trade yang sama.
    #:
    #: Peta di atas menjawab "menang atau kalah"; ini menjawab "berapa, dan
    #: berapa yang dimakan ongkos". Dua peta dan bukan satu karena yang pertama
    #: sudah dibaca di beberapa tempat sebagai string.
    #:
    #: Angkanya bukan hiasan. Ongkos bolak-balik terukur 3,67 pada modal paper
    #: sekarang, sementara gerak kotor satu horizon 15 menit biasanya 0-7 - jadi
    #: sebagian besar prediksi yang ARAHNYA BENAR tetap kalah uang. Pesan yang
    #: menyebut "arahnya benar" di sebelah kata LOSS tanpa menunjukkan ongkosnya
    #: terbaca seperti sistem yang menyangkal kekalahannya sendiri.
    economics: dict[str, tuple] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [f"resolved={self.resolved}"]
        if self.coarsely_sampled:
            parts.append(f"coarsely_sampled={self.coarsely_sampled}")
        if self.awaiting_candles:
            parts.append(f"awaiting_candles={self.awaiting_candles}")
        if self.unavailable_interval:
            parts.append(f"unavailable_interval={self.unavailable_interval}")
        if self.no_prices:
            parts.append(f"no_prices={self.no_prices}")
        if self.no_bars_in_window:
            parts.append(f"no_bars_in_window={self.no_bars_in_window}")
        if self.unscoreable:
            parts.append(f"unscoreable={self.unscoreable}")
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


@dataclass(slots=True)
class _ClaimedScore:
    """A score this process wrote to ``paper_results`` and has not finished.

    ``record_outcome`` is a plain INSERT under a unique key, so the row it
    writes *is* the record for that prediction, permanently (SPEC 22). The
    writes after it - the samples, the paper trade, the status flip - can still
    fail, and what a later pass must then do is finish **that** score rather
    than compute a new one. Recomputing is not harmless: the loop refreshes
    candles before every resolution pass, so a second reading of the same
    horizon can pick a different sampling interval or find bars that were not
    there yet, and writing those numbers next to a stored outcome that says
    something else is exactly how ``paper_results`` and ``paper_trades`` came to
    disagree about one prediction, for ever.

    In memory because :class:`SignalRepository` offers no way to read a stored
    outcome back. That bounds what the recovery can promise, and the bound is
    stated rather than hidden: a pass that meets the row and holds no entry here
    - the process restarted, or another one wrote it - completes the lifecycle
    and writes nothing else. It can never contradict the record; it can leave
    the paper trade missing, and it says so.

    Entries are dropped as soon as the status flip lands, so this holds only
    scores whose pass died partway.
    """

    signal: LockedSignal
    samples: list
    outcome: Any
    #: Whether that score came from the horizon's own interval. Carried so a
    #: repair reports the sampling the record was actually made from, not the
    #: sampling a later pass would have chosen.
    coarse: bool


class SignalService:
    def __init__(
        self,
        *,
        deliberation: DeliberationService,
        market_data: MarketDataRepository,
        store: SignalRepository,
        council: Council | None = None,
        council_store: Any = None,
        model_version: str = "",
    ) -> None:
        self._deliberation = deliberation
        self._market_data = market_data
        self._store = store
        self._council = council or Council()
        self._council_store = council_store
        #: Peta keyakinan mentah -> keyakinan terbukti (bagian 9). Kosong
        #: sampai `use_history` memberinya laporan; kalibrator kosong tidak
        #: menyesuaikan apa pun, dan itu perilaku yang benar untuk sistem yang
        #: belum pernah mengukur dirinya.
        self._kalibrator = _bangun_kalibrator(None)
        self._model_version = model_version
        self._claimed: dict[str, _ClaimedScore] = {}
        #: Prediksi yang sudah dilaporkan tidak punya seri sampling. Lihat
        #: alasannya di :meth:`_resolve_one`.
        self._interval_reported: set[str] = set()

    def use_history(self, history) -> None:
        """Adopt measured SPEC 16 factors (SPEC 29, 30). See CouncilService.

        Kalibrator dibangun di sini juga, dari laporan yang sama (bagian 9).
        Sebelum ini laporan kalibrasi diukur, disimpan, diserahkan - dan angka
        keyakinan yang keluar tetap mentah. Terukur 2026-08-21: pita ≥90%
        menang 47,7% sementara pita <50% menang 55,2%.
        """
        self._council = Council(history=history)
        self._kalibrator = _bangun_kalibrator(history)

    # ---- locking --------------------------------------------------------

    async def lock_signals(
        self,
        market: Market,
        horizons: tuple[Horizon, ...],
        *,
        symbols: tuple[str, ...] | None = None,
        trading_allowed: bool = True,
        persist: bool = True,
    ) -> LockResult:
        """Run the council per horizon and freeze each verdict (SPEC 10, 20)."""
        result = LockResult()
        assets = await self._deliberation.assets_for(market, symbols)
        if not assets:
            result.failures.append(f"no enabled assets for {market.value}")
            return result

        for asset in assets:
            verdicts = {}
            for horizon in horizons:
                try:
                    context = await self._deliberation.build_context(
                        asset, market, horizon, trading_allowed=trading_allowed
                    )
                except ArunaError as exc:
                    result.failures.append(f"{asset.symbol} {horizon.value}: {exc}")
                    continue

                if context is None:
                    result.skipped += 1
                    continue

                verdict = self._council.convene(context)
                verdicts[horizon] = verdict

                # Store the debate first, so the prediction can name it. Without
                # this link a loss autopsy (SPEC 25) has the outcome and the
                # forecast but no access to the argument that produced it - and
                # the argument is the only part that can be learned from.
                session_id = None
                if persist and self._council_store is not None:
                    session_id = await self._council_store.save(asset.id, verdict)

                signal = build_signal(
                    verdict,
                    context,
                    model_version=self._model_version,
                    council_session_id=session_id,
                    # Bagian 9. Tanpa baris ini kalibrator dibangun, diuji,
                    # dan tidak pernah menyentuh satu pun angka yang sampai ke
                    # operator - keluarga cacat yang sudah berulang di repo ini.
                    kalibrator=self._kalibrator,
                )
                lockable, reason = should_lock(signal)

                # PASAL 11.13. Gerbang kualitas berjalan SESUDAH council dan
                # SEBELUM publikasi, persis di urutan yang spec minta - dan ia
                # hanya bisa menolak, tidak pernah meloloskan apa yang sudah
                # ditolak lantai confidence atau aturan bukti basi. Gerbang
                # yang bisa membatalkan penolakan lain akan jadi jalan pintas
                # untuk melewati keduanya.
                quality = await self._score_quality(
                    asset, context, verdict, signal, horizon
                )
                putusan = quality_gate(quality)
                if lockable and not putusan.passed:
                    lockable = False
                    reason = "quality gate: " + "; ".join(putusan.reasons)
                    log.info(
                        "signal.quality_blocked",
                        symbol=asset.symbol,
                        horizon=horizon.value,
                        quality=quality.score,
                        pita=_nama_pita(quality.score),
                        coverage=round(quality.coverage, 3),
                        reasons=list(putusan.reasons),
                    )

                # Bagian 18.22 dan 18.23. Keyakinan tidak boleh melampaui mutu
                # bukti yang menopangnya - dan sebelum ini tidak ada yang
                # menahannya: sinyal 95% di atas rezim berkeyakinan 42%
                # mungkin. Dipasang SESUDAH kalibrasi karena keduanya hanya
                # bisa menurunkan, dan yang terendah yang berlaku.
                #
                # Menahan, bukan membatalkan. Keyakinan yang dipotong tetap
                # keyakinan; yang membatalkan sinyal adalah gerbang mutu di
                # atas, dan hanya ia (bagian 18.43).
                signal = self._batasi_keyakinan(signal, quality, context)

                # PASAL 11.5 dan 11.6, dan hanya untuk yang berarah. Sebuah
                # WAIT tidak bisa jadi duplikat dari apa pun - ia bukan posisi,
                # tidak dikirim ke mana-mana, dan menahannya berarti menghapus
                # catatan bahwa ARUNA memang memilih diam.
                if lockable and signal.is_directional:
                    ditahan = await self._repetition_reason(
                        asset, market, horizon, signal, context
                    )
                    if ditahan:
                        lockable = False
                        reason = ditahan

                # PASAL 11.12. Kelompoknya ditentukan SEKALI di sini, dari
                # kalimat yang baru saja dipilih - bukan dihitung ulang saat
                # dibaca. Klasifikasi yang berjalan saat membaca akan berubah
                # jawabannya setiap kali daftar frasanya diperbaiki, dan
                # hitungan bulan lalu ikut berubah bersamanya.
                penahanan = (
                    None if lockable
                    else Withheld(
                        code=classify_withheld(reason),
                        reason=reason or "",
                        measured=None if quality.score is None else float(quality.score),
                        threshold=float(MIN_QUALITY),
                        # Bagian 18.41: angka yang disimpan tanpa namanya
                        # memaksa tiap pembacanya mengingat ambangnya sendiri -
                        # dan ambang itu bisa berubah, sementara baris yang
                        # sudah tersimpan tidak.
                        extra={
                            "coverage": round(quality.coverage, 3),
                            "pita": _nama_pita(quality.score),
                        },
                    )
                )

                if persist:
                    try:
                        # The publication decision is stored with the record.
                        # Recomputing it later would be impossible: the floor
                        # and the staleness rule can both change.
                        await self._store.lock(
                            asset.id,
                            signal,
                            published=lockable,
                            withheld_reason=None if lockable else reason,
                            quality=quality,
                            withheld=penahanan,
                        )
                    except ArunaError as exc:
                        result.failures.append(
                            f"{asset.symbol} {horizon.value}: {exc}"
                        )
                        continue

                result.signals.append(signal)
                if lockable:
                    result.locked += 1
                    result.published.append(signal)
                    log.info(
                        "signal.locked",
                        signal_id=signal.signal_id,
                        symbol=signal.symbol,
                        horizon=horizon.value,
                        direction=signal.direction.value,
                        confidence=round(signal.confidence, 3),
                        target=str(signal.target_price) if signal.target_price else None,
                    )
                elif signal.is_directional:
                    # A real directional call the system declined to publish.
                    #
                    # **Tingkatnya bergantung sebabnya, dan itu perbaikan atas
                    # bentuk sebelumnya.** Semuanya dulu WARNING, dengan alasan
                    # yang masuk akal: sebuah run yang menahan setiap signal
                    # karena datanya basi terlihat sama persis dengan pasar
                    # yang sepi kalau tidak ada yang mengatakannya.
                    #
                    # Tapi terukur kemudian, 765 penahanan seluruhnya WARNING -
                    # dan 359 di antaranya adalah ARUNA bekerja **persis seperti
                    # dirancang**: 238 masa tenang sesudah kalah, 121 duplikat
                    # dari prediksi yang masih terbuka. Peringatan yang isinya
                    # disiplin yang berjalan benar melatih pembacanya melewati
                    # baris WARNING - dan yang hilang berikutnya adalah 40
                    # gerbang mutu yang benar-benar menunjuk data bermasalah.
                    #
                    # Jadi: yang menunjuk **input** tetap berteriak, yang
                    # menunjuk **keputusan** dicatat sebagai keterangan.
                    # Keduanya tetap tercatat penuh, tetap masuk hitungan
                    # ``result.withheld``, dan tetap terbaca lewat ``/today``.
                    result.withheld.append((signal, reason))
                    tulis = (
                        log.warning
                        if classify_withheld(reason) in PERLU_PERHATIAN
                        else log.info
                    )
                    tulis(
                        "signal.withheld",
                        signal_id=signal.signal_id,
                        symbol=signal.symbol,
                        horizon=horizon.value,
                        direction=signal.direction.value,
                        code=classify_withheld(reason).value,
                        reason=reason,
                    )
                else:
                    # Recorded anyway: SPEC 28 needs the WAITs to judge whether
                    # standing aside was the right call.
                    result.recorded_non_directional += 1
                    log.debug("signal.recorded_wait", symbol=signal.symbol, reason=reason)

            if verdicts:
                result.views.append(build_view(asset.symbol, verdicts))
        return result

    # ---- resolution -----------------------------------------------------

    async def _repetition_reason(
        self, asset: Any, market: Market, horizon: Horizon, signal: Any, context: Any
    ) -> str | None:
        """Alasan menahan karena pengulangan, atau ``None`` (PASAL 11.5, 11.6).

        Cooldown diperiksa lebih dulu daripada duplikat, dan urutannya penting.
        Sesudah kalah, prediksi berikutnya biasanya **tidak** identik - harga
        sudah bergerak, jadi levelnya bergeser dan penjaga duplikat akan
        meloloskannya. Justru itu yang PASAL 11.5 cegah: analisis yang sama
        yang baru saja terbukti salah, diterbitkan ulang karena pasar bergerak
        sedikit.

        Kegagalan membaca database di sini **tidak** menahan signal. Penjaga
        yang berubah menjadi pembungkam saat database berkedip akan menghapus
        prediksi tanpa jejak, dan ketiadaannya tidak bisa ditemukan siapa pun.
        """
        try:
            kalah = await self._store.latest_loss(
                market=market, symbol=asset.symbol, horizon=horizon.value
            )
            terbuka = await self._store.latest_open(
                market=market, symbol=asset.symbol, horizon=horizon.value
            )
        except Exception:
            log.exception("signal.repetition_check_failed", symbol=asset.symbol)
            return None

        if kalah:
            jeda = cooldown_after_loss(
                lost_at=as_utc(kalah["exit_at"]),
                horizon_sec=horizon.duration.total_seconds(),
                loss_pct=abs(float(kalah["net_pnl_pct"] or 0)),
            )
            if jeda.active(signal.locked_at):
                boleh, kenapa = cooldown_overridden(
                    lost_direction=kalah.get("direction"),
                    candidate_direction=signal.direction.value,
                    lost_regime=kalah.get("regime"),
                    candidate_regime=signal.regime,
                )
                if boleh:
                    log.info(
                        "signal.cooldown_overridden",
                        symbol=asset.symbol, horizon=horizon.value, reason=kenapa,
                    )
                else:
                    return (
                        f"cooldown sesudah kalah sampai {isoformat(jeda.until)} "
                        f"({jeda.reason})"
                    )

        ulang = is_duplicate(
            _AsSignal(terbuka) if terbuka else None,
            _AsSignal({
                "direction": signal.direction.value,
                "reference_price": signal.reference_price,
                "target_price": signal.target_price,
            }),
        )
        if ulang.duplicate:
            return "duplikat prediksi terbuka: " + "; ".join(ulang.reasons)

        # Bagian 18.25 - 18.27. Sebuah PEMBALIKAN bukan duplikat - LONG lalu
        # SHORT punya arah yang berbeda, jadi ia lolos seluruh pemeriksaan di
        # atas. Sampai baris ini ada, urutan yang bagian 18.25 larang bisa
        # terjadi tanpa satu pun penjaga:
        #
        #     10:00 LONG  10:01 NO SIGNAL  10:02 LONG  10:03 SHORT  10:04 LONG
        #
        # Menahan pembalikannya, bukan membalik keputusannya: yang tertahan
        # menjadi NO SIGNAL, tidak menjadi "tetap LONG". Yang kedua berarti
        # memaksakan pandangan lama atas bukti yang sudah goyah, dan bagian
        # 18.43 melarang gerbang mengubah arah.
        if terbuka:
            belum = perlu_konfirmasi(
                _AsSignal(terbuka),
                signal,
                gerak_pct=_pct(
                    terbuka.get("reference_price"), signal.reference_price
                ),
            )
            if belum:
                return "; ".join(belum)
        return None

    def _note_interval_unavailable(self, signal: LockedSignal, detail: Any) -> None:
        """Katakan sekali per prediksi, bukan tiap pass.

        Kelas ini adalah "menunggu yang tidak akan pernah selesai" - itu
        definisinya. Jadi setiap prediksi yang masuk ke sini akan masuk lagi
        pada setiap pass resolusi berikutnya, selamanya. Terukur: 88 prediksi
        IDX macet, pass tiap enam puluh detik - 88 baris peringatan per menit
        tentang keadaan yang tidak berubah sejak tiga hari lalu.

        Penghitung ``unavailable_interval`` **tetap** naik pada setiap pass.
        Ia menggambarkan ukuran backlog-nya, dan meredamnya bersama barisnya
        akan menyembunyikan justru angka yang mengatakan seberapa besar
        masalahnya.

        Metode tersendiri, bukan beberapa baris di dalam ``_resolve_one``.
        Versi pertama menaruhnya di sana, dan test-nya terpaksa menyalin ulang
        cabang itu ke dalam berkas test - lalu tetap hijau ketika cabang
        aslinya dicabut. Sebuah seam yang bisa dipanggil langsung adalah
        perbedaan antara menguji kodenya dan menguji salinannya.
        """
        sudah = signal.signal_id in self._interval_reported
        self._interval_reported.add(signal.signal_id)
        pencatat = log.debug if sudah else log.warning
        pencatat(
            "signal.sampling_interval_unavailable",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            horizon=signal.horizon.value,
            # Both paths that raise this reason carry their own detail, so
            # there is nothing left for a default to cover. The one that used
            # to sit here read "no provider serves an interval this horizon can
            # be sampled from" - false for 3m since PASAL 5, and the
            # operator-visible half of that mistake.
            detail=detail,
        )

    async def votes_for(self, signal_id: str) -> Any:
        """Suara agent di balik satu prediksi, untuk pesan hasilnya.

        Diteruskan lewat sini, bukan dengan memanggil repository langsung dari
        loop upkeep. Loop itu memegang resolver, bukan penyimpanan - dan
        menjangkau ``resolver._store`` akan membuat loop bergantung pada nama
        atribut privat modul ini, yang boleh berubah kapan saja tanpa ada yang
        tahu ia dipakai dari luar.
        """
        pencari = getattr(self._store, "votes_for", None)
        return None if pencari is None else await pencari(signal_id)

    async def published_ids(self, signal_ids: Any) -> set[str]:
        """Dari sekumpulan id, mana yang prediksinya benar-benar diumumkan.

        Diteruskan lewat sini dengan alasan yang sama seperti :meth:`votes_for`:
        loop upkeep memegang resolver, bukan penyimpanan.

        **Gagal terbuka, dan itu disengaja.** Kalau pencariannya tidak
        tersedia, yang dikembalikan adalah seluruh id - artinya "anggap semua
        terpublikasi", artinya semuanya tetap dikirim. Arah kegagalan yang
        sebaliknya jauh lebih berbahaya: satu bug pencarian akan membungkam
        setiap pesan hasil, dan yang paling tidak boleh hilang dari layar
        operator adalah kabar bahwa ARUNA salah (PASAL 11.21).
        """
        pencari = getattr(self._store, "published_ids", None)
        if pencari is None:
            return {str(s) for s in signal_ids}
        return await pencari(list(signal_ids))

    async def _score_quality(
        self, asset: Any, context: Any, verdict: Any, signal: Any, horizon: Any
    ):
        """Signal Quality Score untuk satu kandidat (PASAL 11.1).

        Rekam jejak sengaja dioper sebagai belum terukur untuk sekarang.
        Sumbernya adalah ``agent_reliability``, dan tabel itu baru mulai terisi
        - papan angka dari sembilan baris bukan rekam jejak. ``historical``
        karena itu keluar dari pembagi, bukan dimasukkan sebagai nilai tengah
        yang akan menggeser tiap skor ke arah yang tidak diukur siapa pun
        (PASAL 11.16).
        """
        from aruna.notify.verdict import vote_split

        # PASAL 11.8. Diperiksa dari bar yang sama yang dipakai indikator, dan
        # ATR-nya diambil dari pembacaan yang sudah ada - bukan dihitung ulang,
        # supaya ambang anomali dan indikator berbicara tentang skala yang sama.
        # Barnya diambil dari repository, bukan dari context: DecisionContext
        # membawa indikator yang SUDAH dihitung, bukan bar mentahnya. Versi
        # pertama membaca `context.bars` - atribut yang tidak pernah ada - dan
        # tiga dari lima pemeriksaan anomali menjadi kode mati yang selalu
        # melaporkan "tidak bisa dijalankan".
        try:
            bars = await self._market_data.candles(
                asset.id, horizon, limit=ANOMALY_BASELINE_BARS
            )
        except Exception:
            log.exception("signal.anomaly_bars_failed", symbol=asset.symbol)
            bars = None

        anomalies = detect_anomalies(
            bars=bars,
            state=context.state,
            atr=context.value("atr"),
        )
        if anomalies.detected:
            log.info(
                "signal.anomaly",
                symbol=context.symbol,
                interval=getattr(context.interval, "value", context.interval),
                detail=anomalies.summary(),
            )

        return score_signal(
            context=context,
            anomalies=anomalies,
            split=vote_split(verdict.opinions, verdict.decision),
            opinions=verdict.opinions,
            entry=signal.entry_price or signal.reference_price,
            # Stop belum dihitung di jalur spot; ``reward_risk`` melaporkan
            # dirinya tidak terukur daripada mengarang jarak stop.
            stop=getattr(signal, "stop_price", None),
            target=signal.target_price,
            now=context.as_of,
            horizon_sec=horizon.duration.total_seconds(),
            accuracy=None,
            sample=0,
        )

    @staticmethod
    def _skor_risiko(context: Any) -> float | None:
        """Skor risiko 0-100 dari konteks, atau ``None`` kalau tak terukur.

        ``None`` bukan nol: risiko yang belum dinilai bukan risiko rendah, dan
        menyamakannya membuat peringatan keyakinan palsu tidak pernah menyala
        pada aset yang justru paling sedikit diketahui.
        """
        risiko = getattr(context, "risk", None)
        skor = getattr(risiko, "score", None)
        return None if skor is None else float(skor)

    @staticmethod
    def _batasi_keyakinan(signal: Any, quality: Any, context: Any) -> Any:
        """Terapkan langit-langit keyakinan (bagian 18.22 - 18.23).

        Yang diperiksa keyakinan yang **sudah terkalibrasi** - kalibrasi
        memetakan yang diklaim ke yang terbukti, dan langit-langit membatasi
        menurut bukti yang menopangnya. Keduanya hanya bisa menurunkan, jadi
        urutannya tidak mengubah hasil; yang terendah yang berlaku.

        Sinyal tak berarah dilewati: WAIT tidak mengklaim apa pun, jadi tidak
        ada yang bisa melampaui bukti.
        """
        if not getattr(signal, "is_directional", False):
            return signal

        rezim = getattr(context, "regime", None)
        putusan = periksa_keyakinan(
            float(signal.confidence),
            mutu=None if quality.score is None else float(quality.score),
            keyakinan_rezim=(
                None if rezim is None else float(getattr(rezim, "confidence", 0)) * 100
            ),
            risiko=SignalService._skor_risiko(context),
        )
        if not putusan.peringatan:
            return signal

        log.info(
            "signal.keyakinan_dibatasi",
            symbol=signal.symbol,
            semula=round(float(signal.confidence), 3),
            menjadi=putusan.keyakinan,
            peringatan=[p.value for p in putusan.peringatan],
            alasan=list(putusan.alasan),
        )
        return replace(signal, confidence=putusan.keyakinan)

    async def resolve_due(
        self,
        *,
        reference: datetime | None = None,
        limit: int = 50,
        require_fresh_candles: bool = True,
    ) -> ResolveResult:
        """Score predictions whose horizon has elapsed (SPEC 22, 23).

        ``require_fresh_candles`` is the SPEC 22 guard described at
        :data:`CANDLE_FRESHNESS_FACTOR`, and defaults to on: a live run must
        never write a mid-horizon price as a final outcome because the feed was
        behind. Turning it off is for replay and backtest, where the series is
        historical and "stale relative to now" is the normal condition rather
        than a fault.

        No production caller sets it today. That is worth stating rather than
        implying: ``tests/test_signal_resolution.py`` runs the same fixture with
        the flag both ways and asserts the two different prices it produces, so
        the paragraph above is a checked claim and not a description of a branch
        nothing has ever taken.
        """
        moment = reference or now_utc()
        result = ResolveResult()

        for signal_id in await self._store.due(reference=moment, limit=limit):
            try:
                await self._resolve_one(
                    signal_id, moment, result, require_fresh=require_fresh_candles
                )
            except ArunaError as exc:
                # One unscoreable prediction must not abandon the rest of the
                # batch: the others are due now and will not be due again.
                result.failures.append(f"{signal_id}: {exc}")

        if result.no_bars_in_window:
            # Said once per run rather than once per signal: on the database
            # this was found on it is 85 predictions, and a warning per
            # prediction per minute is a warning nobody reads. Only what was
            # measured is stated - that the window is closed and the series have
            # passed it - never *why* the market produced nothing.
            result.notes.append(
                f"{result.no_bars_in_window} prediksi tidak punya satu pun candle "
                "tersimpan di dalam horizonnya, sementara seri samplingnya sudah "
                "melewati akhir horizon itu - refresh berikutnya tidak akan "
                f"mengubah jawabannya. {result.unscoreable} di antaranya ditutup "
                "sebagai UNSCOREABLE, dengan alasan terukurnya disimpan di "
                "signals.withheld_reason. Ini BUKAN hasil: tidak ada outcome, "
                "tidak ada paper trade, tidak ada menang atau kalah."
            )
            log.warning(
                "signal.horizon_without_candles",
                signals=result.no_bars_in_window,
                detail=(
                    "no stored candle falls inside these horizons and every "
                    "sampling series that has rows already runs past the end of "
                    "them, so no later refresh can change the answer; due() "
                    "orders by resolves_at, so they are re-read at the head of "
                    "the queue on every pass"
                ),
            )

        if result.unavailable_interval:
            # Dikatakan, bukan hanya dihitung. Menutup prediksi adalah tindakan
            # atas catatan, dan tindakan atas catatan harus terbaca operator
            # tanpa ia perlu menanyakannya (PASAL 6).
            result.notes.append(
                f"{result.unavailable_interval} prediksi tertahan oleh seri "
                "sampling yang tidak dijaga refresh mana pun - tidak ada "
                "penyegaran berikutnya yang bisa mengubah jawabannya, jadi "
                "menunggu lebih lama tidak menghasilkan apa-apa. Ini BUKAN "
                "hasil: tidak ada outcome, tidak ada paper trade, tidak ada "
                "menang atau kalah, dan win rate tidak bergerak satu angka pun."
            )
        return result

    async def _resolve_one(
        self,
        signal_id: str,
        moment: datetime,
        result: ResolveResult,
        *,
        require_fresh: bool = True,
    ) -> None:
        record = await self._store.get(signal_id)
        if record is None:
            result.failures.append(f"{signal_id}: snapshot missing")
            return

        signal, fingerprint = record
        try:
            # Refuse to score a record that has been altered (SPEC 20).
            verify_integrity(signal, fingerprint)
        except ArunaError as exc:
            result.failures.append(str(exc))
            await self._store.set_status(signal_id, SignalStatus.INVALIDATED)
            return

        ready, _ = is_resolvable(signal, reference=moment)
        if not ready:
            # `due()` filters on the same clock, so this is belt and braces -
            # but the two must never disagree silently about what is scoreable.
            result.not_due += 1
            return
        if signal.market is Market.IDX and IDX_CLOCK_CAVEAT not in result.notes:
            result.notes.append(IDX_CLOCK_CAVEAT)

        claimed = self._claimed.get(signal_id)
        if claimed is not None:
            # This process already scored this prediction and was interrupted
            # before the lifecycle caught up. Finish that score. Reading the
            # candles again first would be worse than pointless: the refresher
            # has moved them, so the second reading is a different answer, and
            # the stored one is the record (SPEC 22).
            await self._complete(claimed, result)
            return

        window = await self._prices_during(
            signal, moment=moment, require_fresh=require_fresh
        )
        if not window.prices:
            # Nothing is written on any of these paths - no outcome, no samples,
            # no status change, no paper trade. The signal stays LOCKED and is
            # picked up again next pass, because a prediction scored from data
            # that had not arrived cannot be corrected afterwards (SPEC 22).
            if window.blocked_by == "stale_candles":
                result.awaiting_candles += 1
                log.info(
                    "signal.awaiting_candles",
                    signal_id=signal_id,
                    symbol=signal.symbol,
                    horizon=signal.horizon.value,
                    detail="sampling series is behind; left LOCKED for a later pass",
                )
            elif window.blocked_by == "interval_unavailable":
                result.unavailable_interval += 1
                self._note_interval_unavailable(signal, window.detail)
                # Ditutup, bukan dibiarkan menggantung selamanya.
                #
                # **Buktinya lebih lemah daripada cabang di bawah, dan itu
                # dinyatakan.** Cabang `no_bars_in_window` bersandar pada sifat
                # data: bar ditambahkan maju ke depan, jendelanya sudah tertutup
                # dan berada di masa lalu, jadi tidak ada data masa depan yang
                # bisa mendarat di dalamnya. Tidak ada konfigurasi yang bisa
                # mengubah itu.
                #
                # Cabang ini bersandar pada konfigurasi: seri yang menahannya
                # tidak dijaga refresh mana pun *hari ini*. Seorang operator
                # yang memperluas set refresh besok akan membuatnya bisa diskor
                # - tapi prediksinya sudah ditutup dan `due()` hanya menawarkan
                # yang berstatus LOCKED, jadi ia tidak akan pernah ditawarkan
                # lagi. Menutupnya berarti menukar backlog yang tidak pernah
                # habis dengan perbaikan yang tidak bisa dibatalkan.
                #
                # Pertukaran itu diambil karena sisi lainnya lebih buruk: 88
                # prediksi duduk di kepala antrean pada setiap pass sejak tiga
                # hari lalu, mendorong prediksi yang benar-benar bisa diskor ke
                # belakang, dan tidak ada yang akan memperbaiki konfigurasi itu
                # justru karena keluhannya tenggelam.
                #
                # Terukur: 88 prediksi IDX terkunci sejak 15 Agustus, dan pada
                # setiap pass resolusi berikutnya ia akan berada di kepala
                # antrean lagi. `due()` mengurutkan dengan `resolves_at`, jadi
                # yang tertua duduk paling depan - selamanya, mendorong
                # prediksi yang benar-benar bisa diskor ke belakang.
                #
                # Yang TIDAK terjadi di sini, dan ini yang penting: tidak ada
                # outcome ditulis, tidak ada paper trade, tidak ada menang dan
                # tidak ada kalah. Win rate tidak bergerak satu angka pun.
                # Prediksinya tidak diubah - entry, stop, target, confidence
                # semuanya tetap seperti saat dikunci (PASAL 11.21). Yang
                # berubah hanya status daur hidupnya, dari "menunggu" menjadi
                # "tidak bisa dijawab", dengan alasan terukurnya disimpan di
                # `signals.withheld_reason` supaya keputusan ini bisa diaudit
                # alih-alih dipercaya begitu saja.
                await self._close_as_unscoreable(signal, window.detail or "", result)
            else:
                result.no_prices += 1
                if window.detail is not None:
                    # Measured, not inferred: every series that has rows runs
                    # past the end of this horizon and none of them has a candle
                    # inside it. Reported once per run in `resolve_due`.
                    result.no_bars_in_window += 1
                    await self._close_as_unscoreable(signal, window.detail, result)
            return
        prices = window.prices
        # Counted only once the writes land, below. A pass that fails partway
        # comes back next time, and tallying it here would report the same
        # signal as coarsely sampled on every attempt - a count of scorings that
        # did not happen.
        coarse = window.interval is signal.horizon

        samples = build_samples(signal, prices)
        if not samples:
            result.no_prices += 1
            return

        outcome = resolve(signal, samples, resolved_at=moment)

        # Four writes, no transaction available. `SignalRepository` exposes no
        # connection-scoped variants, so `Database.transaction()` cannot wrap
        # these; the order is therefore the only guarantee on offer.
        #
        # `record_outcome` goes first and nothing else is written unless it
        # succeeds, because it is the only one of the four that can *fail on
        # purpose*: paper_results carries a unique key and the statement is a
        # plain INSERT, so it doubles as the claim on this prediction. The other
        # three all upsert, and a pass that ran them before the claim would have
        # rewritten them from a fresh reading of the candles before finding out
        # that this prediction was already scored - which is precisely how
        # paper_results said 1,023 while paper_trades said 2,023 for the same
        # signal, permanently, with nothing in either row to show it.
        #
        # Everything after the claim is finished by `_complete`, and a pass that
        # dies partway is finished by a later one through `_claimed`. The state
        # that must never be reached is RESOLVED with no paper trade, because
        # `due()` only ever returns LOCKED rows - so the status flip stays last.
        # Getting *that* wrong is why paper_trades held one row against 31
        # resolutions.
        if not await self._record_outcome_once(signal, outcome):
            # Scored by a run this process cannot see - it restarted, or another
            # one wrote the row. The score cannot be read back, so the honest
            # act is to write nothing and let the lifecycle catch up: anything
            # else would be numbers invented next to a record that says
            # otherwise. `outcome` is deliberately not added to `result.outcomes`
            # - it was computed here and never stored.
            await self._store.set_status(
                signal_id, SignalStatus.RESOLVED, resolved_at=moment
            )
            result.resolved += 1
            return

        claimed = _ClaimedScore(
            signal=signal, samples=samples, outcome=outcome, coarse=coarse
        )
        self._claimed[signal_id] = claimed
        await self._complete(claimed, result)

    async def _close_as_unscoreable(
        self, signal: LockedSignal, reason: str, result: ResolveResult
    ) -> None:
        """Close a prediction no data can ever answer (SPEC 4, 22).

        The condition is a proof, not a timeout: the caller has already
        established that every sampling series carrying rows runs past the end
        of this horizon and that not one bar falls inside it, so the window is
        shut and in the past. Nothing a refresh does can put a bar there.

        Left LOCKED instead, these do real damage rather than merely looking
        untidy. ``due()`` orders by ``resolves_at``, so the oldest unscoreable
        predictions sit permanently at the *head* of the queue, are re-read
        every pass, and hold up everything behind them - and they are counted
        as ``awaiting_candles``, which makes health promise a backlog will
        drain that never will.

        **No outcome is written.** No samples, no paper result, no paper trade,
        no win and no loss - scoring it either way would be inventing a number
        (SPEC 4). The measured reason is stored on the row so the decision can
        be audited rather than taken on trust.

        A failure here is deliberately not fatal to the batch: the prediction
        simply stays LOCKED and is offered again, which is the state it was
        already in.
        """
        try:
            await self._store.set_status(
                signal.signal_id,
                SignalStatus.UNSCOREABLE,
                resolved_at=None,
                withheld_reason=reason[:255],
            )
        except ArunaError as exc:
            result.failures.append(f"{signal.signal_id}: {exc}")
            return

        result.unscoreable += 1
        log.warning(
            "signal.unscoreable",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            horizon=signal.horizon.value,
            locked_at=isoformat(signal.locked_at),
            resolves_at=isoformat(signal.resolves_at),
            detail=reason,
        )

    async def _complete(self, claimed: _ClaimedScore, result: ResolveResult) -> None:
        """Write the rest of a claimed score and advance the lifecycle.

        Split out of :meth:`_resolve_one` so a repair pass runs *this* and only
        this. Every statement here is an upsert or a lifecycle flip, so running
        it again with the same claimed score is a no-op at the database - which
        is what makes "come back next pass" a real repair rather than a second
        opinion.

        ``resolved_at`` is taken from the score, not from the pass doing the
        writing: ``signals.resolved_at`` and ``paper_results.resolved_at``
        describe the same event, and a repair three minutes later must not make
        them disagree.
        """
        signal = claimed.signal
        await self._store.record_samples(claimed.samples)
        hasil_trade = None
        if signal.is_directional:
            hasil_trade = await self._simulate_trade(signal, claimed.outcome)
        await self._store.set_status(
            signal.signal_id,
            SignalStatus.RESOLVED,
            resolved_at=claimed.outcome.resolved_at,
        )
        # Only now: while this entry stands, a later pass finishes this score
        # instead of computing a new one.
        self._claimed.pop(signal.signal_id, None)

        result.outcomes.append(claimed.outcome)
        result.scored.append((signal, claimed.outcome))
        if hasil_trade is not None:
            # Terpisah dari `scored`, bukan dijadikan tuple bertiga: bentuk
            # `scored` sudah dibaca di beberapa tempat, dan melebarkannya akan
            # memaksa setiap pembaca ikut berubah untuk satu keterangan yang
            # tidak selalu ada.
            result.trades[signal.signal_id] = hasil_trade.result.value
            # Kotor, biaya, bersih - bertiga, bukan hanya putusannya.
            #
            # Tanpa angka ini pesan hasil bisa berbunyi "arahnya benar, target
            # tidak tercapai" tepat di sebelah kata LOSS, dan pembacanya tidak
            # punya cara membedakan sistem yang salah dari sistem yang benar
            # tapi geraknya lebih kecil dari ongkos bolak-baliknya. Terukur pada
            # DOT/USDT: masuk 0,747 keluar 0,749 - naik, arahnya benar - kotor
            # +2,68, biaya 3,67, bersih -0,99.
            result.economics[signal.signal_id] = (
                hasil_trade.gross_pnl,
                hasil_trade.total_costs,
                hasil_trade.net_pnl,
            )
        result.resolved += 1
        if claimed.coarse:
            result.coarsely_sampled += 1

    async def _record_outcome_once(self, signal: LockedSignal, outcome) -> bool:
        """Claim this prediction. True when the claim was won, False when taken.

        ``paper_results`` carries ``UNIQUE KEY paper_results_signal`` and
        :meth:`SignalRepository.record_outcome` is a plain INSERT - deliberately
        so, because SPEC 22 forbids re-scoring a prediction. The consequence is
        that a pass killed between this write and the status flip leaves a row
        behind for a signal that is still LOCKED, and every later pass then
        collides with it. Left fatal, that signal never resolves *and* blocks
        the queue: ``due()`` orders by ``resolves_at``, so the oldest casualty
        sits at the head of it forever.

        Returning False rather than raising is what turns that collision into a
        repair - but the caller has to honour it. Writing the samples and the
        paper trade anyway, from an outcome computed *this* pass, keeps SPEC 22
        for the one row that refuses to be overwritten and breaks it for the two
        that accept an upsert; the numbers then contradict each other with
        nothing to say which is the record.
        """
        try:
            await self._store.record_outcome(outcome)
        except ArunaError as exc:
            if not _already_scored(exc):
                raise
            log.warning(
                "signal.outcome_already_recorded",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                detail=(
                    "paper_results already holds this signal; an earlier pass "
                    "was interrupted before the status flip. The outcome just "
                    "computed is discarded - the stored score is the record "
                    "(SPEC 22) - and the lifecycle is completed without writing "
                    "samples or a paper trade, which this run cannot reproduce "
                    "from the stored row. Check paper_trades for this signal"
                ),
            )
            return False
        return True

    async def _prices_during(
        self, signal: LockedSignal, *, moment: datetime, require_fresh: bool = True
    ) -> PriceWindow:
        """Closed-bar closes within the horizon, and the interval they came from.

        Uses stored candles rather than a live quote: the outcome must be
        computed from what the market did during the horizon, not from whatever
        the price happens to be now.

        Tries progressively finer intervals because the horizon's own interval
        yields a single observation - see :func:`sampling_intervals`. Returns
        the interval actually used so the caller can report when it had to
        settle for an endpoint instead of a path.

        **The freshness test is on the series, not on the window.** The
        intuitive rule - "the last observation must reach ~90% of the horizon" -
        blocks IDX forever: an IDX 1d signal locked Friday 10:00 WIB is due
        Saturday 10:00, and the last 1h bar that will ever exist is Friday
        16:00, a quarter of the way in. Coverage would hold it permanently.
        Freshness holds it until Monday and then scores it from Friday's data,
        which is correct - the exchange was shut, and :data:`IDX_CLOCK_CAVEAT`
        already says the wall clock is not discounted for that.

        **A stale candidate disables the endpoint fallback.** Freshness is
        judged per series, so a stale 1h does not stop a fresh 15m from being
        used - that is still a path. But the last candidate is the horizon's own
        interval, and it is coarse enough to pass its own freshness test while
        every finer series is hours behind: a 1d bar only has to be within three
        *days*. Accepting it there would turn "the data has not arrived" into a
        single close written as the final price and the paper trade's exit -
        permanently, because SPEC 22 forbids editing a scored prediction. That
        is precisely the damage :data:`CANDLE_FRESHNESS_FACTOR` exists to
        prevent, so the fallback is taken only when a series that is *being kept
        current* is behind - because only that is a wait with an end.

        The measured case, on the day this was found: IDX 1h was 56 hours
        behind and 15m 57 hours, while 1d was only 40 hours behind and so
        "fresh" - and 53 of the due signals were 1d.

        **Only a maintained series may hold anything back.** "Behind and being
        caught up" and "behind and nobody's job" look identical in a timestamp
        and mean opposite things. ``sampling_intervals`` reaches four candidates
        deep; :func:`maintained_intervals` covers what the refresher keeps
        current, and the two are not the same list. IDX is the measured case:
        1m is the third candidate for a 1d horizon and the *first* for a 15m
        one, 5,500 IDX 1m rows sit in the database, and IDX's refresh set is
        (15m, 1h, 1d) - so that series is frozen for good. Letting it count as
        stale held every affected IDX prediction LOCKED when re-tested a day, a
        week and a month later, and counted the wait under ``awaiting_candles``,
        which promises the backlog drains. A candidate outside the maintained
        set is therefore recorded apart and reported as ``interval_unavailable``
        - the counter that means an operator has to act - and never blocks the
        endpoint fallback.

        **A series with no rows at all is not a hold-back, deliberately.** It is
        skipped before either list, so an asset whose 1d bars are stored while
        its finer intervals are still empty is scored from the endpoint rather
        than held. That is not the same judgement as "behind": a series that
        exists and lags is demonstrably being written and will catch up, while
        one that has never produced a row for this asset offers no evidence that
        it ever will, and holding out for it is the wait with no end again.
        ``CandleFreshnessCheck`` calls a wholly absent series a fault in its own
        right, so the operator is told; resolution does not let that fault
        postpone every score behind it as well.

        **A series that already reaches ``resolves_at`` is never too stale.**
        Staleness is a proxy for "the bars this signal needs have not arrived",
        and once the newest stored bar is at or past the end of the horizon they
        all have: the window is closed and in the past, so no later refresh can
        add anything inside it. Judging such a series against ``moment`` instead
        would hold a scoreable prediction back waiting for data that would never
        change its answer - the same wait-that-never-ends the
        ``interval_unavailable`` counter exists to keep separate.

        That last rule is also what makes the empty-window case *measurable*:
        when every series carrying rows runs past the end of the horizon and not
        one candle falls inside it, nothing a refresh can do will change the
        answer. Said in ``detail`` rather than left as a bare zero (SPEC 4, 49).
        """
        asset = await self._deliberation.find_asset(signal.market, signal.symbol)
        if asset is None:
            return PriceWindow([], None, None)

        maintained = maintained_intervals(signal.market)
        stale: list[Horizon] = []
        unmaintained: list[Horizon] = []
        with_rows = 0
        past_the_end = 0
        saw_a_price = False
        candidates = sampling_intervals(signal.horizon)
        for interval in candidates:
            rows = await self._market_data.candles(
                asset.id, interval, limit=SAMPLE_WINDOW, closed_only=True
            )
            if not rows:
                continue
            with_rows += 1
            # candles() returns oldest-first, so the newest bar is the last row.
            newest = rows[-1]["close_time"]
            cutoff = moment - CANDLE_FRESHNESS_FACTOR * interval.duration
            covers_horizon = newest >= signal.resolves_at
            if covers_horizon:
                past_the_end += 1
            if require_fresh and newest < cutoff and not covers_horizon:
                behind = stale if interval in maintained else unmaintained
                behind.append(interval)
                continue
            prices = [
                (row["close_time"], row["close"])
                for row in rows
                if signal.locked_at <= row["close_time"] <= signal.resolves_at
            ]
            if len(prices) >= MIN_OBSERVATIONS:
                return PriceWindow(prices, interval, None)
            if prices:
                saw_a_price = True
                if interval is signal.horizon and not stale:
                    # The endpoint fallback - see the note above for why `not
                    # stale` is load-bearing rather than defensive, and why
                    # `unmaintained` deliberately does not appear here.
                    return PriceWindow(prices, interval, None)

        if stale:
            return PriceWindow([], None, "stale_candles")
        if unmaintained:
            return PriceWindow(
                [],
                None,
                "interval_unavailable",
                "the only sampling series still behind is one no refresh set "
                f"covers: {', '.join(i.value for i in unmaintained)} for "
                f"{signal.market.value}",
            )
        if not any(interval in STORED_INTERVALS for interval in candidates):
            # "The data has not arrived" and "the data will never arrive"
            # deserve different counters, and this is the second one.
            #
            # A 3m horizon is the whole of it today: no stored interval is fine
            # enough to yield MIN_OBSERVATIONS inside three minutes (1m gives
            # three), so sampling_intervals(M3) falls back to (M3,) alone, and
            # 3m is not in STORED_INTERVALS. The venue is not what blocks it -
            # binance-spot maps M3 to "3m" and serves it - so the reason
            # reported has to be the storage decision, which is the one that
            # is true and also the one somebody can change. Saying "no provider
            # serves 3m" would send the next reader to the adapter to fix
            # something that is not broken there (SPEC 49).
            return PriceWindow(
                [],
                None,
                "interval_unavailable",
                "no sampling series for this horizon is stored: "
                f"{', '.join(i.value for i in candidates)} against "
                f"{', '.join(i.value for i in STORED_INTERVALS)} in "
                "STORED_INTERVALS - a storage decision, not a venue limit",
            )
        detail = None
        if with_rows and with_rows == past_the_end and not saw_a_price:
            detail = (
                "no stored candle falls inside this horizon and every sampling "
                "series that has rows already runs past the end of it"
            )
        return PriceWindow([], None, None, detail)

    async def _simulate_trade(self, signal: LockedSignal, outcome) -> Any:
        """Open and close a paper position over the signal's life (SPEC 34).

        Two failures, told apart on purpose.

        A trade that cannot be **modelled** - a non-positive entry price, say -
        will not model any better next pass. Retrying it forever would park an
        unscoreable prediction at the head of ``due()``, so it is logged and the
        resolution continues without a trade.

        A trade that cannot be **written** is a storage fault, and it is
        re-raised. :meth:`_resolve_one` then stops before the status flip, and
        the signal comes back next pass; ``record_trade`` upserts, so the retry
        is safe. Swallowing this - which is what the single ``except (ValueError,
        ArunaError)`` here used to do - marked the signal RESOLVED and lost the
        trade permanently, because ``due()`` never offers a RESOLVED signal
        again.
        """
        try:
            trade = open_trade(
                signal,
                capital=default_capital(signal.market),
                opened_at=signal.locked_at,
            )
            distance = None
            if signal.target_price:
                distance = abs(signal.target_price - signal.entry_price)
            closed = close_trade(
                trade,
                outcome.final_price,
                closed_at=outcome.resolved_at,
                target_distance=distance,
            )
        except ValueError as exc:
            log.warning(
                "paper.simulation_failed", signal_id=signal.signal_id, error=str(exc)
            )
            return None

        await self._store.record_trade(closed)
        log.info(
            "paper.closed",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            result=closed.result.value,
            net_pnl=str(closed.net_pnl),
            costs=str(closed.total_costs),
        )
        # Dikembalikan, tidak hanya dicatat. Ini satu-satunya tempat di mana
        # menang dan kalah benar-benar diputuskan - dari harga masuk, harga
        # keluar, dan ongkosnya - dan sampai baris ini ada, angka itu ditulis ke
        # database lalu dibuang. Pesan hasil karena itu menebak dari kelas
        # outcome, yang tidak pernah bisa mengatakan LOSS, dan setiap kekalahan
        # terkirim sebagai 🟡 dengan kalimat sementara setiap kemenangan
        # terkirim sebagai 🟢 WIN.
        return closed


__all__ = [
    "CANDLE_FRESHNESS_FACTOR",
    "DEFAULT_CAPITAL",
    "LockResult",
    "PriceWindow",
    "ResolveResult",
    "SignalService",
    "maintained_intervals",
]
