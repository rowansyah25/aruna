"""Backtest and replay storage (SPEC 35-39).

Both tables are written and never updated. `backtest_runs` carries a trigger
enforcing it; `replay_checks` accumulates by design, because the useful question
is whether reproduction rates change over time.

Every run also records the *cost basis* it was measured under - see
:func:`cost_basis`. A PnL figure is only meaningful beside the fee schedule and
the position size that produced it, and both change: crypto moved from 0.30% to
0.10% per side and from a 1,000,000 IDR notional to 1,000 USDT on 2026-08-17.
Remove the marker, or stop filtering on it in :meth:`BacktestRepository.recent_runs`,
and governance goes back to being handed whatever is newest regardless of the
world it was measured in. That is not hypothetical: it is how four research
questions arguing about a withdrawn 0.30% fee schedule came to be OPEN, quoting
rupiah figures in a system whose crypto notional is USDT (migration 0021).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aruna.backtest.replay import ReplayResult
from aruna.core.enums import Market
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.signals.paper import cost_model, default_capital


def cost_basis(market: Market) -> str:
    """Fingerprint of the assumptions a run's PnL was computed under.

    Not decoration. ``net_pnl`` is a number in the market's own quote currency,
    produced by one fee schedule against one notional, and the stored row keeps
    the result while keeping none of the assumptions. Two runs are comparable
    only when both match; without this string nothing in the record can tell
    them apart, so the newest row wins by being newest.

    Normalised through :class:`~decimal.Decimal` so that rewriting ``0.10`` as
    ``0.1`` reads as the same schedule. It is the same number, and treating it
    as a new regime would silently discard every stored run over a cosmetic
    edit.
    """
    model = cost_model(market)
    return (
        f"{market.value}:fee{_plain(model.taker_fee_pct)}/{_plain(model.sell_fee_pct)}"
        f":slip{_plain(model.slippage_bps)}"
        f":spread{int(model.charge_spread)}"
        f":cap{_plain(default_capital(market))}"
    )


def _plain(value: Decimal) -> str:
    """Exact decimal text with no exponent, so 1E+3 and 1000 read alike."""
    return format(value.normalize(), "f")


class BacktestRepository:
    def __init__(self, db: Database, *, model_version: str = "") -> None:
        self._db = db
        self._model_version = model_version

    async def record_backtest(self, run: Any) -> int:
        payload = run.to_dict()
        combined = payload["combined"]
        trades = combined.get("paper_trades") or {}
        span = _span(run)

        return await self._db.insert(
            """
            INSERT INTO backtest_runs
                (model_version, market_code, interval_code, period_start,
                 period_end, holdout_start, holdout_included, assets,
                 decisions_simulated, published, resolved, direction_correct,
                 net_pnl, gross_pnl, total_costs, walk_forward, per_asset,
                 known_optimism, cost_basis)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            """,
            self._model_version,
            run.market.value,
            run.interval.value,
            to_mysql_datetime(span[0]),
            to_mysql_datetime(span[1]),
            to_mysql_datetime(run.split.holdout_start) if run.split else None,
            bool(payload.get("holdout_included")),
            len(run.results),
            combined.get("decisions_simulated") or 0,
            combined.get("published") or 0,
            combined.get("resolved") or 0,
            combined.get("direction_correct") or 0,
            _money(trades.get("net_pnl")),
            _money(trades.get("gross_pnl")),
            _money(trades.get("total_costs")),
            dump_json(payload.get("walk_forward")),
            dump_json(payload.get("per_asset")),
            dump_json(combined.get("known_optimism") or []),
            # Written here, at the moment the figures are produced, because it
            # cannot be reconstructed afterwards: the fee schedule in force
            # today is the only one this process can see.
            cost_basis(run.market),
        )

    async def replayable(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Stored decisions that can be re-run from their own inputs.

        Only signals with a council session behind them: without the debate
        there is nothing to compare a replay against.
        """
        rows = await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.market_code, s.horizon_code, "
            "s.direction, s.confidence, s.as_of, s.locked_at, s.model_version "
            "FROM signal_snapshots s "
            "WHERE s.council_session_id IS NOT NULL "
            "ORDER BY s.locked_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["as_of"] = as_utc(row["as_of"])
            row["locked_at"] = as_utc(row["locked_at"])
        return rows

    async def validasi_terakhir(self) -> dict[str, Any] | None:
        """Status validasi model luring terakhir (PASAL 14.40), atau ``None``.

        **Kueri sendiri, bukan menumpang :meth:`recent_runs`.** Yang itu
        menyaring rezim biaya dengan sengaja - governance memakainya untuk
        mengangkat pertanyaan riset, dan pertanyaan yang lahir dari skema biaya
        yang sudah ditarik memperdebatkan dunia yang tidak ada lagi. Kolomnya
        pun hanya PnL: ``walk_forward`` dan ``holdout_included`` tidak ada di
        sana sama sekali.

        Pertanyaan di sini berbeda: **pernahkah model ini divalidasi**. Itu
        tidak bergantung pada rezim biaya, dan menumpang kueri yang menyaringnya
        akan membuat validasi menghilang tiap kali ongkos berubah.

        Yang dipulangkan hanya status - bukan angkanya. Net PnL backtest yang
        sampai ke jalur keputusan akan terbaca sebagai perkiraan hasil rencana
        yang sedang disusun, dan ia bukan itu.
        """
        row = await self._db.fetchrow(
            "SELECT walk_forward, holdout_included, ran_at, "
            "       decisions_simulated, market_code, interval_code "
            "FROM backtest_runs ORDER BY ran_at DESC LIMIT 1"
        )
        if not row:
            return None
        return {
            "walk_forward": load_json(row.get("walk_forward")),
            "holdout_included": bool(row.get("holdout_included")),
            "ran_at": as_utc(row.get("ran_at")),
            "decisions_simulated": int(row.get("decisions_simulated") or 0),
            "market_code": row.get("market_code"),
            "interval_code": row.get("interval_code"),
        }

    async def recent_runs(self, *, limit: int = 5) -> list[dict[str, Any]]:
        """Recent runs *of the current cost regime*, shaped for research (SPEC 31).

        The filter is the point of this method. Governance turns these rows into
        research questions, and a question derived from a fee schedule that has
        been withdrawn argues about a world that no longer exists - it names a
        cause that stopped applying (SPEC 49) and sends a proposal after it.
        Take the ``WHERE`` away and the newest rows win by being newest, which
        is exactly how ``costs_exceed_edge_1d`` sat OPEN on a 0.30% ratio while
        crypto charges 0.10%.

        Runs written before the marker existed carry an empty ``cost_basis`` and
        match nothing here, deliberately: "we cannot tell what this was measured
        under" has to resolve to silence rather than to a guess.

        The cost of the filter, stated rather than hidden: after a fee change the
        list is empty until new backtests are run, and empty means *not yet
        measured*, not *nothing wrong*.
        """
        pairs = [(market.value, cost_basis(market)) for market in Market]
        placeholders = ", ".join(["(%s, %s)"] * len(pairs))
        params: list[Any] = [value for pair in pairs for value in pair]
        rows = await self._db.fetch(
            "SELECT interval_code, market_code, decisions_simulated, published, "
            "resolved, direction_correct, net_pnl, gross_pnl, total_costs, "
            "cost_basis FROM backtest_runs "
            f"WHERE (market_code, cost_basis) IN ({placeholders}) "
            "ORDER BY ran_at DESC LIMIT %s",
            *params,
            limit,
        )
        runs: list[dict[str, Any]] = []
        for row in rows:
            gross = Decimal(row["gross_pnl"] or 0)
            costs = Decimal(row["total_costs"] or 0)
            resolved = int(row["resolved"] or 0)
            runs.append(
                {
                    "interval": row["interval_code"],
                    "market": row["market_code"],
                    # Carried into the run dict, not just used as a filter, so
                    # a question derived downstream can state which world its
                    # figures came from instead of quoting a bare number.
                    "cost_basis": row["cost_basis"],
                    "combined": {
                        "decisions_simulated": int(row["decisions_simulated"] or 0),
                        "published": int(row["published"] or 0),
                        "resolved": resolved,
                        "direction_correct": int(row["direction_correct"] or 0),
                        "direction_accuracy": (
                            int(row["direction_correct"] or 0) / resolved
                            if resolved
                            else None
                        ),
                        "paper_trades": {
                            "trades": resolved,
                            "net_pnl": str(row["net_pnl"]),
                            "gross_pnl": str(gross),
                            "total_costs": str(costs),
                            "cost_ratio": (
                                float(costs / abs(gross)) if gross else None
                            ),
                        },
                    },
                }
            )
        return runs

    async def record_replay(self, result: ReplayResult) -> None:
        await self._db.execute(
            """
            INSERT INTO replay_checks (signal_id, status, divergences, unavailable)
            VALUES (%s, %s, %s, %s)
            """,
            result.signal_id,
            result.status,
            dump_json(
                [
                    {
                        "field": d.field,
                        "stored": str(d.stored),
                        "replayed": str(d.replayed),
                    }
                    for d in result.divergences
                ]
            ),
            (result.unavailable or None) if result.unavailable else None,
        )


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed figure must not lose the run
        return Decimal(0)


def _span(run: Any) -> tuple[Any, Any]:
    starts = [r.start for r in run.results if r.start]
    ends = [r.end for r in run.results if r.end]
    if not starts or not ends:
        # An empty run is still worth recording: "we tried and there was no
        # data" is a fact PHASE 10 may need.
        fallback = run.split.holdout_end if run.split else None
        return fallback, fallback
    return min(starts), max(ends)


__all__ = ["BacktestRepository", "cost_basis"]
