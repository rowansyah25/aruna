"""PASAL 14.40: WALK_FORWARD dan OUT_OF_SAMPLE, dua masukan terakhir yang diam.

Terukur 2026-08-21: keduanya dilaporkan hilang pada **setiap** keputusan sejak
Phase 14 selesai, dan kelengkapan integrasi berhenti di 90,4% karenanya.

Sebabnya bukan mesinnya. ``BacktestService`` menghitung fold walk-forward,
holdout, dan seluruh peringatannya dengan lengkap; ``BacktestRepository`` punya
``record_backtest``; dan perintah ``aruna backtest`` mencetak hasilnya lalu
**membuangnya**. ``backtest_runs`` berisi nol baris sepanjang umur sistem.

Sesudah perintahnya menyimpan dan backtest sungguhan dijalankan: 11.240
keputusan disimulasikan, walk-forward CONSISTENT di empat fold, holdout
dievaluasi. Berkas ini menjaga bahwa hasil itu benar-benar sampai ke
pengukuran - bukan berhenti di tabel.

**Satu hal yang dijaga terpisah:** angka backtest tidak berubah antar tick, dan
menyuapkannya per keputusan akan membuat pesan membawa angka yang terlihat
relevan padahal tidak. Yang dibaca karena itu adalah **keberadaan validasinya**,
bukan angkanya - dan ada test yang gagal kalau angka itu bocor ke pesan.
"""

from __future__ import annotations

from typing import Any

import pytest

from aruna.decision.integration import Masukan
from aruna.futures.service import _kelengkapan_fase


class _Note:
    def __init__(self, pembelajaran: Any = None) -> None:
        self.pembelajaran = pembelajaran


class _Pembelajaran:
    def __init__(self, backtest: Any = None) -> None:
        self.backtest = backtest


def _hilang(note: Any) -> list[str]:
    return _kelengkapan_fase(
        context=None, verdict=None, plan=None, note=note
    )["integrasi_hilang"]


class TestValidasiTerbaca:
    def test_walk_forward_hadir_kalau_backtestnya_ada(self) -> None:
        note = _Note(_Pembelajaran(backtest={
            "walk_forward": {"verdict": "CONSISTENT", "results": [1, 2, 3, 4]},
            "holdout_included": 1,
        }))

        assert Masukan.WALK_FORWARD.value not in _hilang(note)

    def test_out_of_sample_hadir_kalau_holdout_dievaluasi(self) -> None:
        note = _Note(_Pembelajaran(backtest={
            "walk_forward": {"verdict": "CONSISTENT"},
            "holdout_included": 1,
        }))

        assert Masukan.OUT_OF_SAMPLE.value not in _hilang(note)

    def test_holdout_yang_disisihkan_bukan_out_of_sample(self) -> None:
        """SPEC 38 menyisihkan ekor holdout justru supaya ia TIDAK dilihat saat
        memilih varian. Backtest yang menyisihkannya belum menguji di luar
        sampel - dan menandainya hadir akan mengklaim validasi yang sengaja
        tidak dilakukan."""
        note = _Note(_Pembelajaran(backtest={
            "walk_forward": {"verdict": "CONSISTENT"},
            "holdout_included": 0,
        }))

        hilang = _hilang(note)

        assert Masukan.OUT_OF_SAMPLE.value in hilang
        assert Masukan.WALK_FORWARD.value not in hilang

    def test_tanpa_backtest_keduanya_tetap_hilang(self) -> None:
        """Penjaga terhadap test di atas: keduanya harus benar-benar datang
        dari backtestnya, bukan dari hal lain."""
        hilang = _hilang(_Note(_Pembelajaran()))

        assert Masukan.WALK_FORWARD.value in hilang
        assert Masukan.OUT_OF_SAMPLE.value in hilang

    def test_backtest_kosong_bukan_validasi(self) -> None:
        """Baris yang ada tapi tidak memuat walk-forward apa pun adalah
        backtest yang gagal, bukan backtest yang lulus."""
        note = _Note(_Pembelajaran(backtest={"walk_forward": None}))

        assert Masukan.WALK_FORWARD.value in _hilang(note)


class TestKueriValidasinya:
    """Palsu yang bentuknya salah membuat penyambungan ini lolos sekali.

    Versi pertama memakai ``recent_runs`` - dan **kueri itu tidak memilih
    ``walk_forward`` maupun ``holdout_included`` sama sekali**. Ia kueri khusus
    rezim biaya untuk governance (SPEC 31), dan kolomnya hanya PnL. Palsunya
    memulangkan apa yang testnya inginkan, testnya hijau, dan di produksi kedua
    masukan tetap hilang.

    Test ini memeriksa **SQL-nya**, bukan palsunya.
    """

    @pytest.mark.asyncio
    async def test_kuerinya_mengambil_kolom_validasinya(self) -> None:
        from aruna.db.repositories.backtest import BacktestRepository

        class _DB:
            def __init__(self) -> None:
                self.sql = ""

            async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
                self.sql = sql
                return None

        db = _DB()
        await BacktestRepository(db, model_version="x").validasi_terakhir()

        assert "walk_forward" in db.sql
        assert "holdout_included" in db.sql

    @pytest.mark.asyncio
    async def test_tanpa_lintasan_memulangkan_none(self) -> None:
        from aruna.db.repositories.backtest import BacktestRepository

        class _DB:
            async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
                return None

        assert await BacktestRepository(_DB(), model_version="x").validasi_terakhir() is None


class TestSnapshotMembacanya:
    """Tanpa ini, ``_validasi`` sempurna dan ``note.pembelajaran.backtest``
    selamanya ``None`` - kedua masukan tetap hilang, dan seluruh test di atas
    tetap hijau."""

    @pytest.mark.asyncio
    async def test_pembaca_pembelajaran_membawa_backtest(self) -> None:
        from aruna.core.enums import Horizon, Market
        from aruna.learning.snapshot import PembacaPembelajaran

        class _Repo:
            """Bentuknya mengikuti ``BacktestRepository`` yang sungguhan."""

            def __init__(self) -> None:
                self.dipanggil = 0

            async def validasi_terakhir(self) -> dict[str, Any] | None:
                self.dipanggil += 1
                return {
                    "walk_forward": {"verdict": "CONSISTENT"},
                    "holdout_included": 1,
                }

        repo = _Repo()
        hasil = await PembacaPembelajaran(backtest=repo).baca(
            market=Market.CRYPTO, interval=Horizon.H1
        )

        assert repo.dipanggil == 1
        assert hasil.backtest is not None
        assert hasil.backtest["holdout_included"] == 1

    @pytest.mark.asyncio
    async def test_tanpa_repositori_bukan_kegagalan(self) -> None:
        from aruna.core.enums import Horizon, Market
        from aruna.learning.snapshot import PembacaPembelajaran

        hasil = await PembacaPembelajaran().baca(
            market=Market.CRYPTO, interval=Horizon.H1
        )

        assert hasil.backtest is None

    def test_aplikasi_mengoper_repositorinya(self) -> None:
        """Diperiksa lewat AST: argumen yang tidak dioper membuat pembacanya
        selamanya None di produksi, dan test di atas tetap hijau."""
        import ast
        import inspect

        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "PembacaPembelajaran"):
                assert any(k.arg == "backtest" for k in n.keywords), (
                    "PembacaPembelajaran dibangun tanpa repositori backtest"
                )
                return
        raise AssertionError("PembacaPembelajaran tidak dibangun di app.py")


class TestAngkanyaTidakBocor:
    def test_pesan_futures_tidak_membawa_angka_backtest(self) -> None:
        """Angka backtest tidak berubah antar tick. Menyuapkannya per keputusan
        membuat pesan membawa angka yang terlihat relevan padahal tidak - dan
        net PnL backtest yang tercetak di sebelah entry akan terbaca operator
        sebagai perkiraan hasil rencana ini."""
        from tests.test_futures_notify_pasal1426 import NOW, FakePlan, note

        from aruna.futures.notify import _alert

        pembelajaran = _Pembelajaran(backtest={
            "walk_forward": {"verdict": "CONSISTENT"},
            "holdout_included": 1,
            "net_pnl": "-10551.60",
            "direction_correct": 1560,
        })
        teks = _alert(FakePlan(), NOW, note=note(pembelajaran=pembelajaran))

        assert "10551" not in teks
        assert "CONSISTENT" not in teks


@pytest.mark.parametrize("masukan", [Masukan.WALK_FORWARD, Masukan.OUT_OF_SAMPLE])
def test_bukan_lagi_ditulis_mati_sebagai_false(masukan: Masukan) -> None:
    """Keduanya sempat ditulis ``False`` tanpa syarat, dengan alasan yang masuk
    akal waktu itu: tidak ada backtest yang pernah tersimpan. Alasannya sudah
    tidak berlaku, dan nilai mati yang alasannya kedaluwarsa adalah pengukuran
    yang berhenti mengukur."""
    import ast
    import inspect

    from aruna.futures import service

    sumber = inspect.getsource(service._kelengkapan_fase)
    pohon = ast.parse(sumber.strip())
    for n in ast.walk(pohon):
        if (isinstance(n, ast.Attribute) and n.attr == masukan.name
                and isinstance(getattr(n, "ctx", None), ast.Load)):
            break
    assert f"Masukan.{masukan.name}: False" not in sumber
