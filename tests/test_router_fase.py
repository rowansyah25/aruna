"""Fase router: yang menyambungkan Task 1-7 menjadi keputusan (bagian 17.19).

**Ini test yang paling penting di Phase 17**, dan sebabnya bukan cakupannya.
Task 1 sampai 7 semuanya lulus test unitnya sendiri sementara tidak satu pun
dipanggil siapa pun - keadaan yang di proyek ini sudah lima kali berakhir
sebagai kode mati yang terlihat sehat. Yang membedakannya cuma berkas ini dan
`test_router_terpasang`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aruna.core.enums import Market
from aruna.learning.strategies import Strategy, StrategyStatus
from aruna.router.label import VERSI_ROUTER
from aruna.router.putusan import AlasanKosong
from aruna.scanner.events import ScanResult
from aruna.upkeep.router import FaseRouter, HasilRouter

SAAT = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)
#: Bar SEBELUM `SAAT`. Pilihan tersimpan yang stempelnya sama dengan bar
#: sekarang akan dilewati, jadi fixture peralihan harus memakai bar yang lebih
#: tua - kalau tidak, ia diam-diam menguji jalur "sudah ditulis" alih-alih
#: jalur peralihan.
SEBELUM = datetime(2026, 8, 23, 9, 45, tzinfo=UTC)


def _Pindai(symbol: str, *, scanned: bool = True) -> ScanResult:
    """**`ScanResult` yang ASLI, dan itu koreksi 2026-08-23.**

    Versi pertama berkas ini memakai kelas palsu dengan bidang `asset_id` dan
    `market`. `ScanResult` tidak punya keduanya - bidangnya `symbol`, `events`,
    `usable_bars`, `scanned`, `reason`.

    Akibatnya `_terpindai` membuang SETIAP hasil pemindaian di produksi dan
    fase router diam tanpa satu pun galat, sementara seluruh berkas test ini
    hijau. Terbukti sesudah ARUNA dinyalakan: fase pindai berjalan 410 kali,
    baris router nol.

    Ini persis cacat yang sudah tercatat di proyek ini sebagai "palsu berbentuk
    salah" - dan aku mengulanginya. Karena itu sekarang yang dipakai kelas
    sungguhan, bukan tiruan yang bidangnya kupilih sendiri.
    """
    return ScanResult(symbol=symbol, events=(), usable_bars=50, scanned=scanned)


class _RepoPalsu:
    def __init__(
        self,
        peta: dict[str, tuple[Any, ...]] | None = None,
        riwayat: dict[str, tuple[str, ...]] | None = None,
        risiko: dict[str, str] | None = None,
        sebelumnya: dict[str, tuple[str | None, str | None, datetime | None]]
        | None = None,
    ) -> None:
        self._peta = peta or {}
        self._riwayat = riwayat or {}
        self._sebelumnya = sebelumnya or {}
        #: Bawaannya MODERATE, tingkat yang paling sering tersimpan - terukur
        #: 12.125 dari 15.901 baris dalam tujuh hari. Bawaan yang lolos gerbang
        #: dipilih supaya test yang TIDAK sedang menguji risiko tidak diam-diam
        #: menguji gerbangnya.
        self._risiko = risiko or {}

        self.disimpan: list[Any] = []

    async def peta_rezim(self, *, sekarang: datetime) -> dict[str, tuple[Any, ...]]:
        return self._peta

    async def riwayat_15m(self, *, sekarang: datetime) -> dict[str, tuple[str, ...]]:
        return self._riwayat

    async def risiko_terakhir(self, *, sekarang: datetime) -> dict[str, str]:
        return {s: self._risiko.get(s, "MODERATE") for s in self._peta}

    async def pilihan_terakhir(
        self,
    ) -> dict[str, tuple[str | None, str | None, datetime | None]]:
        """Stempel yang tersimpan ikut dilacak, seperti aslinya.

        Yang sudah ditulis di siklus ini masuk ke sini juga - kalau tidak, test
        dua-panggilan tidak bisa membuktikan bar yang sama tidak dikirim ulang.
        """
        keluar = dict(self._sebelumnya)
        for putusan, kw in self.disimpan:
            kode = None if putusan.champion is None else putusan.champion.kode
            keluar[kw["symbol"]] = (kode, putusan.regime, kw["dipilih_pada"])
        return keluar

    async def identitas(self) -> dict[str, tuple[int, Market]]:
        return {
            s: (i, Market.CRYPTO) for i, s in enumerate(sorted(self._peta), start=1)
        }

    async def simpan(self, putusan: Any, **kw: Any) -> int:
        self.disimpan.append((putusan, kw))
        return 1


class _PerformaMeledak:
    async def semua_slice(self) -> list[Any]:
        raise RuntimeError("tabel performa tidak terbaca")


def _b(interval: str, regime: str) -> Any:
    from aruna.router.rezim import BacaanRezim

    return BacaanRezim(interval=interval, regime=regime)


def _sepakat(regime: str = "TRENDING") -> tuple[Any, ...]:
    """Ketiga horizon sepakat: keyakinan 100, jauh di atas ambang."""
    return (_b("15m", regime), _b("1h", regime), _b("1d", regime))


def _fase(**kw: Any) -> FaseRouter:
    return FaseRouter(**kw)


class TestMemilihSungguhan:
    @pytest.mark.asyncio
    async def test_rezim_yang_bulat_menghasilkan_champion(self) -> None:
        """Ujung ke ujung: bacaan masuk, champion keluar, barisnya tersimpan."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat("TRENDING")})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.dipertimbangkan == 1
        assert hasil.terpilih == 1
        putusan, kw = repo.disimpan[0]
        assert putusan.champion.kode == "STR-001"
        assert kw["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_tren_berarah_tetap_menemukan_strategi_tren(self) -> None:
        """Terukur 2026-08-23: 438 bacaan 15m `TRENDING_BULLISH` dan 270
        `TRENDING_BEARISH` dalam tujuh hari, dan tidak satu pun strategi
        menulisnya di `preferred_regimes`. Pelipatan keluarga harus benar-benar
        sampai ke sini, bukan berhenti di test unit `kecocokan`."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat("TRENDING_BULLISH")})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.terpilih == 1

    @pytest.mark.asyncio
    async def test_stabilitas_ikut_terhitung_dan_tersimpan(self) -> None:
        repo = _RepoPalsu(
            {"BTC/USDT": _sepakat()},
            {"BTC/USDT": ("TRENDING",) * 8},
        )
        await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        _, kw = repo.disimpan[0]

        assert kw["stabilitas"] == 100.0


class TestMenolakDenganSebabYangTercatat:
    @pytest.mark.asyncio
    async def test_satu_horizon_ditolak_dan_sebabnya_dicatat(self) -> None:
        """15m sendirian berkeyakinan 20, jauh di bawah ambang 50 - dan itu
        keadaan yang SERING, bukan pinggiran."""
        repo = _RepoPalsu({"BTC/USDT": (_b("15m", "TRENDING"),)})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.terpilih == 0
        assert hasil.ditolak == {AlasanKosong.KEYAKINAN_KURANG: 1}
        assert "20" in repo.disimpan[0][0].alasan_kosong

    @pytest.mark.asyncio
    async def test_rezim_tanpa_strategi_ditolak_bukan_dipaksakan(self) -> None:
        """`HIGH_VOLATILITY` (453 bacaan) dan `ANOMALY` (49) tidak ada di
        `preferred_regimes` satu pun strategi, dan keluarganya pun tidak. NONE
        di sini jujur, bukan cacat."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat("HIGH_VOLATILITY")})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.terpilih == 0

    @pytest.mark.asyncio
    async def test_sebab_penolakan_dikelompokkan_bukan_ditumpuk(self) -> None:
        """"Router menolak 19 aset" tanpa pengelompokan tidak bisa dibantah.
        Sebab yang sama harus menumpuk di kunci yang sama walau angkanya
        berbeda - kalau tidak, tiap penolakan jadi kelompoknya sendiri dan
        laporannya sama tak bergunanya dengan daftar mentah."""
        repo = _RepoPalsu({
            "BTC/USDT": (_b("15m", "TRENDING"),),
            "ETH/USDT": (_b("1h", "RANGING"),),
        })
        hasil = await _fase(repo=repo).jalankan(
            [_Pindai("BTC/USDT"), _Pindai("ETH/USDT")], now=SAAT
        )

        assert list(hasil.ditolak) == [AlasanKosong.KEYAKINAN_KURANG]
        assert sum(hasil.ditolak.values()) == 2


class TestHanyaYangDipindai:
    @pytest.mark.asyncio
    async def test_aset_di_luar_pindaian_dilewati(self) -> None:
        """**Terukur 2026-08-23, dan bukan pinggiran.** Batas umur dihitung
        dalam bar horizon itu sendiri, jadi jendela 1d membentang delapan HARI -
        cukup untuk menghidupkan kembali aset yang sudah lama berhenti
        dipindai. 31 simbol punya bacaan "segar" sementara yang dipindai 20.

        Sebelas sisanya akan menghasilkan sebelas baris NONE tiap siklus, dan
        NONE yang tidak berarti apa-apa mengencerkan NONE yang berarti.
        """
        repo = _RepoPalsu({
            "BTC/USDT": _sepakat(),
            "SAHAM-LAMA": _sepakat(),
        })
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.dipertimbangkan == 1
        assert [kw["symbol"] for _, kw in repo.disimpan] == ["BTC/USDT"]

    @pytest.mark.asyncio
    async def test_simbol_tanpa_identitas_dilewati(self) -> None:
        """`router_pilihan` menyimpan `asset_id` sebagai kunci; menebaknya
        berarti menulis pilihan atas aset yang salah."""

        class _TanpaIdentitas(_RepoPalsu):
            async def identitas(self) -> dict[str, tuple[int, Market]]:
                return {}

        repo = _TanpaIdentitas({"BTC/USDT": _sepakat()})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.dipertimbangkan == 0
        assert repo.disimpan == []

    @pytest.mark.asyncio
    async def test_aset_yang_tidak_terpindai_dilewati(self) -> None:
        """`scanned=False` berarti buktinya tidak cukup untuk dipindai - dan
        yang buktinya tidak cukup untuk dipindai juga tidak cukup untuk
        dipilihkan strategi."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        hasil = await _fase(repo=repo).jalankan(
            [_Pindai("BTC/USDT", scanned=False)], now=SAAT
        )

        assert hasil.dipertimbangkan == 0

    @pytest.mark.asyncio
    async def test_dua_siklus_dalam_satu_bar_menulis_satu_stempel(self) -> None:
        """**Cacat yang terukur di produksi 2026-08-23, dan komentar migrasiku
        sendiri yang melarangnya.**

        Migrasi 0041 menulis: "menyimpan tiap peringkat berarti mengulang
        `market_snapshots`, yang menjadi 62% basis data ini dengan nol
        pembaca", dan kolomnya diberi komentar "awal bar yang jadi dasar
        keputusan, bukan jam sistem". Lalu `now` yang dioper.

        Kunci UNIQUE `(asset_id, dipilih_pada)` karena itu tidak pernah
        bentrok - resolusinya mikrodetik - dan `INSERT IGNORE` tidak pernah
        menggigit. Terukur: 18 siklus dalam 8,5 menit, 360 baris, proyeksi
        **60.632 baris per hari**. Dengan stempel bar: 20 aset x 96 bar =
        1.920.

        **Satu panggilan tidak bisa menangkap ini**, dan aturannya sudah
        tercatat di proyek ini: fase per-siklus harus diuji DUA panggilan.
        """
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        fase = _fase(repo=repo)
        await fase.jalankan([_Pindai("BTC/USDT")], now=SAAT + timedelta(seconds=3))

        stempel = repo.disimpan[0][1]["dipilih_pada"]

        assert stempel == SAAT, (
            "stempelnya jam sistem, bukan awal bar - kunci UNIQUE "
            "(asset_id, dipilih_pada) tidak akan pernah menggigit"
        )
        assert stempel.second == 0 and stempel.microsecond == 0

    @pytest.mark.asyncio
    async def test_bar_yang_sudah_ditulis_tidak_dikirim_ulang(self) -> None:
        """**Terukur di produksi 2026-08-23.** Stempel bar membuat kunci UNIQUE
        akhirnya menggigit - tapi tiap siklus berikutnya di bar yang sama tetap
        mengirim dua puluh INSERT yang diabaikan, dan tiap satu memuntahkan
        peringatan `Duplicate entry` ke log. Empat siklus per bar berarti enam
        puluh baris peringatan tiap bar, 5.760 sehari.

        Ditahan di hulu, bukan diserahkan kepada kunci: yang paling murah
        adalah tidak mengirimnya sama sekali.

        Ini juga memperbaiki `berganti` yang tadinya membandingkan pilihan
        dengan barisnya SENDIRI - `pilihan_terakhir` memulangkan baris dengan
        stempel terbaru, dan sesudah tulisan pertama di sebuah bar, baris itu
        adalah yang baru saja ditulis.
        """
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        fase = _fase(repo=repo)
        await fase.jalankan([_Pindai("BTC/USDT")], now=SAAT + timedelta(seconds=3))
        hasil = await fase.jalankan(
            [_Pindai("BTC/USDT")], now=SAAT + timedelta(seconds=41)
        )

        assert len(repo.disimpan) == 1
        assert hasil.dipertimbangkan == 0

    @pytest.mark.asyncio
    async def test_bar_berikutnya_stempelnya_berbeda(self) -> None:
        """Kebalikannya juga dijaga: stempel yang tidak pernah berubah berarti
        pilihan kedua dan seterusnya hilang selamanya."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        fase = _fase(repo=repo)
        await fase.jalankan([_Pindai("BTC/USDT")], now=SAAT)
        await fase.jalankan([_Pindai("BTC/USDT")], now=SAAT + timedelta(minutes=16))

        stempel = {kw["dipilih_pada"] for _, kw in repo.disimpan}

        assert len(stempel) == 2

    def test_bentuk_scanresult_yang_asli_dipakai(self) -> None:
        """**Penjaga anti-pengulangan.** Cacat yang baru saja terjadi: test
        double dengan bidang `asset_id` dan `market` yang `ScanResult` tidak
        punya, sehingga `_terpindai` membuang segalanya di produksi sementara
        berkas ini hijau.

        Test ini gagal kalau seseorang kembali menuntut bidang yang tidak ada.
        """
        import dataclasses

        bidang = {f.name for f in dataclasses.fields(ScanResult)}

        assert "asset_id" not in bidang
        assert {"symbol", "scanned"} <= bidang


class TestKegagalanTidakMenjatuhkan:
    @pytest.mark.asyncio
    async def test_tanpa_repo_fase_diam(self) -> None:
        hasil = await _fase().jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil == HasilRouter()

    @pytest.mark.asyncio
    async def test_performa_tak_terbaca_tetap_memeringkat(self) -> None:
        """**Router yang menolak berjalan karena tabel performa tidak terbaca
        akan berhenti justru pada hari tabel itu paling perlu diisi ulang.**

        Dan tanpa performa memang keadaan bawaan sesudah Task 3: seluruh slice
        per-rezim memulangkan None sampai baris berlabel router-1 cukup banyak -
        yang hanya lahir kalau router berjalan.
        """
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        hasil = await _fase(repo=repo, performa=_PerformaMeledak()).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )

        assert hasil.terpilih == 1

    @pytest.mark.asyncio
    async def test_satu_simpan_gagal_tidak_membuang_sisanya(self) -> None:
        class _Rewel(_RepoPalsu):
            async def simpan(self, putusan: Any, **kw: Any) -> int:
                if kw["symbol"] == "BTC/USDT":
                    raise RuntimeError("baris kembar")
                return await super().simpan(putusan, **kw)

        repo = _Rewel({"BTC/USDT": _sepakat(), "ETH/USDT": _sepakat()})
        hasil = await _fase(repo=repo).jalankan(
            [_Pindai("BTC/USDT"), _Pindai("ETH/USDT")], now=SAAT
        )

        assert hasil.dipertimbangkan == 2
        assert hasil.disimpan == 1


class TestGerbangRisikoSampaiKeSini:
    """Task 9. Gerbang yang berhenti di test unitnya sendiri dekoratif."""

    @pytest.mark.asyncio
    async def test_risiko_ekstrem_membatalkan_champion(self) -> None:
        """Champion yang lolos peringkat masih bisa gugur di gerbang. Terukur
        2026-08-23: 673 dari 15.901 baris tujuh hari terakhir tercatat
        EXTREME - 4,2%, jadi ini bukan jalur yang tak pernah dilalui."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()}, risiko={"BTC/USDT": "EXTREME"})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.terpilih == 0
        assert hasil.ditolak == {AlasanKosong.RISIKO_MENAHAN: 1}

    @pytest.mark.asyncio
    async def test_risiko_tinggi_lolos_dengan_peringatan_tercatat(self) -> None:
        repo = _RepoPalsu({"BTC/USDT": _sepakat()}, risiko={"BTC/USDT": "HIGH"})
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)
        putusan, _ = repo.disimpan[0]

        assert hasil.terpilih == 1
        assert any("memperingatkan" in a for a in putusan.alasan)

    @pytest.mark.asyncio
    async def test_tanpa_catatan_risiko_dicatat_belum_dinilai(self) -> None:
        """Champion yang lolos karena gerbangnya berjalan dan champion yang
        lolos karena gerbangnya tidak punya bahan terlihat sama dari luar."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()}, risiko={"BTC/USDT": "?"})
        await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)
        putusan, _ = repo.disimpan[0]

        assert putusan.champion is not None
        assert any("tidak dijalankan" in a for a in putusan.alasan)


class TestPeralihanSampaiKeSini:
    """Task 10. Contoh operator, lewat fase yang sungguhan."""

    @pytest.mark.asyncio
    async def test_contoh_operator_ujung_ke_ujung(self) -> None:
        """TRENDING -> RANGING: STR-001 gugur, STR-004 naik, dan peralihannya
        tercatat di baris yang tersimpan - bukan harus disimpulkan dengan
        membandingkan dua baris."""
        repo = _RepoPalsu(
            {"BTC/USDT": _sepakat("RANGING")},
            sebelumnya={"BTC/USDT": ("STR-001", "TRENDING", SEBELUM)},
        )
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)
        putusan, _ = repo.disimpan[0]

        assert hasil.berganti == 1
        assert putusan.champion.kode == "STR-004"
        assert any("TRENDING -> RANGING" in a for a in putusan.alasan)

    @pytest.mark.asyncio
    async def test_champion_yang_sama_tidak_dihitung_berganti(self) -> None:
        """Angka adaptasi yang naik tiap siklus tidak mengukur adaptasi."""
        repo = _RepoPalsu(
            {"BTC/USDT": _sepakat("TRENDING")},
            sebelumnya={"BTC/USDT": ("STR-001", "TRENDING", SEBELUM)},
        )
        hasil = await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert hasil.berganti == 0


class TestStatusDibacaDariTabelBukanDariKode:
    """**Cacat yang ditemukan saat mengukur Task 11, 2026-08-23.**

    Bentuknya persis varian yang berulang di proyek ini: fungsinya DIPANGGIL,
    tapi masukan yang membedakannya tidak pernah sampai. Sama seperti
    `diinvalidasi=False` yang dulu dipatok mati.

    Katalog di `learning/strategies.py` menulis SETIAP strategi `ACTIVE`.
    Tabel `strategies` - yang governance tulis berdasarkan pengukuran -
    mencatat lain::

        KODE:      STR-002 ACTIVE        STR-005 ACTIVE
        DATABASE:  STR-002 UNDER_REVIEW  STR-005 UNDER_REVIEW

        status_reason: "lebih buruk dari rata-rata pada 1043 sample;
                        cukup diukur untuk pantas dipertimbangkan dihentikan"

    Fase yang membaca katalog kode membuat seluruh pembedaan champion/
    challenger di Task 5 mati di produksi - dan matinya senyap: uji unitnya
    tetap hijau, karena ia mengoper statusnya sendiri.
    """

    @pytest.mark.asyncio
    async def test_status_dari_tabel_menang_atas_kode(self) -> None:
        """**Koreksi atas versi pertama test ini.** Aku sempat menuntut nol
        champion, dan itu salah: `STR-005` juga menyukai TRENDING, jadi ia naik
        menggantikan. Yang benar-benar dijaga bukan "tidak ada yang memimpin"
        melainkan "yang DITURUNKAN tidak memimpin"."""

        class _Katalog:
            async def status(self) -> dict[str, str]:
                return {"STR-001": "UNDER_REVIEW"}

        repo = _RepoPalsu({"BTC/USDT": _sepakat("TRENDING")})
        await _fase(repo=repo, status=_Katalog()).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )
        putusan, _ = repo.disimpan[0]

        assert putusan.champion is None or putusan.champion.kode != "STR-001"

    @pytest.mark.asyncio
    async def test_seluruh_kandidat_diturunkan_berarti_none(self) -> None:
        """Ujung yang sebenarnya: kalau SEMUA yang cocok diturunkan, tidak ada
        yang memimpin - dan itu keadaan nyata. Terukur 2026-08-23: BREAKOUT
        adalah rezim TERBANYAK (2.254 dari 9.437 bacaan 15m) dan kedua strategi
        yang menutupinya - STR-002 dan STR-005 - berstatus UNDER_REVIEW di
        tabel sementara katalog kode menulis keduanya ACTIVE."""

        class _Katalog:
            async def status(self) -> dict[str, str]:
                return {"STR-002": "UNDER_REVIEW", "STR-005": "UNDER_REVIEW"}

        repo = _RepoPalsu({"BTC/USDT": _sepakat("BREAKOUT")})
        hasil = await _fase(repo=repo, status=_Katalog()).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )

        assert hasil.terpilih == 0

    @pytest.mark.asyncio
    async def test_tabel_tak_terbaca_kembali_ke_kode(self) -> None:
        """Router yang menolak berjalan karena tabel status tidak terbaca akan
        berhenti justru pada hari tabel itu paling perlu diperbaiki. Yang
        benar: mundur ke katalog kode, dan **catat** bahwa itu terjadi."""

        class _Meledak:
            async def status(self) -> dict[str, str]:
                raise RuntimeError("strategies tidak terbaca")

        repo = _RepoPalsu({"BTC/USDT": _sepakat("TRENDING")})
        hasil = await _fase(repo=repo, status=_Meledak()).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )

        assert hasil.terpilih == 1

    @pytest.mark.asyncio
    async def test_status_asing_di_tabel_tidak_meloloskan(self) -> None:
        """Nilai yang tidak dikenal enum diperlakukan TIDAK BOLEH MEMIMPIN.
        Menebaknya sebagai ACTIVE berarti status baru yang lupa diurus lolos
        memimpin diam-diam."""

        class _Katalog:
            async def status(self) -> dict[str, str]:
                return dict.fromkeys(
                    ("STR-001", "STR-005"), "SEDANG_DIPIKIRKAN"
                )

        repo = _RepoPalsu({"BTC/USDT": _sepakat("TRENDING")})
        hasil = await _fase(repo=repo, status=_Katalog()).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )

        assert hasil.terpilih == 0


class TestStatusMenyaringSampaiKeSini:
    @pytest.mark.asyncio
    async def test_under_review_tidak_pernah_jadi_champion(self) -> None:
        """Kalau penyaringan status berhenti di test unit `peringkat`, fase ini
        akan tetap memimpin dengan strategi yang sudah diukur kalah."""
        ditimbang = Strategy(
            code="STR-XXX",
            name="uji",
            description="uji",
            conditions=(),
            preferred_regimes=("TRENDING",),
            preferred_horizons=("15m",),
            status=StrategyStatus.UNDER_REVIEW,
        )
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        hasil = await _fase(repo=repo, katalog=(ditimbang,)).jalankan(
            [_Pindai("BTC/USDT")], now=SAAT
        )

        assert hasil.terpilih == 0


class TestVersiIkutSupayaTidakMelingkar:
    @pytest.mark.asyncio
    async def test_baris_yang_ditulis_berlabel_router(self) -> None:
        """Tanpa label ini, slice performa per rezim kembali melingkar - dan
        seluruh Task 3 tidak menghasilkan apa-apa."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        await _fase(repo=repo).jalankan([_Pindai("BTC/USDT")], now=SAAT)

        assert VERSI_ROUTER == "router-1"
        assert repo.disimpan


class TestUmurBacaan:
    def test_jendela_lebih_panjang_daripada_kepadatan_sumbernya(self) -> None:
        """**Angka yang paling mudah salah dipilih di Phase 17.**

        `signal_snapshots` hanya dapat baris ketika sebuah sinyal terkunci,
        jadi kepadatannya jauh lebih jarang daripada barnya. Terukur 2026-08-23
        atas tujuh hari dan dua puluh aset: satu bacaan 1h tiap ~6 jam - enam
        BAR, bukan satu.

        Jendela yang lebih pendek daripada kepadatannya sendiri akan selalu
        membuang horizon itu, dan 15m sendirian berkeyakinan 20 dari ambang 50.
        Router tidak akan pernah memilih siapa pun, tanpa satu pun galat.
        """
        from aruna.db.repositories.router import umur_maksimum

        assert umur_maksimum("1h") >= timedelta(hours=6)
        assert umur_maksimum("15m") >= timedelta(minutes=21)
        assert umur_maksimum("1d") >= timedelta(hours=10)
