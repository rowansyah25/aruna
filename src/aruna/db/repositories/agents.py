"""Deliberation storage (SPEC 39, 42).

Every agent opinion is kept with the evidence it cited, because SPEC 39
requires a decision to be replayable showing only what was known at the time -
and that includes which agents were leaning on the same input.
"""

from __future__ import annotations

from typing import Any

from aruna.agents.deliberation import Deliberation
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime


class DeliberationRepository:
    def __init__(self, db: Database, *, phase: int) -> None:
        self._db = db
        self._phase = phase

    async def save(self, asset_id: int, result: Deliberation) -> int:
        critique = result.critique
        deliberation_id = await self._db.insert(
            """
            INSERT INTO deliberations
                (asset_id, market_code, symbol, interval_code, as_of, decided_at,
                 outcome, confidence, independence, participating_agents,
                 total_agents, proposal_decision, proposal_confidence,
                 prosecutor_decision, prosecutor_confidence, reassessment_required,
                 risk_level, blocked, no_trade_reasons, risk_factors, critique,
                 notes, is_council_decision, phase)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                id                    = LAST_INSERT_ID(deliberations.id),
                decided_at            = new.decided_at,
                outcome               = new.outcome,
                confidence            = new.confidence,
                independence          = new.independence,
                participating_agents  = new.participating_agents,
                total_agents          = new.total_agents,
                proposal_decision     = new.proposal_decision,
                proposal_confidence   = new.proposal_confidence,
                prosecutor_decision   = new.prosecutor_decision,
                prosecutor_confidence = new.prosecutor_confidence,
                reassessment_required = new.reassessment_required,
                risk_level            = new.risk_level,
                blocked               = new.blocked,
                no_trade_reasons      = new.no_trade_reasons,
                risk_factors          = new.risk_factors,
                critique              = new.critique,
                notes                 = new.notes
            """,
            asset_id,
            result.market,
            result.symbol,
            result.interval,
            to_mysql_datetime(result.as_of),
            to_mysql_datetime(result.decided_at),
            result.outcome.value,
            round(result.confidence, 3),
            round(result.independence, 3),
            result.participating,
            len(result.opinions),
            result.proposal.decision.value,
            round(result.proposal.confidence, 3),
            result.prosecutor.decision.value,
            round(result.prosecutor.confidence, 3),
            critique.reassessment_required,
            result.risk.overall.value,
            result.no_trade.blocked,
            dump_json([r.value for r in result.no_trade.reasons]),
            dump_json(result.risk.to_dict()),
            dump_json(critique.to_dict()),
            dump_json(list(result.notes)),
            False,
            self._phase,
        )

        # Replace the opinion set: a recomputed deliberation must not leave
        # stale votes from a previous run attached to it.
        await self._db.execute(
            "DELETE FROM agent_decisions WHERE deliberation_id = %s", deliberation_id
        )
        for opinion in result.opinions:
            await self._db.execute(
                """
                INSERT INTO agent_decisions
                    (deliberation_id, role, decision, confidence, abstained,
                     horizon_code, reasoning, evidence, evidence_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                deliberation_id,
                opinion.role.value,
                opinion.decision.value,
                round(opinion.confidence, 3),
                opinion.abstained,
                opinion.horizon.value if opinion.horizon else None,
                dump_json(list(opinion.reasoning)),
                dump_json([e.to_dict() for e in opinion.evidence]),
                len(opinion.evidence),
            )
        return deliberation_id

    async def latest(self, asset_id: int, interval: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT id, as_of, decided_at, outcome, confidence, independence, "
            "participating_agents, total_agents, risk_level, blocked, "
            "no_trade_reasons, notes FROM deliberations "
            "WHERE asset_id = %s AND interval_code = %s ORDER BY as_of DESC LIMIT 1",
            asset_id,
            interval,
        )
        if row:
            row["as_of"] = as_utc(row["as_of"])
            row["decided_at"] = as_utc(row["decided_at"])
            row["no_trade_reasons"] = load_json(row["no_trade_reasons"])
            row["notes"] = load_json(row["notes"])
        return row

    async def opinions_for(self, deliberation_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT role, decision, confidence, abstained, reasoning, evidence_count "
            "FROM agent_decisions WHERE deliberation_id = %s ORDER BY role",
            deliberation_id,
        )
        for row in rows:
            row["reasoning"] = load_json(row["reasoning"])
        return rows

    async def agent_stance_counts(self) -> list[dict[str, Any]]:
        """Per role, how often it reached each decision.

        SPEC 12 and 48 forbid a permanently biased agent. This is the query
        that would expose one in live data.
        """
        return await self._db.fetch(
            "SELECT role, decision, count(*) AS n FROM agent_decisions "
            "GROUP BY role, decision ORDER BY role, decision"
        )


__all__ = ["DeliberationRepository"]
