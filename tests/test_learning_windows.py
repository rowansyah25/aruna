"""Perbandingan kinerja lintas jendela waktu (PASAL 11.20).

Yang diuji: apakah ia menahan diri pada sampel tipis, dan apakah ia tidak
membuat pergeseran dari dua angka yang sama-sama berisik.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from aruna.learning.breakdown import MIN_CELL_SAMPLE
from aruna.learning.windows import (
    MIN_SHIFT,
    QUALITY_BANDS,
    WINDOWS,
    Cell,
    build_window,
    quality_band,
    shifts,
)


def _rows(key: str, *, wins: int, losses: int, active: int = 0):
    return (
        [{"key": key, "result": "WIN"} for _ in range(wins)]
        + [{"key": key, "result": "LOSS"} for _ in range(losses)]
        + [{"key": key, "result": "OPEN"} for _ in range(active)]
    )


def _window(rows, window="all"):
    return build_window(rows, dimension="asset", window=window)


class TestAmbangSampel:
    def test_sel_tipis_diam_soal_win_rate(self) -> None:
        """"Aset terbaik" dari tiga perdagangan bukan aset terbaik."""
        (sel,) = _window(_rows("BTC/USDT", wins=3, losses=0)).cells
        assert sel.decided == 3
        assert sel.win_rate is None
        assert sel.needs == MIN_CELL_SAMPLE - 3

    def test_sel_cukup_menyebut_angkanya(self) -> None:
        (sel,) = _window(_rows("BTC/USDT", wins=9, losses=6)).cells
        assert sel.win_rate == 9 / 15

    def test_ambang_sama_dengan_rincian_agent(self) -> None:
        """Alasannya sama persis, jadi angkanya tidak boleh berbeda tanpa
        alasan yang juga berbeda."""
        assert Cell("x", wins=MIN_CELL_SAMPLE, losses=0).sufficient is True
        assert Cell("x", wins=MIN_CELL_SAMPLE - 1, losses=0).sufficient is False


class TestPosisiBelumSelesai:
    def test_active_tidak_masuk_penyebut(self) -> None:
        """Memasukkan posisi yang belum selesai ke salah satu sisi adalah cara
        termudah membuat angka terlihat lebih baik daripada kenyataannya."""
        (sel,) = _window(_rows("BTC/USDT", wins=10, losses=5, active=50)).cells
        assert sel.decided == 15
        assert sel.win_rate == 10 / 15
        assert sel.active == 50

    def test_hanya_active_belum_punya_win_rate(self) -> None:
        (sel,) = _window(_rows("BTC/USDT", wins=0, losses=0, active=99)).cells
        assert sel.win_rate is None
        assert sel.decided == 0

    def test_hasil_asing_dihitung_active(self) -> None:
        laporan = _window([{"key": "X", "result": "EXPIRED"}])
        assert laporan.cells[0].active == 1
        assert laporan.cells[0].decided == 0


class TestPeringkat:
    def _campur(self):
        return _window(
            _rows("BAGUS", wins=12, losses=3)      # 80%, terukur
            + _rows("BURUK", wins=3, losses=12)    # 20%, terukur
            + _rows("TIPIS", wins=2, losses=0)     # 100%, tipis
        )

    def test_yang_tipis_tidak_ikut_diperingkat(self) -> None:
        assert self._campur().best().key == "BAGUS"

    def test_terburuk_juga_dari_yang_terukur(self) -> None:
        assert self._campur().worst().key == "BURUK"

    def test_tanpa_sel_terukur_tidak_ada_pemenang(self) -> None:
        laporan = _window(_rows("X", wins=2, losses=1))
        assert laporan.best() is None
        assert laporan.worst() is None


class TestPergeseran:
    def _laporan(self, key, wins, losses, window):
        return _window(_rows(key, wins=wins, losses=losses), window=window)

    def test_pergeseran_dilaporkan_kalau_keduanya_cukup(self) -> None:
        baru = self._laporan("BTC/USDT", 4, 16, "30d")    # 20%
        lama = self._laporan("BTC/USDT", 16, 4, "all")    # 80%

        (geser,) = shifts(baru, lama)
        assert geser.delta == pytest.approx(-0.6)
        assert "turun" in geser.summary()

    def test_sisi_lama_tipis_tidak_menghasilkan_pergeseran(self) -> None:
        """Selisih antara dua angka berisik lebih berisik daripada
        masing-masingnya."""
        baru = self._laporan("BTC/USDT", 4, 16, "30d")
        lama = self._laporan("BTC/USDT", 2, 0, "all")
        assert shifts(baru, lama) == ()

    def test_sisi_baru_tipis_tidak_menghasilkan_pergeseran(self) -> None:
        baru = self._laporan("BTC/USDT", 2, 0, "30d")
        lama = self._laporan("BTC/USDT", 16, 4, "all")
        assert shifts(baru, lama) == ()

    def test_pergeseran_kecil_diabaikan(self) -> None:
        baru = self._laporan("BTC/USDT", 10, 10, "30d")   # 50%
        lama = self._laporan("BTC/USDT", 11, 9, "all")    # 55%
        assert shifts(baru, lama) == ()

    def test_kunci_yang_hanya_ada_di_satu_sisi_dilewati(self) -> None:
        baru = self._laporan("BARU", 16, 4, "30d")
        lama = self._laporan("LAMA", 4, 16, "all")
        assert shifts(baru, lama) == ()

    def test_yang_terbesar_lebih_dulu(self) -> None:
        baru = build_window(
            _rows("A", wins=2, losses=18) + _rows("B", wins=8, losses=12),
            dimension="asset", window="30d",
        )
        lama = build_window(
            _rows("A", wins=18, losses=2) + _rows("B", wins=14, losses=6),
            dimension="asset", window="all",
        )
        assert [s.key for s in shifts(baru, lama)] == ["A", "B"]


class TestRentangKualitas:
    @pytest.mark.parametrize(
        ("skor", "ember"),
        [(0, "0-59"), (59, "0-59"), (60, "60-69"), (75, "70-79"),
         (89, "80-89"), (90, "90-100"), (100, "90-100")],
    )
    def test_skor_masuk_ember_yang_benar(self, skor: int, ember: str) -> None:
        assert quality_band(skor) == ember

    def test_tanpa_skor_jadi_unknown(self) -> None:
        """Prediksi sebelum PASAL 11.1 ada tidak punya skor, dan menebaknya
        mencampur data terukur dengan data karangan."""
        assert quality_band(None) == "UNKNOWN"
        assert quality_band("bukan angka") == "UNKNOWN"

    def test_ember_menutupi_nol_sampai_seratus(self) -> None:
        batas = [(low, high) for _, low, high in QUALITY_BANDS]
        assert batas[0][0] == 0
        assert batas[-1][1] == 100
        for (_, high), (low, _) in pairwise(batas):
            assert low == high + 1


class TestJendela:
    def test_empat_jendela_pasal_11_20(self) -> None:
        assert [w for w, _ in WINDOWS] == ["today", "7d", "30d", "all"]

    def test_bersarang_dari_sempit_ke_lebar(self) -> None:
        """Hari ini ada di dalam tujuh hari, yang ada di dalam tiga puluh."""
        hari = [d for _, d in WINDOWS if d is not None]
        assert hari == sorted(hari)
        assert WINDOWS[-1][1] is None  # sepanjang waktu

    def test_kosong_bukan_kesalahan(self) -> None:
        laporan = _window([])
        assert laporan.cells == ()
        assert laporan.best() is None


class TestTerpasangDiCli:
    def test_perintah_history_terdaftar(self) -> None:
        from aruna.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["history", "--dimension", "asset"])
        assert args.dimension == "asset"
        assert args.func.__name__ == "cmd_history"

    def test_lima_dimensi_pasal_11_20(self) -> None:
        from aruna.db.repositories.learning import LearningRepository

        assert set(LearningRepository.WINDOW_COLUMNS) == {
            "asset", "timeframe", "regime", "direction", "quality",
        }

    def test_arah_diterjemahkan_di_sql(self) -> None:
        """"LONG" di laporan dan "BUY" di database jadi dua nama untuk satu hal
        yang harus dicocokkan orang di kepalanya."""
        from aruna.db.repositories.learning import LearningRepository

        sql = LearningRepository.WINDOW_COLUMNS["direction"]
        assert "'LONG'" in sql and "'SHORT'" in sql

    def test_jendela_diukur_dari_saat_hasil_diketahui(self) -> None:
        """Posisi yang dibuka dua minggu lalu dan ditutup kemarin adalah kabar
        kemarin, bukan kabar dua minggu lalu."""
        import inspect

        from aruna.db.repositories.learning import LearningRepository

        source = inspect.getsource(LearningRepository.window_rows)
        assert "t.exit_at >= %s" in source
        assert "t.entry_at >= %s" not in source

    def test_dimensi_asing_ditolak(self) -> None:
        import asyncio

        from aruna.db.repositories.learning import LearningRepository

        repo = LearningRepository.__new__(LearningRepository)
        with pytest.raises(ValueError, match="dimensi"):
            asyncio.run(repo.window_rows("'; DROP TABLE signals; --"))


def test_ambang_pergeseran_masuk_akal() -> None:
    assert 0 < MIN_SHIFT < 1
