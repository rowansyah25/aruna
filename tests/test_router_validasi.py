"""Pilihan router diuji lintas periode (bagian 17.41 - 17.43).

**Mesinnya dipinjam, bukan ditulis ulang.** `backtest.walkforward` sudah punya
pembagi periode, penghitung fold, holdout yang dijaga, dan putusan
KONSISTEN/TIDAK KONSISTEN. Yang dibangun di sini cuma yang memberinya bahan.

Rencana Phase 17 menunda bagian ini karena "menyambungkannya adalah pekerjaan
tersendiri dengan gerbangnya sendiri". Gerbangnya ternyata sudah ada -
`MIN_FOLD_SAMPLE` dan `MIN_FOLDS` - dan keduanya menolak berbicara ketika
sampelnya kurang.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aruna.backtest.walkforward import MIN_FOLD_SAMPLE, MIN_FOLDS
from aruna.router.validasi import (
    LIPATAN,
    bagi_hasil,
    laporan_per_strategi,
    susun_split,
)

AWAL = datetime(2026, 7, 1, tzinfo=UTC)


def _r(
    hari: int, result: str, *, champion: str = "STR-001", pnl: str = "1"
) -> dict[str, Any]:
    return {
        "champion": champion,
        "regime": "TRENDING",
        "result": result,
        "net_pnl": pnl,
        "resolved_at": AWAL + timedelta(days=hari),
    }


def _deret(
    mulai: int, jumlah: int, menang: int, *, champion: str = "STR-001"
) -> list[dict[str, Any]]:
    return [
        _r(mulai, "WIN" if i < menang else "LOSS", champion=champion)
        for i in range(jumlah)
    ]


class TestRentangDiambilDariData:
    def test_periode_dari_data_bukan_dari_jam_sekarang(self) -> None:
        """Periode yang membentang ke masa depan menghasilkan fold kosong di
        ujung, dan fold kosong terbaca sebagai "sampelnya kurang" - keluhan
        yang benar atas sebab yang salah."""
        split = susun_split([_r(0, "WIN"), _r(40, "LOSS")])

        assert split is not None
        assert split.folds[0].start == AWAL
        # Ujungnya melewati titik terakhir satu satuan terkecil - ember foldnya
        # setengah terbuka, jadi tanpa itu hasil TERAKHIR jatuh di luar seluruh
        # ember. Ditemukan test 2026-08-24: dua puluh baris masuk, sembilan
        # belas terhitung.
        assert split.holdout_end == AWAL + timedelta(days=40) + timedelta.resolution

    def test_satu_titik_waktu_tidak_bisa_dibagi(self) -> None:
        """Jawaban yang sah, bukan kegagalan."""
        assert susun_split([_r(0, "WIN"), _r(0, "LOSS")]) is None

    def test_kosong_tidak_meledak(self) -> None:
        assert susun_split([]) is None


class TestHoldoutDijaga:
    def test_holdout_tidak_ikut_ke_fold_mana_pun(self) -> None:
        """**Seluruh gunanya.** Melaporkannya bersama yang lain akan
        menghabiskan satu-satunya data yang belum tersentuh."""
        baris = [_r(h, "WIN") for h in range(0, 40, 2)]
        split = susun_split(baris)
        laporan = bagi_hasil(baris, split=split)

        di_fold = sum(r.published for r in laporan.results)
        di_holdout = laporan.holdout.published

        assert di_holdout > 0
        assert di_fold + di_holdout == len(baris)

    def test_holdout_dilaporkan_terpisah(self) -> None:
        baris = [_r(h, "WIN") for h in range(0, 40, 2)]
        laporan = bagi_hasil(baris, split=susun_split(baris))

        assert laporan.holdout is not None
        assert laporan.to_dict()["holdout"] is not None


class TestGerbangSampelDipinjam:
    def test_sampel_kurang_menolak_berbicara(self) -> None:
        """`MIN_FOLD_SAMPLE` dan `MIN_FOLDS` sudah ada dan sudah menolak
        berbicara ketika sampelnya kurang - persis perilaku yang dituntut
        bagian 17.42. Tidak ada gerbang baru yang perlu ditulis."""
        baris = [_r(h, "WIN") for h in range(0, 40, 10)]
        laporan = bagi_hasil(baris, split=susun_split(baris))

        assert "INSUFFICIENT SAMPLE" in laporan.verdict

    def test_ambangnya_dipinjam_bukan_diketik_ulang(self) -> None:
        assert MIN_FOLD_SAMPLE > 0
        assert LIPATAN > MIN_FOLDS


class TestKonsistensiTerbaca:
    def test_akurasi_yang_stabil_terbaca_konsisten(self) -> None:
        baris: list[dict[str, Any]] = []
        for hari in (1, 8, 15, 22, 29):
            baris += _deret(hari, 20, 12)
        laporan = bagi_hasil(baris, split=susun_split(baris))

        assert "CONSISTENT" in laporan.verdict
        assert "INCONSISTENT" not in laporan.verdict

    def test_akurasi_yang_berayun_terbaca_tidak_konsisten(self) -> None:
        """**Ini yang bagian 17.42 minta diketahui.** ARUNA tidak mencocokkan
        parameter apa pun, jadi fold yang berbeda-beda tidak berarti kelebihan
        mencocokkan - ia berarti aturannya berperilaku sangat berbeda di pasar
        yang berbeda."""
        baris: list[dict[str, Any]] = []
        for hari, menang in ((1, 19), (8, 1), (15, 19), (22, 1), (29, 18)):
            baris += _deret(hari, 20, menang)
        laporan = bagi_hasil(baris, split=susun_split(baris))

        assert "INCONSISTENT" in laporan.verdict


class TestPerStrategiTidakDijumlahkan:
    def test_tiap_strategi_punya_laporannya_sendiri(self) -> None:
        """Router yang benar di satu strategi dan salah di dua lainnya punya
        rata-rata yang terlihat wajar, dan rata-rata itu tidak menggambarkan
        satu pun dari ketiganya - pelajaran yang sama dengan `regime=ALL`."""
        baris: list[dict[str, Any]] = []
        for hari in (1, 8, 15, 22, 29):
            baris += _deret(hari, 20, 18, champion="STR-001")
            baris += _deret(hari, 20, 2, champion="STR-004")
        laporan = laporan_per_strategi(baris)

        assert set(laporan) == {"STR-001", "STR-004"}
        baik = laporan["STR-001"].measured[0].accuracy
        buruk = laporan["STR-004"].measured[0].accuracy
        assert baik > buruk

    def test_foldnya_sama_untuk_semua_strategi(self) -> None:
        """Split dihitung sekali dari seluruh baris. Fold yang batasnya
        berbeda-beda membuat "fold 2" berarti periode yang berbeda untuk tiap
        strategi, dan laporannya tidak bisa disandingkan."""
        baris: list[dict[str, Any]] = []
        for hari in (1, 20):
            baris += _deret(hari, 12, 6, champion="STR-001")
        baris += _deret(30, 12, 6, champion="STR-004")
        laporan = laporan_per_strategi(baris)

        batas = {
            kode: [(f.fold.start, f.fold.end) for f in lap.results]
            for kode, lap in laporan.items()
        }

        assert batas["STR-001"] == batas["STR-004"]

    def test_penolakan_router_tidak_masuk(self) -> None:
        """Baris tanpa champion adalah penolakan router - ia bukan performa
        sebuah strategi."""
        baris = [*_deret(1, 10, 5), _r(2, "LOSS", champion="")]

        assert set(laporan_per_strategi(baris)) == {"STR-001"}


class TestYangBelumTuntasBukanSalah:
    def test_sinyal_terbuka_tidak_menurunkan_akurasi(self) -> None:
        """Sinyal yang belum tuntas bukan prediksi yang salah - ia prediksi
        yang belum dinilai. Menghitungnya menurunkan akurasi tiap fold yang
        kebetulan memuat banyak posisi terbuka."""
        tuntas = _deret(1, 20, 20)
        campur = [*tuntas, *[_r(1, "WAIT") for _ in range(20)]]

        a = bagi_hasil(tuntas, split=susun_split([*tuntas, _r(40, "WIN")]))
        b = bagi_hasil(campur, split=susun_split([*campur, _r(40, "WIN")]))

        assert a.results[0].accuracy == b.results[0].accuracy
