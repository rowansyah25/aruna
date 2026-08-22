"""Position size, risk budget and expected net PnL (FUTURES SPEC 19, 20, 23).

The rule this module exists to enforce is FUTURES SPEC 19: **position size is
computed from risk, not from leverage.** The two are routinely confused, and the
confusion is expensive - sizing from leverage means the loss when the stop is
hit depends on how much margin happened to be posted, which is backwards.

So :func:`position_size` takes no leverage argument. It cannot. The chain is:

    risk budget  ->  stop distance  ->  quantity  ->  notional  ->  leverage

Leverage is the *last* thing computed and it is an output, not an input. F4
turns that notional into a leverage recommendation; nothing here does.

FUTURES SPEC 16 adds the other half: a daily profit target must never enter this
chain. There is no ``target_profit`` parameter for the same reason there is no
``leverage`` one - a number that cannot be passed in cannot be chased.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any

from aruna.futures.models import ContractSpec, PositionSide

#: Default share of equity risked on one idea (FUTURES SPEC 20). Configurable
#: by the caller; deliberately small, because the number that matters is how
#: many of these in a row the account survives.
DEFAULT_RISK_PCT = Decimal("0.5")

#: A ceiling on the configured risk. Above this, one bad run ends the account,
#: and no setup quality justifies it.
MAX_RISK_PCT = Decimal("2.0")

#: Expected net PnL below this multiple of the risk is not worth taking
#: (FUTURES SPEC 23). Costs are certain; the edge is not.
MIN_NET_REWARD_RATIO = Decimal("1.0")

#: Jatah risiko satu hari, sebagai persen equity (PASAL 14.41).
#:
#: **Ditetapkan operator pada 2026-08-21, bukan disimpulkan.** §13.26 melarang
#: mengarang angka risiko, dan sebuah plafon harian yang dipilih penulis kode
#: adalah persis itu - cuma terdengar lebih resmi.
#:
#: Angkanya dipilih terhadap laju yang terukur: ARUNA menerbitkan 5-7 simbol
#: berbeda per hari, dan pada ``DEFAULT_RISK_PCT`` 0,5% per ide, 3% adalah enam
#: ide. Batas yang jauh di atas laju itu tidak akan pernah tercapai, dan angka
#: yang selalu hijau berhenti dibaca.
DAILY_RISK_PCT = Decimal("3.0")


@dataclass(frozen=True, slots=True)
class JatahHarian:
    """Berapa yang sudah dipertaruhkan hari ini, terhadap berapa yang boleh.

    **ARUNA tidak menahan rencana karena angka ini.** Ia melapor. Jalur futures
    sudah punya dua gerbang yang terukur, dan yang ketiga - lihat catatan di
    :mod:`aruna.decision.engine` - akan membungkam ARUNA hampir sepenuhnya kalau
    dipasang apa adanya. Yang memutuskan berhenti adalah operator, dan ia butuh
    angkanya, bukan keputusan yang sudah diambilkan.
    """

    equity: Decimal
    pct: Decimal
    #: Jumlah risiko rencana yang benar-benar terbit hari ini, dalam mata uang
    #: equity. Diukur dari tabelnya, tidak ditaksir.
    terpakai: Decimal

    def __post_init__(self) -> None:
        # **Dibulatkan ke sen di sini, bukan saat dicetak.** Terukur di
        # produksi 2026-08-21: `SUM(quantity * ABS(entry - stop))` memulangkan
        # 49.998650000000000000000000 - dua puluh empat angka di belakang koma
        # untuk USDT yang dinilai terhadap batas tiga ratus. Membulatkannya di
        # `ringkas()` saja akan meninggalkan bentuk panjangnya di `sisa`, di
        # `pct_terpakai`, dan di setiap pembaca berikutnya.
        object.__setattr__(
            self, "terpakai", Decimal(self.terpakai).quantize(Decimal("0.01"))
        )
        if self.equity <= 0:
            raise ValueError(
                "jatah risiko harian butuh equity positif; dihitung dari nol ia "
                "akan terbaca sebagai 'jatah habis' pada hari yang belum mulai"
            )
        if self.pct <= 0:
            raise ValueError("persen jatah harian harus di atas nol")

    @property
    def batas(self) -> Decimal:
        return (self.equity * self.pct / 100).quantize(Decimal("0.01"))

    @property
    def terlampaui(self) -> bool:
        return self.terpakai > self.batas

    @property
    def sisa(self) -> Decimal:
        """Nol adalah lantainya - dan ``terlampaui`` yang membedakan hari yang
        pas habis dari hari yang kelewatan. Sisa negatif yang dicetak apa adanya
        terbaca seperti salah hitung; sisa yang dijepit tanpa penanda
        menyembunyikan kelewatannya."""
        sisa = self.batas - self.terpakai
        return sisa if sisa > 0 else Decimal("0.00")

    @property
    def pct_terpakai(self) -> Decimal:
        return (self.terpakai / self.batas * 100).quantize(Decimal("0.1"))

    def ringkas(self) -> str:
        """Satu baris untuk operator."""
        if self.terlampaui:
            return (
                f"TERLAMPAUI - {self.terpakai:f} dari {self.batas:f} "
                f"({self.pct_terpakai:f}%)"
            )
        return (
            f"{self.pct_terpakai:f}% terpakai - {self.terpakai:f} dari "
            f"{self.batas:f}, sisa {self.sisa:f}"
        )


def jatah_harian(
    *, equity: Decimal, terpakai: Decimal, pct: Decimal = DAILY_RISK_PCT
) -> JatahHarian:
    """Jatah risiko hari ini (PASAL 14.41)."""
    return JatahHarian(equity=equity, pct=pct, terpakai=terpakai)


@dataclass(frozen=True, slots=True)
class PositionSize:
    quantity: Decimal
    notional: Decimal
    #: What is actually lost if the stop fills at its level.
    risk_amount: Decimal
    risk_pct_of_equity: Decimal
    #: Margin the venue requires at the leverage F4 eventually recommends. None
    #: here on purpose: this module does not choose leverage.
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def viable(self) -> bool:
        return self.quantity > 0

    def implied_leverage(self, equity: Decimal) -> Decimal | None:
        """Notional over equity - a *consequence* of the size, not its cause.

        Named "implied" because that is what it is. Reading this and then
        adjusting the size to change it would invert the whole chain.
        """
        if equity <= 0:
            return None
        return (self.notional / equity).quantize(Decimal("0.01"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": str(self.quantity),
            "notional": str(self.notional),
            "risk_amount": str(self.risk_amount),
            "risk_pct_of_equity": str(self.risk_pct_of_equity),
            "viable": self.viable,
            "findings": list(self.findings),
            "note": (
                "di-size dari risk budget dan jarak stop; leverage adalah "
                "output dari ini, tidak pernah jadi input untuknya "
                "(FUTURES SPEC 19)"
            ),
        }


def position_size(
    *,
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    side: PositionSide,
    contract: ContractSpec | None = None,
    risk_pct: Decimal = DEFAULT_RISK_PCT,
) -> PositionSize | None:
    """Quantity to trade, from the risk budget alone (FUTURES SPEC 19, 20).

    No leverage parameter and no profit target - see the module docstring.
    """
    if equity <= 0 or entry <= 0 or stop <= 0 or side is PositionSide.FLAT:
        return None

    findings: list[str] = []
    if risk_pct > MAX_RISK_PCT:
        findings.append(
            f"risiko {risk_pct}% yang diminta melewati batas atas "
            f"{MAX_RISK_PCT}% dan dipangkas: di atas itu, satu rentetan rugi "
            "biasa saja sudah menghabiskan akun"
        )
        risk_pct = MAX_RISK_PCT
    if risk_pct <= 0:
        return None

    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        findings.append(
            "stop berada tepat di harga entry; tidak ada risiko yang bisa "
            "dijadikan dasar untuk size"
        )
        return PositionSize(
            quantity=Decimal(0),
            notional=Decimal(0),
            risk_amount=Decimal(0),
            risk_pct_of_equity=Decimal(0),
            findings=tuple(findings),
        )

    # The whole calculation: how many units make the planned loss equal the
    # risk budget.
    budget = equity * risk_pct / 100
    quantity = budget / stop_distance

    if contract is not None and contract.step_size > 0:
        steps = (quantity / contract.step_size).to_integral_value(rounding=ROUND_DOWN)
        quantity = steps * contract.step_size

    notional = quantity * entry

    if contract is not None and notional < contract.min_notional:
        findings.append(
            # `f"{notional}"` on a zeroed Decimal prints "0E-11", which reads
            # like a corrupted figure in the one sentence explaining a refusal.
            f"risk budget ini hanya menopang notional {notional:f}, di bawah "
            f"minimum venue {contract.min_notional:f}. Mengambil setup ini "
            "berarti mengambil risiko lebih besar dari yang direncanakan, jadi "
            "posisinya tidak di-size"
        )
        return PositionSize(
            quantity=Decimal(0),
            notional=Decimal(0),
            risk_amount=Decimal(0),
            risk_pct_of_equity=Decimal(0),
            findings=tuple(findings),
        )

    risk_amount = quantity * stop_distance
    return PositionSize(
        quantity=quantity,
        notional=notional,
        risk_amount=risk_amount,
        risk_pct_of_equity=(risk_amount / equity * 100).quantize(Decimal("0.0001")),
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class TradeEconomics:
    """Gross reward, every cost, and what is left (FUTURES SPEC 23)."""

    risk_amount: Decimal
    gross_reward: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    funding_cost: Decimal
    slippage_cost: Decimal
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_costs(self) -> Decimal:
        return self.entry_fee + self.exit_fee + self.funding_cost + self.slippage_cost

    #: Skala uang, sama dengan kolom penyimpanannya (``DECIMAL(30,12)``).
    #:
    #: Tanpa pembulatan ini, MySQL menerima angka dengan dua puluh delapan
    #: angka di belakang koma, membulatkannya sendiri ke dua belas, dan
    #: mengeluh - satu baris ``Data truncated for column 'expected_net_pnl'``
    #: pada setiap plan yang tersimpan.
    #:
    #: Yang hilang bukan uang: selisihnya di bawah 1e-12 USDT. Yang hilang
    #: adalah log yang bisa dibaca, dan itu tetap ongkos - peringatan yang
    #: muncul pada setiap penulisan yang sehat mengajari pembacanya melewati
    #: baris peringatan.
    PNL_SCALE = Decimal("0.000000000001")

    @property
    def net_reward(self) -> Decimal:
        """Imbalan sesudah ongkos, dibulatkan ke skala penyimpanannya.

        Dibulatkan di sini dan bukan di lapisan penyimpanan, supaya angka yang
        dicetak, angka yang dipakai menghitung ``net_rr``, dan angka yang
        tersimpan adalah angka yang sama. Membulatkan hanya saat menyimpan akan
        membuat pesan Telegram dan baris database berbeda di digit terakhir -
        selisih yang tidak berarti apa-apa sampai seseorang membandingkan
        keduanya dan berhenti mempercayai keduanya.
        """
        return (self.gross_reward - self.total_costs).quantize(self.PNL_SCALE)

    @property
    def gross_rr(self) -> Decimal | None:
        if self.risk_amount <= 0:
            return None
        return (self.gross_reward / self.risk_amount).quantize(Decimal("0.01"))

    @property
    def net_rr(self) -> Decimal | None:
        """The only ratio worth quoting.

        Gross R:R ignores the costs that are certain while counting the reward
        that is not, which flatters every setup and flatters the marginal ones
        most.
        """
        if self.risk_amount <= 0:
            return None
        return (self.net_reward / self.risk_amount).quantize(Decimal("0.01"))

    @property
    def worth_taking(self) -> bool:
        ratio = self.net_rr
        return ratio is not None and ratio >= MIN_NET_REWARD_RATIO

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_amount": str(self.risk_amount),
            "gross_reward": str(self.gross_reward),
            "costs": {
                "entry_fee": str(self.entry_fee),
                "exit_fee": str(self.exit_fee),
                "funding": str(self.funding_cost),
                "slippage": str(self.slippage_cost),
                "total": str(self.total_costs),
            },
            "expected_net_pnl": str(self.net_reward),
            "gross_rr": str(self.gross_rr) if self.gross_rr is not None else None,
            "net_rr": str(self.net_rr) if self.net_rr is not None else None,
            "worth_taking": self.worth_taking,
            "findings": list(self.findings),
            "note": (
                "net R:R adalah satu-satunya rasio yang dipakai sebagai input "
                "keputusan; gross mengabaikan biaya yang sudah tentu sambil "
                "menghitung reward yang belum tentu (FUTURES SPEC 23)"
            ),
        }


def trade_economics(
    *,
    size: PositionSize,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    contract: ContractSpec | None = None,
    funding_cost_pct: Decimal = Decimal(0),
    slippage_pct: Decimal = Decimal("0.05"),
) -> TradeEconomics:
    """Reward after every cost FUTURES SPEC 23 names.

    ``funding_cost_pct`` comes from :mod:`aruna.futures.funding` and is a
    projection; ``slippage_pct`` is an assumption. Both are named in the output
    so nobody mistakes the total for a measurement.
    """
    findings: list[str] = []
    risk = size.risk_amount
    gross = abs(target - entry) * size.quantity

    taker = contract.taker_fee_pct if contract else Decimal("0.05")
    entry_fee = size.notional * taker / 100
    exit_fee = size.notional * taker / 100
    funding = size.notional * funding_cost_pct / 100
    slippage = size.notional * slippage_pct / 100

    economics = TradeEconomics(
        risk_amount=risk,
        gross_reward=gross,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        funding_cost=funding,
        slippage_cost=slippage,
    )

    if economics.gross_rr is not None and economics.net_rr is not None:
        erosion = economics.gross_rr - economics.net_rr
        if erosion > 0:
            findings.append(
                f"biaya memakan {erosion:.2f} dari rasio risk/reward "
                f"({economics.gross_rr} gross, {economics.net_rr} net)"
            )
    if not economics.worth_taking:
        # Same guard as the plan's refusal text, and for the same reason: with
        # a zero planned risk `net_rr` is None, and interpolating it wrote
        # "net reward is Nonex the risk" into a caveat a person reads.
        if economics.net_rr is not None:
            findings.append(
                f"net reward hanya {economics.net_rr}x dari risiko, di bawah "
                f"batas bawah {MIN_NET_REWARD_RATIO}x - biayanya sudah tentu "
                "dan edge-nya tidak, jadi ini tidak layak diambil "
                "(FUTURES SPEC 23, 52)"
            )
        else:
            findings.append(
                "rasio risk/reward tidak bisa dihitung: risiko yang "
                "direncanakan nol, jadi tidak ada rasio untuk dibandingkan "
                f"dengan batas bawah {MIN_NET_REWARD_RATIO}x "
                "(FUTURES SPEC 23, 52)"
            )
    if funding > 0:
        findings.append(
            "angka funding ini adalah proyeksi dari funding rate saat ini, dan "
            "rate itu bergerak sebelum settlement"
        )

    return TradeEconomics(
        risk_amount=risk,
        gross_reward=gross,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        funding_cost=funding,
        slippage_cost=slippage,
        findings=tuple(findings),
    )


__all__ = [
    "DEFAULT_RISK_PCT",
    "MAX_RISK_PCT",
    "MIN_NET_REWARD_RATIO",
    "PositionSize",
    "TradeEconomics",
    "position_size",
    "trade_economics",
]
