"""Learning storage (SPEC 25-30).

The read side is the interesting half. Autopsy needs a losing prediction joined
to the argument that produced it, and that join only works because PHASE 7
stores ``council_session_id`` on the locked signal.

Measurements (calibration, reliability) are inserted, never updated: the
triggers in migration 0010 enforce it. Findings about a single prediction are
upserted, because rerunning an improved analysis over an immutable record should
replace the finding rather than pile up duplicates.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Any

from aruna.backtest.korpus import Opini
from aruna.core.clock import now_utc
from aruna.core.enums import Stance, VetoReviewOutcome
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.learning.autopsy import Autopsy
from aruna.learning.calibration import CalibrationReport
from aruna.learning.counterfactual import Counterfactual, GhostSignal
from aruna.learning.reliability import ReliabilityReport

#: Stance yang berarti "menentang", untuk membedakannya dari SUPPORT.
#:
#: ``overruled_objections()`` dulu menyaring ``stance = 'OPPOSE'``. Stance tidak
#: pernah punya anggota bernama OPPOSE - yang ada SUPPORT / OBJECT /
#: COUNTER_PROPOSE / ACCEPT_CORRECTION - dan CHECK di migrasi 0007 melarang
#: nilai itu tersimpan. Query-nya karena itu selalu mengembalikan nol baris,
#: dan analisis vindikasi SPEC 26 kosong permanen tanpa satu pun error.
#: Diambil dari enum, bukan diketik ulang, supaya anggota yang berganti nama
#: jadi error dan tidak bisa lolos diam-diam lagi.
OBJECTING_STANCES: tuple[str, ...] = (
    Stance.OBJECT.value,
    Stance.COUNTER_PROPOSE.value,
)


def _gerak_satu_bar(
    deret: list[tuple[datetime, float]] | None, saat: datetime
) -> float | None:
    """Gerak harga satu bar ke depan dari ``saat``, dalam persen.

    ``None`` berarti belum bisa dinilai - bukan nol. Sebuah keputusan yang bar
    berikutnya belum tutup adalah keputusan yang belum sempat terbukti, dan
    menghitungnya sebagai kegagalan akan menghukum tiap keputusan terbaru
    justru karena ia terbaru. Bar yang belum tutup juga masih bergerak, jadi
    menilai terhadapnya membaca harga yang belum final (SPEC 24).
    """
    if not deret:
        return None
    # `deret` sudah terurut menurut waktu tutup.
    i = bisect_right([t for t, _ in deret], saat)
    if i == 0 or i >= len(deret):
        return None
    dasar = deret[i - 1][1]
    if dasar <= 0:
        return None
    return (deret[i][1] - dasar) / dasar * 100


class LearningRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- reading the record ---------------------------------------------

    async def resolved(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Resolved predictions joined to their outcome and paper trade.

        Directional and non-directional both: SPEC 28 needs the WAITs, and a
        query that quietly dropped them would make ghost signals impossible to
        find.
        """
        rows = await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.market_code, s.horizon_code, "
            "s.direction, "
            # Keyakinan MENTAH, bukan yang sudah dikalibrasi (bagian 9).
            # Membaca `s.confidence` di sini berarti mengukur keluaran
            # kalibrator dengan kalibrator - dan pada putaran kedua ia akan
            # melaporkan bahwa semuanya baik-baik saja, makin meyakinkan justru
            # makin salah. COALESCE untuk baris yang dikunci sebelum kolom ini
            # ada.
            "COALESCE(s.confidence_raw, s.confidence) AS confidence, "
            "s.reference_price, s.entry_price, "
            "s.target_price, s.expected_move_pct, s.locked_at, s.as_of, "
            "s.resolves_at, s.reasoning, s.regime, s.risk_level, s.news_state, "
            "s.council_session_id, g.published, "
            # **`resolved_at` sempat hilang dari daftar ini, dan diamnya
            # mahal.** `adaptive._strategy_slices` mengurutkan barisnya menurut
            # `resolved_at` SEBELUM menghitung drawdown - dan kolom yang tidak
            # pernah dipilih memulangkan `None` untuk tiap baris, jadi
            # pengurutannya menjadi tanpa efek dan `sorted` yang stabil
            # membiarkan urutan `locked_at DESC` apa adanya.
            #
            # Akibatnya `strategy_performance.max_drawdown` dihitung atas deret
            # TERBALIK. Ia tetap sebuah angka, tetap masuk akal dilihat, dan
            # tidak menggambarkan apa pun - persis bentuk kegagalan yang
            # docstring `drawdown` sendiri peringatkan.
            "r.resolved_at, r.outcome_class, r.direction_correct, "
            "r.actual_move_pct, r.predicted_move_pct, r.final_price, "
            "r.max_adverse_pct, r.max_favourable_pct, r.target_reached, "
            "t.net_pnl, t.result "
            "FROM signal_snapshots s "
            "JOIN signals g ON g.signal_id = s.signal_id "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "LEFT JOIN paper_trades t ON t.signal_id = s.signal_id "
            "ORDER BY s.locked_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["locked_at"] = as_utc(row["locked_at"])
            row["as_of"] = as_utc(row["as_of"])
            row["resolves_at"] = as_utc(row["resolves_at"])
            row["resolved_at"] = as_utc(row["resolved_at"])
            row["reasoning"] = load_json(row["reasoning"]) or []
            row["published"] = bool(row["published"])
        return rows

    async def council_context(self, session_id: int) -> dict[str, Any]:
        """The argument behind one prediction: weights, objections, vetoes."""
        judgement = await self._db.fetchrow(
            "SELECT weights, reasoning, minority_prevailed, unavailable_factors "
            "FROM judge_decisions WHERE session_id = %s",
            session_id,
        )
        objections = await self._db.fetch(
            "SELECT o.accuser, o.target, o.stance, o.ground, o.detail, o.severity, "
            # An objection counts as answered only when its own accuser was
            # conceded to on the same ground - not when any rebuttal happened.
            "EXISTS (SELECT 1 FROM agent_rebuttals b WHERE b.session_id = o.session_id "
            "AND b.accuser = o.accuser AND b.ground = o.ground AND b.conceded = TRUE) "
            "AS conceded "
            "FROM agent_objections o WHERE o.session_id = %s",
            session_id,
        )
        vetoes = await self._db.fetch(
            "SELECT v.reason, v.detail, v.raised_by, r.outcome, r.rationale "
            "FROM veto_events v LEFT JOIN veto_reviews r ON r.veto_id = v.id "
            "WHERE v.session_id = %s",
            session_id,
        )
        for row in objections:
            row["conceded"] = bool(row["conceded"])
        return {
            "weights": load_json(judgement["weights"]) if judgement else [],
            "judge_reasoning": (
                load_json(judgement["reasoning"]) if judgement else []
            ),
            "minority_prevailed": (
                bool(judgement["minority_prevailed"]) if judgement else False
            ),
            "objections": objections,
            "vetoes": vetoes,
        }

    #: Dimensi rincian keandalan (PASAL 11.2) dan kolom yang mengisinya.
    #:
    #: Rezim diambil dari ``signal_snapshots``, bukan dari ``council_sessions``:
    #: yang tersimpan bersama prediksi adalah rezim yang berlaku saat prediksi
    #: dibuat, dan itu yang harus dinilai. Sesi council tidak menyimpan rezim.
    BREAKDOWN_COLUMNS: dict[str, str] = {  # noqa: RUF012
        "regime": "COALESCE(s.regime, 'UNKNOWN')",
        "timeframe": "s.horizon_code",
        "asset": "s.symbol",
    }

    #: Dimensi PASAL 11.20 dan kolom yang mengisinya.
    #:
    #: ``direction`` diterjemahkan ke kosakata publik di SQL, bukan di Python:
    #: pengelompokannya harus terjadi pada nilai yang sama dengan yang dibaca
    #: operator, kalau tidak "LONG" di laporan dan "BUY" di database menjadi
    #: dua nama untuk satu hal yang harus dicocokkan orang di kepalanya.
    WINDOW_COLUMNS: dict[str, str] = {  # noqa: RUF012
        "asset": "t.symbol",
        "timeframe": "p.horizon_code",
        "regime": "COALESCE(p.regime, 'UNKNOWN')",
        "direction": (
            "CASE t.direction WHEN 'BUY' THEN 'LONG' "
            "WHEN 'SELL' THEN 'SHORT' ELSE t.direction END"
        ),
        "quality": "p.signal_quality",
    }

    async def window_rows(
        self, dimension: str, *, days: int | None = None, limit: int = 20000
    ) -> list[dict[str, Any]]:
        """Posisi kertas dalam satu jendela, dikelompokkan per dimensi (11.20).

        ``days=None`` berarti sepanjang waktu.

        Diurutkan pada ``exit_at`` dan bukan ``entry_at``: jendela "tujuh hari
        terakhir" untuk sebuah HASIL adalah tujuh hari sejak hasilnya
        diketahui. Posisi yang dibuka dua minggu lalu dan ditutup kemarin
        adalah kabar kemarin, bukan kabar dua minggu lalu - dan mengurutkannya
        pada pembukaan akan menyembunyikan hasil terbaru dari horizon panjang
        di jendela yang sudah lewat.

        Posisi yang masih terbuka ikut, dengan ``result`` apa adanya. Ia
        dihitung sebagai ``active`` di lapisan atas dan tidak masuk penyebut -
        tapi menyembunyikannya di sini akan membuat "belum ada apa-apa" tidak
        bisa dibedakan dari "semuanya masih berjalan".
        """
        column = self.WINDOW_COLUMNS.get(dimension)
        if column is None:
            raise ValueError(f"dimensi tidak dikenal: {dimension!r}")

        clause = ""
        args: list[Any] = []
        if days is not None:
            clause = " AND t.exit_at >= %s"
            args.append(to_mysql_datetime(now_utc() - timedelta(days=days)))
        args.append(limit)

        rows = await self._db.fetch(
            f"""
            SELECT {column} AS `key`, t.result AS result
            FROM paper_trades t
            JOIN signal_snapshots p ON p.signal_id = t.signal_id
            WHERE t.exit_at IS NOT NULL{clause}
            ORDER BY t.exit_at DESC
            LIMIT %s
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def agent_breakdown(
        self, dimension: str, *, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """Opini berarah tiap agent, dirinci per dimensi (PASAL 11.2).

        Sumbernya ``council_votes`` - suara yang benar-benar diberikan tiap
        agent - bukan bobot judge. Keduanya menjawab pertanyaan berbeda: bobot
        judge menyimpan bagaimana sebuah opini DITIMBANG, dan tabel ini
        menyimpan apa yang agent itu KATAKAN.

        ``correct`` dihitung dari arah pasar, bukan dari kesepakatan dengan
        council. Agent yang sepakat berbagi hasil council; agent yang menentang
        benar tepat ketika council salah. Menilainya dari kesepakatan akan
        mengukur kepatuhan, bukan keandalan - dan seorang agent yang selalu
        ikut suara terbanyak akan terlihat paling andal justru karena tidak
        pernah menyumbang apa pun.

        Hanya opini BERARAH yang masuk. Agent yang abstain tidak menyatakan apa
        pun, dan yang bilang tidak-ada-posisi tidak bisa benar atau salah
        terhadap pergerakan harga.
        """
        column = self.BREAKDOWN_COLUMNS.get(dimension)
        if column is None:
            raise ValueError(f"dimensi tidak dikenal: {dimension!r}")

        rows = await self._db.fetch(
            f"""
            SELECT v.role AS agent,
                   {column} AS `key`,
                   v.decision AS agent_decision,
                   s.direction AS council_decision,
                   r.direction_correct AS council_correct
            FROM council_votes v
            JOIN signal_snapshots s ON s.council_session_id = v.council_session_id
            JOIN signals g ON g.signal_id = s.signal_id
            JOIN paper_results r ON r.signal_id = s.signal_id
            WHERE v.decision IN ('BUY', 'SELL')
              AND s.direction IN ('BUY', 'SELL')
              AND g.published = TRUE
              AND v.abstained = FALSE
            ORDER BY s.locked_at DESC
            LIMIT %s
            """,
            limit,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            sepakat = row["agent_decision"] == row["council_decision"]
            benar = bool(row["council_correct"])
            out.append({
                "agent": row["agent"],
                "key": row["key"],
                # Sepakat -> berbagi hasil council. Menentang -> benar tepat
                # ketika council salah.
                "correct": benar if sepakat else not benar,
            })
        return out

    async def agent_outcomes(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """One row per agent opinion in a resolved session (SPEC 30).

        The agent's own decision comes from the stored judge weights, so an
        agent is scored on what it argued rather than on what the council
        concluded.

        **Sumbernya keputusan council HIDUP yang dinilai dari candle tersimpan,
        bukan lagi hasil spot.** Versi sebelumnya berlabuh di
        ``signal_snapshots`` -> ``signals`` -> ``paper_results``; ketiganya
        berhenti tumbuh ketika jalur spot dicabut (2026-08-25). Kuerinya tidak
        gagal - ia memulangkan baris beku yang sama selamanya, jadi
        ``build_reliability`` menghitung ulang angka yang identik tiap siklus
        dan pengali agen tidak pernah lagi bergerak. Terukur: dua snapshot
        berurutan sama persis sampai empat desimal.

        Cacat itu tidak bisa dilihat dari mana pun. Tabelnya terisi, kuerinya
        sukses, loop-nya jalan - yang mati cuma pertumbuhannya.

        Gantinya tidak menuntut jalur baru: ``council_sessions`` dan
        ``judge_decisions`` tumbuh tiap siklus, dan gerak harga sesudah tiap
        keputusan ada di ``candles``. Definisi "benar" diambil dari
        :class:`~aruna.backtest.korpus.Opini` - satu definisi untuk replay dan
        untuk keputusan hidup, supaya keduanya tidak bisa berpisah diam-diam.

        ``futures_ghost_results`` sengaja TIDAK dipakai meski 7.240 baris dan
        tumbuh tiap jam: ``max_move_pct`` di sana besaran tanpa tanda (0 dari
        7.240 negatif) dan ``side`` selalu FLAT. Ia bisa menjawab "ada gerakan
        selagi ARUNA diam", tidak bisa menjawab "ke arah mana". Agen yang
        dinilai dari sana hanya akan mempelajari satu hal - bicara lebih banyak
        - dan itu jalan kembali ke akurasi 50,4% yang baru saja ditinggalkan.
        """
        rows = await self._db.fetch(
            "SELECT c.id, c.market_code, c.symbol, c.interval_code, "
            "c.decided_at, c.decision AS council_decision, j.weights "
            "FROM council_sessions c "
            "JOIN judge_decisions j ON j.session_id = c.id "
            "WHERE c.decision IN ('BUY', 'SELL') "
            "ORDER BY c.decided_at DESC LIMIT %s",
            limit,
        )
        if not rows:
            return []

        deret = await self._deret_penutupan(rows)

        out: list[dict[str, Any]] = []
        for row in rows:
            gerak = _gerak_satu_bar(
                deret.get((str(row["market_code"]), str(row["symbol"]),
                           str(row["interval_code"]))),
                as_utc(row["decided_at"]),
            )
            # Belum ada bar yang tertutup sesudah keputusan ini. Dilewati, BUKAN
            # dihitung nol: keputusan yang belum sempat terbukti bukan keputusan
            # yang salah, dan memasukkannya sebagai kegagalan akan menghukum
            # setiap keputusan terbaru justru karena ia terbaru.
            if gerak is None:
                continue

            benar = Opini(
                symbol=str(row["symbol"]),
                pada=as_utc(row["decided_at"]),
                agen="",
                arah=str(row["council_decision"]),
                keyakinan=0.0,
                council=str(row["council_decision"]),
                gerak_pct=gerak,
            ).benar

            for weight in load_json(row["weights"]) or []:
                out.append(
                    {
                        "agent": weight.get("role"),
                        "agent_decision": weight.get("decision"),
                        "council_decision": row["council_decision"],
                        "direction_correct": bool(benar),
                    }
                )
        return out

    async def _deret_penutupan(
        self, sesi: list[dict[str, Any]]
    ) -> dict[tuple[str, str, str], list[tuple[datetime, float]]]:
        """Semua penutupan yang dibutuhkan seluruh sesi, dalam SATU kueri.

        **Versi pertama menanyakan dua kueri per sesi dan tidak pernah
        selesai.** Enam ribu perjalanan bolak-balik, dan tiap satunya
        pemindaian tabel penuh - karena kueri itu menyaring ``close_time``,
        yang tidak punya indeks. Yang ada ``candles_lookup_idx`` atas
        ``(market_code, symbol, interval_code, open_time)``.

        Jadi penyaringannya memakai ``open_time``, dan urutannya identik:
        ``close_time = open_time + interval`` untuk tiap baris di satu
        interval, jadi mengurutkan salah satunya mengurutkan keduanya. Batas
        bawahnya dilonggarkan satu langkah lewat ``MIN(decided_at)`` supaya bar
        yang menaungi keputusan paling awal tetap terbawa.

        Ini berjalan di dalam loop upkeep, jadi biayanya bukan detail: satu pass
        yang menghabiskan puluhan detik akan menaikkan waktu siklus, dan itu
        persis cacat yang baru saja dicabut dari loop ini.
        """
        pasangan = {
            (str(r["market_code"]), str(r["symbol"]), str(r["interval_code"]))
            for r in sesi
        }
        paling_awal = min(as_utc(r["decided_at"]) for r in sesi)

        pasar = sorted({p[0] for p in pasangan})
        slot = ", ".join(["%s"] * len(pasar))
        baris = await self._db.fetch(
            "SELECT market_code, symbol, interval_code, close_time, close "
            f"FROM candles WHERE market_code IN ({slot}) AND open_time >= %s "
            "ORDER BY market_code, symbol, interval_code, open_time",
            *pasar,
            to_mysql_datetime(paling_awal - timedelta(days=2)),
        )

        deret: dict[tuple[str, str, str], list[tuple[datetime, float]]] = {}
        for b in baris:
            kunci = (
                str(b["market_code"]), str(b["symbol"]), str(b["interval_code"])
            )
            if kunci not in pasangan:
                continue
            deret.setdefault(kunci, []).append(
                (as_utc(b["close_time"]), float(b["close"]))
            )
        return deret

    async def overruled_objections(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Objections raised against a call that was made anyway (SPEC 26)."""
        stances = ", ".join(["%s"] * len(OBJECTING_STANCES))
        return await self._db.fetch(
            "SELECT o.accuser, o.ground, r.direction_correct "
            "FROM signal_snapshots s "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "JOIN agent_objections o ON o.session_id = s.council_session_id "
            f"WHERE o.stance IN ({stances}) AND s.direction IN ('BUY', 'SELL') "
            "LIMIT %s",
            *OBJECTING_STANCES,
            limit,
        )

    async def rejected_vetoes(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Veto yang ditolak atas keputusan yang tetap dibuat (bagian 18.13).

        Sejajar dengan :meth:`overruled_objections`, dan sengaja: sebuah
        keberatan yang dikesampingkan lalu ternyata benar adalah titik buta,
        entah ia datang sebagai objection atau sebagai veto.

        **Hanya yang ``VETO_REJECTED``.** Veto yang ditegakkan menghentikan
        sinyalnya, jadi tidak ada ``paper_results`` untuk dibandingkan - dan
        JOIN ini memang tidak akan menemukannya. Batas itu bukan kelalaian
        kueri melainkan kenyataan: kita tidak akan pernah tahu apa yang akan
        terjadi kalau vetonya tidak ada.
        """
        return await self._db.fetch(
            "SELECT v.reason, r.direction_correct "
            "FROM signal_snapshots s "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "JOIN veto_events v ON v.session_id = s.council_session_id "
            "JOIN veto_reviews w ON w.veto_id = v.id "
            "WHERE w.outcome = %s AND s.direction IN ('BUY', 'SELL') "
            "LIMIT %s",
            VetoReviewOutcome.VETO_REJECTED.value,
            limit,
        )

    async def upheld_vetoes(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Veto yang DITEGAKKAN, berikut jangkauan pasar sesudahnya (18.13).

        Pasangan :meth:`rejected_vetoes`, dan keduanya perlu karena keduanya
        menjawab pertanyaan yang berbeda. Terukur 2026-08-24: dari 279 veto di
        ARUNA, nol pernah ditolak - jadi ukuran "ditolak lalu benar" benar dan
        tidak akan pernah menyala.

        Yang ini menjawab contoh bagian 18.13 apa adanya: veto atas volatilitas
        ekstrem, lalu pasar bergejolak.

        Keputusannya WAIT karena vetonya menahan, jadi jangkauannya datang dari
        ``max_favourable_pct`` dan ``max_adverse_pct`` - dua ujung terjauh yang
        pasar capai selama horizon, sama dengan yang dipakai ghost signal.
        """
        return await self._db.fetch(
            "SELECT v.reason, r.max_favourable_pct, r.max_adverse_pct "
            "FROM signal_snapshots s "
            "JOIN paper_results r ON r.signal_id = s.signal_id "
            "JOIN veto_events v ON v.session_id = s.council_session_id "
            "JOIN veto_reviews w ON w.veto_id = v.id "
            "WHERE w.outcome <> %s AND s.direction NOT IN ('BUY', 'SELL') "
            "LIMIT %s",
            VetoReviewOutcome.VETO_REJECTED.value,
            limit,
        )

    # ---- writing findings ------------------------------------------------

    async def record_autopsy(self, autopsy: Autopsy) -> None:
        await self._db.execute(
            """
            INSERT INTO loss_autopsies
                (signal_id, outcome_class, hypothesis, sebab, confidence,
                 predicted_move_pct, actual_move_pct, max_adverse_pct, net_pnl,
                 regime, risk_level, news_state, backers, dissenters,
                 unanswered_objections, rejected_vetoes, findings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                outcome_class         = new.outcome_class,
                hypothesis            = new.hypothesis,
                sebab                 = new.sebab,
                backers               = new.backers,
                dissenters            = new.dissenters,
                unanswered_objections = new.unanswered_objections,
                rejected_vetoes       = new.rejected_vetoes,
                findings              = new.findings,
                performed_at          = CURRENT_TIMESTAMP(6)
            """,
            autopsy.signal_id,
            autopsy.outcome_class.value,
            autopsy.hypothesis[:255],
            # Bagian 12: KENAPA, bukan apa. Tanpa baris ini klasifikasinya
            # dihitung lalu dibuang - terlihat di keluaran CLI, tidak pernah
            # bisa dikueri, tidak pernah bisa dipakai pembelajaran.
            autopsy.sebab.value,
            round(autopsy.confidence, 3),
            autopsy.predicted_move_pct,
            round(autopsy.actual_move_pct, 6),
            round(autopsy.max_adverse_pct, 6),
            autopsy.net_pnl,
            autopsy.regime,
            autopsy.risk_level,
            autopsy.news_state,
            dump_json([{"agent": a, "weight": w} for a, w in autopsy.backers]),
            dump_json(list(autopsy.dissenters)),
            dump_json(list(autopsy.unanswered_objections)),
            dump_json(list(autopsy.rejected_vetoes)),
            dump_json(list(autopsy.findings)),
        )

    async def record_counterfactual(self, item: Counterfactual) -> None:
        await self._db.execute(
            """
            INSERT INTO counterfactuals
                (signal_id, taken, taken_move_pct, alternative,
                 alternative_move_pct, alternative_was_better)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                taken_move_pct         = new.taken_move_pct,
                alternative_move_pct   = new.alternative_move_pct,
                alternative_was_better = new.alternative_was_better,
                computed_at            = CURRENT_TIMESTAMP(6)
            """,
            item.signal_id,
            item.taken.value,
            round(item.taken_move_pct, 6),
            item.alternative.value,
            round(item.alternative_move_pct, 6),
            item.alternative_was_better,
        )

    async def record_ghost(self, ghost: GhostSignal) -> None:
        await self._db.execute(
            """
            INSERT INTO ghost_signals
                (signal_id, symbol, horizon_code, missed_move_pct, direction,
                 why_we_waited)
            VALUES (%s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                missed_move_pct = new.missed_move_pct,
                direction       = new.direction,
                why_we_waited   = new.why_we_waited,
                computed_at     = CURRENT_TIMESTAMP(6)
            """,
            ghost.signal_id,
            ghost.symbol,
            ghost.horizon,
            round(ghost.missed_move_pct, 6),
            ghost.direction.value,
            dump_json(list(ghost.reasoning)),
        )

    async def record_calibration(self, report: CalibrationReport) -> int:
        """Append a measurement. Never an update - see migration 0010."""
        return await self._db.insert(
            """
            INSERT INTO calibration_snapshots
                (total_resolved, correct, brier_score, sufficient_sample,
                 verdict, buckets)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            report.total,
            report.correct,
            report.brier,
            report.sufficient,
            report.verdict[:255],
            dump_json([b.to_dict() for b in report.buckets]),
        )

    async def record_reliability(self, report: ReliabilityReport) -> int:
        for record in report.records:
            await self._db.execute(
                """
                INSERT INTO agent_reliability
                    (agent, scored_opinions, correct, accuracy, multiplier,
                     vindicated, overruled_correctly, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                record.role.value,
                record.scored,
                record.correct,
                record.accuracy,
                record.multiplier,
                record.vindicated,
                record.overruled_correctly,
                record.status,
            )
        return len(report.records)

    # ---- reading findings back -------------------------------------------

    async def autopsies(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT a.*, s.symbol, s.horizon_code, s.direction "
            "FROM loss_autopsies a "
            "JOIN signal_snapshots s ON s.signal_id = a.signal_id "
            "ORDER BY a.performed_at DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["performed_at"] = as_utc(row["performed_at"])
            for field in ("backers", "dissenters", "findings",
                          "unanswered_objections", "rejected_vetoes"):
                row[field] = load_json(row[field]) or []
        return rows

    async def ghosts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM ghost_signals "
            "ORDER BY abs(missed_move_pct) DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["computed_at"] = as_utc(row["computed_at"])
            row["why_we_waited"] = load_json(row["why_we_waited"]) or []
        return rows

    async def latest_calibration(self) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM calibration_snapshots ORDER BY measured_at DESC LIMIT 1"
        )
        if row:
            row["measured_at"] = as_utc(row["measured_at"])
            row["buckets"] = load_json(row["buckets"]) or []
            row["sufficient_sample"] = bool(row["sufficient_sample"])
        return row

    async def reliability_as_of(self, moment: datetime) -> list[dict[str, Any]]:
        """Each agent's most recent measurement at or before ``moment``.

        This is what the append-only rule on `agent_reliability` buys: replaying
        a past decision needs the weights that were in force then, and a table
        that overwrote itself could only ever offer today's.
        """
        return await self._db.fetch(
            "SELECT a.* FROM agent_reliability a "
            "JOIN (SELECT agent, max(measured_at) AS latest FROM agent_reliability "
            "      WHERE measured_at <= %s GROUP BY agent) m "
            "  ON m.agent = a.agent AND m.latest = a.measured_at "
            "ORDER BY a.agent",
            to_mysql_datetime(moment),
        )

    async def calibration_as_of(self, moment: datetime) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM calibration_snapshots WHERE measured_at <= %s "
            "ORDER BY measured_at DESC LIMIT 1",
            to_mysql_datetime(moment),
        )
        if row:
            row["measured_at"] = as_utc(row["measured_at"])
            row["buckets"] = load_json(row["buckets"]) or []
            row["sufficient_sample"] = bool(row["sufficient_sample"])
        return row

    async def latest_reliability(self) -> list[dict[str, Any]]:
        return await self._db.fetch(
            "SELECT a.* FROM agent_reliability a "
            "JOIN (SELECT agent, max(measured_at) AS latest FROM agent_reliability "
            "      GROUP BY agent) m "
            "  ON m.agent = a.agent AND m.latest = a.measured_at "
            "ORDER BY a.agent"
        )

    async def drift_windows(
        self, *, days: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Behaviour before and during the last ``days``, for drift detection.

        Split on ``resolved_at`` rather than lock time: a prediction belongs to
        the period whose outcome it reports, and grouping by when it was made
        would put a still-running call in the window it has not finished.
        """

        async def window(clause: str) -> dict[str, Any]:
            row = await self._db.fetchrow(
                "SELECT count(*) AS resolved, sum(r.direction_correct) AS correct "
                "FROM paper_results r JOIN signals g ON g.signal_id = r.signal_id "
                "WHERE r.original_direction IN ('BUY','SELL') AND g.published = TRUE "
                f"AND r.resolved_at {clause}",
                days,
            )
            regimes = await self._db.fetch(
                "SELECT s.regime, count(*) AS n FROM paper_results r "
                "JOIN signal_snapshots s ON s.signal_id = r.signal_id "
                f"WHERE s.regime IS NOT NULL AND r.resolved_at {clause} "
                "GROUP BY s.regime",
                days,
            )
            return {
                "resolved": int(row["resolved"] or 0) if row else 0,
                "correct": int(row["correct"] or 0) if row else 0,
                "regimes": {r["regime"]: int(r["n"]) for r in regimes},
            }

        return (
            await window("< (NOW(6) - INTERVAL %s DAY)"),
            await window(">= (NOW(6) - INTERVAL %s DAY)"),
        )

    async def performance_window(self, *, days: int) -> dict[str, Any]:
        """Net performance over a period (SPEC 41 weekly and monthly)."""
        row = await self._db.fetchrow(
            "SELECT count(*) AS trades, sum(t.result = 'WIN') AS wins, "
            "sum(t.result = 'LOSS') AS losses, sum(t.net_pnl) AS net, "
            "sum(t.gross_pnl) AS gross, "
            "sum(t.entry_fee + t.exit_fee + t.slippage_cost + t.spread_cost) AS costs "
            "FROM paper_trades t WHERE t.result <> 'OPEN' "
            "AND t.exit_at >= (NOW(6) - INTERVAL %s DAY)",
            days,
        )
        accuracy = await self._db.fetchrow(
            "SELECT count(*) AS resolved, sum(r.direction_correct) AS correct "
            "FROM paper_results r WHERE r.original_direction IN ('BUY', 'SELL') "
            "AND r.resolved_at >= (NOW(6) - INTERVAL %s DAY)",
            days,
        )
        return {
            "days": days,
            **(dict(row) if row else {}),
            **(dict(accuracy) if accuracy else {}),
        }


__all__ = ["LearningRepository"]
