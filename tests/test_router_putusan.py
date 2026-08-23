"""Champion, challenger, dan penolakan yang jujur (bagian 17.17-17.18, 17.29-17.30).

**Yang paling dijaga di sini kemampuan memulangkan NONE.** Bagian 17.29
melarang memaksa memilih strategi hanya supaya sistem menghasilkan arah, dan
angka nyatanya menuntut itu: win rate tertinggi di katalog sekarang 25,4%.
Router yang selalu memilih seseorang akan memilih yang kalah.

Kosakata status yang dipakai di sini yang **benar-benar ada**. Rencana Phase 17
menulis `StrategyStatus.DISABLED`; nilai itu tidak pernah ada. Yang ada: ACTIVE,
DEGRADED, UNDER_REVIEW, SUSPENDED, RETIRED.

Dan artinya diambil dari kata-katanya sendiri di produksi, bukan dikarang.
Diperiksa 2026-08-23::

    STR-002  UNDER_REVIEW  lebih buruk dari rata-rata pada 1043 sample; cukup
                           diukur untuk pantas dipertimbangkan dihentikan
    STR-005  UNDER_REVIEW  lebih buruk dari rata-rata pada 213 sample; ...
    STR-000  UNDER_REVIEW  penampung, bukan strategi yang dipilih siapa pun

"Sedang ditimbang" bukan "dimatikan" - jadi ia boleh jadi CHALLENGER dan tidak
boleh jadi CHAMPION. Slot challenger memang untuk itu.
"""

from __future__ import annotations

from aruna.learning.strategies import Strategy, StrategyStatus
from aruna.router.kecocokan import Kecocokan
from aruna.router.peringkat import kandidat_layak
from aruna.router.putusan import (
    AMBANG_KEYAKINAN_REZIM,
    AMBANG_LAYAK,
    PutusanRouter,
    pilih,
)
from aruna.router.rezim import PetaRezim


def _peta(regime: str = "TRENDING", keyakinan: float = 85.0) -> PetaRezim:
    return PetaRezim(regime, keyakinan, (), (), ())


def _k(kode: str, skor: int, *, sampel: int = 900) -> Kecocokan:
    return Kecocokan(kode=kode, skor=skor, alasan=("uji",), sampel=sampel)


def _s(kode: str, status: StrategyStatus) -> Strategy:
    return Strategy(
        code=kode,
        name="uji",
        description="uji",
        conditions=(),
        preferred_regimes=("TRENDING",),
        preferred_horizons=("15m",),
        status=status,
    )


class TestMenolakDenganJujur:
    """Bagian 17.29-17.30."""

    def test_rezim_tak_terbaca_tidak_memilih_siapa_pun(self) -> None:
        buta = PetaRezim(None, 0.0, (), (), ())
        hasil = pilih((_k("STR-001", 95),), peta=buta)

        assert hasil.champion is None
        assert hasil.alasan_kosong

    def test_uncertain_diperlakukan_tidak_terbaca(self) -> None:
        """**19,7% bacaan, dan mendiamkannya adalah bug.** Terukur 2026-08-23:
        1.860 dari 9.437 bacaan 15m dalam tujuh hari berlabel `UNCERTAIN`.

        Itu classifier yang mengaku tidak tahu, bukan sebuah rezim. Kalau
        dipakai apa adanya, ia rezim yang tidak ada di `preferred_regimes`
        siapa pun - jadi SETIAP strategi jatuh di bawah NETRAL dan router
        menolak dengan alasan yang salah ("skor tertinggi di bawah ambang")
        alih-alih alasan yang benar ("rezimnya belum terbaca").

        Prinsipnya sudah berlaku di Phase 16: `konteks_pemicu.TIDAK_TERBACA`
        memperlakukan UNCERTAIN sejajar dengan NULL, bukan sebagai rezim
        tersendiri.
        """
        hasil = pilih((_k("STR-001", 95),), peta=_peta("UNCERTAIN", 95.0))

        assert hasil.champion is None
        assert "terbaca" in hasil.alasan_kosong.lower()

    def test_keyakinan_rezim_rendah_menolak_dan_menyebut_angkanya(self) -> None:
        """Bagian 17.30. Angka yang tidak disebut tidak bisa dibantah."""
        hasil = pilih((_k("STR-001", 95),), peta=_peta("TRENDING", 41.0))

        assert hasil.champion is None
        assert "41" in hasil.alasan_kosong

    def test_skor_tertinggi_di_bawah_ambang_menolak(self) -> None:
        """Bagian 17.29 apa adanya: dilarang memaksa memilih hanya supaya
        sistem menghasilkan arah."""
        hasil = pilih((_k("STR-001", 51), _k("STR-004", 55)), peta=_peta())

        assert hasil.champion is None
        assert "55" in hasil.alasan_kosong

    def test_tanpa_kandidat_sama_sekali_tetap_menjawab(self) -> None:
        hasil = pilih((), peta=_peta())

        assert hasil.champion is None
        assert hasil.alasan_kosong


class TestAmbangnyaDiturunkanBukanDipinjam:
    def test_ambang_keyakinan_adalah_mayoritas_bobot_horizon(self) -> None:
        """**Rencana menyuruh meminjam `signals.quality.MIN_QUALITY` (60).
        Itu ditolak, dan sebabnya ada di Global Constraints rencana itu
        sendiri:** ambang yang dipinjam harus dipinjam dari pertanyaan yang
        sama. MIN_QUALITY menjawab "berapa skor minimum agar sebuah kandidat
        SINYAL boleh terbit" - pertanyaan yang berbeda, dan salah pinjam sudah
        tiga kali jadi bug di proyek ini.

        Yang dipakai justru diturunkan dari bentuk `primary_confidence` itu
        sendiri. Karena ia = cakupan x kesepakatan x keyakinan, "lebih dari
        setengah bobot horizon mendukung primary" berarti tepat 50. Dan itu
        klaim yang bisa dipertahankan: bagian 17.8 ada supaya satu horizon
        pendek tidak memutuskan sendirian.
        """
        assert AMBANG_KEYAKINAN_REZIM == 50.0

    def test_satu_horizon_sendirian_tidak_pernah_lolos(self) -> None:
        """Konsekuensi yang diukur dari bobot nyata (15m 1,0 / 1h 1,6 /
        1d 2,4). Sendirian, yang terberat sekalipun hanya mencapai 48 - dan
        itu memang di bawah setengah."""
        from aruna.router.rezim import BacaanRezim, susun_peta

        for interval in ("15m", "1h", "1d"):
            peta = susun_peta((BacaanRezim(interval, "TRENDING"),))

            assert peta.primary_confidence < AMBANG_KEYAKINAN_REZIM, interval

    def test_dua_horizon_yang_sepakat_lolos(self) -> None:
        """Dan yang paling ringan pun cukup: 15m + 1h sepakat memberi 52."""
        from aruna.router.rezim import BacaanRezim, susun_peta

        peta = susun_peta((
            BacaanRezim("15m", "TRENDING"), BacaanRezim("1h", "TRENDING"),
        ))

        assert peta.primary_confidence > AMBANG_KEYAKINAN_REZIM

    def test_ambang_layak_di_atas_netral(self) -> None:
        """Yang bisa dipertahankan bukan angkanya melainkan bahwa ia DI ATAS
        NETRAL - lima puluh adalah skor strategi yang rezimnya tidak cocok
        maupun tidak bertentangan, dan memilih atas dasar itu berarti memilih
        tanpa alasan."""
        from aruna.router.kecocokan import NETRAL

        assert AMBANG_LAYAK > NETRAL


class TestChampionDanChallenger:
    """Bagian 17.17-17.18."""

    def test_skor_tertinggi_jadi_champion(self) -> None:
        hasil = pilih((_k("STR-004", 84), _k("STR-001", 91)), peta=_peta())

        assert hasil.champion.kode == "STR-001"
        assert hasil.challenger.kode == "STR-004"
        assert not hasil.alasan_kosong

    def test_challenger_kosong_kalau_cuma_satu_yang_layak(self) -> None:
        """``None`` berarti tidak ada penantang, bukan penantang bernilai nol."""
        hasil = pilih((_k("STR-001", 91), _k("STR-004", 55)), peta=_peta())

        assert hasil.champion.kode == "STR-001"
        assert hasil.challenger is None

    def test_seri_diputus_stabil_bukan_urutan_masuk(self) -> None:
        """Dua skor yang sama harus memberi jawaban yang sama tiap kali.
        Bersandar pada urutan berarti champion berubah karena urutan baris
        yang kebetulan keluar dari database."""
        kandidat = (_k("STR-004", 88), _k("STR-001", 88))

        assert pilih(kandidat, peta=_peta()).champion.kode == (
            pilih(kandidat[::-1], peta=_peta()).champion.kode
        )


class TestStatusMenyaringDiHulu:
    """Bagian 17.13, dengan kosakata status yang benar-benar ada."""

    def test_aktif_boleh_jadi_champion(self) -> None:
        layak = kandidat_layak((_s("STR-001", StrategyStatus.ACTIVE),))

        assert [s.code for s in layak.champion] == ["STR-001"]

    def test_sedang_ditimbang_boleh_menantang_tapi_tidak_memimpin(self) -> None:
        """**Ini keputusan yang paling berdampak di Task 5, dan angkanya
        menuntutnya.**

        `STR-002` (BREAKOUT) dan `STR-005` (TRENDING, BREAKOUT) keduanya
        UNDER_REVIEW. Membuang UNDER_REVIEW sepenuhnya berarti BREAKOUT -
        rezim TERBANYAK, 2.254 dari 9.437 bacaan 15m - tidak punya satu pun
        kandidat, selamanya.

        Tapi alasan statusnya juga tidak boleh diabaikan: "lebih buruk dari
        rata-rata pada 1043 sample". Menjadikannya champion berarti memimpin
        dengan strategi yang sudah diukur kalah.

        Slot challenger justru untuk keadaan ini.
        """
        layak = kandidat_layak((
            _s("STR-001", StrategyStatus.ACTIVE),
            _s("STR-002", StrategyStatus.UNDER_REVIEW),
        ))

        assert [s.code for s in layak.champion] == ["STR-001"]
        assert {s.code for s in layak.challenger} == {"STR-001", "STR-002"}

    def test_degraded_diperlakukan_sama_dengan_sedang_ditimbang(self) -> None:
        layak = kandidat_layak((_s("STR-009", StrategyStatus.DEGRADED),))

        assert layak.champion == ()
        assert [s.code for s in layak.challenger] == ["STR-009"]

    def test_dihentikan_dan_pensiun_tidak_pernah_muncul(self) -> None:
        layak = kandidat_layak((
            _s("STR-007", StrategyStatus.SUSPENDED),
            _s("STR-008", StrategyStatus.RETIRED),
        ))

        assert layak.champion == ()
        assert layak.challenger == ()

    def test_penampung_tidak_pernah_dipilih(self) -> None:
        """`STR-000` menyatakan dirinya sendiri: "penampung, bukan strategi
        yang dipilih siapa pun; besarnya mengukur kelengkapan katalog".
        Memilihnya berarti melaporkan ketiadaan strategi sebagai sebuah
        strategi."""
        layak = kandidat_layak((_s("STR-000", StrategyStatus.ACTIVE),))

        assert layak.champion == ()
        assert layak.challenger == ()


class TestBentuknya:
    def test_putusannya_beku(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(PutusanRouter)
        assert PutusanRouter.__dataclass_params__.frozen

    def test_champion_ada_berarti_alasan_kosong_kosong(self) -> None:
        """Dua bidang yang bisa terisi bersamaan adalah dua sumber kebenaran
        yang bisa bertentangan. Yang satu terisi berarti yang lain tidak."""
        ada = pilih((_k("STR-001", 91),), peta=_peta())
        tidak = pilih((_k("STR-001", 10),), peta=_peta())

        assert bool(ada.champion) is not bool(ada.alasan_kosong)
        assert bool(tidak.champion) is not bool(tidak.alasan_kosong)
