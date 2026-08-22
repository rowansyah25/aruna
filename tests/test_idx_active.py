"""Pekerjaan IDX berhenti saat bursanya tutup, dan mulai lagi sebelum bel.

Operator: "yang berhubungan dengan IDX harus berhenti total ketika market
tutup, dan jalan lagi tiga puluh menit sebelum market buka."

Satu predikat untuk semua jalur - kutipan harga, penyegaran candle, dan
penguncian prediksi. Dua salinan aturan ini akan berbeda pendapat tentang
apakah bursa buka, dan satu-satunya yang mengungkapnya adalah tagihan rate
limit atau prediksi yang tidak bisa diskor.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aruna.core.clock import IDX_CALENDAR, IDX_WARMUP, idx_active
from aruna.core.enums import Horizon, Market

WIB = ZoneInfo("Asia/Jakarta")

#: Selasa - hari bursa biasa, bukan Jumat yang jam sesinya berbeda.
SELASA = 18


def _wib(jam: int, menit: int = 0, *, hari: int = SELASA) -> datetime:
    return datetime(2026, 8, hari, jam, menit, tzinfo=WIB)


class TestJendelaPemanasan:
    def test_tiga_puluh_menit_sebelum_bel(self) -> None:
        assert IDX_WARMUP.total_seconds() == 30 * 60

    def test_tepat_di_pembukaan_jendela_sudah_aktif(self) -> None:
        assert idx_active(_wib(8, 30)) is True

    def test_semenit_sebelumnya_masih_mati(self) -> None:
        assert idx_active(_wib(8, 29)) is False

    def test_lelang_pra_pembukaan_bursa_ikut_aktif(self) -> None:
        assert idx_active(_wib(8, 45)) is True

    def test_jendelanya_diturunkan_dari_jam_bursa(self) -> None:
        """Bukan angka yang diketik: kalau bursa mengubah jam bukanya, jendela
        pemanasan ikut bergeser tanpa ada yang perlu mengingatnya."""
        buka = IDX_CALENDAR.windows.session1_start
        awal = (
            datetime.combine(_wib(0).date(), buka, tzinfo=WIB) - IDX_WARMUP
        )
        assert idx_active(awal) is True
        assert idx_active(awal - timedelta(minutes=1)) is False


class TestBerhentiSaatTutup:
    @pytest.mark.parametrize(
        ("jam", "menit"),
        [(0, 0), (7, 0), (8, 29), (16, 1), (18, 30), (23, 37)],
    )
    def test_di_luar_jam_bursa_mati(self, jam: int, menit: int) -> None:
        assert idx_active(_wib(jam, menit)) is False

    def test_jam_2337_mati(self) -> None:
        """Angka ini bukan contoh. Prediksi IDX benar-benar dikunci pukul 23:37
        WIB - tujuh setengah jam sesudah bursa tutup - dan horizon 1h yang
        dimulai di situ berakhir seluruhnya di dalam bursa yang tutup."""
        assert idx_active(_wib(23, 37)) is False

    def test_akhir_pekan_mati_sepanjang_hari(self) -> None:
        # 23 Agustus 2026 jatuh hari Minggu.
        for jam in (8, 9, 12, 15):
            assert idx_active(_wib(jam, hari=23)) is False, jam


class TestUjungBelakangTidakDilebarkan:
    """Sapuan penyelesaian bergantung pada gerbang ini **tertutup**.

    Bar penutupan 15m dan 1h hari itu baru final sesudah sesi pasca-perdagangan,
    dan sapuan itulah satu-satunya yang menyimpannya. Melebarkan ujung belakang
    sampai 16:15 menunda sapuannya melewati batas bar 15m - persis cacat yang
    pernah diukur dan diperbaiki di ``market_gate``.
    """

    def test_masih_aktif_di_pra_penutupan(self) -> None:
        assert idx_active(_wib(16, 0)) is True

    def test_sudah_mati_saat_sapuan_harus_jalan(self) -> None:
        assert idx_active(_wib(16, 1)) is False
        assert idx_active(_wib(16, 15)) is False


class TestSatuPredikatDipakaiSemuaJalur:
    def test_gerbang_kutipan_harga_meneruskannya(self) -> None:
        from aruna.data.ingest import idx_worth_polling

        for jam, menit in [(8, 29), (8, 30), (12, 30), (16, 0), (16, 1)]:
            saat = _wib(jam, menit)
            assert idx_worth_polling(saat) == idx_active(saat), (jam, menit)

    def test_tidak_ada_salinan_aturannya(self) -> None:
        """Versi lama mengeja aturannya sendiri di ``ingest.py``. Dua aturan
        yang harus sepakat, ditulis dua kali."""
        import inspect

        from aruna.data import ingest

        sumber = inspect.getsource(ingest.idx_worth_polling)
        assert "return idx_active(" in sumber
        assert "IdxSession.OPENING" not in sumber


class TestPenguncianPrediksiIkutBerhenti:
    """Cacat yang ditutup: council berjalan penuh untuk pasar yang tutup.

    ``notrade`` memang memblokir dengan MARKET_HALT saat ``market_open`` bernilai
    False - tapi itu berjalan SESUDAH seluruh deliberasi, jadi ongkosnya sudah
    dikeluarkan dan barisnya tetap tersimpan.
    """

    def _loop(self, **overrides):
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        return UpkeepLoop(
            refresher=None, resolver=None, locker=None,
            settings=UpkeepSettings(_env_file=None, **overrides),
            stats=UpkeepStats(started_at=_wib(9)),
        )

    def test_idx_tidak_dikunci_saat_bursa_tutup(self) -> None:
        loop = self._loop(lock_markets="IDX", lock_horizons="1d")
        assert loop._horizons_due(_wib(23, 37)) == []

    def test_idx_dikunci_saat_bursa_buka(self) -> None:
        loop = self._loop(lock_markets="IDX", lock_horizons="1d")
        assert (Market.IDX, Horizon.D1) in loop._horizons_due(_wib(10, 0))

    def test_idx_dikunci_di_jendela_pemanasan(self) -> None:
        """Pemanasan bukan sekadar menarik data: prediksi untuk hari itu boleh
        dibentuk begitu bukti hari sebelumnya lengkap."""
        loop = self._loop(lock_markets="IDX", lock_horizons="1d")
        assert (Market.IDX, Horizon.D1) in loop._horizons_due(_wib(8, 35))

    def test_crypto_tidak_ikut_berhenti(self) -> None:
        """Peredamnya harus berhenti tepat di batas pasar. Crypto tidak pernah
        tutup, dan menghentikannya karena bursa Jakarta tutup akan mematikan
        satu-satunya pasar yang sedang bergerak."""
        loop = self._loop(lock_markets="CRYPTO", lock_horizons="15m")
        assert (Market.CRYPTO, Horizon.M15) in loop._horizons_due(_wib(23, 37))

    def test_dua_pasar_sekaligus_hanya_idx_yang_berhenti(self) -> None:
        loop = self._loop(lock_markets="CRYPTO,IDX", lock_horizons="1d")
        due = loop._horizons_due(_wib(23, 37))

        assert (Market.CRYPTO, Horizon.D1) in due
        assert (Market.IDX, Horizon.D1) not in due
