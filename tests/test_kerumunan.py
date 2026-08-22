"""Mesin kerumunan: kohort bereaksi, harga muncul.

Yang dikunci di sini **mekanismenya**, bukan angkanya. Bobot skenario akan
bergeser tiap kali pangsa kohort disetel, dan test yang mematok angka akan
memaksa penyetelnya menyunting test - yang mengubah penjaga menjadi stempel.

Yang tidak boleh bergeser ada empat, dan tiap satu lahir dari cacat yang
benar-benar terjadi saat mesin ini dibangun:

* **Simulasi butuh rangsangan.** Versi pertama dimulai dari keadaan netral
  sempurna dan berhenti di titik tetap: 18 lintasan, semuanya rata di nol.
* **Premis harus bisa membalik kesimpulan.** Versi kedua membagi aliran BERSIH
  dengan kekuatan penyerapan, yang hanya mengubah besar umpan balik dan tidak
  pernah tandanya: 34 dari 36 lintasan `Sideways`.
* **Kaskade harus kehabisan bahan bakar.** Versi ketiga tidak punya kolam
  posisi: satu lintasan berakhir di +12,54 ATR.
* **Penjaga tidak boleh jadi dinamika.** Batas gerak per ronde sempat menggigit
  hampir tiap ronde, sehingga bentuk lintasannya ditentukan pemotongan alih-alih
  simulasi.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from aruna.scenario.kerumunan import (
    _BATAS_GERAK,
    AMBANG_ARAH,
    GUNCANGAN_DASAR,
    KELUARGA,
    RONDE,
    Guncangan,
    jalankan,
    klasifikasi,
    simulasikan_kerumunan,
)
from aruna.scenario.pemicu import Peristiwa
from aruna.scenario.premis import Absorpsi, Dorongan, Kedalaman, Premis

TEMBUS = frozenset({Peristiwa.BREAKOUT_BESAR})
NAIK = Guncangan(besar=GUNCANGAN_DASAR, sebab="uji naik")


def _premis(a=Absorpsi.NETRAL, k=Kedalaman.NORMAL, d=Dorongan.TIDAK_ADA) -> Premis:
    return Premis(absorpsi=a, kedalaman=k, dorongan=d)


class TestButuhRangsangan:
    """Cacat pertama: pasar netral sempurna adalah titik tetap di nol."""

    def test_lintasan_bergerak_dari_guncangan(self) -> None:
        L = jalankan(_premis(), NAIK)

        assert L.jejak[0] == 0.0
        assert L.jejak[1] == pytest.approx(GUNCANGAN_DASAR)

    def test_ada_lintasan_yang_tidak_rata(self) -> None:
        """Titik tetap di nol lolos tiap test determinisme dan tiap test
        "jumlahnya seratus" - yang menangkapnya cuma test ini."""
        L = simulasikan_kerumunan(frozenset(Peristiwa))

        assert any(abs(x.akhir) > AMBANG_ARAH for x in L)

    def test_guncangan_ke_bawah_menurunkan(self) -> None:
        turun = jalankan(_premis(), Guncangan(besar=-GUNCANGAN_DASAR, sebab="x"))

        assert turun.jejak[1] < 0


class TestPremisMembalikKesimpulan:
    """Cacat kedua, dan yang paling halus: premis yang hanya mengubah besar
    umpan balik membuat seluruh kisi mendarat di satu keluarga."""

    def test_penyerapan_menentukan_urutannya(self) -> None:
        kuat = jalankan(_premis(a=Absorpsi.KUAT), NAIK).akhir
        netral = jalankan(_premis(a=Absorpsi.NETRAL), NAIK).akhir
        lemah = jalankan(_premis(a=Absorpsi.LEMAH), NAIK).akhir

        assert kuat < netral < lemah

    def test_kisi_menghasilkan_lebih_dari_satu_keluarga(self) -> None:
        """Simulasi yang seluruh lintasannya mendarat di satu keluarga tidak
        menyimulasikan apa pun - ia satu ramalan yang dijalankan berkali-kali."""
        L = simulasikan_kerumunan(frozenset(Peristiwa))

        assert len({klasifikasi(x) for x in L}) >= 3

    def test_kedalaman_tipis_memperbesar_ayunan(self) -> None:
        normal = jalankan(_premis(k=Kedalaman.NORMAL), NAIK).ayunan
        tipis = jalankan(_premis(k=Kedalaman.TIPIS), NAIK).ayunan

        assert tipis > normal

    def test_dorongan_berita_menggeser_hasil(self) -> None:
        pos = jalankan(_premis(d=Dorongan.POSITIF), NAIK).akhir
        neg = jalankan(_premis(d=Dorongan.NEGATIF), NAIK).akhir

        assert pos > neg


class TestKaskadeMuncul:
    """Bagian 16.8: efek orde-dua, bukan aturan yang dijadwalkan."""

    def test_kaskade_bisa_terjadi(self) -> None:
        L = simulasikan_kerumunan(frozenset(Peristiwa))

        assert any(x.kaskade for x in L)

    def test_kaskade_tidak_terjadi_pada_pasar_teredam(self) -> None:
        """Kalau kaskade terjadi di setiap premis, ia dijadwalkan - bukan
        muncul."""
        tenang = jalankan(_premis(a=Absorpsi.KUAT, k=Kedalaman.NORMAL), NAIK)

        assert not tenang.kaskade

    def test_kaskade_kehabisan_bahan_bakar(self) -> None:
        """Cacat ketiga. Posisi berungkit jumlahnya terbatas; kaskade yang tidak
        pernah habis menghasilkan angka yang tidak berarti apa-apa."""
        L = simulasikan_kerumunan(frozenset(Peristiwa))

        assert max(abs(x.akhir) for x in L) < 8.0

    def test_kedalaman_yang_menyusut_memperbesar_gerak(self) -> None:
        """**Efek orde-dua bagian 16.8, diadu langsung.**

        Test ini lahir dari cabut-uji yang gagal menggigit: mencabut penyusutan
        kedalaman sama sekali meninggalkan seluruh berkas ini hijau. Klaim
        "modul ini menghasilkan efek orde-dua" jadi tidak diuji apa pun -
        momentum sendirian sudah cukup mencapai ambang likuidasi, dan
        kedalamannya hanya hiasan yang tampak berfungsi.

        Yang diadu: aliran yang sama, lintasan yang sama, hanya lajunya
        berbeda. Kalau kedalaman tidak menyusut, gerak ronde belakangan tidak
        lebih besar daripada ronde awal.
        """
        premis = _premis(a=Absorpsi.LEMAH, k=Kedalaman.TIPIS)
        dengan = jalankan(premis, NAIK)
        tanpa = jalankan(premis, NAIK, susut=0.0)

        assert abs(dengan.akhir) > abs(tanpa.akhir)

    def test_tanpa_penyusutan_kaskadenya_lebih_jarang(self) -> None:
        """Kedalaman yang menipis bukan sekadar memperbesar angka - ia yang
        mengubah gerak biasa menjadi gerak yang menyentuh ambang likuidasi."""
        premis = _premis(a=Absorpsi.NETRAL, k=Kedalaman.TIPIS)

        assert jalankan(premis, NAIK).kaskade
        assert not jalankan(premis, NAIK, susut=0.0).kaskade

    def test_rondenya_tercatat(self) -> None:
        """Kaskade yang tidak bisa dilihat per ronde tidak bisa dibantah."""
        L = [x for x in simulasikan_kerumunan(frozenset(Peristiwa)) if x.kaskade]

        assert all(0 <= x.ronde_kaskade < RONDE for x in L)

    def test_yang_mengejar_tembusan_bisa_terjebak(self) -> None:
        """Kolam kedua. Tanpanya tidak ada satu pun lintasan yang berbalik:
        yang beli di tembusan tidak pernah terjebak, jadi tidak ada bahan bakar
        untuk pembalikan."""
        L = simulasikan_kerumunan(frozenset(Peristiwa))
        naik = [x for x in L if x.guncangan.besar > 0]

        assert any(x.akhir < 0 for x in naik)


class TestPenjagaBukanDinamika:
    """Cacat keempat. Lintasan yang bentuknya ditentukan penjaga bukan hasil
    simulasi melainkan hasil pemotongan."""

    def test_batasnya_jarang_menggigit(self) -> None:
        L = simulasikan_kerumunan(frozenset(Peristiwa))
        gigit = sum(
            1
            for x in L
            for i in range(1, len(x.jejak))
            if abs(x.jejak[i] - x.jejak[i - 1]) >= _BATAS_GERAK - 1e-9
        )
        total = sum(len(x.jejak) - 1 for x in L)

        assert gigit / total < 0.05, f"{gigit}/{total} ronde terpotong"

    def test_tidak_ada_nan_atau_tak_hingga(self) -> None:
        from math import isfinite

        for x in simulasikan_kerumunan(frozenset(Peristiwa)):
            assert all(isfinite(v) for v in x.jejak)


class TestKlasifikasi:
    def test_namanya_cocok_dengan_keluarga(self) -> None:
        """Nama yang meleset membuat bobot sebuah keluarga tidak pernah bertemu
        skenarionya - dan hasilnya bobot nol tanpa satu pun test merah."""
        for x in simulasikan_kerumunan(frozenset(Peristiwa)):
            assert klasifikasi(x) in KELUARGA

    def test_kaskade_menang_atas_arah(self) -> None:
        """Lintasan yang melikuidasi setengah pasar dan berakhir naik tetap
        kaskade; melabelinya Bullish Continuation menyembunyikan satu-satunya
        hal yang paling perlu diketahui pembacanya."""
        L = [x for x in simulasikan_kerumunan(frozenset(Peristiwa)) if x.kaskade]
        naik = [x for x in L if x.akhir > AMBANG_ARAH]

        assert naik, "tidak ada kaskade yang berakhir naik - kasusnya tak teruji"
        assert all(klasifikasi(x) == "Liquidation Cascade" for x in naik)

    def test_false_breakout_butuh_naik_lalu_kembali(self) -> None:
        """Titik akhirnya sendiri tidak bisa membedakan tembusan palsu dari
        lintasan yang memang tidak ke mana-mana."""
        from aruna.scenario.kerumunan import Lintasan

        palsu = Lintasan(
            premis=_premis(),
            guncangan=NAIK,
            jejak=(0.0, 0.5, 1.2, 0.8, 0.0, -0.05),
        )
        diam = Lintasan(
            premis=_premis(), guncangan=NAIK, jejak=(0.0, 0.05, 0.02, -0.01, 0.0)
        )

        assert klasifikasi(palsu) == "False Breakout"
        assert klasifikasi(diam) == "Sideways"


class TestSimetriTanpaArah:
    def test_pemicu_tanpa_arah_menjalankan_dua_guncangan(self) -> None:
        """Memilih satu arah berarti mengarang arah yang tidak terbaca dari
        bukti apa pun - persis yang bagian 16.18 tutup."""
        L = simulasikan_kerumunan(frozenset({Peristiwa.PERUBAHAN_REGIME}))

        assert {x.guncangan.besar > 0 for x in L} == {True, False}

    def test_hasilnya_simetris(self) -> None:
        """Tanpa informasi arah, kerumunan tidak boleh condong ke mana pun."""
        L = simulasikan_kerumunan(frozenset({Peristiwa.PERUBAHAN_REGIME}))
        naik = sorted(round(x.akhir, 6) for x in L if x.guncangan.besar > 0)
        turun = sorted(-round(x.akhir, 6) for x in L if x.guncangan.besar < 0)

        assert naik == turun

    def test_tembusan_berarah_hanya_satu_guncangan(self) -> None:
        """Arahnya terbaca dari bukti - harga memang sudah menembus ke atas.
        Menjalankan kedua arah di sini menyimulasikan pasar yang tidak ada."""
        L = simulasikan_kerumunan(TEMBUS)

        assert all(x.guncangan.besar > 0 for x in L)


class TestDeterministik:
    """Tanpa ini bagian 16.19 mustahil."""

    def test_dua_panggilan_identik(self) -> None:
        a = [x.jejak for x in simulasikan_kerumunan(frozenset(Peristiwa))]
        b = [x.jejak for x in simulasikan_kerumunan(frozenset(Peristiwa))]

        assert a == b

    def test_tidak_ada_acak_atau_jam(self) -> None:
        from aruna.scenario import kerumunan

        pohon = ast.parse(inspect.getsource(kerumunan))
        modul: set[str] = set()
        nama: set[str] = set()
        for n in ast.walk(pohon):
            if isinstance(n, ast.Import):
                modul |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                modul.add((n.module or "").split(".")[0])
                nama |= {a.name for a in n.names}

        assert not (modul & {"random", "secrets", "time", "numpy"})
        assert not (nama & {"now", "utcnow", "monotonic", "random", "shuffle"})

    def test_kekuatan_menskalakan_guncangan(self) -> None:
        kecil = simulasikan_kerumunan(TEMBUS, kekuatan=1.0)[0]
        besar = simulasikan_kerumunan(TEMBUS, kekuatan=2.0)[0]

        assert abs(besar.jejak[1]) > abs(kecil.jejak[1])


class TestBentukLintasan:
    def test_panjang_jejaknya_konsisten(self) -> None:
        """Ronde nol adalah guncangannya sendiri, lalu RONDE ronde reaksi."""
        L = jalankan(_premis(), NAIK)

        assert len(L.jejak) == RONDE + 2

    def test_ayunan_tidak_pernah_negatif(self) -> None:
        for x in simulasikan_kerumunan(frozenset(Peristiwa)):
            assert x.ayunan >= 0
