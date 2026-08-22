"""PASAL 15.39: ingatan tidak boleh melihat masa depan.

Ini satu-satunya test di Phase 15 yang kegagalannya merusak seluruh nilai
fasenya. Sebuah memory engine yang boleh membaca hasil yang belum terjadi akan
melaporkan akurasi tinggi pada backtest mana pun - dan angkanya naik justru
ketika kebocorannya makin parah. Tidak ada satu pun log yang akan menyebutnya,
karena secara teknis tidak ada yang gagal.

Yang dijaga: pencarian terikat ``as_of``, dan ingatan yang resolusinya terjadi
SESUDAH ``as_of`` tidak boleh muncul - meskipun ia sudah ada di tabel saat
pencarian dijalankan hari ini.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


class _DBPalsu:
    """Meniru ``Database``: mencatat SQL dan argumennya apa adanya.

    Bentuknya mengikuti yang sungguhan - ``fetch`` memulangkan daftar dict,
    ``execute`` memulangkan jumlah baris - supaya palsu ini tidak meloloskan
    pemanggilan yang akan gagal di produksi.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.sql = ""
        self.args: tuple = ()

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql = sql
        self.args = args
        return self.rows

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql = sql
        self.args = args
        return 1


class TestPencarianTerikatWaktu:
    @pytest.mark.asyncio
    async def test_kueri_menyaring_resolved_at(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).cari(
            as_of=NOW, market="CRYPTO", timeframe="15m"
        )

        assert "resolved_at < %s" in db.sql
        assert "resolved_at IS NOT NULL" in db.sql

    @pytest.mark.asyncio
    async def test_as_of_ikut_sebagai_argumen(self) -> None:
        """Kueri yang menyebut ``resolved_at < %s`` tapi tidak pernah mengoper
        ``as_of`` menyaring terhadap nilai lain - dan tetap lolos test yang
        cuma memeriksa teks SQL-nya. Dua cara berbeda kebocoran ini bisa
        lolos, jadi dua test."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).cari(
            as_of=NOW, market="CRYPTO", timeframe="15m"
        )

        assert any("2026-08-21" in str(a) for a in db.args)

    @pytest.mark.asyncio
    async def test_tanpa_as_of_ditolak(self) -> None:
        """Bawaan ``as_of=None`` yang berarti "sekarang" adalah bawaan yang
        akan dipakai pemanggil backtest tanpa sadar (PASAL 15.40), dan
        kebocoran itu tidak meninggalkan jejak apa pun."""
        from aruna.db.repositories.memory import MemoryRepository

        with pytest.raises(TypeError):
            await MemoryRepository(_DBPalsu()).cari(  # type: ignore[call-arg]
                market="CRYPTO", timeframe="15m"
            )

    @pytest.mark.asyncio
    async def test_pasar_dan_timeframe_ikut_menyaring(self) -> None:
        """Tanpa keduanya, ingatan IDX harian akan muncul sebagai "kondisi
        serupa" untuk rencana CRYPTO 15 menit."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).cari(
            as_of=NOW, market="CRYPTO", timeframe="15m"
        )

        assert "market_code = %s" in db.sql
        assert "timeframe = %s" in db.sql
        assert "CRYPTO" in db.args
        assert "15m" in db.args


class TestPerkayaanTeknikal:
    """Lima dimensi teknikal dihitung ulang dari candle - dan **hanya dari bar
    yang sudah tutup sebelum keputusan itu dibuat**.

    Ini pintu kebocoran yang paling halus di seluruh Phase 15: bar yang tutup
    SESUDAH keputusan berisi persis jawaban yang sedang dicari, dan volatilitas
    yang dihitung dengan bar itu akan tampak menerangkan hasil dengan sangat
    baik. Tidak ada log yang akan menyebutnya.
    """

    @pytest.mark.asyncio
    async def test_kandil_disaring_sampai_waktu_keputusan(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        class _DB:
            def __init__(self) -> None:
                self.sql: list[str] = []
                self.args: list[tuple] = []

            async def fetch(self, sql: str, *a: Any) -> list[dict[str, Any]]:
                self.sql.append(sql)
                self.args.append(a)
                return []

            async def execute(self, sql: str, *a: Any) -> int:
                return 1

        db = _DB()
        await MemoryRepository(db).kandil_sampai(
            symbol="BTC/USDT", timeframe="15m", sampai=NOW, limit=200
        )

        gabung = " ".join(db.sql)
        assert "close_time <= %s" in gabung or "close_time < %s" in gabung
        assert any("2026-08-21" in str(x) for a in db.args for x in a)

    @pytest.mark.asyncio
    async def test_hanya_bar_yang_sudah_tutup(self) -> None:
        """SPEC 24: bar yang belum settle adalah harga yang belum jadi, dan
        indikator yang membacanya membaca masa depan yang belum ada."""
        from aruna.db.repositories.memory import MemoryRepository

        class _DB:
            def __init__(self) -> None:
                self.sql = ""

            async def fetch(self, sql: str, *a: Any) -> list[dict[str, Any]]:
                self.sql = sql
                return []

        db = _DB()
        await MemoryRepository(db).kandil_sampai(
            symbol="BTC/USDT", timeframe="15m", sampai=NOW, limit=200
        )

        assert "is_closed" in db.sql


class TestTidakAdaPemotonganDiamDiam:
    """PASAL 15.9 menuntut jumlah sampel dilaporkan. Sampel yang dipotong tanpa
    diberitahukan terbaca persis seperti sampel yang utuh."""

    @pytest.mark.asyncio
    async def test_kandidat_yang_terpotong_dilaporkan(self) -> None:
        """Terukur 2026-08-21: pencarian sungguhan memulangkan **tepat 5.000**
        baris - yaitu batas yang dioper. Batas yang persis tercapai berarti
        ingatan lain terpotong, dan karena urutannya ``resolved_at DESC``, yang
        terpotong selalu yang paling lama.

        Dua kesalahan sekaligus: jumlah sampel yang dilaporkan jadi salah, dan
        pemotongannya menambahkan bias kebaruan yang tidak pernah diputuskan
        siapa pun.
        """
        from aruna.db.repositories.memory import MemoryRepository

        penuh = [{"signal_id": f"m{i:015d}"} for i in range(3)]
        repo = MemoryRepository(_DBPalsu(penuh))

        rows, terpotong = await repo.cari_terhitung(
            as_of=NOW, market="CRYPTO", timeframe="15m", limit=3
        )

        assert len(rows) == 3
        assert terpotong is True

    @pytest.mark.asyncio
    async def test_yang_tidak_terpotong_dinyatakan_utuh(self) -> None:
        """Penjaga terhadap test di atas: penanda yang selalu True tidak
        memberitahu apa pun."""
        from aruna.db.repositories.memory import MemoryRepository

        repo = MemoryRepository(_DBPalsu([{"signal_id": "m1"}]))

        _, terpotong = await repo.cari_terhitung(
            as_of=NOW, market="CRYPTO", timeframe="15m", limit=100
        )

        assert terpotong is False


class TestHitungPerTimeframe:
    """Pemilihan horizon butuh tahu timeframe mana yang punya cukup ingatan -
    dan itu satu kueri per tick, bukan satu per simbol."""

    @pytest.mark.asyncio
    async def test_memulangkan_peta_timeframe(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([
            {"timeframe": "15m", "n": 5377},
            {"timeframe": "1h", "n": 2189},
        ])

        peta = await MemoryRepository(db).hitung_per_timeframe(
            as_of=NOW, market="CRYPTO"
        )

        assert peta == {"15m": 5377, "1h": 2189}

    @pytest.mark.asyncio
    async def test_hanya_menghitung_yang_bisa_mengajari(self) -> None:
        """Terukur 2026-08-21 saat proyektor futures hendak dibangun: dari 182
        hasil futures, **165 EXPIRED** - bukan menang, bukan kalah. Hanya 17
        yang bisa menyumbang win rate.

        Penghitung yang menghitung seluruh baris akan melihat "182 ingatan 4h",
        melewati ambang dua puluh, dan meninggalkan korpus 1h yang punya 2.189
        dengan hasil sungguhan - berpindah ke timeframe yang lebih tepat dan
        nyaris tidak bisa mengatakan apa pun.
        """
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([])
        await MemoryRepository(db).hitung_per_timeframe(as_of=NOW, market="CRYPTO")

        assert "hasil IN" in db.sql
        assert "WIN" in db.args
        assert "LOSS" in db.args

    @pytest.mark.asyncio
    async def test_juga_terikat_as_of(self) -> None:
        """Hitungan yang memasukkan ingatan masa depan akan memilih timeframe
        yang belum punya apa-apa pada saat keputusan itu dibuat - kebocoran
        PASAL 15.39 lewat pintu belakang."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu([])
        await MemoryRepository(db).hitung_per_timeframe(as_of=NOW, market="CRYPTO")

        assert "resolved_at < %s" in db.sql
        assert any("2026-08-21" in str(a) for a in db.args)


class TestProyeksiTidakMengarang:
    @pytest.mark.asyncio
    async def test_hanya_yang_punya_outcome_final(self) -> None:
        """Ingatan tanpa hasil final tidak bisa mengajari apa pun tentang
        hasil, dan memproyeksikannya sekarang berarti barisnya harus disunting
        nanti - yang PASAL 15.25 larang."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).proyeksikan(sampai=NOW, limit=100)

        assert "is_final" in db.sql

    @pytest.mark.asyncio
    async def test_proyeksi_juga_terikat_waktu(self) -> None:
        """Backtest yang memproyeksikan seluruh sejarah lebih dulu, lalu
        mencari dengan `as_of` yang benar, tetap bocor lewat pintu ini kalau
        proyeksinya sendiri tidak dibatasi."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).proyeksikan(sampai=NOW, limit=100)

        assert any("2026-08-21" in str(a) for a in db.args)

    @pytest.mark.asyncio
    async def test_waktu_tanpa_zona_dari_mysql_diterima(self) -> None:
        """Ditemukan lintasan pertama terhadap data produksi 2026-08-21, dan
        tidak akan ditemukan test mana pun yang memakai waktu buatan sendiri.

        Kolom ``DATETIME`` MySQL tidak membawa zona waktu, jadi driver
        memulangkannya **naif** - sementara ``to_mysql_datetime`` menolak yang
        naif dengan sengaja, supaya urutan antar provider tidak pernah ambigu.
        Proyeksi karena itu harus memasang UTC di batas repositori, sama
        seperti pembaca lain di lapisan ini (``as_utc``).
        """
        from aruna.db.repositories.memory import MemoryRepository

        naif = datetime(2026, 8, 20, 10, 0)
        db = _DBPalsu([{
            "signal_id": "b6fad072584e423f",
            "symbol": "BTC/USDT",
            "market_code": "CRYPTO",
            "horizon_code": "15m",
            "regime": "TRENDING",
            "risk_level": "MODERATE",
            "news_state": "1 item(s): 0+ / 0- / 1 unreadable",
            "signal_quality": 57,
            "spread_bps": None,
            "direction": "BUY",
            "model_version": "1.0.0+phase10",
            "locked_at": naif,
            "move_pct": None,
            "favourable": 1,
            "resolved_at": naif,
        }])

        tersisip = await MemoryRepository(db).proyeksikan(sampai=NOW, limit=1)

        assert tersisip == 1
        assert "INSERT IGNORE" in db.sql.upper()

    @pytest.mark.asyncio
    async def test_move_pct_dipendekkan_sebelum_disimpan(self) -> None:
        """Terukur pada lintasan pertama 2026-08-21: MySQL memperingatkan
        ``Data truncated for column 'move_pct'`` berkali-kali.
        ``outcome_snapshots.move_pct`` menyimpan enam desimal
        (``-0.079282``) dan kolom ingatan ``DECIMAL(12,4)``.

        Nilainya tidak rusak - ia dibulatkan - tapi peringatannya nyata, dan
        ``Data truncated`` adalah tepat yang §26 dan daftar periksa Phase 14
        tuntut nol. Kelas yang sama dengan tiga kolom DECIMAL yang pernah
        terpotong diam-diam di proyek ini: yang dibulatkan database tidak
        pernah diketahui siapa pun yang membaca kodenya.
        """
        from decimal import Decimal

        from aruna.db.repositories.memory import MemoryRepository

        naif = datetime(2026, 8, 20, 10, 0)
        db = _DBPalsu([{
            "signal_id": "b6fad072584e423f",
            "symbol": "BTC/USDT",
            "market_code": "CRYPTO",
            "horizon_code": "15m",
            "regime": "TRENDING",
            "risk_level": "MODERATE",
            "news_state": "NO_RECENT_NEWS",
            "signal_quality": 57,
            "spread_bps": None,
            "direction": "BUY",
            "model_version": "1.0.0+phase10",
            "locked_at": naif,
            "move_pct": Decimal("-0.079282"),
            "favourable": 0,
            "resolved_at": naif,
        }])

        await MemoryRepository(db).proyeksikan(sampai=NOW, limit=1)

        tersimpan = next(
            a for a in db.args if isinstance(a, Decimal)
        )
        assert tersimpan == Decimal("-0.0793")
        assert -tersimpan.as_tuple().exponent <= 4

    @pytest.mark.asyncio
    async def test_menyimpan_memakai_insert_ignore(self) -> None:
        """PASAL 15.26 ditegakkan database, bukan niat: pengulangan proyeksi
        tidak boleh melahirkan ingatan kedua untuk peristiwa yang sama."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).simpan({
            "signal_id": "b6fad072584e423f",
            "market_code": "CRYPTO",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "regime": "TRENDING",
            "risk_level": "MODERATE",
            "news": "NEUTRAL",
            "quality_band": "MEDIUM",
            "liquidity_band": "TIGHT",
            "arah": "BUY",
            "hasil": "WIN",
            "move_pct": None,
            "cakupan": 75,
            "mutu": "HIGH",
            "model_version": "1.0.0+phase10",
            "locked_at": NOW,
            "resolved_at": NOW,
        })

        assert "INSERT IGNORE" in db.sql.upper()
