"""PASAL 14.39-14.41: apa yang WAJIB dibaca dari tiga fase sebelumnya.

Ketiga pasal itu berupa daftar - bukan rumus. Nilainya bukan pada menghitung
sesuatu, melainkan pada membuat "lapisan ini tidak terbaca" menjadi angka yang
muncul di log, bukan ketiadaan yang tidak ada yang menyadarinya.

Ini keluarga cacat yang paling sering muncul di sistem ini: kode yang ditulis,
diekspor, diuji, dan tidak pernah dilewati jalur hidup. Daftar ini yang
membuatnya terlihat.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from aruna.decision.integration import WAJIB, Fase, Masukan, periksa


class TestDaftarnya:
    def test_tiga_fase_semuanya_punya_daftar(self) -> None:
        assert set(WAJIB) == set(Fase)

    def test_tidak_ada_daftar_yang_kosong(self) -> None:
        """Fase tanpa masukan wajib berarti fase yang boleh diabaikan, dan
        tidak satu pun dari ketiganya begitu."""
        for fase, daftar in WAJIB.items():
            assert daftar, fase

    def test_risk_score_wajib_dari_phase_13(self) -> None:
        assert Masukan.RISK_SCORE in WAJIB[Fase.TIGA_BELAS]

    def test_signal_quality_wajib_dari_phase_11(self) -> None:
        assert Masukan.SIGNAL_QUALITY in WAJIB[Fase.SEBELAS]

    def test_strategy_performance_wajib_dari_phase_12(self) -> None:
        assert Masukan.STRATEGY_PERFORMANCE in WAJIB[Fase.DUA_BELAS]

    def test_setiap_masukan_dimiliki_tepat_satu_fase(self) -> None:
        """Masukan yang muncul di dua daftar akan dihitung dua kali, dan
        kelengkapannya jadi angka yang tidak berarti."""
        semua = [m for daftar in WAJIB.values() for m in daftar]

        assert len(semua) == len(set(semua))

    def test_setiap_anggota_enum_terpakai(self) -> None:
        """Anggota yang tidak masuk daftar mana pun tidak pernah diperiksa -
        ia ada di kode dan tidak ada di pengukuran."""
        semua = {m for daftar in WAJIB.values() for m in daftar}

        assert semua == set(Masukan)

    def test_jumlahnya_sesuai_pasalnya(self) -> None:
        """PASAL 14.39 menyebut tujuh baris, 14.40 sembilan, 14.41 sebelas.

        Angkanya dieja supaya baris yang hilang saat penyuntingan terlihat.
        Sebuah daftar yang kehilangan satu anggota tetap terbaca masuk akal,
        dan kelengkapan 100% atas daftar yang bolong adalah angka yang salah
        justru ketika ia paling meyakinkan.
        """
        assert len(WAJIB[Fase.SEBELAS]) == 7
        assert len(WAJIB[Fase.DUA_BELAS]) == 9
        assert len(WAJIB[Fase.TIGA_BELAS]) == 11


class TestKelengkapan:
    def _semua(self, nilai: bool) -> dict[Masukan, bool]:
        return dict.fromkeys(Masukan, nilai)

    def test_semuanya_hadir(self) -> None:
        hasil = periksa(self._semua(True))

        assert hasil.hilang == ()
        assert hasil.pct == 100

    def test_semuanya_hilang(self) -> None:
        hasil = periksa(self._semua(False))

        assert hasil.hadir == ()
        assert hasil.pct == 0

    def test_yang_tidak_disebut_dihitung_hilang(self) -> None:
        """Masukan yang tidak dilaporkan sama sekali bukan masukan yang hadir.
        Menganggapnya hadir akan membuat kelengkapan terlihat penuh justru pada
        pemanggil yang paling sedikit melapor."""
        hasil = periksa({})

        assert hasil.pct == 0
        assert len(hasil.hilang) == len(Masukan)

    def test_sebagian(self) -> None:
        tersedia = self._semua(False)
        tersedia[Masukan.RISK_SCORE] = True
        hasil = periksa(tersedia)

        assert hasil.hadir == (Masukan.RISK_SCORE,)
        assert 0 < hasil.pct < 100

    def test_urutannya_tetap(self) -> None:
        """Laporan yang urutannya berubah tiap pemanggilan tidak bisa
        dibandingkan antar tick."""
        tersedia = self._semua(False)
        tersedia[Masukan.RISK_SCORE] = True

        assert periksa(tersedia).hilang == periksa(tersedia).hilang

    def test_hadir_dan_hilang_tidak_pernah_beririsan(self) -> None:
        """Satu masukan yang tercatat di kedua sisi membuat jumlahnya melebihi
        totalnya, dan persennya melebihi seratus."""
        tersedia = self._semua(False)
        tersedia[Masukan.CHAMPION] = True
        hasil = periksa(tersedia)

        assert not set(hasil.hadir) & set(hasil.hilang)
        assert len(hasil.hadir) + len(hasil.hilang) == len(Masukan)

    def test_dipanggil_dari_jalur_hidup(self) -> None:
        """Daftar ini ada supaya "lapisan ini tidak terbaca" menjadi angka.
        Sebuah daftar yang tidak pernah diperiksa tidak menghasilkan angka apa
        pun - dan modul ini akan menjadi anggota kesembilan dari delapan yang
        pernah diam."""
        from aruna.futures import service

        assert (
            "from aruna.decision.integration import" in inspect.getsource(service)
        )

    def test_kelengkapan_masuk_pengamatan(self, monkeypatch) -> None:
        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )

        modul.observe_decision(
            context=SimpleNamespace(regime=SimpleNamespace(regime="TRENDING")),
            verdict=SimpleNamespace(opinions=("a",)),
            plan=SimpleNamespace(entry=1, horizon_hours=4.0),
            note=SimpleNamespace(confidence=0.8, risk_readings={"x": 1.0}),
            symbol="BTCUSDT",
        )

        assert [n for n, _ in keluar] == ["decision.observed"]
        isi = keluar[0][1]
        assert "integrasi_pct" in isi
        assert set(isi["integrasi_fase"]) == {f.value for f in Fase}

    def test_yang_hilang_disebut_namanya(self, monkeypatch) -> None:
        """Angka gabungan memberi tahu bahwa ada yang hilang; hanya namanya yang
        memberi tahu apa yang harus dicari."""
        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )

        modul.observe_decision(
            context=None, verdict=None, plan=None, note=None, symbol="BTCUSDT",
        )

        isi = keluar[0][1]
        assert isi["integrasi_pct"] == 0
        assert Masukan.RISK_SCORE.value in isi["integrasi_hilang"]

class TestTidakBerlakuBukanHilang:
    """Rencana WAIT tidak punya entry, leverage, atau harga likuidasi - dan itu
    benar, bukan lapisan yang putus.

    Terukur pada 2026-08-20: laporan yang sama menyebut Phase 13 **27%** pada
    rencana WAIT dan **73%** pada rencana PLAN. Selisihnya bukan perbedaan
    perakitan; ia perbedaan sejauh mana rencananya sempat berjalan.

    Menyamakan keduanya membuat angkanya menyesatkan ke arah yang paling
    berbahaya: seseorang akan membaca "Phase 13 cuma 27%" lalu menghabiskan
    waktu menyambungkan lapisan yang **sudah** tersambung. Itu persis kesalahan
    yang sudah lima kali terjadi di sesi ini, satu tingkat lebih tinggi.
    """

    def _semua(self, nilai: bool) -> dict[Masukan, bool]:
        return dict.fromkeys(Masukan, nilai)

    def test_yang_tak_berlaku_tidak_dihitung_hilang(self) -> None:
        hasil = periksa(
            self._semua(False), tak_berlaku={Masukan.LIQUIDATION_RISK}
        )

        assert Masukan.LIQUIDATION_RISK not in hasil.hilang
        assert Masukan.LIQUIDATION_RISK in hasil.tak_berlaku

    def test_yang_tak_berlaku_tidak_masuk_penyebut(self) -> None:
        """Kalau ia tetap di penyebut, "tidak berlaku" cuma nama lain untuk
        "hilang" - dan angkanya tidak berubah sama sekali."""
        semua_hadir = self._semua(True)
        semua_hadir[Masukan.LIQUIDATION_RISK] = False

        assert periksa(semua_hadir).pct < 100
        assert periksa(
            semua_hadir, tak_berlaku={Masukan.LIQUIDATION_RISK}
        ).pct == 100

    def test_gap_sungguhan_tetap_terlihat(self) -> None:
        """Pasangannya, dan ia yang menjaga seluruh perubahan ini tetap jujur.
        Tanpa test ini, "tidak berlaku" bisa dipakai memaafkan apa pun."""
        tersedia = self._semua(True)
        tersedia[Masukan.PATTERN_DISCOVERY] = False

        hasil = periksa(tersedia, tak_berlaku={Masukan.LIQUIDATION_RISK})

        assert Masukan.PATTERN_DISCOVERY in hasil.hilang
        assert hasil.pct < 100

    def test_yang_ternyata_ada_bukan_tak_berlaku(self) -> None:
        """Sebuah masukan yang benar-benar terbaca jelas berlaku. Menandainya
        tidak berlaku akan mengeluarkan lapisan yang bekerja dari hitungannya."""
        tersedia = self._semua(False)
        tersedia[Masukan.LIQUIDATION_RISK] = True

        hasil = periksa(tersedia, tak_berlaku={Masukan.LIQUIDATION_RISK})

        assert Masukan.LIQUIDATION_RISK in hasil.hadir
        assert Masukan.LIQUIDATION_RISK not in hasil.tak_berlaku

    def test_per_fase_juga_mengeluarkannya(self) -> None:
        tersedia = self._semua(True)
        for m in WAJIB[Fase.TIGA_BELAS]:
            tersedia[m] = False

        hasil = periksa(
            tersedia, tak_berlaku=set(WAJIB[Fase.TIGA_BELAS][:5])
        )

        assert hasil.per_fase[Fase.TIGA_BELAS] == 0
        assert hasil.per_fase[Fase.SEBELAS] == 100

    def test_fase_yang_seluruhnya_tak_berlaku_tidak_dinilai(self) -> None:
        """``None``, bukan nol dan bukan seratus. Nol menuduh perakitan yang
        tidak pernah diuji; seratus memuji kelengkapan yang tidak pernah
        diperiksa."""
        hasil = periksa(
            self._semua(False), tak_berlaku=set(WAJIB[Fase.TIGA_BELAS])
        )

        assert hasil.per_fase[Fase.TIGA_BELAS] is None
        assert hasil.per_fase[Fase.SEBELAS] == 0

    def test_bawaannya_tidak_mengubah_apa_pun(self) -> None:
        """Pemanggil yang tidak menyebut apa-apa mendapat perilaku lama."""
        hasil = periksa(self._semua(False))

        assert hasil.tak_berlaku == ()
        assert hasil.pct == 0


class TestJalurHidupMembedakannya:
    """Yang menentukan bukan nama vonisnya, melainkan sejauh mana ia berjalan."""

    def _amati(self, monkeypatch, **objek) -> dict:
        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )
        dasar = {"context": None, "verdict": None, "plan": None, "note": None}
        modul.observe_decision(**(dasar | objek), symbol="BTCUSDT")
        return keluar[0][1]

    def test_rencana_tanpa_entry_tidak_dituduh_kehilangan_sizing(
        self, monkeypatch
    ) -> None:
        """Rencana WAIT berhenti sebelum sizing. Melaporkan leverage dan harga
        likuidasinya "hilang" akan membuat pembacanya mencari lapisan yang
        putus - padahal tidak ada yang putus."""
        isi = self._amati(
            monkeypatch, plan=SimpleNamespace(entry=None, horizon_hours=4.0)
        )

        for m in (
            Masukan.LEVERAGE_ANALYSIS, Masukan.LIQUIDATION_RISK,
            Masukan.EXPOSURE, Masukan.TP_QUALITY, Masukan.SL_QUALITY,
            Masukan.RISK_REWARD,
        ):
            assert m.value not in isi["integrasi_hilang"], m
            assert m.value in isi["integrasi_tak_berlaku"], m

    def test_rencana_yang_sudah_sizing_tetap_dinilai(self, monkeypatch) -> None:
        """Pasangannya, dan ia yang menjaga perubahan ini tetap jujur: begitu
        rencananya sampai ke sizing, ketiadaan leverage kembali jadi temuan."""
        isi = self._amati(
            monkeypatch,
            plan=SimpleNamespace(entry=1, horizon_hours=4.0, target=None),
        )

        assert Masukan.LEVERAGE_ANALYSIS.value in isi["integrasi_hilang"]
        assert Masukan.LEVERAGE_ANALYSIS.value not in isi["integrasi_tak_berlaku"]

    def test_lapisan_yang_sungguhan_putus_tetap_terlihat(
        self, monkeypatch
    ) -> None:
        """Phase 12 tidak bergantung pada sizing sama sekali. Ia tidak boleh
        ikut dimaafkan hanya karena rencananya berhenti awal."""
        isi = self._amati(
            monkeypatch, plan=SimpleNamespace(entry=None, horizon_hours=4.0)
        )

        assert Masukan.PATTERN_DISCOVERY.value in isi["integrasi_hilang"]

    def test_angkanya_ikut_berubah(self, monkeypatch) -> None:
        """Kalau persennya tidak bergerak, "tidak berlaku" cuma label."""
        isi = self._amati(
            monkeypatch, plan=SimpleNamespace(entry=None, horizon_hours=4.0)
        )
        sizing = isi["integrasi_fase"]["PHASE 13"]

        # Lima dari sebelas masukan Phase 13 tidak bergantung sizing; enam
        # sisanya dikeluarkan. Penyebutnya harus lima, bukan sebelas.
        assert sizing == 0
        assert len(isi["integrasi_tak_berlaku"]) == 6


class TestPembacaannyaMenemukanYangMemangAda:
    """Tiga kali pengukuran ini melaporkan lapisan yang berjalan sebagai hilang.

    Selalu sebab yang sama: dibaca dari tempat yang salah. ``note.strategy``
    yang ternyata ada di ``context``; ``verdict.split`` yang ternyata ada di
    ``note``; dan sekarang tiga lagi. Kesalahannya murah dilakukan dan mahal
    ditemukan, karena "hilang" adalah jawaban yang masuk akal - ia terbaca
    seperti temuan, bukan seperti bug.

    Yang paling berbahaya bukan angkanya, melainkan apa yang dilakukan orang
    atasnya: menyambungkan lapisan yang **sudah** tersambung.
    """

    def _amati(self, monkeypatch, **objek) -> dict:
        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )
        dasar = {"context": None, "verdict": None, "plan": None, "note": None}
        modul.observe_decision(**(dasar | objek), symbol="BTCUSDT")
        return keluar[0][1]

    def test_volatilitas_dibaca_dari_pembacaan_risiko(self, monkeypatch) -> None:
        """Terukur di produksi: ``risk_readings`` berisi ``volatility``. Tidak
        ada ``context.volatility`` di ARUNA, dan tidak pernah ada."""
        isi = self._amati(
            monkeypatch,
            note=SimpleNamespace(risk_readings={"volatility": 8.4}),
        )

        assert Masukan.VOLATILITY.value not in isi["integrasi_hilang"]

    def test_volatilitas_yang_benar_benar_kosong_tetap_hilang(
        self, monkeypatch
    ) -> None:
        """Pasangannya. Tanpa ini, perbaikan di atas bisa berupa "anggap selalu
        ada" - dan pengukurannya berhenti mengukur."""
        isi = self._amati(
            monkeypatch, note=SimpleNamespace(risk_readings={})
        )

        assert Masukan.VOLATILITY.value in isi["integrasi_hilang"]

    def test_performa_strategi_terbaca_walau_tidak_ada_yang_menang(
        self, monkeypatch
    ) -> None:
        """``Selection(strategy=None, rejected=(...))`` adalah bukti tabel
        performanya DIBACA - itu yang menolak kandidatnya. Membaca
        ``strategy.evidence``, yang hanya terisi kalau ada yang menang,
        melaporkan Phase 12 sebagai tidak terbaca justru ketika ia bekerja."""
        isi = self._amati(
            monkeypatch,
            context=SimpleNamespace(
                strategy=SimpleNamespace(
                    strategy=None, evidence=None,
                    rejected=(("STR-005", "tidak lebih baik"),),
                    refusal="NOT_BETTER_THAN_AVERAGE",
                )
            ),
        )

        assert Masukan.STRATEGY_PERFORMANCE.value not in isi["integrasi_hilang"]

    def test_strategi_yang_tidak_pernah_ditanya_tetap_hilang(
        self, monkeypatch
    ) -> None:
        """Bedanya dengan di atas: ``None`` berarti pemilihnya tidak dirangkai
        sama sekali, dan itu memang Phase 12 yang tidak terbaca."""
        isi = self._amati(
            monkeypatch, context=SimpleNamespace(strategy=None)
        )

        assert Masukan.STRATEGY_PERFORMANCE.value in isi["integrasi_hilang"]

    def test_risiko_berita_dibaca_dari_dua_tempat(self, monkeypatch) -> None:
        """Berita sampai lewat ``context.news`` DAN lewat
        ``risk_readings['news_risk']``. Membaca satu saja membuat jalur yang
        memakai yang lain terlihat kosong."""
        isi = self._amati(
            monkeypatch,
            note=SimpleNamespace(risk_readings={"news_risk": 0.0}),
        )

        assert Masukan.NEWS_RISK.value not in isi["integrasi_hilang"]

    def test_nol_adalah_pengukuran_bukan_ketiadaan(self, monkeypatch) -> None:
        """``news_risk: 0.0`` berarti berita sudah dinilai dan hasilnya nol -
        bukan berarti berita tidak terbaca. Kelas kesalahan yang sama dengan
        ``confidence=0`` dan ``side='FLAT'``, dan sudah lima kali muncul."""
        isi = self._amati(
            monkeypatch,
            note=SimpleNamespace(risk_readings={"volatility": 0.0}),
        )

        assert Masukan.VOLATILITY.value not in isi["integrasi_hilang"]


class TestKelengkapanLanjutan:
    def _semua(self, nilai: bool) -> dict[Masukan, bool]:
        return dict.fromkeys(Masukan, nilai)

    def test_per_fase_bisa_dipisah(self) -> None:
        """Angka gabungan tidak menjawab pertanyaan yang sebenarnya diajukan:
        fase MANA yang tidak sampai. Kelengkapan 70% bisa berarti Phase 13
        seluruhnya hilang atau tiga baris tersebar - dua masalah yang sangat
        berbeda."""
        tersedia = self._semua(True)
        for m in WAJIB[Fase.TIGA_BELAS]:
            tersedia[m] = False
        hasil = periksa(tersedia)

        assert set(hasil.hilang) == set(WAJIB[Fase.TIGA_BELAS])
        assert hasil.per_fase[Fase.TIGA_BELAS] == 0
        assert hasil.per_fase[Fase.SEBELAS] == 100
