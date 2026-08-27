"""M15/H1/H4 dirakit dari M5 yang sama - satu sumber, nol kredit tambahan.

Kenapa ini penting melampaui penghematan kredit: empat timeframe yang ditarik
terpisah bisa tidak sinkron. Bar H1 yang tiba sedetik lebih lambat dapat memuat
pergerakan yang belum ada di M5 saat keputusan diambil - kebocoran masa depan
yang tidak terlihat seperti kebocoran karena setiap bar-nya sah.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.timeframes import TIMEFRAME_TURUNAN, TumpukanTimeframe, rakit_tumpukan

AWAL = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)


def _m5(jumlah: int, *, mulai: datetime = AWAL) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = mulai + timedelta(minutes=5 * i)
        harga = Decimal(1000 + i)
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 1,
                low=harga - 1,
                close=harga,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


class TestRakitTumpukan:
    def test_semua_turunan_terisi_dari_m5_saja(self) -> None:
        """48 bar M5 = 4 jam penuh = satu bar H4."""
        tumpukan = rakit_tumpukan(_m5(48))
        assert len(tumpukan.m15) == 16
        assert len(tumpukan.h1) == 4
        assert len(tumpukan.h4) == 1
        assert tumpukan.lengkap is True

    def test_bar_h4_merangkum_seluruh_jendela(self) -> None:
        bars = _m5(48)
        h4 = rakit_tumpukan(bars).h4[0]
        assert h4.open == bars[0].open
        assert h4.close == bars[-1].close
        assert h4.high == max(c.high for c in bars)
        assert h4.low == min(c.low for c in bars)

    def test_ember_tak_lengkap_dibuang_bukan_dirata_rata(self) -> None:
        """Satu bar H4 butuh 48 bar M5; 47 tidak boleh jadi H4 palsu."""
        tumpukan = rakit_tumpukan(_m5(47))
        assert tumpukan.h4 == []
        assert tumpukan.lengkap is False
        assert Horizon.H4 in tumpukan.kurang()

    def test_bar_terbuka_tidak_ikut(self) -> None:
        """Bar yang belum tutup masih berubah setelah dibaca - itu kebocoran."""
        bars = _m5(48)
        bars[-1] = replace(bars[-1], is_closed=False)
        tumpukan = rakit_tumpukan(bars)
        assert tumpukan.h4 == [], "H4 tidak boleh dirakit dari bar yang belum tutup"

    def test_m5_diteruskan_apa_adanya(self) -> None:
        bars = _m5(12)
        assert rakit_tumpukan(bars).m5 == bars

    def test_kosong_menghasilkan_kosong_bukan_galat(self) -> None:
        tumpukan = rakit_tumpukan([])
        assert tumpukan.m5 == []
        assert tumpukan.lengkap is False
        assert set(tumpukan.kurang()) == set(TIMEFRAME_TURUNAN)

    def test_turunan_tidak_pernah_lebih_baru_dari_m5(self) -> None:
        """Inti keamanan timestamp: tak ada timeframe yang boleh tahu lebih
        banyak daripada M5 yang melahirkannya."""
        bars = _m5(48)
        tumpukan = rakit_tumpukan(bars)
        batas = bars[-1].close_time
        for turunan in (tumpukan.m15, tumpukan.h1, tumpukan.h4):
            for candle in turunan:
                assert candle.close_time <= batas

    def test_ember_diikat_ke_epoch_bukan_ke_bar_pertama(self) -> None:
        """Jendela yang berbeda harus menghasilkan batas ember yang sama.

        Kalau ember diikat ke bar pertama dalam daftar, menarik 40 bar dan
        menarik 48 bar akan memberi bar H1 yang isinya berbeda untuk jam yang
        sama - dan dua keputusan pada jam yang sama jadi tak bisa dibandingkan.
        """
        panjang = rakit_tumpukan(_m5(48)).h1
        pendek = rakit_tumpukan(_m5(24)).h1
        assert [c.open_time for c in pendek] == [c.open_time for c in panjang[:2]]


class TestTumpukanKosong:
    def test_default_kosong_bisa_dibangun(self) -> None:
        assert TumpukanTimeframe().lengkap is False

    def test_kurang_menyebut_yang_belum_cukup_saja(self) -> None:
        """Di awal jam, M15 sudah ada sementara H4 belum. Itu keadaan WAJAR,
        dan pemanggil butuh tahu mana yang mana - bukan sekadar 'belum lengkap'."""
        tumpukan = rakit_tumpukan(_m5(3))
        assert Horizon.M15 not in tumpukan.kurang()
        assert Horizon.H1 in tumpukan.kurang()
        assert Horizon.H4 in tumpukan.kurang()
