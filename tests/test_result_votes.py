"""HASIL PEMILIHAN di pesan hasil, dibaca balik dari ``council_votes``.

Pesan hasil selalu berbunyi "Tidak ada catatan pemilihan untuk prediksi ini."
Kalimat itu benar saat ditulis - ``agent_decisions`` memang kosong - dan
berhenti benar begitu ``council_votes`` mulai terisi tanpa ada yang membacanya
kembali.

Tautannya tidak perlu dibuat. ``signal_snapshots.council_session_id`` sudah
ditulis pada setiap penguncian sejak kolomnya ada; yang hilang hanya
pencariannya. Satu sisi jembatan dibangun, sisi lainnya tidak - dan itu kelas
cacat yang sama seperti kode yang ditulis, diekspor, diuji, lalu tidak pernah
dicapai jalur yang benar-benar jalan.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from aruna.core.enums import Market
from aruna.db.repositories.signals import SignalRepository

SAAT = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class _Db:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return self.rows


def _repo(rows) -> SignalRepository:
    repo = SignalRepository.__new__(SignalRepository)
    repo._db = _Db(rows)
    return repo


def _vote(role: str, *, setuju: bool = True, abstain: bool = False):
    return {"role": role, "abstained": abstain, "agreed_with_council": setuju}


class TestMembacaSuaraBalik:
    @pytest.mark.asyncio
    async def test_setuju_dan_kontra_dipisah(self) -> None:
        split = await _repo([
            _vote("TECHNICAL"), _vote("MOMENTUM"), _vote("VOLUME", setuju=False),
        ]).votes_for("abc")

        assert split.setuju == ("TECHNICAL", "MOMENTUM")
        assert split.kontra == ("VOLUME",)

    @pytest.mark.asyncio
    async def test_abstain_berdiri_sendiri(self) -> None:
        """Agent yang tidak punya bukti tidak sedang menolak apa pun.
        Memasukkannya ke KONTRA membuat data yang hilang terbaca sebagai
        perlawanan, dan setiap feed yang mati terlihat seperti council yang
        terbelah."""
        split = await _repo([
            _vote("TECHNICAL"),
            _vote("NEWS", setuju=False, abstain=True),
        ]).votes_for("abc")

        assert split.abstain == ("NEWS",)
        assert split.kontra == ()

    @pytest.mark.asyncio
    async def test_abstain_menang_atas_agreed(self) -> None:
        """Baris yang abstain DAN bertanda setuju tetap abstain: ia tidak
        memberikan suara, dan menghitungnya sebagai dukungan akan menggelembungkan
        sisi yang menang."""
        split = await _repo([_vote("NEWS", setuju=True, abstain=True)]).votes_for("a")
        assert split.abstain == ("NEWS",) and split.setuju == ()

    @pytest.mark.asyncio
    async def test_tanpa_baris_mengembalikan_none(self) -> None:
        """Bukan VoteSplit kosong. "Nol agent setuju" dan "tidak ada catatan"
        adalah dua pernyataan berbeda, dan pesannya mengatakan mana yang
        terjadi."""
        assert await _repo([]).votes_for("abc") is None

    @pytest.mark.asyncio
    async def test_dibaca_apa_adanya_bukan_dihitung_ulang(self) -> None:
        """``agreed_with_council`` yang tersimpan adalah penilaian saat sesi itu
        berjalan. Menyusunnya ulang dari ``decision`` sekarang berarti
        membandingkan pendapat lama dengan aturan baru - cara paling halus untuk
        mengubah catatan lama (PASAL 11.21).
        """
        repo = _repo([_vote("TECHNICAL", setuju=False)])
        await repo.votes_for("abc")

        sql = repo._db.queries[0]
        assert "agreed_with_council" in sql
        assert "decision" not in sql.replace("v.decision", "")


class TestDipasangKeBarisHasil:
    class _Resolver:
        def __init__(self, split=None, meledak: bool = False) -> None:
            self.split = split
            self.meledak = meledak
            self.diminta: list[str] = []

        async def votes_for(self, signal_id: str):
            self.diminta.append(signal_id)
            if self.meledak:
                raise RuntimeError("database sedang tidak enak badan")
            return self.split

    def _loop(self, resolver):
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        loop = UpkeepLoop(
            refresher=None, resolver=resolver, locker=None,
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=SAAT),
        )
        return loop

    @pytest.mark.asyncio
    async def test_suara_dipasang(self) -> None:
        from aruna.notify.verdict import VoteSplit

        split = VoteSplit(("TECHNICAL",), ("VOLUME",))
        loop = self._loop(self._Resolver(split))
        baris = [{"signal_id": "abc"}]

        await loop._attach_votes(baris)
        assert baris[0]["votes"] is split

    @pytest.mark.asyncio
    async def test_tanpa_catatan_tidak_memasang_apa_pun(self) -> None:
        loop = self._loop(self._Resolver(None))
        baris = [{"signal_id": "abc"}]

        await loop._attach_votes(baris)
        assert "votes" not in baris[0]

    @pytest.mark.asyncio
    async def test_pencarian_gagal_tidak_membatalkan_pesan(self) -> None:
        """Yang hilang kalau query gagal adalah satu blok keterangan; yang
        hilang kalau pesannya batal adalah kabar bahwa ARUNA salah."""
        loop = self._loop(self._Resolver(meledak=True))
        baris = [{"signal_id": "abc"}, {"signal_id": "def"}]

        await loop._attach_votes(baris)
        assert all("votes" not in b for b in baris)

    @pytest.mark.asyncio
    async def test_satu_gagal_tidak_menjatuhkan_sisanya(self) -> None:
        from aruna.notify.verdict import VoteSplit

        class _Pilih(self._Resolver):
            async def votes_for(self, signal_id: str):
                if signal_id == "buruk":
                    raise RuntimeError("baris ini saja")
                return VoteSplit(("TECHNICAL",), ())

        loop = self._loop(_Pilih())
        baris = [{"signal_id": "buruk"}, {"signal_id": "baik"}]

        await loop._attach_votes(baris)
        assert "votes" not in baris[0]
        assert baris[1]["votes"].setuju == ("TECHNICAL",)

    @pytest.mark.asyncio
    async def test_resolver_tanpa_pencarian_dilewati(self) -> None:
        """Resolver lama tanpa metode ini tidak boleh membuat pass resolusi
        meledak."""
        loop = self._loop(SimpleNamespace())
        baris = [{"signal_id": "abc"}]

        await loop._attach_votes(baris)
        assert "votes" not in baris[0]


class TestSampaiKeJalurYangBenarBenarJalan:
    """Memanggil ``_attach_votes`` sendiri hanya membuktikan metodenya bekerja.

    Cacat berulang di repo ini bukan metode yang salah - melainkan metode yang
    benar dan tidak pernah dipanggil dari jalur hidup.
    """

    @pytest.mark.asyncio
    async def test_pass_resolusi_memasang_suara_ke_pesannya(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.notify.verdict import VoteSplit
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        split = VoteSplit(("TECHNICAL",), ("VOLUME",))

        class _Resolver:
            async def votes_for(self, signal_id: str):
                return split

        class _Notifier:
            def __init__(self) -> None:
                self.baris: list[dict] = []

            async def announce(self, baris, *, now):
                self.baris = baris
                return len(baris)

        notifier = _Notifier()
        loop = UpkeepLoop(
            refresher=None, resolver=_Resolver(), locker=None,
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=SAAT),
            results=notifier,
        )

        signal = SimpleNamespace(
            symbol="BTC/USDT", direction="BUY", signal_id="abc",
            reference_price="63000", target_price="66000",
        )
        outcome = SimpleNamespace(
            outcome_class=SimpleNamespace(value="TARGET_REACHED"),
            target_reached=True,
        )
        await loop._announce_results(
            SimpleNamespace(scored=[(signal, outcome)]), SAAT
        )

        assert notifier.baris, "pesan hasil tidak terkirim sama sekali"
        assert notifier.baris[0]["votes"] is split


class TestHasilTradeSampaiKePesan:
    """Diputuskan di paper trade, dicetak di pesan - dan sampai ini ada,
    angkanya ditulis ke database lalu dibuang."""

    @pytest.mark.asyncio
    async def test_hasil_trade_ikut_di_baris_pesan(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        class _Notifier:
            def __init__(self) -> None:
                self.baris: list[dict] = []

            async def announce(self, baris, *, now):
                self.baris = baris
                return len(baris)

        notifier = _Notifier()
        loop = UpkeepLoop(
            refresher=None, resolver=SimpleNamespace(), locker=None,
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=SAAT),
            results=notifier,
        )

        signal = SimpleNamespace(
            symbol="BTC/USDT", direction="BUY", signal_id="abc",
            reference_price="63000", target_price="66000",
        )
        outcome = SimpleNamespace(
            outcome_class=SimpleNamespace(value="WRONG_FROM_START"),
            target_reached=False,
        )
        await loop._announce_results(
            SimpleNamespace(scored=[(signal, outcome)], trades={"abc": "LOSS"}),
            SAAT,
        )

        assert notifier.baris[0]["trade_result"] == "LOSS"

    @pytest.mark.asyncio
    async def test_tanpa_paper_trade_barisnya_kosong_bukan_salah(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        class _Notifier:
            def __init__(self) -> None:
                self.baris: list[dict] = []

            async def announce(self, baris, *, now):
                self.baris = baris
                return len(baris)

        notifier = _Notifier()
        loop = UpkeepLoop(
            refresher=None, resolver=SimpleNamespace(), locker=None,
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=SAAT),
            results=notifier,
        )
        signal = SimpleNamespace(
            symbol="BTC/USDT", direction="BUY", signal_id="abc",
            reference_price="63000", target_price="66000",
        )
        outcome = SimpleNamespace(
            outcome_class=SimpleNamespace(value="NO_POSITION"), target_reached=False,
        )
        await loop._announce_results(
            SimpleNamespace(scored=[(signal, outcome)]), SAAT
        )

        assert notifier.baris[0]["trade_result"] is None

    @pytest.mark.asyncio
    async def test_resolver_mengembalikan_hasil_tradenya(self) -> None:
        """``_simulate_trade`` menghitung menang-kalah lalu - sebelum ini -
        hanya menuliskannya ke database.

        **Diperiksa dari yang dikembalikannya, bukan dari ejaan sumbernya.**
        Versi pertama test ini mencocokkan teks ``"return closed.result"``, dan
        teks itu berhenti cocok begitu paper trade-nya dibawa keluar utuh
        supaya pesannya bisa menyebut kotor, biaya, dan bersih - perubahan yang
        justru memperluas apa yang keluar dari sini. Sebuah test yang merah
        karena barisnya ditulis ulang, dan yang akan tetap hijau kalau nilainya
        diam-diam dibuang, menguji ejaan dan bukan perilaku.
        """
        from decimal import Decimal

        from aruna.signals.service import SignalService

        ditulis: list[Any] = []

        class _Store:
            async def record_trade(self, trade: Any) -> None:
                ditulis.append(trade)

        svc = SignalService.__new__(SignalService)
        svc._store = _Store()

        ditutup = SimpleNamespace(
            result=SimpleNamespace(value="LOSS"),
            gross_pnl=Decimal("2.68"),
            total_costs=Decimal("3.67"),
            net_pnl=Decimal("-0.99"),
        )
        modul = sys.modules["aruna.signals.service"]
        asli_open = modul.open_trade
        asli_close = modul.close_trade
        modul.open_trade = lambda *a, **k: SimpleNamespace()
        modul.close_trade = lambda *a, **k: ditutup
        try:
            hasil = await svc._simulate_trade(
                SimpleNamespace(
                    signal_id="abc", symbol="BTC/USDT", market=Market.CRYPTO,
                    locked_at=SAAT, target_price=None,
                    entry_price=Decimal("63000"),
                ),
                SimpleNamespace(final_price=Decimal("62000"), resolved_at=SAAT),
            )
        finally:
            modul.open_trade = asli_open
            modul.close_trade = asli_close

        assert ditulis == [ditutup], "trade-nya harus tetap tersimpan"
        assert hasil is ditutup, (
            "menang-kalah dan angkanya harus KELUAR dari sini; selama ia "
            "berhenti di database, pesan hasil menebaknya dari kelas outcome"
        )
        assert hasil.result.value == "LOSS"
        assert (hasil.gross_pnl, hasil.total_costs, hasil.net_pnl) == (
            Decimal("2.68"), Decimal("3.67"), Decimal("-0.99")
        )

    def test_hasilnya_dicatat_di_resolve_result(self) -> None:
        import inspect

        from aruna.signals.service import ResolveResult, SignalService

        assert "trades" in ResolveResult.__dataclass_fields__
        assert "result.trades[" in inspect.getsource(SignalService._complete)


class TestSeamnyaAda:
    def test_service_meneruskan_ke_repository(self) -> None:
        """Loop memegang resolver, bukan penyimpanan. Menjangkau
        ``resolver._store`` akan membuatnya bergantung pada nama atribut privat
        modul lain."""
        import inspect

        from aruna.signals.service import SignalService

        assert hasattr(SignalService, "votes_for")
        assert "_store" not in inspect.getsource(
            __import__("aruna.upkeep.loop", fromlist=["x"]).UpkeepLoop._attach_votes
        )

    @pytest.mark.asyncio
    async def test_service_mengembalikan_apa_yang_repository_beri(self) -> None:
        from aruna.signals.service import SignalService

        svc = SignalService.__new__(SignalService)
        svc._store = SimpleNamespace(
            votes_for=lambda sid: _selesai("dipanggil")
        )
        assert await svc.votes_for("abc") == "dipanggil"

    @pytest.mark.asyncio
    async def test_penyimpanan_tanpa_metode_itu_aman(self) -> None:
        from aruna.signals.service import SignalService

        svc = SignalService.__new__(SignalService)
        svc._store = SimpleNamespace()
        assert await svc.votes_for("abc") is None


async def _selesai(value):
    return value
