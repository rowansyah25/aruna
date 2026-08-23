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
from aruna.upkeep.router import FaseRouter, HasilRouter

SAAT = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)


class _Pindai:
    """Bentuknya mengikuti hasil pemindai, bukan apa yang mudah ditulis."""

    def __init__(self, symbol: str, asset_id: int | None = 1) -> None:
        self.symbol = symbol
        self.asset_id = asset_id
        self.market = Market.CRYPTO


class _RepoPalsu:
    def __init__(
        self,
        peta: dict[str, tuple[Any, ...]] | None = None,
        riwayat: dict[str, tuple[str, ...]] | None = None,
        risiko: dict[str, str] | None = None,
    ) -> None:
        self._peta = peta or {}
        self._riwayat = riwayat or {}
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
            [_Pindai("BTC/USDT", 1), _Pindai("ETH/USDT", 2)], now=SAAT
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
    async def test_hasil_pindai_tanpa_asset_id_dilewati(self) -> None:
        """`router_pilihan` menyimpan `asset_id` sebagai kunci; menebaknya
        berarti menulis pilihan atas aset yang salah."""
        repo = _RepoPalsu({"BTC/USDT": _sepakat()})
        hasil = await _fase(repo=repo).jalankan(
            [_Pindai("BTC/USDT", asset_id=None)], now=SAAT
        )

        assert hasil.dipertimbangkan == 0
        assert repo.disimpan == []


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
            [_Pindai("BTC/USDT", 1), _Pindai("ETH/USDT", 2)], now=SAAT
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
