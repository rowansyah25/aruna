"""Skor kecocokan strategi terhadap rezim (bagian 17.14 - 17.15, 17.21 - 17.23).

**Yang dijaga di sini urutannya, bukan angkanya.** Skor akan bergeser tiap kali
bobot rezim disetel; yang tidak boleh terbalik adalah bahwa strategi yang
rezimnya cocok mengalahkan yang tidak, bahwa sampel tipis tidak mengalahkan
sampel tebal, dan bahwa risiko ekstrem menahan alih-alih menaikkan.

Test yang mematok angka akan memaksa penyetelnya menyunting test - dan itu
mengubah penjaga menjadi stempel.
"""

from __future__ import annotations

from decimal import Decimal

from aruna.learning.strategies import Strategy, StrategyStatus
from aruna.risk.score import RiskLevel
from aruna.router.kecocokan import NETRAL, Kecocokan, nilai
from aruna.router.label import SlicePerforma
from aruna.router.rezim import PetaRezim


def _strategi(
    *,
    kode: str = "STR-001",
    preferred: tuple[str, ...] = ("TRENDING",),
) -> Strategy:
    return Strategy(
        code=kode,
        name="uji",
        description="uji",
        conditions=(),
        preferred_regimes=preferred,
        preferred_horizons=("15m",),
        status=StrategyStatus.ACTIVE,
    )


def _peta(regime: str = "TRENDING", confidence: float = 85.0) -> PetaRezim:
    return PetaRezim(regime, confidence, (), (), ())


def _slice(win: float, n: int, dd: str | None = None) -> SlicePerforma:
    return SlicePerforma(win_rate=win, sample_size=n,
                         max_drawdown=Decimal(dd) if dd else None)


class TestKecocokanRezim:
    """Bagian 17.14."""

    def test_rezim_cocok_mengalahkan_yang_tidak(self) -> None:
        cocok = nilai(_strategi(preferred=("TRENDING",)),
                      peta=_peta(), performa=None, stabil=90.0)
        tidak = nilai(_strategi(preferred=("RANGING",)),
                      peta=_peta(), performa=None, stabil=90.0)

        assert cocok.skor > tidak.skor

    def test_strategi_tanpa_preferensi_netral(self) -> None:
        """Strategi tanpa `preferred_regimes` cocok di mana pun - itu bentuk
        `Conservative` di bagian 17.2, bukan data yang lupa diisi. Ia tidak
        dinaikkan maupun diturunkan."""
        bebas = nilai(_strategi(preferred=()), peta=_peta(),
                      performa=None, stabil=100.0)

        assert bebas.skor == NETRAL

    def test_tren_berarah_cocok_dengan_strategi_tren(self) -> None:
        """**Cacat yang diukur 2026-08-23, dan ia total bukan sebagian.**

        Classifier memulangkan taksonomi BERARAH sejak bagian 2 spec -
        `TRENDING_BULLISH`, `TRENDING_BEARISH`, `BREAKDOWN` - sementara tidak
        satu pun dari tujuh strategi di katalog menulisnya di
        `preferred_regimes`. Seluruhnya menulis bentuk keluarganya: `TRENDING`,
        `BREAKOUT`.

        Terukur pada 9.437 bacaan 15m dalam tujuh hari::

            TRENDING_BULLISH  438
            TRENDING_BEARISH  270
            BREAKDOWN          16

        Tanpa pelipatan keluarga, 724 bacaan itu membuat SETIAP strategi
        berpreferensi jatuh di bawah NETRAL sekaligus, dan router memulangkan
        NONE untuk rezim yang jelas-jelas punya strateginya.

        Petanya tidak dibuat di sini: `Regime.keluarga` sudah ada di
        `core.enums` justru untuk mencocokkan lintas generasi taksonomi.
        """
        naik = nilai(_strategi(preferred=("TRENDING",)),
                     peta=_peta("TRENDING_BULLISH"), performa=None, stabil=90.0)
        turun = nilai(_strategi(preferred=("TRENDING",)),
                      peta=_peta("TRENDING_BEARISH"), performa=None, stabil=90.0)
        tembus = nilai(_strategi(preferred=("BREAKOUT",)),
                       peta=_peta("BREAKDOWN"), performa=None, stabil=90.0)

        assert naik.skor > NETRAL
        assert turun.skor > NETRAL
        assert tembus.skor > NETRAL

    def test_keluarga_tidak_mencocokkan_yang_memang_beda(self) -> None:
        """Pelipatan keluarga bukan izin mencocokkan apa saja. `RANGING` dan
        `TRENDING` tetap dua rezim yang berbeda, dan strategi tren tidak boleh
        naik hanya karena petanya melipat sesuatu di tempat lain."""
        salah = nilai(_strategi(preferred=("TRENDING",)),
                      peta=_peta("RANGING"), performa=None, stabil=90.0)

        assert salah.skor < NETRAL

    def test_rezim_di_luar_kosakata_tidak_meledak(self) -> None:
        """`signal_snapshots` memuat rezim dari beberapa generasi taksonomi.
        Nilai yang tidak dikenal `Regime` harus diperlakukan tidak cocok -
        bukan melempar dan menjatuhkan seluruh fase router."""
        asing = nilai(_strategi(preferred=("TRENDING",)),
                      peta=_peta("REZIM_YANG_BELUM_ADA"), performa=None,
                      stabil=90.0)

        assert asing.skor < NETRAL

    def test_rezim_tanpa_strategi_mana_pun_tetap_dijawab(self) -> None:
        """`HIGH_VOLATILITY` (453 bacaan) dan `ANOMALY` (49) tidak ada di
        `preferred_regimes` satu pun strategi, dan keluarganya pun tidak.
        Itu NONE yang jujur - bukan bug - dan `nilai` tetap harus menjawab
        alih-alih melempar."""
        hasil = nilai(_strategi(preferred=("TRENDING",)),
                      peta=_peta("HIGH_VOLATILITY"), performa=None, stabil=90.0)

        assert hasil.skor < NETRAL
        assert hasil.alasan

    def test_rezim_tak_terbaca_tidak_menghukum_siapa_pun(self) -> None:
        """`primary is None` berarti belum terbaca. Menghukum seluruh kandidat
        atas ketidaktahuan kita sendiri akan membuat setiap aset yang baru
        dipantau terlihat tidak punya strategi yang cocok."""
        buta = PetaRezim(None, 0.0, (), (), ())
        hasil = nilai(_strategi(), peta=buta, performa=None, stabil=90.0)

        assert hasil.skor == NETRAL


class TestKeyakinanDanStabilitasMenskalakan:
    def test_keyakinan_rendah_menarik_skor_ke_netral(self) -> None:
        """Rezim yang benar tapi tidak yakin bukan bukti yang lebih kuat
        daripada rezim yang salah - ia bukti yang lebih LEMAH atas hal yang
        sama. Karena itu keyakinan menskalakan, tidak menambah."""
        yakin = nilai(_strategi(), peta=_peta(confidence=100.0),
                      performa=None, stabil=100.0)
        ragu = nilai(_strategi(), peta=_peta(confidence=20.0),
                     performa=None, stabil=100.0)

        assert yakin.skor > ragu.skor > NETRAL

    def test_stabilitas_rendah_menarik_skor_ke_netral(self) -> None:
        """Bagian 17.10: stabilitas rendah harus MENURUNKAN keyakinan
        strategi."""
        stabil = nilai(_strategi(), peta=_peta(), performa=None, stabil=100.0)
        goyah = nilai(_strategi(), peta=_peta(), performa=None, stabil=20.0)

        assert stabil.skor > goyah.skor

    def test_stabilitas_belum_terukur_tidak_menghukum(self) -> None:
        """`stabilitas()` memulangkan `None` untuk riwayat pendek - belum bisa
        diukur, bukan sangat tidak stabil. Menyamakannya dengan nol akan
        menghukum tiap aset yang baru dipantau."""
        belum = nilai(_strategi(), peta=_peta(), performa=None, stabil=None)
        penuh = nilai(_strategi(), peta=_peta(), performa=None, stabil=100.0)

        assert belum.skor == penuh.skor


class TestIngatanMenskalakan:
    """Bagian 17.20. **Test yang lahir dari cabut-uji yang gagal menangkap.**

    Versi pertama hanya memeriksa kalimat alasannya muncul di baris tersimpan -
    dan kalimat itu ditambahkan fase, bukan oleh `nilai`. Pengali ingatannya
    bisa dicabut dari penskalaan dan seluruh suite tetap hijau.
    """

    def test_ingatan_buruk_menurunkan_skor(self) -> None:
        biasa = nilai(_strategi(), peta=_peta(), performa=None, stabil=90.0)
        buruk = nilai(
            _strategi(), peta=_peta(), performa=None, stabil=90.0, ingatan=0.85
        )

        assert buruk.skor < biasa.skor

    def test_ingatan_baik_menaikkan_skor(self) -> None:
        biasa = nilai(_strategi(), peta=_peta(), performa=None, stabil=90.0)
        baik = nilai(
            _strategi(), peta=_peta(), performa=None, stabil=90.0, ingatan=1.15
        )

        assert baik.skor > biasa.skor

    def test_pengali_seragam_tidak_membalik_peringkat(self) -> None:
        """**Ini yang membuat ingatan aman dipakai di sini.** Ia bukti tentang
        KONDISI, bukan tentang satu strategi melawan yang lain - jadi pengali
        yang sama untuk semua kandidat tidak boleh mengubah siapa yang unggul.
        """
        for pengali in (0.8, 1.0, 1.2):
            cocok = nilai(_strategi(preferred=("TRENDING",)), peta=_peta(),
                          performa=None, stabil=90.0, ingatan=pengali)
            tidak = nilai(_strategi(preferred=("RANGING",)), peta=_peta(),
                          performa=None, stabil=90.0, ingatan=pengali)

            assert cocok.skor > tidak.skor, pengali

    def test_ingatan_netral_tidak_mengubah_apa_pun(self) -> None:
        """Pengali satu harus benar-benar tidak berpengaruh - kalau tidak,
        setiap aset yang ingatannya diam ikut tergeser diam-diam."""
        tanpa = nilai(_strategi(), peta=_peta(), performa=None, stabil=90.0)
        netral = nilai(
            _strategi(), peta=_peta(), performa=None, stabil=90.0, ingatan=1.0
        )

        assert tanpa.skor == netral.skor


class TestPerlindunganSampel:
    """Bagian 17.23."""

    def test_sampel_kecil_tidak_mengalahkan_sampel_besar(self) -> None:
        """Angka bagian 17.23 apa adanya: 95% dari 8 sampel melawan 82% dari
        1.200. Selang kepercayaan yang pertama membentang hampir separuh
        sumbu, dan router yang memilihnya sedang memilih derau."""
        tipis = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.95, 8), stabil=90.0)
        tebal = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.82, 1200), stabil=90.0)

        assert tebal.skor > tipis.skor

    def test_sampel_kurang_disebut_alasannya(self) -> None:
        """Skor yang turun tanpa sebab tidak bisa dibantah."""
        hasil = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.95, 8), stabil=90.0)

        assert any("sampel" in a.lower() for a in hasil.alasan)

    def test_tanpa_performa_sama_sekali_bukan_hukuman(self) -> None:
        """`None` berarti belum bisa dijawab. Sesudah Task 3, seluruh slice
        per-rezim memulangkan `None` sampai baris router cukup - dan kalau itu
        dihukum, router tidak akan pernah memilih siapa pun."""
        kosong = nilai(_strategi(), peta=_peta(), performa=None, stabil=90.0)
        rendah = nilai(_strategi(), peta=_peta(),
                       performa=_slice(0.30, 1200), stabil=90.0)

        assert kosong.skor > rendah.skor


class TestRisiko:
    """Bagian 17.21 - 17.22."""

    def test_risiko_ekstrem_menahan_performa_tinggi(self) -> None:
        """Angka bagian 17.21 apa adanya: performa 91% dengan risiko tinggi
        tidak boleh otomatis mengalahkan performa 84% dengan risiko rendah.

        Sumbernya `strategy_performance.max_drawdown` yang SUDAH tersimpan -
        bukan pembacaan baru.
        """
        ganas = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.91, 900, "-0.92"), stabil=90.0)
        tenang = nilai(_strategi(), peta=_peta(),
                       performa=_slice(0.84, 900, "-0.05"), stabil=90.0)

        assert tenang.skor > ganas.skor

    def test_risiko_menurunkan_tidak_pernah_menaikkan(self) -> None:
        """Drawdown dangkal bukan prestasi - ia ketiadaan bukti bahaya. Yang
        menaikkan skor hanya performa dan kecocokan."""
        tanpa_dd = nilai(_strategi(), peta=_peta(),
                         performa=_slice(0.84, 900), stabil=90.0)
        dd_dangkal = nilai(_strategi(), peta=_peta(),
                           performa=_slice(0.84, 900, "-0.01"), stabil=90.0)

        assert dd_dangkal.skor <= tanpa_dd.skor

    def test_tingkat_risikonya_ikut_dibawa(self) -> None:
        """Gerbang di Task 9 dan pembaca laporan sama-sama butuh tingkatnya,
        bukan cuma skor yang sudah dipotong."""
        hasil = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.91, 900, "-0.92"), stabil=90.0)

        assert hasil.risiko is RiskLevel.VERY_HIGH

    def test_seluruh_tingkat_risiko_punya_potongan(self) -> None:
        """**Bug yang ditangkap test, 2026-08-23.** Versi pertama peta
        potongan cuma memuat HIGH dan MEDIUM - jadi `VERY_HIGH`, tingkat
        paling berbahaya di PASAL 13.2, jatuh ke bawaan nol dan tidak
        dipotong sama sekali.

        Peta yang tidak lengkap atas sebuah enum akan selalu diam pada anggota
        yang lupa ditulis, dan diamnya terlihat seperti keputusan.
        """
        from aruna.router.kecocokan import _POTONGAN_RISIKO

        assert set(_POTONGAN_RISIKO) == set(RiskLevel)
        assert _POTONGAN_RISIKO[RiskLevel.VERY_HIGH] > _POTONGAN_RISIKO[
            RiskLevel.HIGH
        ]

    def test_tanpa_drawdown_risikonya_tak_terbaca(self) -> None:
        hasil = nilai(_strategi(), peta=_peta(),
                      performa=_slice(0.84, 900), stabil=90.0)

        assert hasil.risiko is None


class TestBentuknya:
    def test_skor_tidak_pernah_keluar_batas(self) -> None:
        ekstrem = nilai(_strategi(preferred=("RANGING",)), peta=_peta(),
                        performa=_slice(0.0, 5000, "-0.99"), stabil=100.0)

        assert 0 <= ekstrem.skor <= 100

    def test_alasannya_tidak_pernah_kosong(self) -> None:
        """Bagian 17.6 melarang kesimpulan tanpa alasan, dan skor kecocokan
        adalah kesimpulan."""
        hasil = nilai(_strategi(), peta=_peta(), performa=None, stabil=90.0)

        assert hasil.alasan

    def test_kecocokannya_beku(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(Kecocokan)
        assert Kecocokan.__dataclass_params__.frozen
