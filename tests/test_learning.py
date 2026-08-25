"""Learning from outcomes (PHASE 8).

The load-bearing tests here are the ones about *refusing to answer*. Calibration
and reliability are the two places where a system is most tempted to report a
number it has not earned, and where doing so is most damaging: a calibration
curve drawn through four observations looks exactly like one drawn through four
hundred, and the judge would start moving real weight on the strength of it.

So the tests that must never be relaxed are these: an empty history reports
nothing; a small sample reports INSUFFICIENT_SAMPLE rather than a figure; and a
`None` from either provider leaves the judge neutral with the factor still
declared unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.enums import (
    AgentRole,
    Decision,
    Horizon,
    Market,
    Stance,
    VetoReason,
    VetoReviewOutcome,
)
from aruna.learning.autopsy import (
    LOSING_CLASSES,
    perform_autopsy,
    successful_objections,
)
from aruna.learning.calibration import (
    MIN_BUCKET_SAMPLE,
    MIN_TOTAL_SAMPLE,
    calibrate,
)
from aruna.learning.counterfactual import (
    GHOST_THRESHOLD_PCT,
    counterfactual,
    ghost_signal,
    reclassify_with_lookahead,
    summarise_ghosts,
)
from aruna.learning.history import MeasuredHistory, empty_history
from aruna.learning.reliability import (
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    MIN_RELIABILITY_SAMPLE,
    build_reliability,
)
from aruna.signals.models import LockedSignal, OutcomeClass

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _signal(**overrides) -> LockedSignal:
    base = {
        "signal_id": "learn00000000001",
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "horizon": Horizon.H1,
        "direction": Decision.BUY,
        "confidence": 0.7,
        "reference_price": Decimal(1000),
        "entry_price": Decimal(1000),
        "target_price": Decimal(1050),
        "expected_move_pct": 5.0,
        "locked_at": NOW,
        "as_of": NOW - timedelta(minutes=1),
        "resolves_at": NOW + timedelta(hours=1),
        "reasoning": ("structure was mixed", "regime uncertain"),
    }
    return LockedSignal(**(base | overrides))


def _resolved(confidence: float, correct: bool) -> dict:
    return {"confidence": confidence, "direction_correct": correct}


# ---------------------------------------------------------------------------
# SPEC 29 - calibration
# ---------------------------------------------------------------------------


class TestCalibration:
    def test_an_empty_record_reports_no_accuracy(self) -> None:
        report = calibrate([])
        assert report.total == 0
        assert all(b.accuracy is None for b in report.buckets)
        assert "INSUFFICIENT SAMPLE" in report.verdict

    def test_a_small_sample_refuses_to_report_accuracy(self) -> None:
        """Four predictions at 3/4 is not "75% accurate". It is nothing."""
        report = calibrate([_resolved(0.7, True)] * 3 + [_resolved(0.7, False)])
        bucket = next(b for b in report.buckets if b.low == 0.65)

        assert bucket.predictions == 4
        assert bucket.accuracy is None
        assert bucket.sufficient is False
        assert bucket.to_dict()["status"] == "INSUFFICIENT_SAMPLE"
        assert bucket.to_dict()["needs"] == MIN_BUCKET_SAMPLE - 4

    def test_accuracy_appears_once_the_bucket_is_full(self) -> None:
        records = [_resolved(0.7, True)] * 15 + [_resolved(0.7, False)] * 5
        report = calibrate(records)
        bucket = next(b for b in report.buckets if b.low == 0.65)

        assert bucket.predictions == MIN_BUCKET_SAMPLE
        assert bucket.accuracy == 0.75
        assert bucket.gap == pytest.approx(-0.05, abs=1e-9)

    def test_overconfidence_is_named(self) -> None:
        # Says 90%, delivers 40%.
        records = [_resolved(0.9, True)] * 20 + [_resolved(0.9, False)] * 30
        report = calibrate(records)
        assert report.sufficient
        assert "OVERCONFIDENT" in report.verdict

    def test_underconfidence_is_named_too(self) -> None:
        records = [_resolved(0.4, True)] * 45 + [_resolved(0.4, False)] * 5
        report = calibrate(records)
        assert "UNDERCONFIDENT" in report.verdict

    def test_the_total_alone_does_not_license_a_claim(self) -> None:
        """50 predictions spread thinly across buckets still says nothing about
        any particular confidence level."""
        records = (
            [_resolved(0.40, True)] * 13
            + [_resolved(0.55, True)] * 13
            + [_resolved(0.70, False)] * 12
            + [_resolved(0.85, False)] * 12
        )
        report = calibrate(records)
        assert report.total >= MIN_TOTAL_SAMPLE
        assert all(not b.sufficient for b in report.buckets)
        assert "INSUFFICIENT SAMPLE" in report.verdict

    def test_the_multiplier_is_none_until_measured(self) -> None:
        report = calibrate([_resolved(0.7, True)] * 5)
        assert report.multiplier(0.7) is None

    def test_the_multiplier_corrects_toward_realised_accuracy(self) -> None:
        records = [_resolved(0.8, True)] * 10 + [_resolved(0.8, False)] * 10
        report = calibrate(records)
        multiplier = report.multiplier(0.8)
        assert multiplier is not None
        # Said 80%, was 50%: the correction pulls weight down.
        assert multiplier < 1.0

    def test_the_brier_score_is_reported(self) -> None:
        # Always says 100% and is always wrong: the worst possible score.
        report = calibrate([_resolved(1.0, False)] * 10)
        assert report.brier == 1.0

    def test_non_directional_records_are_excluded(self) -> None:
        # A WAIT has no direction to be right about; counting them would let a
        # system that never calls anything look well calibrated.
        report = calibrate(
            [_resolved(0.7, True)] * 3 + [{"confidence": 0.7, "direction_correct": None}]
        )
        assert report.total == 3


# ---------------------------------------------------------------------------
# SPEC 30 - agent reliability
# ---------------------------------------------------------------------------


def _opinion_row(agent: AgentRole, agent_decision: str, council: str, correct: bool):
    return {
        "agent": agent.value,
        "agent_decision": agent_decision,
        "council_decision": council,
        "direction_correct": correct,
    }


class TestReliability:
    def test_no_history_means_no_multiplier(self) -> None:
        report = build_reliability([])
        assert report.records == ()
        assert report.multiplier(AgentRole.TECHNICAL) is None

    def test_a_small_sample_reports_insufficient(self) -> None:
        rows = [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", True)] * 5
        record = build_reliability(rows).records[0]

        assert record.scored == 5
        assert record.accuracy is None
        assert record.multiplier is None
        assert record.status == "INSUFFICIENT_SAMPLE"

    def test_an_agent_is_scored_on_its_own_call_not_the_councils(self) -> None:
        """An agent that argued SELL must not be credited when the council's
        BUY works out."""
        rows = [
            _opinion_row(AgentRole.REVERSAL, "SELL", "BUY", True)
        ] * MIN_RELIABILITY_SAMPLE
        record = build_reliability(rows).records[0]

        # The council was right, so the dissenter was wrong every time.
        assert record.correct == 0
        assert record.accuracy == 0.0
        assert record.overruled_correctly == MIN_RELIABILITY_SAMPLE

    def test_a_dissenter_is_vindicated_when_the_council_is_wrong(self) -> None:
        """**Pasarnya harus bergerak dua arah, dan itu bukan kerapian.**

        Versi pertama test ini hanya memuat baris tempat pasar TURUN, dan di
        sampel seperti itu keahlian tidak bisa diukur sama sekali: agen yang
        selalu bilang SELL akan selalu benar tanpa membuktikan apa pun. Sejak
        titik netral diukur dari garis dasar (2026-08-25), keadaan itu
        dilaporkan NEUTRAL - dan itu jawaban yang benar.

        Baris TECHNICAL di bawah ada untuk memberi pasar arah yang lain, bukan
        untuk diuji.
        """
        rows = (
            [_opinion_row(AgentRole.REVERSAL, "SELL", "BUY", False)]
            * MIN_RELIABILITY_SAMPLE
            + [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", True)]
            * MIN_RELIABILITY_SAMPLE
        )
        laporan = build_reliability(rows)
        record = next(
            r for r in laporan.records if r.role is AgentRole.REVERSAL
        )

        assert record.correct == MIN_RELIABILITY_SAMPLE
        assert record.vindicated == MIN_RELIABILITY_SAMPLE
        assert laporan.pasar_naik == 0.5
        assert record.status == "ABOVE_NEUTRAL"

    def test_selalu_benar_di_pasar_satu_arah_bukan_keahlian(self) -> None:
        """**Inti perbaikan 2026-08-25.**

        Agen yang selalu bilang BUY di sampel yang pasarnya selalu naik akan
        berakurasi 1,0 - dan menyumbang nol, karena garis dasarnya juga 1,0.
        Pada titik netral tetap 0,5 ia mendapat pengali maksimum untuk mengikuti
        arus. Terukur di produksi: pasar naik 58,9%, dan agen yang selalu BUY
        mendapat bobot tambahan sementara satu-satunya agen ber-edge nyata
        justru dikurangi.
        """
        record = build_reliability(
            [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", True)] * 100
        ).records[0]

        assert record.accuracy == 1.0
        assert record.edge == 0.0
        assert record.multiplier == 1.0
        assert record.status == "NEUTRAL"

    def test_abstentions_are_not_scored(self) -> None:
        """Declining to read thin evidence is a legitimate act, not a failure."""
        rows = [_opinion_row(AgentRole.NEWS, "WAIT", "BUY", False)] * 30
        assert build_reliability(rows).records == ()

    def test_the_multiplier_is_bounded_in_both_directions(self) -> None:
        """Pasarnya dibuat bergerak dua arah - lihat alasannya di
        ``test_a_dissenter_is_vindicated_when_the_council_is_wrong``. Agen yang
        benar di KEDUA arah punya keahlian; agen yang selalu benar di pasar
        satu arah tidak.
        """
        perfect = build_reliability(
            [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", True)] * 50
            + [_opinion_row(AgentRole.TECHNICAL, "SELL", "SELL", True)] * 50
        ).records[0]
        hopeless = build_reliability(
            [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", False)] * 50
            + [_opinion_row(AgentRole.TECHNICAL, "SELL", "SELL", False)] * 50
        ).records[0]

        assert perfect.multiplier == MAX_MULTIPLIER
        assert hopeless.multiplier == MIN_MULTIPLIER
        # An agent is never silenced: one that cannot be heard can never be
        # proven right later.
        assert hopeless.multiplier > 0


# ---------------------------------------------------------------------------
# The judge's two SPEC 16 factors
# ---------------------------------------------------------------------------


class TestMeasuredHistory:
    def test_an_empty_history_answers_none_to_everything(self) -> None:
        history = empty_history()
        assert history.measurable is False
        for role in AgentRole:
            assert history.reliability(role) is None
        for confidence in (0.4, 0.55, 0.7, 0.9):
            assert history.calibration(confidence) is None

    def test_it_says_plainly_that_it_changes_nothing(self) -> None:
        described = empty_history().describe()
        assert described["measurable"] is False
        assert "neutral" in described["effect_on_judging"]
        assert "unavailable" in described["effect_on_judging"]

    def _measured(self, **kw) -> MeasuredHistory:
        # Benar di KEDUA arah, bukan hanya BUY. Sejak titik netral diukur dari
        # garis dasar (2026-08-25), agen yang selalu bilang BUY di sampel yang
        # pasarnya selalu naik berkeunggulan NOL - jadi tidak ada yang bisa
        # diusulkan, dan test ini akan menguji ketiadaan usulan alih-alih
        # rutenya.
        return MeasuredHistory(
            reliability_report=build_reliability(
                [_opinion_row(AgentRole.TECHNICAL, "BUY", "BUY", True)]
                * MIN_RELIABILITY_SAMPLE
                + [_opinion_row(AgentRole.TECHNICAL, "SELL", "SELL", True)]
                * MIN_RELIABILITY_SAMPLE
            ),
            calibration_report=calibrate(
                [_resolved(0.7, True)] * 15 + [_resolved(0.7, False)] * 10
            ),
            **kw,
        )

    def test_measuring_an_agent_now_does_change_its_weight(self) -> None:
        """**PASAL 11.11 dan 11.16 ditimpa operator pada 2026-08-25.**

        Test ini sudah berbalik dua kali, dan riwayatnya layak disimpan.
        Mula-mula ia menyatakan bahwa melewati ambang sampel membuat
        ``reliability()`` memulangkan pengali. Lalu dibalik: PASAL 11.11
        menyebut kasus itu persis (``1,00 -> 1,10``) dan melarangnya, jadi
        pengukuran berhenti berlaku sendiri.

        Sekarang dibalik lagi, dan kali ini bukan karena penulis kode berubah
        pikiran - operator memutuskannya. Yang menentukan: gerbang itu tidak
        memperlambat penerapan, ia MENIADAKANNYA. Tabel yang mengisi
        ``approved_weights`` tidak pernah ada, jadi terukur di
        ``judge_decisions``, ``historical_reliability`` tercatat tidak tersedia
        pada 100% keputusan di setiap hari yang tersimpan.

        Pagarnya tidak ikut dicabut - ia pindah tempat: sampel minimum, batas
        pengali 0,7-1,2, dan titik netral yang diukur. Lihat
        ``test_bobot_berlaku_tanpa_persetujuan``.
        """
        history = self._measured()

        assert history.measurable is True
        assert history.calibration(0.7) is not None
        assert history.reliability_report.measured

        pengali = history.reliability(AgentRole.TECHNICAL)
        assert pengali is not None, (
            "pengukuran tidak berlaku - gerbang persetujuan masih terpasang"
        )
        assert 0.7 <= pengali <= 1.2

    def test_bobot_yang_disetujui_tidak_lagi_menimpa_pengukuran(self) -> None:
        """Pasangan test di atas. ``approved_weights`` masih ada sebagai bahan
        usulan, tapi ia bukan lagi yang menentukan apa yang berlaku - dan kalau
        ia menimpa lagi, gerbangnya kembali lewat pintu belakang."""
        from aruna.learning.weights import ApprovedWeights

        history = self._measured(
            approved_weights=ApprovedWeights({"TECHNICAL": 1.1})
        )

        assert history.reliability(AgentRole.TECHNICAL) != 1.1

    def test_an_agent_with_no_record_is_still_unmeasured(self) -> None:
        assert self._measured().reliability(AgentRole.FUNDAMENTAL) is None

    def test_measurement_becomes_a_proposal_instead(self) -> None:
        """The measurement is not discarded - it changes route. It stops being
        an instruction and becomes something a human is asked about."""
        proposals = self._measured().proposals()

        assert [p.role for p in proposals] == ["TECHNICAL"]
        assert proposals[0].current == 1.0
        assert proposals[0].proposed > 1.0
        # Dua kali ambangnya: fixture-nya memuat sampel di kedua arah pasar,
        # karena keahlian tidak bisa diukur di pasar yang bergerak satu arah.
        assert proposals[0].sample == MIN_RELIABILITY_SAMPLE * 2


# ---------------------------------------------------------------------------
# SPEC 25, 26 - autopsy
# ---------------------------------------------------------------------------


def _loss_record(**overrides) -> dict:
    base = {
        "signal_id": "learn00000000001",
        "symbol": "BTC/USDT",
        "horizon_code": "1h",
        "direction": "BUY",
        "confidence": 0.85,
        "outcome_class": "WRONG_FROM_START",
        "predicted_move_pct": 5.0,
        "actual_move_pct": -3.2,
        "max_adverse_pct": -4.1,
        "net_pnl": Decimal("-42000.00"),
        "regime": "TRENDING",
        "risk_level": "MODERATE",
        "news_state": "NO_RECENT_NEWS",
        "weights": [
            {"role": "TECHNICAL", "decision": "BUY", "weight": 0.51},
            {"role": "STRUCTURE", "decision": "BUY", "weight": 0.32},
            {"role": "REVERSAL", "decision": "SELL", "weight": 0.44},
        ],
        # Nilai di bawah ini HARUS yang benar-benar tersimpan di database.
        # Sebelumnya fixture ini memakai "OPPOSE" dan "REJECTED" - dua nilai
        # yang tidak ada di enum mana pun dan dilarang CHECK di migrasi 0007.
        # Kode produksinya menyaring literal yang sama, jadi test ini lulus
        # sementara autopsy sungguhan selalu mengembalikan nol objection dan nol
        # veto ditolak. Test-nya bukan gagal menangkap bug; test-nya ikut
        # memakai bug yang sama.
        "objections": [
            {
                "accuser": "REVERSAL",
                "target": "TECHNICAL",
                "stance": Stance.OBJECT.value,
                "ground": "overconfident",
                "detail": "rsi above 70",
                "conceded": False,
            }
        ],
        "vetoes": [
            {
                "reason": VetoReason.STALE_PRICE.value,
                "outcome": VetoReviewOutcome.VETO_REJECTED.value,
                "rationale": "feed was fine",
            }
        ],
    }
    return base | overrides


class TestAutopsy:
    def test_a_winner_gets_no_autopsy(self) -> None:
        assert perform_autopsy(_loss_record(outcome_class="TARGET_REACHED")) is None

    def test_every_losing_class_is_dissected(self) -> None:
        for outcome_class in LOSING_CLASSES:
            record = _loss_record(outcome_class=outcome_class.value)
            assert perform_autopsy(record) is not None, outcome_class

    def test_the_losing_side_and_the_dissenters_are_named(self) -> None:
        autopsy = perform_autopsy(_loss_record())
        assert autopsy is not None
        assert autopsy.backers[0] == ("TECHNICAL", 0.51)
        assert autopsy.dissenters == ("REVERSAL",)
        assert any("argued the other way" in f for f in autopsy.findings)

    def test_unanswered_objections_and_rejected_vetoes_are_surfaced(self) -> None:
        autopsy = perform_autopsy(_loss_record())
        assert autopsy is not None
        assert len(autopsy.unanswered_objections) == 1
        # Ground-nya, bukan sekadar ada isinya: yang berguna bagi pembaca
        # autopsy adalah ATAS DASAR APA objection itu diajukan.
        assert "overconfident" in autopsy.unanswered_objections[0]
        assert len(autopsy.rejected_vetoes) == 1
        assert any("rejected" in f for f in autopsy.findings)

    def test_a_high_confidence_loss_is_flagged(self) -> None:
        autopsy = perform_autopsy(_loss_record(confidence=0.9))
        assert autopsy is not None
        assert any("high-confidence loss" in f for f in autopsy.findings)

    def test_a_magnitude_failure_is_told_from_a_direction_failure(self) -> None:
        autopsy = perform_autopsy(
            _loss_record(
                outcome_class="RIGHT_THEN_REVERSED",
                predicted_move_pct=5.0,
                actual_move_pct=1.0,
            )
        )
        assert autopsy is not None
        assert any("magnitude problem" in f for f in autopsy.findings)

    def test_an_autopsy_states_that_it_changes_nothing(self) -> None:
        """The dangerous version of this feature re-weights after every loss."""
        autopsy = perform_autopsy(_loss_record())
        assert autopsy is not None
        assert "does not adjust any weight" in autopsy.to_dict()["note"]

    def test_objections_overruled_and_then_vindicated_are_counted(self) -> None:
        rows = [
            {"accuser": "REVERSAL", "ground": "overbought", "direction_correct": False},
            {"accuser": "REVERSAL", "ground": "overbought", "direction_correct": False},
            {"accuser": "REVERSAL", "ground": "overbought", "direction_correct": True},
            {"accuser": "NEWS", "ground": "stale_news", "direction_correct": True},
        ]
        records = successful_objections(rows)
        top = records[0]

        assert top.accuser == "REVERSAL"
        assert top.raised == 3
        assert top.vindicated == 2
        assert top.vindication_rate == pytest.approx(0.6667, abs=1e-4)
        # Sorted so the blind spot that keeps being dismissed comes first.
        assert records[1].vindicated == 0


# ---------------------------------------------------------------------------
# SPEC 27, 28 - counterfactual and ghost signals
# ---------------------------------------------------------------------------


class TestCounterfactual:
    def test_the_mirror_of_a_losing_call_is_a_winner(self) -> None:
        result = counterfactual(_signal(), Decimal(950))
        assert result is not None
        assert result.taken_move_pct == pytest.approx(-5.0)
        assert result.alternative is Decision.SELL
        assert result.alternative_move_pct == pytest.approx(5.0)
        assert result.alternative_was_better is True

    def test_a_winning_call_had_no_better_alternative(self) -> None:
        result = counterfactual(_signal(), Decimal(1050))
        assert result is not None
        assert result.alternative_was_better is False

    def test_a_wait_has_no_single_mirror(self) -> None:
        assert counterfactual(_signal(direction=Decision.WAIT), Decimal(1050)) is None

    def test_the_figure_is_labelled_as_gross(self) -> None:
        result = counterfactual(_signal(), Decimal(950))
        assert result is not None
        assert "before the costs" in result.to_dict()["note"]


class TestGhostSignals:
    def test_a_wait_through_a_real_move_is_recorded(self) -> None:
        ghost = ghost_signal(
            _signal(direction=Decision.WAIT), max_favourable_pct=4.0, max_adverse_pct=-0.5
        )
        assert ghost is not None
        assert ghost.missed_move_pct == pytest.approx(4.0)
        assert ghost.direction is Decision.BUY
        assert ghost.reasoning  # why we stood aside is kept with the miss

    def test_the_bigger_side_of_the_move_is_the_one_named(self) -> None:
        ghost = ghost_signal(
            _signal(direction=Decision.WAIT), max_favourable_pct=1.2, max_adverse_pct=-6.0
        )
        assert ghost is not None
        assert ghost.direction is Decision.SELL
        assert ghost.missed_move_pct == pytest.approx(-6.0)

    def test_a_quiet_market_produces_no_ghost(self) -> None:
        assert (
            ghost_signal(
                _signal(direction=Decision.WAIT),
                max_favourable_pct=GHOST_THRESHOLD_PCT / 2,
                max_adverse_pct=-0.1,
            )
            is None
        )

    def test_a_taken_position_is_not_a_ghost(self) -> None:
        assert ghost_signal(_signal(), 5.0, -1.0) is None

    def test_the_summary_refuses_to_call_a_miss_a_mistake(self) -> None:
        ghost = ghost_signal(_signal(direction=Decision.WAIT), 4.0, -0.5)
        assert ghost is not None
        summary = summarise_ghosts([ghost])
        assert summary["ghost_signals"] == 1
        assert "depends on the evidence at the time" in summary["note"]

    def test_no_ghosts_says_so_rather_than_claiming_success(self) -> None:
        assert summarise_ghosts([])["ghost_signals"] == 0


class TestLookahead:
    """HORIZON_MISMATCH: the one class that needs data from after the horizon."""

    def _after(self, *pairs: tuple[int, float]):
        return [
            (NOW + timedelta(minutes=m), Decimal(str(p))) for m, p in pairs
        ]

    def test_a_target_reached_just_after_the_horizon_is_reclassified(self) -> None:
        signal = _signal()  # 1h horizon, target 1050
        outcome, note = reclassify_with_lookahead(
            signal,
            OutcomeClass.WRONG_FROM_START,
            self._after((90, 1060.0)),
        )
        assert outcome is OutcomeClass.HORIZON_MISMATCH
        assert note is not None
        assert "horizon was too short" in note

    def test_the_recorded_score_is_explicitly_unchanged(self) -> None:
        _, note = reclassify_with_lookahead(
            _signal(), OutcomeClass.WRONG_FROM_START, self._after((90, 1060.0))
        )
        assert note is not None
        assert "recorded score is unchanged" in note

    def test_looking_far_enough_ahead_is_bounded(self) -> None:
        """Given enough time every direction is right at some point."""
        outcome, note = reclassify_with_lookahead(
            _signal(),
            OutcomeClass.WRONG_FROM_START,
            self._after((60 * 24 * 30, 2000.0)),
        )
        assert outcome is OutcomeClass.WRONG_FROM_START
        assert note is None

    def test_a_prediction_that_was_simply_wrong_stays_wrong(self) -> None:
        outcome, _ = reclassify_with_lookahead(
            _signal(), OutcomeClass.WRONG_FROM_START, self._after((90, 900.0))
        )
        assert outcome is OutcomeClass.WRONG_FROM_START

    def test_only_wrong_from_start_is_a_candidate(self) -> None:
        # A call that worked and reversed was not early; it was mistimed, and
        # RIGHT_THEN_REVERSED already says so.
        outcome, _ = reclassify_with_lookahead(
            _signal(), OutcomeClass.RIGHT_THEN_REVERSED, self._after((90, 1060.0))
        )
        assert outcome is OutcomeClass.RIGHT_THEN_REVERSED

    def test_no_target_means_no_reclassification(self) -> None:
        outcome, _ = reclassify_with_lookahead(
            _signal(target_price=None),
            OutcomeClass.WRONG_FROM_START,
            self._after((90, 1060.0)),
        )
        assert outcome is OutcomeClass.WRONG_FROM_START
