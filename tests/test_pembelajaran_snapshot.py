"""Phase 12 dan Phase 13 yang tidak pernah sampai ke keputusan (14.40, 14.41).

Terukur di produksi 2026-08-20: Phase 12 hanya **22%** sampai ke keputusan.
Pattern discovery, spesialisasi agent, champion, challenger, drift - semuanya
sudah dibangun berbulan-bulan, tersimpan di database, dan tidak satu pun
dibaca oleh lapisan yang memutuskan. Korelasi sama: mesinnya ada sejak Phase 4,
tabelnya terisi, dan ``DecisionContext.correlation`` **tidak pernah diisi di
mana pun** - termasuk jalur spot.

**Dibaca sekali per jendela, bukan sekali per simbol.** Dua puluh simbol tiap
lima belas menit dikali enam pertanyaan adalah 120 kueri per tick untuk data
yang berubah dalam hitungan jam. Polanya menyalin :class:`Strategist`, yang
sudah memecahkan masalah yang sama.

**Kegagalannya kosong, bukan meledak.** Sebuah lapisan pembelajaran yang
menjadi syarat agar council bisa memutuskan akan mengubah kegagalan
pembelajaran menjadi kegagalan analisis.
"""

from __future__ import annotations

from typing import Any

import pytest

from aruna.core.enums import Market
from aruna.learning.snapshot import PembacaPembelajaran, Pembelajaran


class _Learning12:
    """Bentuknya meniru ``learning12.LearningRepository`` yang sungguhan.

    ``notable_patterns`` di sana menuntut ``model_version`` sebagai kata kunci
    WAJIB. Palsu yang menerima ``**kw`` apa adanya menelan kelalaian itu, dan
    versi pertama kode ini lolos seluruh test lalu gagal dua puluh kali pada
    tick pertama di produksi.
    """

    def __init__(self, patterns=None, votes=None) -> None:
        self._patterns = patterns or []
        self._votes = votes or []
        self.panggilan = 0
        self.versi_diminta: str | None = None

    async def notable_patterns(
        self, *, model_version: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        self.panggilan += 1
        self.versi_diminta = model_version
        return self._patterns

    async def agent_votes(self, **kw) -> list[dict[str, Any]]:
        return self._votes


class _Governance:
    def __init__(self, drift=None, proposals=None) -> None:
        self._drift = drift
        self._proposals = proposals or []

    async def latest_drift(self) -> dict[str, Any] | None:
        return self._drift

    async def proposals(self, **kw) -> list[dict[str, Any]]:
        return self._proposals


class _Correlation:
    """``CorrelationRepository.latest`` yang sungguhan memanggil
    ``market.value`` - ia menuntut enum :class:`Market`, bukan teks. Palsu yang
    menerima apa pun menyembunyikan itu."""

    def __init__(self, rows=None) -> None:
        self._rows = rows or []
        self.pasar_diminta: Any = None

    async def latest(self, market, interval, **kw) -> list[dict[str, Any]]:
        self.pasar_diminta = market
        _ = market.value  # meniru pembacaan yang sungguhan
        return self._rows


def _pembaca(**kw) -> PembacaPembelajaran:
    dasar = {
        "learning12": _Learning12(),
        "governance": _Governance(),
        "correlation": _Correlation(),
        "model_version": "futures-f5",
    }
    return PembacaPembelajaran(**(dasar | kw))


class TestApaYangDibaca:
    @pytest.mark.asyncio
    async def test_pola_terbaca(self) -> None:
        p = await _pembaca(
            learning12=_Learning12(patterns=[{"pattern_key": "P-1"}])
        ).baca(market=Market.CRYPTO, interval="4h")

        assert p.patterns == ("P-1",)

    @pytest.mark.asyncio
    async def test_spesialisasi_disusun_dari_suara_agent(self) -> None:
        """PASAL 14.40 minta spesialisasi agent, bukan daftar suara mentah.
        Yang menyusunnya sudah ada di ``learning.specialization``; yang hilang
        cuma pemanggilnya."""
        # Bentuknya persis keluaran ``learning12.agent_votes``: kolom
        # `agreed_with_council`, `abstained`, `regime`, `result`. Palsu yang
        # bentuknya salah adalah cara paling andal membuat test hijau di atas
        # kode yang rusak, dan itu sudah terjadi tiga kali di sesi ini.
        #
        # Datanya harus melewati ambang `AgentProfile.specialty` yang sungguhan:
        # dua rezim yang keduanya bersample cukup, dengan jarak yang selangnya
        # tidak bertindihan. Ambang itu tinggi, dan memang seharusnya - tanpa
        # ia, "spesialis reversal" hanyalah rezim yang kebetulan menang lebih
        # sering.
        def _suara(regime: str, benar: bool) -> dict[str, Any]:
            return {
                "role": "TECHNICAL", "regime": regime,
                "agreed_with_council": 1, "abstained": 0,
                "result": "WIN" if benar else "LOSS",
            }

        suara = [_suara("TRENDING", True) for _ in range(38)]
        suara += [_suara("TRENDING", False) for _ in range(2)]
        suara += [_suara("SIDEWAYS", True) for _ in range(8)]
        suara += [_suara("SIDEWAYS", False) for _ in range(32)]

        p = await _pembaca(learning12=_Learning12(votes=suara)).baca(
            market="CRYPTO", interval="4h"
        )

        assert p.specialists == {"TECHNICAL": "TRENDING"}

    @pytest.mark.asyncio
    async def test_spesialisasi_yang_belum_terbukti_tetap_kosong(self) -> None:
        """Peta kosong berarti "belum terbukti", bukan "semua agent sama saja".
        Satu rezim saja tidak bisa membuktikan spesialisasi apa pun - tidak ada
        pembandingnya."""
        suara = [
            {"role": "TECHNICAL", "regime": "TRENDING",
             "agreed_with_council": 1, "abstained": 0, "result": "WIN"}
            for _ in range(40)
        ]
        p = await _pembaca(learning12=_Learning12(votes=suara)).baca(
            market="CRYPTO", interval="4h"
        )

        assert p.specialists == {}

    @pytest.mark.asyncio
    async def test_champion_adalah_versi_yang_sedang_jalan(self) -> None:
        """SPEC 37 punya enum ``ModelRole.CHAMPION`` dan tidak satu pun baris
        yang menyimpannya. Yang benar-benar berlaku adalah versi model yang
        sedang memutuskan - dan itu sudah ada, tinggal disebut."""
        p = await _pembaca(model_version="futures-f5").baca(
            market="CRYPTO", interval="4h"
        )

        assert p.champion == "futures-f5"

    @pytest.mark.asyncio
    async def test_challenger_adalah_usulan_yang_belum_diputuskan(self) -> None:
        """Statusnya diambil dari :class:`ProposalStatus`, bukan dikarang.

        Versi pertama memakai ``{"PENDING", "APPROVED"}`` - dua nilai yang aku
        tebak. Yang benar-benar ada di database: ``DRAFT`` dan ``VALIDATED``.
        Akibatnya challenger selalu kosong padahal ada tiga usulan tersimpan,
        dan tidak ada satu pun test yang bisa menangkapnya karena palsunya
        memakai tebakan yang sama.
        """
        from aruna.governance.proposal import ProposalStatus

        p = await _pembaca(
            governance=_Governance(
                proposals=[
                    {"proposal_key": "MP-7",
                     "status": ProposalStatus.VALIDATED.value},
                    {"proposal_key": "MP-6",
                     "status": ProposalStatus.REJECTED.value},
                ]
            )
        ).baca(market=Market.CRYPTO, interval="4h")

        assert p.challenger == "MP-7"

    @pytest.mark.asyncio
    async def test_usulan_yang_sudah_selesai_bukan_penantang(self) -> None:
        """``REJECTED`` sudah kalah dan ``APPROVED`` sudah menang - keduanya
        bukan penantang, dan melaporkannya begitu akan membuat keputusan
        mengira ada model yang sedang diuji padahal tidak ada."""
        from aruna.governance.proposal import ProposalStatus

        for status in (ProposalStatus.REJECTED, ProposalStatus.ABANDONED):
            p = await _pembaca(
                governance=_Governance(
                    proposals=[{"proposal_key": "MP-9", "status": status.value}]
                )
            ).baca(market=Market.CRYPTO, interval="4h")

            assert p.challenger == "", status

    @pytest.mark.asyncio
    async def test_daftar_menantang_memakai_nilai_yang_ada(self) -> None:
        """Penjaga terhadap tebakan. Setiap anggota ``MENANTANG`` harus benar-
        benar ada di ``ProposalStatus`` - sebuah status yang tidak pernah ada
        di database tidak akan pernah cocok, dan diamnya terbaca seperti
        "tidak ada penantang"."""
        from aruna.governance.proposal import ProposalStatus
        from aruna.learning.snapshot import MENANTANG

        sah = {s.value for s in ProposalStatus}

        assert MENANTANG
        assert sah >= MENANTANG, MENANTANG - sah

    @pytest.mark.asyncio
    async def test_versi_pola_dari_mesin_pembelajaran(self) -> None:
        """Pola tersimpan di bawah ``learn-12.0`` - versi mesin pembelajaran -
        bukan di bawah versi aplikasi.

        Terukur: 365 baris di ``discovered_patterns``, dan pembacanya
        memulangkan kosong karena mencari dengan kunci yang salah.
        """
        from aruna.learning.adaptive import LEARNING_VERSION

        repo = _Learning12(patterns=[{"pattern_key": "P-1"}])
        await _pembaca(learning12=repo, model_version="v9+phase14").baca(
            market=Market.CRYPTO, interval="4h"
        )

        assert repo.versi_diminta == LEARNING_VERSION

    @pytest.mark.asyncio
    async def test_tanpa_usulan_challengernya_kosong(self) -> None:
        """Kosong berarti tidak ada penantang - bukan penantang bernama
        "UNKNOWN" yang terbaca seperti model misterius."""
        p = await _pembaca().baca(market=Market.CRYPTO, interval="4h")

        assert p.challenger == ""

    @pytest.mark.asyncio
    async def test_drift_terbaca(self) -> None:
        p = await _pembaca(
            governance=_Governance(drift={"performance_drift": 0.12})
        ).baca(market=Market.CRYPTO, interval="4h")

        assert p.drift == {"performance_drift": 0.12}

    @pytest.mark.asyncio
    async def test_korelasi_terbaca(self) -> None:
        """Mesinnya ada sejak Phase 4 dan tabelnya terisi. Yang tidak pernah
        ada adalah pembacanya."""
        p = await _pembaca(
            correlation=_Correlation(
                rows=[{"left_symbol": "BTC/USDT", "right_symbol": "ETH/USDT",
                       "coefficient": 0.91, "strength": "STRONG"}]
            )
        ).baca(market=Market.CRYPTO, interval="4h")

        assert len(p.correlation) == 1


class TestPembacaanDriftDiRepositori:
    """``record_drift`` sudah ada sejak Phase 10 dan **tidak punya pembaca**.

    Sebuah tabel yang hanya ditulis adalah tabel yang tidak pernah menjawab
    pertanyaan apa pun - dan drift adalah pertanyaan yang paling perlu dijawab
    sebelum keputusan: apakah model yang memutuskan ini masih model yang diuji.
    """

    class _Db:
        def __init__(self, rows=None) -> None:
            self.rows = rows or []
            self.sql = ""

        async def fetch(self, sql: str, *args):
            self.sql = sql
            return self.rows

    @pytest.mark.asyncio
    async def test_yang_terbaru_yang_diambil(self) -> None:
        from aruna.db.repositories.governance import GovernanceRepository

        db = self._Db([{"verdict": "STABLE", "performance_drift": 0.02}])
        hasil = await GovernanceRepository(db).latest_drift()

        assert hasil["verdict"] == "STABLE"
        assert "ORDER BY" in db.sql
        assert "LIMIT 1" in db.sql

    def test_kolomnya_benar_benar_ada_di_tabelnya(self) -> None:
        """Penjaga untuk dua test di atasnya.

        Keduanya memakai db palsu yang tidak memvalidasi SQL - jadi keduanya
        hijau atas kueri yang akan meledak di MySQL. Versi pertama kueri ini
        memakai ``created_at``; kolomnya bernama ``checked_at``, dan tidak ada
        satu pun test yang bisa menangkapnya.
        """
        import inspect
        import pathlib
        import re

        from aruna.db.repositories.governance import GovernanceRepository

        sumber = inspect.getsource(GovernanceRepository.latest_drift)
        sql = re.search(r"SELECT(.*?)FROM drift_checks", sumber, re.S)

        assert sql, "kuerinya tidak ketemu"
        kolom = {k.strip() for k in sql.group(1).replace("\n", " ").split(",")}

        migrasi = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0014_governance.sql"
        ).read_text(encoding="utf-8")
        blok = migrasi[migrasi.index("CREATE TABLE drift_checks"):]
        blok = blok[: blok.index(") ENGINE")]

        for nama in kolom:
            assert re.search(rf"^\s*{re.escape(nama)}\s", blok, re.M), nama

    @pytest.mark.asyncio
    async def test_belum_pernah_ada_menghasilkan_none(self) -> None:
        """``None`` berarti belum pernah diperiksa. Sebuah dict kosong akan
        terbaca seperti pemeriksaan yang hasilnya nol drift - dua hal yang
        sangat berbeda."""
        from aruna.db.repositories.governance import GovernanceRepository

        assert await GovernanceRepository(self._Db([])).latest_drift() is None


class TestDibacaSekaliPerJendela:
    @pytest.mark.asyncio
    async def test_dua_puluh_simbol_satu_kueri(self) -> None:
        """Dua puluh simbol tiap lima belas menit dikali enam pertanyaan adalah
        120 kueri per tick untuk data yang berubah dalam hitungan jam."""
        repo = _Learning12(patterns=[{"pattern_key": "P-1"}])
        pembaca = _pembaca(learning12=repo)

        for _ in range(20):
            await pembaca.baca(market=Market.CRYPTO, interval="4h")

        assert repo.panggilan == 1

    @pytest.mark.asyncio
    async def test_jendela_habis_dibaca_ulang(self) -> None:
        """Cache tanpa masa berlaku adalah data yang membeku pada saat proses
        menyala - dan sebuah strategi yang statusnya baru diubah operator tidak
        akan pernah terlihat."""
        repo = _Learning12(patterns=[{"pattern_key": "P-1"}])
        pembaca = _pembaca(learning12=repo)
        pembaca.ttl_sec = 0.0

        await pembaca.baca(market=Market.CRYPTO, interval="4h")
        await pembaca.baca(market=Market.CRYPTO, interval="4h")

        assert repo.panggilan == 2

    @pytest.mark.asyncio
    async def test_dua_puluh_simbol_serentak_tetap_satu_kueri(self) -> None:
        """Terukur di produksi 2026-08-20: dua puluh kegagalan identik dalam
        delapan puluh milidetik.

        Loop futures menjalankan simbolnya **serentak**, jadi kedua puluh
        pemanggil sampai di cache sebelum ada satu pun yang selesai mengisinya.
        Cache yang hanya menyimpan hasil tidak menahan serbuan itu; yang
        menahannya adalah menyimpan **pembacaan yang sedang berjalan**, supaya
        yang datang berikutnya menunggu yang pertama alih-alih memulai yang
        kedua.
        """
        import asyncio

        class _Lambat:
            def __init__(self) -> None:
                self.panggilan = 0

            async def notable_patterns(self, **kw):
                self.panggilan += 1
                await asyncio.sleep(0.02)
                return [{"pattern_key": "P-1"}]

            async def agent_votes(self, **kw):
                return []

        repo = _Lambat()
        pembaca = _pembaca(learning12=repo)

        await asyncio.gather(*[
            pembaca.baca(market=Market.CRYPTO, interval="4h") for _ in range(20)
        ])

        assert repo.panggilan == 1

    @pytest.mark.asyncio
    async def test_pasar_lain_dibaca_sendiri(self) -> None:
        """Korelasi CRYPTO bukan korelasi IDX. Satu cache untuk keduanya akan
        memberi jawaban pasar yang salah kepada yang datang belakangan."""
        repo = _Learning12(patterns=[{"pattern_key": "P-1"}])
        pembaca = _pembaca(learning12=repo)

        await pembaca.baca(market=Market.CRYPTO, interval="4h")
        await pembaca.baca(market=Market.IDX, interval="4h")

        assert repo.panggilan == 2


class TestSampaiKeKeputusan:
    """Penjaga penyambungan.

    Sebuah snapshot yang dibaca sempurna dan tidak pernah sampai ke lapisan
    yang memutuskan adalah kueri yang mahal tanpa satu pun gunanya - dan itu
    persis keadaan Phase 12 sebelum ini.
    """

    def _amati(self, monkeypatch, note) -> dict:
        from types import SimpleNamespace

        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )
        modul.observe_decision(
            context=None, verdict=None, plan=None, note=note, symbol="BTCUSDT"
        )
        return keluar[0][1]

    def test_service_mengimpor_snapshot(self) -> None:
        import inspect

        from aruna.futures import service

        assert (
            "from aruna.learning.snapshot import" in inspect.getsource(service)
        )

    def test_lima_lapisan_terbaca_lewat_snapshot(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from aruna.decision.integration import Masukan

        note = SimpleNamespace(
            pembelajaran=Pembelajaran(
                patterns=("P-1",),
                specialists={"TECHNICAL": "TRENDING"},
                champion="futures-f5",
                challenger="MP-7",
                drift={"verdict": "STABLE"},
                correlation=({"left_symbol": "BTC/USDT"},),
            )
        )
        hilang = self._amati(monkeypatch, note)["integrasi_hilang"]

        for m in (
            Masukan.PATTERN_DISCOVERY, Masukan.AGENT_SPECIALIZATION,
            Masukan.CHAMPION, Masukan.CHALLENGER, Masukan.DRIFT_DETECTION,
            Masukan.CORRELATION_RISK,
        ):
            assert m.value not in hilang, m

    def test_snapshot_kosong_tetap_hilang(self, monkeypatch) -> None:
        """Pasangannya wajib. Tanpa ia, perbaikan di atas bisa berupa "anggap
        selalu ada" - dan pengukurannya berhenti mengukur."""
        from types import SimpleNamespace

        from aruna.decision.integration import Masukan

        note = SimpleNamespace(pembelajaran=Pembelajaran())
        hilang = self._amati(monkeypatch, note)["integrasi_hilang"]

        assert Masukan.PATTERN_DISCOVERY.value in hilang
        assert Masukan.DRIFT_DETECTION.value in hilang

    def test_dipanggil_dari_jalur_hidup(self) -> None:
        import inspect

        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)

        assert "attach_pembelajaran(" in sumber

    def test_app_membangun_pembacanya(self) -> None:
        """Tanpa baris ini, pembacanya selamanya ``None`` di produksi dan
        seluruh test di atas tetap hijau."""
        import inspect

        from aruna.app import ArunaApplication

        sumber = inspect.getsource(ArunaApplication)

        assert "PembacaPembelajaran(" in sumber

    def test_loop_futures_menerimanya(self) -> None:
        """Dibangun di ``app`` dan tidak dioper ke loop yang memakainya akan
        menghasilkan pembaca yang hidup, bercache, dan tidak pernah ditanya.

        Diperiksa pada loop futures - jalur yang benar-benar berjalan tiap lima
        belas menit - bukan pada perintah ``futures-plan`` sekali jalan.
        """
        import inspect

        from aruna import cli

        sumber = inspect.getsource(cli)
        loop = sumber[sumber.index("async def _futures_loop"):]

        assert "pembelajaran=app.pembelajaran" in loop


class TestKegagalannyaKosongBukanMeledak:
    @pytest.mark.asyncio
    async def test_repositori_yang_meledak_menghasilkan_kosong(self) -> None:
        class _Meledak:
            async def notable_patterns(self, **kw):
                raise RuntimeError("database putus")

            async def agent_votes(self, **kw):
                raise RuntimeError("database putus")

        p = await _pembaca(learning12=_Meledak()).baca(
            market="CRYPTO", interval="4h"
        )

        assert isinstance(p, Pembelajaran)
        assert p.patterns == ()

    @pytest.mark.asyncio
    async def test_satu_yang_gagal_tidak_menghapus_yang_lain(self) -> None:
        """Kalau drift tidak terbaca, pola tetap terbaca. Menjatuhkan seluruh
        snapshot karena satu bagian gagal akan membuat Phase 12 hilang dari
        keputusan setiap kali satu tabel bermasalah."""
        class _DriftMeledak:
            async def latest_drift(self):
                raise RuntimeError("database putus")

            async def proposals(self, **kw):
                return []

        p = await _pembaca(
            learning12=_Learning12(patterns=[{"pattern_key": "P-1"}]),
            governance=_DriftMeledak(),
        ).baca(market=Market.CRYPTO, interval="4h")

        assert p.patterns == ("P-1",)
        assert p.drift is None

    @pytest.mark.asyncio
    async def test_tanpa_repositori_sama_sekali(self) -> None:
        """Pemanggil lama yang tidak menyediakannya menghasilkan snapshot
        kosong, bukan kegagalan."""
        p = await PembacaPembelajaran().baca(market=Market.CRYPTO, interval="4h")

        assert p.patterns == ()
        assert p.champion == ""

