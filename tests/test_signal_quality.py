"""Signal Quality Score dan gerbangnya (PASAL 11.1, 11.13).

Yang diuji bukan angkanya, tapi cara angka itu bisa berbohong: faktor yang
tidak terukur diperlakukan seperti terukur, skor tinggi dari sampel tipis, dan
data basi yang ditebus oleh faktor-faktor bagus di sekitarnya.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from typing import ClassVar

import pytest

from aruna.signals.quality import (
    BLOCKING_FLOOR,
    MIN_COVERAGE,
    MIN_QUALITY,
    Factor,
    QualityScore,
    agreement_factor,
    data_quality_factor,
    evidence_factor,
    freshness_factor,
    gate,
    historical_factor,
    liquidity_factor,
    regime_factor,
    reward_risk_factor,
    score_signal,
    structure_factor,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
JAM = 3600.0


def _state(**kw):
    base = {
        "data_quality": "OK", "market_open": True, "is_realtime": True,
        "declared_delay_sec": 0, "spread_bps": 2.0,
        "bid_depth": None, "ask_depth": None,
    }
    base.update(kw)
    return NS(**base)


def _context(*, values=None, as_of=NOW, news=(), **kw):
    values = values or {}
    return NS(
        state=_state(**kw),
        as_of=as_of,
        structure=NS(confirmed_swings=6, reliable=True),
        regime=NS(regime="TRENDING_UP", confidence=0.8,
                  evidence_used=4, evidence_available=5),
        value=lambda name: values.get(name),
        recent_news=lambda hours=24: news,
    )


class TestFaktorTakTerukur:
    def test_none_bukan_nol(self) -> None:
        """Nol berarti "sudah dinilai dan buruk". None berarti "tidak ada yang
        bisa dinilai". Menyamakan keduanya menghapus perbedaan itu."""
        assert Factor("x", None).measured is False
        assert Factor("x", 0.0).measured is True

    def test_keluar_dari_pembagi(self) -> None:
        """Spot tidak punya funding. Memberinya nilai tengah menaikkan skor
        tiap signal spot dengan angka yang tidak pernah diukur; memberi nol
        menghukumnya karena memperdagangkan pasar yang memang tidak punya
        mekanisme itu."""
        skor = QualityScore(factors=(
            Factor("a", 1.0, 1.0),
            Factor("funding", None, 1.0),
        ))
        assert skor.score == 100
        assert skor.unavailable == ("funding",)

    def test_tanpa_apa_pun_terukur_bukan_nol(self) -> None:
        kosong = QualityScore(factors=(Factor("a", None), Factor("b", None)))
        assert kosong.score is None
        assert kosong.coverage == 0.0

    def test_cakupan_dihitung_dari_bobot(self) -> None:
        skor = QualityScore(factors=(
            Factor("berat", 1.0, weight=3.0),
            Factor("ringan", None, weight=1.0),
        ))
        assert skor.coverage == 0.75


class TestKehadiranBukanKualitas:
    """Skor pertama yang benar-benar terukur: 89/100 untuk council yang
    terbelah dua lawan lima. Delapan dari dua belas faktor terukur bernilai
    1.00, dan semuanya menjawab "apakah datanya ada" - bukan "apakah setup
    ini bagus"."""

    def test_faktor_tak_bernilai_tidak_menaikkan_skor(self) -> None:
        tanpa = QualityScore(factors=(Factor("agreement", 0.3, 4.0),))
        dengan = QualityScore(factors=(
            Factor("agreement", 0.3, 4.0),
            Factor("data_ada", 1.0, 8.0, graded=False),
        ))
        assert tanpa.score == dengan.score

    def test_tapi_tetap_masuk_cakupan(self) -> None:
        """Yang hilang hanya kemampuannya menaikkan skor. Ia tetap tercatat,
        dan ketiadaannya tetap menurunkan cakupan."""
        skor = QualityScore(factors=(
            Factor("agreement", 0.3, 4.0),
            Factor("data_ada", 1.0, 4.0, graded=False),
        ))
        assert skor.coverage == 1.0

    def test_faktor_tak_bernilai_tetap_bisa_memblokir(self) -> None:
        skor = QualityScore(factors=(
            Factor("agreement", 1.0, 4.0),
            Factor("freshness", 0.0, 3.0, blocking=True, graded=False),
        ))
        assert "freshness" in skor.blocked_by

    def test_indikator_tidak_dihitung_dua_kali(self) -> None:
        """trend, momentum, volume dan volatility SUDAH dinilai para agent,
        dan kesimpulan mereka masuk lewat agent_agreement. Menilainya lagi
        menghitung bukti yang sama dua kali."""
        skor = score_signal(
            context=_context(values={
                "macd": 1.0, "vwap": 1.0, "rsi": 55.0, "momentum": 1.0,
                "atr": 2.0, "realised_volatility": 1.0, "bollinger": 1.0,
                "volume_trend": 1.1, "volume_anomaly": 1.0,
            }),
            now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a",), kontra=("b", "c", "d", "e")),
            opinions=(NS(evidence=(1,)),),
        )
        bernilai = {f.name for f in skor.factors if f.graded}
        assert {"trend", "momentum", "volume", "volatility"}.isdisjoint(bernilai)

    def test_council_terbelah_tidak_lagi_dapat_skor_tinggi(self) -> None:
        """Regresi dari kasus nyata: 2 setuju lawan 5 kontra keluar 89/100."""
        skor = score_signal(
            context=_context(values={
                "macd": 1.0, "vwap": 1.0, "rsi": 55.0, "momentum": 1.0,
                "atr": 2.0, "realised_volatility": 1.0, "bollinger": 1.0,
                "volume_trend": 1.1, "volume_anomaly": 1.0,
            }, news=(1,) * 13),
            now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a", "b"), kontra=("c", "d", "e", "f", "g")),
            opinions=(NS(evidence=tuple(range(31))),),
        )
        assert skor.score < 80, skor.to_dict()

    def test_yang_bernilai_hanya_yang_bergradasi(self) -> None:
        """Faktor bernilai adalah yang nilainya lebih tinggi berarti setup
        lebih baik - bukan yang nilainya lebih tinggi berarti data lebih
        banyak."""
        skor = score_signal(context=_context(), now=NOW, horizon_sec=JAM)
        bernilai = {f.name for f in skor.factors if f.graded}
        assert bernilai == {
            "liquidity", "regime_clarity", "risk_reward", "agent_agreement",
            "evidence_strength", "historical",
            "funding", "open_interest", "liquidation",
            # Bagian 18.14 dan 18.15, ditambahkan 2026-08-24. Keduanya
            # BERNILAI - bukan sekadar penanda kehadiran data - karena "router
            # memilih STR-001 berskor 92" dan "router tidak menemukan strategi"
            # menjawab pertanyaan yang sama dengan faktor lain di daftar ini:
            # apakah setup ini bagus, bukan apakah datanya ada.
            "strategy", "scenario",
        }


class TestGerbang:
    def _lulus(self, **kw) -> QualityScore:
        return QualityScore(factors=(
            Factor("data_quality", 1.0, 3.0, blocking=True),
            Factor("freshness", 1.0, 3.0, blocking=True),
            Factor("a", 0.9, 3.0),
            Factor("b", 0.9, 3.0),
            *kw.get("extra", ()),
        ))

    def test_setup_bagus_lolos(self) -> None:
        assert gate(self._lulus()).passed is True

    def test_data_basi_tidak_bisa_ditebus(self) -> None:
        """Cukup menumpuk faktor bagus untuk melewati data yang tidak boleh
        dipakai - itu yang dicegah `blocking` (PASAL 11.7)."""
        skor = QualityScore(factors=(
            Factor("freshness", 0.0, 3.0, blocking=True),
            Factor("a", 1.0, 10.0),
            Factor("b", 1.0, 10.0),
        ))
        putusan = gate(skor)

        assert putusan.quality.score >= 80
        assert putusan.passed is False
        assert any("freshness" in r for r in putusan.reasons)

    def test_gerbang_tak_terukur_juga_memblokir(self) -> None:
        """Pemeriksaan kesegaran yang tidak bisa dijalankan tidak membuktikan
        datanya segar - ia hanya berarti tidak ada yang tahu."""
        skor = QualityScore(factors=(
            Factor("freshness", None, 3.0, blocking=True),
            Factor("a", 1.0, 3.0),
        ))
        assert "freshness" in gate(skor).quality.blocked_by

    def test_cakupan_tipis_ditolak(self) -> None:
        """91/100 dari tiga faktor dan dari tujuh belas faktor tercetak
        identik tanpa cakupan."""
        skor = QualityScore(factors=(
            Factor("data_quality", 1.0, 1.0, blocking=True),
            Factor("freshness", 1.0, 1.0, blocking=True),
            *[Factor(f"n{i}", None, 1.0) for i in range(10)],
        ))
        putusan = gate(skor)

        assert putusan.quality.score == 100
        assert putusan.passed is False
        assert any("cakupan" in r for r in putusan.reasons)

    def test_skor_rendah_ditolak(self) -> None:
        skor = QualityScore(factors=(
            Factor("data_quality", 1.0, 1.0, blocking=True),
            Factor("freshness", 1.0, 1.0, blocking=True),
            Factor("a", 0.0, 6.0),
        ))
        putusan = gate(skor)
        assert putusan.passed is False
        assert any("quality" in r for r in putusan.reasons)

    def test_semua_alasan_dikumpulkan(self) -> None:
        """Kandidat yang gagal karena tiga hal berbeda dari yang gagal karena
        satu, dan autopsi nanti membaca daftar ini."""
        skor = QualityScore(factors=(
            Factor("freshness", 0.0, 1.0, blocking=True),
            Factor("a", 0.0, 1.0),
            Factor("b", None, 8.0),
        ))
        putusan = gate(skor)
        assert len(putusan.reasons) >= 3

    def test_ambang_blocking_masuk_akal(self) -> None:
        assert 0.0 < BLOCKING_FLOOR <= 1.0
        assert 0 < MIN_QUALITY <= 100
        assert 0.0 < MIN_COVERAGE <= 1.0


class TestFaktorSatuan:
    def test_kualitas_data_buruk_memblokir(self) -> None:
        f = data_quality_factor(_state(data_quality="STALE"))
        assert f.blocking is True
        assert f.score == 0.0

    def test_market_tutup_memblokir(self) -> None:
        assert data_quality_factor(_state(market_open=False)).score == 0.0

    def test_kesegaran_relatif_terhadap_horizon(self) -> None:
        """Bukti sepuluh menit sudah basi untuk prediksi 15 menit dan masih
        segar untuk prediksi harian. Ambang tunggal salah di kedua ujung."""
        tua = NOW - timedelta(minutes=10)
        pendek = freshness_factor(_state(), tua, NOW, horizon_sec=15 * 60)
        panjang = freshness_factor(_state(), tua, NOW, horizon_sec=24 * 3600)

        assert pendek.score < panjang.score
        assert panjang.score > 0.9

    def test_keterlambatan_dideklarasi_menambah_umur(self) -> None:
        """Keterlambatan yang diakui feed adalah bagian dari umur bukti,
        bukan catatan kaki."""
        segar = freshness_factor(_state(), NOW, NOW, horizon_sec=JAM)
        tertunda = freshness_factor(
            _state(is_realtime=False, declared_delay_sec=900),
            NOW, NOW, horizon_sec=JAM,
        )
        assert tertunda.score < segar.score

    def test_horizon_tak_diketahui_memblokir(self) -> None:
        f = freshness_factor(_state(), NOW, NOW, horizon_sec=0)
        assert f.score is None
        assert f.blocking is True

    def test_struktur_kurang_swing_dinilai_rendah(self) -> None:
        kurang = structure_factor(NS(confirmed_swings=2, reliable=False))
        cukup = structure_factor(NS(confirmed_swings=6, reliable=True))
        assert kurang.score < cukup.score

    def test_tanpa_struktur_tidak_terukur(self) -> None:
        assert structure_factor(None).score is None

    def test_regime_tanpa_bukti_tidak_terukur(self) -> None:
        f = regime_factor(NS(regime="X", confidence=0.9,
                             evidence_used=0, evidence_available=0))
        assert f.score is None

    def test_spread_lebar_menurunkan_likuiditas(self) -> None:
        sempit = liquidity_factor(_state(spread_bps=1.0))
        lebar = liquidity_factor(_state(spread_bps=18.0))
        assert lebar.score < sempit.score

    def test_spread_tak_diketahui_tidak_terukur(self) -> None:
        assert liquidity_factor(_state(spread_bps=None)).score is None

    def test_abstain_tidak_menurunkan_kesepakatan(self) -> None:
        """Agent tanpa bukti tidak sedang menentang."""
        tanpa = agreement_factor(NS(setuju=("a", "b"), kontra=("c",)))
        dengan = agreement_factor(
            NS(setuju=("a", "b"), kontra=("c",), abstain=("d", "e"))
        )
        assert tanpa.score == dengan.score

    def test_tidak_ada_yang_memilih_tidak_terukur(self) -> None:
        assert agreement_factor(NS(setuju=(), kontra=())).score is None

    def test_stop_di_harga_masuk_bukan_risiko_nol(self) -> None:
        """Itu rencana yang tidak punya tempat untuk terbukti salah."""
        f = reward_risk_factor(100, 100, 120)
        assert f.score == 0.0

    def test_rr_lebih_baik_skor_lebih_tinggi(self) -> None:
        buruk = reward_risk_factor(100, 90, 105)
        bagus = reward_risk_factor(100, 90, 130)
        assert bagus.score > buruk.score

    def test_level_belum_lengkap_tidak_terukur(self) -> None:
        assert reward_risk_factor(100, None, 120).score is None

    def test_rekam_jejak_tipis_tidak_terukur(self) -> None:
        """Akurasi dari lima prediksi bukan rekam jejak; ia kebisingan yang
        kebetulan punya angka."""
        assert historical_factor(0.9, sample=5).score is None
        assert historical_factor(0.9, sample=40).score == pytest.approx(0.9)

    def test_tanpa_bukti_kekuatan_tidak_terukur(self) -> None:
        assert evidence_factor(()).score is None


class TestGerbangAnomali:
    """PASAL 11.8. Setup yang lahir dari volume lima belas kali garis dasarnya
    tidak menjadi lebih baik karena spread-nya kebetulan sempit."""

    def _bagus(self, anomalies) -> object:
        return score_signal(
            context=_context(values={
                "macd": 1.0, "vwap": 1.0, "rsi": 55.0, "momentum": 1.0,
                "atr": 2.0, "realised_volatility": 1.0, "bollinger": 1.0,
                "volume_trend": 1.1, "volume_anomaly": 1.0,
            }),
            now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a", "b", "c"), kontra=()),
            opinions=(NS(evidence=tuple(range(30))),),
            entry=100, stop=95, target=130,
            accuracy=0.9, sample=40,
            anomalies=anomalies,
        )

    def test_anomali_menolak_meski_skor_tinggi(self) -> None:
        from aruna.signals.anomaly import Anomaly, AnomalyKind, AnomalyReport

        buruk = AnomalyReport((Anomaly(AnomalyKind.VOLUME_SPIKE, 14.0, 10.0),))
        putusan = gate(self._bagus(buruk))

        assert putusan.quality.score >= MIN_QUALITY
        assert putusan.passed is False
        assert "anomaly" in putusan.quality.blocked_by

    def test_bersih_lolos(self) -> None:
        from aruna.signals.anomaly import AnomalyReport

        assert gate(self._bagus(AnomalyReport())).passed is True

    def test_tidak_diperiksa_memblokir(self) -> None:
        """Gerbang yang tidak dijalankan tidak membuktikan pasarnya normal."""
        assert "anomaly" in gate(self._bagus(None)).quality.blocked_by

    def test_sebagian_tak_terperiksa_tetap_lolos(self) -> None:
        """Berbeda dari 11.7: pasal ini bertanya "apakah kami mendeteksi
        sesuatu", bukan "buktikan tidak ada apa-apa"."""
        from aruna.signals.anomaly import AnomalyReport

        sebagian = AnomalyReport((), ("spread", "volume"))
        assert gate(self._bagus(sebagian)).passed is True

    def test_anomali_tidak_menaikkan_maupun_menurunkan_skor(self) -> None:
        """Gerbang, bukan bobot: ia menolak atau meloloskan, tidak menawar."""
        from aruna.signals.anomaly import AnomalyReport

        bersih = self._bagus(AnomalyReport())
        assert all(not f.graded for f in bersih.factors if f.name == "anomaly")


class TestSkorLengkap:
    #: Tujuh belas butir daftar PASAL 11.1, dieja supaya sebuah faktor yang
    #: hilang terlihat namanya - bukan sekadar hitungan yang meleset satu.
    PASAL_11_1: ClassVar[frozenset[str]] = frozenset({
        "data_quality", "freshness", "structure", "trend", "momentum",
        "volume", "volatility", "liquidity", "news", "regime_clarity",
        "risk_reward", "agent_agreement", "evidence_strength", "historical",
        "funding", "open_interest", "liquidation",
    })

    def _semua(self) -> set[str]:
        skor = score_signal(
            context=_context(), now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a",), kontra=()), opinions=(),
        )
        return {f.name for f in skor.factors}

    def test_setiap_butir_pasal_11_1_ada(self) -> None:
        assert self._semua() >= self.PASAL_11_1

    #: Faktor di luar PASAL 11.1, masing-masing dengan pasalnya sendiri.
    #:
    #: Dieja terpisah supaya jelas bahwa daftar 11.1 tidak diam-diam tumbuh:
    #: tiap tambahan harus menyebut dari mana ia datang.
    DI_LUAR_11_1: ClassVar[dict[str, str]] = {
        "anomaly": "PASAL 11.8 - gerbang, bukan bobot",
        "strategy": "bagian 18.14 - mutu strategi pilihan router Phase 17",
        "scenario": "bagian 18.15 - kekokohan skenario Phase 16",
    }

    def test_tidak_ada_faktor_tanpa_pasal(self) -> None:
        """**Yang sebenarnya dijaga.** Sebuah faktor yang muncul di skor tanpa
        pasal yang memintanya adalah bobot yang tidak bisa dibantah siapa pun -
        dan skor mutu yang memuatnya berhenti bisa dijelaskan.

        Versi sebelumnya menuntut daftarnya PERSIS 11.1 + anomaly, dan itu
        MERAH begitu bagian 18.14 dan 18.15 masuk. Yang benar bukan
        melonggarkannya melainkan menuntut tiap tambahan menyebut sumbernya.
        """
        assert self.PASAL_11_1 | set(self.DI_LUAR_11_1) == self._semua()

    def test_spot_tetap_bisa_dinilai(self) -> None:
        """Funding, open interest dan likuidasi memang tidak ada di spot, dan
        itu bukan kekurangan yang layak dihukum."""
        skor = score_signal(
            context=_context(values={
                "macd": 1.0, "rsi": 55.0, "atr": 2.0, "volume_trend": 1.1,
            }),
            now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a", "b"), kontra=("c",)),
            opinions=(NS(evidence=(1, 2, 3)),),
            entry=100, stop=95, target=115,
        )
        assert {"funding", "open_interest", "liquidation"} <= set(skor.unavailable)
        assert skor.score is not None
        assert skor.coverage >= MIN_COVERAGE

    def test_data_basi_memblokir_meski_sisanya_bagus(self) -> None:
        skor = score_signal(
            context=_context(data_quality="STALE", values={"macd": 1.0}),
            now=NOW, horizon_sec=JAM,
            split=NS(setuju=("a", "b", "c"), kontra=()),
            opinions=(NS(evidence=tuple(range(30))),),
            entry=100, stop=95, target=130,
        )
        assert gate(skor).passed is False

    def test_ringkasan_membawa_cakupan(self) -> None:
        """Skor tanpa cakupan di sebelahnya adalah angka yang tidak bisa
        dinilai pembacanya."""
        d = score_signal(
            context=_context(), now=NOW, horizon_sec=JAM
        ).to_dict()
        assert "coverage" in d
        assert "measured" in d
        assert "unavailable" in d
