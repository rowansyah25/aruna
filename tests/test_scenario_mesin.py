"""Mesin skenario internal (bagian 16.5, 16.6, 16.8).

Empat hal yang dijaga, dan tiga di antaranya adalah syarat agar pasal lain bisa
berlaku sama sekali:

* **Tiga selalu ada** (16.5). Mesin yang kadang menghasilkan dua membuat
  "bandingkan seluruh skenario" (16.9) berlaku atas himpunan yang berubah-ubah.
* **Bobot menjumlah seratus** (16.6). Jumlah yang meleset mengundang pembacanya
  menyimpulkan ada skenario yang tidak dilaporkan.
* **Deterministik** (16.19). Mesin yang berubah tanpa sebab tidak bisa
  dievaluasi: skenario yang salah tidak bisa dibedakan dari skenario lain yang
  kebetulan muncul.
* **Tambahan hanya kalau ada buktinya** (16.5). Likuidasi berantai tanpa data
  likuidasi bukan kehati-hatian melainkan karangan yang berformat benar.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.scenario.mesin import (
    MINIMUM_SKENARIO,
    TANPA_KELUARGA_KERUMUNAN,
    TOTAL_BOBOT,
    VERSI,
    simulasikan,
)
from aruna.scenario.models import CATATAN_BOBOT, LABEL_BUKTI
from aruna.scenario.pemicu import Peristiwa

NOW = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
TEMBUS = frozenset({Peristiwa.BREAKOUT_BESAR})


def _hitungan(pemicu) -> dict[str, int]:
    """Berapa lintasan mendarat di tiap keluarga, dihitung ulang di test.

    Dihitung ulang alih-alih diimpor dari `mesin`, supaya test ini menguji
    hubungan antara kerumunan dan bobot - bukan mengulang perhitungan yang
    sama dari fungsi yang sama.
    """
    from collections import Counter

    from aruna.scenario.kerumunan import klasifikasi, simulasikan_kerumunan

    return dict(Counter(klasifikasi(x) for x in simulasikan_kerumunan(pemicu)))


def _lintasan_dari(s) -> int:
    """Berapa lintasan yang disebut provenansi sebuah skenario."""
    b = next(x for x in s.bukti if x.startswith("kerumunan:"))
    return int(b.split(":")[1].split("/")[0])


def _jalan(pemicu=TEMBUS, **kw):
    dasar = {
        "market": "CRYPTO",
        "asset": "BTC/USDT",
        "kondisi_awal": ("harga > resistance", "volume 4,2x"),
        "bukti": ("struktur: higher high",),
        "pada": NOW,
    }
    return simulasikan(pemicu=pemicu, **(dasar | kw))


class TestTigaSelaluAda:
    """Bagian 16.5."""

    def test_minimal_tiga(self) -> None:
        assert len(_jalan()) >= MINIMUM_SKENARIO

    def test_ketiganya_yang_spec_sebut(self) -> None:
        nama = {s.nama for s in _jalan()}

        assert {"Bullish Continuation", "Bearish Reversal", "False Breakout"} <= nama

    def test_tiga_ada_bahkan_pada_pemicu_paling_sepi(self) -> None:
        """Ketiganya adalah bentuk dasar ketidaktahuan - lanjut, berbalik,
        tipuan - dan tidak butuh bukti untuk pantas dipertimbangkan."""
        hasil = _jalan(pemicu=frozenset({Peristiwa.PERUBAHAN_REGIME}))

        assert len(hasil) >= MINIMUM_SKENARIO


class TestTambahanButuhBukti:
    """Bagian 16.5: yang opsional hanya lahir kalau pemicunya menyala."""

    def test_tanpa_berita_tidak_ada_news_driven(self) -> None:
        nama = {s.nama for s in _jalan()}

        assert "News-Driven Reversal" not in nama

    def test_dengan_berita_ada_news_driven(self) -> None:
        nama = {s.nama for s in _jalan(TEMBUS | {Peristiwa.BERITA_BESAR})}

        assert "News-Driven Reversal" in nama

    def test_cascade_muncul_dari_lintasan_bukan_dari_data_likuidasi(self) -> None:
        """**Bedanya harus tetap terbaca.** Ini bukan laporan bahwa likuidasi
        sedang terjadi - datanya memang belum ada, dan `TANPA_SUMBER_DATA`
        masih menyebutnya. Ini laporan bahwa kerumunan, dijalankan di bawah
        premis-premis yang ada, menghasilkan jalan yang berakhir sebagai
        kaskade.

        Aturan lamanya menggerbangi skenario ini dengan pemicu
        `LONJAKAN_LIKUIDASI` yang tidak pernah menyala, dan akibatnya bobot
        lintasan kaskade lenyap dari keluaran tanpa jejak.
        """
        pemicu = TEMBUS | {Peristiwa.VOLATILITAS_ABNORMAL}
        hitung = _hitungan(pemicu)
        nama = {s.nama for s in _jalan(pemicu)}

        if hitung.get("Liquidation Cascade"):
            assert "Liquidation Cascade" in nama
        else:
            assert "Liquidation Cascade" not in nama

    def test_tidak_ada_skenario_tanpa_lintasan_yang_mendukungnya(self) -> None:
        """Kebalikan dari di atas, dan sama pentingnya: skenario opsional yang
        muncul tanpa satu pun lintasan mendukungnya adalah karangan
        berformat."""
        pemicu = frozenset(Peristiwa)
        hitung = _hitungan(pemicu)
        opsional = {
            s.nama
            for s in _jalan(pemicu)
            if s.nama not in {
                "Bullish Continuation", "Bearish Reversal", "False Breakout"
            }
        }

        for n in opsional - TANPA_KELUARGA_KERUMUNAN:
            assert hitung.get(n), f"{n} muncul tanpa satu pun lintasan"

    def test_bobot_lintasan_tidak_hilang(self) -> None:
        """Cacat yang test di atas lahir darinya: tiap lintasan harus punya
        skenario yang mewakilinya, atau bobotnya lenyap dari keluaran."""
        pemicu = frozenset(Peristiwa)
        hitung = _hitungan(pemicu)
        nama = {s.nama for s in _jalan(pemicu)}

        for keluarga, n in hitung.items():
            if n:
                assert keluarga in nama, f"{n} lintasan {keluarga} tidak dilaporkan"

    def test_orde_dua_menambah_second_order(self) -> None:
        nama = {s.nama for s in _jalan(TEMBUS | {Peristiwa.EFEK_ORDE_DUA})}

        assert "Second-Order Effect" in nama


class TestBobot:
    """Bagian 16.6."""

    @pytest.mark.parametrize(
        "pemicu",
        [
            TEMBUS,
            TEMBUS | {Peristiwa.BERITA_BESAR},
            TEMBUS | {Peristiwa.VOLATILITAS_ABNORMAL, Peristiwa.EFEK_ORDE_DUA},
            frozenset({Peristiwa.KETIDAKPASTIAN_TINGGI}),
            frozenset(Peristiwa),
        ],
    )
    def test_selalu_menjumlah_seratus(self, pemicu) -> None:
        """Termasuk saat pembulatan meleset - selisihnya ditaruh pada yang
        terbesar, bukan dibiarkan menjadi 99 atau 101."""
        assert sum(s.bobot for s in _jalan(pemicu)) == TOTAL_BOBOT

    def test_dilabeli_relatif_di_tiap_keluaran(self) -> None:
        for s in _jalan():
            assert s.to_dict()["bobot_catatan"] == CATATAN_BOBOT

    def test_berlabel_simulation_evidence(self) -> None:
        """Bagian 16.1: bukan FACT, bukan GUARANTEED PREDICTION."""
        for s in _jalan():
            assert s.to_dict()["label"] == LABEL_BUKTI

    def test_keyakinan_sejalan_dengan_bobot(self) -> None:
        """Diturunkan dari bobot, jadi keduanya tidak bisa bertentangan - dua
        angka bebas yang mengaku mengukur hal yang sama akan melenceng."""
        for s in _jalan():
            assert s.keyakinan == pytest.approx(s.bobot / TOTAL_BOBOT)

    def test_tanpa_pemicu_berarah_bobotnya_rata(self) -> None:
        """Bobot yang berat sebelah tanpa bukti berarah adalah tebakan arah
        yang menyamar sebagai perhitungan (bagian 16.18).

        Yang diperiksa dua lapis, dan lapis pertama yang sebenarnya diuji:
        **provenansinya harus identik** - kerumunan mendaratkan jumlah lintasan
        yang sama di kedua keluarga. Bobot bulatnya boleh berbeda satu, karena
        seratus tidak habis dibagi tiga; menuntutnya sama persis berarti
        menuntut aritmetika melakukan yang tidak bisa dilakukannya, dan test
        semacam itu akhirnya dilonggarkan oleh orang yang tidak tahu kenapa ia
        ada.
        """
        hasil = _jalan(frozenset({Peristiwa.PERUBAHAN_REGIME}))
        lanjut = next(s for s in hasil if s.nama == "Bullish Continuation")
        balik = next(s for s in hasil if s.nama == "Bearish Reversal")

        def _lintasan(s):
            return next(b for b in s.bukti if b.startswith("kerumunan:"))

        assert _lintasan(lanjut) == _lintasan(balik)
        assert abs(lanjut.bobot - balik.bobot) <= 1


class TestBobotDariKerumunan:
    """Sampai 2026-08-22 bobot di sini ditetapkan tangan lewat `_GESER = 5.0`
    dan sederet `if`. Tebakan yang rapi, dibela komentar, dan tidak bisa
    dibantah dengan apa pun kecuali tebakan lain.

    Sekarang angkanya dihitung dari simulasi kerumunan. Kelas ini yang
    memastikan hubungan itu nyata - bukan mesin kerumunan yang dipanggil lalu
    hasilnya dibuang, yang adalah cacat paling berulang di proyek ini.
    """

    def test_provenansinya_ikut_di_tiap_skenario(self) -> None:
        """Angka telanjang menuntut dipercaya; "3 dari 18 lintasan" bisa
        diperiksa."""
        for s in _jalan():
            assert any(b.startswith("kerumunan:") for b in s.bukti), s.nama

    def test_totalnya_sama_di_seluruh_skenario(self) -> None:
        """Penyebut yang berbeda-beda berarti bobotnya tidak sebanding satu
        sama lain, dan menjumlahkannya jadi seratus tidak berarti apa-apa."""
        penyebut = {
            b.split("/")[1]
            for s in _jalan()
            for b in s.bukti
            if b.startswith("kerumunan:")
        }

        assert len(penyebut) == 1

    def test_bobotnya_mengikuti_hitungan_lintasan(self) -> None:
        """Yang lintasannya lebih banyak harus berbobot lebih besar. Kalau
        tidak, mesin kerumunan dipanggil lalu hasilnya diabaikan - dan itu
        terlihat persis seperti mesin yang bekerja."""
        hasil = _jalan(frozenset(Peristiwa))

        pasangan = sorted(
            (
                (_lintasan_dari(s), s.bobot)
                for s in hasil
                if s.nama not in TANPA_KELUARGA_KERUMUNAN
            ),
            reverse=True,
        )
        from itertools import pairwise

        for (h1, b1), (h2, b2) in pairwise(pasangan):
            if h1 > h2:
                assert b1 >= b2, f"{h1} lintasan berbobot {b1} < {h2} → {b2}"

    def test_pemicu_berbeda_menghasilkan_bobot_berbeda(self) -> None:
        """Bobot yang tidak berubah apa pun pemicunya adalah tetapan yang
        menyamar sebagai perhitungan."""
        tembus = {s.nama: s.bobot for s in _jalan(TEMBUS)}
        gejolak = {
            s.nama: s.bobot
            for s in _jalan(TEMBUS | {Peristiwa.VOLATILITAS_ABNORMAL})
        }
        bersama = set(tembus) & set(gejolak)

        assert any(tembus[n] != gejolak[n] for n in bersama)

    def test_kekuatan_guncangan_menggeser_bobot(self) -> None:
        """Tembusan yang dua kali lebih jauh melewati ambangnya menggoyang
        kerumunan dua kali lebih keras."""
        lemah = {s.nama: s.bobot for s in _jalan(TEMBUS, kekuatan=1.0)}
        kuat = {s.nama: s.bobot for s in _jalan(TEMBUS, kekuatan=3.0)}

        assert lemah != kuat

    def test_skenario_wajib_tetap_ada_walau_nol_lintasan(self) -> None:
        """Bagian 16.5 menuntut tiga skenario dasar selalu muncul. Bobot nol
        dengan "0/18 lintasan" tertulis lebih jujur daripada bobot kecil yang
        dikarang supaya tidak terlihat kosong."""
        hasil = _jalan(TEMBUS)
        nama = {s.nama for s in hasil}

        assert {"Bullish Continuation", "Bearish Reversal", "False Breakout"} <= nama

    def test_versinya_naik(self) -> None:
        """Evaluasi bagian 16.19 membandingkan per versi; skenario dari mesin
        bobot-tangan dan mesin kerumunan yang tercampur dalam satu angka
        akurasi tidak mengatakan apa pun tentang keduanya."""
        assert VERSI == "internal-2"

    def test_geser_tangan_benar_benar_hilang(self) -> None:
        """Penjaga AST. Konstanta lama yang tertinggal akan dipakai lagi oleh
        orang berikutnya yang mencari "cara menyetel bobot"."""
        import ast
        import inspect

        from aruna.scenario import mesin

        pohon = ast.parse(inspect.getsource(mesin))
        nama = {
            t.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
        }

        assert "_GESER" not in nama
        assert "BOBOT_DASAR" not in nama


class TestDeterministik:
    """Bagian 16.19 tidak mungkin tanpa ini."""

    def test_dua_panggilan_identik(self) -> None:
        assert [s.to_dict() for s in _jalan()] == [s.to_dict() for s in _jalan()]

    def test_id_bisa_diulang(self) -> None:
        """Bukan UUID acak: simulasi yang sama dijalankan ulang menghasilkan id
        yang sama, dan itu yang membuat baris ganda bisa dikenali alih-alih
        menumpuk diam-diam."""
        assert [s.scenario_id for s in _jalan()] == [s.scenario_id for s in _jalan()]

    def test_id_berbeda_antar_skenario(self) -> None:
        ids = [s.scenario_id for s in _jalan()]

        assert len(set(ids)) == len(ids)

    def test_tidak_ada_random_atau_jam_di_modulnya(self) -> None:
        """AST atas impor. Satu `random.shuffle` atau satu `datetime.now()`
        cukup untuk membuat evaluasi bagian 16.19 tidak berarti, dan keduanya
        tidak akan terlihat dari keluarannya."""
        import ast
        import inspect

        from aruna.scenario import mesin

        pohon = ast.parse(inspect.getsource(mesin))
        modul: set[str] = set()
        nama: set[str] = set()
        for simpul in ast.walk(pohon):
            if isinstance(simpul, ast.Import):
                modul |= {a.name.split(".")[0] for a in simpul.names}
            elif isinstance(simpul, ast.ImportFrom):
                modul.add((simpul.module or "").split(".")[0])
                nama |= {a.name for a in simpul.names}

        assert "random" not in modul
        assert "time" not in modul
        assert not (nama & {"now", "utcnow", "monotonic", "random", "shuffle"})


class TestEfekOrdeDua:
    """Bagian 16.8: rantai konsekuensi, bukan satu kalimat."""

    def test_perkembangan_adalah_rantai(self) -> None:
        for s in _jalan():
            assert len(s.perkembangan) >= 2, s.nama

    def test_second_order_menyebut_akibat_dari_akibat(self) -> None:
        hasil = _jalan(TEMBUS | {Peristiwa.EFEK_ORDE_DUA})
        s = next(x for x in hasil if x.nama == "Second-Order Effect")

        assert len(s.perkembangan) >= 3
        assert any("reaksi" in p.lower() for p in s.perkembangan)

    def test_rantainya_berurutan_di_keluaran(self) -> None:
        """Disimpan sebagai daftar, bukan digabung jadi paragraf: akibat dari
        akibat punya urutan, dan meratakannya membuang bagian yang membuatnya
        orde-dua."""
        s = _jalan()[0]

        assert s.to_dict()["perkembangan"] == list(s.perkembangan)


class TestTiapSkenarioBisaSalah:
    """Bagian 16.11."""

    def test_semua_punya_invalidasi(self) -> None:
        for s in _jalan(frozenset(Peristiwa)):
            assert s.invalidasi.syarat, s.nama

    def test_perubahan_regime_menambah_syarat(self) -> None:
        tanpa = _jalan(TEMBUS)[0]
        dengan = _jalan(TEMBUS | {Peristiwa.PERUBAHAN_REGIME})[0]

        assert len(dengan.invalidasi.syarat) > len(tanpa.invalidasi.syarat)


class TestPenandaanVersi:
    def test_versi_melekat(self) -> None:
        """Bagian 16.15 `simulation_version`. Tanpanya, hasil dua mesin berbeda
        tercampur dalam satu angka akurasi dan tidak ada yang bisa dikatakan
        tentang keduanya."""
        for s in _jalan():
            assert s.versi_simulasi == VERSI

    def test_pemicunya_tercatat_di_tiap_skenario(self) -> None:
        """Bagian 16.15 `trigger`. Skenario yang tidak menyebut apa yang
        melahirkannya tidak bisa diperiksa ulang."""
        for s in _jalan():
            assert Peristiwa.BREAKOUT_BESAR.value in s.pemicu
