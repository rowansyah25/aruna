"""Peta rezim multi-timeframe: mana yang primary, mana yang cuma sekunder.

**Yang dijaga di sini bedanya, bukan angkanya.** Bobot tiap interval boleh
disetel; yang tidak boleh bergeser adalah bahwa pullback lima menit tidak
terbaca sebagai perubahan tren empat jam. Bagian 17.8 ada justru untuk itu.

Dan yang kedua: interval yang TIDAK ADA harus terlihat. Rezim yang disimpulkan
dari dua interval sementara enam diminta bukan kesimpulan yang sama kuatnya,
dan laporan yang menyembunyikan bedanya membuat keduanya terbaca sama.
"""

from __future__ import annotations

from aruna.router.rezim import (
    BOBOT_INTERVAL,
    MINIMUM_RIWAYAT,
    BacaanRezim,
    PetaRezim,
    stabilitas,
    susun_peta,
)


def _b(interval: str, regime: str, confidence: float = 80.0) -> BacaanRezim:
    return BacaanRezim(
        interval=interval,
        regime=regime,
        confidence=confidence,
        alasan=(f"{regime} pada {interval}",),
    )


class TestPrimaryVsSekunder:
    """Bagian 17.8."""

    def test_banyak_bacaan_pendek_tidak_mengalahkan_satu_panjang(self) -> None:
        """**Kasus yang sebenarnya terjadi, dan yang lolos tanpa pembobotan.**

        Versi pertama test ini memakai satu RANGING 5m melawan DUA
        TRENDING - dan ia hijau walau seluruh bobot disamakan, karena
        mayoritasnya sudah menang tanpa bantuan. Ia menguji jumlah, bukan
        bobot.

        Yang benar kebalikannya: interval pendek dipindai paling sering, jadi
        ia yang paling banyak bacaannya. Tanpa pembobotan, primary diserahkan
        kepada jadwal pemindaian alih-alih kepada pasar.
        """
        peta = susun_peta((
            _b("5m", "RANGING", 70.0),
            _b("5m", "RANGING", 70.0),
            _b("5m", "RANGING", 70.0),
            _b("4h", "TRENDING_BULLISH", 88.0),
        ))

        assert peta.primary == "TRENDING_BULLISH"
        assert "RANGING" in peta.sekunder

    def test_satu_bacaan_pendek_tidak_mengalahkan_satu_panjang(self) -> None:
        peta = susun_peta((
            _b("5m", "REVERSAL", 95.0),
            _b("4h", "TRENDING_BULLISH", 60.0),
        ))

        assert peta.primary == "TRENDING_BULLISH"

    def test_keyakinan_primary_dari_yang_mendukungnya_saja(self) -> None:
        """Bukan rata-rata seluruh bacaan. Bacaan yang menyebut rezim LAIN
        bukan bukti yang lemah untuk rezim ini - ia bukti untuk rezim yang
        lain, dan memasukkannya ke rata-rata mencampur dua pertanyaan."""
        peta = susun_peta((
            _b("1h", "TRENDING_BULLISH", 90.0),
            _b("4h", "TRENDING_BULLISH", 80.0),
            _b("5m", "RANGING", 10.0),
        ))

        assert peta.primary_confidence == 85.0

    def test_sekunder_tidak_memuat_primary(self) -> None:
        peta = susun_peta((
            _b("1h", "TRENDING_BULLISH"),
            _b("4h", "TRENDING_BULLISH"),
        ))

        assert peta.primary == "TRENDING_BULLISH"
        assert peta.sekunder == ()


class TestYangTidakTerbaca:
    def test_tanpa_bacaan_tidak_ada_primary(self) -> None:
        """``None`` berarti tidak terbaca, bukan "tidak ada rezim". Pemanggil
        yang menyamakannya dengan sebuah rezim akan memilih strategi atas
        pasar yang belum pernah dilihatnya."""
        peta = susun_peta(())

        assert peta.primary is None
        assert peta.primary_confidence == 0.0

    def test_interval_yang_tidak_ada_dilaporkan(self) -> None:
        """Rezim yang disimpulkan dari satu interval sementara enam diminta
        bukan kesimpulan yang sama kuatnya - dan bedanya harus terlihat."""
        peta = susun_peta((_b("15m", "RANGING", 60.0),))

        assert "4h" in peta.interval_hilang
        assert "1d" in peta.interval_hilang
        assert "15m" not in peta.interval_hilang

    def test_interval_asing_tidak_menjatuhkan_apa_pun(self) -> None:
        """Interval di luar daftar bobot tetap ikut dihitung dengan bobot
        netral. Membuangnya berarti bacaan yang benar-benar ada hilang tanpa
        jejak; yang benar adalah ia dipakai, bukan diabaikan."""
        peta = susun_peta((_b("2h", "BREAKOUT", 90.0),))

        assert peta.primary == "BREAKOUT"


class TestSeriTidakMengarang:
    def test_seri_diputus_stabil_bukan_acak(self) -> None:
        """Dua rezim dengan bobot yang sama persis harus menghasilkan jawaban
        yang SAMA tiap kali dipanggil. Urutan dict di Python memang stabil,
        tapi bersandar padanya membuat jawabannya bergantung pada urutan baris
        yang kebetulan keluar dari database."""
        bacaan = (
            _b("1h", "BREAKOUT", 80.0),
            _b("1h", "REVERSAL", 80.0),
        )

        assert susun_peta(bacaan).primary == susun_peta(bacaan[::-1]).primary


class TestBobotnyaKebijakan:
    def test_urutannya_yang_dipertahankan_bukan_jaraknya(self) -> None:
        """Angkanya boleh disetel; yang tidak boleh terbalik adalah urutannya.
        Test ini menjaga klaim itu, bukan nilainya."""
        urut = [BOBOT_INTERVAL[i] for i in ("5m", "15m", "1h", "4h", "1d")]

        assert urut == sorted(urut)
        assert BOBOT_INTERVAL["1d"] > BOBOT_INTERVAL["5m"]


class TestStabilitas:
    """Bagian 17.10."""

    def test_rezim_yang_diam_stabil(self) -> None:
        assert stabilitas(("TRENDING_BULLISH",) * 8) == 100.0

    def test_rezim_yang_berkedip_tidak_stabil(self) -> None:
        """**Terukur di Phase 16, 2026-08-22:** classifier 15m berpindah pada
        30,6% bacaan berurutan, dan 11 dari 20 simbol melihat tiga regime
        berbeda dalam dua jam.

        Router yang tidak menghitungnya akan memilih strategi atas rezim yang
        sudah berganti sebelum sinyalnya sempat terbit.
        """
        berkedip = ("TRENDING_BULLISH", "RANGING") * 4

        assert stabilitas(berkedip) < 40.0

    def test_setengah_berpindah_setengah_diam(self) -> None:
        """Empat pasang berurutan, dua di antaranya berpindah."""
        assert stabilitas(("A", "A", "B", "B", "C")) == 50.0

    def test_riwayat_terlalu_pendek_tidak_terukur(self) -> None:
        """``None`` berarti BELUM BISA DIUKUR, bukan "sangat tidak stabil".

        Nol akan terbaca sebagai rezim yang berkedip terus - dan itu
        kesimpulan yang jauh lebih dramatis daripada "baru satu bacaan".
        Pemanggil yang menyamakannya akan menurunkan keyakinan setiap kali
        sebuah aset baru mulai dipantau.
        """
        assert stabilitas(()) is None
        assert stabilitas(("TRENDING_BULLISH",)) is None
        assert MINIMUM_RIWAYAT == 2

    def test_ambangnya_disebut_bukan_diketik_ulang(self) -> None:
        cukup = ("A",) * MINIMUM_RIWAYAT

        assert stabilitas(cukup) is not None


class TestBentuknya:
    def test_bacaan_ikut_terbawa_utuh(self) -> None:
        """Peta yang tidak membawa bacaannya tidak bisa dibantah - dan bagian
        17.6 melarang rezim tanpa alasan."""
        bacaan = (_b("1h", "TRENDING_BULLISH"),)
        peta = susun_peta(bacaan)

        assert peta.per_interval == bacaan
        assert peta.per_interval[0].alasan

    def test_petanya_beku(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(PetaRezim)
        assert PetaRezim.__dataclass_params__.frozen
