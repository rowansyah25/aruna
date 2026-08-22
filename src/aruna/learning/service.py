"""Learning service: read the record, report what it says (PHASE 8).

One pass over resolved predictions produces every SPEC 25-30 finding, because
they all read the same rows and splitting them would mean four scans and four
chances for the snapshots to disagree with each other.

Nothing here writes to a prediction, and nothing here changes a weight during
the run. :class:`MeasuredHistory` is built once at the end and handed to the
*next* council. A system that adjusted its agents mid-batch would make the
decisions in that batch incomparable, and SPEC 39's replay guarantee would be
gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from aruna.core.enums import Decision, Horizon, Market
from aruna.core.logging import get_logger
from aruna.db.repositories.learning import LearningRepository
from aruna.learning.autopsy import (
    Autopsy,
    ObjectionRecord,
    perform_autopsy,
    successful_objections,
)
from aruna.learning.calibration import CalibrationReport, calibrate
from aruna.learning.counterfactual import (
    Counterfactual,
    GhostSignal,
    counterfactual,
    ghost_signal,
    reclassify_with_lookahead,
    summarise_ghosts,
)
from aruna.learning.history import (
    MeasuredHistory,
    empty_history,
    history_from_snapshots,
)
from aruna.learning.reliability import ReliabilityReport, build_reliability
from aruna.signals.models import LockedSignal, OutcomeClass, SignalStatus

log = get_logger("aruna.learning")

#: Bars to read when checking whether a loss was an early call. The look-ahead
#: itself is bounded to one horizon; this is just the query window.
LOOKAHEAD_BARS = 100


@dataclass(slots=True)
class LearningResult:
    reviewed: int = 0
    autopsies: list[Autopsy] = field(default_factory=list)
    counterfactuals: list[Counterfactual] = field(default_factory=list)
    ghosts: list[GhostSignal] = field(default_factory=list)
    objections: list[ObjectionRecord] = field(default_factory=list)
    calibration: CalibrationReport | None = None
    reliability: ReliabilityReport | None = None
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"reviewed={self.reviewed}",
            f"autopsies={len(self.autopsies)}",
            f"ghosts={len(self.ghosts)}",
        ]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewed": self.reviewed,
            "autopsies": [a.to_dict() for a in self.autopsies],
            "counterfactuals": [c.to_dict() for c in self.counterfactuals],
            "ghost_signals": summarise_ghosts(self.ghosts),
            "successful_objections": [o.to_dict() for o in self.objections],
            "calibration": (
                self.calibration.to_dict() if self.calibration else None
            ),
            "reliability": (
                self.reliability.to_dict() if self.reliability else None
            ),
        }


class LearningService:
    def __init__(
        self,
        *,
        store: LearningRepository,
        market_data: Any = None,
        universe: Any = None,
    ) -> None:
        self._store = store
        # Optional, and only used for the SPEC 27 look-ahead. Without them the
        # review still runs; it simply cannot tell a call that was early from
        # one that was wrong, and says so rather than guessing.
        self._market_data = market_data
        self._universe = universe

    async def review(
        self, *, limit: int = 500, persist: bool = True
    ) -> LearningResult:
        """Analyse every resolved prediction (SPEC 25-30)."""
        result = LearningResult()
        records = await self._store.resolved(limit=limit)
        result.reviewed = len(records)

        for record in records:
            try:
                await self._review_one(record, result, persist=persist)
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the pass
                result.failures.append(f"{record.get('signal_id')}: {exc}")

        result.objections = successful_objections(
            await self._store.overruled_objections()
        )
        # Calibration measures the claims ARUNA published. A verdict the lock
        # declined to stand behind was never a claim, and scoring it would
        # measure the system against something it explicitly refused to say.
        result.calibration = calibrate(_klaim_terkalibrasi(records))
        result.reliability = build_reliability(await self._store.agent_outcomes())

        if persist and result.reviewed:
            # No snapshot when there is nothing to measure. These tables are
            # append-only, so a run that recorded "0 resolved" every time would
            # bury the real trend under rows that say nothing.
            await self._store.record_calibration(result.calibration)
            await self._store.record_reliability(result.reliability)

        log.info(
            "learning.reviewed",
            reviewed=result.reviewed,
            autopsies=len(result.autopsies),
            ghosts=len(result.ghosts),
            calibration=result.calibration.verdict,
        )
        return result

    async def _review_one(
        self, record: dict[str, Any], result: LearningResult, *, persist: bool
    ) -> None:
        signal = _to_signal(record)

        if signal.is_directional:
            session_id = record.get("council_session_id")
            context = (
                await self._store.council_context(session_id) if session_id else {}
            )
            autopsy = perform_autopsy({**record, **context})
            if autopsy is not None:
                autopsy = await self._add_lookahead(signal, autopsy)
                result.autopsies.append(autopsy)
                if persist:
                    await self._store.record_autopsy(autopsy)

            alternative = counterfactual(signal, record["final_price"])
            if alternative is not None:
                result.counterfactuals.append(alternative)
                if persist:
                    await self._store.record_counterfactual(alternative)
        else:
            ghost = ghost_signal(
                signal,
                float(record.get("max_favourable_pct") or 0),
                float(record.get("max_adverse_pct") or 0),
            )
            if ghost is not None:
                result.ghosts.append(ghost)
                if persist:
                    await self._store.record_ghost(ghost)

    async def _add_lookahead(
        self, signal: LockedSignal, autopsy: Autopsy
    ) -> Autopsy:
        """Note when a loss was actually an early call (SPEC 23, 27).

        Reads prices from *after* the horizon, which the scoring path is
        forbidden to do. The recorded outcome is left exactly as it was earned;
        this only adds a finding. Letting a later move upgrade a past loss would
        be marking one's own homework with the answers in hand.
        """
        if self._market_data is None or self._universe is None:
            return autopsy
        if autopsy.outcome_class is not OutcomeClass.WRONG_FROM_START:
            return autopsy

        asset = await self._universe.find(signal.market, signal.symbol)
        if asset is None:
            return autopsy

        rows = await self._market_data.candles(
            asset.id, signal.horizon, limit=LOOKAHEAD_BARS, closed_only=True
        )
        after = [
            (row["close_time"], row["close"])
            for row in rows
            if row["close_time"] > signal.resolves_at
        ]
        if not after:
            return autopsy

        _, note = reclassify_with_lookahead(signal, autopsy.outcome_class, after)
        if note is None:
            return autopsy
        return replace(autopsy, findings=(*autopsy.findings, note))

    async def history_as_of(self, moment: datetime) -> MeasuredHistory:
        """The SPEC 16 factors as they stood at ``moment`` (SPEC 39).

        Replay needs the weights a past decision was actually judged under.
        Falls back to an empty history when nothing had been measured yet,
        which is the correct answer for every decision made before PHASE 8.
        """
        rows = await self._store.reliability_as_of(moment)
        calibration = await self._store.calibration_as_of(moment)
        if not rows and calibration is None:
            return empty_history()
        return history_from_snapshots(rows, calibration)

    async def measured_history(self) -> MeasuredHistory:
        """The SPEC 16 factors as they stand now.

        Handed to the council for the *next* run. Both halves answer ``None``
        until their sample thresholds are met, which keeps the judge neutral and
        keeps the factors declared unavailable on every stored decision.
        """
        return MeasuredHistory(
            reliability_report=build_reliability(await self._store.agent_outcomes()),
            calibration_report=calibrate(
                _klaim_terkalibrasi(
                    await self._store.resolved(limit=SAMPEL_KALIBRASI)
                )
            ),
        )


#: Berapa prediksi terselesaikan yang dibaca sebelum kalibrasi dihitung.
#:
#: Terukur 2026-08-21, dan angkanya menentukan apakah kalibrasi berarti sama
#: sekali. Pada 500, hanya 67 baris lolos saringan di bawah dan **tiga dari
#: empat pita kekurangan sampel** - kalibrasi yang hanya mengukur satu pita di
#: atas 24 pengamatan. Pada 5.000, 777 baris lolos dan keempat pita terukur.
#:
#: Batasnya ada karena `resolved` mengurut `locked_at DESC`: yang dipotong
#: selalu yang paling lama, dan itu bias kebaruan yang harus disengaja, bukan
#: didapat.
SAMPEL_KALIBRASI = 5000


def _klaim_terkalibrasi(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prediksi yang layak masuk pengukuran kalibrasi (SPEC 29).

    Kalibrasi mengukur **klaim yang ARUNA terbitkan**. Putusan yang lock-nya
    tolak bukan klaim, dan menilainya berarti mengukur sistem terhadap sesuatu
    yang justru ia menolak mengatakannya. Keputusan tanpa arah juga tidak punya
    sisi untuk benar atau salah.

    **Satu tempat, bukan dua.** Saringan ini pernah hidup dua kali dengan isi
    yang berbeda: :meth:`LearningService.review` menyaring `published`,
    :meth:`LearningService.measured_history` tidak - sehingga laporan yang
    DILAPORKAN ke operator dan laporan yang DITERAPKAN ke keputusan mengukur
    populasi yang berbeda, dan yang salah justru yang menggerakkan keputusan.
    Terukur 2026-08-21: perbedaannya memetakan keyakinan 46% menjadi 19% alih-
    alih 53%.
    """
    return [
        r
        for r in records
        if r["direction"] in ("BUY", "SELL") and r.get("published", True)
    ]


def _to_signal(record: dict[str, Any]) -> LockedSignal:
    """Enough of the locked prediction for the analyses to read.

    Rebuilt rather than refetched: these rows already carry every field the
    SPEC 25-28 functions touch, and a second query per signal would turn one
    pass into hundreds.
    """
    return LockedSignal(
        signal_id=record["signal_id"],
        market=Market(record["market_code"]),
        symbol=record["symbol"],
        horizon=Horizon(record["horizon_code"]),
        direction=Decision(record["direction"]),
        confidence=float(record["confidence"]),
        reference_price=record["reference_price"],
        entry_price=record["entry_price"],
        target_price=record["target_price"],
        expected_move_pct=(
            float(record["expected_move_pct"])
            if record["expected_move_pct"] is not None
            else None
        ),
        locked_at=record["locked_at"],
        as_of=record["as_of"],
        resolves_at=record["resolves_at"],
        reasoning=tuple(record.get("reasoning") or ()),
        regime=record.get("regime"),
        news_state=record.get("news_state"),
        risk_level=record.get("risk_level"),
        status=SignalStatus.RESOLVED,
    )


__all__ = ["LearningResult", "LearningService"]
