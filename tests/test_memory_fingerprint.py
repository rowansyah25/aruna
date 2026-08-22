"""PASAL 15.5: sidik jari yang MEMBANDINGKAN, bukan yang mengunci.

``signal_snapshots.fingerprint`` sudah ada dan berisi SHA-256 64 karakter - dan
ia menjawab pertanyaan yang berbeda: *"apakah ini signal yang sama?"*. Sebuah
hash tidak bisa menjawab *"apakah ini pasar yang mirip?"*, karena dua kondisi
yang nyaris identik menghasilkan hash yang sama sekali berbeda.

Keduanya harus hidup berdampingan dengan nama berbeda; menimpa yang lama akan
merusak ``FuturesRepository.verify()`` yang membuktikan baris yang dinilai
adalah baris yang diterbitkan. Berkas ini menjaga yang baru, dan menjaga bahwa
ia tidak menyamar jadi yang lama.
"""

from __future__ import annotations

from decimal import Decimal

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik, band_kualitas, band_likuiditas, band_news

#: Baris `signal_snapshots` yang sungguhan, disalin apa adanya dari produksi
#: 2026-08-20 (id 9070). Palsu yang bentuknya karangan sudah dua kali membuat
#: suite hijau di atas bug produksi di proyek ini.
BARIS = {
    "symbol": "XRP/USDT",
    "market_code": "CRYPTO",
    "horizon_code": "15m",
    "regime": "TRENDING",
    "risk_level": "MODERATE",
    "news_state": "1 item(s): 0+ / 0- / 1 unreadable",
    "signal_quality": 57,
    "spread_bps": Decimal("0.8091"),
}


class TestDariBarisDatabase:
    def test_dimensi_tersimpan_terbaca_semua(self) -> None:
        s = Sidik.dari_snapshot(BARIS)

        assert s.nilai[Dimensi.REGIME] == "TRENDING"
        assert s.nilai[Dimensi.ASSET] == "XRP/USDT"
        assert s.nilai[Dimensi.TIMEFRAME] == "15m"
        assert s.nilai[Dimensi.RISK_LEVEL] == "MODERATE"
        assert s.nilai[Dimensi.MARKET] == "CRYPTO"

    def test_yang_tidak_pernah_tersimpan_jadi_unknown(self) -> None:
        """Bukan dihilangkan: yang hilang dari sidik jari tidak akan pernah
        muncul sebagai ketiadaan di laporan mana pun."""
        s = Sidik.dari_snapshot(BARIS)

        for d in (Dimensi.VOLATILITY, Dimensi.MOMENTUM, Dimensi.FUNDING,
                  Dimensi.VOLUME, Dimensi.TREND, Dimensi.OPEN_INTEREST,
                  Dimensi.STRUCTURE):
            assert s.nilai[d] == UNKNOWN

    def test_setiap_dimensi_punya_nilainya(self) -> None:
        """Sidik jari dengan kunci yang hilang memaksa tiap pembaca menulis
        `.get(d, UNKNOWN)` sendiri, dan yang lupa akan meledak jauh dari sini."""
        s = Sidik.dari_snapshot(BARIS)

        assert set(s.nilai) == set(Dimensi)

    def test_kolom_null_jadi_unknown_bukan_kosong(self) -> None:
        s = Sidik.dari_snapshot({**BARIS, "regime": None, "signal_quality": None})

        assert s.nilai[Dimensi.REGIME] == UNKNOWN
        assert s.nilai[Dimensi.QUALITY] == UNKNOWN

    def test_yang_diketahui_hanya_yang_benar_benar_terbaca(self) -> None:
        s = Sidik.dari_snapshot({**BARIS, "regime": None})

        assert Dimensi.REGIME not in s.diketahui()
        assert Dimensi.RISK_LEVEL in s.diketahui()
        assert Dimensi.VOLATILITY not in s.diketahui()


class TestDariKondisiSekarang:
    def test_bentuknya_sama_dengan_yang_dari_database(self) -> None:
        """Kalau kondisi sekarang dan rekaman lama menghasilkan bentuk yang
        berbeda, kemiripannya membandingkan dua hal yang tidak sebanding -
        dan tidak ada yang akan melihatnya, karena skornya tetap keluar."""
        sekarang = Sidik.dari_konteks(
            symbol="XRP/USDT", market="CRYPTO", timeframe="15m",
            regime="TRENDING", risk_level="MODERATE",
            news="1 item(s): 0+ / 0- / 1 unreadable",
            quality=57, spread_bps=Decimal("0.8091"),
        )

        assert sekarang.nilai == Sidik.dari_snapshot(BARIS).nilai

    def test_yang_tidak_diberikan_jadi_unknown(self) -> None:
        s = Sidik.dari_konteks(
            symbol="BTC/USDT", market="CRYPTO", timeframe="4h",
            regime=None, risk_level=None, news=None, quality=None,
            spread_bps=None,
        )

        assert s.diketahui() == frozenset(
            {Dimensi.ASSET, Dimensi.MARKET, Dimensi.TIMEFRAME}
        )


class TestBand:
    def test_kualitas_dipetakan_ke_band(self) -> None:
        assert band_kualitas(20) == "LOW"
        assert band_kualitas(57) == "MEDIUM"
        assert band_kualitas(85) == "HIGH"

    def test_kualitas_tak_terbaca_bukan_rendah(self) -> None:
        """Menganggap yang tidak terbaca sebagai LOW akan membuat setiap
        rekaman lama tanpa quality terlihat sebagai setup buruk - kesimpulan
        yang tidak pernah diukur siapa pun."""
        assert band_kualitas(None) == UNKNOWN

    def test_likuiditas_dari_spread(self) -> None:
        """PASAL 15.17: membedakan breakout pada likuiditas kuat dan lemah.
        ``spread_bps`` terisi 99,0% - satu-satunya ukuran likuiditas yang
        benar-benar ada di sejarah."""
        assert band_likuiditas(Decimal("0.8")) == "TIGHT"
        assert band_likuiditas(Decimal("12")) == "NORMAL"
        assert band_likuiditas(Decimal("80")) == "WIDE"

    def test_likuiditas_tak_terbaca_bukan_normal(self) -> None:
        assert band_likuiditas(None) == UNKNOWN


class TestNews:
    def test_lebih_banyak_positif_jadi_positive(self) -> None:
        assert band_news("3 item(s): 2+ / 0- / 1 unreadable") == "POSITIVE"

    def test_lebih_banyak_negatif_jadi_negative(self) -> None:
        assert band_news("4 item(s): 1+ / 3- / 0 unreadable") == "NEGATIVE"

    def test_seimbang_jadi_neutral(self) -> None:
        assert band_news("2 item(s): 1+ / 1- / 0 unreadable") == "NEUTRAL"

    def test_semuanya_tak_terbaca_jadi_unreadable(self) -> None:
        """Berbeda dari NEUTRAL: netral berarti berita dibaca dan tidak
        condong; unreadable berarti beritanya ada dan tidak ada yang tahu
        isinya. Meleburnya membuat hari yang datanya rusak terbaca seperti
        hari yang tenang."""
        assert band_news("1 item(s): 0+ / 0- / 1 unreadable") == "UNREADABLE"

    def test_tidak_ada_berita_terbaru_adalah_pembacaan(self) -> None:
        """Terukur 2026-08-21: ``'NO_RECENT_NEWS'`` adalah bentuk DOMINAN di
        sejarah - 5.980 dari 8.914 baris, 67%. Versi pertama modul ini hanya
        mengenali format ``"1 item(s): 0+ / 0- / 1 unreadable"`` karena itu
        satu-satunya baris contoh yang kubaca, dan dua pertiga korpus jatuh ke
        UNKNOWN.

        Ia **bukan** UNKNOWN: lapisan berita berjalan dan menemukan tidak ada
        apa-apa. Dan bukan NEUTRAL: netral berarti ada berita yang saling
        mengimbangi. Ketiganya keadaan yang berbeda, dan meleburnya membuat
        hari sepi tidak bisa dibedakan dari hari yang beritanya tidak terbaca.
        """
        assert band_news("NO_RECENT_NEWS") == "NO_NEWS"

    def test_bentuk_yang_tidak_dikenali_jadi_unknown(self) -> None:
        """Bukan NEUTRAL. Format yang berubah adalah kegagalan pembacaan, dan
        menerjemahkannya jadi 'netral' akan menyembunyikan kegagalan itu di
        balik nilai yang sah (§13.26)."""
        assert band_news("berita bagus hari ini") == UNKNOWN
        assert band_news(None) == UNKNOWN


class TestDiperkaya:
    """Lima dimensi teknikal masuk lewat pintu ini (PASAL 15.5).

    Dihitung ulang dari candle tersimpan, bukan dibaca dari kolom - lihat
    ``aruna.memory.teknikal``.
    """

    def test_dimensi_tambahan_masuk(self) -> None:
        s = Sidik.dari_snapshot(BARIS).dengan({
            Dimensi.VOLATILITY: "HIGH",
            Dimensi.MOMENTUM: "POSITIVE",
        })

        assert s.nilai[Dimensi.VOLATILITY] == "HIGH"
        assert s.nilai[Dimensi.MOMENTUM] == "POSITIVE"

    def test_yang_lama_tidak_tertimpa(self) -> None:
        s = Sidik.dari_snapshot(BARIS).dengan({Dimensi.VOLATILITY: "HIGH"})

        assert s.nilai[Dimensi.REGIME] == "TRENDING"
        assert s.nilai[Dimensi.ASSET] == "XRP/USDT"

    def test_unknown_tidak_menghapus_yang_sudah_ada(self) -> None:
        """Perkayaan yang gagal tidak boleh mengosongkan sidik jari yang sudah
        terbaca - ia hanya tidak menambah apa-apa."""
        s = Sidik.dari_snapshot(BARIS).dengan({Dimensi.REGIME: UNKNOWN})

        assert s.nilai[Dimensi.REGIME] == "TRENDING"

    def test_hasilnya_sidik_baru_bukan_yang_lama_diubah(self) -> None:
        """``Sidik`` beku. Perkayaan yang menyunting di tempat akan mengubah
        sidik jari yang mungkin sudah dipakai perbandingan lain."""
        asal = Sidik.dari_snapshot(BARIS)
        baru = asal.dengan({Dimensi.VOLATILITY: "HIGH"})

        assert asal.nilai[Dimensi.VOLATILITY] == UNKNOWN
        assert baru is not asal


class TestTidakMenyamarJadiHash:
    def test_sidik_bukan_string(self) -> None:
        """Kalau ini menghasilkan string 64 heksadesimal, seseorang akan
        menukarnya dengan ``signal_snapshots.fingerprint`` dan merusak
        ``verify()``."""
        s = Sidik.dari_snapshot(BARIS)

        assert not isinstance(s.nilai, str)
        assert hasattr(s.nilai, "keys")

    def test_kondisi_yang_beda_tipis_tetap_terbaca_beda_tipis(self) -> None:
        """Yang tidak bisa dilakukan hash: kualitas 57 dan 58 jatuh di band
        yang sama, jadi kemiripannya utuh - sementara hash keduanya sama sekali
        berbeda."""
        a = Sidik.dari_snapshot(BARIS)
        b = Sidik.dari_snapshot({**BARIS, "signal_quality": 58})

        assert a.nilai == b.nilai

    def test_kondisi_yang_benar_benar_beda_terbaca_beda(self) -> None:
        """Penjaga terhadap test di atas: band yang terlalu lebar akan membuat
        semua kondisi terlihat sama, dan test di atas tetap hijau."""
        a = Sidik.dari_snapshot(BARIS)
        b = Sidik.dari_snapshot({**BARIS, "signal_quality": 92})

        assert a.nilai != b.nilai
