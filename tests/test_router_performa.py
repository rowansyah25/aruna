"""Performa per rezim: yang lama melingkar, yang baru mengukur.

**Temuan 2026-08-23.** `learning.strategies.classify()` menurunkan strategi
DARI rezim lewat peta balik satu-rezim-satu-strategi. Akibatnya sebuah strategi
hanya pernah dilabeli pada satu rezim, dan `regime=X` selalu memulangkan baris
yang sama persis dengan `regime=ALL`. Terukur di produksi:

    STR-005  regime=ALL       188W / 726L
    STR-005  regime=TRENDING  188W / 726L
    STR-002  regime=ALL       546W / 1605L
    STR-002  regime=BREAKOUT  546W / 1605L

Router yang memeringkat memakai angka itu memeringkat satu kandidat melawan
dirinya sendiri.

Operator memutuskan slice per rezim dipertahankan (2026-08-23). Jalan keluarnya
bukan menambah kolom melainkan mengganti SUMBER LABELNYA: begitu router yang
memilih - bukan turunan dari rezim - sebuah strategi bisa terpakai di beberapa
rezim, dan pasangan (strategi, rezim) menjadi pengamatan yang sungguhan.

Harganya jujur dan dijaga di sini: baris lama tidak bisa diselamatkan, dan
mencampurnya dengan baris baru menghasilkan angka yang bukan milik keduanya.
"""

from __future__ import annotations

from typing import Any

from aruna.learning.strategies import by_code, classify
from aruna.router.label import VERSI_ROUTER, dilabeli_router, performa_rezim


def _row(
    *,
    kode: str = "STR-005",
    regime: str = "TRENDING",
    versi: str = VERSI_ROUTER,
    menang: int = 60,
    n: int = 100,
) -> dict[str, Any]:
    return {
        "strategy_code": kode,
        "dimensions": {"regime": regime},
        "wins": menang,
        "sample_size": n,
        "model_version": versi,
    }


class TestBarisLamaMelingkar:
    def test_sebagian_besar_strategi_terkunci_ke_satu_rezim(self) -> None:
        """**Koreksi atas klaim pertamaku, 2026-08-23.** Aku sempat menulis
        "sebuah strategi hanya pernah dilabeli pada satu rezim" - dan test ini
        menolaknya: `STR-004` memetakan dari RANGING **dan** LOW_VOLATILITY.

        Jadi melingkarnya SEBAGIAN, bukan total. Yang terkunci satu-ke-satu -
        dan karena itu slice per-rezimnya identik dengan `regime=ALL` - adalah
        strategi yang `preferred_regimes`-nya tunggal. Itu yang terukur di
        produksi: STR-005 dan STR-002.

        Bedanya menentukan: untuk STR-004 slice per rezim SUDAH berarti hari
        ini, sementara untuk sisanya ia baru berarti sesudah router yang
        melabeli.
        """
        rezim = ("TRENDING", "RANGING", "BREAKOUT", "REVERSAL", "LOW_VOLATILITY")
        per_strategi: dict[str, set[str]] = {}
        for r in rezim:
            per_strategi.setdefault(classify(r), set()).add(r)

        terkunci = {k for k, v in per_strategi.items() if len(v) == 1}
        assert len(terkunci) >= 3, per_strategi
        assert "STR-004" not in terkunci

    def test_yang_terkunci_slice_rezimnya_sama_dengan_ALL(self) -> None:
        """Ini bentuk melingkarnya, dieja sebagai sifat bukan sebagai angka.
        Strategi yang cuma punya satu rezim preferensi akan selalu punya
        himpunan baris yang sama pada `regime=X` dan `regime=ALL`."""
        s = by_code("STR-002")

        assert s is not None
        assert len(s.preferred_regimes) == 1
        assert classify(s.preferred_regimes[0]) == "STR-002"

    def test_katalognya_sendiri_TIDAK_mengunci(self) -> None:
        """Yang mengunci bukan katalognya melainkan peta balik di `classify`.
        `preferred_regimes` sudah multi-nilai sejak awal - STR-005 menyukai
        TRENDING DAN BREAKOUT - jadi router boleh memakainya di keduanya."""
        s = by_code("STR-005")

        assert s is not None
        assert len(s.preferred_regimes) > 1
        assert {"TRENDING", "BREAKOUT"} <= set(s.preferred_regimes)


class TestMemisahkanSumberLabel:
    def test_baris_turunan_dikenali(self) -> None:
        assert not dilabeli_router(_row(versi="derivasi-1"))
        assert dilabeli_router(_row(versi=VERSI_ROUTER))

    def test_versi_router_boleh_bertambah_angka(self) -> None:
        """``router-1.2`` tetap baris router. Versi yang naik bukan sumber
        label yang berbeda - dan menuntut kecocokan persis akan membuang
        seluruh sejarah tiap kali parameternya disetel."""
        assert dilabeli_router(_row(versi=f"{VERSI_ROUTER}.2"))

    def test_baris_tanpa_versi_bukan_baris_router(self) -> None:
        assert not dilabeli_router({"strategy_code": "STR-005"})


class TestGerbangSampel:
    def test_baris_turunan_tidak_pernah_dipakai(self) -> None:
        """**Ujung yang sebenarnya dijaga.** Baris turunan punya 914 sampel -
        jauh di atas ambang mana pun - jadi tanpa penyaring ini ia akan lolos
        dan router memeringkat memakai angka yang melingkar."""
        lama = [_row(versi="derivasi-1", n=914, menang=188)]

        assert performa_rezim(lama, kode="STR-005", regime="TRENDING",
                              minimum=100) is None

    def test_sampel_kurang_memulangkan_none(self) -> None:
        """``None`` berarti BELUM BISA DIJAWAB, bukan nol. Pemanggil yang
        menyamakannya akan membuat tiap strategi baru terlihat gagal sejak
        hari pertama."""
        sedikit = [_row(n=40)]

        assert performa_rezim(sedikit, kode="STR-005", regime="TRENDING",
                              minimum=100) is None

    def test_sampel_cukup_menghasilkan_angka(self) -> None:
        cukup = [_row(n=60, menang=30), _row(n=60, menang=30)]
        hasil = performa_rezim(cukup, kode="STR-005", regime="TRENDING",
                               minimum=100)

        assert hasil is not None
        assert hasil.sample_size == 120
        assert hasil.win_rate == 0.5


class TestMenyaringYangBenar:
    def test_rezim_lain_tidak_ikut(self) -> None:
        rows = [
            _row(regime="TRENDING", n=100, menang=80),
            _row(regime="BREAKOUT", n=100, menang=10),
        ]
        hasil = performa_rezim(rows, kode="STR-005", regime="TRENDING",
                               minimum=100)

        assert hasil.sample_size == 100
        assert hasil.win_rate == 0.8

    def test_strategi_lain_tidak_ikut(self) -> None:
        rows = [
            _row(kode="STR-005", n=100, menang=80),
            _row(kode="STR-002", n=100, menang=10),
        ]
        hasil = performa_rezim(rows, kode="STR-005", regime="TRENDING",
                               minimum=100)

        assert hasil.sample_size == 100

    def test_dimensions_yang_datang_sebagai_teks_json(self) -> None:
        """MySQL memulangkan kolom JSON sebagai `str` lewat asyncmy. Baris yang
        tidak terurai akan diam-diam tidak pernah cocok, dan slice per rezim
        akan selamanya `None` tanpa satu pun galat."""
        rows = [{**_row(n=120), "dimensions": '{"regime": "TRENDING"}'}]
        hasil = performa_rezim(rows, kode="STR-005", regime="TRENDING",
                               minimum=100)

        assert hasil is not None
        assert hasil.sample_size == 120
