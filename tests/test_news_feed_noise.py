"""Feed yang sesekali putus tidak berteriak; yang mati berteriak.

Terukur di log produksi sebelum aturan ini ada: 14 kegagalan dari 134 siklus,
tersebar di tiga feed berbeda - coindesk 7, kontan 6, detik-finance 1. Itu
bukan feed yang mati, itu internet yang sesekali putus, dan tiap satunya
menulis satu baris WARNING.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aruna.core.errors import DataSourceUnavailableError
from aruna.news.service import FEED_GAGAL_BERUNTUN, NewsService


class Log:
    def __init__(self) -> None:
        self.warning_: list[dict] = []
        self.info_: list[dict] = []
        self.debug_: list[dict] = []

    def warning(self, event: str, **kw) -> None:
        self.warning_.append({"event": event, **kw})

    def info(self, event: str, **kw) -> None:
        self.info_.append({"event": event, **kw})

    def debug(self, event: str, **kw) -> None:
        self.debug_.append({"event": event, **kw})

    def exception(self, event: str, **kw) -> None:
        self.warning_.append({"event": event, **kw})


@pytest.fixture
def layanan(monkeypatch) -> NewsService:
    from aruna.news import service as modul

    svc = NewsService.__new__(NewsService)
    svc._gagal_beruntun = {}
    monkeypatch.setattr(modul, "log", Log())
    return svc


def _log(monkeypatch=None):
    from aruna.news import service as modul

    return modul.log


class TestKegagalanSesekaliTidakBerteriak:
    def test_gagal_sekali_hanya_dicatat_pelan(self, layanan) -> None:
        layanan._catat_gagal("coindesk", DataSourceUnavailableError("timeout"))

        assert _log().warning_ == []
        assert _log().debug_[0]["event"] == "news.feed_blip"

    def test_di_bawah_ambang_tetap_diam(self, layanan) -> None:
        for _ in range(FEED_GAGAL_BERUNTUN - 1):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))

        assert _log().warning_ == []

    def test_tepat_di_ambang_berteriak(self, layanan) -> None:
        """Tiga kegagalan beruntun pada jadwal lima menit berarti seperempat
        jam tanpa satu berita pun dari sumber itu."""
        for _ in range(FEED_GAGAL_BERUNTUN):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))

        assert len(_log().warning_) == 1
        assert _log().warning_[0]["feed"] == "coindesk"
        assert _log().warning_[0]["consecutive"] == FEED_GAGAL_BERUNTUN

    def test_ambangnya_masuk_akal_terhadap_jadwalnya(self) -> None:
        """Test di atas mengulang ``range(FEED_GAGAL_BERUNTUN)``, jadi ia
        menyesuaikan diri berapa pun angkanya - ia menguji mekanismenya, bukan
        nilainya. Yang di bawah ini mengikat nilainya, dan mengikatnya pada
        alasan, bukan pada dirinya sendiri.

        Feed disegarkan tiap lima menit. Di bawah dua, satu putus jaringan
        sudah berteriak - persis kebisingan yang aturan ini hapus. Di atas
        enam, sebuah feed yang benar-benar mati diam selama setengah jam
        sebelum ada yang diberi tahu.
        """
        assert 2 <= FEED_GAGAL_BERUNTUN <= 6

    def test_di_atas_ambang_terus_berteriak(self, layanan) -> None:
        """Feed yang tetap mati tidak boleh berhenti dilaporkan."""
        for _ in range(FEED_GAGAL_BERUNTUN + 2):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))

        assert len(_log().warning_) == 3


class TestBeruntunBukanTotal:
    def test_pulih_mereset_hitungannya(self, layanan) -> None:
        """Feed yang gagal sekali lalu pulih tidak sedang bermasalah."""
        for _ in range(FEED_GAGAL_BERUNTUN - 1):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))
        layanan._catat_pulih("coindesk")
        for _ in range(FEED_GAGAL_BERUNTUN - 1):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))

        assert _log().warning_ == []

    def test_feed_dihitung_terpisah(self, layanan) -> None:
        """Tiga feed yang masing-masing gagal sekali bukan satu feed yang gagal
        tiga kali - dan pola produksi persis yang pertama."""
        for nama in ("coindesk", "kontan", "detik-finance"):
            layanan._catat_gagal(nama, DataSourceUnavailableError("x"))

        assert _log().warning_ == []


class TestPemulihanDicatat:
    def test_pulih_sesudah_berteriak_dicatat(self, layanan) -> None:
        """Peringatan yang tidak pernah dicabut meninggalkan pembacanya menduga
        masalahnya masih ada."""
        for _ in range(FEED_GAGAL_BERUNTUN):
            layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))
        layanan._catat_pulih("coindesk")

        assert _log().info_[-1]["event"] == "news.feed_recovered"
        assert _log().info_[-1]["after"] == FEED_GAGAL_BERUNTUN

    def test_pulih_tanpa_pernah_berteriak_tidak_berisik(self, layanan) -> None:
        layanan._catat_gagal("coindesk", DataSourceUnavailableError("x"))
        layanan._catat_pulih("coindesk")

        assert _log().info_ == []

    def test_pulih_tanpa_pernah_gagal_diam(self, layanan) -> None:
        layanan._catat_pulih("coindesk")

        assert _log().info_ == []


class TestJalurHidup:
    @pytest.mark.asyncio
    async def test_ingest_memanggil_pencatatnya(self, monkeypatch) -> None:
        """Tanpa ini, seluruh peredam bisa dicabut dan tiap test unit tetap
        hijau - feed yang mati kembali berteriak tiap siklus, atau tidak sama
        sekali."""
        from aruna.news import service as modul

        svc = NewsService.__new__(NewsService)
        svc._gagal_beruntun = {}
        svc._aliases = {"BTC": 1}
        svc._provider = SimpleNamespace(
            feeds=[SimpleNamespace(name="coindesk")],
            fetch=_gagal,
        )
        monkeypatch.setattr(modul, "log", Log())

        hasil = await svc.ingest()

        assert hasil.failures
        assert svc._gagal_beruntun["coindesk"] == 1


async def _gagal(feed, *, symbol_aliases):
    raise DataSourceUnavailableError("rss unreachable")
