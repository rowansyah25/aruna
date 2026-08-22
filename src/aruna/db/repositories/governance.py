"""Governance storage (SPEC 31, 44).

Questions and proposals are upserted - they are derived and edited. Decisions
are inserted and never touched: the database enforces it, and the application
has no method that could.
"""

from __future__ import annotations

from typing import Any

from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json
from aruna.governance.drift import DriftReport
from aruna.governance.proposal import ModelProposal
from aruna.governance.research import ResearchQuestion


class GovernanceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- research questions ---------------------------------------------

    async def record_question(self, question: ResearchQuestion) -> None:
        await self._db.execute(
            """
            INSERT INTO research_questions
                (question_key, question, source, evidence, severity, status)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                question = new.question,
                evidence = new.evidence,
                severity = new.severity
            """,
            question.key,
            question.question[:500],
            question.source.value,
            dump_json(list(question.evidence)),
            round(question.severity, 3),
            question.status.value,
        )

    async def close_absent(self, current_keys: list[str]) -> int:
        """Close open questions the record no longer raises.

        Questions are re-derived from measurements on every run, so one that
        stops appearing has stopped being true - the costs came down, the
        calibration gap closed. Leaving it OPEN would report a solved problem as
        outstanding indefinitely, and the list would only ever grow.

        Closed rather than deleted: that a question was once raised, and when it
        stopped, is part of the record.
        """
        if not current_keys:
            # Nothing derived at all is more likely a broken run than every
            # problem being solved at once, so nothing is closed.
            return 0
        placeholders = ", ".join(["%s"] * len(current_keys))
        return await self._db.execute(
            f"UPDATE research_questions SET status = 'CLOSED' "
            f"WHERE status = 'OPEN' AND question_key NOT IN ({placeholders})",
            *current_keys,
        )

    async def questions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM research_questions WHERE status <> 'CLOSED' "
            "ORDER BY severity DESC, raised_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["evidence"] = load_json(row["evidence"]) or []
            row["raised_at"] = as_utc(row["raised_at"])
        return rows

    # ---- proposals -------------------------------------------------------

    async def record_proposal(self, proposal: ModelProposal) -> None:
        payload = proposal.to_dict()
        await self._db.execute(
            """
            INSERT INTO model_proposals
                (proposal_key, title, hypothesis, change_note, question_key,
                 parameters, status, validation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                title       = new.title,
                hypothesis  = new.hypothesis,
                change_note = new.change_note,
                parameters  = new.parameters,
                status      = new.status,
                validation  = new.validation
            """,
            proposal.key,
            proposal.title[:255],
            proposal.hypothesis[:1000],
            proposal.change[:1000],
            proposal.question_key,
            dump_json(payload["parameters"]),
            proposal.status.value,
            dump_json(payload["validation"]),
        )

    async def proposals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM model_proposals ORDER BY updated_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["parameters"] = load_json(row["parameters"]) or []
            row["validation"] = load_json(row["validation"])
            row["shadow"] = load_json(row["shadow"])
            row["raised_at"] = as_utc(row["raised_at"])
        return rows

    async def proposal(self, key: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM model_proposals WHERE proposal_key = %s", key
        )
        if row:
            row["parameters"] = load_json(row["parameters"]) or []
            row["validation"] = load_json(row["validation"])
            row["shadow"] = load_json(row["shadow"])
            row["raised_at"] = as_utc(row["raised_at"])
        return row

    async def variants_tested(self) -> int:
        """How many proposals have ever carried a validation result.

        Feeds the multiple-comparisons correction: the twentieth variant tested
        has to clear a higher bar than the first, and this is where that count
        comes from.
        """
        return int(
            await self._db.fetchval(
                "SELECT count(*) FROM model_proposals WHERE validation IS NOT NULL"
            )
            or 0
        )

    # ---- decisions (append-only) -----------------------------------------

    async def record_decision(self, proposal: ModelProposal) -> None:
        """Write the row a person signed. There is no update path."""
        payload = proposal.to_dict()
        await self._db.execute(
            """
            INSERT INTO proposal_decisions
                (proposal_key, decision, decided_by, note, validation)
            VALUES (%s, %s, %s, %s, %s)
            """,
            proposal.key,
            proposal.status.value,
            proposal.decided_by or "",
            (proposal.decision_note or None),
            dump_json(payload["validation"]),
        )

    async def decisions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM proposal_decisions ORDER BY decided_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["decided_at"] = as_utc(row["decided_at"])
            row["validation"] = load_json(row["validation"])
        return rows

    # ---- drift ------------------------------------------------------------

    async def record_drift(self, report: DriftReport) -> None:
        await self._db.execute(
            """
            INSERT INTO drift_checks
                (baseline_resolved, recent_resolved, performance_drift,
                 regime_shift, sufficient_sample, verdict, findings)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            report.baseline.resolved,
            report.recent.resolved,
            report.performance_drift,
            report.regime_shift,
            report.sufficient,
            report.verdict[:500],
            dump_json(list(report.findings)),
        )

    async def latest_drift(self) -> dict[str, Any] | None:
        """Pemeriksaan drift terakhir, atau ``None`` kalau belum pernah ada.

        ``record_drift`` sudah ada sejak Phase 10 dan **tidak punya pembaca**
        sampai baris ini. Sebuah tabel yang hanya ditulis tidak pernah menjawab
        pertanyaan apa pun - dan drift adalah pertanyaan yang paling perlu
        dijawab sebelum keputusan: apakah model yang memutuskan ini masih model
        yang pernah diuji.

        ``None`` berarti belum pernah diperiksa. Sebuah dict kosong akan
        terbaca seperti pemeriksaan yang hasilnya nol drift, dan keduanya
        berarti hal yang sangat berbeda.
        """
        rows = await self._db.fetch(
            """
            SELECT baseline_resolved, recent_resolved, performance_drift,
                   regime_shift, sufficient_sample, verdict, checked_at
            FROM drift_checks
            ORDER BY checked_at DESC, id DESC
            LIMIT 1
            """
        )
        return dict(rows[0]) if rows else None


__all__ = ["GovernanceRepository"]
