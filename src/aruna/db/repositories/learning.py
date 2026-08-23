"""Learning storage (SPEC 25-30).

The read side is the interesting half. Autopsy needs a losing prediction joined
to the argument that produced it, and that join only works because PHASE 7
stores ``council_session_id`` on the locked signal.

Measurements (calibration, reliability) are inserted, never updated: the
triggers in migration 0010 enforce it. Findings about a single prediction are
upserted, because rerunning an improved analysis over an immutable record should
replace the finding rather than pile up duplicates.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.enums import Stance
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.learning.autopsy import Autopsy
from aruna.learning.calibration import CalibrationReport
from aruna.learning.counterfactual import Counterfactual, GhostSignal
from aruna.learning.reliability import ReliabilityReport

#: Stance yang berarti "menentang", untuk membedakannya dari SUPPORT.
#:
#: ``overruled_objections()`` dulu menyaring ``stance = 'OPPOSE'``. Stance tidak
#: pernah punya anggota bernama OPPOSE - yang ada SUPPORT / OBJECT /
#: COUNTER_PROPOSE / ACCEPT_CORRECTION - dan CHECK di migrasi 0007 melarang
#: nilai itu tersimpan. Query-nya karena itu selalu mengembalikan nol baris,
#: dan analisis vindikasi SPEC 26 kosong permanen tanpa satu pun error.
#: Diambil dari enum, bukan diketik ulang, supaya anggota yang berganti nama
#: jadi error dan tidak bisa lolos diam-diam lagi.
OBJECTING_STANCES: tuple[str, ...] = (
    Stance.OBJECT.value,
    Stance.COUNTER_PROPOSE.value,
)


class LearningRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- reading the record ---------------------------------------------

    async def resolved(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Resolved predictions joined to their outcome and paper trade.

        Directional and non-directional both: SPEC 28 needs the WAITs, and a
        query that quietly dropped them would make ghost signals impossible to
        find.
        """
        rows = await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.market_code, s.horizon_code, "
            "s.direction, "
            # Keyakinan MENTAH, bukan yang sudah dikalibrasi (bagian 9).
            # Membaca `s.confidence` di sini berarti mengukur keluaran
            # kalibrator dengan kalibrator - dan pada putaran kedua ia akan
            # melaporkan bahwa semuanya baik-baik saja, makin meyakinkan justru
            # makin salah. COALESCE untuk baris yang dikunci sebelum kolom ini
            # ada.
            "COALESCE(s.confidence_raw, s.confidence) AS confidence, "
            "s.reference_price, s.entry_price, "
            "s.target_price, s.expected_move_pct, s.locked_at, s.as_of, "
            "s.resolves_at, s.reasoning, s.regime, s.risk_level, s.news_state, "
            "s.council_session_id, g.published, "
            # **`resolved_at` sempat hilang dari daftar ini, dan diamnya
            # mahal.** `adaptive._strategy_slices` mengurutkan barisnya menurut
            # `resolved_at` SEBELUM menghitung drawdown - dan kolom yang tidak
            # pernah dipilih memulangkan `None` untuk tiap baris, jadi
            # pengurutannya menjadi tanpa efek dan `sorted` yang stabil
            # membiarkan urutan `locked_at DESC` apa adanya.
            #
            # Akibatnya `strategy_performance.max_drawdown` dihitung atas deret
            # TERBALIK. Ia tetap sebuah angka, tetap masuk akal dilihat, dan
            # tidak menggambarkan apa pun - persis bentuk kegagalan yang
            # docstring `drawdown` sendiri peringatkan.
            "r.resolved_at, r.outcome_class, r.direction_correct, "
            "r.actual_move_pct, r.predicted_move_pct, r.final_price, "
            "r.max_adverse_pct, r.max_favourable_pct, r.target_reached, "
            "t.net_pnl, t.result "
            "FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "LEFT JOIN paper_trades t ON t.signal_id = s.signal_id "
            "ORDER BY s.locked_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["locked_at"] = as_utc(row["locked_at"])
            row["as_of"] = as_utc(row["as_of"])
            row["resolves_at"] = as_utc(row["resolves_at"])
            row["resolved_at"] = as_utc(row["resolved_at"])
            row["reasoning"] = load_json(row["reasoning"]) or []
            row["published"] = bool(row["published"])
        return rows

    async def council_context(self, session_id: int) -> dict[str, Any]:
        """The argument behind one prediction: weights, objections, vetoes."""
        judgement = await self._db.fetchrow(
            "SELECT weights, reasoning, minority_prevailed, unavailable_factors "
            "FROM judge_decisions WHERE session_id = %s",
            session_id,
        )
        objections = await self._db.fetch(
            "SELECT o.accuser, o.target, o.stance, o.ground, o.detail, o.severity, "
            # An objection counts as answered only when its own accuser was
            # conceded to on the same ground - not when any rebuttal happened.
            "EXISTS (SELECT 1 FROM agent_rebuttals b WHERE b.session_id = o.session_id "
            "AND b.accuser = o.accuser AND b.ground = o.ground AND b.conceded = TRUE) "
            "AS conceded "
            "FROM agent_objections o WHERE o.session_id = %s",
            session_id,
        )
        vetoes = await self._db.fetch(
            "SELECT v.reason, v.detail, v.raised_by, r.outcome, r.rationale "
            "FROM veto_events v LEFT JOIN veto_reviews r ON r.veto_id = v.id "
            "WHERE v.session_id = %s",
            session_id,
        )
        for row in objections:
            row["conceded"] = bool(row["conceded"])
        return {
            "weights": load_json(judgement["weights"]) if judgement else [],
            "judge_reasoning": (
                load_json(judgement["reasoning"]) if judgement else []
            ),
            "minority_prevailed": (
                bool(judgement["minority_prevailed"]) if judgement else False
            ),
            "objections": objections,
            "vetoes": vetoes,
        }

    #: Dimensi rincian keandalan (PASAL 11.2) dan kolom yang mengisinya.
    #:
    #: Rezim diambil dari ``signal_snapshots``, bukan dari ``council_sessions``:
    #: yang tersimpan bersama prediksi adalah rezim yang berlaku saat prediksi
    #: dibuat, dan itu yang harus dinilai. Sesi council tidak menyimpan rezim.
    BREAKDOWN_COLUMNS: dict[str, str] = {  # noqa: RUF012
        "regime": "COALESCE(s.regime, 'UNKNOWN')",
        "timeframe": "s.horizon_code",
        "asset": "s.symbol",
    }

    #: Dimensi PASAL 11.20 dan kolom yang mengisinya.
    #:
    #: ``direction`` diterjemahkan ke kosakata publik di SQL, bukan di Python:
    #: pengelompokannya harus terjadi pada nilai yang sama dengan yang dibaca
    #: operator, kalau tidak "LONG" di laporan dan "BUY" di database menjadi
    #: dua nama untuk satu hal yang harus dicocokkan orang di kepalanya.
    WINDOW_COLUMNS: dict[str, str] = {  # noqa: RUF012
        "asset": "t.symbol",
        "timeframe": "p.horizon_code",
        "regime": "COALESCE(p.regime, 'UNKNOWN')",
        "direction": (
            "CASE t.direction WHEN 'BUY' THEN 'LONG' "
            "WHEN 'SELL' THEN 'SHORT' ELSE t.direction END"
        ),
        "quality": "p.signal_quality",
    }

    async def window_rows(
        self, dimension: str, *, days: int | None = None, limit: int = 20000
    ) -> list[dict[str, Any]]:
        """Posisi kertas dalam satu jendela, dikelompokkan per dimensi (11.20).

        ``days=None`` berarti sepanjang waktu.

        Diurutkan pada ``exit_at`` dan bukan ``entry_at``: jendela "tujuh hari
        terakhir" untuk sebuah HASIL adalah tujuh hari sejak hasilnya
        diketahui. Posisi yang dibuka dua minggu lalu dan ditutup kemarin
        adalah kabar kemarin, bukan kabar dua minggu lalu - dan mengurutkannya
        pada pembukaan akan menyembunyikan hasil terbaru dari horizon panjang
        di jendela yang sudah lewat.

        Posisi yang masih terbuka ikut, dengan ``result`` apa adanya. Ia
        dihitung sebagai ``active`` di lapisan atas dan tidak masuk penyebut -
        tapi menyembunyikannya di sini akan membuat "belum ada apa-apa" tidak
        bisa dibedakan dari "semuanya masih berjalan".
        """
        column = self.WINDOW_COLUMNS.get(dimension)
        if column is None:
            raise ValueError(f"dimensi tidak dikenal: {dimension!r}")

        clause = ""
        args: list[Any] = []
        if days is not None:
            clause = " AND t.exit_at >= %s"
            args.append(to_mysql_datetime(now_utc() - timedelta(days=days)))
        args.append(limit)

        rows = await self._db.fetch(
            f"""
            SELECT {column} AS `key`, t.result AS result
            FROM paper_trades t
            JOIN signal_snapshots p ON p.signal_id = t.signal_id
            WHERE t.exit_at IS NOT NULL{clause}
            ORDER BY t.exit_at DESC
            LIMIT %s
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def agent_breakdown(
        self, dimension: str, *, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """Opini berarah tiap agent, dirinci per dimensi (PASAL 11.2).

        Sumbernya ``council_votes`` - suara yang benar-benar diberikan tiap
        agent - bukan bobot judge. Keduanya menjawab pertanyaan berbeda: bobot
        judge menyimpan bagaimana sebuah opini DITIMBANG, dan tabel ini
        menyimpan apa yang agent itu KATAKAN.

        ``correct`` dihitung dari arah pasar, bukan dari kesepakatan dengan
        council. Agent yang sepakat berbagi hasil council; agent yang menentang
        benar tepat ketika council salah. Menilainya dari kesepakatan akan
        mengukur kepatuhan, bukan keandalan - dan seorang agent yang selalu
        ikut suara terbanyak akan terlihat paling andal justru karena tidak
        pernah menyumbang apa pun.

        Hanya opini BERARAH yang masuk. Agent yang abstain tidak menyatakan apa
        pun, dan yang bilang tidak-ada-posisi tidak bisa benar atau salah
        terhadap pergerakan harga.
        """
        column = self.BREAKDOWN_COLUMNS.get(dimension)
        if column is None:
            raise ValueError(f"dimensi tidak dikenal: {dimension!r}")

        rows = await self._db.fetch(
            f"""
            SELECT v.role AS agent,
                   {column} AS `key`,
                   v.decision AS agent_decision,
                   s.direction AS council_decision,
                   r.direction_correct AS council_correct
            FROM council_votes v
            JOIN signal_snapshots s ON s.council_session_id = v.council_session_id
            JOIN signals g ON g.signal_id = s.signal_id
            JOIN paper_results r ON r.signal_id = s.signal_id
            WHERE v.decision IN ('BUY', 'SELL')
              AND s.direction IN ('BUY', 'SELL')
              AND g.published = TRUE
              AND v.abstained = FALSE
            ORDER BY s.locked_at DESC
            LIMIT %s
            """,
            limit,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            sepakat = row["agent_decision"] == row["council_decision"]
            benar = bool(row["council_correct"])
            out.append({
                "agent": row["agent"],
                "key": row["key"],
                # Sepakat -> berbagi hasil council. Menentang -> benar tepat
                # ketika council salah.
                "correct": benar if sepakat else not benar,
            })
        return out

    async def agent_outcomes(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """One row per agent opinion in a resolved session (SPEC 30).

        The agent's own decision comes from the stored judge weights, so an
        agent is scored on what it argued rather than on what the council
        concluded.
        """
        rows = await self._db.fetch(
            "SELECT j.weights, c.decision AS council_decision, "
            "r.direction_correct "
            "FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "JOIN council_sessions c ON c.id = s.council_session_id "
            "JOIN judge_decisions j ON j.session_id = c.id "
            "WHERE s.direction IN ('BUY', 'SELL') AND g.published = TRUE "
            "ORDER BY s.locked_at DESC LIMIT %s",
            limit,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            for weight in load_json(row["weights"]) or []:
                out.append(
                    {
                        "agent": weight.get("role"),
                        "agent_decision": weight.get("decision"),
                        "council_decision": row["council_decision"],
                        "direction_correct": bool(row["direction_correct"]),
                    }
                )
        return out

    async def overruled_objections(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Objections raised against a call that was made anyway (SPEC 26)."""
        stances = ", ".join(["%s"] * len(OBJECTING_STANCES))
        return await self._db.fetch(
            "SELECT o.accuser, o.ground, r.direction_correct "
            "FROM signal_snapshots s "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "JOIN agent_objections o ON o.session_id = s.council_session_id "
            f"WHERE o.stance IN ({stances}) AND s.direction IN ('BUY', 'SELL') "
            "LIMIT %s",
            *OBJECTING_STANCES,
            limit,
        )

    # ---- writing findings ------------------------------------------------

    async def record_autopsy(self, autopsy: Autopsy) -> None:
        await self._db.execute(
            """
            INSERT INTO loss_autopsies
                (signal_id, outcome_class, hypothesis, sebab, confidence,
                 predicted_move_pct, actual_move_pct, max_adverse_pct, net_pnl,
                 regime, risk_level, news_state, backers, dissenters,
                 unanswered_objections, rejected_vetoes, findings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                outcome_class         = new.outcome_class,
                hypothesis            = new.hypothesis,
                sebab                 = new.sebab,
                backers               = new.backers,
                dissenters            = new.dissenters,
                unanswered_objections = new.unanswered_objections,
                rejected_vetoes       = new.rejected_vetoes,
                findings              = new.findings,
                performed_at          = CURRENT_TIMESTAMP(6)
            """,
            autopsy.signal_id,
            autopsy.outcome_class.value,
            autopsy.hypothesis[:255],
            # Bagian 12: KENAPA, bukan apa. Tanpa baris ini klasifikasinya
            # dihitung lalu dibuang - terlihat di keluaran CLI, tidak pernah
            # bisa dikueri, tidak pernah bisa dipakai pembelajaran.
            autopsy.sebab.value,
            round(autopsy.confidence, 3),
            autopsy.predicted_move_pct,
            round(autopsy.actual_move_pct, 6),
            round(autopsy.max_adverse_pct, 6),
            autopsy.net_pnl,
            autopsy.regime,
            autopsy.risk_level,
            autopsy.news_state,
            dump_json([{"agent": a, "weight": w} for a, w in autopsy.backers]),
            dump_json(list(autopsy.dissenters)),
            dump_json(list(autopsy.unanswered_objections)),
            dump_json(list(autopsy.rejected_vetoes)),
            dump_json(list(autopsy.findings)),
        )

    async def record_counterfactual(self, item: Counterfactual) -> None:
        await self._db.execute(
            """
            INSERT INTO counterfactuals
                (signal_id, taken, taken_move_pct, alternative,
                 alternative_move_pct, alternative_was_better)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                taken_move_pct         = new.taken_move_pct,
                alternative_move_pct   = new.alternative_move_pct,
                alternative_was_better = new.alternative_was_better,
                computed_at            = CURRENT_TIMESTAMP(6)
            """,
            item.signal_id,
            item.taken.value,
            round(item.taken_move_pct, 6),
            item.alternative.value,
            round(item.alternative_move_pct, 6),
            item.alternative_was_better,
        )

    async def record_ghost(self, ghost: GhostSignal) -> None:
        await self._db.execute(
            """
            INSERT INTO ghost_signals
                (signal_id, symbol, horizon_code, missed_move_pct, direction,
                 why_we_waited)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                missed_move_pct = new.missed_move_pct,
                direction       = new.direction,
                why_we_waited   = new.why_we_waited,
                computed_at     = CURRENT_TIMESTAMP(6)
            """,
            ghost.signal_id,
            ghost.symbol,
            ghost.horizon,
            round(ghost.missed_move_pct, 6),
            ghost.direction.value,
            dump_json(list(ghost.reasoning)),
        )

    async def record_calibration(self, report: CalibrationReport) -> int:
        """Append a measurement. Never an update - see migration 0010."""
        return await self._db.insert(
            """
            INSERT INTO calibration_snapshots
                (total_resolved, correct, brier_score, sufficient_sample,
                 verdict, buckets)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            report.total,
            report.correct,
            report.brier,
            report.sufficient,
            report.verdict[:255],
            dump_json([b.to_dict() for b in report.buckets]),
        )

    async def record_reliability(self, report: ReliabilityReport) -> int:
        for record in report.records:
            await self._db.execute(
                """
                INSERT INTO agent_reliability
                    (agent, scored_opinions, correct, accuracy, multiplier,
                     vindicated, overruled_correctly, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                record.role.value,
                record.scored,
                record.correct,
                record.accuracy,
                record.multiplier,
                record.vindicated,
                record.overruled_correctly,
                record.status,
            )
        return len(report.records)

    # ---- reading findings back -------------------------------------------

    async def autopsies(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT a.*, s.symbol, s.horizon_code, s.direction "
            "FROM loss_autopsies a "
            "JOIN signal_snapshots s ON s.signal_id = a.signal_id "
            "ORDER BY a.performed_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["performed_at"] = as_utc(row["performed_at"])
            for field in ("backers", "dissenters", "findings",
                          "unanswered_objections", "rejected_vetoes"):
                row[field] = load_json(row[field]) or []
        return rows

    async def ghosts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM ghost_signals "
            "ORDER BY abs(missed_move_pct) DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["computed_at"] = as_utc(row["computed_at"])
            row["why_we_waited"] = load_json(row["why_we_waited"]) or []
        return rows

    async def latest_calibration(self) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM calibration_snapshots ORDER BY measured_at DESC LIMIT 1"
        )
        if row:
            row["measured_at"] = as_utc(row["measured_at"])
            row["buckets"] = load_json(row["buckets"]) or []
            row["sufficient_sample"] = bool(row["sufficient_sample"])
        return row

    async def reliability_as_of(self, moment: datetime) -> list[dict[str, Any]]:
        """Each agent's most recent measurement at or before ``moment``.

        This is what the append-only rule on `agent_reliability` buys: replaying
        a past decision needs the weights that were in force then, and a table
        that overwrote itself could only ever offer today's.
        """
        return await self._db.fetch(
            "SELECT a.* FROM agent_reliability a "
            "JOIN (SELECT agent, max(measured_at) AS latest FROM agent_reliability "
            "      WHERE measured_at <= %s GROUP BY agent) m "
            "  ON m.agent = a.agent AND m.latest = a.measured_at "
            "ORDER BY a.agent",
            to_mysql_datetime(moment),
        )

    async def calibration_as_of(self, moment: datetime) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM calibration_snapshots WHERE measured_at <= %s "
            "ORDER BY measured_at DESC LIMIT 1",
            to_mysql_datetime(moment),
        )
        if row:
            row["measured_at"] = as_utc(row["measured_at"])
            row["buckets"] = load_json(row["buckets"]) or []
            row["sufficient_sample"] = bool(row["sufficient_sample"])
        return row

    async def latest_reliability(self) -> list[dict[str, Any]]:
        return await self._db.fetch(
            "SELECT a.* FROM agent_reliability a "
            "JOIN (SELECT agent, max(measured_at) AS latest FROM agent_reliability "
            "      GROUP BY agent) m "
            "  ON m.agent = a.agent AND m.latest = a.measured_at "
            "ORDER BY a.agent"
        )

    async def drift_windows(
        self, *, days: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Behaviour before and during the last ``days``, for drift detection.

        Split on ``resolved_at`` rather than lock time: a prediction belongs to
        the period whose outcome it reports, and grouping by when it was made
        would put a still-running call in the window it has not finished.
        """

        async def window(clause: str) -> dict[str, Any]:
            row = await self._db.fetchrow(
                "SELECT count(*) AS resolved, sum(r.direction_correct) AS correct "
                "FROM paper_results r JOIN signals g ON g.signal_id = r.signal_id "
                "WHERE r.original_direction IN ('BUY','SELL') AND g.published = TRUE "
                f"AND r.resolved_at {clause}",
                days,
            )
            regimes = await self._db.fetch(
                "SELECT s.regime, count(*) AS n FROM paper_results r "
                "JOIN signal_snapshots s ON s.signal_id = r.signal_id "
                f"WHERE s.regime IS NOT NULL AND r.resolved_at {clause} "
                "GROUP BY s.regime",
                days,
            )
            return {
                "resolved": int(row["resolved"] or 0) if row else 0,
                "correct": int(row["correct"] or 0) if row else 0,
                "regimes": {r["regime"]: int(r["n"]) for r in regimes},
            }

        return (
            await window("< (NOW(6) - INTERVAL %s DAY)"),
            await window(">= (NOW(6) - INTERVAL %s DAY)"),
        )

    async def performance_window(self, *, days: int) -> dict[str, Any]:
        """Net performance over a period (SPEC 41 weekly and monthly)."""
        row = await self._db.fetchrow(
            "SELECT count(*) AS trades, sum(t.result = 'WIN') AS wins, "
            "sum(t.result = 'LOSS') AS losses, sum(t.net_pnl) AS net, "
            "sum(t.gross_pnl) AS gross, "
            "sum(t.entry_fee + t.exit_fee + t.slippage_cost + t.spread_cost) AS costs "
            "FROM paper_trades t WHERE t.result <> 'OPEN' "
            "AND t.exit_at >= (NOW(6) - INTERVAL %s DAY)",
            days,
        )
        accuracy = await self._db.fetchrow(
            "SELECT count(*) AS resolved, sum(r.direction_correct) AS correct "
            "FROM paper_results r WHERE r.original_direction IN ('BUY', 'SELL') "
            "AND r.resolved_at >= (NOW(6) - INTERVAL %s DAY)",
            days,
        )
        return {
            "days": days,
            **(dict(row) if row else {}),
            **(dict(accuracy) if accuracy else {}),
        }


__all__ = ["LearningRepository"]
