"""Statistik Phase 18 di laporan harian (bagian 18.47, 18.48, 18.51).

Bagian 18.47 meminta mutu keputusan masuk laporan harian, dan sampai berkas ini
ada tidak satu pun angkanya muncul di sana - walau ketiganya sudah tersimpan di
tiap baris sejak lama: ``signal_snapshots.signal_quality``,
``.quality_coverage``, dan ``signals.withheld_code``.

Bagian 18.48 lebih tajam lagi: *"Namun selalu tampilkan sample size."* Papan
peringkat agent sudah ada, penyebutnya sudah dihitung mesin keandalan, dan
``AgentScore`` membuangnya di baris terakhir.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aruna.notify.daily import (
    AgentScore,
    DailyReport,
    MarketBlock,
    Tally,
    render_daily,
)

SAAT = datetime(2026, 8, 24, tzinfo=UTC)


def _laporan(**kw: object) -> DailyReport:
    return DailyReport(
        date=SAAT,
        markets=(MarketBlock(title="FUTURES", icon="🔮", tally=Tally(total=3)),),
        **kw,
    )


class TestSampelAgentSelaluDisebut:
    """Bagian 18.48."""

    def test_n_dicetak_di_sebelah_persennya(self) -> None:
        teks = render_daily(
            _laporan(agents=(AgentScore("VOLUME", 55.23, sample=746),))
        )

        assert "55.23% Win Rate (n=746)" in teks

    def test_tanpa_sampel_tidak_mencetak_n_nol(self) -> None:
        """``n=0`` di sebelah persen yang jelas-jelas terhitung terbaca seperti
        angka yang lahir dari ketiadaan - dan itu bukan yang terjadi."""
        teks = render_daily(_laporan(agents=(AgentScore("VOLUME", 55.23),)))

        assert "n=" not in teks

    def test_yang_terbawah_juga_membawa_sampelnya(self) -> None:
        """**Justru di sini yang paling menentukan.** Agent terbawah dengan
        sampel tipis bukan agent terburuk - ia agent yang belum terukur, dan
        peringkat yang menyebutnya "Lowest" tanpa penyebut menuduhnya."""
        teks = render_daily(_laporan(agents=(
            AgentScore("A", 90.0, sample=800),
            AgentScore("B", 80.0, sample=700),
            AgentScore("C", 70.0, sample=600),
            AgentScore("D", 40.0, sample=21),
        )))

        assert "D — 40.00% (n=21)" in teks

    def test_repositori_mengoper_penyebutnya(self) -> None:
        """Bidang yang ada tapi tak pernah diisi adalah cacat yang sama sekali
        lagi. Diuji lewat sumber karena kueri sungguhannya butuh database."""
        import inspect

        from aruna.db.repositories.daily import DailyRepository

        assert "sample=record.scored" in inspect.getsource(DailyRepository.agents)


