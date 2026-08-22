"""Council storage (SPEC 42).

Everything is kept, including rejected vetoes and answered objections. SPEC 26
later asks which objections turned out to be correct, and that question cannot
be answered if the losing side of an argument is discarded.
"""

from __future__ import annotations

from typing import Any

from aruna.council.session import CouncilVerdict
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime

#: Name of the cross-process write lock guarding one council session's rows.
#:
#: Both processes save council sessions - ``aruna-run`` through
#: :class:`~aruna.signals.service.SignalService` and ``futures-loop`` through
#: :class:`~aruna.futures.service.FuturesPlanService`, the latter twenty symbols
#: at a time - and the session ids handed out inside one tick are consecutive,
#: so concurrent savers always write neighbouring rows.  See
#: :meth:`~aruna.db.pool.Database.write_lock`.
COUNCIL_WRITE_LOCK = "council_votes"


class CouncilRepository:
    def __init__(self, db: Database, *, phase: int) -> None:
        self._db = db
        self._phase = phase

    async def _save_votes(self, session_id: int, verdict: CouncilVerdict) -> None:
        """Simpan suara tiap agent, bukan hanya jumlahnya (PASAL 11.10).

        Sebelum metode ini ada, sesi council menyimpan agregat - berapa agent
        ikut, berapa keberatan diajukan - dan ``verdict.opinions`` berhenti di
        memori. Terukur: 154 sesi tersimpan, nol suara agent.

        Akibatnya bukan "kurang detail". Keandalan agent, pertanggungjawaban,
        bobot adaptif, dan peringkat agent di laporan harian semuanya menghitung
        dari baris-baris ini; tanpa mereka satu-satunya papan peringkat yang
        bisa dibuat adalah karangan - dan karangan itu terbaca sama
        meyakinkannya dengan yang benar.

        Kesepakatan dinilai dengan **kosakata publik**: seorang agent yang
        bilang WAIT sementara council memutuskan NO_SIGNAL sepakat pada apa yang
        sampai ke operator - keduanya berarti tidak ada posisi - dan mencatatnya
        sebagai penentangan menampilkan perpecahan yang tidak pernah terjadi.

        **Satu perjalanan, bukan sebelas.** Barisnya disusun dulu lalu dikirim
        sekali dengan ``executemany``. Yang disimpan persis sama; yang hilang
        hanya sepuluh perjalanan bolak-balik ke MySQL per simbol. Lihat catatan
        ongkosnya di :meth:`save`.
        """
        from aruna.notify.verdict import public_decision

        putusan = public_decision(verdict.decision)
        await self._db.execute(
            "DELETE FROM council_votes WHERE council_session_id = %s", session_id
        )
        baris = []
        for opinion in verdict.opinions:
            abstain = bool(opinion.abstained)
            baris.append(
                (
                    session_id,
                    opinion.role.value,
                    opinion.decision.value,
                    round(float(opinion.confidence), 3),
                    abstain,
                    # Agent yang abstain tidak pernah dicatat sepakat: ia tidak
                    # menyatakan apa pun, dan menghitungnya sebagai dukungan
                    # menaikkan angka "setuju" dengan suara yang tidak diberikan.
                    (not abstain) and public_decision(opinion.decision) == putusan,
                    dump_json(list(opinion.reasoning)),
                    dump_json([e.to_dict() for e in opinion.evidence])
                    if opinion.evidence
                    else None,
                    len(opinion.evidence),
                )
            )
        if baris:
            await self._db.executemany(
                """
                INSERT INTO council_votes
                    (council_session_id, role, decision, confidence, abstained,
                     agreed_with_council, reasoning, evidence, evidence_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                baris,
            )

    async def save(self, asset_id: int, verdict: CouncilVerdict) -> int:
        """Simpan satu sesi council, satu penulis pada satu waktu.

        **Kenapa digilir.** Isi sesungguhnya ada di :meth:`_write`; yang
        ditambahkan di sini hanya antreannya, dan antrean itulah perbaikannya.
        ``_write`` menghapus lalu menulis ulang baris-baris satu sesi di enam
        tabel, dan dua puluh simbol dalam satu tick futures menjalankannya
        bersamaan lewat ``asyncio.gather``. Id sesi yang dibagikan dalam satu
        tick berurutan (terukur: 13814-13825), jadi baris dua penyimpan yang
        bersamaan selalu bertetangga di ``council_votes_unique`` - dan dua
        ``INSERT`` yang bertetangga saling mengunci celah di antaranya. Lihat
        :meth:`~aruna.db.pool.Database.write_lock` untuk mekanismenya.

        Yang hilang saat itu terjadi bukan hanya baris yang gagal ditulis.
        ``_write`` tidak transaksional: ``council_sessions`` sudah tersimpan dan
        ``DELETE FROM council_votes`` sudah dijalankan sebelum ``INSERT``-nya
        kena deadlock, jadi sesi itu tertinggal **tanpa satu pun suara**.
        Terukur di basis data yang berjalan: 196 dari 5.461 sesi (3,6%) kosong,
        dan setiap statistik keandalan agent menghitung dari baris yang tidak
        pernah sampai.
        """
        async with self._db.write_lock(COUNCIL_WRITE_LOCK):
            return await self._write(asset_id, verdict)

    async def _write(self, asset_id: int, verdict: CouncilVerdict) -> int:
        """Simpan satu sesi council beserta seluruh argumennya.

        **Yang dikelompokkan, dan kenapa.** Versi lama menulis satu baris per
        perjalanan ke MySQL: sebelas suara agent, satu keberatan per keberatan,
        satu sanggahan per sanggahan. Untuk ETH - enam belas keberatan - itu
        sekitar empat puluh perjalanan bolak-balik untuk satu simbol.

        Terukur pada tick dua puluh simbol: ``save`` menghabiskan 1206 ms
        rata-rata dan 24,1 detik kalau dijumlah - biaya terbesar seluruh tick,
        di atas jaringan bursa. Kolam koneksi berisi sepuluh, jadi dua puluh
        simbol yang berjalan bersamaan saling menunggu giliran, dan tiap
        perjalanan tambahan dibayar oleh semua yang mengantre.

        ``executemany`` mengirim baris-baris yang sama dalam satu perjalanan.
        Yang tersimpan tidak berubah sedikit pun - PASAL 11.21 tetap berlaku,
        sisi yang kalah dalam sebuah argumen tetap disimpan utuh, termasuk veto
        yang ditolak dan keberatan yang terjawab.

        ``veto_events`` sengaja tidak ikut dikelompokkan: tiap barisnya butuh
        ``lastrowid``-nya sendiri untuk menautkan ``veto_reviews``, dan veto
        jarang - mengelompokkannya menukar kejelasan dengan penghematan yang
        tidak ada.
        """
        protest = verdict.protest
        conceded = sum(1 for r in protest.rebuttals if r.conceded)

        session_id = await self._db.insert(
            """
            INSERT INTO council_sessions
                (asset_id, market_code, symbol, interval_code, as_of, decided_at,
                 decision, confidence, participating_agents, total_agents,
                 rounds_run, disagreement, objection_count, correction_count,
                 risk_level, veto_raised, veto_upheld, blocked, no_trade_reasons,
                 notes, is_locked_signal, phase)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                id                   = LAST_INSERT_ID(council_sessions.id),
                decided_at           = new.decided_at,
                decision             = new.decision,
                confidence           = new.confidence,
                participating_agents = new.participating_agents,
                total_agents         = new.total_agents,
                rounds_run           = new.rounds_run,
                disagreement         = new.disagreement,
                objection_count      = new.objection_count,
                correction_count     = new.correction_count,
                risk_level           = new.risk_level,
                veto_raised          = new.veto_raised,
                veto_upheld          = new.veto_upheld,
                blocked              = new.blocked,
                no_trade_reasons     = new.no_trade_reasons,
                notes                = new.notes
            """,
            asset_id,
            verdict.market,
            verdict.symbol,
            verdict.interval,
            to_mysql_datetime(verdict.as_of),
            to_mysql_datetime(verdict.decided_at),
            verdict.decision.value,
            round(verdict.confidence, 3),
            verdict.participating,
            len(verdict.opinions),
            len(verdict.rounds_run),
            round(protest.disagreement, 3),
            len(protest.objections),
            conceded,
            verdict.risk.overall.value,
            len(verdict.veto.vetoes),
            len(verdict.veto.upheld),
            verdict.no_trade.blocked,
            dump_json([r.value for r in verdict.no_trade.reasons]),
            dump_json(list(verdict.notes)),
            False,
            self._phase,
        )

        await self._save_votes(session_id, verdict)

        # Recomputing a session replaces its record rather than accumulating
        # duplicate arguments from a previous run.
        for table in ("agent_objections", "agent_rebuttals", "judge_decisions"):
            await self._db.execute(
                f"DELETE FROM {table} WHERE session_id = %s", session_id
            )
        await self._db.execute(
            "DELETE FROM veto_events WHERE session_id = %s", session_id
        )

        keberatan = [
            (
                session_id,
                objection.accuser.value,
                objection.target.value,
                objection.stance.value,
                objection.ground,
                objection.detail,
                round(objection.severity, 3),
            )
            for objection in (*protest.objections, *protest.supports)
        ]
        if keberatan:
            await self._db.executemany(
                """
                INSERT INTO agent_objections
                    (session_id, accuser, target, stance, ground, detail, severity)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                keberatan,
            )

        sanggahan = [
            (
                session_id,
                rebuttal.target.value,
                rebuttal.accuser.value,
                rebuttal.ground,
                rebuttal.stance.value,
                rebuttal.detail,
                rebuttal.conceded,
            )
            for rebuttal in protest.rebuttals
        ]
        if sanggahan:
            await self._db.executemany(
                """
                INSERT INTO agent_rebuttals
                    (session_id, target, accuser, ground, stance, detail, conceded)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                sanggahan,
            )

        for review in verdict.veto.reviews:
            veto_id = await self._db.insert(
                """
                INSERT INTO veto_events
                    (session_id, raised_by, reason, detail, evidence_key)
                VALUES (%s, %s, %s, %s, %s)
                """,
                session_id,
                review.veto.raised_by.value,
                review.veto.reason.value,
                review.veto.detail,
                review.veto.evidence_key or None,
            )
            await self._db.execute(
                "INSERT INTO veto_reviews (veto_id, outcome, rationale) "
                "VALUES (%s, %s, %s)",
                veto_id,
                review.outcome.value,
                review.rationale,
            )

        judgement = verdict.judgement
        await self._db.execute(
            """
            INSERT INTO judge_decisions
                (session_id, decision, confidence, buy_weight, sell_weight,
                 minority_prevailed, reasoning, weights, unavailable_factors)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            session_id,
            judgement.decision.value,
            round(judgement.confidence, 3),
            round(judgement.buy_weight, 5),
            round(judgement.sell_weight, 5),
            judgement.minority_prevailed,
            dump_json(list(judgement.reasoning)),
            dump_json([w.to_dict() for w in judgement.weights]),
            dump_json(list(judgement.unavailable_factors)),
        )
        return session_id

    async def latest(self, asset_id: int, interval: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT s.id, s.as_of, s.decided_at, s.decision, s.confidence, "
            "s.participating_agents, s.total_agents, s.rounds_run, s.disagreement, "
            "s.objection_count, s.correction_count, s.risk_level, s.veto_raised, "
            "s.veto_upheld, s.blocked, s.no_trade_reasons, s.notes, "
            # SPEC 16: whether the smaller camp won lives on the judge's row,
            # and is the single most audit-worthy fact about a verdict.
            "COALESCE(j.minority_prevailed, FALSE) AS minority_prevailed, "
            # Also SPEC 16, and the reason /council could not tell the truth:
            # which judge factors had no measurement behind them is decided per
            # session and stored here, but was never selected. The report
            # therefore asserted "reliability and calibration are unavailable"
            # unconditionally - correct on day one, wrong the moment either
            # cleared its sample threshold, and unable to notice either way.
            "j.unavailable_factors "
            "FROM council_sessions s "
            "LEFT JOIN judge_decisions j ON j.session_id = s.id "
            "WHERE s.asset_id = %s AND s.interval_code = %s "
            "ORDER BY s.as_of DESC LIMIT 1",
            asset_id,
            interval,
        )
        if row:
            row["as_of"] = as_utc(row["as_of"])
            row["decided_at"] = as_utc(row["decided_at"])
            row["no_trade_reasons"] = load_json(row["no_trade_reasons"])
            row["notes"] = load_json(row["notes"])
            row["minority_prevailed"] = bool(row["minority_prevailed"])
            # NULL when no judge row was joined, which is not the same as "all
            # factors were measured" - the caller has to be able to tell those
            # apart, so the None survives rather than collapsing to [].
            raw = row.get("unavailable_factors")
            row["unavailable_factors"] = load_json(raw) if raw is not None else None
        return row

__all__ = ["CouncilRepository"]
