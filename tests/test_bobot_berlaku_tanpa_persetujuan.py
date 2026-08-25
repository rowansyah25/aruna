"""Bobot agen berlaku sendiri, dan operator dikabari setiap kali ia berubah.

**PASAL 11.11 dan 11.16 ditimpa keputusan operator pada 2026-08-25**, dinyatakan
langsung: ARUNA harus belajar dari kesalahannya dan memperbaiki dirinya sendiri
tanpa persetujuan per perubahan.

Sebelumnya `MeasuredHistory.reliability` memulangkan bobot dari
`approved_weights`. Tabel yang mengisinya tidak pernah ada, jadi jawabannya
SELALU None - dan terukur di `judge_decisions`, `historical_reliability`
tercatat tidak tersedia pada 100% keputusan di setiap hari yang tersimpan.
Seluruh mesin keandalan berjalan, mengukur, menulis snapshot, dan tidak pernah
menyentuh satu pun putusan.

Yang menggantikan persetujuan ada dua, dan keduanya dijaga di sini:

* PAGAR - sampel minimum, batas atas dan bawah pengali, titik netral yang
  diukur. Tanpa itu "tanpa persetujuan" berarti tanpa rem.
* KABAR - operator tidak lagi memeriksa di depan, jadi ia harus bisa memeriksa
  di belakang. Perubahan yang tidak pernah dikatakan sama saja dengan sistem
  yang menyetel dirinya diam-diam.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.core.enums import AgentRole
from aruna.learning.calibration import calibrate
from aruna.learning.history import MeasuredHistory
from aruna.learning.reliability import (
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    MIN_RELIABILITY_SAMPLE,
    AgentRecord,
    ReliabilityReport,
    build_reliability,
)

SAAT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _sejarah(*records: AgentRecord) -> MeasuredHistory:
    return MeasuredHistory(
        reliability_report=ReliabilityReport(records=tuple(records)),
        calibration_report=calibrate([]),
    )


def _rekam(role: AgentRole, *, scored: int, correct: int) -> AgentRecord:
    return AgentRecord(role=role, scored=scored, correct=correct)


class TestGerbangPersetujuanTercabut:
    def test_pengali_berlaku_tanpa_ada_yang_menyetujui(self) -> None:
        """Inti perubahannya. `approved_weights` sengaja dibiarkan kosong -
        itulah keadaan produksi, dan dulu ia membuat jawabannya selalu None."""
        sejarah = _sejarah(
            _rekam(AgentRole.VOLUME, scored=400, correct=220)
        )

        pengali = sejarah.reliability(AgentRole.VOLUME)

        assert pengali is not None, (
            "bobot masih digerbangi persetujuan - keputusan operator "
            "2026-08-25 tidak berlaku"
        )
        assert pengali > 1.0

    def test_agen_buruk_bobotnya_turun_sendiri(self) -> None:
        """Pasangannya, supaya test di atas tidak bisa lulus dengan selalu
        memulangkan angka di atas satu."""
        sejarah = _sejarah(
            _rekam(AgentRole.MOMENTUM, scored=400, correct=160)
        )

        assert (sejarah.reliability(AgentRole.MOMENTUM) or 1.0) < 1.0


class TestPagarnyaMasihBerdiri:
    def test_sampel_tipis_tidak_menggerakkan_apa_pun(self) -> None:
        """Bukan bergerak sedikit - NOL. Satu pekan sial tidak boleh
        membungkam sebuah agen selamanya, karena agen yang dibungkam berhenti
        mengumpulkan catatan yang bisa membersihkan namanya."""
        sejarah = _sejarah(
            _rekam(AgentRole.VOLUME, scored=MIN_RELIABILITY_SAMPLE - 1, correct=0)
        )

        assert sejarah.reliability(AgentRole.VOLUME) is None

    @pytest.mark.parametrize(
        "correct", [0, 400], ids=["selalu-salah", "selalu-benar"]
    )
    def test_pengali_tetap_di_dalam_batas(self, correct: int) -> None:
        sejarah = _sejarah(
            _rekam(AgentRole.REVERSAL, scored=400, correct=correct)
        )
        pengali = sejarah.reliability(AgentRole.REVERSAL)

        assert pengali is not None
        assert float(MIN_MULTIPLIER) <= pengali <= float(MAX_MULTIPLIER)

    def test_titik_netral_diukur_bukan_setengah(self) -> None:
        """Di pasar yang naik 60% waktu, agen yang selalu bilang BUY benar 60%
        tanpa keahlian apa pun. Titik netral 0,5 akan memberinya bobot tambahan
        untuk mengikuti arus."""
        baris = [
            {
                "agent": "TECHNICAL", "agent_decision": "BUY",
                "council_decision": "BUY", "direction_correct": i < 60,
            }
            for i in range(100)
        ]
        laporan = build_reliability(baris)
        rec = next(r for r in laporan.records if r.role is AgentRole.TECHNICAL)

        assert rec.netral != 0.5
        assert rec.edge is not None
        assert abs(rec.edge) < 0.05, (
            "agen yang cuma mengikuti arus mendapat edge besar - titik "
            "netralnya tidak diukur"
        )


class _Pengirim:
    def __init__(self, *, siap: bool = True) -> None:
        self._siap = siap
        self.dikirim: list[str] = []

    def ready(self) -> bool:
        return self._siap

    async def send(self, text: str) -> bool:
        self.dikirim.append(text)
        return True


class _State:
    def __init__(self) -> None:
        self.isi: dict = {}

    async def get(self, key):
        return self.isi.get(key)

    async def set(self, key, value, *, actor):
        self.isi[key] = value


def _loop(pengirim, state):
    from aruna.core.config import UpkeepSettings
    from aruna.upkeep.loop import UpkeepLoop

    return UpkeepLoop(
        refresher=None,
        resolver=None,
        settings=UpkeepSettings(_env_file=None),
        review_state=state,
        pemberitahu=pengirim,
    )


class TestOperatorDikabari:
    @pytest.mark.asyncio
    async def test_perubahan_bobot_dikabarkan(self) -> None:
        pengirim, state = _Pengirim(), _State()
        sejarah = _sejarah(_rekam(AgentRole.VOLUME, scored=400, correct=220))

        await _loop(pengirim, state)._catat_perubahan_bobot(sejarah, SAAT)

        assert pengirim.dikirim, (
            "bobot berubah sendiri dan operator tidak diberi tahu - itu "
            "menyetel diri diam-diam, bukan yang diminta"
        )
        pesan = pengirim.dikirim[0]
        assert "VOLUME" in pesan
        assert "MENGANALISIS SAJA" in pesan

    @pytest.mark.asyncio
    async def test_tidak_dikabarkan_dua_kali_untuk_bobot_yang_sama(self) -> None:
        """Pesan harian yang berbunyi sama berhenti dibaca, dan yang tenggelam
        bersamanya justru perubahan yang sesungguhnya."""
        pengirim, state = _Pengirim(), _State()
        sejarah = _sejarah(_rekam(AgentRole.VOLUME, scored=400, correct=220))
        loop = _loop(pengirim, state)

        for _ in range(3):
            await loop._catat_perubahan_bobot(sejarah, SAAT)

        assert len(pengirim.dikirim) == 1

    @pytest.mark.asyncio
    async def test_bobot_yang_benar_benar_berubah_dikabarkan_lagi(self) -> None:
        """Pasangannya, supaya test di atas tidak bisa lulus dengan membungkam
        semuanya sesudah kabar pertama."""
        pengirim, state = _Pengirim(), _State()
        loop = _loop(pengirim, state)

        await loop._catat_perubahan_bobot(
            _sejarah(_rekam(AgentRole.VOLUME, scored=400, correct=220)), SAAT
        )
        await loop._catat_perubahan_bobot(
            _sejarah(_rekam(AgentRole.VOLUME, scored=400, correct=140)), SAAT
        )

        assert len(pengirim.dikirim) == 2

    @pytest.mark.asyncio
    async def test_sampel_tipis_tidak_menghasilkan_kabar(self) -> None:
        """Tidak ada yang berlaku, jadi tidak ada yang perlu dikabarkan."""
        pengirim, state = _Pengirim(), _State()
        sejarah = _sejarah(
            _rekam(AgentRole.VOLUME, scored=MIN_RELIABILITY_SAMPLE - 1, correct=5)
        )

        await _loop(pengirim, state)._catat_perubahan_bobot(sejarah, SAAT)

        assert pengirim.dikirim == []

    @pytest.mark.asyncio
    async def test_tanpa_tujuan_pesannya_tidak_dibangun(self) -> None:
        """Cacat yang sama dengan laporan harian yang dulu memakan 45% waktu
        siklus: membangun pesan lalu membuangnya, tiap kali."""
        pengirim, state = _Pengirim(siap=False), _State()
        sejarah = _sejarah(_rekam(AgentRole.VOLUME, scored=400, correct=220))

        await _loop(pengirim, state)._catat_perubahan_bobot(sejarah, SAAT)

        assert pengirim.dikirim == []


class TestTersambungKeJalurHidup:
    def test_app_mengoper_pemberitahu(self) -> None:
        """Kabar yang tidak pernah dirangkai bukan kabar. Cacat ini sudah
        berulang di proyek ini: kode benar, ada di tempatnya, tidak pernah
        berjalan."""
        import inspect

        from aruna import app as app_module

        sumber = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "pemberitahu=" in sumber

    def test_review_memanggil_pencatat_bobot(self) -> None:
        import inspect

        from aruna.upkeep.loop import UpkeepLoop

        sumber = inspect.getsource(UpkeepLoop._review_pembelajaran)
        assert "_catat_perubahan_bobot" in sumber
