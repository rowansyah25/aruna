"""Asumsi apa yang divariasikan antar lintasan.

Dua hal yang dijaga, dan keduanya adalah alasan modul ini menolak angka acak:

* **Bisa diulang** — kisi yang sama dari pemicu yang sama, selalu. Tanpa ini
  bagian 16.19 mustahil.
* **Digerbangi bukti** — premis dorongan berita hanya ada kalau beritanya
  menyala. Memvariasikan asumsi yang tidak punya bukti adalah karangan
  berformat.
"""

from __future__ import annotations

import pytest

from aruna.scenario.pemicu import Peristiwa
from aruna.scenario.premis import (
    MINIMUM_LINTASAN,
    Absorpsi,
    Dorongan,
    Kedalaman,
    Premis,
    kisi,
)

TEMBUS = frozenset({Peristiwa.BREAKOUT_BESAR})


class TestKisiDigerbangiBukti:
    def test_tembusan_biasa_hanya_memvariasikan_penyerapan(self) -> None:
        """Pertanyaan yang menentukan pada tiap tembusan, dan satu-satunya yang
        tidak bisa dijawab dari data yang sudah ada."""
        k = kisi(TEMBUS)

        assert len(k) == len(Absorpsi)
        assert {p.absorpsi for p in k} == set(Absorpsi)

    def test_tanpa_berita_tidak_ada_premis_dorongan(self) -> None:
        assert all(p.dorongan is Dorongan.TIDAK_ADA for p in kisi(TEMBUS))

    def test_dengan_berita_kedua_arah_muncul(self) -> None:
        """Berita yang diketahui ADA belum tentu diketahui BAIK. Memvariasikan
        satu arah saja adalah tebakan arah yang menyamar sebagai simulasi."""
        k = kisi(TEMBUS | {Peristiwa.BERITA_BESAR})

        assert {p.dorongan for p in k} == set(Dorongan)

    def test_tanpa_tanda_gejolak_kedalamannya_normal_saja(self) -> None:
        """Menganggap buku tipis tanpa tanda apa pun membuat tiap pasar terlihat
        rapuh."""
        assert all(p.kedalaman is Kedalaman.NORMAL for p in kisi(TEMBUS))

    @pytest.mark.parametrize(
        "gejolak",
        [
            Peristiwa.VOLATILITAS_ABNORMAL,
            Peristiwa.VOLUME_EKSTREM,
            Peristiwa.LONJAKAN_LIKUIDASI,
        ],
    )
    def test_tanda_gejolak_menambah_kedalaman_tipis(self, gejolak) -> None:
        k = kisi(TEMBUS | {gejolak})

        assert {p.kedalaman for p in k} == set(Kedalaman)

    def test_pemicu_lengkap_menghasilkan_kisi_terbesar(self) -> None:
        k = kisi(frozenset(Peristiwa))

        assert len(k) == len(Absorpsi) * len(Kedalaman) * len(Dorongan)


class TestBisaDiulang:
    def test_kisi_sama_dari_pemicu_sama(self) -> None:
        assert kisi(TEMBUS) == kisi(TEMBUS)

    def test_urutannya_stabil(self) -> None:
        """Urutan yang berubah membuat `scenario_id` berubah untuk simulasi yang
        sama, dan baris ganda berhenti bisa dikenali."""
        a = [p.kalimat for p in kisi(frozenset(Peristiwa))]
        b = [p.kalimat for p in kisi(frozenset(Peristiwa))]

        assert a == b

    def test_tidak_ada_acak_di_modulnya(self) -> None:
        """AST atas impor. Satu `random.choice` cukup membuat evaluasi bagian
        16.19 tidak berarti, dan itu tidak akan terlihat dari keluarannya."""
        import ast
        import inspect

        from aruna.scenario import premis

        pohon = ast.parse(inspect.getsource(premis))
        modul: set[str] = set()
        for n in ast.walk(pohon):
            if isinstance(n, ast.Import):
                modul |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                modul.add((n.module or "").split(".")[0])

        assert not (modul & {"random", "secrets", "time", "numpy"})


class TestPremisBisaDibantah:
    def test_kalimatnya_menyebut_penyerapan_dan_kedalaman(self) -> None:
        """Lintasan yang lahir dari benih 4172 tidak bisa didebat; yang lahir
        dari premis yang tertulis bisa."""
        p = Premis(
            absorpsi=Absorpsi.LEMAH,
            kedalaman=Kedalaman.TIPIS,
            dorongan=Dorongan.TIDAK_ADA,
        )

        assert "lemah" in p.kalimat
        assert "tipis" in p.kalimat

    def test_dorongan_disebut_hanya_kalau_ada(self) -> None:
        tanpa = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)
        dengan = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.POSITIF)

        assert "dorongan" not in tanpa.kalimat
        assert "dorongan" in dengan.kalimat

    def test_pembatalnya_adalah_premis_yang_terbantah(self) -> None:
        """Syarat pembatal sebuah skenario ADALAH premis yang terbantah.
        Menuliskannya dua kali membuat keduanya bisa melenceng."""
        kuat = Premis(Absorpsi.KUAT, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)
        lemah = Premis(Absorpsi.LEMAH, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)

        assert "lemah" in kuat.pembatal
        assert "kuat" in lemah.pembatal

    def test_kedalaman_tipis_menambah_pembatal(self) -> None:
        tipis = Premis(Absorpsi.NETRAL, Kedalaman.TIPIS, Dorongan.TIDAK_ADA)

        assert "kedalaman" in tipis.pembatal

    def test_tiap_premis_di_kisi_punya_pembatal(self) -> None:
        for p in kisi(frozenset(Peristiwa)):
            assert p.pembatal, p.kalimat


class TestMinimumLintasan:
    def test_kisi_terkecil_masih_memenuhi_minimum(self) -> None:
        """Satu lintasan bukan simulasi melainkan satu ramalan; dua hanya bisa
        mengatakan "naik atau turun", yang sudah diketahui tanpa menyimulasikan
        apa pun."""
        assert len(kisi(TEMBUS)) >= MINIMUM_LINTASAN

    def test_kisi_kosong_pun_memenuhi(self) -> None:
        """Pemicu kosong tidak seharusnya sampai ke sini - `layak_simulasi`
        menahannya - tapi kisi yang runtuh pada masukan kosong adalah cabang
        yang menunggu dipanggil dari tempat lain."""
        assert len(kisi(frozenset())) >= MINIMUM_LINTASAN


class TestNilaiPremisMasukAkal:
    def test_absorpsi_kuat_lebih_besar_dari_lemah(self) -> None:
        kuat = Premis(Absorpsi.KUAT, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)
        lemah = Premis(Absorpsi.LEMAH, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)

        assert kuat.kekuatan_absorpsi > lemah.kekuatan_absorpsi

    def test_kedalaman_tipis_lebih_kecil(self) -> None:
        tipis = Premis(Absorpsi.NETRAL, Kedalaman.TIPIS, Dorongan.TIDAK_ADA)
        normal = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)

        assert 0 < tipis.kedalaman_awal < normal.kedalaman_awal

    def test_dorongan_berlawanan_tanda(self) -> None:
        pos = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.POSITIF)
        neg = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.NEGATIF)

        assert pos.dorongan_berita == -neg.dorongan_berita
        assert pos.dorongan_berita > 0

    def test_tanpa_berita_dorongannya_nol(self) -> None:
        p = Premis(Absorpsi.NETRAL, Kedalaman.NORMAL, Dorongan.TIDAK_ADA)

        assert p.dorongan_berita == 0.0
