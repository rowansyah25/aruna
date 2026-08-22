# Findings

## 2026-08-20 — "signal dikirim ke tele gaada resultnya, hilang semua"

Dua celah terpisah, dan keluhan operator menyentuh keduanya. Ditemukan lewat
akar-sebab-dulu: tidak ada perbaikan sebelum buktinya lengkap.

**1. Futures: hasilnya tidak punya jalur kirim sama sekali.** `PlanNotifier`
punya tepat dua metode — `announce` dan `daily`. Tidak ada metode hasil.
Rencana dikabarkan, horizonnya lewat, hasilnya diskor dan disimpan ke
`futures_plan_results`, dicatat ke log — dan operator tidak pernah tahu
bagaimana akhirnya. Bukan yang rusak; yang **tidak pernah dibangun**.

**2. Spot: semuanya diblokir satu bidang.** 521 signal ditahan gerbang
`not_actionable`, dan buktinya seragam sempurna — 521 karena `stop`, nol karena
bidang lain. Rantainya:

```
tidak ada stop → tidak dikirim → pushed_at tidak pernah ditulis
               → hasilnya dibungkam (87 baris)
```

Database membenarkan: 675 signal terbit, **nol** yang tercatat pernah didorong.
Dan `results_announced` tidak pernah melebihi nol di berkas log mana pun —
bukan cuma sekarang, tapi tidak pernah sama sekali.

Celah kedua adalah akibat keputusan operator sendiri ("biarkan spot hening"
soal stop-loss). Yang tidak disebutkan siapa pun waktu itu: keputusan itu ikut
membawa serta seluruh result. Rantainya tidak terlihat sampai keluhan ini.

**Yang diperbaiki:** celah pertama — migrasi 0029 (`futures_plans.pushed_at`,
`telegram_message_id`), `PlanNotifier.results()`, dan penyambungannya ke
penjadwal. Celah kedua menunggu keputusan operator, karena membalikkannya
berarti membalikkan pilihan mereka sendiri.

---

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

What has actually been measured, and what was done about it. Kept separate from
the phase reports because it outlives them: phases describe what was built,
this describes what the built thing found out.

---

## 1. The rules have no measured directional edge

**1d horizon, 580 published predictions, April 2025 – April 2026:**

| | |
|---|---|
| direction accuracy | **50%** |
| walk-forward, 3 folds | 54% / 46% / 50% |
| net PnL | −2,953,814 IDR |
| cost ratio | 3.67 |

A coin flip, consistently, across a year. No execution change, cost reduction or
exit rule rescues a zero edge — everything below is about how much less it
loses, not about making it profitable.

## 2. At 1h it was arithmetically impossible to win

Measured before the fix:

```
round-trip cost      0.70%   (0.30 entry + 0.30 exit + 0.10 slippage)
average 1h bar move  0.16% – 0.24%
average ARUNA target 0.398%
```

The target was smaller than the cost. Every 1h prediction lost money **even
when its direction was right and its target was hit exactly**. Over 218
predictions the gross edge was +11,503 IDR and costs were 1,526,063 — 99.2% of
the loss was fees and slippage, not bad forecasting.

**Fixed.** `covers_costs` in `signals/lock.py` withholds any signal whose target
cannot cover the round trip, spread included. This is a correctness fix, not a
strategy choice: publishing a prediction that loses when correct is misleading.

Effect on the same 1h period:

| | before | after |
|---|---|---|
| published | 218 | 111 |
| net PnL | −1,514,560 | −817,179 |
| cost ratio | 132.7 | 19.4 |

BTC/IDR is the clearest case: 49 directional calls, 1 published, 48 withheld —
it is the least volatile asset in the universe, so its targets almost never
clear the fees.

Still a loss. The floor is a *necessary* condition, never a sufficient one.

## 3. Exiting at the target makes it worse — hypothesis rejected

The obvious next fix looked compelling: at 1d the average target is 3.57% and
realised gross is 0.19%, so the system appears to predict moves and then give
them back by holding to expiry.

Measured over the same 580 predictions, differing only in the exit rule:

| | hold to expiry | exit at target |
|---|---|---|
| direction accuracy | 50% | 50% |
| net PnL | −2,953,814 | **−3,417,354** |
| cost ratio | 3.67 | **6.33** |
| implied gross | ~1,105,000 | ~641,000 |

**Worse by 463,540 IDR — the gross edge fell 42%.** Taking profit at the target
caps the winners while the losers still run to expiry. With no stop-loss, that
asymmetry is strictly harmful.

The hypothesis was mine, it was plausible, and the measurement rejected it. The
variant stays available as `aruna backtest --exit-at-target`; it is **not** the
live rule.

This also exposed a gap in the validation itself: a change to exit rules moves
money without moving direction accuracy by a single point, so the comparison
reported "no effect" about the wrong quantity. `validate()` now reports the PnL
difference alongside — reported, not scored, because PnL over a few hundred
overlapping trades is far too noisy to carry a change through the gate.

## 4. Cutting losers helps; capping winners hurts more

Finding 3 pointed at the symmetric test. All four exit rules, measured over the
same 580 predictions with only the exit rule changed:

| exit rule | net PnL | cost ratio | vs live |
|---|---|---|---|
| hold to expiry (**live**) | −2,953,814 | 3.67 | — |
| take profit only | −3,417,354 | 6.33 | −463,540 |
| **stop-loss only** | **−2,784,903** | **3.19** | **+168,911** |
| both (symmetric) | −3,248,443 | 5.01 | −294,629 |

A coherent picture: cutting losers helps, capping winners hurts, and capping
winners hurts about three times as much as cutting losers helps. Stop-loss alone
— cut the losses, let the profits run — is the only variant that beats the live
rule.

**It is still not worth acting on, and the gate says so.** Direction accuracy is
unchanged at 50%, the improvement is 5.7% of a loss that remains a loss, and the
comparison was in-sample. Verdict: `NO_IMPROVEMENT`.

Two reasons for caution beyond the verdict. With 1:1 reward-to-risk at 50%
accuracy a stop should be roughly neutral, so an improvement most likely reflects
**truncating a fat left tail** — crypto drawdowns — rather than any skill in the
rules. And 580 daily predictions on five correlated assets over one year is
nowhere near 580 independent observations.

All three variants are available for further testing and **none is the live
rule**:

```bash
aruna backtest --interval 1d --stop-loss
aruna backtest --interval 1d --exit-at-target
aruna backtest --interval 1d --exit-at-target --stop-loss
```

## 5. What the record says to try next

Not tested, and stated as hypotheses rather than plans:

- **Stop-loss out of sample.** Finding 4 was measured in-sample. The reserved
  holdout exists precisely for this, and spending it needs a reason better than
  a 5.7% loss reduction.
- **Lower costs.** 0.70% round trip against a gross edge of 0.19% per trade at
  1d. Maker orders would help; they also introduce fill bias, which would need
  modelling rather than assuming.
- **A different edge entirely.** Findings 1–4 are all execution questions. At
  50% accuracy, execution is not the binding constraint, and no exit rule
  invented will make a coin flip profitable.

---

## Open proposals

| Key | Status | Verdict |
|---|---|---|
| `exit-at-target` | APPROVED (on fabricated demo figures) | NO_IMPROVEMENT when measured |
| `revert-exit-at-target` | DRAFT, awaiting a human | unvalidated |
| `stop-loss-only` | VALIDATED | NO_IMPROVEMENT — better on PnL, unchanged on accuracy, in-sample |

The first was approved during a gate demonstration on numbers supplied by hand,
before it had been measured. Its decision row is append-only and stays; the
proposal now carries the real `NO_IMPROVEMENT` verdict, so the discrepancy is
visible rather than hidden. The reversal is filed and **not** approved by ARUNA.
