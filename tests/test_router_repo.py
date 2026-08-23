"""Penyimpanan pilihan router (bagian 17.9, 17.27, 17.44, 17.52).

**Yang paling dijaga di sini: penolakan ikut tersimpan.** Nol karena tidak ada
strategi yang cocok dan nol karena fasenya mati terlihat sama persis dari luar -
yang pertama normal sementara yang kedua bug. Baris tanpa `alasan_kosong` tidak
bisa membedakan keduanya, dan laporan yang berdiri di atasnya tidak bisa
dibantah.

Dan penolakan akan **sering** terjadi. Diukur 2026-08-23 sebelum router menyala:
1.860 dari 9.437 bacaan 15m berlabel UNCERTAIN, 453 HIGH_VOLATILITY dan 49
ANOMALY tanpa strategi mana pun, ditambah tiap aset yang cuma punya satu horizon
segar - keyakinan tertingginya 48 sementara ambangnya 50.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aruna.core.enums import Market
from aruna.db.repositories.router import RouterRepository
from aruna.router.kecocokan import Kecocokan
from aruna.router.label import VERSI_ROUTER
from aruna.router.putusan import PutusanRouter
from aruna.router.rezim import PetaRezim

SAAT = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)


class _DbPalsu:
    """Bentuknya mengikuti `Database`, bukan mengikuti apa yang mudah ditulis.

    Cacat yang sudah berulang di proyek ini: test double yang bidangnya beda
    dari objek asli membuat suite hijau di atas bug produksi. `execute`
    memulangkan `int` (jumlah baris terpengaruh), sama seperti aslinya.
    """

    def __init__(self) -> None:
        self.sql: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql.append((sql, args))
        return 1


def _peta(
    primary: str | None = "TRENDING",
    keyakinan: float = 85.0,
    hilang: tuple[str, ...] = (),
) -> PetaRezim:
    return PetaRezim(primary, keyakinan, (), (), hilang)


def _terisi(kode: str = "STR-001") -> PutusanRouter:
    return PutusanRouter(
        champion=Kecocokan(kode, 88, ("rezim TRENDING cocok",), 900),
        challenger=Kecocokan("STR-004", 71, (), 900),
        alasan_kosong="",
        regime="TRENDING",
        alasan=("rezim TRENDING cocok",),
    )


def _kosong(sebab: str) -> PutusanRouter:
    return PutusanRouter(None, None, sebab, "UNCERTAIN")


async def _simpan(
    db: _DbPalsu,
    putusan: PutusanRouter,
    *,
    peta: PetaRezim | None = None,
    pada: datetime = SAAT,
    stabil: float | None = 80.0,
) -> int:
    return await RouterRepository(db).simpan(
        putusan,
        asset_id=7,
        market=Market.CRYPTO,
        symbol="BTC/USDT",
        peta=peta or _peta(),
        dipilih_pada=pada,
        stabilitas=stabil,
    )


class TestPenolakanIkutTersimpan:
    @pytest.mark.asyncio
    async def test_tanpa_champion_tetap_dicatat(self) -> None:
        db = _DbPalsu()
        await _simpan(db, _kosong("keyakinan rezim 41% di bawah ambang 50%"))

        sql, args = db.sql[0]

        assert "alasan_kosong" in sql
        assert any("41" in str(a) for a in args)

    @pytest.mark.asyncio
    async def test_sebabnya_tidak_dipotong_diam_diam(self) -> None:
        """Kolomnya VARCHAR(255). Alasan yang lebih panjang harus dipendekkan
        DI SINI dengan sengaja, bukan diserahkan kepada MySQL - yang dalam
        mode ketat menolak barisnya sama sekali, dan pilihannya hilang."""
        db = _DbPalsu()
        await _simpan(db, _kosong("x" * 400))

        _, args = db.sql[0]
        sebab = next(a for a in args if isinstance(a, str) and a.startswith("x"))

        assert len(sebab) <= 255

    @pytest.mark.asyncio
    async def test_champion_ada_berarti_alasan_kosong_null(self) -> None:
        """Dua kolom yang bisa terisi bersamaan adalah dua sumber kebenaran
        yang bisa bertentangan."""
        db = _DbPalsu()
        await _simpan(db, _terisi())

        _, args = db.sql[0]

        assert "STR-001" in args
        assert None in args


class TestTidakPernahDitulisUlang:
    @pytest.mark.asyncio
    async def test_hanya_insert_tidak_ada_update(self) -> None:
        """Bagian 17.27. Rezim berganti sesudah sebuah pilihan tercatat adalah
        hal biasa; mengubah catatannya membuat seluruh evaluasi Phase 12
        mengukur keputusan yang tidak pernah diambil siapa pun."""
        db = _DbPalsu()
        await _simpan(db, _terisi("STR-001"), pada=SAAT)
        await _simpan(db, _terisi("STR-004"), pada=SAAT + timedelta(minutes=15))

        perintah = [s.strip().split()[0].upper() for s, _ in db.sql]

        assert perintah == ["INSERT", "INSERT"]
        assert not any("UPDATE" in s.upper() for s, _ in db.sql)
        assert not any("ON DUPLICATE" in s.upper() for s, _ in db.sql)

    @pytest.mark.asyncio
    async def test_baris_kembar_ditolak_bukan_ditimpa(self) -> None:
        """``INSERT IGNORE``: siklus yang berjalan dua kali pada bar yang sama
        tidak menghasilkan dua baris, dan yang PERTAMA yang bertahan."""
        db = _DbPalsu()
        await _simpan(db, _terisi())

        sql, _ = db.sql[0]

        assert "IGNORE" in sql.upper()


class TestYangIkutTersimpan:
    @pytest.mark.asyncio
    async def test_versi_router_ikut(self) -> None:
        """Tanpa ini slice performa per rezim kembali melingkar - ia yang
        membedakan baris berlabel ROUTER dari baris turunan `classify()`."""
        db = _DbPalsu()
        await _simpan(db, _terisi())

        _, args = db.sql[0]

        assert VERSI_ROUTER in args

    @pytest.mark.asyncio
    async def test_interval_hilang_ikut_sebagai_teks(self) -> None:
        """Rezim yang disimpulkan dari satu horizon sementara tiga tersedia
        bukan kesimpulan yang sama kuatnya, dan pembaca baris lama tidak punya
        cara lain mengetahuinya."""
        db = _DbPalsu()
        await _simpan(db, _terisi(), peta=_peta(hilang=("1h", "1d")))

        _, args = db.sql[0]

        assert any(a == "1h,1d" for a in args)

    @pytest.mark.asyncio
    async def test_stabilitas_belum_terukur_disimpan_null(self) -> None:
        """``None`` berarti riwayatnya terlalu pendek, bukan "sangat tidak
        stabil". Menyimpannya sebagai nol akan membuat tiap aset yang baru
        dipantau terlihat berkedip terus."""
        db = _DbPalsu()
        await _simpan(db, _terisi(), stabil=None)

        _, args = db.sql[0]

        assert 0.0 not in args
        assert None in args

    @pytest.mark.asyncio
    async def test_jumlah_kolom_sama_dengan_jumlah_nilai(self) -> None:
        """**Yang tidak bisa ditangkap test double.** `_DbPalsu` menerima
        argumen apa pun tanpa mengeluh; MySQL tidak. Kolom yang ditambahkan
        tanpa `%s` pasangannya - atau sebaliknya - baru meledak di produksi,
        pada fase yang kegagalannya sengaja ditelan supaya siklus tetap jalan.

        Diuji juga sekali terhadap MySQL sungguhan 2026-08-23: dua baris masuk,
        yang menolak menyimpan `alasan_kosong` terpotong tepat 255 dan
        `regime_stability` NULL bukan nol.
        """
        db = _DbPalsu()
        await _simpan(db, _terisi())

        sql, args = db.sql[0]
        kolom = sql[sql.index("(") + 1 : sql.index(")")].split(",")

        assert len(kolom) == sql.count("%s") == len(args)

    @pytest.mark.asyncio
    async def test_waktunya_dari_bar_bukan_jam_sistem(self) -> None:
        """`dipilih_pada` dioper, tidak dibaca dari jam di dalam repositori.
        Yang kedua membuat pilihan tidak bisa diuji ulang dan membuat replay
        Phase 9 menulis stempel hari ini pada keputusan tahun lalu."""
        db = _DbPalsu()
        await _simpan(db, _terisi(), pada=SAAT)

        _, args = db.sql[0]

        assert any("2026-08-23 10:00" in str(a) for a in args)
