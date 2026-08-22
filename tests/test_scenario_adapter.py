"""Perilaku batas MiroFish: DEGRADED, TIMEOUT, dan gagal tanpa menjatuhkan.

Penjaga struktur batasnya - Protocol tanpa implementasi, tidak ada metode
eksekusi, tidak ada impor yang bisa memesan - ada di
`test_scenario_interface.py`, yang lahir untuk bagian 29 Phase 15 dan bertahan
ke sini karena bagian 16.16 menuntut hal yang sama. Berkas ini menguji apa yang
**terjadi**, bukan apa yang ada.

Satu kalimat menanggung seluruh bagian 16.12: `coba_simulasi` tidak pernah
melempar. Kalau ia melempar, tiap pemanggil harus membungkusnya dengan `try`,
dan pemanggil yang lupa satu kali menjatuhkan siklus yang seluruh pasal ini ada
untuk melindunginya.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from aruna.scenario.adapter import (
    TIMEOUT_DETIK,
    HasilAdapter,
    StatusSimulasi,
    coba_simulasi,
)
from aruna.scenario.mesin import simulasikan
from aruna.scenario.models import Invalidasi, Skenario
from aruna.scenario.pemicu import Peristiwa

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _skenario() -> Skenario:
    return Skenario(
        scenario_id="ext-1",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama="Bullish Continuation",
        deskripsi="dari mesin eksternal",
        kondisi_awal=("k",),
        pemicu="BREAKOUT_BESAR",
        perkembangan=("a", "b"),
        invalidasi=Invalidasi(syarat=("harga kembali ke rentang",)),
        risiko="MEDIUM",
        keyakinan=0.5,
        bobot=50,
        bukti=("b",),
        versi_simulasi="mirofish-0",
    )


class _Baik:
    async def simulasikan(self, *, pertanyaan, masukan):
        return (_skenario(),)


class _Lambat:
    async def simulasikan(self, *, pertanyaan, masukan):
        await asyncio.sleep(3600)
        return (_skenario(),)


class _Meledak:
    async def simulasikan(self, *, pertanyaan, masukan):
        raise RuntimeError("mesin eksternal rusak")


class _MeledakBukanException:
    """Mesin pihak ketiga bisa melempar apa saja - termasuk yang tidak turun
    dari `Exception`, dan `except Exception` tidak akan menangkapnya."""

    async def simulasikan(self, *, pertanyaan, masukan):
        raise BaseException("bukan turunan Exception")


async def _coba(mesin, **kw) -> HasilAdapter:
    return await coba_simulasi(
        mesin, pertanyaan="Simulasikan perkembangan.", masukan={"volume": 2.1}, **kw
    )


class TestMirofishAbsen:
    """Bagian 16.12."""

    @pytest.mark.asyncio
    async def test_tanpa_mesin_statusnya_degraded(self) -> None:
        hasil = await _coba(None)

        assert hasil.status is StatusSimulasi.DEGRADED

    @pytest.mark.asyncio
    async def test_tanpa_mesin_tidak_melempar(self) -> None:
        assert await _coba(None) is not None

    @pytest.mark.asyncio
    async def test_degraded_menyebut_sebabnya(self) -> None:
        """"MiroFish belum dipasang" dan "MiroFish bermasalah" menuntut
        tindakan yang berbeda; catatan kosong menyatukan keduanya."""
        assert (await _coba(None)).catatan

    @pytest.mark.asyncio
    async def test_mesin_internal_tetap_jalan_tanpa_mirofish(self) -> None:
        """Inti bagian 16.12. Adapter DEGRADED, dan ARUNA tetap menghasilkan
        tiga skenario dari mesinnya sendiri."""
        hasil = await _coba(None)
        internal = simulasikan(
            market="CRYPTO",
            asset="BTC/USDT",
            pemicu=frozenset({Peristiwa.BREAKOUT_BESAR}),
            kondisi_awal=("harga > resistance",),
            bukti=("struktur",),
            pada=NOW,
        )

        assert hasil.status is StatusSimulasi.DEGRADED
        assert len(internal) >= 3


class TestTimeout:
    """Bagian 16.13."""

    @pytest.mark.asyncio
    async def test_mesin_lambat_menghasilkan_timeout(self) -> None:
        hasil = await _coba(_Lambat(), timeout=0.01)

        assert hasil.status is StatusSimulasi.TIMEOUT

    @pytest.mark.asyncio
    async def test_pesannya_menyebut_simulation_timeout(self) -> None:
        """Kata yang bagian 16.13 pakai, supaya lognya bisa dicari dengan
        istilah spec-nya."""
        hasil = await _coba(_Lambat(), timeout=0.01)

        assert "SIMULATION TIMEOUT" in hasil.catatan

    @pytest.mark.asyncio
    async def test_hasil_telat_dibuang_bukan_dipakai(self) -> None:
        """Bagian 16.13 mengejanya: jangan menggunakan hasil yang sudah stale.
        Nol skenario, bukan skenario sebagian."""
        hasil = await _coba(_Lambat(), timeout=0.01)

        assert hasil.skenario == ()
        assert not hasil.terpakai

    def test_timeout_bawaannya_masuk_akal(self) -> None:
        assert 0 < TIMEOUT_DETIK <= 60


class TestKegagalanTidakMenjatuhkan:
    @pytest.mark.asyncio
    async def test_mesin_meledak_menghasilkan_gagal(self) -> None:
        hasil = await _coba(_Meledak())

        assert hasil.status is StatusSimulasi.GAGAL

    @pytest.mark.asyncio
    async def test_lemparan_bukan_exception_pun_tertangkap(self) -> None:
        """`except Exception` tidak menangkap ini, dan satu lemparan yang lolos
        menjatuhkan siklus yang seluruh bagian 16.12 ada untuk melindunginya."""
        hasil = await _coba(_MeledakBukanException())

        assert hasil.status is StatusSimulasi.GAGAL

    @pytest.mark.asyncio
    async def test_pembatalan_diteruskan_bukan_ditelan(self) -> None:
        """Pembatalan bukan kegagalan mesin - ia perintah berhenti. Menelannya
        membuat penghentian ARUNA menggantung sampai timeout."""

        class _Dibatalkan:
            async def simulasikan(self, *, pertanyaan, masukan):
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _coba(_Dibatalkan())

    @pytest.mark.asyncio
    async def test_catatan_kegagalan_menyebut_galatnya(self) -> None:
        hasil = await _coba(_Meledak())

        assert "mesin eksternal rusak" in hasil.catatan


class TestJalanNormal:
    @pytest.mark.asyncio
    async def test_mesin_baik_menghasilkan_ok(self) -> None:
        hasil = await _coba(_Baik())

        assert hasil.status is StatusSimulasi.OK
        assert hasil.terpakai

    @pytest.mark.asyncio
    async def test_skenarionya_diteruskan(self) -> None:
        hasil = await _coba(_Baik())

        assert len(hasil.skenario) == 1
        assert hasil.skenario[0].nama == "Bullish Continuation"

    @pytest.mark.asyncio
    async def test_hasilnya_selalu_tuple(self) -> None:
        """Mesin eksternal bisa memulangkan list; menyimpannya apa adanya
        membuat `HasilAdapter` yang beku punya isi yang bisa diubah."""

        class _Daftar:
            async def simulasikan(self, *, pertanyaan, masukan):
                return [_skenario()]

        assert isinstance((await _coba(_Daftar())).skenario, tuple)


class TestHanyaOkYangTerpakai:
    """Bagian 16.13: hasil sebagian dari simulasi bermasalah terlihat seperti
    bukti dan bukan bukti."""

    @pytest.mark.parametrize(
        "status",
        [StatusSimulasi.DEGRADED, StatusSimulasi.TIMEOUT, StatusSimulasi.GAGAL],
    )
    def test_selain_ok_tidak_terpakai(self, status) -> None:
        assert not HasilAdapter(status=status, skenario=(_skenario(),)).terpakai
