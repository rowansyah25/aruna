"""Menutup lingkarannya (FUTURES SPEC 40-45).

Loop menyimpan plan. Horizon-nya lewat. Sampai modul ini ada, **tidak ada apa
pun yang kembali dan bertanya apa yang terjadi** - jadi F6 lengkap, ber-test,
dan tidak pernah dijalankan. Itu persis keluarga cacat yang proyek ini buru
sepuluh fase: ditulis, diekspor, di-test, tidak pernah dipanggil dari jalur
hidup.

Tanpa ini sistem tidak akan pernah tahu apakah aturannya bekerja. Aturan spot
yang sama terukur 50% - lemparan koin - setelah 580 prediksi, dan tidak ada
alasan menganggap futures berbeda sampai diukur.

Tiga hal yang modul ini tolak lakukan:

**Menilai plan yang belum selesai horizonnya.** Menilai lebih awal berarti
menilai posisi yang masih berjalan, dan hasilnya akan berubah setelah dicatat.

**Menilai baris yang fingerprint-nya tidak cocok.** Kalau baris berubah setelah
diterbitkan, hasil apa pun yang dinilai terhadapnya tidak berarti apa-apa
(FUTURES SPEC 47). Ini satu-satunya tempat :meth:`verify` benar-benar dipanggil.

**Memakai bar yang belum tutup.** Harga di dalam bar berjalan masih bisa
berubah, dan menilai terhadapnya adalah kebocoran ke depan (SPEC 24).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.futures.learning import PlanOutcome, PriceBar, score_plan, score_wait
from aruna.futures.plan import FuturesPlan, PlanVerdict

log = get_logger("aruna.futures.resolve")

#: ``PlanOutcome`` -> empat akhir PASAL 14.31.
#:
#: ``LIQUIDATED`` masuk LOSS dan **tidak** punya kolom sendiri. §11.21 melarang
#: menyembunyikan LOSS, dan likuidasi adalah kekalahan yang paling buruk -
#: memberinya kategori sendiri akan mengeluarkannya dari kolom kalah, yang
#: adalah bentuk penyembunyian yang paling mudah dibela.
#:
#: ``OPEN`` sengaja tidak ada di sini: ia bukan akhir, dan yang tidak ada di
#: peta ini tidak dicatat sama sekali.
#: Ketelitian gerak pasar yang dilaporkan, sebagai persen.
#:
#: Dua angka di belakang koma - lima puluh kali lebih halus daripada ambang dua
#: persen yang menilainya, dan sudah cukup untuk dibaca manusia. Tanpa
#: pembulatan, pembagian Decimal membawa dua puluh delapan angka ke dalam baris
#: log; terukur di produksi pada APTUSDT,
#: ``-0.5952539839308117342444545285``.
_SKALA_GERAK = Decimal("0.01")

_AKHIR: dict[str, str] = {
    PlanOutcome.TARGET_HIT.value: "WIN",
    PlanOutcome.STOPPED_OUT.value: "LOSS",
    PlanOutcome.LIQUIDATED.value: "LOSS",
    PlanOutcome.EXPIRED.value: "EXPIRED",
}


def catat_hasil(result: Any) -> Any:
    """Kirim nasib satu rencana ke pembelajaran (PASAL 14.31, 14.34).

    **``move_pct`` adalah gerak pasar apa adanya** - positif berarti harga naik,
    apa pun arah rencananya. Yang membalik tandanya untuk SHORT adalah
    :class:`aruna.decision.outcome.Catatan`. Menyerahkan angka yang sudah
    diorientasikan dari sini akan membuatnya dibalik dua kali, dan kekalahan
    besar tercatat sebagai kemenangan besar di dalam data yang dipelajari
    Phase 12.

    **Signal palsu tanpa sebab tidak dikirim** (PASAL 14.34). ARUNA belum punya
    pencari sebab otomatis di jalur futures - loss autopsy Phase 8 berjalan di
    jalur signal - jadi yang terjadi hari ini adalah peringatan, bukan kiriman
    kosong. Itu perilaku yang benar: mengirimnya tanpa sebab tidak mengajarkan
    apa pun, dan mengarang sebabnya melanggar §13.26.
    """
    try:
        from aruna.decision.final import arah_dari
        from aruna.decision.outcome import Catatan, Hasil, OutcomeError

        nama = _AKHIR.get(str(getattr(result.outcome, "value", result.outcome)))
        if nama is None:
            return None

        entry = getattr(result, "entry", None)
        keluar = getattr(result, "exit_price", None)
        gerak = None
        if entry is not None and keluar is not None and entry > 0:
            gerak = (
                (Decimal(keluar) - Decimal(entry)) / Decimal(entry) * 100
            ).quantize(_SKALA_GERAK)

        catatan = Catatan(
            symbol=str(getattr(result, "symbol", "")),
            decision=arah_dari(getattr(result, "side", None)),
            outcome=Hasil(nama),
            move_pct=gerak,
        )
        if catatan.needs_analysis:
            log.warning(
                "futures.false_signal_tanpa_sebab",
                symbol=catatan.symbol,
                signal_id=getattr(result, "signal_id", ""),
                move_pct=str(catatan.move_pct),
                # Hasilnya ikut disebut, dan itu bukan hiasan: yang tertahan di
                # sini hampir selalu LOSS, dan sebuah peringatan yang tidak
                # menyebutkannya terbaca seperti kekalahan yang menghilang.
                # Kerugiannya sendiri TIDAK hilang - ia tetap tersimpan di
                # `futures_plan_results` dan tetap masuk laporan harian (§11.21).
                # Yang tertahan cuma jalur pembelajarannya.
                outcome=catatan.outcome.value,
            )
            return None
        log.info("decision.outcome", **catatan.learning_payload())
        return catatan
    except OutcomeError as exc:
        # Gabungan yang mustahil - WIN yang pasarnya melawan, misalnya. Itu
        # berarti dua lapisan bercerita berbeda tentang rencana yang sama, dan
        # yang benar adalah berteriak, bukan mencatat salah satunya.
        log.error("futures.outcome_bertentangan", sebab=str(exc))
        return None
    except Exception:
        # Pencatat yang menjatuhkan resolusi akan menghentikan penilaian
        # seluruh rencana lain di batch yang sama.
        log.exception("decision.outcome_failed")
        return None

#: Batas bar per permintaan yang venue izinkan.
MAX_BARS = 1500

#: Interval klines yang tersedia, dari yang paling halus, dengan menitnya.
#:
#: Dipilih dari panjang horizon, bukan dipatok. Yang paling halus yang masih
#: memuat seluruh jendela dalam satu permintaan selalu menang: bar yang lebih
#: halus melihat urutan stop-versus-target di dalam satu bar besar, dan itu
#: yang membedakan kalah dari menang ketika keduanya tersentuh.
PATH_INTERVALS: tuple[tuple[str, int], ...] = (
    ("1m", 1),
    ("5m", 5),
    ("15m", 15),
    ("1h", 60),
    ("4h", 240),
    ("1d", 1440),
)


def _interval_for(span_minutes: int) -> tuple[str, int]:
    """Interval terhalus yang muat dalam satu permintaan."""
    for name, minutes in PATH_INTERVALS:
        if span_minutes // minutes + 2 <= MAX_BARS:
            return name, minutes
    return PATH_INTERVALS[-1]


@dataclass(slots=True)
class ResolveRun:
    """Apa yang dinilai pada satu pass."""

    scored: int = 0
    ghosts: int = 0
    skipped: int = 0
    tampered: int = 0
    liquidations: int = 0
    errors: list[str] = field(default_factory=list)
    #: Hasil yang baru saja diskor pada pass ini, untuk dikabarkan.
    #:
    #: Dibawa di sini alih-alih dibaca ulang dari database sesudahnya: pembacaan
    #: kedua harus menebak jendela waktunya, dan jendela yang meleset sedikit
    #: akan mengabarkan hasil lama dua kali atau melewatkan yang baru.
    results: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "ghosts": self.ghosts,
            "skipped": self.skipped,
            "tampered": self.tampered,
            "liquidations": self.liquidations,
            "errors": list(self.errors),
        }

    def summary(self) -> str:
        # `tampered` belongs in this guard. Without it a pass that rejected
        # every single row reported "nothing was due" - which is the opposite
        # of what happened, and the one case where silence is most misleading.
        if not (self.scored or self.ghosts or self.skipped or self.tampered):
            return "tidak ada plan yang jatuh tempo untuk dinilai"
        parts = [f"{self.scored} plan dinilai", f"{self.ghosts} WAIT dinilai"]
        if self.skipped:
            parts.append(f"{self.skipped} dilewati (jalur harga tidak ada)")
        if self.tampered:
            parts.append(f"{self.tampered} DITOLAK: fingerprint tidak cocok")
        if self.liquidations:
            parts.append(f"{self.liquidations} LIQUIDATED")
        return ", ".join(parts)


class FuturesResolver:
    def __init__(self, *, store: Any, provider: Any) -> None:
        self._store = store
        self._provider = provider

    async def resolve_due(
        self, *, limit: int = 50, reference: datetime | None = None
    ) -> ResolveRun:
        """Nilai setiap plan yang horizonnya sudah lewat dan belum dinilai."""
        now = reference or now_utc()
        run = ResolveRun()

        rows = await self._store.due_for_resolution(limit=limit, reference=now)
        if not rows:
            return run

        for row in rows:
            try:
                await self._resolve_one(row, run, now=now)
            except ArunaError as exc:
                run.errors.append(f"{row.get('signal_id')}: {exc}")
                log.warning(
                    "futures.resolve_failed",
                    signal_id=row.get("signal_id"),
                    error=str(exc)[:200],
                )
        return run

    async def _resolve_one(
        self, row: dict[str, Any], run: ResolveRun, *, now: datetime
    ) -> None:
        plan = _plan_from_row(row)

        # FUTURES SPEC 47. The one place this is actually checked: a row that
        # changed after it was issued makes every outcome scored against it
        # meaningless, and scoring it anyway would launder the tampering into
        # the win rate.
        try:
            await self._store.verify(plan)
        except ArunaError as exc:
            run.tampered += 1
            run.errors.append(f"{plan.signal_id}: {exc}")
            log.error("futures.plan_tampered", signal_id=plan.signal_id)
            return

        started = plan.created_at
        ends = started + timedelta(hours=plan.horizon_hours)
        path = await self._path(plan.symbol, started, ends, now=now)
        if not path:
            run.skipped += 1
            return

        if plan.verdict is PlanVerdict.PLAN:
            result = score_plan(plan, path)
            if result is None:
                run.skipped += 1
                return
            await self._store.save_result(result, ends)
            # PASAL 14.31, sesudah tersimpan: yang dikirim ke pembelajaran
            # adalah hasil yang sudah jadi catatan, bukan yang masih bisa gagal
            # tersimpan.
            catat_hasil(result)
            run.results.append(result)
            run.scored += 1
            if result.outcome is PlanOutcome.LIQUIDATED:
                run.liquidations += 1
                # Loudly. A liquidation on a plan ARUNA recommended is a defect
                # in the leverage engine, not a data point in a distribution.
                log.error(
                    "futures.liquidated",
                    signal_id=plan.signal_id,
                    symbol=plan.symbol,
                    entry=str(result.entry),
                    exit=str(result.exit_price),
                )
        elif plan.verdict is PlanVerdict.WAIT:
            ghost = score_wait(plan, path)
            if ghost is None:
                run.skipped += 1
                return
            await self._store.save_ghost(ghost, ends)
            run.ghosts += 1
        else:
            # A refusal is not scored: the gates rejected it, and there is no
            # claim in it to be right or wrong about. It is counted in the
            # daily tally, which is where it belongs.
            run.skipped += 1

    async def _path(
        self, symbol: str, started: datetime, ends: datetime, *, now: datetime
    ) -> list[PriceBar]:
        """Bar yang sudah TUTUP di dalam jendela horizon.

        Diambil dari perpetual itu sendiri, bukan dari candle spot.

        Dulu alasannya mata uang: spot dikutip IDR, plan dikutip USDT, dan
        menilai yang satu dengan yang lain adalah kesalahan satuan yang sempat
        membuat stop duduk 5,5 juta "dolar" dari entry 63.000. Sejak PASAL 6
        keduanya USDT, jadi alasan itu habis - tapi aturannya tetap, dengan
        alasan yang lebih sempit dan tetap nyata: entry, stop dan target plan
        dikutip terhadap harga mark, dan mark berbeda dari spot sebesar basis
        perpetual. Menilai stop mark dengan bar spot berarti stop bisa
        dinyatakan kena padahal mark tidak pernah menyentuhnya, atau
        sebaliknya. Selisihnya kecil; arah kesalahannya tidak acak.
        """
        span_minutes = max(1, int((ends - started).total_seconds() // 60))

        # The interval is chosen from the horizon, not fixed at 5m.
        #
        # Fixed, it failed at both ends. A horizon under ~9 minutes contained
        # no closed 5m bar at all, so those plans were skipped every tick
        # forever. And a horizon past ~125 hours needed more than MAX_BARS,
        # so the venue returned the first 1500 and the tail was silently
        # missing - the position's later hours simply did not exist, and a
        # plan whose target was hit in hour 130 was recorded as EXPIRED. Not
        # "unknown": EXPIRED, as a statement of fact, in a table F6 scores.
        interval, minutes = _interval_for(span_minutes)
        needed = min(MAX_BARS, span_minutes // minutes + 2)
        try:
            raw = await self._provider._get(  # noqa: SLF001 - allowlisted transport
                "/fapi/v1/klines",
                symbol=symbol,
                interval=interval,
                startTime=int(started.timestamp() * 1000),
                endTime=int(ends.timestamp() * 1000),
                limit=needed,
            )
        except ArunaError:
            return []

        bars: list[PriceBar] = []
        for entry in raw or []:
            # entry[6] is the venue's close time. A bar whose close time has
            # not passed is still moving, and scoring against it reads a price
            # that is not final yet (SPEC 24).
            closes_at = datetime.fromtimestamp(int(entry[6]) / 1000, tz=UTC)
            if closes_at > now or closes_at > ends:
                continue
            bars.append(
                PriceBar(
                    as_of=closes_at,
                    high=Decimal(str(entry[2])),
                    low=Decimal(str(entry[3])),
                    close=Decimal(str(entry[4])),
                )
            )
        return bars


def _plan_from_row(row: dict[str, Any]) -> FuturesPlan:
    """Rebuild only what scoring and verification need.

    Deliberately partial. The fingerprint covers exactly these fields, so a
    round trip that reproduces them reproduces the hash - and anything else on
    the row is not part of what must not have changed.
    """
    from aruna.futures.liquidation import Liquidation
    from aruna.futures.models import MarginMode, PositionSide

    liquidation = None
    if row.get("liquidation_price") is not None:
        price = Decimal(str(row["liquidation_price"]))
        entry = _dec(row.get("entry")) or price
        # Only `price` is used downstream - by the fingerprint and by the
        # liquidation check in `score_plan` - so the rest is reconstructed
        # from what the row holds rather than invented. `distance` is
        # derivable; the maintenance figures are not stored and are left at
        # zero rather than guessed, because nothing reads them here and a
        # fabricated rate would be worse than an obviously empty one.
        liquidation = Liquidation(
            price=price,
            distance=abs(entry - price),
            distance_pct=(
                (abs(entry - price) / entry * 100) if entry > 0 else Decimal(0)
            ),
            margin_mode=MarginMode(str(row.get("margin_mode") or "ISOLATED")),
            maintenance_rate=Decimal(0),
            maintenance_amount=Decimal(0),
        )

    return FuturesPlan(
        signal_id=str(row["signal_id"]),
        symbol=str(row["symbol"]),
        side=PositionSide(str(row["side"])),
        verdict=PlanVerdict(str(row["verdict"])),
        horizon_hours=float(row["horizon_hours"]),
        created_at=row["created_at"],
        reference_price=_dec(row.get("reference_price")),
        entry=_dec(row.get("entry")),
        stop=_dec(row.get("stop")),
        target=_dec(row.get("target")),
        quantity=_dec(row.get("quantity")),
        leverage=int(row["leverage"]) if row.get("leverage") else None,
        liquidation=liquidation,
        refusals=tuple(row.get("refusals") or ()),
    )


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


__all__ = ["MAX_BARS", "PATH_INTERVALS", "FuturesResolver", "ResolveRun"]
