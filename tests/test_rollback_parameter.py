"""Perubahan parameter otomatis tercatat dan bisa dibalikkan (bagian 23).

Bagian 23 menuntut lima bidang tersimpan untuk tiap perubahan: ``old_value``,
``new_value``, ``reason``, ``trigger``, ``timestamp`` - dan jalan kembali kalau
perubahannya memperburuk performa.

**Sisi proposal sudah terpenuhi sejak lama.** `governance/approval.py` menolak
menyetujui proposal yang validasinya tidak mendukung, dan membalikkan perubahan
aktif wajib menjadi proposal baru supaya tercatat, bukan diam-diam dibatalkan.

**Yang kosong adalah sisi otomatis.** Terukur 2026-08-21: proposal
`exit-at-target` berstatus APPROVED dengan `parameters: []`, dan `exit_at_target`
ternyata hanya ada di mesin backtest - `cli.py` sendiri menulis *"neither is the
live rule"*. Jadi proposal tidak pernah mengubah parameter hidup, dan tidak ada
yang bisa di-rollback dari sana.

Yang **memang** berubah sendiri dan memengaruhi keputusan adalah kalibrasi: ia
menimpa dirinya tiap hari, tanpa catatan apa yang berubah dan tanpa jalan
kembali. Modul ini menutup itu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.governance.rollback import (
    BATAS_RIWAYAT,
    KUNCI_STATE,
    PerubahanParameter,
    balikkan,
    catat,
    dari_json,
    ke_json,
    terakhir,
)

NOW = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)


def _ubah(baru: str, *, lama: str = "lama", pada: datetime = NOW) -> PerubahanParameter:
    return PerubahanParameter(
        nama="kalibrasi",
        lama=lama,
        baru=baru,
        alasan="brier 0.40 -> 0.38",
        pemicu="upkeep.review harian",
        pada=pada,
    )


class TestLimaBidangBagian23:
    def test_semua_bidang_yang_pasalnya_minta_ada(self) -> None:
        u = _ubah("baru")

        assert u.lama == "lama"
        assert u.baru == "baru"
        assert u.alasan
        assert u.pemicu
        assert u.pada == NOW

    def test_ringkas_menyebut_keduanya(self) -> None:
        """Catatan yang hanya menyebut nilai barunya membuat pembacanya tidak
        bisa tahu apa yang hilang."""
        r = _ubah("baru").ringkas()

        assert "lama" in r
        assert "baru" in r


class TestRiwayat:
    def test_yang_dicatat_bisa_dibaca_lagi(self) -> None:
        riwayat = catat((), _ubah("v1"))

        assert terakhir(riwayat, "kalibrasi").baru == "v1"

    def test_yang_terbaru_yang_dipulangkan(self) -> None:
        riwayat = catat(catat((), _ubah("v1")), _ubah("v2", lama="v1"))

        assert terakhir(riwayat, "kalibrasi").baru == "v2"

    def test_parameter_lain_tidak_tercampur(self) -> None:
        lain = PerubahanParameter(
            nama="reliability", lama="a", baru="b", alasan="x",
            pemicu="y", pada=NOW,
        )
        riwayat = catat(catat((), _ubah("v1")), lain)

        assert terakhir(riwayat, "kalibrasi").baru == "v1"
        assert terakhir(riwayat, "reliability").baru == "b"

    def test_yang_belum_pernah_berubah_memulangkan_none(self) -> None:
        assert terakhir((), "kalibrasi") is None

    def test_riwayat_berbatas(self) -> None:
        """Riwayat tanpa batas di `app_state` akan tumbuh selamanya - dan
        seluruh Phase 15.1 hari ini tentang tidak melakukan itu."""
        riwayat: tuple[PerubahanParameter, ...] = ()
        for i in range(BATAS_RIWAYAT + 20):
            riwayat = catat(riwayat, _ubah(f"v{i}", pada=NOW + timedelta(minutes=i)))

        assert len(riwayat) == BATAS_RIWAYAT
        # Yang dibuang yang paling lama, bukan yang paling baru.
        assert terakhir(riwayat, "kalibrasi").baru == f"v{BATAS_RIWAYAT + 19}"


class TestMembalikkan:
    def test_mengembalikan_nilai_sebelumnya(self) -> None:
        riwayat = catat((), _ubah("buruk", lama="bagus"))

        pulih, jejak = balikkan(riwayat, "kalibrasi", pemicu="operator", pada=NOW)

        assert pulih == "bagus"
        assert jejak.baru == "bagus"
        assert jejak.lama == "buruk"

    def test_pembalikan_ikut_tercatat(self) -> None:
        """Bagian 23 menuntut auditable. Pembalikan yang tidak tercatat membuat
        riwayatnya berbohong tentang apa yang pernah aktif."""
        riwayat = catat((), _ubah("buruk", lama="bagus"))

        _, jejak = balikkan(riwayat, "kalibrasi", pemicu="operator", pada=NOW)
        sesudah = catat(riwayat, jejak)

        assert terakhir(sesudah, "kalibrasi").baru == "bagus"
        assert "balik" in terakhir(sesudah, "kalibrasi").alasan.lower()

    def test_membalikkan_yang_belum_pernah_berubah_ditolak(self) -> None:
        """Memulangkan `None` diam-diam akan membuat pemanggil menerapkan
        `None` sebagai parameter."""
        with pytest.raises(ValueError):
            balikkan((), "kalibrasi", pemicu="operator", pada=NOW)

    def test_pemicunya_wajib(self) -> None:
        """Pembalikan tanpa pemicu tidak bisa dijawab pertanyaan 'kenapa ini
        dibalikkan' berbulan-bulan kemudian."""
        riwayat = catat((), _ubah("buruk", lama="bagus"))

        with pytest.raises(ValueError):
            balikkan(riwayat, "kalibrasi", pemicu="  ", pada=NOW)


class TestBolakBalikJson:
    def test_pulang_pergi_utuh(self) -> None:
        riwayat = catat(catat((), _ubah("v1")), _ubah("v2", lama="v1"))

        pulih = dari_json(ke_json(riwayat))

        assert len(pulih) == 2
        assert pulih[-1].baru == "v2"
        assert pulih[-1].pada == NOW

    def test_bentuk_rusak_tidak_meledak(self) -> None:
        """`app_state` yang ditulis versi lama tidak boleh menjatuhkan siklus
        upkeep - dan riwayat yang meledak lebih buruk daripada riwayat yang
        kosong."""
        assert dari_json(None) == ()
        assert dari_json([{"bukan": "bentuk yang benar"}]) == ()

    def test_kuncinya_stabil(self) -> None:
        assert KUNCI_STATE == "perubahan_parameter"


class TestTerpasangDiUpkeep:
    """Pencatat yang benar dan tidak dipanggil tidak mencatat apa pun."""

    class _State:
        def __init__(self) -> None:
            self.isi: dict[str, object] = {}

        async def get(self, key: str):
            return self.isi.get(key)

        async def set(self, key: str, value, *, actor: str) -> None:
            self.isi[key] = value

    class _Laporan:
        verdict = "OVERCONFIDENT in 80-96%"
        total = 777
        brier = 0.3998

    class _Sejarah:
        calibration_report = None

    def _loop(self, state):
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        loop = UpkeepLoop.__new__(UpkeepLoop)
        loop._settings = UpkeepSettings()
        loop._stats = UpkeepStats(started_at=NOW)
        loop._review_state = state
        return loop

    @pytest.mark.asyncio
    async def test_perubahan_pertama_tercatat(self) -> None:
        state = self._State()
        sejarah = self._Sejarah()
        sejarah.calibration_report = self._Laporan()

        await self._loop(state)._catat_perubahan_kalibrasi(sejarah, NOW)

        riwayat = dari_json(state.isi[KUNCI_STATE])
        assert terakhir(riwayat, "kalibrasi").baru == self._Laporan.verdict
        assert "belum pernah" in terakhir(riwayat, "kalibrasi").lama

    @pytest.mark.asyncio
    async def test_nilai_yang_sama_tidak_dicatat_ulang(self) -> None:
        """Baris identik tiap hari mengubur perubahan yang sesungguhnya di
        antara lima puluh baris yang tidak mengatakan apa-apa."""
        state = self._State()
        sejarah = self._Sejarah()
        sejarah.calibration_report = self._Laporan()
        loop = self._loop(state)

        await loop._catat_perubahan_kalibrasi(sejarah, NOW)
        await loop._catat_perubahan_kalibrasi(sejarah, NOW + timedelta(days=1))

        assert len(dari_json(state.isi[KUNCI_STATE])) == 1

    @pytest.mark.asyncio
    async def test_tanpa_state_tidak_meledak(self) -> None:
        sejarah = self._Sejarah()
        sejarah.calibration_report = self._Laporan()

        await self._loop(None)._catat_perubahan_kalibrasi(sejarah, NOW)

    @pytest.mark.asyncio
    async def test_kegagalan_tidak_menjatuhkan_review(self) -> None:
        """Catatan yang hilang lebih murah daripada kalibrasi yang tidak pernah
        diterapkan."""
        class _Meledak:
            async def get(self, key: str):
                raise RuntimeError("app_state sedang tidak bisa dibaca")

            async def set(self, key, value, *, actor):  # pragma: no cover
                raise AssertionError("tidak boleh sampai sini")

        sejarah = self._Sejarah()
        sejarah.calibration_report = self._Laporan()

        await self._loop(_Meledak())._catat_perubahan_kalibrasi(sejarah, NOW)

    def test_app_merangkai_statenya(self) -> None:
        import ast
        import inspect
        from textwrap import dedent

        from aruna import app as modul

        pohon = ast.parse(
            dedent(inspect.getsource(modul.ArunaApplication._start_upkeep))
        )
        kata = {
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "UpkeepLoop"
            for k in n.keywords
        }

        assert "review_state" in kata

    def test_review_memanggil_pencatatnya(self) -> None:
        import ast
        import inspect
        from textwrap import dedent

        from aruna.upkeep.loop import UpkeepLoop

        pohon = ast.parse(
            dedent(inspect.getsource(UpkeepLoop._review_pembelajaran))
        )
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "_catat_perubahan_kalibrasi" in dipanggil
