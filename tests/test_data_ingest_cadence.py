"""Cadence poll dan cara melaporkan pembacaan ulang.

Dua cacat yang ditemukan dari log operator, dan keduanya satu sebab: ARUNA
bertanya ke Yahoo empat sampai empat belas kali lebih cepat daripada Yahoo
menjawab.

Terukur dari enam jam snapshot tersimpan, sebelum apa pun diubah:

===========  ==========  ============  ===========
simbol       polling     harga beda    DUPLICATE
===========  ==========  ============  ===========
BTC/USDT     2083        970           0
GOTO         1269        1             505
ICBP         1269        5             265
===========  ==========  ============  ===========

Nol pada feed yang berubah tiap panggilan, ratusan pada feed yang tidak. Itu
bukan ukuran mutu data - itu ukuran cadence pemanggilnya sendiri.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Market
from aruna.data.ingest import IngestResult


class _Ingestor:
    """Ingestor palsu yang hanya menghitung berapa kali ia dipanggil."""

    def __init__(self) -> None:
        self.panggilan = 0

    async def poll_once(self):
        self.panggilan += 1
        return SimpleNamespace(failures=[], snapshots=1)


def _service(idx_interval: float = 20.0):
    from aruna.data.ingest import IngestService

    idx, crypto = _Ingestor(), _Ingestor()
    service = IngestService(
        {Market.IDX: idx, Market.CRYPTO: crypto},
        DataSettings(_env_file=None, idx_poll_interval_sec=idx_interval),
    )
    return service, idx, crypto


class TestCadenceIdxTerpisah:
    """Loop satu kecepatan selalu salah untuk salah satu feed."""

    @pytest.mark.asyncio
    async def test_idx_dilewati_sebelum_jendelanya_lewat(self, monkeypatch) -> None:
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "idx_worth_polling", lambda: True)
        detik = [1000.0]
        monkeypatch.setattr(modul, "monotonic", lambda: detik[0])

        service, idx, crypto = _service(idx_interval=20.0)

        await service.poll_once()
        detik[0] += 5.0          # satu tick loop kemudian
        await service.poll_once()
        detik[0] += 5.0
        await service.poll_once()

        assert idx.panggilan == 1, "IDX dipanggil lebih cepat dari jendelanya"
        assert crypto.panggilan == 3, "crypto tidak boleh ikut melambat"

    @pytest.mark.asyncio
    async def test_idx_dipanggil_lagi_setelah_jendelanya_lewat(
        self, monkeypatch
    ) -> None:
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "idx_worth_polling", lambda: True)
        detik = [1000.0]
        monkeypatch.setattr(modul, "monotonic", lambda: detik[0])

        service, idx, _ = _service(idx_interval=20.0)

        await service.poll_once()
        detik[0] += 20.0
        await service.poll_once()

        assert idx.panggilan == 2

    @pytest.mark.asyncio
    async def test_poll_pertama_selalu_lolos(self, monkeypatch) -> None:
        """Perintah sekali jalan tidak boleh diam tanpa alasan yang terlihat.

        Jamnya dikunci ke angka kecil dengan sengaja. Dibiarkan memakai
        ``monotonic()`` asli, test ini lulus pada versi yang rusak sekalipun -
        uptime mesin selalu jauh lebih besar daripada interval mana pun, jadi
        "sudah cukup lama sejak poll terakhir" selalu benar dan cabang yang
        diuji tidak pernah dijalankan.
        """
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "idx_worth_polling", lambda: True)
        monkeypatch.setattr(modul, "monotonic", lambda: 10.0)
        service, idx, _ = _service(idx_interval=3600.0)

        await service.poll_once()
        assert idx.panggilan == 1

    @pytest.mark.asyncio
    async def test_bursa_tutup_tetap_menang_atas_cadence(self, monkeypatch) -> None:
        """Jendela poll yang terbuka bukan alasan memanggil bursa yang tutup."""
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "idx_worth_polling", lambda: False)
        service, idx, crypto = _service()

        await service.poll_once()
        assert idx.panggilan == 0
        assert crypto.panggilan == 1


class TestPembacaanUlangTidakDiteriakkan:
    """1165 peringatan dan 1165 baris kejadian per enam jam, semuanya untuk
    data yang tidak apa-apa."""

    class _Store:
        def __init__(self) -> None:
            self.kejadian: list[dict] = []
            self.snapshot = 0

        async def record_provider_event(self, **kwargs) -> None:
            self.kejadian.append(kwargs)

        async def record_snapshot(self, asset_id, snapshot) -> None:
            self.snapshot += 1

    def _ingestor(self, kualitas: DataQuality, store):
        from aruna.data.ingest import MarketIngestor

        ingestor = MarketIngestor.__new__(MarketIngestor)
        ingestor._store = store
        # Pembanding gerbang perubahan. Dibangun tangan seperti bidang lain di
        # sini karena `__init__` dilewati; kosong berarti setiap aset dilihat
        # untuk pertama kali, dan `Perubahan.PERTAMA` melewatkannya - yang
        # memang dituju kedua test ini, karena keduanya soal peredaman
        # kejadian mutu, bukan soal gerbangnya.
        ingestor._tersimpan = {}
        # `market` adalah property yang membaca provider, jadi pasarnya
        # ditentukan di sini, bukan ditimpa pada instance.
        ingestor._provider = SimpleNamespace(
            name="yahoo",
            market=Market.IDX,
            capabilities=SimpleNamespace(supports_order_book=False),
            fetch_snapshot=lambda symbol: asyncio.sleep(
                0,
                SimpleNamespace(
                    provenance=SimpleNamespace(latency_ms=12.0),
                    quality=DataQuality.OK,
                    quality_detail=None,
                ),
            ),
        )
        ingestor._gate = SimpleNamespace(
            evaluate_quote=lambda quote: SimpleNamespace(
                ok=False, quality=kualitas, detail="uji",
            )
        )
        return ingestor

    @pytest.mark.asyncio
    async def test_pembacaan_ulang_tidak_menulis_kejadian(self, monkeypatch) -> None:
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "_snapshot_as_quote", lambda s: s)
        monkeypatch.setattr(modul, "replace", lambda s, **k: s)

        store = self._Store()
        ingestor = self._ingestor(DataQuality.REPEATED_READ, store)
        # `IngestResult` yang sungguhan, bukan SimpleNamespace: pencacah yang
        # dikarang tangan sudah melenceng sekali dari bidang objek aslinya, dan
        # test yang hijau di atas bidang karangan tidak menguji apa pun.
        hasil = IngestResult(market=Market.IDX, provider="yahoo")

        await ingestor._poll_asset(SimpleNamespace(id=1, symbol="GOTO"), hasil)

        assert store.kejadian == []
        # Barisnya tetap ditulis: kelasnya tersimpan, tidak ada yang hilang.
        assert store.snapshot == 1

    @pytest.mark.asyncio
    async def test_cacat_sungguhan_tetap_menulis_kejadian(self, monkeypatch) -> None:
        """Yang diredam hanya satu kelas. Kalau semuanya ikut diam, ini bukan
        memperbaiki laporan melainkan mematikannya."""
        from aruna.data import ingest as modul

        monkeypatch.setattr(modul, "_snapshot_as_quote", lambda s: s)
        monkeypatch.setattr(modul, "replace", lambda s, **k: s)

        store = self._Store()
        ingestor = self._ingestor(DataQuality.STALE, store)
        # `IngestResult` yang sungguhan, bukan SimpleNamespace: pencacah yang
        # dikarang tangan sudah melenceng sekali dari bidang objek aslinya, dan
        # test yang hijau di atas bidang karangan tidak menguji apa pun.
        hasil = IngestResult(market=Market.IDX, provider="yahoo")

        await ingestor._poll_asset(SimpleNamespace(id=1, symbol="GOTO"), hasil)

        assert len(store.kejadian) == 1
        assert store.kejadian[0]["quality"] is DataQuality.STALE
