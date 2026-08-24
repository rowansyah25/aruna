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
from types import SimpleNamespace as NS

from aruna.notify.daily import (
    AgentScore,
    DailyReport,
    MarketBlock,
    MutuHarian,
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


class TestBlokMutuHarian:
    """Bagian 18.47."""

    def test_angkanya_dicetak(self) -> None:
        teks = render_daily(_laporan(mutu=MutuHarian(
            rata_mutu=72.0, rata_keyakinan=0.13, rata_cakupan=0.74,
            lolos=7210, gagal=85,
        )))

        assert "DECISION QUALITY" in teks
        assert "72/100" in teks
        assert "13%" in teks
        assert "7210/7295" in teks
        assert "85/7295" in teks

    def test_cakupan_ikut_dicetak(self) -> None:
        """Mutu 84 dari tiga faktor terukur dan mutu 84 dari lima belas adalah
        dua pernyataan yang sangat berbeda, dan tanpa cakupan keduanya mencetak
        baris yang sama persis."""
        teks = render_daily(_laporan(mutu=MutuHarian(
            rata_mutu=84.0, rata_cakupan=0.20
        )))

        assert "Cakupan faktor:" in teks
        assert "20%" in teks

    def test_gerbang_dilaporkan_sebagai_pecahan(self) -> None:
        """2/12 dan 17% membawa angka yang sama, tapi yang pertama menyebut
        penyebutnya - dan "17% gagal" tanpa tahu itu dari dua belas keputusan
        terbaca seperti tren."""
        teks = render_daily(_laporan(mutu=MutuHarian(lolos=10, gagal=2)))

        assert "10/12" in teks
        assert "2/12" in teks

    def test_belum_terhitung_tidak_mencetak_apa_pun(self) -> None:
        """``None`` berarti belum terhitung. "Decision Quality: 0/100" yang
        lahir dari ketiadaan hitungan adalah tuduhan terhadap seluruh hari."""
        assert "DECISION QUALITY" not in render_daily(_laporan(mutu=None))

    def test_hari_tanpa_keputusan_tidak_dicetak_nol(self) -> None:
        """``AVG`` atas nol baris adalah ``NULL``, dan hari tanpa satu pun
        keputusan bukan hari bermutu nol."""
        teks = render_daily(_laporan(mutu=MutuHarian()))

        assert "DECISION QUALITY" not in teks

    def test_kalibrasi_dibawa_sebagai_kalimat_penuh(self) -> None:
        """Bagian 18.47 mencontohkan "Calibration: GOOD", dan satu kata itu
        membuang bagian yang bisa ditindaklanjuti: pita mana yang terlalu
        percaya diri."""
        vonis = "OVERCONFIDENT in 50-65%, 65-80%: stated confidence exceeds accuracy"
        teks = render_daily(_laporan(mutu=MutuHarian(kalibrasi=vonis)))

        assert vonis in teks

    def test_belum_pernah_dikalibrasi_tidak_dicetak_good(self) -> None:
        teks = render_daily(_laporan(mutu=MutuHarian(rata_mutu=70.0)))

        assert "Kalibrasi" not in teks


class TestTerpasangDiJalurHidup:
    """Blok yang dirender dengan benar tapi tak pernah diisi adalah cacat yang
    sudah dua kali terjadi di fungsi ``build()`` yang sama - sekali untuk
    ingatan (PASAL 15.43), sekali untuk pembalikan (bagian 18.52).
    """

    async def test_build_mengisi_mutunya(self) -> None:
        from aruna.notify.daily_service import DailyReportService

        diminta: list[str] = []

        class _Repo:
            async def futures(self, **kw: object) -> MarketBlock:
                return MarketBlock(title="FUTURES", icon="🔮")

            async def spot_or_equity(self, *, title: str, icon: str,
                                     **kw: object) -> MarketBlock:
                return MarketBlock(title=title, icon=icon)

            async def agents(self) -> tuple:
                return ()

            async def council(self, **kw: object) -> NS:
                from aruna.notify.daily import CouncilScore

                return CouncilScore()

            async def correction(self, **kw: object) -> NS:
                from aruna.notify.daily import SelfCorrection

                return SelfCorrection()

            async def pembalikan(self, **kw: object) -> list:
                return []

            async def mutu(self, **kw: object) -> MutuHarian:
                diminta.append("mutu")
                return MutuHarian(rata_mutu=81.0)

        svc = object.__new__(DailyReportService)
        svc.repo = _Repo()
        svc.model_version = "uji"
        svc.uptime_seconds = None
        svc.state = None
        svc.health = None
        svc.memory_repo = None

        laporan = await svc.build(SAAT)

        assert diminta == ["mutu"]
        assert laporan.mutu is not None
        assert laporan.mutu.rata_mutu == 81.0
        assert "81/100" in render_daily(laporan)

    def test_kode_gerbang_dipinjam_bukan_diketik(self) -> None:
        """Kode yang berganti ejaan akan membuat hitungan gerbang menjadi nol
        tanpa satu pun error - laporan yang berbunyi "gerbang mutu tidak pernah
        gagal" persis pada hari ia paling sering gagal."""
        from aruna.db.repositories.daily import KODE_GERBANG_MUTU
        from aruna.signals.withheld import WithheldCode

        assert WithheldCode.QUALITY_GATE.value == KODE_GERBANG_MUTU
