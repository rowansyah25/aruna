"""Pertanyaan riset didorong ke operator (PASAL 11.16).

Operator meminta ARUNA mengabari setiap kali ia "meminta pembaruan model".
Permintaan itu bertabrakan dengan aturannya sendiri: PASAL 11.16 melarang
modifikasi model otomatis, dan ``aruna proposals`` mencetak "ARUNA does not
author changes to itself".

Yang dibangun karena itu adalah bentuk yang diizinkan aturan tersebut: ARUNA
membaca kekalahannya, mengangkat **pertanyaan**, lalu berhenti. Yang menulis
proposal dan memutuskannya adalah orang.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aruna.notify.research import (
    MAX_PERTANYAAN,
    MENUNGGU_KEPUTUSAN,
    RESEARCH_SENT_KEY,
    WIB,
    ResearchNotifier,
    render_digest,
)


def _wib(hari: int, jam: int = 9) -> datetime:
    return datetime(2026, 8, hari, jam, 0, tzinfo=WIB)


def _q(key: str, question: str = "kenapa?", evidence=()):
    return SimpleNamespace(key=key, question=question, evidence=tuple(evidence))


def _proposal(key: str, status: str = "AWAITING_APPROVAL"):
    return {"proposal_key": key, "status": status, "title": f"judul {key}"}


class _Sender:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


class _Governance:
    def __init__(self, questions=()) -> None:
        self.questions = list(questions)
        self.dipanggil = 0

    async def research(self):
        self.dipanggil += 1
        return SimpleNamespace(questions=list(self.questions))


class _Store:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)

    async def proposals(self, *, limit: int = 20):
        return self.rows[:limit]


class _State:
    def __init__(self, stored=None) -> None:
        self.stored = stored

    async def get(self, key: str):
        return self.stored

    async def set(self, key: str, value, *, actor: str) -> None:
        self.stored = value


def _notifier(*, questions=(), proposals=(), sender=None, state=None):
    return ResearchNotifier(
        governance=_Governance(questions),
        store=_Store(proposals),
        sender=sender or _Sender(),
        state=state,
    )


class TestBertanyaBukanMengusulkan:
    """PASAL 11.16. Sebuah sistem yang mengusulkan perubahan atas dirinya
    sendiri akan condong mengusulkan yang membuat angkanya terlihat lebih baik,
    dan tidak ada yang bisa membedakan itu dari perbaikan."""

    def test_pesannya_menyebut_larangannya(self) -> None:
        teks = render_digest([_q("a")], [])
        assert "PASAL 11.16" in teks
        assert "tidak mengubah dirinya sendiri" in teks

    def test_bloknya_diberi_judul_pertanyaan_bukan_usulan(self) -> None:
        """Judul blok adalah hal pertama yang dibaca, dan ia yang menentukan
        apakah isinya dibaca sebagai bahan pertimbangan atau sebagai tuntutan.

        Versi pertama test ini memindai seluruh pesan untuk frasa "usulan
        perubahan" - dan gagal pada kalimat penyangkalannya sendiri, yang justru
        memuat frasa itu untuk menyangkalnya. Pemindaian frasa tidak bisa
        membedakan menyatakan dari menyangkal.
        """
        teks = render_digest([_q("a", "apakah stop terlalu rapat?")], [])
        judul = [b for b in teks.splitlines() if b.endswith(":") or "PERTANYAAN" in b]

        assert any("PERTANYAAN BARU" in b for b in judul)
        assert not any(b.startswith("USULAN") for b in judul), judul

    def test_bukti_ikut_dicetak(self) -> None:
        """Tanpa bukti, sebuah pertanyaan hanyalah pendapat."""
        teks = render_digest([_q("a", "kenapa?", ("41 dari 60 kalah",))], [])
        assert "41 dari 60 kalah" in teks


class TestIsiPesan:
    def test_proposal_menunggu_disebut_dengan_cara_memutuskannya(self) -> None:
        teks = render_digest([], [_proposal("P-1")])
        assert "P-1" in teks
        assert "/approve" in teks and "/reject" in teks

    def test_tanpa_proposal_dikatakan_kosong(self) -> None:
        teks = render_digest([_q("a")], [])
        assert "tidak ada proposal yang menunggu" in teks

    def test_tanpa_pertanyaan_juga_dikatakan(self) -> None:
        teks = render_digest([], [_proposal("P-1")])
        assert "PERTANYAAN BARU: tidak ada" in teks

    def test_daftar_panjang_dipotong_dan_sisanya_disebut(self) -> None:
        """Daftar yang lebih panjang dari satu layar tidak dibaca lebih banyak,
        hanya digulir lebih cepat."""
        teks = render_digest([_q(f"q{i}") for i in range(MAX_PERTANYAAN + 3)], [])
        assert "3 pertanyaan lagi" in teks
        assert "/research" in teks


class TestSekaliSehari:
    @pytest.mark.asyncio
    async def test_dikirim_saat_ada_yang_baru(self) -> None:
        sender = _Sender()
        n = _notifier(questions=[_q("a")], sender=sender)

        assert await n.run(_wib(18)) is True
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_tidak_dikirim_dua_kali_di_hari_yang_sama(self) -> None:
        sender = _Sender()
        n = _notifier(questions=[_q("a")], sender=sender)

        await n.run(_wib(18, 9))
        assert await n.run(_wib(18, 20)) is False
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_hari_berikutnya_dikirim_lagi(self) -> None:
        sender = _Sender()
        n = _notifier(questions=[_q("a"), _q("b")], sender=sender)

        await n.run(_wib(18))
        n.governance.questions.append(_q("c"))
        assert await n.run(_wib(19)) is True

    @pytest.mark.asyncio
    async def test_pertanyaan_lama_tidak_dikabarkan_ulang(self) -> None:
        sender = _Sender()
        n = _notifier(questions=[_q("a")], sender=sender)

        await n.run(_wib(18))
        assert await n.run(_wib(19)) is False
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_restart_tidak_mengirim_ulang_hari_yang_sama(self) -> None:
        """Penjaga proses memang menyalakan ulang ARUNA. Kabar yang datang tiap
        restart berhenti dibaca sebagai kabar."""
        state = _State()
        lama = _notifier(questions=[_q("a")], sender=_Sender(), state=state)
        await lama.run(_wib(18))

        sender = _Sender()
        baru = _notifier(questions=[_q("z")], sender=sender, state=state)
        assert await baru.run(_wib(18, 22)) is False
        assert sender.sent == []


class TestHariSepi:
    @pytest.mark.asyncio
    async def test_tanpa_apa_pun_tidak_mengirim(self) -> None:
        """Tidak ada kabar bukan kabar."""
        sender = _Sender()
        n = _notifier(sender=sender)

        assert await n.run(_wib(18)) is False
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_hari_sepi_tetap_distempel(self) -> None:
        """Tanpa stempel, riset dijalankan ulang tiap siklus sepanjang hari
        itu - dan analisis kekalahan bukan kueri yang murah."""
        n = _notifier()

        await n.run(_wib(18, 9))
        await n.run(_wib(18, 10))
        await n.run(_wib(18, 11))

        assert n.governance.dipanggil == 1

    @pytest.mark.asyncio
    async def test_proposal_menunggu_saja_sudah_cukup(self) -> None:
        """Proposal yang menunggu keputusan akan menunggu selamanya kalau tidak
        ada yang mengingatkan."""
        sender = _Sender()
        n = _notifier(proposals=[_proposal("P-1")], sender=sender)

        assert await n.run(_wib(18)) is True


class TestGagalKirim:
    @pytest.mark.asyncio
    async def test_tidak_distempel_supaya_dicoba_lagi(self) -> None:
        state = _State()
        gagal = _Sender(ok=False)
        n = _notifier(questions=[_q("a")], sender=gagal, state=state)

        assert await n.run(_wib(18)) is False
        assert state.stored is None

    @pytest.mark.asyncio
    async def test_pertanyaannya_belum_dianggap_terkabarkan(self) -> None:
        gagal = _Sender(ok=False)
        n = _notifier(questions=[_q("a")], sender=gagal)

        await n.run(_wib(18))
        assert n._seen == set()


class TestStatusYangMenunggu:
    def test_hanya_yang_menunggu_keputusan_yang_dihitung(self) -> None:
        assert "APPROVED" not in MENUNGGU_KEPUTUSAN
        assert "REJECTED" not in MENUNGGU_KEPUTUSAN
        assert "DRAFT" not in MENUNGGU_KEPUTUSAN

    def test_setiap_namanya_status_yang_benar_benar_ada(self) -> None:
        """Versi pertama daftar ini memuat "SUBMITTED" - status yang tidak
        pernah ada di enum-nya.

        Nama yang tidak ada tidak pernah cocok dengan apa pun, jadi ia tidak
        merusak apa-apa; ia hanya berbohong dengan tenang tentang apa yang
        diperiksa. Daftar teks bebas tidak bisa gagal dengan berisik, dan test
        ini yang membuatnya bisa.
        """
        from aruna.governance.proposal import ProposalStatus

        nyata = {s.value for s in ProposalStatus}
        assert set(MENUNGGU_KEPUTUSAN) <= nyata, set(MENUNGGU_KEPUTUSAN) - nyata

    def test_yang_didaftar_memang_bisa_diputuskan(self) -> None:
        """Mengingatkan operator tentang proposal yang ``/approve``-nya akan
        gagal sama saja dengan mengirimnya ke jalan buntu."""
        from aruna.governance.proposal import ProposalStatus

        for nama in MENUNGGU_KEPUTUSAN:
            status = ProposalStatus(nama)
            assert status not in (
                ProposalStatus.APPROVED,
                ProposalStatus.REJECTED,
                ProposalStatus.ABANDONED,
            ), nama

    @pytest.mark.asyncio
    async def test_proposal_yang_sudah_diputuskan_tidak_mengganggu(self) -> None:
        sender = _Sender()
        n = _notifier(
            proposals=[_proposal("P-1", "APPROVED"), _proposal("P-2", "REJECTED")],
            sender=sender,
        )

        assert await n.run(_wib(18)) is False


class TestTerpasangDiLoopYangJalan:
    """Cacat berulang di repo ini: kode ditulis, diekspor, diuji, tidak pernah
    dicapai jalur yang benar-benar jalan."""

    @pytest.mark.asyncio
    async def test_siklus_upkeep_memanggilnya(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        dipanggil: list[datetime] = []

        class _Riset:
            async def run(self, moment):
                dipanggil.append(moment)
                return True

        saat = datetime(2026, 8, 18, 2, 0, tzinfo=UTC)
        loop = UpkeepLoop(
            refresher=None, resolver=None, locker=None, research=_Riset(),
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=saat),
        )
        await loop.cycle(now=saat)

        assert dipanggil == [saat]
        assert loop.stats.research_digests == 1

    @pytest.mark.asyncio
    async def test_kegagalannya_tidak_menjatuhkan_siklus(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        class _Meledak:
            async def run(self, moment):
                raise RuntimeError("kueri berat gagal")

        saat = datetime(2026, 8, 18, 2, 0, tzinfo=UTC)
        loop = UpkeepLoop(
            refresher=None, resolver=None, locker=None, research=_Meledak(),
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=saat),
        )
        stats = await loop.cycle(now=saat)

        assert stats.research_failures == 1
        assert stats.cycles == 1

    def test_app_merakitnya(self) -> None:
        import inspect

        from aruna import app as app_module

        sumber = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "research=self._build_research()" in sumber

    def test_tanpa_governance_tidak_dirakit(self) -> None:
        """Mengembalikan None supaya loop melewatinya, bukan menabrak atribut
        yang tidak ada."""
        from aruna.app import ArunaApplication

        app = ArunaApplication.__new__(ArunaApplication)
        app.governance = None
        app.governance_store = None

        assert app._build_research() is None

    def test_kuncinya_terpisah_dari_laporan_harian(self) -> None:
        """Dua penanda yang berbagi satu kunci akan saling membungkam."""
        from aruna.notify.daily_service import DAILY_SENT_KEY

        assert RESEARCH_SENT_KEY != DAILY_SENT_KEY
