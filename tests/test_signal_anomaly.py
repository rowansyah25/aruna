"""Deteksi kondisi pasar abnormal (PASAL 11.8).

Yang diuji: apakah ia menandai hal yang benar, dan apakah ia menahan diri pada
hal yang bukan urusannya. Gerbang anomali yang terlalu peka membuat ARUNA diam
selamanya sambil terlihat bekerja - kegagalan yang tidak berbunyi.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from aruna.scanner.events import ScanThresholds
from aruna.signals.anomaly import (
    GAP_ANOMALY,
    RANGE_ANOMALY,
    SPREAD_ANOMALY_BPS,
    VOLUME_ANOMALY,
    AnomalyKind,
    detect,
)


def _bar(volume=100.0, open_=100.0, high=101.0, low=99.0, close=100.0):
    return NS(volume=volume, open=open_, high=high, low=low, close=close)


def _state(spread_bps=2.0, data_quality="OK"):
    return NS(spread_bps=spread_bps, data_quality=data_quality)


def _normal_bars(n=10):
    return [_bar() for _ in range(n)]


class TestAmbangLebihTinggiDariPemindai:
    """Pemindai bertanya "layak dilihat"; ini bertanya "tidak bisa dipercaya".
    Kalau ambangnya sama, setiap peristiwa yang layak dilihat sekaligus jadi
    alasan untuk tidak melihatnya."""

    def test_volume(self) -> None:
        assert ScanThresholds().volume_spike < VOLUME_ANOMALY

    def test_rentang(self) -> None:
        assert ScanThresholds().volatility_spike < RANGE_ANOMALY

    def test_selisihnya_bermakna(self) -> None:
        """Bukan sekadar lebih besar - cukup jauh sehingga pasar yang sedang
        bekerja keras tidak otomatis dianggap rusak."""
        assert ScanThresholds().volume_spike * 2 <= VOLUME_ANOMALY


class TestMendeteksiYangBenar:
    def test_pasar_normal_bersih(self) -> None:
        laporan = detect(bars=_normal_bars(), state=_state(), atr=2.0)
        assert laporan.detected is False

    def test_pergerakan_besar_bukan_anomali(self) -> None:
        """Pergerakan besar adalah pasar yang sedang bekerja. Yang dicari
        adalah tanda datanya tidak lagi menggambarkan pasar yang sama."""
        bars = [*_normal_bars(9), _bar(volume=400.0)]  # 4x - besar, bukan rusak
        assert detect(bars=bars, state=_state(), atr=2.0).detected is False

    def test_volume_lima_belas_kali_itu_anomali(self) -> None:
        """Biasanya berarti listing, peretasan, atau penghentian perdagangan -
        dan setiap indikator dari garis dasar sebelumnya kehilangan artinya."""
        bars = [*_normal_bars(9), _bar(volume=1500.0)]
        laporan = detect(bars=bars, state=_state(), atr=2.0)

        assert laporan.detected is True
        assert laporan.worst.kind is AnomalyKind.VOLUME_SPIKE

    def test_rentang_bar_jauh_di_atas_atr(self) -> None:
        bars = [*_normal_bars(9), _bar(high=120.0, low=80.0)]
        laporan = detect(bars=bars, state=_state(), atr=2.0)
        assert any(a.kind is AnomalyKind.RANGE_SPIKE for a in laporan.anomalies)

    def test_celah_antar_bar(self) -> None:
        bars = [*_normal_bars(9), _bar(open_=130.0, high=131.0, low=129.0)]
        laporan = detect(bars=bars, state=_state(), atr=2.0)
        assert any(a.kind is AnomalyKind.PRICE_GAP for a in laporan.anomalies)

    def test_spread_melebar_ekstrem(self) -> None:
        laporan = detect(
            bars=_normal_bars(), state=_state(spread_bps=80.0), atr=2.0
        )
        assert any(a.kind is AnomalyKind.SPREAD_BLOWOUT for a in laporan.anomalies)

    def test_kualitas_data_buruk_dicatat(self) -> None:
        laporan = detect(
            bars=_normal_bars(), state=_state(data_quality="STALE"), atr=2.0
        )
        assert any(a.kind is AnomalyKind.DATA_QUALITY for a in laporan.anomalies)


class TestGarisDasarTidakMengangkatDirinya:
    def test_bar_terakhir_dikeluarkan_dari_garis_dasar(self) -> None:
        """Memasukkannya membuat lonjakan mengangkat garis dasarnya sendiri,
        dan lonjakan terbesar paling banyak menyamarkan dirinya."""
        bars = [*(_bar(volume=100.0) for _ in range(3)), _bar(volume=1200.0)]
        laporan = detect(bars=bars, state=_state(), atr=2.0)

        lonjakan = next(
            a for a in laporan.anomalies if a.kind is AnomalyKind.VOLUME_SPIKE
        )
        # 1200/100 = 12x. Kalau bar terakhir ikut dirata-rata: 1200/375 = 3,2x
        # dan tidak akan terdeteksi sama sekali.
        assert lonjakan.measured == pytest.approx(12.0)


class TestTakTerukurBukanAnomali:
    """Berbeda dari PASAL 11.7 dengan sengaja: yang itu bertanya "buktikan
    datanya segar", yang ini bertanya "apakah kami mendeteksi sesuatu"."""

    def test_tanpa_bar_tidak_menuduh(self) -> None:
        laporan = detect(bars=(), state=_state(), atr=2.0)
        assert laporan.detected is False
        assert "volume" in laporan.unchecked

    def test_tanpa_atr_tidak_menuduh(self) -> None:
        laporan = detect(bars=_normal_bars(), state=_state(), atr=None)
        assert laporan.detected is False
        assert "range" in laporan.unchecked

    def test_tanpa_spread_tidak_menuduh(self) -> None:
        """Pasar spot yang tidak menyediakan kedalaman buku tidak boleh
        dianggap anomali selamanya."""
        laporan = detect(
            bars=_normal_bars(), state=_state(spread_bps=None), atr=2.0
        )
        assert laporan.detected is False
        assert "spread" in laporan.unchecked

    def test_tanpa_state_sama_sekali(self) -> None:
        laporan = detect(bars=_normal_bars(), state=None, atr=2.0)
        assert laporan.detected is False
        assert "spread" in laporan.unchecked

    def test_yang_tak_diperiksa_tetap_dicatat(self) -> None:
        """Bedanya "diperiksa dan bersih" dari "tidak diperiksa" harus
        terbaca, kalau tidak keduanya tercetak sebagai lolos."""
        laporan = detect(bars=(), state=None, atr=None)
        assert laporan.unchecked
        assert "tidak ada anomali" in laporan.summary()


class TestKlaimBisaDibantah:
    def test_angka_dan_ambang_disimpan_berpasangan(self) -> None:
        """"Volume 14,2x garis dasar, ambang 10,0x" bisa diperiksa;
        "anomali terdeteksi" tidak."""
        bars = [*_normal_bars(9), _bar(volume=1500.0)]
        a = detect(bars=bars, state=_state(), atr=2.0).anomalies[0]

        assert a.measured > 0
        assert a.threshold == VOLUME_ANOMALY
        assert a.severity == pytest.approx(a.measured / a.threshold)

    def test_ringkasan_memuat_angkanya(self) -> None:
        bars = [*_normal_bars(9), _bar(volume=1500.0)]
        ringkas = detect(bars=bars, state=_state(), atr=2.0).summary()
        assert "VOLUME_SPIKE" in ringkas
        assert "ambang" in ringkas

    def test_terparah_yang_dilaporkan(self) -> None:
        bars = [*_normal_bars(9), _bar(volume=5000.0, high=200.0, low=50.0)]
        laporan = detect(bars=bars, state=_state(), atr=2.0)
        assert laporan.worst is not None
        assert laporan.worst.severity == max(
            a.severity for a in laporan.anomalies
        )


class TestTerpasangDiJalurHidup:
    """Diuji lewat perilaku. Versi pertama membaca `context.bars` - atribut
    yang tidak pernah ada - dan tiga dari lima pemeriksaan menjadi kode mati
    yang selalu melaporkan "tidak bisa dijalankan"."""

    async def _quality(self, bars):
        from datetime import UTC, datetime

        from aruna.core.enums import Decision, Horizon
        from aruna.signals.service import SignalService

        class _Data:
            async def candles(self, asset_id, interval, *, limit):
                return bars

        svc = object.__new__(SignalService)
        svc._market_data = _Data()

        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        context = NS(
            state=_state(), as_of=now, symbol="BTC/USDT", interval=Horizon.H1,
            structure=NS(confirmed_swings=6, reliable=True),
            regime=NS(regime="X", confidence=0.8,
                      evidence_used=4, evidence_available=5),
            value=lambda name: 2.0 if name == "atr" else None,
            recent_news=lambda hours=24: (),
        )
        verdict = NS(opinions=(), decision=Decision.BUY)
        signal = NS(
            entry_price=100, reference_price=100, target_price=110,
            regime="X", direction=Decision.BUY,
        )
        return await svc._score_quality(
            NS(id=1, symbol="BTC/USDT"), context, verdict, signal, Horizon.H1
        )

    async def test_bar_sungguhan_terbaca(self) -> None:
        skor = await self._quality([
            {"volume": 100.0, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0}
            for _ in range(10)
        ])
        anomali = next(f for f in skor.factors if f.name == "anomaly")
        assert anomali.score == 1.0
        assert "bersih" in anomali.detail

    async def test_baris_database_berbentuk_dict(self) -> None:
        """Repository mengembalikan dict; analisis mengembalikan dataclass.
        Kalau detektornya hanya menerima satu bentuk, ia mati diam-diam."""
        bars = [
            {"volume": 100.0, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0}
            for _ in range(9)
        ]
        bars.append({"volume": 1500.0, "open": 100.0, "high": 101.0,
                     "low": 99.0, "close": 100.0})
        skor = await self._quality(bars)

        anomali = next(f for f in skor.factors if f.name == "anomaly")
        assert anomali.score == 0.0
        assert "VOLUME_SPIKE" in anomali.detail

    async def test_gagal_baca_bar_tidak_meledak(self) -> None:
        from aruna.core.enums import Decision, Horizon
        from aruna.signals.service import SignalService

        class _Meledak:
            async def candles(self, *a, **kw):
                raise RuntimeError("database berkedip")

        svc = object.__new__(SignalService)
        svc._market_data = _Meledak()

        from datetime import UTC, datetime

        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        context = NS(
            state=_state(), as_of=now, symbol="BTC/USDT", interval=Horizon.H1,
            structure=None, regime=None,
            value=lambda name: None, recent_news=lambda hours=24: (),
        )
        skor = await svc._score_quality(
            NS(id=1, symbol="BTC/USDT"), context,
            NS(opinions=(), decision=Decision.BUY),
            NS(entry_price=None, reference_price=100, target_price=None,
               regime=None, direction=Decision.BUY),
            Horizon.H1,
        )
        anomali = next(f for f in skor.factors if f.name == "anomaly")
        # Tidak meledak, dan tidak berpura-pura bersih.
        assert anomali.score == 1.0
        assert "tidak bisa dijalankan" in anomali.detail


def test_ambang_masuk_akal() -> None:
    assert VOLUME_ANOMALY > 1 and RANGE_ANOMALY > 1 and GAP_ANOMALY > 1
    assert SPREAD_ANOMALY_BPS > 0
