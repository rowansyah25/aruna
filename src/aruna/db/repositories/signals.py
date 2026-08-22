"""Signal storage (SPEC 20, 22, 23, 34).

``signal_snapshots`` is append-only at the database level. This repository has
no update method for it, and none should ever be added: a revised prediction is
a new row that supersedes the old one, never an edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from aruna.core.enums import Decision, Horizon, Market
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.signals.models import (
    LockedSignal,
    OutcomeSample,
    PaperTrade,
    SignalOutcome,
    SignalStatus,
)


class SignalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- the lock -------------------------------------------------------

    async def lock(
        self,
        asset_id: int,
        signal: LockedSignal,
        *,
        published: bool,
        withheld_reason: str | None = None,
        quality: Any = None,
        withheld: Any = None,
    ) -> None:
        """Write the immutable snapshot, then its lifecycle row.

        ``published`` records what the lock decided. It is required rather than
        defaulted: a caller that forgot to say would otherwise have every
        withheld verdict stored as a claim ARUNA stood behind, and SPEC 29 would
        later measure the system against calls it explicitly declined to make.

        Plain INSERT with no ON DUPLICATE KEY clause: re-locking the same
        signal_id must fail loudly rather than quietly overwrite a prediction.
        """
        await self._db.execute(
            """
            INSERT INTO signal_snapshots
                (signal_id, fingerprint, asset_id, market_code, symbol,
                 horizon_code, direction, confidence, confidence_raw,
                 reference_price,
                 entry_price, target_price, expected_move_pct, bid, ask,
                 spread_bps, locked_at, as_of, resolves_at, data_timestamp,
                 reasoning, regime, news_state, risk_level, data_source,
                 model_version, council_session_id, supersedes,
                 signal_quality, quality_coverage, quality_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            """,
            signal.signal_id,
            signal.fingerprint,
            asset_id,
            signal.market.value,
            signal.symbol,
            signal.horizon.value,
            signal.direction.value,
            round(signal.confidence, 3),
            # Keluaran model sebelum kalibrasi (bagian 9). Yang mengukur
            # kalibrasi berikutnya membaca kolom INI - kalau ia membaca
            # `confidence`, ia mengukur hasil kalibrasinya sendiri.
            (
                round(signal.confidence_raw, 3)
                if signal.confidence_raw is not None
                else None
            ),
            signal.reference_price,
            signal.entry_price,
            signal.target_price,
            signal.expected_move_pct,
            signal.bid,
            signal.ask,
            signal.spread_bps,
            to_mysql_datetime(signal.locked_at),
            to_mysql_datetime(signal.as_of),
            to_mysql_datetime(signal.resolves_at),
            to_mysql_datetime(signal.data_timestamp),
            dump_json(list(signal.reasoning)),
            signal.regime,
            signal.news_state,
            signal.risk_level,
            signal.data_source or None,
            signal.model_version,
            signal.council_session_id,
            signal.supersedes,
            # Skor dan cakupan berpasangan, dijaga CHECK di skema: skor tanpa
            # cakupan adalah angka yang tidak bisa dinilai pembacanya, dan
            # cakupan tanpa skor adalah cakupan atas apa.
            None if quality is None else quality.score,
            None if quality is None else round(quality.coverage, 4),
            None if quality is None else dump_json(quality.to_dict()),
        )
        await self._db.execute(
            """
            INSERT INTO signals
                (signal_id, asset_id, market_code, symbol, horizon_code, status,
                 locked_at, resolves_at, published, withheld_reason,
                 withheld_code, withheld_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            signal.signal_id,
            asset_id,
            signal.market.value,
            signal.symbol,
            signal.horizon.value,
            signal.status.value,
            to_mysql_datetime(signal.locked_at),
            to_mysql_datetime(signal.resolves_at),
            published,
            None if published else (withheld_reason or "")[:255],
            # Kode dan rinciannya hanya untuk yang ditahan. Prediksi yang
            # terbit tidak ditahan, dan skema menolaknya lewat CHECK - tanpa
            # itu satu bug di jalur penguncian bisa membuat hitungan "kenapa
            # diam" memuat baris yang justru tidak diam.
            None if published or withheld is None else withheld.code.value,
            None if published or withheld is None else dump_json(withheld.to_dict()),
        )

    async def get(self, signal_id: str) -> tuple[LockedSignal, str] | None:
        """Load a prediction and its stored fingerprint, for integrity checking."""
        row = await self._db.fetchrow(
            "SELECT s.*, g.status, g.superseded_by FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id WHERE s.signal_id = %s",
            signal_id,
        )
        return (_to_signal(row), row["fingerprint"]) if row else None

    async def votes_for(self, signal_id: str) -> Any:
        """Suara tiap agent di balik satu prediksi, atau ``None``.

        Pesan hasil selalu mencetak "Tidak ada catatan pemilihan untuk prediksi
        ini." Kalimat itu jujur pada saat ditulis - ``agent_decisions`` memang
        kosong - dan berhenti jujur ketika ``council_votes`` mulai terisi tanpa
        ada yang membacanya kembali.

        Tautannya tidak perlu dibuat: ``signal_snapshots.council_session_id``
        sudah ditulis pada setiap penguncian sejak kolomnya ada. Yang hilang
        hanya pencariannya - satu sisi jembatan dibangun, sisi lainnya tidak.

        ``agreed_with_council`` dibaca apa adanya dan tidak dihitung ulang dari
        ``decision``. Yang tersimpan adalah penilaian saat sesi itu berjalan;
        menyusunnya ulang sekarang akan membandingkan pendapat lama dengan
        aturan baru, dan itu cara paling halus untuk mengubah catatan lama
        (PASAL 11.21).
        """
        from aruna.notify.verdict import VoteSplit

        rows = await self._db.fetch(
            """
            SELECT v.role, v.abstained, v.agreed_with_council
            FROM council_votes v
            JOIN signal_snapshots s
              ON s.council_session_id = v.council_session_id
            WHERE s.signal_id = %s
            ORDER BY v.role
            """,
            signal_id,
        )
        if not rows:
            # Tidak sama dengan "nol agent setuju". Pemanggil membedakan
            # keduanya, dan pesannya mengatakan mana yang terjadi.
            return None

        setuju, kontra, abstain = [], [], []
        for row in rows:
            nama = str(row["role"])
            if row["abstained"]:
                abstain.append(nama)
            elif row["agreed_with_council"]:
                setuju.append(nama)
            else:
                kontra.append(nama)
        return VoteSplit(tuple(setuju), tuple(kontra), tuple(abstain))

    async def due(self, *, reference: datetime, limit: int = 50) -> list[str]:
        """Signal ids whose horizon has elapsed and which are still LOCKED."""
        rows = await self._db.fetch(
            "SELECT signal_id FROM signals WHERE status = 'LOCKED' "
            "AND resolves_at <= %s ORDER BY resolves_at LIMIT %s",
            to_mysql_datetime(reference),
            limit,
        )
        return [row["signal_id"] for row in rows]

    async def due_count(self, *, reference: datetime) -> int:
        """How many signals :meth:`due` would find if nothing limited it.

        The same filter, without ``LIMIT``.  A resolution pass that always
        returns exactly its limit looks identical whether the backlog is 100 or
        10,000; this is the number that tells them apart, and health reports it
        so a queue that stops draining is visible before it is a month deep.
        """
        value = await self._db.fetchval(
            "SELECT count(*) FROM signals WHERE status = 'LOCKED' AND resolves_at <= %s",
            to_mysql_datetime(reference),
        )
        return int(value or 0)

    async def locked_horizons(self) -> list[str]:
        """Distinct horizons that still have unresolved predictions on them.

        Read by health: a horizon carrying LOCKED signals whose candles nothing
        keeps current is evidence going stale under a prediction that will be
        scored from it.
        """
        rows = await self._db.fetch(
            "SELECT DISTINCT horizon_code FROM signals WHERE status = 'LOCKED'"
        )
        return [row["horizon_code"] for row in rows]

    async def set_status(
        self,
        signal_id: str,
        status: SignalStatus,
        *,
        resolved_at: datetime | None = None,
        superseded_by: str | None = None,
        withheld_reason: str | None = None,
    ) -> None:
        """Advance the lifecycle. Never touches the frozen snapshot.

        ``withheld_reason`` is written only when one is given, so an ordinary
        RESOLVED does not wipe the note the lock left behind. UNSCOREABLE uses
        it to record the measurement that justified closing a prediction no
        data can answer - a status change that cannot be audited later is a
        prediction quietly discarded.
        """
        if withheld_reason is None:
            await self._db.execute(
                "UPDATE signals SET status = %s, resolved_at = %s, superseded_by = %s "
                "WHERE signal_id = %s",
                status.value,
                to_mysql_datetime(resolved_at),
                superseded_by,
                signal_id,
            )
            return
        await self._db.execute(
            "UPDATE signals SET status = %s, resolved_at = %s, superseded_by = %s, "
            "withheld_reason = %s WHERE signal_id = %s",
            status.value,
            to_mysql_datetime(resolved_at),
            superseded_by,
            withheld_reason,
            signal_id,
        )

    async def withheld_tally(
        self, *, since: datetime, market: Market | None = None
    ) -> list[dict[str, Any]]:
        """Berapa banyak penahanan per kelompok sejak ``since`` (PASAL 11.12).

        Inilah bentuk yang menjawab "kenapa NO SIGNAL sebanyak ini": satu
        daftar pendek yang bisa dibaca sekali lihat, bukan seribu kalimat.

        Baris tanpa kode ikut dihitung sebagai ``UNKNOWN`` daripada
        disembunyikan. Yang tidak terkelompokkan adalah bagian dari jawabannya
        - kalau angkanya besar, hitungan di atasnya tidak selengkap
        penampilannya.
        """
        clause = " AND market_code = %s" if market else ""
        args: list[Any] = [to_mysql_datetime(since)]
        if market:
            args.append(market.value)
        rows = await self._db.fetch(
            "SELECT COALESCE(withheld_code, 'UNKNOWN') AS code, count(*) AS n "
            "FROM signals WHERE published = FALSE AND locked_at >= %s"
            f"{clause} GROUP BY COALESCE(withheld_code, 'UNKNOWN') "
            "ORDER BY n DESC, code",
            *args,
        )
        return [dict(r) for r in rows]

    async def latest_open(
        self, *, market: Market, symbol: str, horizon: str
    ) -> dict[str, Any] | None:
        """Prediksi terpublikasi yang masih berjalan untuk satu simbol+horizon.

        Dipakai penjaga duplikat (PASAL 11.6). Difilter pada ``published``,
        bukan hanya ``status = 'LOCKED'``: sebuah verdict yang ARUNA sendiri
        tolak publikasikan bukan prediksi yang sedang berjalan, dan
        memperlakukannya begitu akan membungkam simbol itu karena catatan yang
        tidak pernah dikirim ke siapa pun.
        """
        row = await self._db.fetchrow(
            """
            SELECT p.direction, p.reference_price, p.target_price,
                   p.regime, g.locked_at, g.signal_id
            FROM signals g
            JOIN signal_snapshots p ON p.signal_id = g.signal_id
            WHERE g.market_code = %s AND g.symbol = %s AND g.horizon_code = %s
              AND g.status = 'LOCKED' AND g.published = TRUE
            ORDER BY g.locked_at DESC
            LIMIT 1
            """,
            market.value,
            symbol,
            horizon,
        )
        return dict(row) if row else None

    async def latest_loss(
        self, *, market: Market, symbol: str, horizon: str
    ) -> dict[str, Any] | None:
        """Kekalahan terakhir yang tercatat untuk satu simbol+horizon.

        Dipakai cooldown (PASAL 11.5). ``exit_at`` yang dipakai mengurutkan,
        bukan ``locked_at``: yang memulai jeda adalah saat kekalahannya
        diketahui, bukan saat prediksinya dibuat - dua hal yang bisa terpisah
        berjam-jam pada horizon panjang.
        """
        row = await self._db.fetchrow(
            """
            SELECT t.exit_at, t.net_pnl_pct, p.direction, p.regime
            FROM paper_trades t
            JOIN signals g ON g.signal_id = t.signal_id
            JOIN signal_snapshots p ON p.signal_id = t.signal_id
            WHERE g.market_code = %s AND g.symbol = %s AND g.horizon_code = %s
              AND t.result = 'LOSS' AND t.exit_at IS NOT NULL
            ORDER BY t.exit_at DESC
            LIMIT 1
            """,
            market.value,
            symbol,
            horizon,
        )
        return dict(row) if row else None

    async def open_signals(
        self, *, market: Market | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Predictions ARUNA actually stands behind, still running.

        Filtered on ``published``, not merely on ``status = 'LOCKED'``. Every
        verdict is stored, including the WAITs and the calls the lock declined -
        listing those as open predictions turned a quiet market into a screen
        full of live signals.
        """
        clause = " AND s.market_code = %s" if market else ""
        args: list[Any] = [limit] if not market else [market.value, limit]
        rows = await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.horizon_code, s.direction, "
            "s.confidence, s.reference_price, s.target_price, s.expected_move_pct, "
            "s.locked_at, s.resolves_at FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id "
            "WHERE g.status = 'LOCKED' AND g.published = TRUE"
            f"{clause} ORDER BY s.locked_at DESC LIMIT %s",
            *args,
        )
        for row in rows:
            row["locked_at"] = as_utc(row["locked_at"])
            row["resolves_at"] = as_utc(row["resolves_at"])
        return rows

    async def since(
        self, *, reference: datetime, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Signals locked at or after ``reference``, with any outcome so far.

        LEFT JOIN on purpose: a signal whose horizon has not elapsed appears
        with empty outcome columns rather than being hidden, so a report cannot
        quietly show only the calls that have already been scored.
        """
        rows = await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.market_code, s.horizon_code, "
            "s.direction, s.confidence, s.reference_price, s.target_price, "
            "s.expected_move_pct, s.locked_at, s.resolves_at, g.status, "
            "g.published, g.withheld_reason, "
            "r.actual_move_pct, r.direction_correct, r.outcome_class, "
            "t.net_pnl, t.result "
            "FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id "
            "LEFT JOIN paper_results r ON r.signal_id = s.signal_id "
            "LEFT JOIN paper_trades t ON t.signal_id = s.signal_id "
            "WHERE s.locked_at >= %s ORDER BY s.locked_at DESC LIMIT %s",
            to_mysql_datetime(reference),
            limit,
        )
        for row in rows:
            row["locked_at"] = as_utc(row["locked_at"])
            row["resolves_at"] = as_utc(row["resolves_at"])
            row["published"] = bool(row["published"])
        return rows

    # ---- outcomes -------------------------------------------------------

    async def record_samples(self, samples: list[OutcomeSample]) -> int:
        for sample in samples:
            await self._db.execute(
                """
                INSERT INTO outcome_snapshots
                    (signal_id, offset_label, sampled_at, price, move_pct,
                     favourable, is_final)
                VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
                ON DUPLICATE KEY UPDATE
                    price      = new.price,
                    move_pct   = new.move_pct,
                    favourable = new.favourable,
                    is_final   = new.is_final
                """,
                sample.signal_id,
                sample.offset_label,
                to_mysql_datetime(sample.sampled_at),
                sample.price,
                round(sample.move_pct, 6),
                sample.favourable,
                sample.is_final,
            )
        return len(samples)

    async def record_outcome(self, outcome: SignalOutcome) -> None:
        await self._db.execute(
            """
            INSERT INTO paper_results
                (signal_id, original_direction, reference_price, final_price,
                 predicted_move_pct, actual_move_pct, prediction_error,
                 direction_correct, outcome_class, target_reached,
                 max_adverse_pct, max_favourable_pct, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            outcome.signal_id,
            outcome.original_direction.value,
            outcome.reference_price,
            outcome.final_price,
            outcome.predicted_move_pct,
            round(outcome.actual_move_pct, 6),
            outcome.prediction_error,
            outcome.direction_correct,
            outcome.outcome_class.value,
            outcome.target_reached,
            round(outcome.max_adverse_pct, 6),
            round(outcome.max_favourable_pct, 6),
            to_mysql_datetime(outcome.resolved_at),
        )

    # ---- paper trades ---------------------------------------------------

    async def record_trade(self, trade: PaperTrade) -> None:
        await self._db.execute(
            """
            INSERT INTO paper_trades
                (signal_id, market_code, symbol, direction, quantity, entry_price,
                 entry_at, exit_price, exit_at, holding_seconds, entry_fee,
                 exit_fee, slippage_cost, spread_cost, gross_pnl, net_pnl,
                 net_pnl_pct, target_multiple, result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                exit_price      = new.exit_price,
                exit_at         = new.exit_at,
                holding_seconds = new.holding_seconds,
                exit_fee        = new.exit_fee,
                gross_pnl       = new.gross_pnl,
                net_pnl         = new.net_pnl,
                net_pnl_pct     = new.net_pnl_pct,
                target_multiple = new.target_multiple,
                result          = new.result
            """,
            trade.signal_id,
            trade.market.value,
            trade.symbol,
            trade.direction.value,
            trade.quantity,
            trade.entry_price,
            to_mysql_datetime(trade.entry_at),
            trade.exit_price,
            to_mysql_datetime(trade.exit_at),
            int(trade.holding_seconds) if trade.holding_seconds is not None else None,
            trade.entry_fee,
            trade.exit_fee,
            trade.slippage_cost,
            trade.spread_cost,
            trade.gross_pnl,
            trade.net_pnl,
            round(trade.net_pnl_pct, 6),
            trade.target_multiple,
            trade.result.value,
        )

    async def performance(self) -> dict[str, Any]:
        """Net performance across closed paper trades (SPEC 34, 41)."""
        row = await self._db.fetchrow(
            "SELECT count(*) AS trades, "
            "sum(result = 'WIN') AS wins, sum(result = 'LOSS') AS losses, "
            "sum(net_pnl) AS net, sum(gross_pnl) AS gross, "
            "sum(entry_fee + exit_fee + slippage_cost + spread_cost) AS costs "
            "FROM paper_trades WHERE result <> 'OPEN'"
        )
        return dict(row) if row else {}

    async def accuracy(self) -> dict[str, Any]:
        """Directional accuracy over predictions ARUNA published.

        Withheld calls are excluded on purpose: the system said out loud that it
        would not stand behind them, and scoring them either way would measure
        it against claims it never made.
        """
        row = await self._db.fetchrow(
            "SELECT count(*) AS resolved, sum(r.direction_correct) AS correct, "
            "avg(abs(r.prediction_error)) AS mean_abs_error "
            "FROM paper_results r JOIN signals g ON g.signal_id = r.signal_id "
            "WHERE r.original_direction IN ('BUY', 'SELL') AND g.published = TRUE"
        )
        return dict(row) if row else {}

    async def published_ids(self, signal_ids: Sequence[str]) -> set[str]:
        """Dari sekumpulan id, mana yang prediksinya benar-benar diumumkan.

        Dipakai pesan hasil untuk menjawab pertanyaan yang sebelumnya tidak
        pernah ditanyakan: apakah operator pernah melihat prediksi ini?

        Terukur saat ditemukan: dalam dua belas jam, 73 prediksi berarah diskor
        tanpa pernah dipublikasikan - ditahan karena bukti basi, cooldown, atau
        duplikat - lawan 28 yang dipublikasikan. Hasil ketiganya didorong ke
        Telegram dengan cara yang sama, jadi mayoritas pesan hasil adalah kabar
        tentang prediksi yang tidak pernah ada di layar siapa pun.

        **Ini bukan pintu untuk menyembunyikan kekalahan (PASAL 11.21).**
        ``published`` diputuskan pada saat prediksi dikunci - jauh sebelum ada
        yang tahu ia menang atau kalah - jadi penyaringan ini tidak bisa
        memilih-milih hasil. Yang hilang hanya dorongannya: barisnya tetap
        tersimpan, tetap masuk hitungan win rate, tetap terbaca lewat ``/today``
        dan laporan harian. Kolom yang sama sudah dipakai :meth:`accuracy` dan
        penjaga duplikat dengan alasan yang sama.
        """
        wanted = [str(s) for s in signal_ids]
        if not wanted:
            return set()
        tanya = ", ".join(["%s"] * len(wanted))
        rows = await self._db.fetch(
            # `tanya` hanya berisi placeholder "%s" sebanyak id-nya - tidak ada
            # nilai yang masuk ke teks SQL. Id-nya sendiri lewat parameter.
            f"SELECT signal_id FROM signals "
            f"WHERE published = TRUE AND signal_id IN ({tanya})",
            *wanted,
        )
        return {str(r["signal_id"]) for r in rows}

    async def mark_pushed(
        self, signal_id: str, *, message_id: int | None, at: datetime
    ) -> None:
        """Catat bahwa signal ini benar-benar sampai ke Telegram.

        Ditulis oleh yang **mengirim**, bukan oleh yang mengunci. Itu seluruh
        maksudnya: ``published`` menjawab "layak diterbitkan" dan diputuskan
        saat penguncian, sementara pertanyaan yang dipakai pesan hasil adalah
        "pernah dilihat operator" - dan di antara keduanya ada gerbang yang
        menolak signal tanpa entry, stop, atau target.

        ``message_id`` boleh ``None``: pengirim yang tidak melaporkan id-nya
        tetap berhasil mengirim, dan ``pushed_at`` sendiri sudah cukup untuk
        menjawab pertanyaan pertama.
        """
        await self._db.execute(
            "UPDATE signals SET pushed_at = %s, telegram_message_id = %s "
            "WHERE signal_id = %s",
            at,
            message_id,
            str(signal_id),
        )

    async def pushed_message_ids(
        self, signal_ids: Sequence[str]
    ) -> dict[str, int | None]:
        """Signal mana yang benar-benar terkirim, dan pesan mana yang membawanya.

        Yang **tidak ada di hasilnya** tidak pernah terkirim. Nilai ``None``
        berarti terkirim tanpa id yang tercatat - dua keadaan yang berbeda dan
        harus tetap berbeda: yang pertama membungkam hasilnya, yang kedua hanya
        membuat hasilnya tidak bisa membalas.

        **Tidak ada kelonggaran untuk baris lama, dan itu keputusan yang
        diambil sesudah dua percobaan gagal.**

        Percobaan pertama: batasnya ``MIN(pushed_at)`` - mengeras sendiri pada
        pengiriman pertama yang tercatat. Gagal karena pengiriman itu tidak
        datang: dua jam sesudah pencatatan hidup, ``pushed_at`` masih NULL di
        seluruh 4.554 baris. Batas yang menunggu peristiwa yang belum tentu
        datang tidak pernah berlaku.

        Percobaan kedua: batasnya waktu penerapan migrasi, dan baris yang lebih
        tua **dianggap terkirim**. Gagal karena asumsinya salah. Terukur pada
        keluhan operator berikutnya: signal ``483bb3b78ad54e52`` dikunci 18
        Agustus - sehari penuh sebelum migrasi - dengan ``published = TRUE``,
        dan hasilnya sampai ke Telegram tanpa signalnya. Anggapan "yang lama
        pasti terkirim" adalah persis bug yang sedang diperbaiki, ditulis ulang
        sebagai kelonggaran.

        Yang tersisa adalah aturan yang tidak menebak: **hanya yang punya jejak
        yang punya hasil.** Baris lama tidak punya jejak, jadi hasilnya tidak
        didorong - untuk paling lama satu horizon sesudah migrasi, dan sesudah
        itu setiap signal yang terkirim membawa jejaknya sendiri.

        Harganya disebut terus terang: sebuah signal lama yang **memang**
        sampai ke operator kehilangan pemberitahuan hasilnya. Itu kehilangan
        yang nyata, dan dipilih karena kesalahan sebaliknya - hasil untuk
        signal yang tidak pernah ada di layar - adalah yang dilaporkan operator
        dua kali. Catatannya tetap utuh: barisnya tersimpan, masuk hitungan win
        rate, dan terbaca lewat ``/today`` serta laporan harian (PASAL 11.21
        melarang menghapus catatan, bukan melarang diam).
        """
        wanted = [str(s) for s in signal_ids]
        if not wanted:
            return {}
        tanya = ", ".join(["%s"] * len(wanted))
        rows = await self._db.fetch(
            f"SELECT signal_id, telegram_message_id FROM signals "
            f"WHERE pushed_at IS NOT NULL AND signal_id IN ({tanya})",
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


def _to_signal(row: dict[str, Any]) -> LockedSignal:
    return LockedSignal(
        signal_id=row["signal_id"],
        market=Market(row["market_code"]),
        symbol=row["symbol"],
        horizon=Horizon(row["horizon_code"]),
        direction=Decision(row["direction"]),
        confidence=float(row["confidence"]),
        reference_price=row["reference_price"],
        entry_price=row["entry_price"],
        target_price=row["target_price"],
        expected_move_pct=(
            float(row["expected_move_pct"])
            if row["expected_move_pct"] is not None
            else None
        ),
        locked_at=as_utc(row["locked_at"]),
        as_of=as_utc(row["as_of"]),
        resolves_at=as_utc(row["resolves_at"]),
        bid=row["bid"],
        ask=row["ask"],
        spread_bps=row["spread_bps"],
        reasoning=tuple(load_json(row["reasoning"]) or ()),
        regime=row["regime"],
        news_state=row["news_state"],
        risk_level=row["risk_level"],
        data_source=row["data_source"] or "",
        data_timestamp=as_utc(row["data_timestamp"]),
        model_version=row["model_version"],
        council_session_id=row["council_session_id"],
        status=SignalStatus(row["status"]),
        supersedes=row["supersedes"],
    )


__all__ = ["SignalRepository"]
