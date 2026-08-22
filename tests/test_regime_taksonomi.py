"""Taksonomi regime yang berarah (bagian 2 spec, Gate 1).

Bagian 2 menuntut sembilan regime, dan tiga di antaranya tidak ada:
``TRENDING_BULLISH``, ``TRENDING_BEARISH``, ``BREAKDOWN``. Yang ada hanya
``TRENDING`` dan ``BREAKOUT`` tanpa arah.

**Arahnya sudah diketahui saat pemilihan** - `regime.py` memilih `TRENDING`
untuk `UPTREND` maupun `DOWNTREND` di dua cabang yang bersebelahan, lalu
membuang bedanya. Sama untuk `BREAKOUT_UP` dan `BREAKOUT_DOWN`.

**Kenapa bedanya berarti**, terukur 2026-08-21: di regime `TRENDING`, BUY
menang 49,8% sementara SELL menang 13,8%. Satu regime yang menampung keduanya
membuat bobot agent per-regime tidak bisa membedakan tren naik dari tren turun -
padahal bagian 2 justru menuntut bobot berbeda per regime.

**Dan satu hal yang harus tetap utuh:** 9.897 ingatan lama menyimpan
``TRENDING``. Dimensi REGIME berbobot 4 di mesin kemiripan; kalau baris lama
berhenti cocok dengan yang baru, seluruh korpus itu berhenti bisa dipakai.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import Regime


class TestTaksonomiLengkap:
    @pytest.mark.parametrize(
        "nama",
        [
            "TRENDING_BULLISH", "TRENDING_BEARISH", "RANGING",
            "HIGH_VOLATILITY", "LOW_VOLATILITY", "BREAKOUT", "BREAKDOWN",
            "REVERSAL", "UNCERTAIN",
        ],
    )
    def test_sembilan_regime_bagian_2_ada(self, nama) -> None:
        assert nama in Regime.__members__

    def test_trending_lama_tetap_ada(self) -> None:
        """Dipertahankan **bukan** untuk dipakai classifier lagi, melainkan
        karena 9.897 baris tersimpan memuatnya. Membuangnya dari enum membuat
        setiap pembacaan baris lama meledak."""
        assert "TRENDING" in Regime.__members__


class TestKeluarga:
    """Peta halus -> kasar, supaya baris lama tetap terbaca."""

    def test_keduanya_berkeluarga_trending(self) -> None:
        assert Regime.TRENDING_BULLISH.keluarga is Regime.TRENDING
        assert Regime.TRENDING_BEARISH.keluarga is Regime.TRENDING

    def test_breakdown_berkeluarga_breakout(self) -> None:
        assert Regime.BREAKDOWN.keluarga is Regime.BREAKOUT

    def test_yang_tidak_punya_keluarga_adalah_dirinya(self) -> None:
        assert Regime.RANGING.keluarga is Regime.RANGING
        assert Regime.TRENDING.keluarga is Regime.TRENDING

    def test_setiap_anggota_punya_keluarga(self) -> None:
        """Anggota baru yang lupa dipetakan akan diam-diam jadi dirinya
        sendiri; test ini memaksa keputusannya disengaja."""
        for r in Regime:
            assert isinstance(r.keluarga, Regime)


class TestArahnya:
    def test_arah_terbaca_dari_regime(self) -> None:
        assert Regime.TRENDING_BULLISH.naik is True
        assert Regime.TRENDING_BEARISH.naik is False
        assert Regime.BREAKOUT.naik is True
        assert Regime.BREAKDOWN.naik is False

    def test_yang_tak_berarah_memulangkan_none(self) -> None:
        """`None`, bukan `False`: "tidak berarah" dan "arahnya turun" adalah
        dua hal yang sangat berbeda, dan menyatukannya membuat setiap
        RANGING terbaca bearish."""
        for r in (Regime.RANGING, Regime.UNCERTAIN, Regime.TRENDING):
            assert r.naik is None


class TestClassifierMenghasilkanArah:
    """Penjaga enum tidak cukup: anggota yang ada dan tidak pernah dihasilkan
    persis keadaan `HIGH_VOLATILITY` selama ini - terdefinisi, bisa dihitung,
    dan nol baris memakainya karena selalu kalah argmax."""

    def _regime(self, closes: list[float]):
        from tests.test_council import _context

        return _context(closes).regime.regime

    def test_pasar_naik_menghasilkan_bullish(self) -> None:
        from tests.test_council import RISING

        assert self._regime(RISING) is Regime.TRENDING_BULLISH

    def test_pasar_turun_menghasilkan_bearish(self) -> None:
        """Kemiringannya sengaja landai.

        Kemiringannya sama dengan `RISING` (1,5 per bar) tapi dari harga yang
        lebih tinggi, dan kedua batasnya nyata:

        * mulai dari 220 dengan kemiringan itu menghasilkan `ANOMALY` -
          penyebut persentase mengecil saat harga turun, jadi gerak per-window
          melewati `ANOMALY_MOVE_PCT` = 12% padahal kemiringan mutlaknya sama;
        * melandaikannya ke 0,4 per bar menghasilkan `REVERSAL` - geraknya
          terlalu kecil untuk terbaca sebagai struktur tren.

        500 dengan kemiringan 1,5 berada di antara keduanya.
        """
        turun = [float(500 - i * 1.5) for i in range(80)]

        assert self._regime(turun) is Regime.TRENDING_BEARISH

    def test_trending_tanpa_arah_tidak_pernah_dihasilkan_lagi(self) -> None:
        """Kalau ia masih keluar, taksonominya belum benar-benar berpisah.

        **Penjaga ini bukan pelengkap.** Cabut-uji membuktikannya: mengembalikan
        cabang STRUKTUR ke `Regime.TRENDING` tidak membuat satu pun test
        perilaku di atas merah - kedua deret uji itu ternyata digerakkan oleh
        suara MOMENTUM, dan cabang struktur tidak pernah tersentuh olehnya.
        Tanpa penjaga AST ini, separuh classifier bisa dikembalikan ke ember
        tanpa arah tanpa ada yang memberi tahu.
        """
        import ast
        import inspect

        from aruna.analysis import regime as modul

        pohon = ast.parse(inspect.getsource(modul))
        dipilih = {
            n.args[0].attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "vote"
            and n.args
            and isinstance(n.args[0], ast.Attribute)
        }

        assert "TRENDING" not in dipilih
        assert {"TRENDING_BULLISH", "TRENDING_BEARISH", "BREAKDOWN"} <= dipilih


class TestIngatanLamaTetapCocok:
    """Bagian yang paling mudah rusak diam-diam."""

    def test_trending_lama_cocok_dengan_yang_berarah(self) -> None:
        """Baris lama tidak tahu arahnya. Menolak kecocokan berarti membuang
        9.897 ingatan; menerimanya berarti kecocokan yang lebih longgar tapi
        jujur - dan bukti yang longgar lebih baik daripada tidak ada bukti."""
        from aruna.memory.dimensions import sama

        assert sama("TRENDING", "TRENDING_BULLISH")
        assert sama("TRENDING_BEARISH", "TRENDING")

    def test_breakout_lama_TIDAK_bisa_dipulihkan_arahnya(self) -> None:
        """Batas jujur dari perubahan ini, dan ia tidak bisa dihilangkan.

        `TRENDING` aman: ia nama kasar yang classifier tidak pernah pakai lagi,
        jadi baris lama bisa dikenali sebagai lama. `BREAKOUT` tidak - bagian 2
        memasangkannya dengan `BREAKDOWN`, jadi mulai sekarang ia berarti
        "tembus ke ATAS", sementara 2.261 baris lama memakainya untuk kedua
        arah.

        Baris lama yang sebenarnya breakdown akan terbaca sebagai breakout
        naik, dan tidak ada cara membedakannya - arahnya tidak tersimpan di
        mana pun. Yang bisa dilakukan hanya menyebutnya, dan test ini yang
        menyebutnya.
        """
        from aruna.memory.dimensions import sama

        assert not sama("BREAKOUT", "BREAKDOWN")
        # Dan inilah kerusakannya: baris lama yang breakdown kini cocok dengan
        # breakout naik, karena keduanya string yang sama persis.
        assert sama("BREAKOUT", "BREAKOUT")

    def test_dua_arah_berlawanan_TIDAK_cocok(self) -> None:
        """Inti seluruh pemisahan ini. Kalau tren naik cocok dengan tren turun,
        tidak ada yang bertambah dan seluruh perubahan ini sia-sia."""
        from aruna.memory.dimensions import sama

        assert not sama("TRENDING_BULLISH", "TRENDING_BEARISH")

    def test_regime_yang_beda_keluarga_tidak_cocok(self) -> None:
        from aruna.memory.dimensions import sama

        assert not sama("TRENDING_BULLISH", "RANGING")
        assert not sama("BREAKOUT", "REVERSAL")

    def test_unknown_tetap_tidak_pernah_cocok(self) -> None:
        from aruna.memory.dimensions import UNKNOWN, sama

        assert not sama(UNKNOWN, "TRENDING_BULLISH")
        assert not sama(UNKNOWN, UNKNOWN)

    def test_dimensi_bukan_regime_tetap_persis(self) -> None:
        """Kelonggaran ini khusus regime. Kalau ia bocor ke dimensi lain,
        dimensi yang seharusnya ketat berhenti membedakan apa pun."""
        from aruna.memory.dimensions import sama

        assert not sama("HIGH", "MEDIUM")
        assert sama("HIGH", "high")
