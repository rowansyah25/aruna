"""Bukti per timeframe, dan jangkar waktunya.

`as_of` diambil dari M5 dan HANYA dari M5. Timeframe besar tersettle lebih
jarang, jadi mengambil yang tertua di antara keempatnya akan melaporkan bukti
lebih tua daripada yang sebenarnya ada - dan gerbang kesegaran yang berdiri di
atasnya akan menolak analisis yang sebetulnya mutakhir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.bukti import rakit_bukti
from aruna.xau.timeframes import rakit_tumpukan

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)


def _m5(jumlah: int) -> list[Candle]:
    """Deret naik lalu turun, supaya indikator arah punya sesuatu untuk dibaca."""
    prov = Provenance(source="twelvedata")
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = AWAL + timedelta(minutes=5 * i)
        harga = Decimal(1000 + (i if i < jumlah // 2 else jumlah - i))
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 2,
                low=harga - 2,
                close=harga + 1,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


class TestRakitBukti:
    def test_m5_selalu_ada_saat_bahannya_cukup(self) -> None:
        bukti = rakit_bukti(rakit_tumpukan(_m5(250)))
        assert bukti is not None
        assert bukti.m5.interval is Horizon.M5

    def test_as_of_diambil_dari_m5(self) -> None:
        """Bukan yang tertua di antara empat timeframe.

        250 bar, bukan 240, dan itu yang membuat test ini berarti: 240 bar M5
        adalah tepat 20 jam - kelipatan bulat H4 - sehingga bar H4 terakhir
        tutup PERSIS bersamaan dengan M5 dan perbandingannya jadi sama besar
        apa pun yang dipilih `as_of`. 250 bar menyisakan 50 menit, jadi M5
        benar-benar lebih maju daripada H4.
        """
        bukti = rakit_bukti(rakit_tumpukan(_m5(250)))
        assert bukti.as_of == bukti.m5.as_of
        assert bukti.as_of > bukti.h4.as_of, (
            "H4 tersettle lebih jarang; kalau as_of ikut yang tertua, bukti "
            "M5 yang segar akan dilaporkan basi"
        )

    def test_timeframe_besar_none_saat_bahannya_kurang(self) -> None:
        """None berarti BELUM CUKUP BAHAN, bukan nol dan bukan kerusakan."""
        bukti = rakit_bukti(rakit_tumpukan(_m5(30)))
        assert bukti is not None
        assert bukti.m5 is not None
        assert bukti.h4 is None
        assert Horizon.H4 not in bukti.tersedia()

    def test_tanpa_m5_tidak_ada_bukti(self) -> None:
        """Keputusan final berasal dari M5; tanpa M5 tidak ada apa pun."""
        assert rakit_bukti(rakit_tumpukan([])) is None

    def test_tidak_ada_bukti_yang_mendahului_as_of(self) -> None:
        """Inti SPEC 24: tak satu pun timeframe boleh tahu lebih dulu."""
        bukti = rakit_bukti(rakit_tumpukan(_m5(250)))
        for snap in (bukti.m15, bukti.h1, bukti.h4):
            if snap is not None:
                assert snap.as_of <= bukti.as_of

    def test_tersedia_menyebut_yang_terhitung_saja(self) -> None:
        bukti = rakit_bukti(rakit_tumpukan(_m5(250)))
        assert Horizon.M5 in bukti.tersedia()
        assert set(bukti.tersedia()) <= {
            Horizon.M5,
            Horizon.M15,
            Horizon.H1,
            Horizon.H4,
        }

    def test_indikator_m5_benar_benar_terhitung(self) -> None:
        """Snapshot kosong akan lolos semua test di atas tanpa arti."""
        bukti = rakit_bukti(rakit_tumpukan(_m5(250)))
        atr = bukti.m5.reading("atr")
        assert atr is not None
        assert atr.available, "ATR wajib terukur; geometri bergantung padanya"
