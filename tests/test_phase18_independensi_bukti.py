"""Bukti yang saling menyalin tidak dihitung penuh (bagian 18.6).

**Celah 2, dan alatnya ternyata sudah ada.** `OpinionPool.independence()`
sudah mengukur "berapa bagian bukti yang benar-benar berbeda", dengan
docstring yang sudah mengeja alasannya:

    Six agents all reading RSI is one witness repeated six times, and
    SPEC 17 says the judge must not count that as six.

Angkanya bahkan sudah disimpan ke `deliberations.independence`. Yang hilang
cuma pemakaiannya di skor mutu - dihitung, disimpan, lalu tidak pernah
menyentuh keputusan. Pola "dihitung lalu dibuang" yang sudah berulang di
proyek ini.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from aruna.signals.quality import evidence_factor


def _opini(*kunci: str) -> NS:
    """Bentuknya mengikuti `AgentOpinion`: `evidence` dan `evidence_keys`."""
    return NS(
        evidence=tuple(kunci),
        evidence_keys=tuple(kunci),
        abstained=False,
    )


class TestBuktiYangMenyalinTidakDihitungPenuh:
    def test_enam_agent_membaca_rsi_bukan_enam_saksi(self) -> None:
        """Kalimat docstring `independence()` apa adanya, dijadikan test."""
        satu_saksi = evidence_factor(tuple(_opini("rsi") for _ in range(6)))
        enam_saksi = evidence_factor(
            (
                _opini("rsi"), _opini("macd"), _opini("volume"),
                _opini("structure"), _opini("news"), _opini("funding"),
            )
        )

        assert enam_saksi.score > satu_saksi.score

    def test_sepuluh_lemah_tidak_mengalahkan_tiga_kuat_mandiri(self) -> None:
        """Bagian 18.5 apa adanya: "10 indikator lemah tidak otomatis lebih
        baik daripada 3 evidence yang kuat dan independen"."""
        sepuluh_menyalin = evidence_factor(
            tuple(_opini("rsi", "rsi_slope") for _ in range(5))
        )
        tiga_mandiri = evidence_factor(
            (_opini("rsi", "macd", "volume"), _opini("structure", "news"))
        )

        assert tiga_mandiri.score >= sepuluh_menyalin.score

    def test_alasannya_menyebut_independensinya(self) -> None:
        """Skor yang turun tanpa menyebut sebabnya tidak bisa dibantah."""
        f = evidence_factor(tuple(_opini("rsi") for _ in range(4)))

        assert "independensi" in f.detail
        assert "setara" in f.detail


class TestAturannyaDipinjam:
    def test_memakai_opinionpool_bukan_rumus_kedua(self) -> None:
        """Dua rumus independensi adalah dua angka yang harus tetap sepakat
        selamanya. Yang satu ini sudah dipakai judge dan sudah disimpan ke
        `deliberations.independence`."""
        import inspect

        sumber = inspect.getsource(evidence_factor)

        assert "OpinionPool" in sumber
        assert "independence()" in sumber

    def test_hasilnya_persis_sama_dengan_pool(self) -> None:
        from aruna.agents.analyst import OpinionPool

        opini = (_opini("rsi", "macd"), _opini("rsi"))
        pool = OpinionPool(opinions=opini)
        f = evidence_factor(opini)

        assert f"{pool.independence() * 100:.0f}%" in f.detail


class TestYangTidakBolehDihukum:
    def test_tanpa_kunci_bukti_independensi_tak_terukur(self) -> None:
        """Opini yang tidak membawa `evidence_keys` tidak bisa diukur
        independensinya - dan itu bukan sama dengan redundan penuh.
        Menghukumnya membuat tiap jalur yang belum melaporkan kuncinya terbaca
        seolah seluruh buktinya menyalin."""
        tanpa = evidence_factor((NS(evidence=(1, 2, 3), evidence_keys=()),))

        assert tanpa.score is not None
        assert "tak terukur" in tanpa.detail

    def test_tanpa_opini_sama_sekali_tidak_terukur(self) -> None:
        assert evidence_factor(()).score is None
        assert evidence_factor(None).score is None
