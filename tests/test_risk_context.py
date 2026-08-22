"""Faktor risiko dari konteks keputusan (PASAL 13.2, 13.16, 13.31).

Adapter ini mengukur apa yang rencana futures tidak bisa: volatilitas, rezim,
berita, korelasi. Terukur - cakupan naik dari **62% ke 87%** saat keduanya
digabung, dan 62% adalah angka yang tepat di ambang, yang membuat gerbang
risiko sering menjawab "tidak bisa dinilai" alih-alih menilai.

Seperti adapter futures, yang diuji adalah **arahnya** - bukan angkanya.
Angkanya tebakan awal yang akan dikalibrasi (PASAL 13.29); tandanya tidak boleh
salah sekarang.
"""

from __future__ import annotations

from types import SimpleNamespace as N

from aruna.risk import assess
from aruna.risk.context_readings import merge, readings_from_context


def _ctx(*, vol=2.0, regime="TRENDING", yakin=0.8, mutu="OK",
         berita=0, kor=0.3) -> N:
    return N(
        value=lambda n: vol if n == "realised_volatility" else None,
        regime=N(regime=N(value=regime), confidence=yakin),
        state=N(data_quality=mutu),
        recent_news=lambda hours=24: [N(high_impact=True)] * berita,
        correlation=N(max_abs=kor),
    )


class TestArahnyaTidakTerbalik:
    def test_volatilitas_tinggi_lebih_berisiko(self) -> None:
        tenang = readings_from_context(_ctx(vol=1.0))
        liar = readings_from_context(_ctx(vol=9.0))

        assert tenang["volatility"] < liar["volatility"]

    def test_rezim_tidak_terbaca_lebih_berisiko_dari_trending(self) -> None:
        """UNCERTAIN bukan netral - ia berarti indikatornya saling
        bertentangan, dan bertaruh di atas pertentangan lebih berisiko
        daripada bertaruh di atas kesepakatan."""
        tren = readings_from_context(_ctx(regime="TRENDING"))
        ragu = readings_from_context(_ctx(regime="UNCERTAIN"))

        assert tren["market_regime"] < ragu["market_regime"]

    def test_keyakinan_rezim_rendah_menaikkan_risiko(self) -> None:
        """TRENDING dengan keyakinan 0,2 bukan pernyataan sekuat yang dibaca
        0,9; memperlakukannya sama membuang informasi yang sudah dihitung."""
        yakin = readings_from_context(_ctx(regime="TRENDING", yakin=0.9))
        ragu = readings_from_context(_ctx(regime="TRENDING", yakin=0.2))

        assert yakin["market_regime"] < ragu["market_regime"]

    def test_berita_berdampak_menaikkan_risiko(self) -> None:
        sepi = readings_from_context(_ctx(berita=0))
        ramai = readings_from_context(_ctx(berita=3))

        assert sepi["news_risk"] < ramai["news_risk"]

    def test_korelasi_tinggi_lebih_berisiko(self) -> None:
        rendah = readings_from_context(_ctx(kor=0.15))
        tinggi = readings_from_context(_ctx(kor=0.95))

        assert rendah["correlation"] < tinggi["correlation"]

    def test_data_basi_lebih_berisiko(self) -> None:
        segar = readings_from_context(_ctx(mutu="OK"))
        basi = readings_from_context(_ctx(mutu="STALE"))

        assert segar["data_quality"] < basi["data_quality"]

    def test_kualitas_signal_tinggi_berarti_risiko_rendah(self) -> None:
        bagus = readings_from_context(_ctx(), signal_quality=95.0)
        buruk = readings_from_context(_ctx(), signal_quality=20.0)

        assert bagus["signal_quality"] < buruk["signal_quality"]


class TestKualitasHanyaSatuFaktor:
    """PASAL 13.21, dari sisi yang berbeda dengan gerbangnya."""

    def test_kualitas_sempurna_tidak_menghapus_risiko_lain(self) -> None:
        """Kualitas adalah satu dari tujuh belas faktor berbobot 1.0 - bukan
        pengurang yang bisa menutupi sisanya."""
        buruk = _ctx(vol=9.0, regime="UNCERTAIN", yakin=0.2, kor=0.95, berita=3)
        hasil = assess(
            merge(
                readings_from_context(buruk, signal_quality=100.0),
                {f: 90.0 for f in ("liquidation_distance", "stop_quality",
                                   "risk_reward", "leverage", "liquidity")},
            )
        )

        assert hasil.usable
        assert hasil.score is not None and hasil.score > 60, hasil.line()


class TestYangTidakAdaTidakDikarang:
    """PASAL 13.26."""

    def test_konteks_kosong_tidak_menghasilkan_pembacaan(self) -> None:
        assert readings_from_context(N()) == {}

    def test_indikator_yang_tidak_dihitung_dilewati(self) -> None:
        ctx = N(value=lambda n: None)
        assert "volatility" not in readings_from_context(ctx)

    def test_kualitas_tidak_dioper_berarti_tidak_diukur(self) -> None:
        assert "signal_quality" not in readings_from_context(_ctx())

    def test_rezim_asing_dilewati_bukan_ditebak(self) -> None:
        hasil = readings_from_context(_ctx(regime="SESUATU_YANG_BARU"))
        assert "market_regime" not in hasil

    def test_konteks_yang_meledak_tidak_menjatuhkan(self) -> None:
        class _Ledakan:
            def __getattr__(self, nama):
                raise RuntimeError("bentuk tak terduga")

        assert readings_from_context(_Ledakan()) == {}


class TestPenggabungan:
    def test_cakupan_naik_saat_digabung(self) -> None:
        """Inti seluruh adapter ini. 62% tepat di ambang; digabung ia jauh di
        atasnya, dan gerbang risiko berhenti sering menjawab 'tidak bisa
        dinilai'."""
        futures = {
            "liquidation_distance": 10.0, "stop_quality": 25.0,
            "risk_reward": 40.0, "leverage": 30.0, "liquidity": 10.0,
            "spread": 5.0, "slippage": 20.0, "funding": 10.0,
            "data_quality": 10.0,
        }
        sendiri = assess(futures)
        gabung = assess(merge(futures, readings_from_context(_ctx())))

        assert gabung.coverage > sendiri.coverage
        assert gabung.coverage >= 0.80

    def test_yang_pertama_menang(self) -> None:
        """Dua sumber sah untuk mutu data - gerbang integritas dan snapshot.
        Yang dipakai harus yang diputuskan pemanggil, bukan yang kebetulan
        terakhir ditulis."""
        hasil = merge({"data_quality": 10.0}, {"data_quality": 90.0})
        assert hasil["data_quality"] == 10.0

    def test_none_tidak_menutupi_nilai_yang_ada(self) -> None:
        hasil = merge({"volatility": None}, {"volatility": 40.0})
        assert hasil["volatility"] == 40.0
