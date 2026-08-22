"""Futures plan storage (FUTURES SPEC 37-39, 40-45, 47).

Append-only, and the database enforces it. See ``migrations/0015``.

There is no ``update`` method here and there will not be one: a plan is
frozen at the moment it is issued, because F6 scores these rows against what
the market actually did, and a system that can revise its own past plans
cannot be evaluated at all.

The one method that reads a plan back, :meth:`verify`, exists to prove the row
being scored is the row that was written. A fingerprint that only ever gets
computed and never re-checked is a hash nobody is holding to account.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.futures.learning import PlanResult
from aruna.futures.plan import FINGERPRINT_VERSION, FuturesPlan, PlanVerdict

#: Scale of the DECIMAL(30,12) price columns in migration 0015.
PRICE_SCALE = Decimal("0.000000000001")

#: Every fingerprint basis that has ever been written. Verification tries the
#: version a row declares first, then these, because a row's declared version
#: can itself be wrong - see :meth:`FuturesRepository.verify`.
_KNOWN_FINGERPRINT_VERSIONS: tuple[int, ...] = (1, 2)

log = get_logger("aruna.db.futures")


def _at_column_scale(value: Decimal | None) -> Decimal | None:
    """Quantize to the column's scale, with the rounding the fingerprint uses.

    The liquidation price comes out of a division with twenty-three decimal
    places, and MySQL silently truncates it to twelve on insert - emitting
    "Data truncated for column 'liquidation_price'" and storing a value that
    is not the one that was fingerprinted.

    PHASE 7 hit exactly this and it would have invalidated every prediction:
    the hash was computed on the in-memory value, the database kept a rounded
    one, and every later verification failed on rows nobody had touched. The
    scale and the rounding mode are pinned here so the value written is the
    value hashed.
    """
    if value is None:
        return None
    return value.quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN)


#: Skala kolom ``futures_plans.funding_cost_pct``: ``DECIMAL(14,6)``.
#:
#: Bukan ``PRICE_SCALE``. Enam angka di belakang koma, bukan dua belas -
#: memakai skala harga di sini akan menyimpan angka yang tetap dipotong MySQL,
#: hanya dengan langkah tambahan di tengahnya.
PCT_SCALE = Decimal("0.000001")


def _at_pct_scale(value: Decimal | None) -> Decimal | None:
    """Quantize ke skala kolom persen.

    ``plan.economics.funding_cost_pct`` sudah dibulatkan di
    :mod:`aruna.futures.plan`, dan itu sempat membuat masalah ini terlihat
    selesai. Yang ditulis ke kolom ini bukan angka itu melainkan
    ``projection.funding_cost`` - jalur kedua, dari proyeksi horizon, yang
    tidak pernah lewat pembulatan mana pun.

    Terukur: "Data truncated for column 'funding_cost_pct'" tetap muncul
    berjam-jam sesudah pembulatan yang pertama dipasang, karena yang diperbaiki
    bukan jalur yang menulis.
    """
    if value is None:
        return None
    return value.quantize(PCT_SCALE, rounding=ROUND_HALF_EVEN)


class PlanTampered(ArunaError):
    """A stored plan no longer matches its fingerprint (FUTURES SPEC 47)."""


class FuturesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self,
        plan: FuturesPlan,
        *,
        model_version: str,
        council_session_id: int | None = None,
    ) -> int:
        """Store a plan exactly once.

        No ``ON DUPLICATE KEY UPDATE``. Every other repository in ARUNA has one
        so a re-run refreshes its row; here a second write under the same
        signal id is an attempt to change an issued plan, and it must fail
        loudly rather than quietly win.
        """
        projection = plan.projection
        return await self._db.insert(
            """
            INSERT INTO futures_plans
                (signal_id, fingerprint, fingerprint_version,
                 symbol, side, verdict, horizon_hours,
                 reference_price,
                 entry, stop, target, quantity, notional, leverage,
                 margin_mode, margin_required, liquidation_price,
                 buffer_score, buffer_band, net_rr, expected_net_pnl,
                 integrity_verdict, refusals, caveats, settlements,
                 funding_cost_pct, created_at, model_version,
                 council_session_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            plan.signal_id,
            plan.fingerprint,
            FINGERPRINT_VERSION,
            plan.symbol,
            plan.side.value,
            plan.verdict.value,
            Decimal(str(plan.horizon_hours)),
            _at_column_scale(plan.reference_price),
            _at_column_scale(plan.entry),
            _at_column_scale(plan.stop),
            _at_column_scale(plan.target),
            _at_column_scale(plan.quantity),
            _at_column_scale(plan.notional),
            plan.leverage,
            plan.margin_mode.value,
            _at_column_scale(plan.margin_required),
            _at_column_scale(plan.liquidation.price if plan.liquidation else None),
            plan.buffer.score if plan.buffer else None,
            plan.buffer.band if plan.buffer else None,
            plan.net_rr,
            plan.expected_net_pnl,
            plan.integrity.verdict.value if plan.integrity else None,
            dump_json(list(plan.refusals)),
            dump_json(list(plan.caveats)),
            projection.settlements if projection else None,
            _at_pct_scale(
                projection.funding_cost
                if projection and projection.funding_cost is not None
                else None
            ),
            to_mysql_datetime(plan.created_at),
            model_version,
            council_session_id,
        )

    async def mark_pushed(
        self, signal_id: str, *, message_id: int | None, at: Any
    ) -> None:
        """Catat bahwa rencana ini benar-benar sampai ke Telegram.

        Tanpa jejak ini tidak ada yang bisa membuktikan operator pernah melihat
        rencananya, dan hasilnya nanti dibungkam - yang persis kegagalan yang
        dilaporkan operator: *"saat signal dikirim ke tele gaada resultnya"*.

        ``message_id`` boleh ``None``: pengirim yang tidak melaporkan id pesan
        tetap berhasil mengirim. Yang membedakan "tidak terkirim" dari
        "terkirim tanpa id" adalah **ada tidaknya barisnya**, bukan kolom ini.

        **Ditulis ke tabelnya sendiri, bukan ke ``futures_plans``.** Tabel itu
        append-only dan trigger-nya menolak setiap ``UPDATE`` tanpa syarat -
        versi pertama baris ini mencoba mengubahnya dan gagal pada setiap
        rencana, diam-diam, karena kegagalannya tertangkap penjaga di
        pemanggilnya. Immutability itu load-bearing: :meth:`verify` membuktikan
        baris yang dinilai adalah baris yang diterbitkan.

        ``INSERT IGNORE``: satu rencana terkirim satu kali, dan pengiriman
        kedua adalah pengulangan yang tidak boleh merusak jejak pertama - itu
        yang benar-benar dilihat operator.
        """
        await self._db.execute(
            "INSERT IGNORE INTO futures_plan_delivery "
            "(signal_id, pushed_at, telegram_message_id) VALUES (%s, %s, %s)",
            signal_id,
            to_mysql_datetime(at),
            message_id,
        )

    async def pushed_message_ids(
        self, signal_ids: list[str]
    ) -> dict[str, int | None]:
        """Rencana mana yang benar-benar terkirim, dan pesan mana yang membawanya.

        Dibaca dari ``futures_plan_delivery``, bukan dari ``futures_plans``:
        tabel rencana append-only dan tidak bisa membawa kolom yang berubah -
        lihat :meth:`mark_pushed`.

        Yang **tidak ada** di peta ini tidak pernah terkirim - tidak ada
        kelonggaran untuk baris lama. Dua kali percobaan sebelumnya di jalur
        signal memakai kelonggaran ("baris sebelum migrasi dianggap terkirim")
        dan dua-duanya salah: operator melaporkan bug yang sama tiga kali
        sebelum aturannya benar.
        """
        wanted = [s for s in signal_ids if s]
        if not wanted:
            return {}
        tanya = ", ".join(["%s"] * len(wanted))
        rows = await self._db.fetch(
            f"SELECT signal_id, telegram_message_id "
            f"FROM futures_plan_delivery WHERE signal_id IN ({tanya})",
            *wanted,
        )
        return {
            str(r["signal_id"]): (
                int(r["telegram_message_id"])
                if r["telegram_message_id"] is not None
                else None
            )
            for r in rows
        }

    async def save_result(self, result: PlanResult, resolved_at: Any) -> int:
        return await self._db.insert(
            """
            INSERT INTO futures_plan_results
                (signal_id, outcome, entry, exit_price, max_adverse_pct,
                 touched_liquidation, findings, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                id                  = LAST_INSERT_ID(futures_plan_results.id),
                outcome             = new.outcome,
                exit_price          = new.exit_price,
                max_adverse_pct     = new.max_adverse_pct,
                touched_liquidation = new.touched_liquidation,
                findings            = new.findings,
                resolved_at         = new.resolved_at
            """,
            result.signal_id,
            result.outcome.value,
            _at_column_scale(result.entry),
            _at_column_scale(result.exit_price),
            result.max_adverse_pct,
            1 if result.touched_liquidation else 0,
            dump_json(list(result.findings)),
            to_mysql_datetime(resolved_at),
        )

    async def save_ghost(self, ghost: Any, resolved_at: Any) -> int:
        """Store how a WAIT turned out (migration 0017)."""
        return await self._db.insert(
            """
            INSERT INTO futures_ghost_results
                (signal_id, verdict, reference_price, max_move_pct,
                 findings, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                id              = LAST_INSERT_ID(futures_ghost_results.id),
                verdict         = new.verdict,
                max_move_pct    = new.max_move_pct,
                findings        = new.findings,
                resolved_at     = new.resolved_at
            """,
            ghost.signal_id,
            ghost.verdict.value,
            _at_column_scale(ghost.reference),
            ghost.max_move_pct,
            dump_json(list(ghost.findings)),
            to_mysql_datetime(resolved_at),
        )

    async def due_for_resolution(
        self, *, limit: int = 50, reference: Any = None
    ) -> list[dict[str, Any]]:
        """Plans whose horizon has elapsed and which nothing has scored yet.

        The horizon is added in SQL rather than filtered in Python so a large
        backlog does not have to be read into memory to find the few rows that
        are due.

        Refusals are excluded here rather than fetched and discarded: they were
        never positions and never stood aside, so there is no outcome to
        attach to them at all.
        """
        rows = await self._db.fetch(
            """
            SELECT p.*
            FROM futures_plans p
            LEFT JOIN futures_plan_results r  ON r.signal_id = p.signal_id
            LEFT JOIN futures_ghost_results g ON g.signal_id = p.signal_id
            WHERE p.verdict IN ('PLAN', 'WAIT')
              AND r.signal_id IS NULL
              AND g.signal_id IS NULL
              AND DATE_ADD(p.created_at,
                           INTERVAL p.horizon_hours * 3600 SECOND) <= %s
            ORDER BY p.created_at
            LIMIT %s
            """,
            to_mysql_datetime(reference or now_utc()),
            limit,
        )
        return [_hydrate(row) for row in rows]

    async def results_since(self, since: Any, until: Any = None) -> list[Any]:
        """Resolved plans, rebuilt into what the learning report scores.

        The report used to be constructed empty - ``FuturesLearningReport()``
        with no arguments - so it reported "INSUFFICIENT SAMPLE: 0 resolved
        plan(s)" no matter how many rows the resolver had written. The
        resolver closed the loop and this was the step where it re-opened:
        outcomes stored, nothing reading them.

        ``until`` closes the window at the far end. Without it the daily report
        covers "everything since a moment", which is a rolling window rather
        than a day: a report sent late then carries part of the next day, and a
        report for Monday grows every time it is rebuilt. A day that keeps
        changing after it ended is not a day.
        """
        from aruna.futures.learning import PlanOutcome, PlanResult
        from aruna.futures.models import PositionSide

        rows = await self._db.fetch(
            """
            SELECT r.*, p.symbol, p.side
            FROM futures_plan_results r
            JOIN futures_plans p ON p.signal_id = r.signal_id
            WHERE r.resolved_at >= %s
              AND (%s IS NULL OR r.resolved_at < %s)
            ORDER BY r.resolved_at DESC
            """,
            to_mysql_datetime(since),
            to_mysql_datetime(until) if until is not None else None,
            to_mysql_datetime(until) if until is not None else None,
        )
        return [
            PlanResult(
                signal_id=str(row["signal_id"]),
                symbol=str(row["symbol"]),
                side=PositionSide(str(row["side"])),
                outcome=PlanOutcome(str(row["outcome"])),
                entry=Decimal(str(row["entry"])),
                exit_price=(
                    Decimal(str(row["exit_price"]))
                    if row["exit_price"] is not None
                    else None
                ),
                max_adverse_pct=(
                    Decimal(str(row["max_adverse_pct"]))
                    if row["max_adverse_pct"] is not None
                    else None
                ),
                touched_liquidation=bool(row["touched_liquidation"]),
                findings=tuple(load_json(row["findings"]) or ()),
            )
            for row in rows
        ]

    async def outcome_counts(self) -> dict[str, int]:
        """What the resolved plans came to, for the daily report."""
        rows = await self._db.fetch(
            "SELECT outcome, COUNT(*) AS n FROM futures_plan_results "
            "GROUP BY outcome"
        )
        return {str(r["outcome"]): int(r["n"]) for r in rows}

    async def ghost_counts(self) -> dict[str, int]:
        rows = await self._db.fetch(
            "SELECT verdict, COUNT(*) AS n FROM futures_ghost_results "
            "GROUP BY verdict"
        )
        return {str(r["verdict"]): int(r["n"]) for r in rows}

    async def get(self, signal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM futures_plans WHERE signal_id = %s", signal_id
        )
        return _hydrate(row) if row else None

    async def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recent plans, each carrying its outcome if it has one.

        The join is not cosmetic. Without it the discipline engine
        (FUTURES SPEC 34) reads rows that have no ``outcome`` key at all, so
        its losing-streak, revenge, size-escalation and leverage-escalation
        patterns can never fire - four of its five. Worse, it then reports
        ``loss_streak: 0`` and ``clean: true``, which is an affirmative "no
        losing streak" where the honest answer is that no outcome was read.

        Only overtrading kept working, because that reads ``created_at`` and
        ``verdict``, which this query always supplied. So the module returned
        plausible, sometimes non-empty reports - which is exactly why the hole
        was invisible.
        """
        rows = await self._db.fetch(
            """
            SELECT p.*, r.outcome, r.resolved_at, r.touched_liquidation
            FROM futures_plans p
            LEFT JOIN futures_plan_results r ON r.signal_id = p.signal_id
            ORDER BY p.created_at DESC
            LIMIT %s
            """,
            limit,
        )
        return [_hydrate(row) for row in rows]

    async def counts_since(self, since: Any, until: Any = None) -> dict[str, int]:
        """Verdict tallies for the daily report (FUTURES SPEC 48).

        Every verdict, not only the plans. A day of two plans and forty
        refusals is a day of mostly saying no, and counting only the two would
        describe a different system from the one that ran.

        ``until`` closes the window, for the reason given on
        :meth:`results_since`: a tally without an upper bound keeps growing
        after the day it claims to describe has ended.
        """
        rows = await self._db.fetch(
            "SELECT verdict, COUNT(*) AS n FROM futures_plans "
            "WHERE created_at >= %s AND (%s IS NULL OR created_at < %s) "
            "GROUP BY verdict",
            to_mysql_datetime(since),
            to_mysql_datetime(until) if until is not None else None,
            to_mysql_datetime(until) if until is not None else None,
        )
        tally = {v.value: 0 for v in PlanVerdict}
        for row in rows:
            tally[str(row["verdict"])] = int(row["n"])
        return tally

    async def risiko_terpakai_since(
        self, since: Any, until: Any = None
    ) -> Decimal:
        """Risiko yang benar-benar dipertaruhkan sejak ``since`` (PASAL 14.41).

        Dihitung dari kuantitas kali jarak entry ke stop - yaitu yang hilang
        kalau stopnya kena - dan **bukan** dari notional. Notional adalah ukuran
        posisi; pada leverage tiga ia tiga kali lebih besar daripada yang
        benar-benar dipertaruhkan, dan memakainya akan membuat jatah harian
        habis tiga kali lebih cepat daripada seharusnya.

        Hanya vonis ``PLAN``. WAIT dan REFUSED tidak mempertaruhkan apa pun, dan
        menghitungnya akan membuat jatah habis justru pada hari ARUNA paling
        banyak menolak.

        **Dihitung dari jejak kirim, bukan dari baris rencana.** Versi pertama
        menjumlahkan ``futures_plans`` apa adanya dan memberi 3.099 USDT untuk
        2026-08-20 terhadap jatah 300 - seribu tiga puluh tiga persen. Bukan
        jatah yang jebol: rencana yang sama disusun ulang tiap lima belas menit,
        jadi satu ide dihitung sebelas kali. Hari itu 55 baris PLAN lahir dari
        lima simbol, dan **satu** yang benar-benar terkirim.

        Penahan duplikat PASAL 14.35-14.37 sudah memastikan setup yang sama
        tidak dikirim dua kali, jadi ``futures_plan_delivery`` menghitung tiap
        ide sekali - dan ia juga jawaban yang benar untuk pertanyaannya:
        yang dipertaruhkan operator adalah yang sampai kepadanya.

        Jendelanya karena itu memakai ``pushed_at``, bukan ``created_at``: yang
        menentukan hari adalah kapan operator melihatnya.
        """
        row = await self._db.fetchrow(
            "SELECT SUM(p.quantity * ABS(p.entry - p.stop)) AS terpakai "
            "FROM futures_plan_delivery d "
            "JOIN futures_plans p ON p.signal_id = d.signal_id "
            "WHERE p.verdict = %s AND d.pushed_at >= %s "
            "AND (%s IS NULL OR d.pushed_at < %s)",
            PlanVerdict.PLAN.value,
            to_mysql_datetime(since),
            to_mysql_datetime(until) if until is not None else None,
            to_mysql_datetime(until) if until is not None else None,
        )
        # Hari tanpa rencana adalah nol yang diukur, bukan UNKNOWN: kuerinya
        # berjalan dan jawabannya "belum ada yang dipertaruhkan".
        nilai = (row or {}).get("terpakai")
        return Decimal(str(nilai)) if nilai is not None else Decimal(0)

    async def verify(self, plan: FuturesPlan) -> None:
        """Prove the stored row is the plan that was issued (FUTURES SPEC 47).

        Raises rather than returning a boolean. A caller that can ignore a
        tampering check will eventually ignore it.
        """
        row = await self._db.fetchrow(
            "SELECT fingerprint, fingerprint_version FROM futures_plans "
            "WHERE signal_id = %s",
            plan.signal_id,
        )
        if row is None:
            raise PlanTampered(
                f"plan {plan.signal_id} is not stored, so nothing about it can "
                "be verified"
            )
        stored = str(row["fingerprint"])
        # Against the basis in force when the row was written, not today's.
        # Recomputing an old row with a newer field list rejects it for having
        # been "changed" when nothing touched it - which is what happened to 26
        # plans the first time the resolver ran.
        declared = int(row.get("fingerprint_version") or 1)
        if stored == plan.fingerprint_at(declared):
            return

        # The declared version can itself be wrong, and mine were: rows written
        # after migration 0016 but before 0018 hashed on the v2 basis while the
        # new column defaulted them to 1. Their own hash identifies the basis
        # they used, so the other known versions are tried before calling this
        # tampering.
        #
        # The archive is NOT rewritten to correct them. The append-only trigger
        # refused exactly that repair, and it was right to: patching the
        # provenance of an issued plan is the thing this table exists to
        # prevent, and a mislabelled version is a smaller problem than a
        # mutable history. The mismatch is reported instead.
        for version in _KNOWN_FINGERPRINT_VERSIONS:
            if version != declared and stored == plan.fingerprint_at(version):
                log.warning(
                    "futures.fingerprint_version_mislabelled",
                    signal_id=plan.signal_id,
                    declared=declared,
                    actual=version,
                )
                return

        raise PlanTampered(
                f"plan {plan.signal_id} does not match its stored fingerprint. "
                "The row was changed after it was issued, and no outcome "
                "scored against it means anything (FUTURES SPEC 47)"
            )


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    row["refusals"] = load_json(row.get("refusals")) or []
    row["caveats"] = load_json(row.get("caveats")) or []
    row["created_at"] = as_utc(row["created_at"])
    return row


__all__ = ["FuturesRepository", "PlanTampered"]
