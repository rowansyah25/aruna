"""Penyimpanan Phase 12: pola, strategi, performa, dan jejak pembelajaran.

Semua yang ditulis di sini bisa dihitung ulang dari `signals`, `paper_trades`
dan `council_votes`. Itu properti yang disengaja - baris di sini adalah hasil
analisis, bukan catatan. Kalau salah, ia dibuang dan dibangun ulang tanpa satu
fakta pun hilang.

Konsekuensinya untuk PASAL 12.1: tidak ada satu pun metode di sini yang menulis
ke tabel historis. Catatan historis bersifat IMMUTABLE, dan modul yang tugasnya
belajar dari catatan adalah tempat paling mungkin seseorang kelak "memperbaiki"
satu baris lama supaya polanya lebih rapi.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from aruna.db.pool import Database
from aruna.db.types import dump_json, load_json, to_mysql_datetime


class LearningRepository:
    """Baca sejarah, tulis hasil analisisnya."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- membaca sejarah -------------------------------------------------

    async def scored_observations(self, *, limit: int = 20000) -> list[dict[str, Any]]:
        """Prediksi berarah yang sudah punya hasil perdagangan kertasnya.

        ``published`` TIDAK disaring di sini, dan itu keputusan yang perlu
        dinyatakan. Pembelajaran memakai semua yang diskor - termasuk yang
        ARUNA sendiri tahan - karena yang ditahan tetap memberi tahu sesuatu
        tentang keadaan pasar dan tentang penahanannya sendiri.

        Yang TIDAK memakainya adalah rekam jejak yang dilaporkan ke operator:
        di sana hanya yang terpublikasi ikut dihitung, karena hanya itu klaim
        yang pernah ARUNA buat. Dua pertanyaan berbeda, dua populasi berbeda,
        dan mencampurnya membuat salah satunya salah.
        """
        rows = await self._db.fetch(
            """
            SELECT ss.market_code, ss.symbol, ss.horizon_code, ss.direction,
                   ss.regime, ss.signal_quality, ss.confidence,
                   ss.model_version, g.published, p.result,
                   p.net_pnl, p.gross_pnl, g.resolved_at
            FROM signals g
            JOIN signal_snapshots ss ON ss.signal_id = g.signal_id
            JOIN paper_trades p ON p.signal_id = g.signal_id
            WHERE g.status = 'RESOLVED'
              AND ss.direction IN ('BUY','SELL')
              AND p.result IN ('WIN','LOSS')
            ORDER BY g.resolved_at DESC
            LIMIT %s
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def agent_votes(self, *, limit: int = 50000) -> list[dict[str, Any]]:
        """Suara tiap agent pada sesi yang hasilnya sudah diketahui."""
        rows = await self._db.fetch(
            """
            SELECT v.role, v.agreed_with_council, v.abstained,
                   ss.regime, p.result
            FROM council_votes v
            JOIN council_sessions cs ON cs.id = v.council_session_id
            JOIN signal_snapshots ss ON ss.council_session_id = cs.id
            JOIN paper_trades p ON p.signal_id = ss.signal_id
            WHERE p.result IN ('WIN','LOSS')
            LIMIT %s
            """,
            limit,
        )
        return [dict(r) for r in rows]

    # ---- menulis hasil ---------------------------------------------------

    async def save_patterns(self, rows: Sequence[dict[str, Any]]) -> int:
        """Simpan pola yang ditemukan. Menimpa hasil lama untuk versi yang sama.

        Menimpa, bukan menumpuk: menjalankan pencarian dua kali dalam satu hari
        tidak menghasilkan dua kebenaran tentang irisan yang sama. Versi model
        ikut jadi kunci, jadi hasil dari model berbeda tetap hidup
        berdampingan (PASAL 12.21).
        """
        if not rows:
            return 0
        nilai = [
            (
                r["pattern_key"],
                dump_json(r["dimensions"]),
                r["wins"],
                r["losses"],
                r["sample_size"],
                r["win_rate"],
                r["ci_low"],
                r["ci_high"],
                r["evidence"],
                bool(r["beats_baseline"]),
                r["model_version"],
                to_mysql_datetime(r["computed_at"]),
            )
            for r in rows
        ]
        return await self._db.executemany(
            """
            INSERT INTO discovered_patterns
                (pattern_key, dimensions, wins, losses, sample_size, win_rate,
                 ci_low, ci_high, evidence, beats_baseline, model_version,
                 computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                dimensions     = new.dimensions,
                wins           = new.wins,
                losses         = new.losses,
                sample_size    = new.sample_size,
                win_rate       = new.win_rate,
                ci_low         = new.ci_low,
                ci_high        = new.ci_high,
                evidence       = new.evidence,
                beats_baseline = new.beats_baseline,
                computed_at    = new.computed_at
            """,
            nilai,
        )

    async def upsert_strategies(self, rows: Sequence[dict[str, Any]]) -> int:
        """Pasang katalog. Status yang sudah ada TIDAK ditimpa.

        Katalog boleh diperbarui deskripsinya; statusnya tidak. Sebuah strategi
        yang operator taruh di SUSPENDED harus tetap di sana sesudah restart -
        kalau tidak, penangguhan itu hanya bertahan sampai proses berikutnya
        menyala, dan tidak ada yang akan menyadarinya.
        """
        if not rows:
            return 0
        nilai = [
            (
                r["code"], r["name"], r["description"],
                dump_json(r["conditions"]),
                dump_json(r["preferred_regimes"]),
                dump_json(r["preferred_horizons"]),
                r["status"], r["status_reason"], r["model_version"],
                to_mysql_datetime(r["created_at"]),
                to_mysql_datetime(r["updated_at"]),
            )
            for r in rows
        ]
        return await self._db.executemany(
            """
            INSERT INTO strategies
                (code, name, description, conditions, preferred_regimes,
                 preferred_horizons, status, status_reason, model_version,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                name               = new.name,
                description        = new.description,
                conditions         = new.conditions,
                preferred_regimes  = new.preferred_regimes,
                preferred_horizons = new.preferred_horizons,
                model_version      = new.model_version,
                updated_at         = new.updated_at
            """,
            nilai,
        )

    async def save_strategy_performance(
        self, rows: Sequence[dict[str, Any]]
    ) -> int:
        if not rows:
            return 0
        nilai = [
            (
                r["strategy_code"], r["slice_key"], dump_json(r["dimensions"]),
                r["wins"], r["losses"], r["sample_size"], r["win_rate"],
                r["ci_low"], r["ci_high"], r["evidence"],
                r["net_pnl"], r["max_drawdown"], r["model_version"],
                to_mysql_datetime(r["computed_at"]),
            )
            for r in rows
        ]
        return await self._db.executemany(
            """
            INSERT INTO strategy_performance
                (strategy_code, slice_key, dimensions, wins, losses,
                 sample_size, win_rate, ci_low, ci_high, evidence, net_pnl,
                 max_drawdown, model_version, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                AS new
            ON DUPLICATE KEY UPDATE
                dimensions   = new.dimensions,
                wins         = new.wins,
                losses       = new.losses,
                sample_size  = new.sample_size,
                win_rate     = new.win_rate,
                ci_low       = new.ci_low,
                ci_high      = new.ci_high,
                evidence     = new.evidence,
                net_pnl      = new.net_pnl,
                max_drawdown = new.max_drawdown,
                computed_at  = new.computed_at
            """,
            nilai,
        )

    async def record_event(
        self,
        *,
        event_type: str,
        subject: str,
        summary: str,
        model_version: str,
        occurred_at: datetime,
        evidence: Any = None,
        sample_size: int | None = None,
    ) -> int:
        """Satu peristiwa pembelajaran yang perlu bisa diaudit nanti.

        Sengaja satu baris per peristiwa BERARTI - bukan per perhitungan.
        PASAL 12.22 menyebut daftarnya, dan tabelnya menegakkannya lewat CHECK
        constraint: sebuah `event_type` di luar daftar itu ditolak database.
        """
        return await self._db.insert(
            """
            INSERT INTO learning_events
                (event_type, subject, summary, evidence, sample_size,
                 model_version, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            event_type,
            subject[:255],
            summary[:500],
            dump_json(evidence) if evidence is not None else None,
            sample_size,
            model_version,
            to_mysql_datetime(occurred_at),
        )

    # ---- membaca hasil ---------------------------------------------------

    async def notable_patterns(
        self, *, model_version: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Pola yang sample-nya cukup, diurutkan dari sample terbesar.

        Bukan dari win rate: mengurutkan menurut win rate menaruh setiap
        kebetulan bersample kecil di puncak halaman.
        """
        rows = await self._db.fetch(
            "SELECT pattern_key, dimensions, wins, losses, sample_size, "
            "win_rate, ci_low, ci_high, evidence, beats_baseline "
            "FROM discovered_patterns "
            "WHERE model_version = %s AND evidence <> 'INSUFFICIENT_SAMPLE' "
            "ORDER BY sample_size DESC LIMIT %s",
            model_version,
            limit,
        )
        hasil = []
        for r in rows:
            d = dict(r)
            d["dimensions"] = load_json(d["dimensions"])
            hasil.append(d)
        return hasil

    async def recent_events(
        self, *, since: datetime, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT event_type, subject, summary, sample_size, occurred_at "
            "FROM learning_events WHERE occurred_at >= %s "
            "ORDER BY occurred_at DESC LIMIT %s",
            to_mysql_datetime(since),
            limit,
        )
        return [dict(r) for r in rows]

    async def strategy_slices(self) -> list[dict[str, Any]]:
        """Performa tiap strategi, digabung lintas rezim.

        Yang disaring hanya SUSPENDED dan RETIRED - status yang **operator**
        pasang untuk mengeluarkan sebuah strategi dari pertimbangan.

        DEGRADED dan UNDER_REVIEW tetap ditawarkan, dan itu perbaikan atas
        bentuk sebelumnya yang menyaring semua kecuali ACTIVE. Keduanya adalah
        label pengamatan yang ARUNA pasang sendiri, dan menyaringnya di sini
        berarti ARUNA menonaktifkan strateginya sendiri diam-diam - persis
        modifikasi otomatis yang PASAL 11.16 larang, lewat pintu yang tidak
        bernama begitu.

        Lagipula gerbangnya sudah ada dan lebih baik: tujuh pertimbangan di
        ``aruna.learning.selection`` menolak strategi yang tidak terbukti
        mengalahkan rata-rata. Menyaring dua kali tidak menambah keamanan; ia
        hanya menyembunyikan alasan sebenarnya sebuah strategi tidak terpilih.
        """
        rows = await self._db.fetch(
            """
            SELECT p.strategy_code,
                   SUM(p.wins)   AS wins,
                   SUM(p.losses) AS losses,
                   SUM(p.net_pnl) AS net_pnl,
                   MAX(p.max_drawdown) AS max_drawdown
            FROM strategy_performance p
            JOIN strategies s ON s.code = p.strategy_code
            WHERE p.slice_key LIKE '%|regime=ALL'
              AND s.status NOT IN ('SUSPENDED', 'RETIRED')
            GROUP BY p.strategy_code
            """
        )
        return [dict(r) for r in rows]

    async def catalog_with_performance(self) -> list[dict[str, Any]]:
        """Seluruh katalog beserta hasilnya, termasuk yang dikeluarkan.

        Yang SUSPENDED dan RETIRED ikut, dan itu bukan kelengkapan: katalog
        yang hanya memuat strategi yang masih dipakai akan selalu terbaca
        seperti kumpulan ide bagus, karena setiap kegagalan sudah dihapus dari
        pandangan (PASAL 11.21, 12.15).
        """
        rows = await self._db.fetch(
            """
            SELECT s.code, s.name, s.status, s.status_reason,
                   s.preferred_regimes, s.preferred_horizons, s.retired_at,
                   COALESCE(SUM(p.wins), 0)   AS wins,
                   COALESCE(SUM(p.losses), 0) AS losses,
                   COALESCE(SUM(p.net_pnl), 0) AS net_pnl,
                   COALESCE(MAX(p.max_drawdown), 0) AS max_drawdown
            FROM strategies s
            LEFT JOIN strategy_performance p
                   ON p.strategy_code = s.code
                  AND p.slice_key LIKE '%|regime=ALL'
            -- Dikelompokkan menurut kunci utamanya saja.
            --
            -- ``preferred_regimes`` dan ``preferred_horizons`` bertipe JSON,
            -- dan mengelompokkan menurut keduanya membuat MySQL mencatat
            -- "This version of MySQL doesn't yet support 'sorting of
            -- non-scalar JSON values'" pada setiap putaran pembelajaran -
            -- terhitung 17 kali di log produksi.
            --
            -- ``s.id`` adalah kunci utama ``strategies``, jadi seluruh kolom
            -- ``s.*`` bergantung fungsional padanya: hasilnya sama persis,
            -- tanpa memaksa MySQL mengurutkan nilai yang memang tidak bisa
            -- diurutkannya.
            GROUP BY s.id
            ORDER BY s.code
            """
        )
        hasil = []
        for r in rows:
            d = dict(r)
            d["preferred_regimes"] = load_json(d["preferred_regimes"])
            d["preferred_horizons"] = load_json(d["preferred_horizons"])
            hasil.append(d)
        return hasil

    async def set_strategy_status(
        self,
        code: str,
        status: str,
        *,
        reason: str,
        now: datetime,
    ) -> int:
        """Ubah status satu strategi. Tidak pernah menghapus barisnya.

        ``retired_at`` hanya distempel, tidak pernah dibersihkan: sebuah
        strategi yang pernah dipensiunkan lalu diaktifkan lagi tetap punya
        tanggal itu dalam sejarahnya, dan menghapusnya akan membuat
        pemensiunannya seolah tidak pernah terjadi.
        """
        return await self._db.execute(
            """
            UPDATE strategies
               SET status = %s,
                   status_reason = %s,
                   updated_at = %s,
                   retired_at = CASE
                       WHEN %s = 'RETIRED' AND retired_at IS NULL THEN %s
                       ELSE retired_at
                   END
             WHERE code = %s
            """,
            status,
            reason[:500],
            to_mysql_datetime(now),
            status,
            to_mysql_datetime(now),
            code,
        )

    async def overall_win_rate(self) -> float | None:
        """Win rate keseluruhan, sebagai baseline pemilihan strategi.

        ``None`` ketika belum ada apa pun untuk dibagi - dan pemanggilnya harus
        memperlakukannya sebagai "belum ada pembanding", bukan sebagai nol.
        """
        row = await self._db.fetchrow(
            "SELECT SUM(result = 'WIN') AS menang, COUNT(*) AS total "
            "FROM paper_trades WHERE result IN ('WIN','LOSS')"
        )
        if not row or not row["total"]:
            return None
        return float(row["menang"] or 0) / float(row["total"])

    async def strategy_status(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT code, name, status, status_reason FROM strategies "
            "ORDER BY code"
        )
        return [dict(r) for r in rows]


__all__ = ["LearningRepository"]
