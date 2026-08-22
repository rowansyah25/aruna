"""Keandalan agent per rezim, timeframe, dan aset (PASAL 11.2).

Yang diuji bukan aritmetikanya, tapi satu godaan: membelah sampel membuat
angkanya lebih ekstrem justru saat datanya jadi lebih tipis, dan "Trending:
96%" dari lima observasi terlihat persis seperti hasil penelitian.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from aruna.learning.breakdown import (
    MIN_CELL_SAMPLE,
    Breakdown,
    Cell,
    build_breakdown,
)


def _rows(agent: str, key: str, *, votes: int, correct: int):
    return [
        {"agent": agent, "key": key, "correct": i < correct}
        for i in range(votes)
    ]


class TestAmbangPerSel:
    def test_sel_tipis_diam_soal_akurasinya(self) -> None:
        """Lima observasi bukan pengukuran; ia satu lemparan koin yang
        kebetulan jatuh bagus, dicetak dengan dua desimal."""
        b = build_breakdown(
            _rows("TECHNICAL", "TRENDING_UP", votes=5, correct=5),
            dimension="regime",
        )
        (sel,) = b.cells
        assert sel.votes == 5
        assert sel.accuracy is None
        assert sel.status == "INSUFFICIENT_SAMPLE"

    def test_sel_cukup_menyebut_angkanya(self) -> None:
        b = build_breakdown(
            _rows("TECHNICAL", "TRENDING_UP",
                  votes=MIN_CELL_SAMPLE, correct=12),
            dimension="regime",
        )
        (sel,) = b.cells
        assert sel.accuracy == 12 / MIN_CELL_SAMPLE

    def test_ambang_berlaku_per_sel_bukan_per_agent(self) -> None:
        """Dua puluh lima opini tersebar di lima rezim adalah lima observasi
        per rezim - dan itu yang harus dinilai, bukan totalnya."""
        rows = []
        for i in range(5):
            rows += _rows("TECHNICAL", f"REGIME_{i}", votes=5, correct=5)
        b = build_breakdown(rows, dimension="regime")

        assert sum(c.votes for c in b.cells) == 25
        assert b.measured == ()

    def test_berapa_lagi_yang_dibutuhkan_disebut(self) -> None:
        b = build_breakdown(
            _rows("NEWS", "SIDEWAYS", votes=4, correct=2), dimension="regime"
        )
        assert b.cells[0].needs == MIN_CELL_SAMPLE - 4

    def test_sel_tipis_tidak_dihilangkan(self) -> None:
        """Sel yang hilang terbaca sebagai "tidak ada masalah di sana"."""
        b = build_breakdown(
            _rows("NEWS", "SIDEWAYS", votes=2, correct=0), dimension="regime"
        )
        assert len(b.cells) == 1
        assert b.measured == ()
        # Tercatat di keluarannya, lengkap dengan berapa lagi yang dibutuhkan.
        (sel,) = b.to_dict()["cells"]
        assert sel["votes"] == 2
        assert sel["accuracy"] is None
        assert sel["needs"] == MIN_CELL_SAMPLE - 2

    def test_ambang_sel_lebih_rendah_dari_ambang_keseluruhan(self) -> None:
        """Menuntut dua puluh lima observasi PER rezim PER agent berarti
        rincian ini tidak akan pernah melaporkan apa pun."""
        from aruna.learning.reliability import MIN_RELIABILITY_SAMPLE

        assert MIN_CELL_SAMPLE < MIN_RELIABILITY_SAMPLE
        assert MIN_CELL_SAMPLE >= 10


class TestPeringkat:
    def _campur(self) -> Breakdown:
        rows = []
        rows += _rows("A", "TRENDING", votes=20, correct=18)   # 90%, terukur
        rows += _rows("B", "TRENDING", votes=20, correct=10)   # 50%, terukur
        rows += _rows("C", "TRENDING", votes=3, correct=3)     # 100%, tipis
        return build_breakdown(rows, dimension="regime")

    def test_yang_tipis_tidak_ikut_diperingkat(self) -> None:
        """Papan peringkat yang memasukkannya akan selalu dimenangkan oleh sel
        yang datanya paling sedikit - karena di situlah seratus persen paling
        mudah terjadi."""
        terbaik = self._campur().best()
        assert terbaik is not None
        assert terbaik.agent == "A"

    def test_terburuk_juga_dari_yang_terukur(self) -> None:
        terburuk = self._campur().worst()
        assert terburuk.agent == "B"

    def test_tanpa_sel_terukur_tidak_ada_pemenang(self) -> None:
        b = build_breakdown(
            _rows("A", "TRENDING", votes=3, correct=3), dimension="regime"
        )
        assert b.best() is None
        assert b.worst() is None


class TestPengumpulan:
    def test_dipisah_per_agent_dan_per_kunci(self) -> None:
        rows = (
            _rows("A", "TRENDING", votes=5, correct=5)
            + _rows("A", "SIDEWAYS", votes=5, correct=0)
            + _rows("B", "TRENDING", votes=5, correct=3)
        )
        b = build_breakdown(rows, dimension="regime")
        assert len(b.cells) == 3
        assert len(b.for_agent("A")) == 2

    def test_kunci_kosong_jadi_unknown(self) -> None:
        b = build_breakdown(
            [{"agent": "A", "key": None, "correct": True}], dimension="regime"
        )
        assert b.cells[0].key == "UNKNOWN"

    def test_kosong_bukan_kesalahan(self) -> None:
        b = build_breakdown([], dimension="regime")
        assert b.cells == ()
        assert b.best() is None

    def test_ringkasan_menyebut_ambangnya(self) -> None:
        d = build_breakdown([], dimension="regime").to_dict()
        assert str(MIN_CELL_SAMPLE) in d["note"]


class TestKueriMenilaiArahBukanKepatuhan:
    """Agent yang menentang council dan ternyata benar harus tercatat benar.

    Menilainya dari kesepakatan akan mengukur kepatuhan - dan agent yang selalu
    ikut suara terbanyak akan terlihat paling andal justru karena tidak pernah
    menyumbang apa pun.
    """

    def _hitung(self, agent_decision, council_decision, council_correct) -> bool:
        sepakat = agent_decision == council_decision
        return council_correct if sepakat else not council_correct

    def test_sepakat_dan_council_benar(self) -> None:
        assert self._hitung("BUY", "BUY", True) is True

    def test_sepakat_dan_council_salah(self) -> None:
        assert self._hitung("BUY", "BUY", False) is False

    def test_menentang_dan_council_salah(self) -> None:
        assert self._hitung("SELL", "BUY", False) is True

    def test_menentang_dan_council_benar(self) -> None:
        assert self._hitung("SELL", "BUY", True) is False

    def test_kueri_memakai_aturan_yang_sama(self) -> None:
        import inspect

        from aruna.db.repositories.learning import LearningRepository

        source = inspect.getsource(LearningRepository.agent_breakdown)
        assert "benar if sepakat else not benar" in source

    def test_hanya_opini_berarah(self) -> None:
        """Agent yang abstain tidak menyatakan apa pun, dan yang bilang
        tidak-ada-posisi tidak bisa benar atau salah terhadap harga."""
        import inspect

        from aruna.db.repositories.learning import LearningRepository

        source = inspect.getsource(LearningRepository.agent_breakdown)
        assert "v.decision IN ('BUY', 'SELL')" in source
        assert "v.abstained = FALSE" in source

    def test_dimensi_asing_ditolak(self) -> None:
        import pytest

        from aruna.db.repositories.learning import LearningRepository

        repo = LearningRepository.__new__(LearningRepository)
        with pytest.raises(ValueError, match="dimensi"):
            import asyncio

            asyncio.run(repo.agent_breakdown("'; DROP TABLE signals; --"))

    def test_tiga_dimensi_pasal_11_2(self) -> None:
        from aruna.db.repositories.learning import LearningRepository

        assert set(LearningRepository.BREAKDOWN_COLUMNS) == {
            "regime", "timeframe", "asset",
        }


class TestTerpasangDiJalurHidup:
    """Rincian yang dibangun dan tidak dipanggil dari mana pun adalah kode
    yang tidak pernah salah karena tidak pernah jalan."""

    def test_autopsy_memanggil_rincian(self) -> None:
        import inspect

        from aruna import cli

        # getsource(_autopsy) hanya memuat badan fungsi itu - baris `def
        # _print_agent_breakdown` tidak ikut, jadi kecocokan ini benar-benar
        # membuktikan pemanggilannya, bukan sekadar keberadaannya.
        source = inspect.getsource(cli._autopsy)
        assert "_print_agent_breakdown" in source

    async def test_rincian_mencetak_dan_menahan_diri(self, capsys) -> None:
        from aruna import cli

        class _Db:
            async def fetch(self, sql, *args):
                return [
                    {
                        "agent": "TECHNICAL", "key": "TRENDING",
                        "agent_decision": "BUY", "council_decision": "BUY",
                        "council_correct": 1,
                    }
                ] * 5

        await cli._print_agent_breakdown(NS(db=_Db()))
        keluaran = capsys.readouterr().out

        assert "AGENT PER REGIME" in keluaran
        assert "needs 10 more" in keluaran
        # Lima observasi tidak boleh dicetak sebagai akurasi.
        assert "100%" not in keluaran

    async def test_rincian_cukup_sampel_menyebut_angkanya(self, capsys) -> None:
        from aruna import cli

        class _Db:
            async def fetch(self, sql, *args):
                return [
                    {
                        "agent": "TECHNICAL", "key": "TRENDING",
                        "agent_decision": "BUY", "council_decision": "BUY",
                        "council_correct": 1,
                    }
                ] * MIN_CELL_SAMPLE

        await cli._print_agent_breakdown(NS(db=_Db()))
        keluaran = capsys.readouterr().out
        assert "100%" in keluaran
        assert "best: TECHNICAL @ TRENDING" in keluaran


def test_sel_tanpa_suara_tidak_membagi_nol() -> None:
    assert Cell(agent="A", key="X", votes=0, correct=0).accuracy is None
