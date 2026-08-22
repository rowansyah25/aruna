"""PASAL 15.7 dan 15.23: skor kemiripan, dan seberapa banyak yang terbaca.

Keduanya angka yang berbeda dan tidak boleh dilebur. Similarity 100% atas dua
dimensi yang terbaca dari delapan bukan hal yang sama dengan 100% atas
delapan-delapannya - yang pertama berarti "yang sedikit itu cocok", yang kedua
berarti "kondisinya memang mirip".

Melebur keduanya menghasilkan angka tinggi justru pada rekaman yang paling
sedikit datanya. Itu keluarga cacat yang sama dengan kelengkapan integrasi
Phase 14 yang dulu terlihat penuh pada pemanggil yang paling sedikit melapor,
dan dengan `periksa()` yang menghitung yang tidak dilaporkan sebagai hadir.
"""

from __future__ import annotations

from aruna.memory.dimensions import TERSIMPAN, UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.similarity import AMBANG_MIRIP, BOBOT, bandingkan


def _sidik(**ganti: str) -> Sidik:
    dasar: dict[Dimensi, str] = {
        Dimensi.ASSET: "BTC/USDT",
        Dimensi.MARKET: "CRYPTO",
        Dimensi.TIMEFRAME: "15m",
        Dimensi.REGIME: "TRENDING",
        Dimensi.RISK_LEVEL: "MODERATE",
        Dimensi.NEWS: "NEUTRAL",
        Dimensi.QUALITY: "MEDIUM",
        Dimensi.LIQUIDITY: "TIGHT",
        # Lima dimensi teknikal, ditambahkan 2026-08-21. Helper ini semula
        # hanya mengisi delapan yang lama, dan cakupannya turun ke 65% -
        # palsu yang tertinggal di belakang kodenya.
        Dimensi.VOLATILITY: "MEDIUM",
        Dimensi.MOMENTUM: "POSITIVE",
        Dimensi.VOLUME: "NORMAL",
        Dimensi.TREND: "BULLISH",
        Dimensi.STRUCTURE: "UPTREND",
    }
    dasar.update({Dimensi[k]: v for k, v in ganti.items()})
    penuh = {d: UNKNOWN for d in Dimensi}
    penuh.update(dasar)
    return Sidik(nilai=penuh)


class TestSkornya:
    def test_identik_seratus(self) -> None:
        assert bandingkan(_sidik(), _sidik()).skor == 100

    def test_rezim_berbeda_menurunkan_skor(self) -> None:
        hasil = bandingkan(_sidik(), _sidik(REGIME="RANGING"))

        assert hasil.skor < 100
        assert Dimensi.REGIME in hasil.beda

    def test_semuanya_berbeda_nol(self) -> None:
        semua_beda = _sidik(
            ASSET="ETH/USDT", MARKET="IDX", TIMEFRAME="1d", REGIME="RANGING",
            RISK_LEVEL="HIGH", NEWS="NEGATIVE", QUALITY="LOW", LIQUIDITY="WIDE",
            VOLATILITY="HIGH", MOMENTUM="NEGATIVE", VOLUME="HIGH",
            TREND="BEARISH", STRUCTURE="DOWNTREND",
        )
        hasil = bandingkan(_sidik(), semua_beda)

        assert hasil.skor == 0

    def test_skor_selalu_di_dalam_nol_seratus(self) -> None:
        for a, b in ((_sidik(), _sidik()),
                     (_sidik(), _sidik(REGIME="RANGING")),
                     (_sidik(), _sidik(ASSET="ETH/USDT", QUALITY="LOW"))):
            assert 0 <= bandingkan(a, b).skor <= 100

    def test_aset_yang_berbeda_lebih_mahal_daripada_berita(self) -> None:
        """PASAL 15.13: tiap aset punya kepribadian sendiri. Kemiripan lintas
        aset bernilai jauh lebih kecil daripada kemiripan di aset yang sama,
        dan bobot yang rata membuat BTC dan SOL terbaca setara."""
        beda_aset = bandingkan(_sidik(), _sidik(ASSET="ETH/USDT")).skor
        beda_berita = bandingkan(_sidik(), _sidik(NEWS="NEGATIVE")).skor

        assert beda_aset < beda_berita


class TestCakupan:
    def test_yang_tak_terbaca_keluar_dari_penyebut(self) -> None:
        """Dua dimensi cocok dari dua yang terbaca tetap 100 - dan cakupannya
        yang memberitahu bahwa "dua" itu sedikit."""
        tipis = Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.ASSET: "BTC/USDT",
            Dimensi.REGIME: "TRENDING",
        })
        hasil = bandingkan(tipis, _sidik())

        assert hasil.skor == 100
        assert hasil.cakupan < 100

    def test_cakupan_penuh_saat_semua_tersimpan_terbaca(self) -> None:
        hasil = bandingkan(_sidik(), _sidik())

        assert hasil.cakupan == 100

    def test_tak_terbaca_disebut_namanya(self) -> None:
        """Dihitung saja tidak cukup: hanya namanya yang memberi tahu apa yang
        harus dicari kalau cakupannya rendah."""
        hasil = bandingkan(_sidik(), _sidik())

        # Open interest dan funding: satu-satunya yang benar-benar tidak ada.
        assert Dimensi.OPEN_INTEREST in hasil.tak_terbaca
        assert Dimensi.REGIME not in hasil.tak_terbaca

    def test_tanpa_satu_pun_dimensi_terbaca_skornya_nol(self) -> None:
        """Bukan seratus, dan bukan pengecualian. Dua sidik jari kosong yang
        dibandingkan tanpa penjaga ini membagi nol dengan nol, dan jawaban apa
        pun yang bukan nol berarti ARUNA mengaku mengenali kondisi yang tidak
        pernah ia lihat."""
        kosong = Sidik(nilai={d: UNKNOWN for d in Dimensi})

        hasil = bandingkan(kosong, kosong)

        assert hasil.skor == 0
        assert hasil.cakupan == 0

    def test_cakupan_tidak_ikut_menaikkan_skor(self) -> None:
        """Penjaga terhadap peleburan: dua rekaman tipis yang cocok harus
        menghasilkan skor tinggi DAN cakupan rendah - bukan satu angka
        menengah yang menyembunyikan keduanya."""
        tipis = Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.REGIME: "TRENDING",
        })
        hasil = bandingkan(tipis, _sidik())

        assert hasil.skor == 100
        assert hasil.cakupan <= 25


class TestBobot:
    def test_setiap_dimensi_tersimpan_punya_bobot(self) -> None:
        """Dimensi tersimpan tanpa bobot tidak pernah ikut menghitung - ia ada
        di sidik jari dan tidak ada di pengukuran."""
        assert set(BOBOT) >= TERSIMPAN

    def test_yang_tidak_tersimpan_tidak_punya_bobot(self) -> None:
        """Memberi bobot pada dimensi yang selalu UNKNOWN akan mengecilkan
        cakupan setiap perbandingan tanpa pernah bisa dinaikkan."""
        assert not (set(BOBOT) - TERSIMPAN)

    def test_bobotnya_positif(self) -> None:
        assert all(b > 0 for b in BOBOT.values())

    def test_ambangnya_delapan_puluh(self) -> None:
        """PASAL 15.8 memberi contohnya sendiri: Minimum Similarity 80%."""
        assert AMBANG_MIRIP == 80
