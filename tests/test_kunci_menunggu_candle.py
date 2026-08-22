"""Penguncian menunggu candle bar itu benar-benar tiba (SPEC 24, akurasi).

Terukur 2026-08-21 di log produksi:

    18:00:15.663  upkeep.locked      <- kunci menyala
    18:00:32.832  upkeep.refreshed   CRYPTO:15m  <- bar 18:00 tiba 17 detik KEMUDIAN

Kunci dan refresh berada di siklus berbeda, dan pemeriksa-jatuh-temponya tidak
sepakat kapan batas bar lewat. Akibatnya keputusan dibuat di atas bar yang tutup
**satu bar sebelumnya**, padahal bar terbaru sudah ada beberapa detik kemudian.

**Ongkosnya terukur.** Akurasi BUY dibanding garis dasar horizonnya:

======== ================== ================== =========
horizon  bukti bar terbaru  bukti satu bar lalu  jumlah basi
======== ================== ================== =========
15m      +7,2 poin          -4,9 poin          1.476 dari 2.070
1h       +8,9 poin          -4,9 poin          614 dari 1.414
======== ================== ================== =========

Bukan kebocoran: seluruh `as_of` jatuh **tepat** di batas bar (100%), dan yang
segar dikunci 19-45 detik SESUDAH batas itu. Yang basi murni terlambat.

**Konsekuensi yang disengaja:** bar yang candle-nya tidak pernah tiba tidak
menghasilkan prediksi sama sekali. Itu mengurangi jumlah sampel - dan itu lebih
baik daripada prediksi yang terukur berkinerja negatif.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.core.enums import Horizon, Market

NOW = datetime(2026, 8, 21, 18, 0, 15, tzinfo=UTC)


def _loop():
    from aruna.core.config import UpkeepSettings
    from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

    loop = UpkeepLoop.__new__(UpkeepLoop)
    loop._settings = UpkeepSettings()
    loop._stats = UpkeepStats(started_at=NOW)
    loop._locked_bar = {}
    loop._refreshed_bar = {}
    loop._menunggu_candle = {}
    loop._horizon_not_offered = set()
    return loop


def _bar(moment: datetime, horizon: Horizon) -> datetime:
    from aruna.upkeep.candles import bar_start

    return bar_start(moment, horizon, market=Market.CRYPTO)


class TestMenungguCandle:
    def test_tanpa_refresh_bar_ini_tidak_mengunci(self) -> None:
        """Keadaan tepat di log produksi: kunci menyala 18:00:15, candle 15m
        untuk bar itu baru tiba 18:00:32."""
        loop = _loop()
        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(
            NOW - timedelta(minutes=15), Horizon.M15
        )

        assert not loop._bukti_siap(Market.CRYPTO, Horizon.M15, NOW)

    def test_sesudah_refresh_bar_ini_boleh_mengunci(self) -> None:
        loop = _loop()
        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(NOW, Horizon.M15)

        assert loop._bukti_siap(Market.CRYPTO, Horizon.M15, NOW)

    def test_belum_pernah_refresh_tidak_mengunci(self) -> None:
        """Sesudah restart, `_refreshed_bar` kosong. Mengunci di atas apa pun
        yang kebetulan ada di database berarti mengunci di atas bukti yang
        umurnya tidak diketahui."""
        assert not _loop()._bukti_siap(Market.CRYPTO, Horizon.M15, NOW)

    def test_horizon_lain_tidak_saling_memblokir(self) -> None:
        """1h yang belum di-refresh tidak boleh menahan 15m yang sudah."""
        loop = _loop()
        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(NOW, Horizon.M15)

        assert loop._bukti_siap(Market.CRYPTO, Horizon.M15, NOW)
        assert not loop._bukti_siap(Market.CRYPTO, Horizon.H1, NOW)


class TestJatuhTempoMenghormatinya:
    """Gerbangnya ada di `_lock`, bukan di `_horizons_due`.

    `_horizons_due` menjawab pertanyaan kalender - *"bar mana yang sudah
    berganti, dan horizon mana yang sah untuk pasar ini"*. Kesegaran candle
    pertanyaan lain, dan `_lock` sudah punya polanya untuk hal yang harus
    dicoba lagi tick berikutnya.
    """

    def _due(self, loop, moment=NOW):
        return {
            h
            for m, h in loop._horizons_due(moment)
            if loop._siap_atau_ditunda(m, h, moment)
        }

    def test_horizon_tanpa_candle_bar_ini_tidak_jatuh_tempo(self) -> None:
        loop = _loop()
        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(
            NOW - timedelta(minutes=15), Horizon.M15
        )

        assert Horizon.M15 not in self._due(loop)

    def test_horizon_dengan_candle_bar_ini_jatuh_tempo(self) -> None:
        loop = _loop()
        for h in (Horizon.M15, Horizon.H1, Horizon.D1):
            loop._refreshed_bar[(Market.CRYPTO, h)] = _bar(NOW, h)

        assert Horizon.M15 in self._due(loop)

    def test_barnya_tidak_ditandai_saat_ditunda(self) -> None:
        """Pola yang sama dengan penanganan kegagalan yang sudah ada: bar yang
        ditunda harus dicoba lagi tick berikutnya, bukan hilang sampai bar
        berganti."""
        loop = _loop()
        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(
            NOW - timedelta(minutes=15), Horizon.M15
        )

        self._due(loop)
        # Belum dikunci, jadi belum ditandai - dan begitu candle-nya tiba,
        # tick berikutnya menemukannya masih jatuh tempo.
        assert (Market.CRYPTO, Horizon.M15) not in loop._locked_bar

        loop._refreshed_bar[(Market.CRYPTO, Horizon.M15)] = _bar(NOW, Horizon.M15)
        assert Horizon.M15 in self._due(loop)


class TestRefreshMencatatnya:
    def test_refresh_mencatat_bar_yang_diambilnya(self) -> None:
        """Tanpa pencatatan ini gerbangnya tidak pernah terbuka dan ARUNA
        berhenti mengunci sama sekali."""
        import ast
        import inspect
        from textwrap import dedent

        from aruna.upkeep.loop import UpkeepLoop

        pohon = ast.parse(dedent(inspect.getsource(UpkeepLoop.cycle)))
        atribut = {
            n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)
        }

        assert "_refreshed_bar" in atribut

    @pytest.mark.asyncio
    async def test_yang_ditunda_tidak_dicatat_sebagai_siap(self) -> None:
        """`deferred` berarti candle-nya TIDAK diambil. Mencatatnya sebagai
        siap akan membuka gerbang di atas bukti yang tidak pernah tiba."""
        import inspect

        from aruna.upkeep.loop import UpkeepLoop

        sumber = inspect.getsource(UpkeepLoop.cycle)
        awal = sumber.index("_refreshed_bar")
        cuplikan = sumber[max(0, awal - 400):awal]

        assert "result.refreshed" in cuplikan
