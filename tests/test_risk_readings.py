"""Arah tiap faktor risiko, diuji satu per satu (PASAL 13.2, 13.26).

**Kenapa berkas ini ada, dan kenapa isinya membosankan.**

Cacat paling mahal di lapisan ini bukan angka yang meleset - ia **tanda yang
terbalik**. ``BufferScore.score`` tinggi berarti AMAN; ``Faktor`` menuntut
tinggi berarti BERISIKO. Satu pembalikan yang lupa dilakukan akan membuat setup
dengan likuidasi paling dekat mendapat skor risiko paling rendah, dan hasilnya
tetap terlihat masuk akal di setiap baris laporan.

Sama untuk risk/reward: R:R 3.0 itu bagus, dan 3.0 lebih besar dari 1.0. Sebuah
pemetaan yang lupa membaliknya akan menghadiahi setup dengan imbalan terburuk.

Jadi tiap faktor diuji dua kali - keadaan aman dan keadaan berbahaya - dan yang
dipastikan hanya urutannya. Bukan angkanya: angkanya adalah tebakan awal yang
akan dikalibrasi (PASAL 13.29), sementara arahnya tidak boleh salah sekarang.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as N

import pytest

from aruna.risk.futures_readings import readings_from_plan


def _plan(**ganti) -> N:
    """Rencana kosong; tiap test mengisi hanya bagian yang diujinya."""
    dasar = dict(
        buffer=None, stop_detail=None, net_rr=None, leverage=None,
        liquidity=None, slippage=None, funding=None, integrity=None,
    )
    dasar.update(ganti)
    return N(**dasar)


def _liq(*, spread=None, sweep=None, tradeable=True) -> N:
    return N(spread_bps=spread, sweep_cost_pct=sweep, tradeable=tradeable)


class TestArahnyaTidakTerbalik:
    def test_buffer_besar_berarti_risiko_kecil(self) -> None:
        """``BufferScore.score`` tinggi = AMAN, jadi ia harus DIBALIK."""
        aman = readings_from_plan(_plan(buffer=N(score=95)))
        bahaya = readings_from_plan(_plan(buffer=N(score=5)))

        assert aman["liquidation_distance"] < bahaya["liquidation_distance"]

    def test_risk_reward_besar_berarti_risiko_kecil(self) -> None:
        """R:R 3.0 lebih baik dari 1.0, dan 3.0 angkanya lebih besar - pemetaan
        yang lupa membalik akan menghadiahi imbalan terburuk."""
        bagus = readings_from_plan(_plan(net_rr=Decimal("3.0")))
        buruk = readings_from_plan(_plan(net_rr=Decimal("1.0")))

        # Nilainya ditegaskan, bukan hanya urutannya. Versi pertama test ini
        # membandingkan `bagus < buruk` dan tetap hijau ketika arahnya dibalik
        # di sumber - keduanya menjepit ke ujung rentang yang sama, jadi
        # perbandingannya tidak pernah menyentuh pembalikannya.
        assert bagus["risk_reward"] == 0.0, bagus
        assert buruk["risk_reward"] == 100.0, buruk

    def test_leverage_besar_berarti_risiko_besar(self) -> None:
        rendah = readings_from_plan(_plan(leverage=2))
        tinggi = readings_from_plan(_plan(leverage=20))

        assert rendah["leverage"] < tinggi["leverage"]

    def test_spread_lebar_berarti_risiko_besar(self) -> None:
        sempit = readings_from_plan(_plan(liquidity=_liq(spread=1.0)))
        lebar = readings_from_plan(_plan(liquidity=_liq(spread=60.0)))

        assert sempit["spread"] < lebar["spread"]

    def test_sweep_mahal_berarti_likuiditas_berisiko(self) -> None:
        murah = readings_from_plan(_plan(liquidity=_liq(sweep=0.05)))
        mahal = readings_from_plan(_plan(liquidity=_liq(sweep=1.0)))

        assert murah["liquidity"] < mahal["liquidity"]

    def test_funding_ekstrem_berarti_risiko_besar(self) -> None:
        wajar = readings_from_plan(_plan(funding=N(projected_cost_pct=0.01)))
        ekstrem = readings_from_plan(_plan(funding=N(projected_cost_pct=0.6)))

        assert wajar["funding"] < ekstrem["funding"]

    def test_funding_negatif_ekstrem_juga_berisiko(self) -> None:
        """Funding sangat negatif sama ekstremnya dengan sangat positif;
        yang membedakan hanya siapa yang membayar."""
        wajar = readings_from_plan(_plan(funding=N(projected_cost_pct=0.01)))
        ekstrem = readings_from_plan(_plan(funding=N(projected_cost_pct=-0.6)))

        assert wajar["funding"] < ekstrem["funding"]

    def test_stop_dari_volatilitas_saja_lebih_berisiko(self) -> None:
        """PASAL 13.4: jangan menentukan SL hanya dari persentase tetap tanpa
        melihat struktur pasar."""
        berstruktur = readings_from_plan(
            _plan(stop_detail=N(from_volatility_only=False))
        )
        tanpa = readings_from_plan(
            _plan(stop_detail=N(from_volatility_only=True))
        )

        assert berstruktur["stop_quality"] < tanpa["stop_quality"]

    def test_integritas_buruk_lebih_berisiko(self) -> None:
        ok = readings_from_plan(_plan(integrity=N(verdict=N(value="OK"))))
        rusak = readings_from_plan(
            _plan(integrity=N(verdict=N(value="BLOCKED")))
        )

        assert ok["data_quality"] < rusak["data_quality"]

    def test_tidak_bisa_diperdagangkan_itu_pengukuran(self) -> None:
        """Bukan ketiadaan data - ia fakta yang keras tentang likuiditasnya."""
        hasil = readings_from_plan(
            _plan(liquidity=_liq(tradeable=False))
        )
        assert hasil["liquidity"] == 100.0


class TestYangTidakAdaTidakDikarang:
    """PASAL 13.26."""

    def test_rencana_kosong_tidak_menghasilkan_pembacaan(self) -> None:
        assert readings_from_plan(_plan()) == {}

    def test_faktor_hilang_tidak_muncul_sebagai_nol(self) -> None:
        """Kunci yang tidak ada berbeda dari kunci bernilai nol: yang pertama
        dilaporkan 'tidak terukur', yang kedua 'tidak berisiko'."""
        hasil = readings_from_plan(_plan(leverage=5))

        assert "leverage" in hasil
        assert "liquidation_distance" not in hasil
        assert "funding" not in hasil

    def test_field_yang_ada_tapi_kosong_tetap_dilewati(self) -> None:
        hasil = readings_from_plan(_plan(buffer=N(score=None)))
        assert "liquidation_distance" not in hasil

    def test_nilai_tak_terbaca_tidak_meledak(self) -> None:
        hasil = readings_from_plan(_plan(net_rr="bukan angka"))
        assert "risk_reward" not in hasil


class TestSelaluDalamRentang:
    @pytest.mark.parametrize(
        "plan",
        [
            _plan(buffer=N(score=1000)),
            _plan(net_rr=Decimal("-5")),
            _plan(leverage=125),
            _plan(liquidity=_liq(spread=99999.0)),
            _plan(funding=N(projected_cost_pct=-99.0)),
        ],
    )
    def test_nilai_ekstrem_tetap_nol_sampai_seratus(self, plan) -> None:
        """Nilai di luar 0-100 tidak punya kategori, dan `assess` menjepitnya
        juga - tapi menjepit dua kali lebih murah daripada satu kali yang
        terlewat."""
        for kunci, nilai in readings_from_plan(plan).items():
            assert nilai is None or 0.0 <= nilai <= 100.0, (kunci, nilai)


class TestBersamaPenilaiannya:
    def test_rencana_lengkap_menghasilkan_skor(self) -> None:
        """Sembilan faktor yang bisa dipetakan berbobot 17 dari 27,5 - 62%,
        tepat di atas ambang cakupan. Kalau seseorang menghapus satu pemetaan,
        skornya berhenti tersedia sama sekali, dan test ini yang merah."""
        from aruna.risk import assess

        lengkap = _plan(
            buffer=N(score=80),
            stop_detail=N(from_volatility_only=False),
            net_rr=Decimal("2.5"),
            leverage=5,
            liquidity=_liq(spread=3.0, sweep=0.1),
            slippage=N(breaks_at=None),
            funding=N(projected_cost_pct=0.02),
            integrity=N(verdict=N(value="OK")),
        )
        hasil = assess(readings_from_plan(lengkap))

        assert hasil.usable, hasil.line()
        assert hasil.coverage >= 0.60

    def test_rencana_yang_ditolak_tidak_dipaksa_dinilai(self) -> None:
        """Rencana WAIT atau REFUSED tidak pernah menghitung likuidasi, stop,
        atau R:R - terukur pada rencana sungguhan: cakupannya 5-20%. Menilai
        risikonya berarti menilai setup yang tidak ada."""
        from aruna.risk import assess

        hasil = assess(readings_from_plan(_plan(leverage=5)))
        assert not hasil.usable
