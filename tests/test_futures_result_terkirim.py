"""Hasil rencana futures yang tidak pernah sampai ke operator.

Dilaporkan operator: *"saat signal dikirim ke tele gaada resultnya, hilang
semua"*.

Sebabnya bukan yang rusak melainkan yang tidak pernah ada: ``PlanNotifier``
punya tepat dua metode - ``announce`` untuk rencana dan ``daily`` untuk laporan
penutup hari. Tidak ada metode hasil. Rencana dikabarkan, horizonnya lewat,
hasilnya diskor dan disimpan, dan operator tidak pernah diberi tahu bagaimana
akhirnya.

**Dua penjaga yang tidak boleh saling menghapus.** Yang satu: hasil tidak boleh
dikirim untuk rencana yang tidak pernah dikabarkan - operator pernah mengeluh
persis itu di jalur signal, *"tiba tiba result aja tanpa sinyal kan aneh"*.
Yang lain: §11.21 melarang menyembunyikan LOSS. Penjaga pertama yang dipasang
terlalu ketat akan melanggar yang kedua, dan itu yang baru saja terjadi di
jalur spot - delapan puluh tujuh hasil dibungkam karena signalnya tidak pernah
bisa dikirim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aruna.futures.learning import PlanOutcome, PlanResult
from aruna.futures.models import PositionSide

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _hasil(
    signal_id: str = "abc",
    symbol: str = "BTCUSDT",
    outcome: PlanOutcome = PlanOutcome.TARGET_HIT,
    side: PositionSide = PositionSide.LONG,
    entry: str = "100",
    exit_price: str | None = "105",
) -> PlanResult:
    return PlanResult(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        outcome=outcome,
        entry=Decimal(entry),
        exit_price=None if exit_price is None else Decimal(exit_price),
    )


class _Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.balasan: list[int | None] = []

    async def send(self, teks: str) -> bool:
        self.sent.append(teks)
        self.balasan.append(None)
        return True

    async def send_id(self, teks: str, *, reply_to: int | None = None) -> int:
        self.sent.append(teks)
        self.balasan.append(reply_to)
        return 999


class _Store:
    """Bentuknya meniru ``FuturesRepository`` yang sungguhan."""

    def __init__(self, terkirim: dict[str, int | None] | None = None) -> None:
        self._terkirim = terkirim if terkirim is not None else {}
        self.ditandai: list[tuple[str, int | None]] = []

    async def pushed_message_ids(
        self, signal_ids: list[str]
    ) -> dict[str, int | None]:
        return {k: v for k, v in self._terkirim.items() if k in signal_ids}

    async def mark_pushed(
        self, signal_id: str, *, message_id: int | None, at: Any
    ) -> None:
        self.ditandai.append((signal_id, message_id))


def _notifier(sender=None, store=None, **kw):
    from aruna.futures.notify import PlanNotifier

    return PlanNotifier(
        sender=sender or _Sender(),
        horizon_hours=4.0,
        store=store,
        **kw,
    )


class TestHasilnyaDikirim:
    @pytest.mark.asyncio
    async def test_hasil_rencana_yang_pernah_dikabarkan_dikirim(self) -> None:
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 555}))

        assert await n.results([_hasil()], now=NOW) == 1
        assert "ARUNA FUTURES RESULT" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_membalas_pesan_rencananya(self) -> None:
        """Operator: *"seharusnya sinyal dulu terus reply chat yang mana hasil
        resultnya"*. Tanpa balasan, sebuah RESULT di antara dua puluh simbol
        menuntut pembacanya menggulir mencari rencana mana yang dimaksud."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 555}))

        await n.results([_hasil()], now=NOW)

        assert sender.balasan == [555]

    @pytest.mark.asyncio
    async def test_menang_dan_kalah_sama_sama_dikirim(self) -> None:
        """§11.21 melarang menyembunyikan LOSS."""
        sender = _Sender()
        n = _notifier(sender, _Store({"a": 1, "b": 2, "c": 3}))

        await n.results(
            [
                _hasil("a", outcome=PlanOutcome.TARGET_HIT, exit_price="105"),
                _hasil("b", outcome=PlanOutcome.STOPPED_OUT, exit_price="97"),
                _hasil("c", outcome=PlanOutcome.LIQUIDATED, exit_price="90"),
            ],
            now=NOW,
        )

        assert len(sender.sent) == 3
        gabung = "\n".join(sender.sent)
        assert "LOSS" in gabung
        assert "LIQUIDATED" in gabung

    @pytest.mark.asyncio
    async def test_likuidasi_disebut_dengan_namanya(self) -> None:
        """Likuidasi adalah kekalahan terburuk yang bisa dihasilkan sistem ini.
        Menyebutnya "LOSS" saja menghapus perbedaan antara kena stop dan
        ditutup paksa bursa."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results(
            [_hasil(outcome=PlanOutcome.LIQUIDATED, exit_price="90")], now=NOW
        )

        assert "LIQUIDATED" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_geraknya_dicetak(self) -> None:
        """Angka inti pesannya, dan versi pertama diam-diam tidak mencetaknya.

        ``Decimal`` tidak diimpor di tingkat modul, jadi ``_gerak_pct``
        melempar ``NameError`` - dan penjaga ``except Exception`` di dalamnya
        menelannya lalu memulangkan ``None``. Pesannya tetap terkirim, tetap
        terlihat masuk akal, dan kehilangan satu-satunya angka yang mengatakan
        seberapa benar rencananya. Tidak ada satu pun test yang menangkapnya
        karena tak satu pun memeriksa barisnya.
        """
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results([_hasil(entry="100", exit_price="105")], now=NOW)

        assert "GERAK:" in sender.sent[0]
        assert "+5.00%" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_short_yang_menang_positif(self) -> None:
        """Bertanda menurut ARAH POSISI. Gerak pasar mentah akan membuat SHORT
        yang menang terbaca negatif - dan operator membaca tanda minus sebagai
        rugi."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results(
            [_hasil(side=PositionSide.SHORT, entry="100", exit_price="95")],
            now=NOW,
        )

        assert "+5.00%" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_kakinya_tetap_ada(self) -> None:
        from aruna.decision.output import KAKI

        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results([_hasil()], now=NOW)

        for baris in KAKI:
            assert baris in sender.sent[0]


class TestTidakAdaHasilTanpaRencananya:
    @pytest.mark.asyncio
    async def test_yang_tidak_pernah_dikabarkan_dibungkam(self) -> None:
        """*"tiba tiba result aja tanpa sinyal kan aneh"* - keluhan operator di
        jalur signal, dan jalur futures tidak boleh mengulanginya."""
        sender = _Sender()
        n = _notifier(sender, _Store({}))

        assert await n.results([_hasil()], now=NOW) == 0
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_tanpa_penyimpanan_semuanya_tetap_dikirim(self) -> None:
        """Arah kegagalannya disengaja. Tanpa penyimpanan tidak ada yang bisa
        membuktikan apa pun, dan membungkam semuanya akan menghapus kabar bahwa
        ARUNA salah gara-gara perakitan yang belum lengkap (§11.21)."""
        sender = _Sender()
        n = _notifier(sender, None)

        assert await n.results([_hasil()], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_pencarian_yang_gagal_tidak_membungkam(self) -> None:
        """Satu bug pencarian akan membungkam setiap kabar bahwa ARUNA salah.
        Gagal terbuka, sama seperti jalur signal."""
        class _Meledak:
            async def pushed_message_ids(self, signal_ids):
                raise RuntimeError("database putus")

        sender = _Sender()
        n = _notifier(sender, _Meledak())

        assert await n.results([_hasil()], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_terkirim_tanpa_id_tetap_dikirim(self) -> None:
        """``None`` berarti terkirim tapi id pesannya tidak tercatat - itu tetap
        terkirim, hanya tanpa balasan."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": None}))

        assert await n.results([_hasil()], now=NOW) == 1
        assert sender.balasan == [None]


class TestTidakDikirimDuaKali:
    @pytest.mark.asyncio
    async def test_hasil_yang_sama_hanya_sekali(self) -> None:
        """Resolver membaca ulang jendela yang sama tiap tick. Tanpa penanda,
        satu hasil dikirim setiap lima belas menit selamanya."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results([_hasil()], now=NOW)
        await n.results([_hasil()], now=NOW + timedelta(minutes=15))

        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_hasil_lain_tetap_lewat(self) -> None:
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1, "def": 2}))

        await n.results([_hasil("abc")], now=NOW)
        await n.results([_hasil("def")], now=NOW + timedelta(minutes=15))

        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_yang_belum_selesai_tidak_dikirim(self) -> None:
        """``OPEN`` bukan hasil."""
        sender = _Sender()
        n = _notifier(sender, _Store({"abc": 1}))

        await n.results(
            [_hasil(outcome=PlanOutcome.OPEN, exit_price=None)], now=NOW
        )

        assert sender.sent == []


class TestJejakPengirimanRencana:
    @pytest.mark.asyncio
    async def test_rencana_yang_dikabarkan_ditandai(self) -> None:
        """Tanpa ini, tidak ada satu pun hasil yang akan pernah lolos
        penjaganya - dan seluruh jalur ini menjadi kode yang tidak pernah
        mengirim apa pun."""
        from dataclasses import replace as _replace
        from types import SimpleNamespace

        from tests.test_futures_notify_pasal1426 import FakePlan

        from aruna.futures.plan import PlanVerdict

        dasar = _replace(FakePlan(), symbol="BTCUSDT")
        plan = SimpleNamespace(
            **{
                f.name: getattr(dasar, f.name)
                for f in dasar.__dataclass_fields__.values()
            },
            verdict=PlanVerdict.PLAN,
            signal_id="abc",
        )

        store = _Store({})
        n = _notifier(_Sender(), store)
        await n.announce([plan], now=NOW)

        assert store.ditandai == [("abc", 999)]


class TestJejakDiTabelnyaSendiri:
    """``futures_plans`` append-only, dan trigger-nya menolak setiap UPDATE.

    Terukur di produksi 2026-08-21::

        ERROR 1644: futures_plans is append-only: an issued plan cannot change
                    (FUTURES SPEC 47) - issue a new one

    Migrasi 0029 menambahkan kolom ke tabel itu dan menulisnya lewat UPDATE.
    Setiap tulisan ditolak, rencana tetap terkirim, pengirimannya tidak pernah
    tercatat - dan seluruh hasil tertahan penjaganya. Persis kegagalan yang
    0029 dimaksudkan memperbaiki.

    Testnya tidak menangkapnya karena db palsu tidak menjalankan trigger. Yang
    menangkapnya cuma produksi, dan itu terlambat.
    """

    class _Db:
        def __init__(self, rows=None) -> None:
            self.rows = rows or []
            self.sql: list[str] = []

        async def execute(self, sql: str, *args):
            self.sql.append(sql)

        async def fetch(self, sql: str, *args):
            self.sql.append(sql)
            return self.rows

    def _repo(self, rows=None):
        from aruna.db.repositories.futures import FuturesRepository

        db = self._Db(rows)
        return FuturesRepository(db), db

    @pytest.mark.asyncio
    async def test_tidak_pernah_mengubah_rencananya(self) -> None:
        """Penjaga yang seharusnya ada kemarin."""
        repo, db = self._repo()
        await repo.mark_pushed("abc", message_id=1, at=NOW)

        gabung = " ".join(db.sql).upper()
        assert "UPDATE FUTURES_PLANS" not in gabung

    @pytest.mark.asyncio
    async def test_ditulis_ke_tabel_pengiriman(self) -> None:
        repo, db = self._repo()
        await repo.mark_pushed("abc", message_id=1, at=NOW)

        assert "futures_plan_delivery" in " ".join(db.sql)

    @pytest.mark.asyncio
    async def test_pengiriman_kedua_tidak_merusak(self) -> None:
        """Satu rencana terkirim satu kali. Pengiriman kedua adalah
        pengulangan, dan jejak yang pertama yang berlaku - itu yang benar-benar
        dilihat operator."""
        repo, db = self._repo()
        await repo.mark_pushed("abc", message_id=1, at=NOW)

        assert "IGNORE" in " ".join(db.sql).upper()

    @pytest.mark.asyncio
    async def test_dibaca_dari_tabel_pengiriman(self) -> None:
        repo, db = self._repo([{"signal_id": "abc", "telegram_message_id": 7}])
        hasil = await repo.pushed_message_ids(["abc"])

        assert hasil == {"abc": 7}
        assert "futures_plan_delivery" in " ".join(db.sql)

    def test_kolomnya_benar_benar_ada_di_tabelnya(self) -> None:
        """Penjaga terhadap db palsu yang tidak memvalidasi SQL.

        Kesalahan kemarin lolos justru karena palsunya menerima SQL apa pun.
        Test ini membaca migrasinya dan membandingkan nama kolom yang dipakai
        kueri dengan yang benar-benar dibuat.
        """
        import inspect
        import pathlib
        import re

        from aruna.db.repositories.futures import FuturesRepository

        migrasi = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0030_futures_delivery_table.sql"
        ).read_text(encoding="utf-8")
        blok = migrasi[migrasi.index("CREATE TABLE futures_plan_delivery"):]
        blok = blok[: blok.index(") ENGINE")]

        sumber = (
            inspect.getsource(FuturesRepository.mark_pushed)
            + inspect.getsource(FuturesRepository.pushed_message_ids)
        )
        for nama in ("signal_id", "pushed_at", "telegram_message_id"):
            assert nama in sumber, nama
            assert re.search(rf"^\s*{nama}\s", blok, re.M), nama

    def test_migrasi_membuang_kolom_yang_salah_tempat(self) -> None:
        """Dua kolom yang terlihat berarti dan tidak pernah terisi adalah
        bentuk paling halus dari kode yang menyesatkan pembaca berikutnya."""
        import pathlib

        migrasi = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0030_futures_delivery_table.sql"
        ).read_text(encoding="utf-8")

        assert "DROP COLUMN pushed_at" in migrasi
        assert "DROP COLUMN telegram_message_id" in migrasi


class TestTersambungKeJalurHidup:
    """Tanpa penyambungan, seluruh berkas ini menguji kode yang tidak pernah
    dijalankan siapa pun - keluarga cacat yang paling sering muncul di sistem
    ini."""

    def test_penjadwal_mengabarkan_hasil(self) -> None:
        import inspect

        from aruna.futures.scheduler import FuturesScheduler

        sumber = inspect.getsource(FuturesScheduler.tick)

        assert "self._notifier.results(" in sumber

    def test_hasilnya_dibawa_dari_pass_resolusi(self) -> None:
        """Dibaca ulang dari database sesudahnya akan menuntut tebakan jendela
        waktu, dan jendela yang meleset sedikit mengabarkan hasil lama dua kali
        atau melewatkan yang baru."""
        import inspect

        from aruna.futures.resolve import FuturesResolver

        sumber = inspect.getsource(FuturesResolver._resolve_one)

        assert "run.results.append(result)" in sumber

    def test_notifier_menerima_penyimpanan(self) -> None:
        """Tanpa ``store`` tidak ada jejak pengiriman yang ditulis, dan setiap
        hasil akan lolos penjaganya tanpa diperiksa - persis bug "result tanpa
        signal" yang sudah dikeluhkan operator."""
        import inspect

        from aruna import cli

        sumber = inspect.getsource(cli)
        loop = sumber[sumber.index("async def _futures_loop"):]
        # Dicari di dalam panggilan `PlanNotifier(...)` saja. Versi pertama test
        # ini mencari di seluruh fungsi, dan `_futures_loop` sudah memuat
        # `store=app.futures_store` untuk `FuturesPlanService` - jadi ia hijau
        # tanpa notifiernya menerima apa pun.
        mulai = loop.index("PlanNotifier(")
        panggilan = loop[mulai : loop.index(")", loop.index("state=app", mulai))]

        assert "store=app.futures_store" in panggilan
