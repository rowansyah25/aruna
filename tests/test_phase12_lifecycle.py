"""Daur hidup strategi, dan batas wewenang ARUNA atasnya (PASAL 12.15, 12.20).

Satu pertanyaan membelah lima status ini: **apakah mengubahnya mengubah
perilaku ARUNA?**

Label pengamatan - ACTIVE, DEGRADED, UNDER_REVIEW - menggambarkan apa yang
terukur, dan ARUNA memasangnya sendiri. Menghentikan sebuah strategi -
SUSPENDED, RETIRED - mengeluarkannya dari pertimbangan, dan itu keputusan
operator.

Sebuah sistem yang boleh menonaktifkan strateginya sendiri akan, pada data
tiga hari, menonaktifkan hampir semuanya - dan yang tersisa adalah yang
kebetulan belum cukup diuji untuk terlihat buruk. Penyempitan itu terjadi
diam-diam dan terbaca seperti pembelajaran.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.learning.evidence import Evidence
from aruna.learning.lifecycle import (
    NEEDS_APPROVAL,
    REVIEW_SAMPLE,
    assess,
    evaluate,
)
from aruna.learning.strategies import UNMAPPED, StrategyStatus


def _nilai(status: StrategyStatus, w: int, kalah: int, *, baseline=0.13,
           pnl="0"):
    return assess(
        "STR-001", status, Evidence(wins=w, losses=kalah),
        baseline=baseline, net_pnl=Decimal(pnl),
    )


class TestLabelPengamatanDipasangSendiri:
    def test_yang_terukur_lebih_buruk_jadi_degraded(self) -> None:
        a = _nilai(StrategyStatus.ACTIVE, 1, 61)
        assert a.proposed is StrategyStatus.DEGRADED
        assert a.applicable, "label pengamatan boleh dipasang tanpa bertanya"

    def test_sample_besar_yang_buruk_naik_ke_under_review(self) -> None:
        a = _nilai(StrategyStatus.DEGRADED, 5, REVIEW_SAMPLE + 20)
        assert a.proposed is StrategyStatus.UNDER_REVIEW
        assert a.applicable

    def test_yang_pulih_kembali_ke_active(self) -> None:
        """Label yang hanya bergerak satu arah berhenti menjadi pengukuran dan
        menjadi jam: setiap strategi sesekali melewati periode buruk."""
        a = _nilai(StrategyStatus.DEGRADED, 30, 30)
        assert a.proposed is StrategyStatus.ACTIVE
        assert a.applicable

    def test_sample_belum_cukup_tidak_menurunkan_apa_pun(self) -> None:
        a = _nilai(StrategyStatus.ACTIVE, 0, 5)
        assert a.proposed is StrategyStatus.ACTIVE
        assert not a.changed

    def test_label_lama_dilepas_saat_buktinya_menyusut(self) -> None:
        """Sebuah DEGRADED yang buktinya tidak lagi cukup harus dilepas, bukan
        dipertahankan karena pernah benar."""
        a = _nilai(StrategyStatus.DEGRADED, 1, 4)
        assert a.proposed is StrategyStatus.ACTIVE

    def test_tanpa_baseline_tidak_ada_yang_berubah(self) -> None:
        a = _nilai(StrategyStatus.ACTIVE, 1, 61, baseline=None)
        assert not a.changed


class TestMenghentikanStrategiButuhOperator:
    """PASAL 12.20."""

    def test_suspended_dan_retired_menuntut_persetujuan(self) -> None:
        assert set(NEEDS_APPROVAL) == {
            StrategyStatus.SUSPENDED,
            StrategyStatus.RETIRED,
        }

    def test_aruna_tidak_pernah_mengusulkan_lewat_penerapan(self) -> None:
        """Tidak ada jalur di mana penilaian menghasilkan status yang
        diterapkan sendiri dan sekaligus menghentikan strategi."""
        for w, kalah in ((0, 500), (1, 999), (5, REVIEW_SAMPLE * 3)):
            a = _nilai(StrategyStatus.ACTIVE, w, kalah)
            assert a.proposed not in NEEDS_APPROVAL, (w, kalah, a.proposed)

    def test_usulan_penghentian_tidak_pernah_ikut_diterapkan(self) -> None:
        """Penjaga untuk aturan yang belum ada, dan itu gunanya.

        Hari ini tidak ada jalur di ``assess`` yang mengusulkan SUSPENDED,
        jadi ``to_apply`` yang menyaring ``changed`` dan yang menyaring
        ``applicable`` menghasilkan hal yang sama - terbukti saat penyaringnya
        dilonggarkan dan test lain tetap hijau.

        Yang dijaga adalah aturan BERIKUTNYA. Begitu seseorang menambahkan
        "strategi yang kalah dua ratus kali berturut-turut diusulkan
        dihentikan", penyaring yang salah akan menuliskannya langsung ke kolom
        status tanpa satu pun manusia melihatnya (PASAL 12.20).
        """
        from aruna.learning.lifecycle import Assessment, LifecycleReport

        usulan = Assessment(
            code="STR-001",
            current=StrategyStatus.ACTIVE,
            proposed=StrategyStatus.SUSPENDED,
            reason="andai suatu hari ada aturan yang mengusulkannya",
            evidence=Evidence(wins=0, losses=300),
        )
        laporan = LifecycleReport(assessments=(usulan,))

        assert usulan.changed
        assert usulan.needs_approval
        assert not usulan.applicable
        assert laporan.to_apply == ()
        assert laporan.to_propose == (usulan,)

    def test_yang_dikeluarkan_operator_tidak_dinilai_ulang(self) -> None:
        """Penangguhan yang bisa dibatalkan mesin bukan penangguhan."""
        for status in NEEDS_APPROVAL:
            a = _nilai(status, 200, 10)
            assert not a.changed, a.proposed
            assert "operator" in a.reason

    def test_penampung_tidak_ikut_dikelola(self) -> None:
        """Label UNDER_REVIEW pada penampung adalah pernyataan tentang
        kelengkapan katalog, bukan tentang performanya.

        Ditemukan saat dijalankan pada data sungguhan: versi pertama menimpanya
        menjadi ACTIVE karena penampungnya memang tidak punya data - dan
        besarnya penampung adalah satu-satunya ukuran seberapa banyak prediksi
        yang belum terpetakan.
        """
        a = assess(
            UNMAPPED.code, StrategyStatus.UNDER_REVIEW,
            Evidence(wins=0, losses=0), baseline=0.13,
        )
        assert not a.changed
        assert "penampung" in a.reason


class TestLaporanDaurHidup:
    def _katalog(self):
        return [
            {"code": "STR-001", "status": "ACTIVE", "wins": 1, "losses": 61},
            {"code": "STR-004", "status": "ACTIVE", "wins": 27, "losses": 30},
            {"code": "STR-009", "status": "SUSPENDED", "wins": 99, "losses": 1},
        ]

    def test_memisahkan_yang_diterapkan_dari_yang_diusulkan(self) -> None:
        laporan = evaluate(self._katalog(), baseline=0.13)
        assert [a.code for a in laporan.to_apply] == ["STR-001"]
        assert laporan.to_propose == ()

    def test_semua_strategi_ikut_dinilai(self) -> None:
        """Termasuk yang tidak berubah: daftar yang hanya memuat perubahan
        tidak bisa menjawab 'apa yang sedang diamati'."""
        laporan = evaluate(self._katalog(), baseline=0.13)
        assert len(laporan.assessments) == 3

    def test_status_yang_tidak_dikenal_tidak_meledak(self) -> None:
        laporan = evaluate(
            [{"code": "STR-X", "status": "ENTAH", "wins": 0, "losses": 0}],
            baseline=0.13,
        )
        assert laporan.assessments[0].current is StrategyStatus.ACTIVE

    def test_baris_tanpa_kode_dilewati(self) -> None:
        assert evaluate([{"status": "ACTIVE"}], baseline=0.13).assessments == ()


class TestDirangkaiDanTidakMenghapus:
    def test_putaran_pembelajaran_menilai_daur_hidup(self) -> None:
        import inspect

        from aruna.learning.adaptive import AdaptiveLearningService

        sumber = inspect.getsource(AdaptiveLearningService.run)
        assert "_assess_lifecycle" in sumber

    def test_yang_butuh_persetujuan_dicatat_bukan_ditulis(self) -> None:
        """Yang menunggu keputusan operator tidak boleh menyentuh kolom
        status."""
        import inspect

        from aruna.learning.adaptive import AdaptiveLearningService

        sumber = inspect.getsource(
            AdaptiveLearningService._assess_lifecycle
        )
        terap = sumber.index("laporan.to_apply")
        usul = sumber.index("laporan.to_propose")
        # set_strategy_status hanya muncul di blok penerapan, sebelum usulan.
        assert sumber.index("set_strategy_status") < usul
        assert terap < usul

    def test_tidak_ada_penghapusan_strategi(self) -> None:
        """PASAL 12.15."""
        import inspect

        from aruna.db.repositories import learning12
        from aruna.learning import lifecycle

        for modul in (learning12, lifecycle):
            sumber = inspect.getsource(modul).upper()
            assert "DELETE FROM STRATEGIES" not in sumber, modul.__name__

    def test_label_pengamatan_tetap_ditawarkan_ke_pemilih(self) -> None:
        """DEGRADED adalah pengamatan, bukan larangan.

        Menyaringnya di penyimpanan berarti ARUNA menonaktifkan strateginya
        sendiri diam-diam - modifikasi otomatis lewat pintu yang tidak bernama
        begitu (PASAL 11.16). Gerbang buktinya sudah menolak yang tidak
        terbukti; menyaring dua kali hanya menyembunyikan alasannya.
        """
        import inspect

        from aruna.db.repositories import learning12

        sumber = inspect.getsource(
            learning12.LearningRepository.strategy_slices
        )
        assert "SUSPENDED" in sumber and "RETIRED" in sumber
        assert "s.status = 'ACTIVE'" not in sumber

    def test_katalog_menampilkan_yang_dikeluarkan_juga(self) -> None:
        """Katalog yang hanya memuat yang masih dipakai selalu terbaca seperti
        kumpulan ide bagus (PASAL 11.21)."""
        import inspect

        from aruna.db.repositories import learning12

        sumber = inspect.getsource(
            learning12.LearningRepository.catalog_with_performance
        )
        # LEFT JOIN supaya strategi tanpa satu pun hasil tetap muncul, dan
        # tidak ada penyaringan status sama sekali di query katalog.
        assert "LEFT JOIN" in sumber
        for saringan in ("s.status =", "s.status NOT IN", "status IN ("):
            assert saringan not in sumber, saringan

    def test_retired_at_hanya_distempel_tidak_dibersihkan(self) -> None:
        import inspect

        from aruna.db.repositories import learning12

        sumber = inspect.getsource(
            learning12.LearningRepository.set_strategy_status
        )
        assert "ELSE retired_at" in sumber


@pytest.mark.asyncio
async def test_penerapan_dan_usulan_lewat_penyimpanan_palsu() -> None:
    """Rangkaiannya, tanpa MySQL."""
    from aruna.learning.adaptive import AdaptiveLearningService

    class _Store:
        def __init__(self) -> None:
            self.status_ditulis: list[tuple[str, str]] = []
            self.events: list[dict] = []

        async def catalog_with_performance(self):
            return [
                {"code": "STR-001", "status": "ACTIVE",
                 "wins": 1, "losses": 61, "net_pnl": -10},
                {"code": "STR-009", "status": "SUSPENDED",
                 "wins": 5, "losses": 5, "net_pnl": 0},
            ]

        async def overall_win_rate(self):
            return 0.13

        async def set_strategy_status(self, code, status, *, reason, now):
            self.status_ditulis.append((code, status))
            return 1

        async def record_event(self, **kwargs):
            self.events.append(kwargs)
            return len(self.events)

    store = _Store()
    svc = AdaptiveLearningService(store)
    from aruna.core.clock import now_utc

    laporan = await svc._assess_lifecycle(now_utc())

    assert store.status_ditulis == [("STR-001", "DEGRADED")]
    jenis = [e["event_type"] for e in store.events]
    assert "STRATEGY_STATUS_CHANGED" in jenis
    # Yang disuspend operator tidak disentuh sama sekali.
    assert not any(k == "STR-009" for k, _ in store.status_ditulis)
    assert laporan.to_propose == ()
