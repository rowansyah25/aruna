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


class TestKeluargaYangTakTerjangkau:
    """**Temuan 2026-08-23, dan perbaikannya.**

    :data:`KELUARGA` punya enam keluarga dan `klasifikasi_jejak` punya cabang
    untuk semuanya. Diukur pada 162 kombinasi pemicu yang benar-benar menyala
    di produksi, generatornya cuma pernah menghasilkan **tiga**. `False
    Breakout` - hasil pasar yang paling sering, 45% dari 270 simulasi yang
    sudah dinilai - mustahil dihasilkan, jadi ia muncul di keluaran hanya lewat
    `LANTAI_WAJIB` dan bobotnya selalu lantai.

    Empat perbaikan diuji. Tiga ditolak pengukuran, dan yang keempat - dua
    bahan bersamaan - berhasil. Yang ditolak tetap ditulis di bawah, karena
    daftar tempat yang BUKAN jawabannya sama berharganya dengan jawabannya.

    Satu klaim di sini dibatalkan pada hari yang sama: "pembobotan ~3 sigma di
    bawah acak" diukur pada satu jendela yang didominasi pasar menyamping.
    Diadu out-of-sample - frekuensi dipelajari dari paruh awal, diuji pada
    paruh baru - bobot lama mencetak 39,2% melawan acak 22,5%. Yang benar:
    generatornya sempit, bukan pembobotannya terbalik.
    """

    def test_false_breakout_bisa_dihasilkan(self) -> None:
        """**Diperbaiki 2026-08-23 (internal-3).** Sebelumnya generator cuma
        sanggup TIGA dari enam keluarga yang dimiliki `klasifikasi_jejak`, dan
        `False Breakout` - 45% hasil pasar yang sebenarnya - mustahil dihasilkan.
        Ia muncul di keluaran hanya lewat `LANTAI_WAJIB`, jadi bobotnya selalu
        lantai dan tidak pernah bisa menjadi yang tertinggi.

        Dua bahan bersamaan yang memperbaikinya, dan masing-masing sendirian
        sudah diuji dan GAGAL:

        * absorpsi dipusatkan di titik seimbang model, supaya aliran bersih
          benar-benar bisa berbalik tanda;
        * inersia, supaya harga MELEWATI titik awalnya alih-alih berhenti di
          situ.
        """
        from aruna.scenario.kerumunan import guncangan_dari, klasifikasi_jejak
        from aruna.scenario.premis import kisi

        hidup = [
            Peristiwa.PERUBAHAN_REGIME,
            Peristiwa.BREAKOUT_BESAR,
            Peristiwa.SELISIH_PENDAPAT_TAJAM,
            Peristiwa.VOLATILITAS_ABNORMAL,
            Peristiwa.VOLUME_EKSTREM,
        ]
        tercapai = set()
        for i in range(1, len(hidup) + 1):
            for j in range(len(hidup) - i + 1):
                pemicu = frozenset(hidup[j : j + i])
                for g in guncangan_dari(pemicu):
                    for p in kisi(pemicu):
                        tercapai.add(klasifikasi_jejak(jalankan(p, g).jejak))

        assert "False Breakout" in tercapai, tercapai

    def test_dua_keluarga_masih_tak_terjangkau(self) -> None:
        """Ditulis, bukan didiamkan. `High Volatility` dan
        `Liquidation Cascade` tetap mustahil dihasilkan generator.

        Yang kedua nyaris: mundur terjauh sebuah lintasan sekarang 0,98 ATR,
        tepat di bawah `AMBANG_LIKUIDASI_BALIK` = 1,0 yang menyalakan kaskade
        long-terjebak. Menurunkan ambang itu akan membukanya - dan justru
        karena semudah itu, ia tidak dilakukan di sini: perubahan berikutnya
        harus dinilai bagian 16.19 sebagai versi tersendiri, bukan ditumpuk ke
        perubahan yang belum sempat diukur.
        """
        from aruna.scenario.kerumunan import guncangan_dari, klasifikasi_jejak
        from aruna.scenario.premis import kisi

        hidup = [
            Peristiwa.PERUBAHAN_REGIME,
            Peristiwa.BREAKOUT_BESAR,
            Peristiwa.VOLATILITAS_ABNORMAL,
            Peristiwa.VOLUME_EKSTREM,
        ]
        tercapai = set()
        for i in range(1, len(hidup) + 1):
            for j in range(len(hidup) - i + 1):
                pemicu = frozenset(hidup[j : j + i])
                for g in guncangan_dari(pemicu):
                    for p in kisi(pemicu):
                        tercapai.add(klasifikasi_jejak(jalankan(p, g).jejak))

        assert "High Volatility" not in tercapai
        assert "Liquidation Cascade" not in tercapai
        assert len(KELUARGA) - len(tercapai) == 2

    def test_kisi_premis_menghasilkan_hasil_yang_BERBEDA(self) -> None:
        """Ujung yang sebenarnya dijaga, dan sebab semua ini.

        Sampai 2026-08-23 ketiga premis absorpsi menghasilkan aliran bersih
        yang sama-sama POSITIF, jadi kisinya dekoratif: tiga nama untuk satu
        hasil. Sekarang KUAT membalik harga, LEMAH membiarkannya berlari, dan
        NETRAL duduk di antaranya - dan itu yang membuat kisi premis berguna.
        """
        from aruna.scenario.kerumunan import guncangan_dari, klasifikasi_jejak
        from aruna.scenario.premis import kisi

        pemicu = frozenset({
            Peristiwa.PERUBAHAN_REGIME, Peristiwa.VOLATILITAS_ABNORMAL
        })
        per: dict[Absorpsi, set[str]] = {}
        for g in guncangan_dari(pemicu):
            for p in kisi(pemicu):
                per.setdefault(p.absorpsi, set()).add(
                    klasifikasi_jejak(jalankan(p, g).jejak)
                )

        assert len(per) >= 2, per
        nilai = list(per.values())
        assert any(a != b for a in nilai for b in nilai), (
            f"ketiga premis menghasilkan keluarga yang sama: {per}"
        )

    def test_kekuatan_menggeser_jangkauan_bukan_melebarkan(self) -> None:
        """**Perbaikan yang tampak jelas, dan ia SALAH.**

        `simulasikan` menerima `kekuatan` dan mendokumentasikannya sebagai
        severity peristiwa; produksi tidak pernah mengisinya, jadi guncangannya
        terkunci di `GUNCANGAN_DASAR`. Menyambungkannya satu baris - severity-nya
        sudah ada di tangan pemanggil.

        Diukur pada internal-2 sebelum disambung: menaikkan kekuatan ke 1,5-4,0
        justru MENGHAPUS `Sideways` dan membuat hampir semuanya
        `Bullish Continuation`. Ia tidak membuka satu pun keluarga yang hilang.

        **Pada internal-3 hasilnya berubah** - dengan inersia dan absorpsi yang
        berpusat di titik seimbang, kekuatan 2,0 mulai menghasilkan
        `High Volatility`. Itu petunjuk untuk versi berikutnya, bukan izin
        memasangnya sekarang: perubahan yang belum sempat dinilai bagian 16.19
        tidak boleh ditumpuk di atas perubahan yang juga belum dinilai.

        Jadi yang dijaga sekarang bukan lagi "tidak membuka apa pun" melainkan
        "`False Breakout` tetap tidak datang dari sini" - keluarga yang justru
        paling penting, dan yang perbaikan sebenarnya sudah membukanya lewat
        jalur lain.
        """
        from aruna.scenario.kerumunan import guncangan_dari, klasifikasi_jejak
        from aruna.scenario.premis import kisi

        pemicu = frozenset({Peristiwa.BREAKOUT_BESAR})

        def tercapai(kuat: float) -> set[str]:
            return {
                klasifikasi_jejak(jalankan(p, g).jejak)
                for g in guncangan_dari(pemicu, kekuatan=kuat)
                for p in kisi(pemicu)
            }

        # Yang dicatat: severity yang dioper MENGGESER jangkauan generator -
        # bukan melebarkannya. Pada 1,0 keluarannya {Bullish, Sideways}; pada
        # 4,0 menjadi {Bullish, False Breakout}. Jumlahnya sama, isinya tidak.
        #
        # Itu kandidat internal-4, dan test ini menandainya supaya tidak
        # hilang - tapi juga menahannya dari dipasang tanpa diukur: menukar
        # `Sideways` dengan `False Breakout` bukan perbaikan sampai ada yang
        # membuktikan pasarnya memang lebih sering yang kedua pada pemicu ini.
        assert "False Breakout" in tercapai(4.0)
        assert "False Breakout" not in tercapai(1.0)

    def test_absorpsi_kuat_benar_benar_membalik_aliran(self) -> None:
        """Angka yang membuat perbaikan ini bisa dibantah.

        Titik seimbang model - tempat ``mengejar + meredam * absorpsi = 0`` -
        diukur di 1,39 sampai 2,15 tergantung keadaan. Sampai 2026-08-23
        ``KUAT = 1,35`` berada DI BAWAH seluruhnya, jadi "penyerapan kuat" tidak
        pernah membalik apa pun.

        Sekarang KUAT di atas titik itu dan LEMAH di bawahnya. Kalau salah satu
        pindah sisi, mesinnya berubah dan sebaran keluarganya harus diukur
        ulang.
        """
        from aruna.scenario.kohort import KOHORT, aliran
        from aruna.scenario.premis import _NILAI_ABSORPSI, Absorpsi

        mengejar = meredam = 0.0
        for k in KOHORT:
            nilai = aliran(
                k,
                gerak_terakhir=GUNCANGAN_DASAR,
                jarak_kumulatif=GUNCANGAN_DASAR,
                ketidakseimbangan=GUNCANGAN_DASAR,
                kedalaman=1.0,
                dorongan_berita=0.0,
                paksa=0.0,
            )
            if k.tanda < 0:
                meredam += nilai
            else:
                mengejar += nilai

        seimbang = -mengejar / meredam
        assert _NILAI_ABSORPSI[Absorpsi.KUAT] > seimbang
        assert _NILAI_ABSORPSI[Absorpsi.LEMAH] < seimbang

    def test_lintasan_sekarang_bisa_mundur(self) -> None:
        """Bahan yang dulu tidak ada.

        Sampai 2026-08-23 lintasannya MONOTON - naik lalu bertahan, atau
        membeku - jadi `kolam_searah` (long yang membeli tembusan lalu
        terjebak) tidak pernah punya bahan untuk menyala, dan menurunkan
        ambangnya sampai 0,2 tidak mengubah apa pun.

        Sekarang mundurnya sampai 0,98 ATR. `AMBANG_LIKUIDASI_BALIK` = 1,0,
        jadi kaskadenya hampir - tapi belum - menyala.
        """
        from aruna.scenario.kerumunan import guncangan_dari
        from aruna.scenario.premis import kisi

        pemicu = frozenset({
            Peristiwa.PERUBAHAN_REGIME, Peristiwa.VOLATILITAS_ABNORMAL
        })
        mundur_maks = 0.0
        for g in guncangan_dari(pemicu):
            for p in kisi(pemicu):
                j = jalankan(p, g).jejak
                puncak = max(j) if g.besar >= 0 else min(j)
                mundur_maks = max(mundur_maks, abs(puncak - j[-1]))

        assert mundur_maks > 0.5, mundur_maks

    def test_lintasan_tidak_pernah_lari_liar(self) -> None:
        """Batas kewarasan yang sudah dibayar sekali: versi tanpa saturasi
        berakhir di +12,54 ATR - angka yang bukan ramalan melainkan bug yang
        terlihat seperti tren kuat."""
        from aruna.scenario.kerumunan import guncangan_dari
        from aruna.scenario.premis import kisi

        pemicu = frozenset({Peristiwa.BREAKOUT_BESAR})
        for kuat in (1.0, 2.0, 4.0, 8.0):
            for g in guncangan_dari(pemicu, kekuatan=kuat):
                for p in kisi(pemicu):
                    j = jalankan(p, g).jejak
                    assert max(abs(x) for x in j) < 6.0, (kuat, p, j)


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

        dengan = jalankan(premis, NAIK).jejak
        tanpa = jalankan(premis, NAIK, susut=0.0).jejak

        # **Diubah bentuknya pada internal-3.** Sampai internal-2 test ini
        # membandingkan `kaskade` langsung: premis ini menyala dengan susut dan
        # diam tanpanya. Sesudah absorpsi dipusatkan di titik seimbang,
        # lintasannya lebih kecil - maks 3,28 ATR dari sebelumnya 4,33 - dan
        # premis ini tidak lagi mencapai `AMBANG_LIKUIDASI`.
        #
        # Yang diuji tetap klaim yang sama: penyusutan kedalaman BUKAN sekadar
        # memperbesar angka, ia mengubah bentuk lintasannya. Mencabutnya harus
        # terlihat, dan sampai 2026-08-22 tidak terlihat sama sekali - mencabut
        # penyusutannya meninggalkan seluruh test hijau.
        assert dengan != tanpa
        assert max(dengan) > max(tanpa)

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
