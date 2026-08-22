"""Prediksi hanya dikunci pada horizon yang pasarnya memang punya.

Cacat yang ditemukan dari database, bukan dari kode: **88 prediksi IDX
terkunci, tidak satu pun pernah diskor**, yang tertua tiga hari.

======  =======  ========  ==========
pasar   horizon  LOCKED    RESOLVED
======  =======  ========  ==========
CRYPTO  15m      5         422
CRYPTO  1h       10        174
IDX     15m      22        0
IDX     1h       33        0
IDX     1d       33        0
======  =======  ========  ==========

Sebabnya perkalian silang dua daftar yang harus cocok dan tidak pernah
dicocokkan: ``lock_market_set`` dikali ``lock_horizon_set``, apa adanya.
``horizons_for_market(IDX)`` dimulai dari 1d - tidak ada 15m, tidak ada 1h -
tapi keduanya tetap dikunci.

Yang membuat kegagalannya diam adalah jaraknya dari akibatnya:
``refresh_intervals`` menurunkan interval yang dijaga dari daftar horizon yang
sama, jadi IDX menjaga (15m, 1h, 1d) dan tidak pernah menjaga 1m - sementara
prediksi IDX 15m hanya bisa disampel dari 1m. Tidak ada error, tidak ada
kegagalan, hanya catatan yang tidak pernah bertambah.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aruna.core.config import UpkeepSettings
from aruna.core.enums import Horizon, Market, horizons_for_market
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

SAAT = datetime(2026, 8, 18, 4, 0, tzinfo=UTC)


def _loop(**overrides) -> UpkeepLoop:
    return UpkeepLoop(
        refresher=None,
        resolver=None,
        locker=None,
        settings=UpkeepSettings(_env_file=None, **overrides),
        stats=UpkeepStats(started_at=SAAT),
    )


class TestHorizonYangTidakDipunyaiPasar:
    def test_idx_tidak_dikunci_pada_15m(self) -> None:
        """Ini pasangan yang menghasilkan 22 prediksi tak terskor."""
        loop = _loop(
            lock_markets="IDX", lock_horizons="15m,1d",
        )
        due = loop._horizons_due(SAAT)

        assert (Market.IDX, Horizon.M15) not in due
        assert (Market.IDX, Horizon.D1) in due

    def test_idx_tidak_dikunci_pada_1h(self) -> None:
        loop = _loop(lock_markets="IDX", lock_horizons="1h")
        assert loop._horizons_due(SAAT) == []

    def test_crypto_tetap_dikunci_pada_15m(self) -> None:
        """Peredamnya harus berhenti tepat di batas pasar. Kalau crypto ikut
        diam, ini bukan memperbaiki melainkan mematikan."""
        loop = _loop(lock_markets="CRYPTO", lock_horizons="15m,1h")
        due = loop._horizons_due(SAAT)

        assert (Market.CRYPTO, Horizon.M15) in due
        assert (Market.CRYPTO, Horizon.H1) in due

    def test_satu_horizon_bisa_sah_di_satu_pasar_dan_tidak_di_pasar_lain(
        self,
    ) -> None:
        """Inti cacatnya: daftarnya dipakai apa adanya untuk kedua pasar."""
        loop = _loop(lock_markets="CRYPTO,IDX", lock_horizons="15m")
        due = loop._horizons_due(SAAT)

        assert (Market.CRYPTO, Horizon.M15) in due
        assert (Market.IDX, Horizon.M15) not in due


class TestDikatakanSekali:
    def _catat(self, monkeypatch) -> list[dict]:
        from aruna.upkeep import loop as modul

        keluar: list[dict] = []
        monkeypatch.setattr(
            modul.log, "warning", lambda event, **kw: keluar.append({"e": event, **kw})
        )
        return keluar

    def test_dikatakan(self, monkeypatch) -> None:
        """Operator yang menulis 15m untuk IDX sedang meminta sesuatu yang tidak
        akan pernah terjadi, dan harus tahu dari satu baris log."""
        keluar = self._catat(monkeypatch)
        _loop(lock_markets="IDX", lock_horizons="15m")._horizons_due(SAAT)

        assert [k for k in keluar if k["e"] == "upkeep.horizon_not_offered"]

    def test_tidak_diulang_tiap_tick(self, monkeypatch) -> None:
        """Loop berdetak tiap lima belas detik. Peringatan yang berulang di situ
        menenggelamkan dirinya sendiri - kegagalan yang sama dengan banjir
        DUPLICATE."""
        keluar = self._catat(monkeypatch)
        loop = _loop(lock_markets="IDX", lock_horizons="15m")
        for _ in range(5):
            loop._horizons_due(SAAT)

        assert len([k for k in keluar if k["e"] == "upkeep.horizon_not_offered"]) == 1

    def test_pasangan_lain_dikatakan_sendiri(self, monkeypatch) -> None:
        keluar = self._catat(monkeypatch)
        loop = _loop(lock_markets="IDX", lock_horizons="15m,1h")
        loop._horizons_due(SAAT)
        loop._horizons_due(SAAT)

        horizons = {
            k["horizon"] for k in keluar if k["e"] == "upkeep.horizon_not_offered"
        }
        assert horizons == {"15m", "1h"}


class TestDuaDaftarItuHarusCocok:
    def test_horizon_idx_dimulai_dari_1d(self) -> None:
        """Bukan asumsi - dibaca dari sumbernya, supaya test ini ikut berubah
        kalau daftar horizon IDX diperluas."""
        idx = horizons_for_market(Market.IDX)

        assert Horizon.M15 not in idx
        assert Horizon.H1 not in idx
        assert Horizon.D1 in idx

    def test_interval_sampling_15m_hanya_1m(self) -> None:
        """Yang membuat prediksi IDX 15m mustahil diskor: satu-satunya seri yang
        cukup halus untuknya adalah 1m, dan 1m tidak ikut dijaga untuk IDX."""
        from aruna.signals.outcome import sampling_intervals
        from aruna.upkeep.candles import refresh_intervals

        halus = [i for i in sampling_intervals(Horizon.M15) if i is not Horizon.M15]
        assert halus == [Horizon.M1]

        dijaga = refresh_intervals(Market.IDX, tuple(Horizon))
        assert Horizon.M1 not in dijaga
