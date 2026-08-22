# FUTURES F1 — Market data foundation

> **SUPERSEDED IN PART — dated correction, 2026-08-17.**
>
> Everything below is preserved exactly as written and is **still a true record
> of what was measured on the day it was written**. It is not edited to match
> today, because a measurement rewritten after the fact stops being a
> measurement (SPEC 49).
>
> What changed since:
>
> - ARUNA's crypto source is no longer Indodax. It is **Binance spot, USDT
>   pairs only** (PASAL 5, 6, 33). The Indodax client, config, symbol mapping
>   and environment variables were removed entirely; there is no fallback.
> - Crypto pairs are quoted **USDT**, not IDR. Any `BTC/IDR` in this document
>   is a symbol that no longer exists in the universe.
> - Statements here that Binance is unreachable from Indonesian networks, or
>   blocked by Kominfo TrustPositif, **were true as measured then and are not
>   true on this machine now**: on 2026-08-17 `api.binance.com`, `api1`, `api2`
>   and `fapi.binance.com` each answered. Reachability is a property of the
>   network a deployment sits on. Binance is still not registered with
>   Bappebti — that part was never a network claim and has not changed.
> - Crypto paper-trading costs moved from 0.30% to 0.10% per side with the
>   venue. **PnL figures in this document are not comparable to newer ones.**
> - `market_ticks` was dropped by migration 0020 (PASAL 26).
> - The IDR-quoted crypto history this report describes — candles, snapshots,
>   signals, paper trades and council sessions — was deleted by migration 0020
>   at the operator's decision. Backup: `backup/aruna_sebelum_binance_2026-08-17.sql`.
>
> See `README.md` and `migrations/0020_crypto_usdt_binance.sql` for the
> current state.

**844 tests passing (+27) · ruff clean · `src/aruna/futures/` created**

Covers FUTURES SPEC 2 (data fields), 3 (read-only), 4 (freshness), 5
(connectivity), 29 (order book), 46 (data integrity), and the parts of 52 that
depend on them.

---

> **Correction, 2026-08-15.** The measurement below was real on the day it was
> taken and is no longer true. Every venue in that table now answers normally
> from this machine, with ordinary IPs and valid certificates, and the adapter
> pulls live data. **Reachability is a property of the network, not of the host**
> — this report presented it as the latter. What has not changed: Binance is not
> registered with Bappebti, and whether that matters is a question about the
> deployment's jurisdiction (FUTURES SPEC 5).
>
> Connecting for the first time exposed four defects in the code below, all
> fixed and recorded in `FUTURES_F4_REPORT.md`.

## The blocker, measured

FUTURES SPEC 2 names Binance Futures as the primary source. From this machine:

```
binance-futures    202.3.218.139   SSL CERTIFICATE_VERIFY_FAILED
binance-spot       202.3.218.139   SSL CERTIFICATE_VERIFY_FAILED
bybit              202.3.218.139   SSL CERTIFICATE_VERIFY_FAILED
okx                202.3.218.139   SSL CERTIFICATE_VERIFY_FAILED
bitget             202.3.218.139   SSL CERTIFICATE_VERIFY_FAILED
indodax            104.18.247.104  HTTP 200
```

Every offshore derivatives venue resolves to the same address — Kominfo
TrustPositif — and TLS fails against a Telkomsel certificate. Only Indodax is
reachable, and **Indodax lists no perpetual futures**, so there is no local
substitute for funding, open interest, mark price or perpetual order book data.

The adapter is built for a deployment where Binance is reachable and permitted.
Here it reports the block and produces nothing. ARUNA does not route around it
(FUTURES SPEC 5).

## What was built

| File | Lines | Covers |
|---|---|---|
| `futures/models.py` | 380 | Mark/index/basis, funding, OI, liquidations, long-short, contract spec with margin brackets, bundled snapshot |
| `futures/binance.py` | 430 | USD-M market data, read-only by construction |
| `futures/orderbook.py` | 210 | Depth, sweep cost, imbalance, liquidity verdict |
| `futures/integrity.py` | 190 | FUTURES SPEC 46 cross-input coherence |
| `tests/test_futures_data.py` | 380 | 27 tests |

**Perpetuals are CRYPTO instruments, not a third market.** ARUNA has two markets
and the database enforces it. A perpetual carries an instrument *type*, so the
existing invariant and its test survive untouched.

## FUTURES SPEC 3 — read-only, structurally

Not a promise in a docstring. Four independent guarantees:

- every request passes an allowlist of twelve market-data paths;
- the module contains no `/order`, `/leverage`, `/marginType`, `/account`,
  `/transfer` or `/withdraw` string anywhere outside the docstring that explains
  their absence — asserted by reading its own source;
- nothing signs a request: no `hmac`, no `signature`, no API secret, so an
  authenticated endpoint is uncallable even if a path slipped through;
- the class exposes no `place_order`, `cancel_order`, `set_leverage`,
  `set_margin_mode`, `transfer` or `withdraw` method.

Verified live: `_get("/fapi/v1/order")` raises before any network call.

## FUTURES SPEC 46 — the failure neither number reveals

Spot needed one question: is this price fresh? A perpetual needs a harder one —
**are these six inputs describing the same moment?** A mark price from two
seconds ago and an open-interest reading from twenty minutes ago describe a
market that never existed, and both look perfectly valid alone.

Three ways the set is refused: `INCOMPLETE` (an input never arrived, and nothing
was substituted), `STALE` (an input is older than the market it claims to
describe), `SKEWED` (individually fresh, collectively incoherent).

There is no "degraded but usable" state. A leveraged position sized from stale
liquidity is not degraded, it is wrong.

Funding is exempt from the skew test and has its own age limit: it settles every
eight hours, so judging it by a mark price's clock would block every signal ever
produced.

## FUTURES SPEC 29 — depth, not just spread

Spot priced a spread from the touch. A leveraged position is sized *before* the
fill, so what matters is `sweep_cost_pct` — what filling a given notional would
actually cost, walking the book level by level.

An order larger than the visible book returns `None`, not an extrapolation. What
happens past the last level is unknown, and a number invented there is exactly
what would make a size look tradeable when it is not.

## Live behaviour, verified

```
probe            reachable=False, TLS hostname mismatch
snapshot         complete=False, missing all five inputs
integrity        INCOMPLETE -> blocks_signal=True
place an order   refused before any network call
```

## What this does not do yet

- **No signal.** F1 is data only: no council, no leverage, no position size.
- **No liquidation feed.** Binance withdrew the REST endpoint; cascade analysis
  (SPEC 26) needs the `forceOrder` websocket. `liquidations()` returns empty and
  says why rather than raising or inventing.
- **Funding is a forecast** until it settles, and `cost_pct` assumes the current
  rate persists — stated on the method, because it will not.
- **Order book is visible resting orders only.** Hidden liquidity and depth that
  vanishes before an order arrives are not in it.
- **Nothing is stored.** No futures schema yet; F1 ends at the provider and the
  integrity gate.

## The thing to keep in view

ARUNA's spot rules were measured at **50% direction accuracy over 580 daily
predictions** — a coin flip. This module adds leverage to the same council,
reading the same indicators. Leverage multiplies whatever edge exists; on a zero
edge it multiplies the losses and adds liquidation risk that spot does not have.

The measurement machinery being reused here will say the same thing about
futures, and it will say it before any money is involved. That is the reason to
reuse it rather than start fresh.

---

## Next: F2

Funding analysis (SPEC 27), open interest (SPEC 28) and the futures regimes
(SPEC 6: liquidation-driven, news-driven) — the first layer that turns these
records into evidence a council can weigh.
