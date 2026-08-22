"""Denyut, dan berapa lama ARUNA sempat mati.

Kesalahan baca yang paling mahal di sistem ini tidak melibatkan satu pun angka
yang salah: operator membaca diam sebagai "tidak ada setup", padahal ARUNA
sedang mati. Keduanya terlihat persis sama di layar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.health.heartbeat import (
    ACTOR,
    HEARTBEAT_KEY,
    MIN_GAP_SEC,
    Jeda,
    beat,
    check,
    gap_of,
    last_beat,
)

NOW = datetime(2026, 8, 19, 17, 14, tzinfo=UTC)


class FakeState:
    """``app_state`` secukupnya, dan mencatat siapa yang menulis."""

    def __init__(self, isi: dict | None = None) -> None:
        self.isi = dict(isi or {})
        self.penulis: list[str] = []

    async def get(self, key: str):
        return self.isi.get(key)

    async def set(self, key: str, value: dict, *, actor: str) -> None:
        self.isi[key] = value
        self.penulis.append(actor)


class TestDenyut:
    @pytest.mark.asyncio
    async def test_denyut_tersimpan_dan_terbaca(self) -> None:
        state = FakeState()

        await beat(state, NOW)

        assert await last_beat(state) == NOW
        assert state.penulis == [ACTOR]

    @pytest.mark.asyncio
    async def test_belum_pernah_berdenyut(self) -> None:
        assert await last_beat(FakeState()) is None

    @pytest.mark.asyncio
    async def test_nilai_rusak_diperlakukan_seperti_tidak_ada(self) -> None:
        """Nilai yang tidak bisa dibaca sebagai denyut di tahun nol akan
        mengirim alarm "mati 2026 tahun" atas satu baris yang rusak."""
        state = FakeState({HEARTBEAT_KEY: {"at": "bukan tanggal"}})

        assert await last_beat(state) is None

    @pytest.mark.asyncio
    async def test_nilai_kosong_diperlakukan_seperti_tidak_ada(self) -> None:
        assert await last_beat(FakeState({HEARTBEAT_KEY: {}})) is None

    @pytest.mark.asyncio
    async def test_kegagalan_penyimpanan_tidak_ditelan(self) -> None:
        """Denyut yang gagal diam-diam menghasilkan laporan waktu mati yang
        mengarang jendelanya."""

        class Rusak(FakeState):
            async def set(self, *a, **k):
                raise RuntimeError("basis data mati")

        with pytest.raises(RuntimeError):
            await beat(Rusak(), NOW)


class TestJeda:
    def test_tanpa_denyut_sebelumnya_bukan_waktu_mati(self) -> None:
        """Ini pemasangan baru. Melaporkan "mati sejak awal waktu" pada
        penyalaan pertama adalah alarm yang isinya kekosongan basis data."""
        assert gap_of(None, NOW) is None

    def test_jam_mundur_bukan_waktu_mati(self) -> None:
        """"ARUNA mati -3 jam" lebih buruk daripada diam."""
        assert gap_of(NOW + timedelta(hours=1), NOW) is None
        assert gap_of(NOW, NOW) is None

    def test_jeda_dihitung_dari_denyut_terakhir(self) -> None:
        j = gap_of(NOW - timedelta(hours=3, minutes=12), NOW)

        assert j is not None
        assert j.seconds == pytest.approx(3 * 3600 + 12 * 60)

    def test_restart_rutin_tidak_dilaporkan(self) -> None:
        """Restart tercepat terukur 10,1 detik. Ambangnya tidak boleh
        menyentuhnya."""
        j = gap_of(NOW - timedelta(seconds=11), NOW)

        assert j is not None
        assert not j.reportable

    def test_tepat_satu_siklus_perencanaan_sudah_dilaporkan(self) -> None:
        """Di atas ambang, setidaknya satu siklus perencanaan tidak pernah
        terjadi - dan jendela itulah yang salah dibaca sebagai "tidak ada
        setup"."""
        j = gap_of(NOW - timedelta(seconds=MIN_GAP_SEC), NOW)

        assert j is not None
        assert j.reportable

    def test_sedikit_di_bawah_ambang_belum_dilaporkan(self) -> None:
        j = gap_of(NOW - timedelta(seconds=MIN_GAP_SEC - 1), NOW)

        assert j is not None
        assert not j.reportable

    def test_ambangnya_satu_siklus_futures(self) -> None:
        """900 detik - irama loop futures, bukan angka karangan."""
        assert MIN_GAP_SEC == 900.0


class TestKalimat:
    def test_menyebut_jendelanya_bukan_hanya_durasinya(self) -> None:
        """"Mati 3 jam" bicara tentang mesin. "Antara 14:02 dan 17:14 tidak ada
        analisis" bicara tentang apa yang harus dilakukan operator dengan
        ingatannya tentang jam-jam itu."""
        j = Jeda(since=NOW - timedelta(hours=3, minutes=12), until=NOW)
        teks = j.line()

        assert "14:02" in teks
        assert "17:14" in teks

    def test_menyatakan_diam_itu_bukan_pendapat(self) -> None:
        teks = Jeda(since=NOW - timedelta(hours=3), until=NOW).line()

        assert "BUKAN" in teks
        assert "tidak ada setup" in teks

    def test_durasi_terbaca_manusia(self) -> None:
        assert Jeda(NOW - timedelta(hours=3, minutes=12), NOW).human() == (
            "3 jam 12 menit"
        )
        assert Jeda(NOW - timedelta(hours=2), NOW).human() == "2 jam"
        assert Jeda(NOW - timedelta(minutes=25), NOW).human() == "25 menit"


class TestPemeriksaan:
    @pytest.mark.asyncio
    async def test_hanya_melapor_kalau_layak(self) -> None:
        lama = FakeState({HEARTBEAT_KEY: {"at": "2026-08-19T14:02:00.000Z"}})
        baru = FakeState({HEARTBEAT_KEY: {"at": "2026-08-19T17:13:50.000Z"}})

        assert await check(lama, NOW) is not None
        assert await check(baru, NOW) is None

    @pytest.mark.asyncio
    async def test_tanpa_denyut_tidak_melapor(self) -> None:
        assert await check(FakeState(), NOW) is None
