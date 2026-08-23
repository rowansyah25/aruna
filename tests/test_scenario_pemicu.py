"""Kapan simulasi dibangunkan (bagian 16.2).

Yang dijaga di sini tiga hal, dan yang pertama paling penting:

1. **Pemindaian biasa menghasilkan kosong.** Bagian 16.2 dibuka dengan
   larangan - *"JANGAN menjalankan MiroFish pada setiap market scan"* - dan
   pemicu yang bocor pada pasar tenang membatalkan seluruh pasalnya.
2. **Tiap pemicu yang buktinya ada bisa dihasilkan**, dan dua yang buktinya
   tidak ada disebut namanya di sini, bukan dihilangkan diam-diam.
3. **Ambangnya dipinjam.** Tiap test ambang mengimpor konstanta aslinya dan
   menghitung dari situ, jadi test ini tidak bisa lulus sambil kodenya memakai
   angka lain - dan tidak akan basi kalau angkanya kelak diubah di sumbernya.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aruna.analysis.regime import ANOMALY_VOLUME_RATIO
from aruna.core.enums import Regime
from aruna.council.protest import HIGH_DISAGREEMENT
from aruna.futures.funding import EXTREME_RATE
from aruna.futures.openinterest import SIGNIFICANT_PCT
from aruna.scanner.events import EventKind, SignificantEvent
from aruna.scenario.pemicu import (
    AMBANG_BESAR,
    AMBANG_SELISIH_TAJAM,
    TANPA_SUMBER_DATA,
    KonteksPemicu,
    Peristiwa,
    deteksi,
    layak_simulasi,
)
from aruna.signals.quality import MIN_QUALITY

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _peristiwa(kind: EventKind, severity: float, measured: float) -> SignificantEvent:
    return SignificantEvent(
        symbol="BTCUSDT",
        kind=kind,
        severity=severity,
        detail="uji",
        at=NOW,
        evidence={"measured": measured, "threshold": measured / severity},
    )


class TestPemindaianBiasaDiam:
    """Bagian 16.2: normal scan mendapat ARUNA STANDARD ANALYSIS, titik."""

    def test_konteks_kosong_tidak_menyalakan_apa_pun(self) -> None:
        assert deteksi(KonteksPemicu()) == frozenset()

    def test_konteks_kosong_tidak_layak_disimulasikan(self) -> None:
        assert not layak_simulasi(deteksi(KonteksPemicu()))

    def test_pasar_tenang_yang_terukur_penuh_tetap_diam(self) -> None:
        """Semua bidang terisi, semuanya di bawah ambangnya. Ini bentuk yang
        paling mudah bocor: nilai yang ada tapi biasa."""
        tenang = KonteksPemicu(
            regime_sekarang=Regime.RANGING,
            regime_sebelumnya=Regime.RANGING,
            disagreement=HIGH_DISAGREEMENT / 2,
            mutu=MIN_QUALITY + 10,
            berita_penting=0,
            funding_rate=EXTREME_RATE / 2,
            perubahan_oi_pct=SIGNIFICANT_PCT / 2,
        )

        assert deteksi(tenang) == frozenset()

    def test_breakout_biasa_bukan_breakout_besar(self) -> None:
        """Pemindai menyalakan BREAKOUT di 0,25 ATR. Bagian 16.2 minta *major*
        breakout - kalau tiap break membangunkan simulasi, larangan "jangan
        tiap scan" kehilangan artinya pada aset yang sedang tren."""
        biasa = KonteksPemicu(
            peristiwa_pindai=(
                _peristiwa(EventKind.BREAKOUT, AMBANG_BESAR - 0.1, 1.0),
            )
        )

        assert deteksi(biasa) == frozenset()

    def test_volume_naik_tapi_belum_ekstrem_tetap_diam(self) -> None:
        naik = KonteksPemicu(
            peristiwa_pindai=(
                _peristiwa(
                    EventKind.VOLUME_SPIKE, 1.1, ANOMALY_VOLUME_RATIO - 0.5
                ),
            )
        )

        assert deteksi(naik) == frozenset()


class TestTiapPemicuBisaMenyala:
    def test_breakout_besar(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKOUT, AMBANG_BESAR, 1.0),)
        )

        assert Peristiwa.BREAKOUT_BESAR in deteksi(k)

    def test_breakdown_besar(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKDOWN, AMBANG_BESAR, 1.0),)
        )

        assert Peristiwa.BREAKDOWN_BESAR in deteksi(k)

    def test_volume_ekstrem_memakai_ambang_regime(self) -> None:
        """`ANOMALY_VOLUME_RATIO`, bukan ambang pemindai (3,0) dan bukan angka
        baru. Diimpor dari sumbernya supaya test ini ikut berubah kalau
        angkanya berubah."""
        k = KonteksPemicu(
            peristiwa_pindai=(
                _peristiwa(EventKind.VOLUME_SPIKE, 1.4, ANOMALY_VOLUME_RATIO),
            )
        )

        assert Peristiwa.VOLUME_EKSTREM in deteksi(k)

    def test_volatilitas_abnormal_dari_pemindai(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.VOLATILITY_SPIKE, 1.0, 2.5),)
        )

        assert Peristiwa.VOLATILITAS_ABNORMAL in deteksi(k)

    def test_volatilitas_abnormal_dari_regime(self) -> None:
        k = KonteksPemicu(regime_sekarang=Regime.HIGH_VOLATILITY)

        assert Peristiwa.VOLATILITAS_ABNORMAL in deteksi(k)

    def test_anomali_funding_memakai_extreme_rate(self) -> None:
        assert Peristiwa.ANOMALI_FUNDING in deteksi(
            KonteksPemicu(funding_rate=EXTREME_RATE)
        )

    def test_funding_negatif_ekstrem_juga_menyala(self) -> None:
        """Funding sangat negatif adalah anomali yang sama besarnya - short
        yang membayar long. Tanda yang tidak diabsolutkan membuat separuh
        anomali tak terlihat."""
        assert Peristiwa.ANOMALI_FUNDING in deteksi(
            KonteksPemicu(funding_rate=-EXTREME_RATE)
        )

    def test_anomali_open_interest_memakai_significant_pct(self) -> None:
        assert Peristiwa.ANOMALI_OPEN_INTEREST in deteksi(
            KonteksPemicu(perubahan_oi_pct=SIGNIFICANT_PCT)
        )

    def test_open_interest_turun_tajam_juga_menyala(self) -> None:
        assert Peristiwa.ANOMALI_OPEN_INTEREST in deteksi(
            KonteksPemicu(perubahan_oi_pct=-SIGNIFICANT_PCT)
        )

    def test_selisih_pendapat_menuntut_dua_kali_ambang_council(self) -> None:
        """**Koreksi atas versi pertama.** Ia memakai `HIGH_DISAGREEMENT` apa
        adanya, dengan alasan yang benar tapi untuk pertanyaan yang salah: itu
        ambang council untuk memutuskan kapan ronde adversarial digelar, bukan
        untuk "selisihnya luar biasa".

        Terukur 2026-08-22 atas 2.527 sesi: median 0,29, dan ambang 0,40
        menyaring **37%** sesi. Sepertiga dari semua keputusan bukan *strong*
        disagreement, dan pemicu yang menyala sesering itu membatalkan bagian
        16.2 alih-alih memenuhinya.
        """
        assert Peristiwa.SELISIH_PENDAPAT_TAJAM not in deteksi(
            KonteksPemicu(disagreement=HIGH_DISAGREEMENT)
        )
        assert Peristiwa.SELISIH_PENDAPAT_TAJAM in deteksi(
            KonteksPemicu(disagreement=AMBANG_SELISIH_TAJAM)
        )

    def test_ambangnya_diturunkan_dari_milik_council(self) -> None:
        """Digandakan, bukan diganti angka lepas: kalau council menggeser
        ambangnya, yang ini ikut bergeser. Satu sumber tetap satu sumber."""
        assert AMBANG_SELISIH_TAJAM == HIGH_DISAGREEMENT * 2

    def test_ketidakpastian_memakai_min_quality(self) -> None:
        """Mutu di bawah garis yang ARUNA sendiri pakai untuk "cukup yakin
        bertindak". Di bawah itu, ketidakpastiannya sudah terukur."""
        assert Peristiwa.KETIDAKPASTIAN_TINGGI in deteksi(
            KonteksPemicu(mutu=MIN_QUALITY - 1)
        )

    def test_mutu_tepat_di_ambang_belum_tidak_pasti(self) -> None:
        assert deteksi(KonteksPemicu(mutu=MIN_QUALITY)) == frozenset()

    def test_berita_besar_dari_hitungan(self) -> None:
        assert Peristiwa.BERITA_BESAR in deteksi(KonteksPemicu(berita_penting=1))

    def test_berita_besar_dari_peristiwa_pindai(self) -> None:
        k = KonteksPemicu(peristiwa_pindai=(_peristiwa(EventKind.NEWS, 1.0, 1.0),))

        assert Peristiwa.BERITA_BESAR in deteksi(k)


class TestPerubahanRegime:
    def test_pindah_keluarga_itu_perubahan(self) -> None:
        k = KonteksPemicu(
            regime_sebelumnya=Regime.RANGING,
            regime_sekarang=Regime.TRENDING_BULLISH,
        )

        assert Peristiwa.PERUBAHAN_REGIME in deteksi(k)

    def test_arah_membalik_itu_perubahan(self) -> None:
        """Bullish ke bearish tak terlihat oleh `keluarga` - keduanya serumpun
        TRENDING - padahal justru itu perubahan yang paling besar."""
        k = KonteksPemicu(
            regime_sebelumnya=Regime.TRENDING_BULLISH,
            regime_sekarang=Regime.TRENDING_BEARISH,
        )

        assert Peristiwa.PERUBAHAN_REGIME in deteksi(k)

    def test_lintas_generasi_taksonomi_bukan_perubahan(self) -> None:
        """`TRENDING` lama dan `TRENDING_BULLISH` baru adalah dua generasi
        taksonomi, bukan pasar yang berubah. 9.897 baris `market_memories`
        menyimpan bentuk lamanya; menghitungnya sebagai perubahan menyalakan
        pemicu ini pada hampir tiap aset."""
        k = KonteksPemicu(
            regime_sebelumnya=Regime.TRENDING,
            regime_sekarang=Regime.TRENDING_BULLISH,
        )

        assert Peristiwa.PERUBAHAN_REGIME not in deteksi(k)

    def test_regime_sebelumnya_tidak_diketahui_bukan_perubahan(self) -> None:
        """Aset yang baru dipantau tidak sedang berubah regime - ia belum
        punya regime sebelumnya. `None` berarti tidak diukur, bukan berbeda."""
        k = KonteksPemicu(regime_sekarang=Regime.TRENDING_BULLISH)

        assert Peristiwa.PERUBAHAN_REGIME not in deteksi(k)

    @pytest.mark.parametrize(
        ("sebelum", "sekarang"),
        [
            (Regime.RANGING, Regime.UNCERTAIN),
            (Regime.UNCERTAIN, Regime.RANGING),
            (Regime.TRENDING_BULLISH, Regime.UNCERTAIN),
            (Regime.UNCERTAIN, Regime.TRENDING_BEARISH),
        ],
    )
    def test_uncertain_bukan_regime(self, sebelum, sekarang) -> None:
        """**Bug produksi, 2026-08-22.** `UNCERTAIN` adalah classifier yang
        mengaku tidak tahu, bukan keadaan pasar - dan `UNCERTAIN.keluarga`
        adalah dirinya sendiri, jadi tiap peralihan yang menyentuhnya terbaca
        sebagai perpindahan keluarga. Ia 15,1% dari seluruh bacaan 15m.

        Repositori sudah membuangnya dari riwayat, tapi `deteksi` dan
        `KonteksPemicu` keduanya publik: dijaga di satu tempat saja berarti
        pemanggil berikutnya bisa menulis ulang bug yang sama.
        """
        k = KonteksPemicu(regime_sebelumnya=sebelum, regime_sekarang=sekarang)

        assert Peristiwa.PERUBAHAN_REGIME not in deteksi(k)


class TestEfekOrdeDua:
    """Bagian 16.2 butir terakhir, bagian 16.8."""

    def test_satu_pemicu_belum_orde_dua(self) -> None:
        k = KonteksPemicu(funding_rate=EXTREME_RATE)

        assert deteksi(k) == frozenset({Peristiwa.ANOMALI_FUNDING})

    def test_dua_pemicu_menyalakan_orde_dua(self) -> None:
        """Efek orde-dua adalah akibat dari akibat: ia butuh yang kedua untuk
        berinteraksi dengan yang pertama."""
        k = KonteksPemicu(
            funding_rate=EXTREME_RATE, perubahan_oi_pct=SIGNIFICANT_PCT
        )

        assert Peristiwa.EFEK_ORDE_DUA in deteksi(k)

    def test_orde_dua_tidak_memicu_dirinya_sendiri(self) -> None:
        """Ditambahkan setelah hitungan, bukan sebelumnya - kalau tidak, satu
        pemicu tunggal plus orde-dua sudah berjumlah dua dan pasalnya berlaku
        untuk tiap peristiwa apa pun."""
        k = KonteksPemicu(funding_rate=EXTREME_RATE)

        assert Peristiwa.EFEK_ORDE_DUA not in deteksi(k)


class TestPemicuTanpaSumberData:
    """Disebut, bukan dihilangkan.

    Pemicu yang hilang dari daftar terbaca sebagai pemicu yang sudah
    dipertimbangkan dan ditolak. Kedua ini belum dipertimbangkan - datanya
    memang belum ada.
    """

    def test_tidak_ada_lagi_yang_tanpa_sumber(self) -> None:
        """Daftar ini kosong sejak 2026-08-23, dan dua yang terakhir keluar
        dengan cara yang sama: bukan dengan menunggu sumber yang ditarik venue,
        melainkan dengan menemukan bacaan yang datanya sudah tersimpan.

        `KONFLIK_LINTAS_PASAR` menjadi "aset yang melawan kohortnya";
        `LONJAKAN_LIKUIDASI` menjadi "gerak keras dengan OI menyusut".
        """
        assert not TANPA_SUMBER_DATA

    def test_daftarnya_tetap_ada_walau_kosong(self) -> None:
        """Menghapusnya menghilangkan tempat bertanya "apakah masih ada pemicu
        tanpa sumber". Pertanyaan itu perlu punya jawaban yang bisa diperiksa,
        bukan disimpulkan dari ketiadaan."""
        from aruna.scenario import pemicu as modul

        assert hasattr(modul, "TANPA_SUMBER_DATA")


class TestLonjakanLikuidasi:
    """Bagian 16.2 "liquidation spike", dibaca sebagai penutupan paksa.

    Uang baru MEMBUKA posisi; uang yang lari MENUTUPNYA. Gerak keras yang
    dibarengi open interest menyusut berarti yang menggerakkannya posisi yang
    keluar - dan itu sidik jari likuidasi, terbaca dari deret yang sudah
    disimpan tanpa endpoint yang ditarik venue.
    """

    def test_tembusan_dengan_oi_menyusut_menyala(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKOUT, 1.0, 1.0),),
            perubahan_oi_pct=-SIGNIFICANT_PCT,
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI in deteksi(k)

    def test_terjun_dengan_oi_menyusut_menyala(self) -> None:
        """Dua arah. Long yang terlempar saat harga jatuh dan short yang
        tertekan saat harga melesat sama-sama penutupan paksa; memilih satu
        berarti menyelundupkan arah ke dalam pemicu (bagian 16.18)."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKDOWN, 1.0, 1.0),),
            perubahan_oi_pct=-SIGNIFICANT_PCT,
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI in deteksi(k)

    def test_oi_yang_TUMBUH_bukan_likuidasi(self) -> None:
        """Ujung yang sebenarnya dijaga. Gerak keras dengan OI NAIK adalah uang
        baru yang masuk - kebalikan dari likuidasi. Menyalakannya di sini akan
        membuat pemicunya menyala pada setiap tembusan bervolume."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKOUT, 3.0, 1.0),),
            perubahan_oi_pct=SIGNIFICANT_PCT * 3,
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI not in deteksi(k)

    def test_oi_menyusut_tanpa_gerak_harga_bukan_likuidasi(self) -> None:
        """Posisi yang ditutup pelan-pelan bukan posisi yang dipaksa keluar."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.VOLUME_SPIKE, 1.4, 9.9),),
            perubahan_oi_pct=-SIGNIFICANT_PCT * 5,
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI not in deteksi(k)

    def test_oi_tak_terbaca_tidak_menyala(self) -> None:
        """`None` berarti futures-loop belum menuliskan bacaan kedua - bukan
        berarti OI-nya diam."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKDOWN, 3.0, 1.0),),
            perubahan_oi_pct=None,
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI not in deteksi(k)

    def test_ambangnya_dipinjam_dari_pertanyaan_yang_sama(self) -> None:
        """`SIGNIFICANT_PCT` memang berarti "pergeseran nyata pada berapa posisi
        yang terbuka". Tidak ada angka baru yang dikarang - dan meminjam ambang
        untuk pertanyaan yang BERBEDA sudah dua kali jadi bug di proyek ini."""
        tepat_di_bawah = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKOUT, 1.0, 1.0),),
            perubahan_oi_pct=-SIGNIFICANT_PCT + Decimal("0.01"),
        )

        assert Peristiwa.LONJAKAN_LIKUIDASI not in deteksi(tepat_di_bawah)

    def test_likuidasi_memang_tidak_bisa_diukur(self) -> None:
        """Bukan kelalaian: Binance menarik endpoint REST-nya, dan adapternya
        memulangkan daftar kosong dengan sengaja. Test ini menempel pada
        sumbernya, jadi ia berubah merah begitu stream `forceOrder` dipasang -
        dan itulah saat pemicu ini harus dihidupkan."""
        import inspect

        from aruna.futures.binance import BinanceFuturesProvider

        sumber = inspect.getsource(BinanceFuturesProvider.liquidations)

        assert "return []" in sumber

    def test_tidak_ada_pemicu_mati_yang_diam_diam_menyala(self) -> None:
        """Kalau salah satunya kelak dihubungkan ke data tanpa dikeluarkan dari
        `TANPA_SUMBER_DATA`, daftar itu jadi bohong. Ini yang memergokinya."""
        penuh = KonteksPemicu(
            peristiwa_pindai=(
                _peristiwa(EventKind.BREAKOUT, AMBANG_BESAR, 1.0),
                _peristiwa(EventKind.VOLUME_SPIKE, 1.4, ANOMALY_VOLUME_RATIO),
                _peristiwa(EventKind.VOLATILITY_SPIKE, 1.0, 2.5),
                _peristiwa(EventKind.NEWS, 1.0, 1.0),
            ),
            regime_sebelumnya=Regime.RANGING,
            regime_sekarang=Regime.HIGH_VOLATILITY,
            disagreement=1.0,
            mutu=0,
            berita_penting=5,
            funding_rate=Decimal("1"),
            perubahan_oi_pct=Decimal("100"),
        )

        assert not (deteksi(penuh) & TANPA_SUMBER_DATA)


class TestKonflikDenganKohort:
    """Bacaan §16.2 "cross-market conflict" yang datanya benar-benar ada.

    Bacaan harfiahnya - CRYPTO melawan IDX pada satu titik waktu - hampir tidak
    pernah tersedia: IDX tutup saat sebagian besar pemindaian crypto berjalan.
    Menunggu bacaan itu berarti membiarkan pemicunya mati selamanya.
    """

    def test_melawan_kohort_menyala(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKDOWN, 1.0, 1.0),),
            arah_kohort=1,
        )

        assert Peristiwa.KONFLIK_LINTAS_PASAR in deteksi(k)

    def test_searah_kohort_tidak_menyala(self) -> None:
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKOUT, 1.0, 1.0),),
            arah_kohort=1,
        )

        assert Peristiwa.KONFLIK_LINTAS_PASAR not in deteksi(k)

    def test_kohort_tanpa_arah_tidak_bisa_dilawan(self) -> None:
        """Pasar yang tidak ke mana-mana tidak bisa dikonfliki siapa pun.
        ``None`` bukan nol."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.BREAKDOWN, 1.0, 1.0),),
            arah_kohort=None,
        )

        assert Peristiwa.KONFLIK_LINTAS_PASAR not in deteksi(k)

    def test_aset_tanpa_arah_tidak_berkonflik(self) -> None:
        """Aset yang cuma melonjak volumenya tidak sedang melawan siapa pun."""
        k = KonteksPemicu(
            peristiwa_pindai=(_peristiwa(EventKind.VOLUME_SPIKE, 1.4, 9.9),),
            arah_kohort=1,
        )

        assert Peristiwa.KONFLIK_LINTAS_PASAR not in deteksi(k)

    def test_menyala_walau_tembusannya_kecil(self) -> None:
        """Konflik soal ARAH, bukan besarnya. Tembusan kecil yang melawan pasar
        tetap konflik - dan menuntut `AMBANG_BESAR` di sini akan membuat pemicu
        ini cuma menyala berbarengan dengan `BREAKDOWN_BESAR`, yang membuatnya
        tidak menambah apa pun."""
        k = KonteksPemicu(
            peristiwa_pindai=(
                _peristiwa(EventKind.BREAKDOWN, AMBANG_BESAR - 0.5, 1.0),
            ),
            arah_kohort=1,
        )

        assert Peristiwa.KONFLIK_LINTAS_PASAR in deteksi(k)


class TestKosakataLengkap:
    def test_tiga_belas_pemicu(self) -> None:
        """Bagian 16.2 menyebut tepat tiga belas. Lebih berarti ada yang
        dikarang; kurang berarti ada yang hilang."""
        assert len(Peristiwa) == 13

    def test_layak_simulasi_saat_ada_pemicu(self) -> None:
        assert layak_simulasi(frozenset({Peristiwa.BREAKOUT_BESAR}))
