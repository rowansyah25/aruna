"""Penguncian disebar antar siklus, bukan ditumpuk di satu siklus.

**Terukur 2026-08-22 sesudah restart.** ``_locked_bar`` hidup di memori proses,
jadi saat ARUNA menyala **seluruh** pasangan ``(market, horizon)`` jatuh tempo
sekaligus. Biaya sebuah pasangan sebanding jumlah simbolnya - council digelar
untuk tiap satu - sehingga siklus pertama menanggung semuanya: tidak selesai
selama lima menit, dan pemeriksa kesehatan melaporkan ``upkeep: DOWN - siklus
terakhir 5 menit lalu, lebih lama dari batas 60 detik``.

Prosesnya sendiri sehat. Ia cuma sedang mengerjakan segalanya sekaligus.

Yang **tidak** boleh rusak oleh perbaikan ini: yang tertunda harus tetap
kebagian, dan tidak boleh ada pasangan yang kelaparan karena tetangganya selalu
di depan antrean.
"""

from __future__ import annotations

from aruna.core.enums import Horizon, Market
from aruna.upkeep.loop import BATAS_KUNCI_PER_SIKLUS, UpkeepLoop


def _loop() -> UpkeepLoop:
    from aruna.core.config import UpkeepSettings

    return UpkeepLoop(
        refresher=None,
        resolver=None,
        locker=None,
        settings=UpkeepSettings(
            lock_enabled=False, resolve_enabled=False, news_enabled=False
        ),
    )


def _pasangan(n: int) -> list[tuple[Market, Horizon]]:
    semua = [(m, h) for m in Market for h in Horizon]
    return semua[:n]


class TestJatahPerSiklus:
    def test_di_bawah_jatah_semuanya_jalan(self) -> None:
        """Dalam keadaan mantap jatahnya hampir tidak pernah menggigit."""
        due = _pasangan(BATAS_KUNCI_PER_SIKLUS)
        jalan, tunda = _loop()._bagi_jatah_kunci(due)

        assert jalan == due
        assert tunda == []

    def test_di_atas_jatah_sisanya_ditunda(self) -> None:
        due = _pasangan(BATAS_KUNCI_PER_SIKLUS + 3)
        jalan, tunda = _loop()._bagi_jatah_kunci(due)

        assert len(jalan) == BATAS_KUNCI_PER_SIKLUS
        assert len(tunda) == 3

    def test_tidak_ada_yang_hilang(self) -> None:
        """Yang ditunda harus benar-benar ditunda, bukan dibuang."""
        due = _pasangan(6)
        jalan, tunda = _loop()._bagi_jatah_kunci(due)

        assert set(jalan) | set(tunda) == set(due)
        assert not set(jalan) & set(tunda)

    def test_daftar_kosong_tidak_meledak(self) -> None:
        assert _loop()._bagi_jatah_kunci([]) == ([], [])


class TestTidakAdaYangKelaparan:
    """Penjaga yang paling menentukan.

    Tanpa pemutaran, satu pasar yang kuncinya terus gagal akan selalu berada di
    depan antrean dan memakan seluruh jatah - pasar lain tidak pernah kebagian,
    dan kelaparan itu tidak menghasilkan satu pun galat.
    """

    def test_titik_awalnya_berputar(self) -> None:
        loop = _loop()
        due = _pasangan(6)

        pertama, _ = loop._bagi_jatah_kunci(due)
        kedua, _ = loop._bagi_jatah_kunci(due)

        assert pertama != kedua

    def test_semua_kebagian_dalam_beberapa_siklus(self) -> None:
        """Simulasi antrean yang **tidak pernah terkuras** - meniru pasangan
        yang kuncinya selalu gagal, sehingga ia tetap jatuh tempo tiap siklus.
        Tanpa pemutaran, sebagian tidak akan pernah tersentuh."""
        loop = _loop()
        due = _pasangan(6)
        tersentuh: set = set()

        for _ in range(6):
            jalan, _ = loop._bagi_jatah_kunci(due)
            tersentuh |= set(jalan)

        assert tersentuh == set(due), f"tidak tersentuh: {set(due) - tersentuh}"

    def test_antrean_yang_terkuras_selesai_cepat(self) -> None:
        """Jalur normal: yang sudah dikunci keluar dari antrean, jadi sisanya
        maju sendiri. Delapan pasangan harus habis dalam empat siklus."""
        loop = _loop()
        sisa = _pasangan(8)
        siklus = 0

        while sisa:
            _, tunda = loop._bagi_jatah_kunci(sisa)
            sisa = tunda
            siklus += 1
            assert siklus <= 10, "antrean tidak terkuras"

        assert siklus == 4


class TestStatnyaMembedakan:
    def test_menunggu_giliran_bukan_menunggu_bukti(self) -> None:
        """`lock_menunggu_candle` dan `lock_ditunda` menghitung dua hal yang
        berbeda: yang satu menunggu **bukti**, yang lain menunggu **giliran**.
        Menyatukannya membuat "candle-nya telat" tidak bisa dibedakan dari
        "antreannya panjang"."""
        from datetime import UTC, datetime

        from aruna.upkeep.loop import UpkeepStats

        s = UpkeepStats(started_at=datetime(2026, 8, 22, tzinfo=UTC))

        assert s.lock_menunggu_candle == 0
        assert s.lock_ditunda == 0
        assert hasattr(s, "lock_ditunda")


class TestJatahnyaMasukAkal:
    def test_lebih_dari_satu(self) -> None:
        """Jatah satu membuat antrean delapan butuh delapan siklus - empat
        menit, dan bar terpendek lima belas menit. Ketat tanpa perlu."""
        assert BATAS_KUNCI_PER_SIKLUS >= 2

    def test_cukup_kecil_untuk_berarti(self) -> None:
        """Jatah yang lebih besar dari jumlah pasangan yang mungkin tidak
        pernah menggigit, dan penjaga yang tidak pernah menggigit bukan
        penjaga."""
        assert len(Market) * len(Horizon) > BATAS_KUNCI_PER_SIKLUS
