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
from aruna.xau.loop import BAR_DIBUTUHKAN
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


#: Bacaan yang butuh riwayat PANJANG, dan karena itu yang pertama mati saat
#: bahannya kurang.  Dipilih justru karena semuanya ``NULL`` pada 205 dari 205
#: keputusan produksi sebelum :data:`BAR_DIBUTUHKAN` dinaikkan.
BUTUH_RIWAYAT = ("ema_50", "sma_50", "macd", "ema_21", "realised_volatility")


class TestKonteksBesarTerisi:
    """Timeframe besar harus BERISI, bukan sekadar terbentuk.

    Test-test di atas memeriksa keempat timeframe HADIR - dan seluruhnya tetap
    hijau selama berbulan-bulan sementara setiap bacaan H4 di produksi bernilai
    ``NULL``.  Ember yang terbentuk lolos pemeriksaan keberadaan; yang tidak
    diperiksa siapa pun adalah apakah ada isinya.

    Akibatnya nyata: dewan tidak mengusulkan arah pada 121 dari 199 keputusan,
    dan agen yang tersisa condong SELL 3:1 karena hanya membaca 5 dan 15 menit.
    """

    def test_h4_punya_bacaan_yang_butuh_riwayat(self) -> None:
        bukti = rakit_bukti(rakit_tumpukan(_m5(BAR_DIBUTUHKAN)))
        assert bukti.h4 is not None

        mati = [
            nama
            for nama in BUTUH_RIWAYAT
            if (r := bukti.h4.reading(nama)) is None or not r.available
        ]
        assert not mati, (
            f"H4 kekurangan bahan untuk {mati}; {BAR_DIBUTUHKAN} bar M5 hanya "
            f"menghasilkan {bukti.h4.bars} bar H4"
        )

    def test_h1_punya_bacaan_yang_butuh_riwayat(self) -> None:
        bukti = rakit_bukti(rakit_tumpukan(_m5(BAR_DIBUTUHKAN)))
        assert bukti.h1 is not None

        mati = [
            nama
            for nama in BUTUH_RIWAYAT
            if (r := bukti.h1.reading(nama)) is None or not r.available
        ]
        assert not mati, f"H1 kekurangan bahan untuk {mati}"

    def test_bar_h4_cukup_untuk_rata_rata_lima_puluh(self) -> None:
        """Angkanya, bukan gejalanya.

        Ambang ini yang membuat kegagalan bisa dibaca tanpa menebak: sebuah
        rata-rata 50 periode mustahil dari 49 bar, berapa pun rapinya kode di
        atasnya.
        """
        tumpukan = rakit_tumpukan(_m5(BAR_DIBUTUHKAN))
        assert len(tumpukan.h4) >= 50, (
            f"{BAR_DIBUTUHKAN} bar M5 -> {len(tumpukan.h4)} bar H4; "
            "EMA-50 dan SMA-50 tidak akan pernah terisi"
        )
