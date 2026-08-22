"""Pembersih retensi: apa yang boleh hilang, dan apa yang tidak pernah boleh.

Audit 2026-08-21 menemukan **tidak ada satu pun retention di seluruh basis
kode**. Setiap `DELETE` yang ada hanya penggantian per-sesi. Basis data tumbuh
selamanya: 506 MB, dan `market_snapshots` sendirian menyumbang 62% dengan
laju 69.048 baris sehari.

Bagian 31 spec menyebut daftar yang tidak boleh hilang: final signal, agent
consensus, disagreement, veto, risk decision, judge decision, WIN, LOSS,
self-correction, dan bukti audit. Yang menahannya bukan kehati-hatian penulis
kueri berikutnya melainkan daftar `DILINDUNGI` dan penjaga yang menolak
menjalankan rencana yang menyebutnya.

Test yang menolak menghapus ditulis lebih dulu daripada test yang menghapus,
dengan sengaja: kerusakan dari pembersih yang terlalu rajin tidak bisa
dibatalkan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aruna.upkeep.retensi import (
    DILINDUNGI,
    RENCANA,
    PembersihRetensi,
    Retensi,
)

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


class _DBPalsu:
    """Basis data yang mencatat setiap kueri dan berhenti memberi baris.

    `dihapus` menirukan `ROW_COUNT`: sekali batch memulangkan kurang dari
    limitnya, tabel itu selesai.
    """

    def __init__(self, *, dihapus: int = 0) -> None:
        self.sql: list[str] = []
        self.args: list[tuple[Any, ...]] = []
        self._dihapus = dihapus

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql.append(sql)
        self.args.append(args)
        return self._dihapus


class TestYangTidakBolehHilang:
    def test_daftar_lindung_menyebut_yang_pasalnya_sebut(self) -> None:
        """Bagian 31, dieja satu per satu supaya penghapusan salah satunya
        terlihat sebagai kegagalan test, bukan sebagai baris hilang di
        produksi."""
        for tabel in (
            "signals",
            "signal_snapshots",
            "outcome_snapshots",
            "futures_plans",
            "futures_plan_results",
            "futures_plan_delivery",
            "council_sessions",
            "council_votes",
            "judge_decisions",
            "veto_events",
            "agent_objections",
            "agent_rebuttals",
            "market_memories",
            "discovered_patterns",
            "learning_events",
            "loss_autopsies",
            "audit_logs",
            "backtest_runs",
        ):
            assert tabel in DILINDUNGI, tabel

    def test_rencana_bawaan_tidak_menyentuh_yang_dilindungi(self) -> None:
        for r in RENCANA:
            assert r.tabel not in DILINDUNGI, r.tabel

    @pytest.mark.asyncio
    async def test_rencana_yang_menyebut_tabel_terlindung_ditolak(self) -> None:
        """Penjaga terhadap rencana yang disunting sembarangan kemudian.

        Ditolak SEBELUM satu pun kueri dijalankan: pembersih yang menghapus
        setengah tabel lalu berteriak sudah terlambat.
        """
        db = _DBPalsu()
        pembersih = PembersihRetensi(
            db,
            rencana=(
                Retensi(
                    tabel="signals", kolom_waktu="locked_at", hari=1, batas_batch=10
                ),
            ),
        )

        with pytest.raises(ValueError, match="signals"):
            await pembersih.sapu(now=NOW, batas_total=100)

        assert db.sql == []


class TestBatchBerbatas:
    @pytest.mark.asyncio
    async def test_setiap_delete_punya_limit(self) -> None:
        """Bagian 26: JANGAN DELETE jutaan baris dalam satu transaksi.

        `market_snapshots` punya 422.172 baris dan sebagian besarnya kandidat
        hapus; satu DELETE tanpa LIMIT akan memegang lock tabel itu selama
        seluruh sistem menunggunya.
        """
        db = _DBPalsu(dihapus=0)
        await PembersihRetensi(db).sapu(now=NOW, batas_total=1000)

        hapus = [s for s in db.sql if "DELETE" in s.upper()]
        assert hapus
        assert all("LIMIT" in s.upper() for s in hapus)

    @pytest.mark.asyncio
    async def test_berhenti_di_batas_total(self) -> None:
        """Pembersih yang tidak bisa dihentikan akan memegang basis data
        selama siklus upkeep berikutnya menunggu gilirannya."""
        db = _DBPalsu(dihapus=500)
        hasil = await PembersihRetensi(db).sapu(now=NOW, batas_total=1000)

        assert sum(hasil.values()) <= 1000

    @pytest.mark.asyncio
    async def test_batch_terakhir_yang_kurang_dari_limit_mengakhiri_tabel(
        self,
    ) -> None:
        """Tanpa ini, tabel yang sudah bersih tetap dikueri sampai batas total
        habis - dan batas total itu milik seluruh rencana, jadi tabel pertama
        akan melahap jatah tabel-tabel berikutnya."""
        db = _DBPalsu(dihapus=0)
        await PembersihRetensi(db).sapu(now=NOW, batas_total=100_000)

        hapus = [s for s in db.sql if "DELETE" in s.upper()]
        assert len(hapus) == len(RENCANA)

    @pytest.mark.asyncio
    async def test_ambang_waktu_dihitung_dari_now_yang_dioper(self) -> None:
        """Pembersih yang membaca jamnya sendiri tidak bisa diuji, dan
        `now` yang dioper adalah jam yang sama dengan sisa loop upkeep."""
        db = _DBPalsu(dihapus=0)
        await PembersihRetensi(db).sapu(now=NOW, batas_total=1000)

        # Hanya argumen yang berbentuk stempel waktu; `interval_code` seperti
        # "4h" juga string, dan membandingkannya secara leksikal dengan
        # tanggal akan gagal karena alasan yang tidak ada hubungannya.
        batas = [
            a
            for args in db.args
            for a in args
            if isinstance(a, str) and a[:2].isdigit() and "-" in a
        ]
        assert batas
        assert all(b < NOW.strftime("%Y-%m-%d %H:%M:%S") for b in batas)


class TestRencanaBawaan:
    def test_snapshot_ada_di_rencana(self) -> None:
        """62% basis data. Kalau tabel ini tidak dibersihkan, tidak ada
        pembersih yang berarti."""
        assert any(r.tabel == "market_snapshots" for r in RENCANA)

    def test_candle_disimpan_lebih_lama_untuk_timeframe_besar(self) -> None:
        """Bagian 9: 1m boleh pendek, 1d harus panjang.

        Ini bukan soal ukuran melainkan soal ingatan pasar - `market_memories`
        dan backtest membaca candle harian bertahun-tahun ke belakang, dan
        memangkasnya sama saja dengan menghapus ingatan yang Phase 15 baru
        saja dibangun.
        """
        from aruna.upkeep.retensi import HARI_CANDLE

        assert HARI_CANDLE["1m"] < HARI_CANDLE["1h"] < HARI_CANDLE["1d"]

    def test_semua_rencana_punya_kolom_waktu(self) -> None:
        """Rencana tanpa kolom waktu akan menghapus tabel penuh."""
        for r in RENCANA:
            assert r.kolom_waktu
            assert r.hari > 0
            assert r.batas_batch > 0
