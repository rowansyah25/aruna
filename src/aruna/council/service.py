"""Council service: assemble context, convene, store the record."""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.agents.service import DeliberationService
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.council.session import Council, CouncilVerdict
from aruna.db.repositories.council import CouncilRepository

log = get_logger("aruna.council.service")


@dataclass(slots=True)
class CouncilResult:
    convened: int = 0
    skipped: int = 0
    blocked: int = 0
    minority_wins: int = 0
    vetoes_upheld: int = 0
    vetoes_rejected: int = 0
    failures: list[str] = field(default_factory=list)
    verdicts: list[CouncilVerdict] = field(default_factory=list)
    decisions: dict[str, int] = field(default_factory=dict)

    def note(self, verdict: CouncilVerdict) -> None:
        self.verdicts.append(verdict)
        self.convened += 1
        key = verdict.decision.value
        self.decisions[key] = self.decisions.get(key, 0) + 1
        if verdict.no_trade.blocked or verdict.veto.blocks:
            self.blocked += 1
        if verdict.judgement.minority_prevailed:
            self.minority_wins += 1
        self.vetoes_upheld += len(verdict.veto.upheld)
        self.vetoes_rejected += len(verdict.veto.rejected)

    def summary(self) -> str:
        parts = [f"digelar={self.convened}", f"diblokir={self.blocked}"]
        if self.minority_wins:
            parts.append(f"minoritas_menang={self.minority_wins}")
        if self.vetoes_upheld or self.vetoes_rejected:
            parts.append(
                f"veto={self.vetoes_upheld} dikuatkan/{self.vetoes_rejected} ditolak"
            )
        if self.skipped:
            parts.append(f"dilewati={self.skipped}")
        if self.failures:
            parts.append(f"gagal={len(self.failures)}")
        return " ".join(parts)


class CouncilService:
    """Reuses the PHASE 5 context assembly, then convenes the full council."""

    def __init__(
        self,
        *,
        deliberation: DeliberationService,
        store: CouncilRepository,
        council: Council | None = None,
    ) -> None:
        self._deliberation = deliberation
        self._store = store
        self._council = council or Council()

    @property
    def council(self) -> Council:
        """The council as currently weighted.

        A property rather than a stored reference, because :meth:`use_history`
        replaces the instance. A caller that grabbed it once at construction
        would keep deliberating with the neutral weights forever, and would do
        so silently - every verdict would look normal.
        """
        return self._council

    def use_history(self, history) -> None:
        """Adopt measured SPEC 16 factors for every council from here on.

        Taken as a snapshot rather than a live lookup: weights that shifted
        between two decisions in the same run would make those decisions
        incomparable, and SPEC 39 requires a decision to replay identically.
        """
        self._council = Council(history=history)

    async def run(
        self,
        market: Market,
        interval: Horizon,
        *,
        symbols: tuple[str, ...] | None = None,
        trading_allowed: bool = True,
        persist: bool = True,
    ) -> CouncilResult:
        result = CouncilResult()
        assets = await self._deliberation.assets_for(market, symbols)
        if not assets:
            result.failures.append(f"tidak ada asset aktif untuk {market.value}")
            return result

        for asset in assets:
            try:
                context = await self._deliberation.build_context(
                    asset, market, interval, trading_allowed=trading_allowed
                )
            except ArunaError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one asset must not stop the rest
                result.failures.append(f"{asset.symbol}: {type(exc).__name__}: {exc}")
                continue

            if context is None:
                result.skipped += 1
                continue

            verdict = self._council.convene(context)
            result.note(verdict)
            if persist:
                await self._store.save(asset.id, verdict)
        return result


__all__ = ["CouncilResult", "CouncilService"]
