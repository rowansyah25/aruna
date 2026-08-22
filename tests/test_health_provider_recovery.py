"""Pemeriksa provider harus bisa pulih.

**Terukur di produksi.** ``provider:yahoo`` melaporkan "11 observation(s)
failed quality checks" berjam-jam, sementara proses baru yang dijalankan pada
menit yang sama melaporkan UP dengan nol penolakan. Data IDX-nya tidak pernah
berhenti mengalir; yang rusak hanya laporannya.

Sebabnya: ``QualityGate.rejection_counts()`` menjumlahkan penolakan seumur
proses dan tidak pernah direset, dan pemeriksa ini memperlakukan angka
bukan-nol apa pun sebagai "sedang bermasalah". Sekali merah, merah selamanya.

Itu keluarga cacat yang sama dengan aliran WebSocket yang diperbaiki
sebelumnya, dan ongkosnya sama: pemeriksa yang tidak bisa pulih mengajari
pembacanya mengabaikan warnanya - lalu peringatan berikutnya, yang mungkin
nyata, ikut terabaikan.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import HealthStatus
from aruna.health.providers import DEGRADED_REJECTION_SHARE, ProviderCheck


class _Gate:
    def __init__(self) -> None:
        self.totals: dict[str, int] = {}

    def rejection_counts(self) -> dict[str, int]:
        return dict(self.totals)

    def observed_clock_skew_sec(self) -> float | None:
        return None


class _Provider:
    name = "fake"

    @property
    def capabilities(self):
        from aruna.core.enums import Market
        from aruna.data.provider import ProviderCapabilities, Transport

        return ProviderCapabilities(
            name=self.name,
            market=Market.IDX,
            transport=Transport.POLL,
            is_realtime=True,
            expected_delay_sec=0,
            supports_order_book=False,
            supported_intervals=(),
            max_candles_per_request=100,
            requires_credentials=False,
            regulatory_note="test double",
        )

    async def status(self):
        from aruna.data.provider import ProviderStatus

        return ProviderStatus(reachable=True, detail="reachable")


class _Ingestor:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate
        self.provider = _Provider()


def _check() -> tuple[ProviderCheck, _Gate]:
    gate = _Gate()
    return ProviderCheck(_Ingestor(gate)), gate


class TestBisaPulih:
    @pytest.mark.asyncio
    async def test_penolakan_lama_tidak_menahan_status_selamanya(self) -> None:
        """Inti seluruh perbaikan ini."""
        cek, gate = _check()

        # Sapuan pertama: semuanya ditolak.
        gate.totals = {"ABNORMAL_PRICE": 11}
        assert (await cek.check()).status is HealthStatus.DEGRADED

        # Sapuan berikutnya: tidak ada penolakan BARU. Totalnya tetap 11.
        assert (await cek.check()).status is HealthStatus.UP

    @pytest.mark.asyncio
    async def test_penolakan_baru_tetap_terdeteksi(self) -> None:
        """Yang tidak boleh ikut hilang: kemampuan menyalakan lampu merah."""
        cek, gate = _check()
        gate.totals = {"OK": 20}
        await cek.check()

        gate.totals = {"OK": 20, "ABNORMAL_PRICE": 15}
        assert (await cek.check()).status is HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_sebagian_kecil_ditolak_bukan_degraded(self) -> None:
        """Satu dari sebelas adalah fakta tentang pasar yang buka-tutup.
        Membuatnya DEGRADED menyalakan lampu merah tiap pagi."""
        cek, gate = _check()
        gate.totals = {"OK": 10, "ABNORMAL_PRICE": 1}

        assert (await cek.check()).status is HealthStatus.UP

    @pytest.mark.asyncio
    async def test_yang_diredam_tetap_disebutkan(self) -> None:
        """Diredam vonisnya, bukan disembunyikan angkanya."""
        cek, gate = _check()
        gate.totals = {"OK": 10, "ABNORMAL_PRICE": 1}

        assert "1 dari 11" in (await cek.check()).message

    @pytest.mark.asyncio
    async def test_seluruhnya_ditolak_itu_degraded(self) -> None:
        cek, gate = _check()
        gate.totals = {"ABNORMAL_PRICE": 11}

        health = await cek.check()
        assert health.status is HealthStatus.DEGRADED
        assert "100%" in health.message

    @pytest.mark.asyncio
    async def test_penghitung_yang_direset_tidak_jadi_angka_negatif(self) -> None:
        """Gerbangnya bisa diganti di bawah sana; selisih negatif adalah
        keadaan yang sah, bukan porsi negatif."""
        cek, gate = _check()
        gate.totals = {"OK": 50, "ABNORMAL_PRICE": 5}
        await cek.check()

        # Gerbangnya diganti: OK terjun dari 50 ke 2, dan SATU penolakan baru
        # muncul. Tanpa menyaring selisih negatif, penyebutnya menjadi -47 dan
        # porsinya negatif - angka yang membuat penolakan nyata terbaca sebagai
        # sehat. Yang benar: satu penolakan dari satu observasi yang naik.
        gate.totals = {"OK": 2, "ABNORMAL_PRICE": 6}
        health = await cek.check()

        assert health.details["observed_since_last_check"] >= 0
        assert health.status is HealthStatus.DEGRADED, health.message

    @pytest.mark.asyncio
    async def test_totalnya_tetap_dilaporkan(self) -> None:
        """'Berapa sejak nyala' adalah angka yang sah - ia hanya bukan jawaban
        atas 'apakah sekarang sehat'."""
        cek, gate = _check()
        gate.totals = {"ABNORMAL_PRICE": 11}
        health = await cek.check()

        assert health.details["quality_rejections"] == {"ABNORMAL_PRICE": 11}
        assert health.details["rejected_since_last_check"] == 11

    def test_ambangnya_masuk_akal(self) -> None:
        assert 0.2 <= DEGRADED_REJECTION_SHARE <= 0.9
