"""Korpus keputusan lintas regime (2026-08-25).

Setiap pertanyaan tentang "agen mana yang menyumbang" sebelumnya berhenti di
korpus hidup yang delapan hari dan satu regime. Modul ini memutar ulang council
di candle yang sudah tersimpan - 506 hari, tujuh belas bulan, empat regime - dan
mengubah pertanyaan itu dari "belum bisa dijawab" menjadi terukur.

Yang dijaga di sini bukan angkanya, melainkan sifat-sifat yang membuat angkanya
berarti: garis dasar dihitung per KEPUTUSAN bukan per opini, edge diukur
terhadap garis dasar, dan berita tidak pernah bocor ke masa lalu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aruna.backtest.korpus import Korpus, Opini, bangun
from aruna.backtest.window import MIN_BARS

AWAL = datetime(2026, 1, 1, tzinfo=UTC)


def _opini(agen: str, arah: str, gerak: float, *, jam: int = 0) -> Opini:
    return Opini(
        symbol="BTC/USDT", pada=AWAL + timedelta(hours=jam), agen=agen,
        arah=arah, keyakinan=0.6, council=arah, gerak_pct=gerak,
    )


def _candle(n: int, *, awal: float = 100.0, langkah: float = 1.0) -> list[dict]:
    baris = []
    harga = awal
    for i in range(n):
        buka = AWAL + timedelta(days=i)
        baris.append({
            "open_time": buka.replace(tzinfo=None),
            "close_time": (buka + timedelta(days=1)).replace(tzinfo=None),
            "open": harga, "high": harga * 1.02, "low": harga * 0.98,
            "close": harga + langkah, "volume": 1000.0,
        })
        harga += langkah
    return baris


class TestGarisDasarPerKeputusan:
    def test_tidak_dihitung_per_opini(self) -> None:
        """**Kalau garis dasar dihitung per opini, keputusan yang agennya
        banyak bersuara akan menimbang lebih berat** - dan itu memiringkan
        pembanding yang seluruh klaim edge bersandar padanya.

        Di bawah: satu keputusan naik disuarakan empat agen, satu keputusan
        turun disuarakan satu agen. Per opini garis dasarnya 80%; per keputusan
        50%, dan yang kedua yang benar.
        """
        korpus = Korpus(opini=[
            _opini("A", "BUY", +1.0), _opini("B", "BUY", +1.0),
            _opini("C", "BUY", +1.0), _opini("D", "BUY", +1.0),
            _opini("A", "SELL", -1.0, jam=1),
        ])

        assert len(korpus.keputusan) == 2
        assert korpus.garis_dasar == 0.5

    def test_korpus_kosong_tidak_meledak(self) -> None:
        assert Korpus().garis_dasar is None
        assert Korpus().edge("A", "BUY") == (None, 0)


class TestEdgeBukanAkurasi:
    def test_selalu_buy_di_pasar_naik_beredge_nol(self) -> None:
        """Akurasi 100% dan sumbangan nol adalah keadaan yang sama di sini:
        garis dasarnya juga 100%."""
        korpus = Korpus(opini=[
            _opini("A", "BUY", +1.0, jam=i) for i in range(10)
        ])

        nilai, n = korpus.edge("A", "BUY")
        assert n == 10
        assert nilai == 0.0

    def test_benar_lebih_sering_daripada_pasar_beredge_positif(self) -> None:
        # Pasar naik pada separuh keputusan; agen A hanya bersuara BUY pada
        # yang naik, jadi ia memilih saat - bukan mengikuti arus.
        opini = [_opini("A", "BUY", +1.0, jam=i) for i in range(5)]
        opini += [_opini("B", "SELL", -1.0, jam=10 + i) for i in range(5)]

        korpus = Korpus(opini=opini)
        assert korpus.garis_dasar == 0.5

        nilai, _ = korpus.edge("A", "BUY")
        assert nilai == 50.0

    def test_tak_berarah_tidak_punya_benar(self) -> None:
        """WAIT bukan tebakan yang salah - ia bukan tebakan."""
        assert _opini("A", "WAIT", +1.0).benar is None
        assert _opini("A", "BUY", +1.0).benar is True
        assert _opini("A", "BUY", -1.0).benar is False
        assert _opini("A", "SELL", -1.0).benar is True


class TestPutarUlang:
    def test_menghasilkan_opini_dari_candle(self) -> None:
        korpus = bangun({"BTC/USDT": _candle(MIN_BARS + 20)})

        assert korpus.opini, "council tidak menghasilkan satu opini pun"
        assert korpus.garis_dasar is not None
        assert all(o.symbol == "BTC/USDT" for o in korpus.opini)

    def test_deret_terlalu_pendek_dilewati_bukan_meledak(self) -> None:
        korpus = bangun({"BTC/USDT": _candle(5)})

        assert korpus.opini == []
        assert korpus.gagal == 0

    def test_berita_tidak_pernah_dioper(self) -> None:
        """**Look-ahead paling parah, dan paling mudah tak sengaja.** Berita
        justru sebab harga bergerak; memakai berita hari ini untuk menilai
        keputusan tahun lalu akan menghasilkan agen yang terlihat jenius.
        """
        import inspect

        from aruna.backtest import korpus as modul

        sumber = inspect.getsource(modul.bangun)
        assert "news=()" in sumber
        assert "fundamentals=None" in sumber

    def test_gerak_dibaca_ke_DEPAN_bukan_ke_belakang(self) -> None:
        """Arah yang terbalik di sini akan membuat setiap agen terbaca
        terbalik, dan seluruh kesimpulan tentang edge ikut terbalik."""
        naik = bangun({"X/USDT": _candle(MIN_BARS + 10, langkah=+1.0)})
        turun = bangun({"X/USDT": _candle(MIN_BARS + 10, langkah=-1.0)})

        assert naik.garis_dasar == 1.0
        assert turun.garis_dasar == 0.0
