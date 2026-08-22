"""PASAL 15.3: kalau datanya tidak ada, UNKNOWN - dan UNKNOWN bukan kecocokan.

Terukur 2026-08-21: tujuh dari dimensi yang PASAL 15.5 sebut - volatility,
volume, momentum, trend, open interest, funding, price structure - tidak punya
satu pun kolom historis. ``risk_history`` ada dan kosong. Untuk seluruh 8.914
rekaman lama ketujuhnya UNKNOWN selamanya, dan tidak ada backfill yang bisa
menghidupkannya: datanya memang tidak pernah ditulis.

Kalau UNKNOWN dihitung sebagai kecocokan, sidik jari yang membandingkan tujuh
ketiadaan dengan tujuh ketiadaan melaporkan kemiripan sempurna terhadap dua
kondisi yang tidak diketahui sama sekali. Itu angka meyakinkan tanpa dasar, dan
§13.26 melarangnya. Berkas ini yang menahan pintu itu.
"""

from __future__ import annotations

from aruna.memory.dimensions import (
    TAK_TERSIMPAN,
    TERSIMPAN,
    UNKNOWN,
    Dimensi,
    diketahui,
    sama,
)


class TestKetidaktahuan:
    def test_unknown_tidak_pernah_sama_dengan_unknown(self) -> None:
        """Dua kondisi yang sama-sama tidak diketahui bukan dua kondisi yang
        mirip - mereka dua kondisi yang tidak ada yang tahu."""
        assert not sama(UNKNOWN, UNKNOWN)

    def test_dua_kolom_kosong_bukan_kecocokan(self) -> None:
        """Bentuk kedua dari cacat yang sama, dan yang benar-benar keluar dari
        database: kolom NULL bertemu kolom NULL.

        Versi pertama test ini berbunyi ``not sama(UNKNOWN, "TRENDING")`` dan
        tetap hijau saat penjaganya dicabut - dua string yang memang berbeda
        toh tidak cocok. Yang di bawah ini cocok tanpa penjaganya: ``"" == ""``
        dan ``None == None`` keduanya benar.
        """
        assert not sama("", "")
        assert not sama(None, None)
        assert not sama(UNKNOWN, "TRENDING")
        assert not sama("TRENDING", UNKNOWN)

    def test_none_dan_kosong_dibaca_sebagai_tidak_diketahui(self) -> None:
        """None dan string kosong yang benar-benar keluar dari kolom database
        yang NULL, jadi keduanya harus terbaca sebagai ketiadaan di sini."""
        assert not diketahui(None)
        assert not diketahui("")
        assert not diketahui("   ")
        assert not diketahui(UNKNOWN)

    def test_nol_dihitung_diketahui(self) -> None:
        """``confidence=0`` berarti council menilai dan hasilnya nol, bukan
        berarti confidence tidak terbaca. Kelas kesalahan yang sama dengan
        ``side='FLAT'`` yang truthy - sudah empat kali muncul di sistem ini."""
        assert diketahui(0)
        assert diketahui(0.0)

    def test_nilai_yang_sama_cocok_tanpa_peduli_huruf(self) -> None:
        assert sama("trending", "TRENDING")


class TestDaftarDimensi:
    def test_tidak_ada_dimensi_yang_dua_kali(self) -> None:
        assert len(TERSIMPAN | TAK_TERSIMPAN) == len(Dimensi)

    def test_keduanya_tidak_beririsan(self) -> None:
        """Sebuah dimensi tidak bisa sekaligus tersimpan dan tidak tersimpan;
        kalau ia di dua daftar, tidak ada yang tahu apakah ia boleh dipakai
        menghitung kemiripan."""
        assert not (TERSIMPAN & TAK_TERSIMPAN)

    def test_yang_tersimpan_memang_yang_terukur_terisi(self) -> None:
        """Kelima ini terisi 95-100% pada 8.914 baris ``signal_snapshots`` -
        itu sebabnya mereka boleh membentuk sidik jari."""
        for d in (Dimensi.REGIME, Dimensi.RISK_LEVEL, Dimensi.NEWS,
                  Dimensi.QUALITY, Dimensi.TIMEFRAME):
            assert d in TERSIMPAN

    def test_yang_bisa_dihitung_ulang_pindah_ke_tersimpan(self) -> None:
        """Kelimanya semula ``TAK_TERSIMPAN`` dengan alasan yang benar waktu
        itu: tidak ada kolomnya. Ternyata mereka **tidak perlu kolom** -
        ``realised_volatility``, ``momentum``, ``volume_anomaly``, dan
        ``analyse_structure`` berjalan atas candle yang sudah tersimpan, dan
        dihitung ulang pada bar yang tersedia saat keputusan itu dibuat.

        Dipindahkan 2026-08-21, sesudah evaluasi PASAL 15.44 melaporkan selisih
        +3 poin: sidik jari berdimensi delapan tidak cukup membedakan satu
        kondisi pasar dari yang lain."""
        for d in (Dimensi.VOLATILITY, Dimensi.VOLUME, Dimensi.MOMENTUM,
                  Dimensi.TREND, Dimensi.STRUCTURE):
            assert d in TERSIMPAN

    def test_yang_benar_benar_tidak_ada_tetap_disebut_namanya(self) -> None:
        """Open interest dan funding: data venue perpetual yang tidak pernah
        disimpan per keputusan dan tidak bisa diturunkan dari candle spot.

        Didaftar, bukan dihilangkan - sebuah dimensi yang dihapus dari enum
        tidak akan pernah muncul sebagai UNKNOWN di laporan mana pun, dan
        ketiadaannya berhenti terlihat."""
        assert {Dimensi.OPEN_INTEREST, Dimensi.FUNDING} == TAK_TERSIMPAN
