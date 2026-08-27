"""Data tak layak menghasilkan NO SIGNAL yang menyebutkan sebabnya.

Alasannya yang penting, bukan sekadar penolakannya. "Tidak ada sinyal karena
memang tak ada setup" dan "tidak ada sinyal karena feed mati" terlihat sama
persis dari luar - yang pertama normal, yang kedua kerusakan.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.config import DataSettings
from aruna.core.enums import Decision, Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.data.quality import QualityGate
from aruna.xau.kelayakan import periksa_kelayakan
from aruna.xau.timeframes import rakit_tumpukan

# Senin 2026-08-31, jam perdagangan penuh.
AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
SEKARANG = AWAL + timedelta(minutes=5 * 48)


def _gate() -> QualityGate:
    return QualityGate(DataSettings(_env_file=None), source="uji")


def _m5(jumlah: int, *, lompati: int | None = None) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar: list[Candle] = []
    for i in range(jumlah):
        if lompati is not None and i == lompati:
            continue
        buka = AWAL + timedelta(minutes=5 * i)
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


class TestKelayakan:
    def test_data_sehat_layak(self) -> None:
        hasil = periksa_kelayakan(rakit_tumpukan(_m5(48)), _gate(), sekarang=SEKARANG)
        assert hasil.layak is True
        assert hasil.alasan is None

    def test_tanpa_data_tidak_layak(self) -> None:
        hasil = periksa_kelayakan(rakit_tumpukan([]), _gate(), sekarang=SEKARANG)
        assert hasil.layak is False
        assert "tidak ada" in hasil.alasan.lower()

    def test_bar_hilang_di_tengah_menolak(self) -> None:
        """Lubang di deret berarti fitur dihitung di atas waktu yang bolong."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48, lompati=20)), _gate(), sekarang=SEKARANG
        )
        assert hasil.layak is False
        assert "lubang" in hasil.alasan.lower()

    def test_data_basi_menolak(self) -> None:
        """Bar terakhir dari sejam lalu tidak boleh jadi dasar keputusan M5."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48)), _gate(), sekarang=SEKARANG + timedelta(hours=1)
        )
        assert hasil.layak is False
        assert "basi" in hasil.alasan.lower()

    def test_alasan_basi_menyebut_batasnya(self) -> None:
        """Operator harus bisa membantah angkanya, jadi angkanya harus ada."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48)), _gate(), sekarang=SEKARANG + timedelta(hours=1)
        )
        assert "660" in hasil.alasan

    def test_bar_invalid_menolak(self) -> None:
        """OHLC yang tidak koheren adalah data rusak, bukan pergerakan."""
        bars = _m5(48)
        bars[10] = replace(bars[10], high=Decimal(1), low=Decimal(9999))
        hasil = periksa_kelayakan(rakit_tumpukan(bars), _gate(), sekarang=SEKARANG)
        assert hasil.layak is False
        assert "invalid" in hasil.alasan.lower()

    def test_timeframe_kurang_menolak_dan_menyebutkannya(self) -> None:
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(20)),
            _gate(),
            sekarang=AWAL + timedelta(minutes=100),
        )
        assert hasil.layak is False
        assert "4h" in hasil.alasan.lower()

    def test_tidak_layak_selalu_no_signal(self) -> None:
        """Kosakata XAU: NO_SIGNAL, tidak pernah WAIT."""
        hasil = periksa_kelayakan(rakit_tumpukan([]), _gate(), sekarang=SEKARANG)
        assert hasil.keputusan is Decision.NO_SIGNAL

    def test_layak_tidak_memutuskan_arah(self) -> None:
        """Kelayakan bukan sinyal: layak berarti boleh dinilai, bukan BUY."""
        hasil = periksa_kelayakan(rakit_tumpukan(_m5(48)), _gate(), sekarang=SEKARANG)
        assert hasil.keputusan is Decision.NO_SIGNAL

    def test_kosakata_futures_tidak_dipakai(self) -> None:
        """Spec melarang LONG/SHORT/WAIT di modul XAU."""
        import aruna.xau.kelayakan as modul

        sumber = modul.__file__
        with open(sumber, encoding="utf-8") as f:
            isi = f.read()
        for terlarang in ("Decision.LONG", "Decision.SHORT", "Decision.WAIT"):
            assert terlarang not in isi

    def test_akhir_pekan_tidak_dianggap_lubang(self) -> None:
        """Senin pagi tidak boleh ditolak karena pasar tutup Sabtu-Minggu."""
        # Dua jam terakhir Jumat, RAPAT sampai penutupan 21:00 UTC, lalu
        # langsung Minggu 22:00 saat pasar buka lagi. Yang di antaranya - Sabtu
        # penuh dan Minggu siang - bukan data hilang, pasarnya tutup.
        jumat = datetime(2026, 8, 28, 19, 0, tzinfo=UTC)
        prov = Provenance(source="twelvedata")
        bars = []
        for mulai, jumlah in ((jumat, 24), (datetime(2026, 8, 30, 22, 0, tzinfo=UTC), 24)):
            for i in range(jumlah):
                buka = mulai + timedelta(minutes=5 * i)
                bars.append(
                    Candle(
                        market=Market.FOREX,
                        symbol="XAU/USD",
                        interval=Horizon.M5,
                        open_time=buka,
                        close_time=buka + timedelta(minutes=5),
                        open=Decimal(1000),
                        high=Decimal(1001),
                        low=Decimal(999),
                        close=Decimal(1000),
                        volume=Decimal(0),
                        provenance=prov,
                        is_closed=True,
                    )
                )
        hasil = periksa_kelayakan(
            rakit_tumpukan(bars),
            _gate(),
            sekarang=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        )
        assert "lubang" not in (hasil.alasan or "").lower()
