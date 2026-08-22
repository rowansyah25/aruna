"""Gerbang perubahan benar-benar terpasang di jalur poll (bagian 4-6).

`layak_simpan` yang benar tapi tidak dipanggil adalah kegagalan yang paling
sering terjadi di repo ini: kode yang ditulis, diuji, diekspor, dan tidak
pernah dipakai. Test di sini memanggil `poll_once` yang sungguhan dan menghitung
`INSERT` yang benar-benar sampai ke penyimpanan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import Market
from aruna.data.ingest import MarketIngestor
from aruna.data.models import Provenance, Snapshot
from aruna.data.perubahan import JEDA_WAJIB_DETIK

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


class _Kemampuan:
    supports_order_book = False
    expected_delay_sec = 0
    supports_streaming = False


class _Provider:
    """Provider yang memulangkan snapshot dari daftar, satu per poll."""

    name = "uji"
    market = Market.CRYPTO
    capabilities = _Kemampuan()

    def __init__(self, urutan: list[Snapshot]) -> None:
        self._urutan = list(urutan)
        self.diminta = 0

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        s = self._urutan[min(self.diminta, len(self._urutan) - 1)]
        self.diminta += 1
        return s


class _Aset:
    def __init__(self) -> None:
        self.id = 1
        self.symbol = "BTC/USDT"


class _Universe:
    async def assets(self, *, market: Any, enabled_only: bool) -> list[_Aset]:
        return [_Aset()]


class _Store:
    def __init__(self) -> None:
        self.ditulis: list[Snapshot] = []

    async def record_snapshot(self, asset_id: int, snapshot: Snapshot) -> int:
        self.ditulis.append(snapshot)
        return len(self.ditulis)

    async def record_provider_event(self, **kwargs: Any) -> int:
        return 1


def _snap(harga: str, *, detik: int = 0) -> Snapshot:
    saat = NOW + timedelta(seconds=detik)
    return Snapshot(
        market=Market.CRYPTO,
        symbol="BTC/USDT",
        captured_at=saat,
        last_price=Decimal(harga),
        provenance=Provenance(source="uji", server_timestamp=saat),
        volume_24h=Decimal("1000"),
        session="OPEN",
        market_open=True,
    )


def _ingestor(urutan: list[Snapshot]) -> tuple[MarketIngestor, _Store]:
    store = _Store()
    return (
        MarketIngestor(
            provider=_Provider(urutan),
            universe=_Universe(),
            store=store,
            settings=DataSettings(),
        ),
        store,
    )


class TestGerbangTerpasang:
    @pytest.mark.asyncio
    async def test_poll_berulang_dengan_harga_sama_berhenti_menulis(self) -> None:
        """Inilah 60.227 baris redundan itu, di jalur yang menghasilkannya.

        Dua baris, bukan satu, dan keduanya membawa keterangan: yang pertama
        karena tidak ada pembanding, yang kedua karena `QualityGate` menaikkan
        mutu dari OK ke DUPLICATE pada kutipan identik ketiga - umpan yang
        berhenti bergerak adalah peristiwa. Sesudah itu mutunya tetap DUPLICATE
        dan tidak ada lagi baris yang ditulis.
        """
        ingestor, store = _ingestor(
            [_snap("100", detik=d) for d in (0, 5, 10, 15, 20)]
        )

        for _ in range(5):
            await ingestor.poll_once()

        assert len(store.ditulis) == 2
        assert [s.quality.value for s in store.ditulis] == ["OK", "DUPLICATE"]

    @pytest.mark.asyncio
    async def test_umpan_yang_terus_mati_tidak_menulis_berulang(self) -> None:
        """Mutu yang berubah adalah peristiwa; mutu yang tetap buruk bukan.

        Tanpa test ini, umpan yang mati berhari-hari akan menulis satu baris per
        poll dengan alasan MUTU - persis kebocoran yang gerbang ini tutup.
        """
        ingestor, store = _ingestor(
            [_snap("100", detik=d) for d in range(0, 60, 5)]
        )

        for _ in range(12):
            await ingestor.poll_once()

        assert len(store.ditulis) == 2

    @pytest.mark.asyncio
    async def test_yang_dilewati_dihitung_bukan_dihilangkan(self) -> None:
        """Gerbang yang melewatkan nol baris dan gerbang yang tidak pernah
        dipanggil terlihat sama dari luar kalau yang dilewati tidak dicatat."""
        ingestor, _ = _ingestor([_snap("100"), _snap("100", detik=5)])

        await ingestor.poll_once()
        hasil = await ingestor.poll_once()

        assert hasil.dilewati == 1
        assert hasil.snapshots == 0

    @pytest.mark.asyncio
    async def test_harga_yang_bergerak_tetap_ditulis(self) -> None:
        """Batas atas optimasi: pasar yang bergerak tidak boleh kehilangan
        satu pun barisnya."""
        ingestor, store = _ingestor([_snap("100"), _snap("120", detik=5)])

        await ingestor.poll_once()
        await ingestor.poll_once()

        assert len(store.ditulis) == 2

    @pytest.mark.asyncio
    async def test_pembandingnya_yang_tersimpan_bukan_yang_terakhir_dilihat(
        self,
    ) -> None:
        """Kalau pembandingnya snapshot yang terakhir **dilihat**, harga bisa
        merambat 100 -> 100,1 -> 100,2 -> ... tanpa satu langkah pun melewati
        ambang, dan pergerakan besar hilang seluruhnya dari SQL.

        Dengan pembanding yang tersimpan, rambatan itu akhirnya melewati ambang
        terhadap 100 dan barisnya ditulis.
        """
        ingestor, store = _ingestor(
            [_snap(h, detik=d) for d, h in enumerate(
                ("100", "100.05", "100.10", "100.16"), start=0
            )]
        )

        for _ in range(4):
            await ingestor.poll_once()

        assert len(store.ditulis) == 2
        assert store.ditulis[1].last_price == Decimal("100.16")

    @pytest.mark.asyncio
    async def test_pasar_diam_tetap_meninggalkan_jejak(self) -> None:
        """Batas yang menahan seluruh optimasi ini.

        "Nol baris" karena pasar diam harus bisa dibedakan dari "nol baris"
        karena ARUNA berhenti melihat.

        Yang dipoll bukan dua kali melainkan seluruh rentangnya lima detik
        sekali, seperti produksi. Dua poll saja lolos bahkan kalau jamnya
        di-reset setiap kali sebuah baris dilewati - dan reset itu justru
        kegagalan yang membuat detak wajibnya tidak pernah berbunyi.
        """
        jeda = int(JEDA_WAJIB_DETIK)
        langkah = 5
        habis = jeda * 2
        ingestor, store = _ingestor(
            [_snap("100", detik=d) for d in range(0, habis, langkah)]
        )

        for _ in range(habis // langkah):
            await ingestor.poll_once()

        # Dua baris pertama punya sebabnya sendiri: PERTAMA, lalu MUTU saat
        # QualityGate menaikkan kutipan identik ke DUPLICATE. Baris ketiga ke
        # atas hanya bisa lahir dari jeda wajib.
        assert len(store.ditulis) >= 3, [s.captured_at for s in store.ditulis]
        assert (
            store.ditulis[2].captured_at - store.ditulis[1].captured_at
        ).total_seconds() >= JEDA_WAJIB_DETIK

    @pytest.mark.asyncio
    async def test_sebab_penyimpanan_dilaporkan(self) -> None:
        """Satu-satunya cara memeriksa gerbang ini di produksi adalah membaca
        sebabnya."""
        ingestor, _ = _ingestor([_snap("100"), _snap("120", detik=5)])

        await ingestor.poll_once()
        hasil = await ingestor.poll_once()

        assert hasil.sebab_simpan.get("HARGA") == 1
