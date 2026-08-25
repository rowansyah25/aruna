"""The market-reading agents (SPEC 12).

TECHNICAL, STRUCTURE, MOMENTUM, VOLUME, REVERSAL, REGIME.

Each reads a narrow slice of the PHASE 3 evidence and forms its own view. The
narrowness is the point: SPEC 17 wants agents that can genuinely disagree
because they are looking at different things, not six views of one indicator.

**No agent here has a fixed stance.** REVERSAL is not a permanent bear - it
calls a reversal *upward* out of oversold just as readily as downward out of
overbought. Every agent can return BUY, SELL or WAIT (SPEC 12, 48).
"""

from __future__ import annotations

from aruna.agents.base import Agent, AgentOpinion, EvidenceRef, abstain, scale_confidence
from aruna.agents.context import DecisionContext
from aruna.analysis.structure import BreakoutState, TrendStructure
from aruna.core.enums import AgentRole, Decision, Regime


def _ref(context: DecisionContext, name: str) -> EvidenceRef | None:
    reading = context.reading(name)
    if reading is None:
        return None
    return EvidenceRef(
        key=f"reading:{name}",
        value=reading.value,
        sample_size=reading.sample_size,
        reliable=reading.reliable,
        detail=reading.detail,
    )


#: Score margin at which an agent is allowed to be fully confident. A lone
#: 1.0-weight signal is a hint, not certainty.
FULL_CONVICTION_MARGIN = 3.0

#: Peran yang membaca pasar dari harga dan volume saja - persis enam agen yang
#: modul ini definisikan.
#:
#: **Dipisahkan karena "berapa agen sepakat" harus menghitung agen yang SAMA di
#: korpus dan di produksi.** Roster lengkap berisi sembilan; tiga sisanya (NEWS,
#: FUNDAMENTAL, RISK) membaca bahan yang tidak tersedia point-in-time, jadi di
#: replay mereka praktis tidak pernah ikut menyetujui apa pun sementara di
#: produksi sesekali ikut. Ambang yang menghitung sembilan berarti dua hal
#: berbeda di dua tempat - dengan nama yang sama.
#:
#: Terukur 2026-08-25: dengan enam peran ini, ambang lima meloloskan porsi
#: keputusan yang sama di korpus dan di produksi (69% vs 69% pada ambang empat).
PERAN_PEMBACA_PASAR = frozenset(
    {
        AgentRole.TECHNICAL,
        AgentRole.STRUCTURE,
        AgentRole.MOMENTUM,
        AgentRole.VOLUME,
        AgentRole.REVERSAL,
        AgentRole.REGIME,
    }
)

#: Perubahan 10-bar, dalam persen, yang MOMENTUM sebut sebagai arah.
#:
#: **Angka ini tidak pernah diturunkan dari apa pun, dan letaknya bukan yang
#: dikira penulisnya.** Diukur 2026-08-25 atas 22.540 jendela di dua puluh lima
#: simbol 15m, sebaran ``momentum`` adalah::
#:
#:     p10 = -1,04    median = +0,06    p90 = +1,57
#:
#: Jadi 1,5 duduk **tepat di p90**. MOMENTUM hanya bersuara pada 16,7% kasus,
#: dan seluruhnya di ekor sebaran - bukan di "momentum yang jelas berarah"
#: seperti yang namanya sarankan.
#:
#: **Itu bertabrakan dengan keterangan agennya sendiri.** Komentar di
#: :class:`MomentumAgent` berbunyi *"the extremes are left to REVERSAL, which is
#: the agent that reads them as a turn"* - dan pada ambang ini MOMENTUM justru
#: beroperasi persis di ekor itu. Dua agen membaca wilayah yang sama dengan
#: tesis yang berlawanan: satu menyebutnya kelanjutan, satu menyebutnya
#: pembalikan.
#:
#: Yang terukur atas keputusan nyata (142.461 suara agen, dibelah menurut
#: waktu): REVERSAL satu-satunya agen yang keunggulannya BERTAHAN di paruh
#: kedua (+6,1 poin), sementara MOMENTUM ber-edge negatif di kedua sisi.
#:
#: **Kenapa ambangnya TIDAK diubah di sini.** Pola per pita diuji dua paruh dan
#: sebagian besar tidak bertahan - pita -3,0..-1,5 memberi +13,1 poin di paruh
#: pertama lalu -1,9 di paruh kedua. Sebabnya garis dasar pasar sendiri
#: bergeser 46,0% -> 62,3% antara kedua paruh; pada ayunan sebesar itu, aturan
#: apa pun yang diturunkan akan terpasang pada paruh mana yang kebetulan
#: bullish. Delapan hari korpus satu-regime tidak bisa menopang perubahan
#: logika agen.
#:
#: Yang dibutuhkan sebelum angka ini boleh disentuh: korpus yang memuat pasar
#: turun dan menyamping, bukan hanya naik. Sampai itu ada, satu-satunya
#: perubahan yang jujur adalah menuliskan apa yang sudah diketahui - dan itulah
#: catatan ini.
AMBANG_MOMENTUM = 1.5


def _decide(bull: float, bear: float, *, threshold: float = 1.0) -> tuple[Decision, float]:
    """Turn a bull/bear score into a decision and a raw confidence.

    A margin below ``threshold`` is WAIT: two roughly balanced sides are not a
    weak signal, they are an absence of one.

    Confidence blends two things that must both hold. *Agreement* is how
    one-sided the vote was; *conviction* is how much total weight was behind
    it. Using agreement alone made a single 1.0-weight signal read as 100%
    certain simply because nothing contradicted it - which is how an agent
    ended up reporting SELL at full confidence while its own reasoning said
    the swing sequence was mixed.
    """
    margin = bull - bear
    total = bull + bear
    if total <= 0 or abs(margin) < threshold:
        return Decision.WAIT, 0.0

    agreement = abs(margin) / total
    conviction = min(1.0, abs(margin) / FULL_CONVICTION_MARGIN)
    confidence = round(agreement * conviction, 3)
    return (Decision.BUY if margin > 0 else Decision.SELL), confidence


class TechnicalAgent(Agent):
    """Moving averages, MACD, and where price sits in its band."""

    role = AgentRole.TECHNICAL

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        if not context.has_technical:
            return abstain(self.role, "tidak ada pembacaan teknikal yang cukup andal")

        bull = bear = 0.0
        reasons: list[str] = []
        evidence: list[EvidenceRef] = []
        used = 0

        close = float(context.state.last_price)
        for fast, slow in (("ema_9", "ema_21"), ("sma_20", "sma_50")):
            fast_value = context.value(fast)
            slow_value = context.value(slow)
            if fast_value is None or slow_value is None:
                continue
            used += 1
            evidence += [r for r in (_ref(context, fast), _ref(context, slow)) if r]
            if fast_value > slow_value:
                bull += 1.0
                reasons.append(f"{fast} di atas {slow}")
            else:
                bear += 1.0
                reasons.append(f"{fast} di bawah {slow}")

        macd = context.reading("macd")
        if macd is not None and macd.reliable:
            used += 1
            ref = _ref(context, "macd")
            if ref:
                evidence.append(ref)
            histogram = macd.components.get("histogram", 0.0)
            previous = macd.components.get("previous_histogram", histogram)
            if histogram > 0:
                bull += 1.0
                reasons.append(f"MACD histogram positif ({histogram:+.4g})")
            else:
                bear += 1.0
                reasons.append(f"MACD histogram negatif ({histogram:+.4g})")
            if previous <= 0 < histogram:
                bull += 0.5
                reasons.append("MACD cross up pada bar ini")
            elif previous >= 0 > histogram:
                bear += 0.5
                reasons.append("MACD cross down pada bar ini")

        band = context.value("bollinger")
        if band is not None:
            used += 1
            ref = _ref(context, "bollinger")
            if ref:
                evidence.append(ref)
            if band > 1.0:
                bear += 0.5
                reasons.append("harga close di atas upper band")
            elif band < 0.0:
                bull += 0.5
                reasons.append("harga close di bawah lower band")

        vwap = context.value("vwap")
        if vwap is not None and vwap > 0:
            used += 1
            ref = _ref(context, "vwap")
            if ref:
                evidence.append(ref)
            if close > vwap:
                bull += 0.5
                reasons.append("harga bergerak di atas VWAP")
            else:
                bear += 0.5
                reasons.append("harga bergerak di bawah VWAP")

        if used < 2:
            return abstain(self.role, f"hanya {used} input teknikal yang bisa dipakai")

        decision, raw = _decide(bull, bear)
        if decision is Decision.WAIT:
            reasons.append("sisi bullish dan bearish dari teknikal sama kuat")

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=min(1.0, used / 4)),
            reasoning=tuple(reasons),
            evidence=tuple(evidence),
            horizon=context.interval,
        )


class StructureAgent(Agent):
    """Swing sequencing and level interaction."""

    role = AgentRole.STRUCTURE

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        structure = context.structure
        if structure is None or not structure.reliable:
            return abstain(
                self.role,
                "structure butuh minimal empat swing terkonfirmasi; "
                f"yang ada {structure.confirmed_swings if structure else 0}",
            )

        bull = bear = 0.0
        reasons: list[str] = []

        if structure.trend is TrendStructure.UPTREND:
            bull += 2.0
            reasons.append("higher high dan higher low")
        elif structure.trend is TrendStructure.DOWNTREND:
            bear += 2.0
            reasons.append("lower high dan lower low")
        else:
            reasons.append("urutan swing masih campur aduk")

        breakout_votes: dict[BreakoutState, tuple[float, float, str]] = {
            BreakoutState.BREAKOUT_UP: (2.0, 0.0, "break ke atas resistance"),
            BreakoutState.BREAKOUT_DOWN: (0.0, 2.0, "break ke bawah support"),
            # A failed break is evidence for the other side, not a weaker
            # version of the same one.
            BreakoutState.FALSE_BREAKOUT_UP: (0.0, 1.5, "gagal break ke atas resistance"),
            BreakoutState.FALSE_BREAKOUT_DOWN: (1.5, 0.0, "gagal break ke bawah support"),
            BreakoutState.REJECTION: (0.0, 1.0, "ditolak di resistance"),
            BreakoutState.RETEST: (1.0, 0.0, "retest support dari atas"),
        }
        vote = breakout_votes.get(structure.breakout)
        if vote:
            bull += vote[0]
            bear += vote[1]
            reasons.append(vote[2])

        decision, raw = _decide(bull, bear)
        if decision is Decision.WAIT:
            reasons.append("structure tidak memihak sisi mana pun")

        quality = min(1.0, structure.confirmed_swings / 8)
        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=quality),
            reasoning=tuple(reasons),
            evidence=(
                EvidenceRef(
                    key="structure",
                    sample_size=structure.confirmed_swings,
                    reliable=structure.reliable,
                    detail=f"{structure.trend.value}/{structure.breakout.value}",
                ),
            ),
            horizon=context.interval,
        )


class MomentumAgent(Agent):
    """Rate of change and RSI positioning.

    Tesisnya **kelanjutan**: gerak kuat berlanjut ke arah yang sama. Ambangnya
    ternyata duduk di p90 sebaran, jadi tesis itu diterapkan justru di ekor -
    wilayah yang docstring di bawah serahkan ke REVERSAL, yang tesisnya
    berlawanan. Lihat :data:`AMBANG_MOMENTUM` untuk angkanya dan untuk kenapa
    ia belum diubah.
    """

    role = AgentRole.MOMENTUM

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        move = context.value("momentum")
        rsi = context.value("rsi")
        if move is None and rsi is None:
            return abstain(self.role, "tidak ada pembacaan momentum maupun RSI")

        bull = bear = 0.0
        reasons: list[str] = []
        evidence = [r for r in (_ref(context, "momentum"), _ref(context, "rsi")) if r]

        if move is not None:
            if move >= AMBANG_MOMENTUM:
                bull += 1.5
                reasons.append(f"{move:+.2f}% sepanjang window momentum")
            elif move <= -AMBANG_MOMENTUM:
                bear += 1.5
                reasons.append(f"{move:+.2f}% sepanjang window momentum")
            else:
                reasons.append(f"momentum datar di {move:+.2f}%")

        if rsi is not None:
            # Mid-range RSI carries directional information; the extremes are
            # left to REVERSAL, which is the agent that reads them as a turn.
            if 55 <= rsi < 70:
                bull += 1.0
                reasons.append(f"RSI {rsi:.0f} memihak buyer")
            elif 30 < rsi <= 45:
                bear += 1.0
                reasons.append(f"RSI {rsi:.0f} memihak seller")
            else:
                reasons.append(f"RSI {rsi:.0f} tidak menunjukkan arah di sini")

        decision, raw = _decide(bull, bear)
        if decision is Decision.WAIT:
            reasons.append("momentum tidak menentukan apa pun")

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=len(evidence) / 2),
            reasoning=tuple(reasons),
            evidence=tuple(evidence),
            horizon=context.interval,
        )


class VolumeAgent(Agent):
    """Participation: does volume confirm the move, or contradict it?"""

    role = AgentRole.VOLUME

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        ratio = context.value("volume_anomaly")
        if ratio is None:
            return abstain(self.role, "tidak ada riwayat volume yang bisa dipakai")

        move = context.value("momentum") or 0.0
        evidence = [
            r
            for r in (_ref(context, "volume_anomaly"), _ref(context, "volume_trend"))
            if r
        ]
        bull = bear = 0.0
        reasons: list[str] = []

        if ratio >= 2.0:
            # Heavy volume confirms whichever way price is going.
            if move > 0.3:
                bull += 2.0
                reasons.append(f"volume {ratio:.1f}x rata-rata mengonfirmasi kenaikan")
            elif move < -0.3:
                bear += 2.0
                reasons.append(f"volume {ratio:.1f}x rata-rata mengonfirmasi penurunan")
            else:
                reasons.append(f"volume {ratio:.1f}x rata-rata tapi harga tidak berarah")
        elif ratio <= 0.5:
            # A move on thin volume is weakly supported whichever way it goes.
            if move > 0.3:
                bear += 1.0
                reasons.append(f"kenaikan dengan volume tipis ({ratio:.2f}x)")
            elif move < -0.3:
                bull += 1.0
                reasons.append(f"penurunan dengan volume tipis ({ratio:.2f}x)")
            else:
                reasons.append(f"volume tipis tidak wajar ({ratio:.2f}x)")
        else:
            reasons.append(f"volume normal ({ratio:.2f}x rata-rata)")

        decision, raw = _decide(bull, bear)
        if decision is Decision.WAIT:
            reasons.append("volume tidak mengonfirmasi arah mana pun")

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=min(1.0, len(evidence) / 2)),
            reasoning=tuple(reasons),
            evidence=tuple(evidence),
            horizon=context.interval,
        )


class ReversalAgent(Agent):
    """Looks for exhaustion and turns - in either direction.

    Not a permanent bear (SPEC 12, 48): oversold plus a failed break below
    support is a bullish reversal, and this agent calls it as readily as the
    bearish case.
    """

    role = AgentRole.REVERSAL

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        rsi = context.value("rsi")
        band = context.value("bollinger")
        structure = context.structure
        if rsi is None and band is None and structure is None:
            return abstain(self.role, "tidak ada bukti exhaustion yang bisa dibaca")

        bull = bear = 0.0
        reasons: list[str] = []
        evidence = [r for r in (_ref(context, "rsi"), _ref(context, "bollinger")) if r]

        if rsi is not None:
            if rsi >= 70:
                bear += 1.5
                reasons.append(f"RSI {rsi:.0f} overbought - peluang berbalik turun lebih besar")
            elif rsi <= 30:
                bull += 1.5
                reasons.append(f"RSI {rsi:.0f} oversold - peluang berbalik naik lebih besar")

        if band is not None:
            if band > 1.0:
                bear += 1.0
                reasons.append("close di luar upper band")
            elif band < 0.0:
                bull += 1.0
                reasons.append("close di luar lower band")

        if structure is not None and structure.reliable:
            if structure.breakout is BreakoutState.FALSE_BREAKOUT_UP:
                bear += 1.5
                reasons.append("false break di atas resistance")
            elif structure.breakout is BreakoutState.FALSE_BREAKOUT_DOWN:
                bull += 1.5
                reasons.append("false break di bawah support")
            elif structure.breakout is BreakoutState.REJECTION:
                bear += 1.0
                reasons.append("ditolak di resistance")

        if not reasons:
            return abstain(self.role, "tidak ada tanda exhaustion ke arah mana pun")

        decision, raw = _decide(bull, bear)
        if decision is Decision.WAIT:
            reasons.append("sinyal exhaustion menunjuk ke dua arah sekaligus")

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=0.8),
            reasoning=tuple(reasons),
            evidence=tuple(evidence),
            horizon=context.interval,
        )


class RegimeAgent(Agent):
    """Reads the SPEC 9 regime and what it implies for taking a position."""

    role = AgentRole.REGIME

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        verdict = context.regime
        if verdict is None:
            return abstain(self.role, "tidak ada klasifikasi regime yang tersedia")

        if verdict.regime in (Regime.UNCERTAIN, Regime.ANOMALY):
            return AgentOpinion(
                role=self.role,
                decision=Decision.WAIT,
                confidence=0.0,
                reasoning=(
                    f"regime {verdict.regime.value}; "
                    "masuk posisi di market yang tidak terbaca tidak memberi edge",
                    *verdict.reasons,
                ),
                evidence=(
                    EvidenceRef(
                        key="regime",
                        value=verdict.confidence,
                        sample_size=verdict.evidence_used,
                        reliable=verdict.evidence_used > 0,
                        detail=verdict.regime.value,
                    ),
                ),
                horizon=context.interval,
            )

        structure = context.structure
        trend = structure.trend if structure else TrendStructure.UNDETERMINED
        bull = bear = 0.0
        reasons: list[str] = [f"regime {verdict.regime.value}"]

        if verdict.regime in (Regime.TRENDING_BULLISH, Regime.TRENDING_BEARISH):
            # **Cabang ini hilang selama taksonomi berarah sudah jalan.**
            # `Regime.TRENDING` pensiun ketika regime tren dipecah menurut
            # arahnya (lihat catatan di :class:`~aruna.core.enums.Regime`), tapi
            # agen ini tidak ikut diperbarui - jadi ia menguji `is
            # Regime.TRENDING`, tidak pernah cocok, dan jatuh ke WAIT.
            #
            # Terukur 2026-08-25: sejak taksonomi itu masuk, 26,8% keputusan
            # produksi mendarat di regime yang agen ini tidak bisa baca
            # (TRENDING_BULLISH 19,1%, TRENDING_BEARISH 6,3%, BREAKDOWN 1,4%),
            # dan cabang `TRENDING` cocok 0%. Di replay 9.805 keputusan, REGIME
            # bilang SELL NOL kali - bukan sebaran yang timpang, melainkan jalur
            # yang tidak bisa dilewati.
            #
            # Bekasnya condong panjang: di produksi REGIME bilang BUY 30,7% dan
            # SELL 3,7%, karena satu-satunya jalur bearish yang tersisa lewat
            # `BREAKOUT` - yang kini berarti "ke atas" saja.
            naik = verdict.regime is Regime.TRENDING_BULLISH
            # Structure boleh MEMBATALKAN, tidak boleh membalik. Labelnya sudah
            # membawa arah, jadi structure tidak lagi diperlukan untuk
            # menemukannya - tapi structure yang berlawanan berarti dua bacaan
            # bertentangan, dan itu bukan edge. Sama hasilnya dengan `TRENDING`
            # yang structure-nya belum punya arah, di cabang berikutnya.
            lawan = TrendStructure.DOWNTREND if naik else TrendStructure.UPTREND
            if trend is lawan:
                reasons.append(
                    f"regime {verdict.regime.value} tapi structure mengarah "
                    f"{trend.value} - dua bacaan yang bertentangan"
                )
            elif naik:
                bull += 2.0
                reasons.append("regime tren naik")
            else:
                bear += 2.0
                reasons.append("regime tren turun")
        elif verdict.regime is Regime.TRENDING:
            # Tren tanpa arah. Classifier tidak menghasilkannya lagi, tapi baris
            # lama memuatnya dan masih dibaca ulang - lihat `Regime.TRENDING`.
            if trend is TrendStructure.UPTREND:
                bull += 2.0
                reasons.append("regime trend dengan structure mengarah naik")
            elif trend is TrendStructure.DOWNTREND:
                bear += 2.0
                reasons.append("regime trend dengan structure mengarah turun")
            else:
                reasons.append("sedang trending, tapi structure belum punya arah")
        elif verdict.regime in (Regime.BREAKOUT, Regime.BREAKDOWN):
            # `BREAKDOWN` dipisah dari `BREAKOUT` di taksonomi yang sama, dan
            # ikut tertinggal - tanpa cabang sama sekali, bukan cuma tanpa arah.
            # Ia diberi perlakuan yang PERSIS sama dengan kembarannya: arahnya
            # tetap dari structure. Membiarkan label regime yang menentukan di
            # sini akan sekalian menggeser ambang `BREAKOUT` yang sedang bekerja,
            # dan itu perubahan lain yang belum diukur.
            breakout = structure.breakout if structure else BreakoutState.NONE
            if breakout is BreakoutState.BREAKOUT_UP:
                bull += 1.5
                reasons.append("breakout ke atas")
            elif breakout is BreakoutState.BREAKOUT_DOWN:
                bear += 1.5
                reasons.append("breakout ke bawah")
        elif verdict.regime in (Regime.RANGING, Regime.LOW_VOLATILITY):
            reasons.append("kondisi range - regime tidak memberi edge arah")
        elif verdict.regime is Regime.HIGH_VOLATILITY:
            reasons.append("volatilitas tinggi - position size lebih menentukan daripada arah")
        elif verdict.regime is Regime.ACCUMULATION:
            bull += 1.0
            reasons.append("pola accumulation")
        elif verdict.regime is Regime.DISTRIBUTION:
            bear += 1.0
            reasons.append("pola distribution")
        elif verdict.regime is Regime.REVERSAL:
            reasons.append("regime reversal - arah tergantung ke mana harga berbalik")

        decision, raw = _decide(bull, bear)
        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=scale_confidence(raw, evidence_quality=verdict.confidence),
            reasoning=tuple(reasons),
            evidence=(
                EvidenceRef(
                    key="regime",
                    value=verdict.confidence,
                    sample_size=verdict.evidence_used,
                    reliable=verdict.evidence_used > 0,
                    detail=verdict.regime.value,
                ),
            ),
            horizon=context.interval,
        )


__all__ = [
    "PERAN_PEMBACA_PASAR",
    "MomentumAgent",
    "RegimeAgent",
    "ReversalAgent",
    "StructureAgent",
    "TechnicalAgent",
    "VolumeAgent",
]
