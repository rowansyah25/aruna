"""Ingatan dari jalur futures: PASAL 15.2 untuk keputusan yang sesungguhnya.

Ingatan yang ada semuanya lahir dari jalur SPOT - 15m, 1h, 1d, ejaan
``BTC/USDT``. Keputusan Phase 14 dibuat di jalur futures, 4h, ejaan
``BTCUSDT``, dan ingatan pada 4h berjumlah **nol** (terukur 2026-08-21).

Selama itu, konteks historis meminjam 1h dan mengakuinya. Proyektor di berkas
ini yang membuat pinjaman itu berakhir sendiri: begitu 4h punya cukup hasil
yang bisa mengajari, ``horizon_ingatan`` memilihnya tanpa ada yang perlu
mengubah apa pun.

Angka hari ini, dan disebut supaya tidak disalahbaca sebagai kemajuan yang
belum terjadi: **182 hasil futures, 165 di antaranya EXPIRED**. Yang benar-benar
menang atau kalah hanya **17** - jauh di bawah ambang dua puluh, untuk waktu
yang lama.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from aruna.memory.record import Hasil

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


class _DBPalsu:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.sql = ""
        self.args: tuple = ()
        self.disimpan: list[tuple] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql = sql
        self.args = args
        return self.rows

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql = sql
        self.args = args
        self.disimpan.append(args)
        return 1


def _baris(outcome: str, **ganti: Any) -> dict[str, Any]:
    """Baris gabungan yang bentuknya disalin dari produksi 2026-08-20."""
    dasar = {
        "signal_id": "ec9dcb1a6c876922",
        "symbol": "APTUSDT",
        "side": "LONG",
        "horizon_hours": Decimal("4.0000"),
        "model_version": "futures-f5",
        "created_at": datetime(2026, 8, 20, 17, 17, 56),
        "outcome": outcome,
        "resolved_at": datetime(2026, 8, 20, 21, 17, 56),
        "entry": Decimal("0.580500000000"),
        "exit_price": Decimal("0.573400000000"),
        "risk_level": "MODERATE",
    }
    dasar.update(ganti)
    return dasar


class TestPemetaanHasil:
    @pytest.mark.parametrize(
        ("outcome", "hasil"),
        [
            ("TARGET_HIT", Hasil.WIN),
            ("STOPPED_OUT", Hasil.LOSS),
            ("LIQUIDATED", Hasil.LOSS),
            ("EXPIRED", Hasil.NEUTRAL),
        ],
    )
    def test_outcome_dipetakan(self, outcome: str, hasil: Hasil) -> None:
        from aruna.db.repositories.memory import hasil_futures

        assert hasil_futures(outcome) is hasil

    def test_likuidasi_masuk_kolom_kalah(self) -> None:
        """§11.21 melarang menyembunyikan LOSS, dan likuidasi adalah kekalahan
        yang paling buruk - memberinya kategori sendiri akan mengeluarkannya
        dari kolom kalah. Keputusan yang sama sudah diambil Phase 14."""
        from aruna.db.repositories.memory import hasil_futures

        assert hasil_futures("LIQUIDATED") is Hasil.LOSS

    def test_kedaluwarsa_bukan_kekalahan(self) -> None:
        """165 dari 182 hasil futures EXPIRED. Menghitungnya sebagai kalah akan
        membuat win rate futures terlihat 2% - angka yang salah dan
        meyakinkan."""
        from aruna.db.repositories.memory import hasil_futures

        assert hasil_futures("EXPIRED") is Hasil.NEUTRAL

    def test_yang_tidak_dikenali_unknown(self) -> None:
        from aruna.db.repositories.memory import hasil_futures

        assert hasil_futures("SESUATU_YANG_BARU") is Hasil.UNKNOWN
        assert hasil_futures(None) is Hasil.UNKNOWN


class TestProyeksiFutures:
    @pytest.mark.asyncio
    async def test_menyimpan_dengan_ejaan_ingatan(self) -> None:
        """``APTUSDT`` disimpan sebagai ``APT/USDT``. Dua ejaan di satu tabel
        berarti pencarian yang tidak pernah cocok dengan separuh isinya."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([_baris("STOPPED_OUT")])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert any("APT/USDT" in str(a) for a in db.args)
        assert not any(a == "APTUSDT" for a in db.args)

    @pytest.mark.asyncio
    async def test_timeframenya_dari_horizon_rencana(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([_baris("STOPPED_OUT")])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert "4h" in db.args

    @pytest.mark.asyncio
    async def test_terikat_waktu(self) -> None:
        """PASAL 15.39 berlaku sama untuk proyektor mana pun."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert "resolved_at < %s" in db.sql
        assert any("2026-08-21" in str(a) for a in db.args)

    @pytest.mark.asyncio
    async def test_hanya_rencana_yang_sudah_diresolusi(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert "futures_plan_results" in db.sql

    @pytest.mark.asyncio
    async def test_tidak_menimpa_ingatan_yang_sudah_ada(self) -> None:
        """PASAL 15.26, ditegakkan kunci UNIQUE - proyektor spot dan futures
        menulis ke tabel yang sama."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([_baris("TARGET_HIT")])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert "INSERT IGNORE" in db.sql.upper()

    @pytest.mark.asyncio
    async def test_dimensi_yang_tidak_tersimpan_jadi_unknown(self) -> None:
        """Jalur futures tidak menyimpan regime, berita, quality, maupun
        spread per rencana. Mengisinya dengan tebakan akan membuat ingatan
        futures terlihat lebih lengkap daripada yang sungguhnya (§13.26)."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([_baris("STOPPED_OUT")])
        await MemoryRepository(db).proyeksikan_futures(sampai=NOW, limit=10)

        assert db.args.count("UNKNOWN") >= 3
