"""Lima dimensi yang PASAL 15.5 minta dan tidak pernah tersimpan.

Terukur 2026-08-21, dan ini akar masalah Phase 15: evaluasi retrospektif atas
1.671 keputusan melaporkan selisih **+3 poin** antara SUPPORTIVE dan CONTRARY -
derau. Sidik jarinya hanya punya delapan dimensi, dan tujuh yang pasalnya sebut
(volatility, volume, momentum, trend, open interest, funding, structure) semua
``UNKNOWN``.

Lima di antaranya ternyata **bisa dihitung ulang dari candle yang sudah
tersimpan**: ``realised_volatility``, ``momentum``, ``volume_anomaly``, dan
``analyse_structure`` semuanya berjalan atas ``CandleSeries``, dan candle-nya
ada sejak Juli. Jadi bukan hanya ingatan baru yang terisi - **korpus 8.548 yang
sudah ada pun ikut**.

Dua sisanya - open interest dan funding - data venue perpetual yang tidak
pernah disimpan per keputusan, dan tetap UNKNOWN.

**Ambangnya diturunkan dari tercile korpus**, bukan dipilih: n=900 jendela di
dua puluh aset kripto 15m. Ambang yang dipilih penulis kode adalah ambang yang
dipilih demi tampilannya.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.analysis.series import CandleSeries
from aruna.core.enums import Horizon, Market
from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.teknikal import (
    MOMENTUM_P33,
    MOMENTUM_P67,
    VOLATILITAS_P33,
    VOLATILITAS_P67,
    VOLUME_P33,
    VOLUME_P67,
    band_momentum,
    band_struktur,
    band_volatilitas,
    band_volume,
    dimensi_teknikal,
)

AWAL = datetime(2026, 8, 1, tzinfo=UTC)


def _seri(closes: list[float], volumes: list[float] | None = None) -> CandleSeries:
    n = len(closes)
    vol = volumes or [100.0] * n
    return CandleSeries(
        market=Market.CRYPTO, symbol="BTC/USDT", interval=Horizon.M15,
        opens=tuple(closes), highs=tuple(c * 1.001 for c in closes),
        lows=tuple(c * 0.999 for c in closes), closes=tuple(closes),
        volumes=tuple(vol),
        times=tuple(AWAL + timedelta(minutes=15 * i) for i in range(n)),
        close_times=tuple(
            AWAL + timedelta(minutes=15 * (i + 1)) for i in range(n)
        ),
    )


class TestBandDariTercile:
    def test_volatilitas_dibagi_di_tercile_terukur(self) -> None:
        assert band_volatilitas(VOLATILITAS_P33 - 0.01) == "LOW"
        assert band_volatilitas((VOLATILITAS_P33 + VOLATILITAS_P67) / 2) == "MEDIUM"
        assert band_volatilitas(VOLATILITAS_P67 + 0.01) == "HIGH"

    def test_momentum_dibagi_di_tercile_terukur(self) -> None:
        assert band_momentum(MOMENTUM_P33 - 0.1) == "NEGATIVE"
        assert band_momentum((MOMENTUM_P33 + MOMENTUM_P67) / 2) == "FLAT"
        assert band_momentum(MOMENTUM_P67 + 0.1) == "POSITIVE"

    def test_volume_dibagi_di_tercile_terukur(self) -> None:
        assert band_volume(VOLUME_P33 - 0.1) == "LOW"
        assert band_volume((VOLUME_P33 + VOLUME_P67) / 2) == "NORMAL"
        assert band_volume(VOLUME_P67 + 0.1) == "HIGH"

    def test_yang_tidak_terbaca_unknown(self) -> None:
        """Nol bukan ketiadaan - tapi None iya, dan itu yang keluar dari
        indikator yang barnya kurang."""
        assert band_volatilitas(None) == UNKNOWN
        assert band_momentum(None) == UNKNOWN
        assert band_volume(None) == UNKNOWN
        assert band_struktur(None) == UNKNOWN

    def test_nol_bukan_unknown(self) -> None:
        assert band_momentum(0.0) != UNKNOWN

    def test_ambangnya_dari_pengukuran(self) -> None:
        """Angka-angka ini disalin dari sebaran terukur, bukan dipilih. Test
        ini menahan agar ia tidak diam-diam "dirapikan" jadi angka bulat yang
        enak dilihat dan tidak membagi apa pun."""
        assert pytest.approx(0.161, abs=0.001) == VOLATILITAS_P33
        assert pytest.approx(0.300, abs=0.001) == VOLATILITAS_P67
        assert pytest.approx(-0.105, abs=0.001) == MOMENTUM_P33
        assert pytest.approx(1.046, abs=0.001) == VOLUME_P67


class TestDuaDimensiVenue:
    """Funding dan open interest - dua yang tersisa, dan keduanya ADA.

    Terukur 2026-08-21: ``futures_plans.funding_cost_pct`` terisi pada 192
    baris (rentang -0,204 sampai +0,348), dan ``BinanceFuturesProvider`` punya
    ``open_interest()`` **dan** ``open_interest_history()`` - keduanya
    terimplementasi penuh, masuk allowlist, dan **tidak pernah disimpan ke mana
    pun**. Kelas cacat yang sama dengan backtest yang dihitung lalu dibuang.
    """

    def test_funding_positif_negatif_dan_nol(self) -> None:
        from aruna.memory.teknikal import band_funding

        assert band_funding(0.30) == "POSITIVE"
        assert band_funding(-0.20) == "NEGATIVE"

    def test_funding_nol_adalah_bacaan_bukan_ketiadaan(self) -> None:
        """129 dari 192 baris berisi tepat nol. Nol berarti biaya funding
        terhitung dan hasilnya nol - bukan berarti tidak terbaca."""
        from aruna.memory.teknikal import band_funding

        assert band_funding(0.0) == "FLAT"
        assert band_funding(None) == UNKNOWN

    def test_open_interest_dibaca_dari_arah_perubahannya(self) -> None:
        """PASAL 15.5 mencontohkannya sendiri: "OI: Increasing". Nilai
        mutlaknya tidak sebanding antar aset - open interest BTC dan DOGE
        berbeda ribuan kali - jadi yang berarti adalah arahnya."""
        from aruna.memory.teknikal import band_open_interest

        assert band_open_interest(110.0, 100.0) == "RISING"
        assert band_open_interest(90.0, 100.0) == "FALLING"
        assert band_open_interest(100.2, 100.0) == "FLAT"

    def test_open_interest_tanpa_pembanding_unknown(self) -> None:
        """Satu bacaan tidak punya arah. Menyebutnya FLAT akan mencampur
        "tidak berubah" dengan "tidak ada yang tahu"."""
        from aruna.memory.teknikal import band_open_interest

        assert band_open_interest(100.0, None) == UNKNOWN
        assert band_open_interest(None, 100.0) == UNKNOWN

    def test_pembanding_nol_tidak_meledak(self) -> None:
        from aruna.memory.teknikal import band_open_interest

        assert band_open_interest(100.0, 0.0) == UNKNOWN


class TestStruktur:
    def test_naik_turun_dan_datar_dikenali(self) -> None:
        assert band_struktur("UPTREND") == "UPTREND"
        assert band_struktur("DOWNTREND") == "DOWNTREND"
        assert band_struktur("RANGE") == "RANGE"

    def test_yang_tidak_dikenali_unknown(self) -> None:
        """Nama pola baru dari lapisan struktur adalah kegagalan pembacaan,
        bukan alasan menebak."""
        assert band_struktur("SESUATU_BARU") == UNKNOWN


class TestDariSeriSungguhan:
    def test_seri_naik_menghasilkan_momentum_positif(self) -> None:
        seri = _seri([100.0 + i * 0.5 for i in range(60)])

        dim = dimensi_teknikal(seri)

        assert dim[Dimensi.MOMENTUM] == "POSITIVE"

    def test_seri_turun_menghasilkan_momentum_negatif(self) -> None:
        seri = _seri([100.0 - i * 0.5 for i in range(60)])

        dim = dimensi_teknikal(seri)

        assert dim[Dimensi.MOMENTUM] == "NEGATIVE"

    def test_seri_datar_volatilitasnya_rendah(self) -> None:
        seri = _seri([100.0] * 60)

        dim = dimensi_teknikal(seri)

        assert dim[Dimensi.VOLATILITY] == "LOW"

    def test_seri_terlalu_pendek_semuanya_unknown(self) -> None:
        """Indikator yang barnya kurang memulangkan `insufficient` - dan
        memaksakan angka darinya adalah mengarang (§13.26)."""
        dim = dimensi_teknikal(_seri([100.0, 101.0, 102.0]))

        assert dim[Dimensi.VOLATILITY] == UNKNOWN
        assert dim[Dimensi.MOMENTUM] == UNKNOWN

    def test_hanya_lima_dimensi_yang_diisi(self) -> None:
        """Open interest dan funding adalah data venue perpetual yang tidak
        pernah tersimpan per keputusan. Mengisinya dari candle spot akan
        mengarang dua dimensi sekaligus."""
        dim = dimensi_teknikal(_seri([100.0 + i * 0.5 for i in range(60)]))

        assert set(dim) == {
            Dimensi.VOLATILITY, Dimensi.MOMENTUM, Dimensi.VOLUME,
            Dimensi.TREND, Dimensi.STRUCTURE,
        }

    def test_seri_kosong_tidak_meledak(self) -> None:
        dim = dimensi_teknikal(None)

        assert all(v == UNKNOWN for v in dim.values())
