"""`market_snapshots.raw` ditulis ratusan ribu kali dan tidak pernah dibaca.

Terukur 2026-08-21 di basis data produksi: `market_snapshots` berisi 419.352
baris dan 286 MB - 62% dari seluruh database. Kolom `raw` sendirian
rata-rata **513 karakter per baris**, yaitu sekitar **215 MB**, atau 42% dari
seluruh database.

Kolom itu tidak muncul di satu pun `SELECT`. `latest_snapshot` dan
`latest_snapshots` keduanya mengeja kolomnya satu per satu, dan `raw` tidak ada
di antaranya; pencarian ke seluruh `src/` hanya menemukannya di satu tempat,
yaitu daftar kolom `INSERT` di bawah ini.

Bagian 16 spec: SQL adalah ingatan jangka panjang, bukan pita rekaman dari
setiap hal yang ARUNA lihat.

Ini mengikuti preseden yang sudah ada di berkas yang sama - `market_ticks`
dibuang dengan alasan yang sama, dan catatannya masih tertulis di
`market_data.py`.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from aruna.core.enums import Market
from aruna.data.models import Provenance, Snapshot

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _snapshot(**kwargs: Any) -> Snapshot:
    """`Snapshot` yang sungguhan, bukan palsu berbentuk karangan.

    Palsu yang bidangnya berbeda dari objek asli sudah dua kali membuat suite
    proyek ini hijau di atas bug produksi.
    """
    base: dict[str, Any] = {
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "captured_at": NOW,
        "last_price": Decimal("100"),
        "provenance": Provenance(source="test", server_timestamp=NOW),
        "raw": {"payload": "x" * 500},
    }
    return Snapshot(**(base | kwargs))


class _DBPerekam:
    def __init__(self) -> None:
        self.sql = ""
        self.args: tuple[Any, ...] = ()

    async def insert(self, sql: str, *args: Any) -> int:
        self.sql = sql
        self.args = args
        return 1


class TestRawTidakDitulis:
    @pytest.mark.asyncio
    async def test_insert_tidak_menyebut_kolom_raw(self) -> None:
        from aruna.db.repositories.market_data import MarketDataRepository

        db = _DBPerekam()
        await MarketDataRepository(db).record_snapshot(1, _snapshot())

        assert "raw" not in db.sql.lower()

    @pytest.mark.asyncio
    async def test_muatan_raw_tidak_ikut_sebagai_nilai(self) -> None:
        """Membuang nama kolomnya tapi meninggalkan nilainya akan menggeser
        seluruh parameter satu posisi - kegagalan yang jauh lebih buruk
        daripada kolom gemuk, karena diam-diam menaruh harga di kolom volume."""
        db = _DBPerekam()
        from aruna.db.repositories.market_data import MarketDataRepository

        await MarketDataRepository(db).record_snapshot(1, _snapshot())

        assert not any("x" * 100 in str(a) for a in db.args)

    @pytest.mark.asyncio
    async def test_jumlah_placeholder_sama_dengan_jumlah_nilai(self) -> None:
        """Penjaga langsung terhadap pergeseran parameter itu."""
        db = _DBPerekam()
        from aruna.db.repositories.market_data import MarketDataRepository

        await MarketDataRepository(db).record_snapshot(1, _snapshot())

        # `asset_id` ikut sebagai argumen pertama sekaligus placeholder pertama.
        assert db.sql.count("%s") == len(db.args)

    @pytest.mark.asyncio
    async def test_kolom_yang_masih_dibaca_tetap_ditulis(self) -> None:
        """Bagian 2 spec: JANGAN MENGHAPUS KOLOM YANG MASIH DIGUNAKAN CODE.

        Ketiga pembaca `market_snapshots` membaca kolom-kolom ini; kehilangan
        salah satunya membuat permukaan pasar dan Telegram diam-diam kosong.
        """
        db = _DBPerekam()
        from aruna.db.repositories.market_data import MarketDataRepository

        await MarketDataRepository(db).record_snapshot(1, _snapshot())

        for kolom in (
            "last_price", "bid", "ask", "spread_bps", "volume_24h",
            "change_24h_pct", "session_code", "market_open", "quality",
            "quality_detail", "captured_at", "is_realtime", "source",
        ):
            assert kolom in db.sql, kolom


class TestTidakAdaPembaca:
    def test_tidak_ada_select_yang_membaca_raw(self) -> None:
        """Penjaga terhadap pembaca yang muncul kemudian.

        Kalau suatu saat ada yang menulis `SELECT ... raw ... FROM
        market_snapshots`, test inilah yang memberitahu bahwa kolomnya sudah
        tidak diisi lagi - bukan `None` yang muncul diam-diam di produksi.
        """
        from aruna.db.repositories import market_data

        pohon = ast.parse(inspect.getsource(market_data))
        for simpul in ast.walk(pohon):
            if not isinstance(simpul, ast.Constant):
                continue
            teks = simpul.value
            if not isinstance(teks, str) or "SELECT" not in teks.upper():
                continue
            kolom = {k.strip().strip("`") for k in teks.replace("\n", " ").split(",")}
            assert "raw" not in kolom, f"ada SELECT yang membaca `raw`: {teks[:80]}"
