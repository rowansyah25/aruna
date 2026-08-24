"""Angka untuk laporan harian, dibaca dari baris yang tersimpan (PASAL 3-9).

Tidak ada satu pun angka di sini yang dihitung dari perkiraan. Setiap hitungan
adalah ``COUNT`` atas baris yang sudah ada di database, dengan jendela waktu
yang eksplisit - dan kategori tanpa baris menghasilkan nol, bukan ketiadaan
yang nanti berubah jadi ``NaN`` di layar.

Tiga pasar dilaporkan terpisah karena sumbernya memang terpisah:

* **FUTURES / PERPETUAL** - ``futures_plans`` dan ``futures_plan_results``;
* **SPOT** - ``signals`` pasar CRYPTO beserta ``paper_trades``-nya;
* **SAHAM INDONESIA** - ``signals`` pasar IDX beserta ``paper_trades``-nya.

Menyatukannya jadi satu kueri akan memaksa salah satunya dibengkokkan ke
bentuk yang lain, dan yang paling mungkin dibengkokkan adalah futures - satu-
satunya yang punya likuidasi, dan satu-satunya yang kalahnya bisa lebih buruk
daripada stop.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from aruna.db.types import as_utc, to_mysql_datetime
from aruna.notify.daily import (
    AgentScore,
    CouncilScore,
    MarketBlock,
    MutuHarian,
    SelfCorrection,
    Tally,
)
from aruna.signals.stabilitas import Peralihan, perlu_konfirmasi
from aruna.signals.withheld import WithheldCode

#: Kode yang berarti "ditahan gerbang mutu" (bagian 18.47).
#:
#: Dipinjam dari :class:`~aruna.signals.withheld.WithheldCode`, bukan ditulis
#: sebagai string: kode yang berganti ejaan akan membuat hitungan gerbang
#: menjadi nol tanpa satu pun error - laporan yang berbunyi "gerbang mutu tidak
#: pernah gagal" persis pada hari ia paling sering gagal.
KODE_GERBANG_MUTU = WithheldCode.QUALITY_GATE.value

#: Bagaimana satu hasil futures dibaca sebagai menang atau kalah (PASAL 4).
#:
#: ``LIQUIDATED`` masuk LOSS, dan itu bukan pilihan gaya. Posisi yang ditutup
#: paksa bursa adalah kekalahan yang lebih buruk daripada kena stop, dan
#: menaruhnya di luar hitungan - bersama EXPIRED - akan membuang justru
#: kekalahan terburuk dari win rate.
FUTURES_WIN = ("TARGET_HIT",)
FUTURES_LOSS = ("STOPPED_OUT", "LIQUIDATED")
FUTURES_ACTIVE = ("OPEN",)


def _int(value: Any) -> int:
    """``COUNT`` selalu angka; ``SUM`` bisa ``NULL`` kalau tidak ada baris."""
    return 0 if value is None else int(value)


def _float(value: Any) -> float | None:
    """``AVG`` atas nol baris adalah ``NULL``, dan itu **bukan** nol.

    Hari tanpa satu pun keputusan bukan hari bermutu nol; baris yang mencetak
    "Rata-rata Decision Quality: 0/100" untuknya adalah tuduhan terhadap sistem
    yang kebetulan tidak ditanya apa-apa.
    """
    return None if value is None else float(value)


def _gerak(sebelum: Any, sesudah: Any) -> float | None:
    """Perubahan harga antara dua keputusan, dalam persen."""
    try:
        awal, akhir = Decimal(str(sebelum)), Decimal(str(sesudah))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return None if awal == 0 else float((akhir - awal) / awal * 100)


def _belum_terkonfirmasi(row: Any) -> tuple[str, ...]:
    """Alasan pembalikan ini belum terkonfirmasi, lewat aturan yang sama.

    **Dipinjam dari** :func:`~aruna.signals.stabilitas.perlu_konfirmasi`, bukan
    ditulis ulang: laporan yang memakai definisi "terkonfirmasi" yang berbeda
    dari penjaganya akan menghitung pembalikan yang justru lolos penjaga
    sebagai tidak terkonfirmasi, dan sebaliknya.
    """
    lama = SimpleNamespace(direction=str(row["arah_lama"]))
    baru = SimpleNamespace(direction=str(row["direction"]))
    return perlu_konfirmasi(
        lama, baru, gerak_pct=_gerak(row["harga_lama"], row["reference_price"])
    )


class DailyRepository:
    """Membaca hitungan satu hari. Tidak menulis apa pun."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- futures ----------------------------------------------------------

    async def futures(self, *, start: datetime, end: datetime) -> MarketBlock:
        rows = await self._db.fetch(
            """
            SELECT p.side AS side,
                   COALESCE(r.outcome, 'PENDING') AS outcome,
                   count(*) AS n
            FROM futures_plans p
            LEFT JOIN futures_plan_results r ON r.signal_id = p.signal_id
            WHERE p.verdict = 'PLAN'
              AND p.created_at >= %s AND p.created_at < %s
            GROUP BY p.side, COALESCE(r.outcome, 'PENDING')
            """,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        )
        return _block_from_rows(
            rows,
            title="FUTURES / PERPETUAL",
            icon="🔮",
            win=FUTURES_WIN,
            loss=FUTURES_LOSS,
            # PENDING = plan yang belum punya baris hasil sama sekali. Itu
            # posisi yang masih berjalan, sama seperti OPEN.
            active=(*FUTURES_ACTIVE, "PENDING"),
            long_values=("LONG",),
            short_values=("SHORT",),
        )

    # -- spot dan saham ---------------------------------------------------

    async def spot_or_equity(
        self, *, market_code: str, title: str, icon: str,
        start: datetime, end: datetime,
    ) -> MarketBlock:
        """Satu pasar signal, dihitung dari ``paper_trades``.

        Sumbernya ``paper_trades`` dan bukan ``signals``, karena ``signals``
        tidak menyimpan arah sama sekali - arahnya ada di snapshot yang
        menyertainya. ``paper_trades`` memegang ketiganya sekaligus: pasar,
        arah, dan hasil.

        Konsekuensinya disebut supaya tidak disalahpahami: yang dihitung di
        sini adalah **posisi kertas yang benar-benar dibuka**. Verdict WAIT
        tidak pernah menghasilkan posisi, jadi tidak muncul - dan memang tidak
        seharusnya muncul di win rate (PASAL 4). Call berarah yang sengaja
        ditahan juga tidak: ARUNA sudah bilang di muka bahwa ia tidak berdiri
        di belakangnya, dan menghitungnya akan menilai sistem atas klaim yang
        tidak pernah dibuat.
        """
        rows = await self._db.fetch(
            """
            SELECT t.direction AS side, t.result AS outcome, count(*) AS n
            FROM paper_trades t
            WHERE t.market_code = %s
              AND t.entry_at >= %s AND t.entry_at < %s
            GROUP BY t.direction, t.result
            """,
            market_code,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        )
        return _block_from_rows(
            rows,
            title=title,
            icon=icon,
            win=("WIN",),
            loss=("LOSS",),
            active=("OPEN",),
            long_values=("BUY",),
            short_values=("SELL",),
        )

    # -- agent, council, koreksi diri -------------------------------------

    async def agents(self) -> tuple[AgentScore, ...]:
        """Keandalan agen yang **sudah terukur** saja (SPEC 30, PASAL 7, 11.2).

        Agen tanpa cukup opini terskor tidak muncul. Merangking sesuatu yang
        belum diukur adalah cara membuat papan peringkat dari kebisingan, dan
        papan peringkat itu akan terbaca sama meyakinkannya dengan yang benar.

        Dihitung langsung dari hasil yang tersimpan, bukan dibaca dari tabel
        snapshot ``agent_reliability``. Tabel itu hanya terisi ketika seseorang
        menjalankan ``aruna autopsy`` dengan persist - dan laporan harian
        berjalan sendiri tiap tengah malam, tanpa ada yang menjalankan apa pun.
        Terukur saat ditemukan: 144 opini terskor tersedia di database, dan
        bagian AGENT PERFORMANCE tetap kosong karena snapshot-nya belum pernah
        ditulis. Laporan yang diam karena tabel perantaranya kosong tidak bisa
        dibedakan dari laporan yang diam karena datanya memang belum ada.
        """
        from aruna.db.repositories.learning import LearningRepository
        from aruna.learning.reliability import build_reliability

        laporan = build_reliability(
            await LearningRepository(self._db).agent_outcomes()
        )
        return tuple(
            AgentScore(
                name=record.role.value,
                win_rate=(record.accuracy or 0.0) * 100,
                # Bagian 18.48: *"Namun selalu tampilkan sample size."*
                # Penyebutnya sudah dihitung mesin keandalan dan dulu dibuang
                # di baris ini - papan peringkat tanpa penyebut mengurutkan
                # 95%-dari-empat di atas 82%-dari-1.500 tanpa satu pun tanda
                # bahwa yang pertama bukan pengukuran (bagian 18.38).
                sample=record.scored,
            )
            # `measured` sudah menyaring yang sampelnya kurang; disiplin
            # INSUFFICIENT_SAMPLE tetap dipegang mesin keandalan, bukan
            # ditiru ulang di sini.
            for record in laporan.measured
        )

    async def mutu(self, *, start: datetime, end: datetime) -> MutuHarian:
        """Statistik Phase 18 satu hari (bagian 18.47).

        Dibaca dari baris yang **sudah tersimpan** - ``signal_snapshots``
        menyimpan ``signal_quality`` dan ``quality_coverage`` di tiap keputusan
        sejak lama, dan ``signals.withheld_code`` mencatat yang ditahan gerbang
        mutu. Tidak ada satu pun angka di sini yang dihitung ulang: menilai
        ulang mutu hari kemarin dengan penilai hari ini akan melaporkan angka
        yang tidak pernah dilihat siapa pun (bagian 18.35).

        **Gerbangnya dihitung dari kodenya, bukan dari terbit atau tidak.**
        Sebuah keputusan bisa tidak terbit karena duplikat, cooldown, atau
        tidak berarah - tidak satu pun berarti mutunya gagal, dan memasukkannya
        ke ``gagal`` akan melaporkan gerbang mutu yang jauh lebih galak
        daripada yang sebenarnya.
        """
        baris = await self._db.fetchrow(
            "SELECT avg(signal_quality) mutu, avg(confidence) yakin, "
            "       avg(quality_coverage) cakupan "
            "FROM signal_snapshots WHERE locked_at >= %s AND locked_at < %s",
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        ) or {}
        gerbang = await self._db.fetchrow(
            "SELECT count(*) diperiksa, "
            "       sum(withheld_code = %s) gagal "
            "FROM signals WHERE locked_at >= %s AND locked_at < %s",
            KODE_GERBANG_MUTU,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        ) or {}
        diperiksa = _int(gerbang.get("diperiksa"))
        gagal = _int(gerbang.get("gagal"))
        return MutuHarian(
            rata_mutu=_float(baris.get("mutu")),
            rata_keyakinan=_float(baris.get("yakin")),
            rata_cakupan=_float(baris.get("cakupan")),
            lolos=max(0, diperiksa - gagal),
            gagal=gagal,
            kalibrasi=await self._kalibrasi(),
        )

    async def _kalibrasi(self) -> str:
        """Vonis kalibrasi terakhir (bagian 18.51), atau kalimat kosong.

        Kalimat kosong berarti belum pernah diukur, dan barisnya hilang - bukan
        dicetak "GOOD". Sistem yang belum pernah memeriksa kejujurannya sendiri
        bukan sistem yang terkalibrasi baik.
        """
        from aruna.db.repositories.learning import LearningRepository

        baris = await LearningRepository(self._db).latest_calibration()
        return str((baris or {}).get("verdict") or "")

    async def pembalikan(
        self, *, start: datetime, end: datetime
    ) -> list[Peralihan]:
        """Pembalikan arah keputusan pada hari itu (bagian 18.52).

        Dua sinyal berurutan untuk aset dan horizon yang sama, keduanya
        berarah, dan arahnya berlawanan. ``LAG`` menyusunnya di SQL alih-alih
        di Python karena yang dibandingkan tetangga berurutan - dan mengambil
        seluruh baris hari itu untuk memasangkannya di sini akan membaca jauh
        lebih banyak daripada yang dipakai.

        Sinyal tak berarah dilewati: berhenti berpendapat bukan pembalikan.

        **Yang dipulangkan seluruh pembalikan, terkonfirmasi maupun tidak.**
        Yang memisahkannya :func:`~aruna.signals.stabilitas.hitung_pembalikan`,
        dan pemisahan itu justru isi laporannya - menyaring di sini akan
        membuang separuh pertanyaannya.
        """
        rows = await self._db.fetch(
            """
            SELECT symbol, horizon_code, arah_lama, direction, locked_at,
                   harga_lama, reference_price
            FROM (
                SELECT s.symbol, s.horizon_code, s.direction, s.locked_at,
                       s.reference_price,
                       LAG(s.direction)       OVER w AS arah_lama,
                       LAG(s.reference_price) OVER w AS harga_lama
                FROM signal_snapshots s
                WHERE s.locked_at >= %s AND s.locked_at < %s
                  AND s.direction IN ('BUY', 'SELL')
                WINDOW w AS (
                    PARTITION BY s.symbol, s.horizon_code ORDER BY s.locked_at
                )
            ) t
            WHERE arah_lama IS NOT NULL AND arah_lama <> direction
            ORDER BY locked_at
            """,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        )
        return [
            Peralihan(
                symbol=str(r["symbol"]),
                horizon=str(r["horizon_code"]),
                sebelum=str(r["arah_lama"]),
                sesudah=str(r["direction"]),
                pada=as_utc(r["locked_at"]),
                gerak_pct=_gerak(r["harga_lama"], r["reference_price"]),
                alasan=_belum_terkonfirmasi(r),
            )
            for r in rows
        ]

    async def council(self, *, start: datetime, end: datetime) -> CouncilScore:
        row = await self._db.fetchrow(
            """
            SELECT sum(r.direction_correct = 1) AS benar,
                   sum(r.direction_correct = 0) AS salah
            FROM paper_results r
            JOIN signals s ON s.signal_id = r.signal_id
            WHERE r.original_direction IN ('BUY', 'SELL')
              AND s.published = TRUE
              AND r.resolved_at >= %s AND r.resolved_at < %s
            """,
            to_mysql_datetime(start),
            to_mysql_datetime(end),
        )
        row = row or {}
        return CouncilScore(correct=_int(row.get("benar")), incorrect=_int(row.get("salah")))

    async def correction(
        self, *, start: datetime, end: datetime, model_version: str
    ) -> SelfCorrection:
        """Jejak PASAL 8, dihitung dari tabelnya masing-masing.

        ``correction_applied`` dibaca dari proposal yang berstatus aktif, bukan
        dari yang disetujui: persetujuan adalah izin, penerapan adalah
        perubahan, dan melaporkan keduanya sebagai satu angka akan membuat
        model terlihat sudah berubah padahal belum.
        """
        async def satu(sql: str) -> int:
            row = await self._db.fetchrow(
                sql, to_mysql_datetime(start), to_mysql_datetime(end)
            )
            return _int((row or {}).get("n"))

        return SelfCorrection(
            loss_analyzed=await satu(
                "SELECT count(*) AS n FROM loss_autopsies "
                "WHERE performed_at >= %s AND performed_at < %s"
            ),
            pattern_detected=await satu(
                "SELECT count(*) AS n FROM research_questions "
                "WHERE raised_at >= %s AND raised_at < %s"
            ),
            correction_proposed=await satu(
                "SELECT count(*) AS n FROM model_proposals "
                "WHERE raised_at >= %s AND raised_at < %s"
            ),
            correction_approved=await satu(
                "SELECT count(*) AS n FROM proposal_decisions "
                "WHERE decision = 'APPROVED' AND decided_at >= %s AND decided_at < %s"
            ),
            correction_applied=await satu(
                # Tidak ada kolom "activated_at"; yang ada `status` dan
                # `updated_at`. Sebuah proposal yang berstatus ACTIVE dan
                # berubah hari ini adalah proposal yang diterapkan hari ini.
                "SELECT count(*) AS n FROM model_proposals "
                "WHERE status = 'ACTIVE' AND updated_at >= %s AND updated_at < %s"
            ),
            model_version=model_version,
        )


def _block_from_rows(
    rows: list[dict[str, Any]],
    *,
    title: str,
    icon: str,
    win: tuple[str, ...],
    loss: tuple[str, ...],
    active: tuple[str, ...],
    long_values: tuple[str, ...],
    short_values: tuple[str, ...],
) -> MarketBlock:
    """Susun satu blok pasar dari baris ``(side, outcome, n)``.

    Hasil yang tidak dikenali sengaja **tidak** dijatuhkan ke salah satu sisi.
    Ia tetap masuk ``total`` dan tidak masuk mana pun dari win/loss/active,
    sehingga muncul sebagai selisih yang bisa dilihat. Menebak-nebak tempatnya
    akan menyembunyikan status baru yang belum pernah dipikirkan siapa pun.
    """
    def kosong() -> dict[str, int]:
        return {"total": 0, "win": 0, "loss": 0, "active": 0}

    semua, panjang, pendek = kosong(), kosong(), kosong()
    ada_pendek = False

    for row in rows:
        side = str(row["side"])
        outcome = str(row["outcome"])
        n = _int(row["n"])
        if side in short_values:
            ada_pendek = True
            sisi = pendek
        elif side in long_values:
            sisi = panjang
        else:  # pragma: no cover - dijaga CHECK di skema
            sisi = kosong()

        for bucket in (semua, sisi):
            bucket["total"] += n
            if outcome in win:
                bucket["win"] += n
            elif outcome in loss:
                bucket["loss"] += n
            elif outcome in active:
                bucket["active"] += n

    return MarketBlock(
        title=title,
        icon=icon,
        tally=Tally(**semua),
        long=Tally(**panjang),
        # Blok SHORT hanya ada kalau pasar ini memang menghasilkan call turun.
        short=Tally(**pendek) if ada_pendek else None,
    )


__all__ = [
    "FUTURES_ACTIVE",
    "FUTURES_LOSS",
    "FUTURES_WIN",
    "DailyRepository",
]
