"""Suara tiap agent disimpan bersama sesinya (PASAL 11.2, 11.10, 11.11).

Terukur sebelum ini ada: 154 sesi council tersimpan, **nol** suara agent.
``CouncilRepository.save`` menulis agregat - berapa agent ikut, berapa
keberatan - dan ``verdict.opinions`` berhenti di memori.

Akibatnya bukan "kurang detail". Empat pasal PHASE 11 menghitung dari
baris-baris ini, dan tanpa mereka papan peringkat agent hanya bisa berupa
karangan - yang terbaca sama meyakinkannya dengan yang benar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from typing import Any

import pytest

from aruna.core.enums import AgentRole, Decision
from aruna.db.repositories.council import CouncilRepository

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class _Db:
    """Menangkap SQL yang dijalankan, tanpa database sungguhan."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    async def insert(self, sql: str, *args: Any) -> int:
        self.executed.append((sql, args))
        return 77

    async def execute(self, sql: str, *args: Any) -> int:
        self.executed.append((sql, args))
        return 1

    async def executemany(self, sql: str, rows: Any) -> int:
        """Satu baris per catatan, bukan satu catatan per panggilan.

        ``save`` mengirim suara agent berkelompok sekarang - satu perjalanan
        untuk sebelas baris, bukan sebelas perjalanan - karena antrean koneksi
        adalah biaya terbesar satu tick futures. Yang DISIMPAN tidak berubah,
        dan itu yang diperiksa berkas ini, jadi tiap baris dicatat sendiri di
        sini. Mencatatnya sebagai satu entri berisi daftar akan membuat
        ``votes()`` mengembalikan satu tuple raksasa dan setiap pemeriksaan di
        bawah berhenti berarti apa-apa.
        """
        baris = list(rows)
        for satu in baris:
            self.executed.append((sql, tuple(satu)))
        return len(baris)

    @asynccontextmanager
    async def write_lock(
        self, table: str, *, timeout: float = 30
    ) -> AsyncIterator[None]:
        """Antrean penulis, sebentuk dengan ``Database.write_lock``.

        ``save`` lewat sini sekarang. Tidak masuk ``executed``: berkas ini
        memeriksa SQL yang dijalankan, dan antrean bukan SQL yang menyimpan
        apa pun.
        """
        yield

    def votes(self) -> list[tuple]:
        return [
            args for sql, args in self.executed
            if "INSERT INTO council_votes" in sql
        ]

    def deletes(self, table: str) -> int:
        return sum(
            1 for sql, _ in self.executed
            if sql.startswith(f"DELETE FROM {table}")
        )


def _opinion(role: AgentRole, decision: Decision, *, abstained=False, conf=0.5):
    return NS(
        role=role, decision=decision, confidence=conf, abstained=abstained,
        reasoning=("alasan",), evidence=(),
    )


def _verdict(decision: Decision, opinions: tuple) -> Any:
    return NS(
        symbol="BTC/USDT", market="CRYPTO", interval="1h",
        as_of=NOW, decided_at=NOW,
        decision=decision, confidence=0.6,
        opinions=opinions, participating=len(opinions),
        rounds_run=(1, 2, 3),
        protest=NS(objections=(), supports=(), rebuttals=(), disagreement=0.0),
        veto=NS(vetoes=(), upheld=(), reviews=()),
        judgement=NS(
            minority_prevailed=False, factors=(), rationale="",
            weighted_scores={}, unavailable_factors=(),
        ),
        risk=NS(overall=NS(value="MEDIUM")),
        no_trade=NS(blocked=False, reasons=()),
        notes=(),
    )


async def _save(verdict: Any) -> _Db:
    db = _Db()
    repo = CouncilRepository(db, phase=11)  # type: ignore[arg-type]
    await repo._save_votes(77, verdict)
    return db


class TestSuaraDisimpan:
    async def test_setiap_agent_dapat_satu_baris(self) -> None:
        verdict = _verdict(Decision.BUY, (
            _opinion(AgentRole.TECHNICAL, Decision.BUY),
            _opinion(AgentRole.RISK, Decision.SELL),
            _opinion(AgentRole.NEWS, Decision.BUY),
        ))
        db = await _save(verdict)
        assert len(db.votes()) == 3

    async def test_bukan_cuma_jumlahnya(self) -> None:
        """Agregat sudah ada sejak dulu, dan itu yang membuat papan peringkat
        agent mustahil dibangun."""
        db = await _save(_verdict(Decision.BUY, (
            _opinion(AgentRole.TECHNICAL, Decision.BUY, conf=0.95),
        )))
        (args,) = db.votes()
        assert AgentRole.TECHNICAL.value in args
        assert Decision.BUY.value in args
        assert 0.95 in args

    async def test_menghitung_ulang_mengganti_bukan_menumpuk(self) -> None:
        db = await _save(_verdict(Decision.BUY, (
            _opinion(AgentRole.TECHNICAL, Decision.BUY),
        )))
        assert db.deletes("council_votes") == 1


class TestKesepakatan:
    async def _agreed(self, council: Decision, agent: Decision, **kw) -> bool:
        db = await _save(_verdict(council, (
            _opinion(AgentRole.TECHNICAL, agent, **kw),
        )))
        (args,) = db.votes()
        return bool(args[5])

    async def test_arah_sama_itu_sepakat(self) -> None:
        assert await self._agreed(Decision.BUY, Decision.BUY) is True

    async def test_arah_berlawanan_itu_menentang(self) -> None:
        assert await self._agreed(Decision.BUY, Decision.SELL) is False

    async def test_wait_dan_no_signal_dihitung_sepakat(self) -> None:
        """Keduanya sampai ke operator sebagai kalimat yang sama: tidak ada
        posisi. Mencatatnya sebagai perpecahan menampilkan perbedaan yang
        tidak pernah terjadi."""
        assert await self._agreed(Decision.NO_SIGNAL, Decision.WAIT) is True
        assert await self._agreed(Decision.WAIT, Decision.NO_SIGNAL) is True

    async def test_abstain_tidak_pernah_dihitung_sepakat(self) -> None:
        """Agent yang abstain tidak menyatakan apa pun. Menghitungnya sebagai
        dukungan menaikkan angka "setuju" dengan suara yang tidak diberikan -
        dan skema menolaknya lewat CHECK."""
        assert await self._agreed(
            Decision.WAIT, Decision.WAIT, abstained=True
        ) is False

    async def test_wait_yang_menilai_beda_dari_abstain(self) -> None:
        """VOLUME bilang WAIT dan menilai: itu menentang BUY. FUNDAMENTAL
        abstain karena tidak punya bukti: itu bukan penentangan."""
        menilai = await _save(_verdict(Decision.BUY, (
            _opinion(AgentRole.VOLUME, Decision.WAIT),
        )))
        abstain = await _save(_verdict(Decision.BUY, (
            _opinion(AgentRole.FUNDAMENTAL, Decision.WAIT, abstained=True),
        )))
        assert menilai.votes()[0][4] is False   # abstained
        assert abstain.votes()[0][4] is True


class TestKabelKeJalurHidup:
    def test_save_memanggil_penyimpan_suara(self) -> None:
        """Tanpa ini tabelnya ikut kosong seperti `agent_decisions`, yang
        punya jalur tulis lengkap dan satu-satunya pemanggilnya perintah CLI."""
        import inspect

        # `save` sekarang hanya antrean di depan `_write`: dua penyimpan council
        # yang berjalan bersamaan saling mengunci celah di
        # `council_votes_unique` (lihat `Database.write_lock`). Jalur tulisnya
        # jadi dua metode, dan yang harus tetap utuh adalah rantainya - memeriksa
        # `save` saja akan lulus walau `_write` berhenti menulis suaranya.
        assert "_write" in inspect.getsource(CouncilRepository.save)
        assert "_save_votes" in inspect.getsource(CouncilRepository._write)

    def test_migrasi_ada(self) -> None:
        from pathlib import Path

        sql = Path("migrations/0023_council_votes.sql").read_text(encoding="utf-8")
        assert "CREATE TABLE council_votes" in sql
        assert "council_votes_session_fk" in sql

    def test_skema_menolak_abstain_yang_sepakat(self) -> None:
        """Aturan ini dijaga dua kali: di aplikasi dan di skema. Yang kedua
        bertahan walau kode aplikasinya salah tulis besok."""
        from pathlib import Path

        sql = Path("migrations/0023_council_votes.sql").read_text(encoding="utf-8")
        assert "council_votes_abstain_not_agreement" in sql


@pytest.mark.parametrize("role", list(AgentRole))
async def test_setiap_peran_bisa_disimpan(role: AgentRole) -> None:
    """Peran baru yang tidak muat di kolom akan gagal di sini, bukan diam-diam
    hilang dari statistik keandalan."""
    db = await _save(_verdict(Decision.BUY, (_opinion(role, Decision.BUY),)))
    (args,) = db.votes()
    assert args[1] == role.value
    assert len(role.value) <= 32
