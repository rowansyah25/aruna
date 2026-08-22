"""Keputusan yang sudah diambil tidak boleh dibatalkan validasi ulang (SPEC 44).

Terukur 2026-08-21 di basis data produksi:

    proposal_key: exit-at-target
          status: VALIDATED          <- tabel proposal
        decision: APPROVED           <- tabel keputusan
      decided_at: 2026-08-15 11:39:31  oleh rowan
      updated_at: 2026-08-17 15:29:51  <- validasi ulang, DUA HARI kemudian

Dua tabel tidak sepakat tentang proposal yang sama. `governance/service.py`
menetapkan ``status=VALIDATED`` **tanpa syarat** saat validasi ulang, dan
``record_proposal`` menimpanya lewat ``ON DUPLICATE KEY UPDATE`` - jadi
persetujuan yang sudah diambil manusia dibatalkan diam-diam oleh rutinitas.

**Tiga akibatnya**, dan yang ketiga paling berbahaya:

* catatan governance berbohong tentang apa yang aktif;
* `ready_for_approval` menolak yang "already approved" - dan penjaga itu
  dikalahkan, karena statusnya sudah bukan APPROVED lagi;
* **perubahan yang sama bisa disetujui dua kali**, masing-masing tercatat
  sebagai keputusan manusia yang terpisah.

Kode ini sudah menyatakan sikapnya di `approval.reject()`: *"Reversing an
active change is a new proposal, so that the reversal is recorded and reviewed
like any other change rather than quietly undone."* Jalur validasi melanggarnya.
"""

from __future__ import annotations

import pytest

from aruna.governance.proposal import (
    Arm,
    ModelProposal,
    ProposalStatus,
    ready_for_approval,
)


class _Store:
    """Penyimpan yang mencatat apa yang benar-benar ditulis."""

    def __init__(self) -> None:
        self.ditulis: list[ModelProposal] = []

    async def variants_tested(self) -> int:
        return 1

    async def record_proposal(self, proposal: ModelProposal) -> None:
        self.ditulis.append(proposal)


def _proposal(status: ProposalStatus = ProposalStatus.DRAFT) -> ModelProposal:
    return ModelProposal(
        key="exit-at-target",
        title="Exit when the target is touched",
        hypothesis="uji",
        change="uji",
        status=status,
    )


def _arm(label: str, *, benar: int, n: int) -> Arm:
    return Arm(label=label, resolved=n, correct=benar)


async def _validasi(service, proposal):
    return await service.validate_proposal(
        proposal,
        _arm("dasar", benar=300, n=600),
        _arm("varian", benar=305, n=600),
    )


def _service(store):
    from aruna.governance.service import GovernanceService

    return GovernanceService(store=store)


class TestStatusTerminalBertahan:
    @pytest.mark.asyncio
    async def test_yang_disetujui_tetap_disetujui(self) -> None:
        """Inti bugnya: `exit-at-target` disetujui 15 Agustus, divalidasi ulang
        17 Agustus, dan statusnya mundur ke VALIDATED."""
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.APPROVED))

        assert store.ditulis[-1].status is ProposalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_yang_ditolak_tetap_ditolak(self) -> None:
        """Sisi lain yang sama berbahayanya: proposal yang sudah ditolak
        dihidupkan lagi oleh validasi rutin, dan bisa disetujui sesudahnya."""
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.REJECTED))

        assert store.ditulis[-1].status is ProposalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_validasinya_tetap_disimpan(self) -> None:
        """Bukti baru tidak dibuang - yang dipertahankan hanya statusnya.

        Validasi ulang atas `exit-at-target` justru menemukan hal yang paling
        penting tentangnya (`NO_IMPROVEMENT`, PnL lebih buruk 463.540), dan
        membuangnya demi menjaga status akan menghapus temuan itu.
        """
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.APPROVED))

        assert store.ditulis[-1].validation is not None


class TestYangBelumDiputuskanTetapMaju:
    @pytest.mark.asyncio
    async def test_draft_menjadi_validated(self) -> None:
        """Perbaikannya tidak boleh membekukan alur yang normal."""
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.DRAFT))

        assert store.ditulis[-1].status is ProposalStatus.VALIDATED

    @pytest.mark.asyncio
    async def test_shadowed_menjadi_validated(self) -> None:
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.SHADOWED))

        assert store.ditulis[-1].status is ProposalStatus.VALIDATED


class TestPenjagaPersetujuanTidakBisaDikalahkan:
    @pytest.mark.asyncio
    async def test_yang_disetujui_tetap_ditolak_untuk_disetujui_lagi(self) -> None:
        """Akibat yang paling berbahaya, diuji langsung.

        `ready_for_approval` menolak yang "already approved". Kalau validasi
        ulang memundurkan statusnya, penjaga itu kalah - dan perubahan yang sama
        bisa disetujui dua kali sebagai dua keputusan manusia yang terpisah.
        """
        store = _Store()
        await _validasi(_service(store), _proposal(ProposalStatus.APPROVED))

        boleh, alasan = ready_for_approval(store.ditulis[-1])

        assert not boleh
        assert "already approved" in alasan

    @pytest.mark.asyncio
    async def test_tanpa_persist_tidak_menulis_apa_pun(self) -> None:
        """`persist=False` dipakai untuk melihat-lihat; ia tidak boleh
        menyentuh status apa pun."""
        store = _Store()
        service = _service(store)
        await service.validate_proposal(
            _proposal(ProposalStatus.APPROVED),
            _arm("dasar", benar=300, n=600),
            _arm("varian", benar=305, n=600),
            persist=False,
        )

        assert store.ditulis == []


class TestBentukPerbaikannya:
    def test_status_terminal_dieja_di_satu_tempat(self) -> None:
        """Daftar yang tersebar adalah daftar yang suatu saat tidak sepakat."""
        from aruna.governance.proposal import STATUS_TERMINAL

        assert set(STATUS_TERMINAL) == {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
        }

    def test_ready_for_approval_memakai_daftar_yang_sama(self) -> None:
        """Kalau ia mengeja daftarnya sendiri, dua daftar itu bisa menyimpang -
        dan yang menyimpang di sini membuka kembali persetujuan ganda."""
        import ast
        import inspect
        from textwrap import dedent

        pohon = ast.parse(dedent(inspect.getsource(ready_for_approval)))
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}

        assert "STATUS_TERMINAL" in nama

    def test_sudah_diputuskan_dipakai_di_service(self) -> None:
        import ast
        import inspect
        from textwrap import dedent

        from aruna.governance.service import GovernanceService

        pohon = ast.parse(
            dedent(inspect.getsource(GovernanceService.validate_proposal))
        )
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
        nama |= {n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)}

        assert "STATUS_TERMINAL" in nama or "sudah_diputuskan" in nama
