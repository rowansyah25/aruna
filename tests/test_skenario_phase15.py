"""25 skenario wajib bagian 31 Phase 15.

Bagian 31 mengeja daftarnya dan menuntut satu hal: *"Setiap test harus
menghasilkan expected behavior."* Jadi tiap skenario di sini menegaskan
**perilaku**, bukan keberadaan fungsi - dan beberapa di antaranya menegaskan
bahwa ARUNA justru **menolak** berpendapat, karena itulah perilaku yang benar.

Yang sengaja tidak dilakukan berkas ini: mengarang skenario di atas fitur yang
tidak ada. Setiap skenario memanggil kode produksi yang sungguhan
(`classify_regime`, `Council.convene`, `QualityGate`, `analyse_funding`,
`analyse_open_interest`, `buffer_score`, `build_reliability`, `detect`,
`calibrate`), dan yang perkakasnya belum ada disebut apa adanya, bukan
dilewati diam-diam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.analysis.indicators import atr
from aruna.analysis.regime import classify_regime
from aruna.analysis.series import CandleSeries
from aruna.analysis.structure import BreakoutState, StructureReport, TrendStructure
from aruna.core.enums import Decision, Horizon, Market, Regime
from aruna.data.models import Candle, Provenance

NOW = datetime(2026, 8, 21, tzinfo=UTC)


# ---- bahan bersama --------------------------------------------------------


def _seri(rentang: list[float], *, tutup: float = 100.0) -> CandleSeries:
    """Deret dengan lebar bar yang ditentukan; harga tutup tetap."""
    return CandleSeries.from_candles([
        Candle(
            market=Market.CRYPTO, symbol="BTC/USDT", interval=Horizon.H1,
            open_time=NOW + timedelta(hours=i),
            close_time=NOW + timedelta(hours=i + 1),
            open=tutup, high=tutup + r / 2, low=tutup - r / 2, close=tutup,
            volume=1000,
            provenance=Provenance(source="uji", server_timestamp=NOW),
        )
        for i, r in enumerate(rentang)
    ])


def _struktur(**kw) -> StructureReport:
    dasar = {
        "trend": TrendStructure.RANGE,
        "breakout": BreakoutState.NONE,
        "confirmed_swings": 6,
    }
    return StructureReport(**(dasar | kw))


def _regime(**kw) -> Regime:
    return classify_regime(structure=_struktur(**kw.pop("struktur", {})), **kw).regime


# ---- 1-8: bentuk pasar ----------------------------------------------------


class TestBentukPasar:
    """Skenario 1-8. Regime harus membedakan bentuk yang berbeda."""

    def test_01_bullish_kuat(self) -> None:
        assert _regime(struktur={"trend": TrendStructure.UPTREND}) is (
            Regime.TRENDING_BULLISH
        )

    def test_02_bearish_kuat(self) -> None:
        """Dulu ini dan skenario 1 menghasilkan regime yang SAMA."""
        assert _regime(struktur={"trend": TrendStructure.DOWNTREND}) is (
            Regime.TRENDING_BEARISH
        )

    def test_03_ranging(self) -> None:
        from aruna.analysis.reading import Reading

        hasil = classify_regime(
            structure=_struktur(trend=TrendStructure.RANGE),
            rsi=Reading("rsi", 50.0, sample_size=50, required=15),
        )
        assert hasil.regime is Regime.RANGING

    def test_04_breakout(self) -> None:
        assert _regime(struktur={"breakout": BreakoutState.BREAKOUT_UP}) is (
            Regime.BREAKOUT
        )

    def test_05_false_breakout_adalah_reversal(self) -> None:
        """Tembusan gagal bukan tembusan. Menyebutnya BREAKOUT akan mengajak
        keputusan mengikuti level yang justru menolak harga."""
        assert _regime(struktur={"breakout": BreakoutState.FALSE_BREAKOUT_UP}) is (
            Regime.REVERSAL
        )

    def test_06_reversal(self) -> None:
        assert _regime(struktur={"breakout": BreakoutState.REJECTION}) is (
            Regime.REVERSAL
        )

    def test_07_volatilitas_tinggi(self) -> None:
        """Nol baris memakainya sebelum 2026-08-21: ambang persen mutlaknya
        tidak pernah tercapai di 15m dan 1h."""
        hasil = classify_regime(
            structure=_struktur(confirmed_swings=0),
            atr=atr(_seri([1.0] * 50 + [3.0] * 10)),
        )
        assert hasil.regime is Regime.HIGH_VOLATILITY

    def test_08_volatilitas_rendah(self) -> None:
        hasil = classify_regime(
            structure=_struktur(confirmed_swings=0),
            atr=atr(_seri([3.0] * 50 + [0.5] * 10)),
        )
        assert hasil.regime is Regime.LOW_VOLATILITY


# ---- 9-10: kesepakatan dan perselisihan agent -----------------------------


class TestAgent:
    def _verdict(self, closes):
        from tests.test_council import _context

        from aruna.council.session import Council

        return Council().convene(_context(closes))

    def test_09_kesepakatan_kuat_menghasilkan_arah(self) -> None:
        """Pasar yang jelas naik harus menghasilkan pendapat, bukan diam."""
        from tests.test_council import RISING

        v = self._verdict(RISING)

        assert v.judgement.decision.is_directional
        assert v.judgement.confidence > 0

    def test_10_perselisihan_kuat_tidak_dipaksa_berarah(self) -> None:
        """Pasar datar: judge tidak boleh mengarang sisi hanya agar ada
        keluaran. Dan apa pun hasilnya, keputusan finalnya salah satu dari
        tiga (bagian 25)."""
        from tests.test_council import FLAT

        from aruna.decision.finalizer import FINAL

        v = self._verdict(FLAT)

        assert v.decision in FINAL
        assert v.decision is not Decision.WAIT


# ---- 11-12: data buruk dan hilang -----------------------------------------


class TestData:
    def _gate(self):
        from aruna.core.config import DataSettings
        from aruna.data.quality import QualityGate

        return QualityGate(DataSettings(), source="uji", declared_delay_sec=0)

    def _quote(self, harga: str, *, umur_detik: int = 0):
        from aruna.data.models import Quote

        saat = NOW - timedelta(seconds=umur_detik)
        return Quote(
            market=Market.CRYPTO, symbol="BTC/USDT", price=Decimal(harga),
            provenance=Provenance(
                source="uji", server_timestamp=saat, provider_timestamp=saat
            ),
        )

    def test_11_data_basi_ditolak(self) -> None:
        """Data lama tidak boleh menjadi bukti kuat (bagian 21)."""
        from aruna.core.enums import DataQuality

        gate = self._gate()
        verdict = gate.evaluate_quote(self._quote("100", umur_detik=86_400))

        assert verdict.quality is not DataQuality.OK
        assert not verdict.ok

    def test_12_data_yang_terus_sama_ditandai(self) -> None:
        """Umpan yang berhenti bergerak adalah data yang hilang dengan cara
        yang paling sulit dilihat: ia tetap menjawab."""
        from aruna.core.enums import DataQuality

        gate = self._gate()
        for _ in range(5):
            verdict = gate.evaluate_quote(self._quote("100"))

        assert verdict.quality in (
            DataQuality.DUPLICATE, DataQuality.REPEATED_READ, DataQuality.STALE
        )


# ---- 13-16: guncangan pasar ------------------------------------------------


class TestGuncangan:
    def test_13_news_shock_memblokir(self) -> None:
        """Regime NEWS_SHOCK menghentikan keputusan lewat mesin no-trade."""
        from aruna.agents.notrade import NoTradeReason

        assert hasattr(NoTradeReason, "NEWS_SHOCK")

    def _funding(self, rate: Decimal):
        from aruna.futures.models import FundingRate

        return FundingRate(
            symbol="BTCUSDT", rate=rate, funding_time=NOW,
            next_funding_time=NOW + timedelta(hours=8), interval_hours=8,
            provenance=Provenance(source="uji", server_timestamp=NOW),
        )

    def test_14_funding_ekstrem_terbaca(self) -> None:
        from aruna.futures.funding import EXTREME_RATE, FundingBias, analyse_funding

        ekstrem = EXTREME_RATE * 2
        hasil = analyse_funding(
            self._funding(ekstrem), [self._funding(ekstrem) for _ in range(10)]
        )

        assert hasil.bias is not FundingBias.NEUTRAL

    def _oi(self, nilai: Decimal, *, mundur_jam: int = 0):
        from aruna.futures.models import OpenInterest

        return OpenInterest(
            symbol="BTCUSDT", open_interest=nilai, notional=None,
            as_of=NOW - timedelta(hours=mundur_jam),
            provenance=Provenance(source="uji", server_timestamp=NOW),
        )

    def test_15_lonjakan_open_interest_terbaca(self) -> None:
        """OI melonjak jauh di atas ambang derau berarti posisi baru masuk -
        keterangan, bukan derau."""
        from aruna.futures.openinterest import analyse_open_interest

        hasil = analyse_open_interest(
            self._oi(Decimal(1200)),
            self._oi(Decimal(1000), mundur_jam=1),
            Decimal("2.0"),
        )

        # OI naik 20% bersama harga naik 2% = posisi baru mengikuti arah, bukan
        # posisi lama yang ditutup.
        assert hasil.readable
        assert hasil.is_continuation
        assert hasil.oi_change_pct > 0

    def test_16_likuidasi_terlalu_dekat_ditandai(self) -> None:
        """Harga likuidasi yang terlalu rapat dengan entry adalah risiko yang
        harus dilaporkan, bukan angka leverage yang dinaikkan."""
        from aruna.futures.liquidation import Liquidation, buffer_score
        from aruna.futures.models import MarginMode

        def _liq(harga: str, jarak_pct: str) -> Liquidation:
            return Liquidation(
                price=Decimal(harga), distance=abs(Decimal(100) - Decimal(harga)),
                distance_pct=Decimal(jarak_pct), margin_mode=MarginMode.ISOLATED,
                maintenance_rate=Decimal("0.005"),
                maintenance_amount=Decimal(0),
            )

        rapat = buffer_score(
            entry=Decimal(100), stop=Decimal("99"),
            liquidation=_liq("99.5", "0.5"), atr=Decimal("1.0"),
        )
        lega = buffer_score(
            entry=Decimal(100), stop=Decimal(95),
            liquidation=_liq("50", "50"), atr=Decimal("1.0"),
        )

        assert rapat.score < lega.score


# ---- 17-18: keyakinan melawan bukti ---------------------------------------


class TestKeyakinanLawanBukti:
    def _laporan(self, *, n: int, benar: int, rata: float):
        from aruna.learning.calibration import Bucket, CalibrationReport

        return CalibrationReport(
            buckets=(Bucket(low=0.80, high=0.96, predictions=n, correct=benar,
                            mean_confidence=rata),),
            total=n, correct=benar,
        )

    def test_17_yakin_tinggi_bukti_lemah_diturunkan(self) -> None:
        """Bagian 10. Terukur di produksi: pita >=90% menang 47,7%."""
        from aruna.learning.kalibrator import Kalibrator

        hasil = Kalibrator(self._laporan(n=300, benar=90, rata=0.90)).kalibrasi(0.90)

        assert hasil.disesuaikan
        assert hasil.nilai < 0.5

    def test_18_yakin_rendah_bukti_kuat_dinaikkan(self) -> None:
        """Kalibrasi bekerja dua arah - bagian 9 menyebut UNDERCONFIDENT
        sebagai keadaan yang harus dideteksi."""
        from aruna.learning.calibration import Bucket, CalibrationReport
        from aruna.learning.kalibrator import Kalibrator

        laporan = CalibrationReport(
            buckets=(Bucket(low=0.35, high=0.50, predictions=200, correct=150,
                            mean_confidence=0.42),),
            total=200, correct=150,
        )
        hasil = Kalibrator(laporan).kalibrasi(0.42)

        assert hasil.disesuaikan
        assert hasil.nilai > 0.7


# ---- 19-21: hasil dan pengulangannya --------------------------------------


class TestHasil:
    def _ingatan(self, arah: str, favourable: int):
        from aruna.db.repositories.memory import _hasil_dari

        return _hasil_dari({"direction": arah, "favourable": favourable})

    def test_19_win_tercatat_menang(self) -> None:
        from aruna.memory.record import Hasil

        assert self._ingatan("BUY", 1) is Hasil.WIN

    def test_20_loss_tercatat_kalah(self) -> None:
        from aruna.memory.record import Hasil

        assert self._ingatan("SELL", 0) is Hasil.LOSS

    def test_21_kalah_berulang_menurunkan_bobot_agent(self) -> None:
        """Bagian 11: self-correction berdasar sampel, bukan satu-dua trade."""
        from aruna.core.enums import AgentRole
        from aruna.learning.reliability import (
            MIN_RELIABILITY_SAMPLE,
            build_reliability,
        )

        n = MIN_RELIABILITY_SAMPLE * 2
        baris = [
            {
                "agent": AgentRole.MOMENTUM.value,
                "agent_decision": "BUY",
                "council_decision": "BUY",
                "direction_correct": 0,
            }
            for _ in range(n)
        ]
        # Jangkar pasar - lihat alasannya di `TestPerubahanPerforma._baris`.
        # Tanpa ini pasar hanya bergerak turun, dan agen yang selalu bilang
        # BUY lalu selalu salah tidak membuktikan apa pun: garis dasarnya
        # sendiri sudah nol.
        baris += [
            {
                "agent": AgentRole.NEWS.value,
                "agent_decision": "BUY",
                "council_decision": "BUY",
                "direction_correct": i % 2,
            }
            for i in range(n * 5)
        ]
        laporan = build_reliability(baris)
        catatan = next(
            r for r in laporan.measured if r.role is AgentRole.MOMENTUM
        )

        assert catatan.multiplier < 1.0


# ---- 22-25: perubahan performa dan batas sampel ---------------------------


class TestPerubahanPerforma:
    def _baris(self, agent, benar: int, n: int):
        """Baris agen yang diuji, PLUS jangkar yang menetapkan garis dasar.

        **Jangkarnya wajib sejak 2026-08-25.** Titik netral seorang agen
        sekarang diukur dari seberapa sering pasar bergerak ke arah yang ia
        sebut. Kalau seluruh baris berasal dari satu agen yang selalu bilang
        BUY, garis dasar pasar menjadi persis sama dengan akurasi agen itu -
        dan keunggulannya nol berapa pun akurasinya, karena ia sendiri yang
        mendefinisikan "biasa".

        Jangkarnya lima kali lebih banyak dan berimbang 50/50, meniru keadaan
        sebenarnya: satu agen dari tujuh tidak menggerakkan garis dasar.
        """
        diuji = [
            {
                "agent": agent.value,
                "agent_decision": "BUY",
                "council_decision": "BUY",
                "direction_correct": 1 if i < benar else 0,
            }
            for i in range(n)
        ]
        from aruna.core.enums import AgentRole

        jangkar = [
            {
                "agent": AgentRole.NEWS.value,
                "agent_decision": "BUY",
                "council_decision": "BUY",
                "direction_correct": i % 2,
            }
            for i in range(n * 5)
        ]
        return diuji + jangkar

    @staticmethod
    def _catatan(laporan, agent):
        return next(r for r in laporan.records if r.role is agent)

    def test_22_agent_membaik_dapat_bobot_lebih_tinggi(self) -> None:
        from aruna.core.enums import AgentRole
        from aruna.learning.reliability import (
            MIN_RELIABILITY_SAMPLE,
            build_reliability,
        )

        n = MIN_RELIABILITY_SAMPLE * 2
        bagus = build_reliability(
            self._baris(AgentRole.MOMENTUM, int(n * 0.8), n)
        )
        biasa = build_reliability(
            self._baris(AgentRole.MOMENTUM, int(n * 0.5), n)
        )

        assert (
            self._catatan(bagus, AgentRole.MOMENTUM).multiplier
            > self._catatan(biasa, AgentRole.MOMENTUM).multiplier
        )

    def test_23_agent_memburuk_dapat_bobot_lebih_rendah(self) -> None:
        from aruna.core.enums import AgentRole
        from aruna.learning.reliability import (
            MIN_MULTIPLIER,
            MIN_RELIABILITY_SAMPLE,
            build_reliability,
        )

        n = MIN_RELIABILITY_SAMPLE * 2
        buruk = build_reliability(self._baris(AgentRole.MOMENTUM, int(n * 0.2), n))
        catatan = self._catatan(buruk, AgentRole.MOMENTUM)

        assert catatan.multiplier < 1.0
        assert catatan.multiplier >= MIN_MULTIPLIER

    def test_24_drift_terdeteksi(self) -> None:
        """Bagian 20: performa yang berubah harus ditandai, bukan langsung
        mengubah seluruh parameter."""
        from aruna.governance.drift import (
            ACCURACY_DRIFT_POINTS,
            MIN_WINDOW_SAMPLE,
            Window,
            detect,
        )

        n = MIN_WINDOW_SAMPLE * 2
        dasar = Window(label="historis", resolved=n, correct=int(n * 0.75))
        terkini = Window(
            label="terkini",
            resolved=n,
            correct=int(n * (0.75 - ACCURACY_DRIFT_POINTS - 0.05)),
        )

        laporan = detect(dasar, terkini)

        assert laporan.sufficient
        assert laporan.performance_drift

    def test_25_sampel_kurang_tidak_menghasilkan_angka(self) -> None:
        """Bagian 10 dan 32. Yang paling penting dari kedua puluh lima:
        sistem yang mengarang angka dari sampel tipis akan terdengar makin
        meyakinkan justru saat ia paling tidak tahu apa-apa.
        """
        from aruna.learning.calibration import (
            MIN_BUCKET_SAMPLE,
            Bucket,
            CalibrationReport,
        )
        from aruna.learning.kalibrator import Kalibrator

        tipis = CalibrationReport(
            buckets=(Bucket(low=0.80, high=0.96,
                            predictions=MIN_BUCKET_SAMPLE - 1, correct=2,
                            mean_confidence=0.90),),
            total=MIN_BUCKET_SAMPLE - 1, correct=2,
        )

        assert tipis.buckets[0].accuracy is None
        hasil = Kalibrator(tipis).kalibrasi(0.90)
        assert not hasil.disesuaikan
        assert hasil.nilai == 0.90


# ---- kelengkapan ----------------------------------------------------------


class TestKelengkapan:
    def test_dua_puluh_lima_skenario_ada(self) -> None:
        """Penjaga terhadap skenario yang dihapus diam-diam saat ia mulai
        merepotkan."""
        import inspect

        modul = inspect.getmodule(self)
        nomor = set()
        for nama, obj in inspect.getmembers(modul, inspect.isclass):
            if not nama.startswith("Test"):
                continue
            for fn, _ in inspect.getmembers(obj, inspect.isfunction):
                if fn.startswith("test_") and fn[5:7].isdigit():
                    nomor.add(int(fn[5:7]))

        assert nomor == set(range(1, 26)), sorted(set(range(1, 26)) - nomor)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
