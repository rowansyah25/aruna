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
from aruna.db.repositories.router import RIWAYAT_STABILITAS, RouterRepository
from aruna.router.kecocokan import Kecocokan
from aruna.router.label import VERSI_ROUTER
from aruna.router.putusan import AlasanKosong, PutusanRouter
from aruna.router.rezim import PetaRezim

SAAT = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)


class _DbPalsu:
    """Bentuknya mengikuti `Database`, bukan mengikuti apa yang mudah ditulis.

    Cacat yang sudah berulang di proyek ini: test double yang bidangnya beda
    dari objek asli membuat suite hijau di atas bug produksi. `execute`
    memulangkan `int` (jumlah baris terpengaruh) dan `fetch` memulangkan
    `list[dict]`, sama seperti aslinya.
    """

    def __init__(self, baris: list[dict[str, Any]] | None = None) -> None:
        self.sql: list[tuple[str, tuple[Any, ...]]] = []
        self._baris = baris or []

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql.append((sql, args))
        return 1

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql.append((sql, args))
        return list(self._baris)


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
        kode_kosong=None,
        regime="TRENDING",
        alasan=("rezim TRENDING cocok",),
    )


def _kosong(sebab: str) -> PutusanRouter:
    return PutusanRouter(
        None, None, sebab, AlasanKosong.KEYAKINAN_KURANG, "UNCERTAIN"
    )


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


def _kolom(db: _DbPalsu, nama: str) -> Any:
    """Nilai yang dikirim untuk satu kolom, dicari MENURUT NAMANYA.

    **Koreksi 2026-08-24.** Versi pertama test stabilitas berbunyi
    ``assert 0.0 not in args`` - melarang nol di mana pun dalam argumen. Itu
    lolos selama tidak ada kolom lain yang sah bernilai nol, lalu MERAH begitu
    `konsensus` lahir dengan bawaan 0,0.

    Assertion yang menjaring seluruh tuple bukan cuma rapuh; ia juga tidak
    menyebutkan kolom mana yang sedang diuji, jadi kegagalannya tidak
    memberitahu apa pun.
    """
    sql, args = db.sql[0]
    kolom = [k.strip() for k in sql[sql.index("(") + 1 : sql.index(")")].split(",")]
    return args[kolom.index(nama)]


def _snap(
    interval: str, regime: str, *, umur: timedelta = timedelta(), simbol: str = "BTC/USDT"
) -> dict[str, Any]:
    return {
        "symbol": simbol,
        "horizon_code": interval,
        "regime": regime,
        "locked_at": SAAT - umur,
    }


class TestMembacaRezimPerHorizon:
    """Batas umurnya per horizon, bukan satu angka untuk semuanya."""

    @pytest.mark.asyncio
    async def test_tiga_horizon_jadi_tiga_bacaan(self) -> None:
        """**Premis yang diukur, bukan diasumsikan.** Hanya 23,0% pasangan
        15m/1h yang sesaat membawa rezim yang sama - terukur 2026-08-23 atas
        3.438 pasangan. Contohnya, aset 5 pada 2026-08-22 18:48: 15m BREAKOUT,
        1h HIGH_VOLATILITY, 1d ANOMALY.

        Tiga bacaan yang benar-benar berbeda di detik yang sama. Kalau bukan
        begitu, peta multi-timeframe cuma satu bacaan yang dilabeli tiga kali
        dan seluruh bagian 17.8 tidak punya isi.
        """
        db = _DbPalsu([
            _snap("15m", "BREAKOUT"),
            _snap("1h", "HIGH_VOLATILITY"),
            _snap("1d", "ANOMALY"),
        ])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert {b.interval: b.regime for b in peta["BTC/USDT"]} == {
            "15m": "BREAKOUT", "1h": "HIGH_VOLATILITY", "1d": "ANOMALY",
        }

    @pytest.mark.asyncio
    async def test_umur_maksimum_berbeda_per_horizon(self) -> None:
        """**Satu batas untuk semuanya membunuh horizon panjang.** Sebuah
        bacaan berumur enam jam basi untuk 15m dan masih baru untuk 1d - dan
        memaksakan satu angka berarti membuang seluruh horizon panjang, lalu
        dengan itu membuang seluruh alasan bagian 17.8 ada.

        Terukur 2026-08-23: bacaan 1h TERMUDA di seluruh dua puluh aset pun
        berumur 5,5 jam, karena `signal_snapshots` cuma dapat baris ketika
        sinyal terkunci. Batas satu jam akan membuang 1h dan 1d selalu, dan
        15m sendirian berkeyakinan 20 - jauh di bawah ambang 50.
        """
        db = _DbPalsu([
            _snap("15m", "BREAKOUT", umur=timedelta(hours=6)),
            _snap("1d", "TRENDING", umur=timedelta(hours=6)),
        ])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert [b.interval for b in peta["BTC/USDT"]] == ["1d"]

    @pytest.mark.asyncio
    async def test_uncertain_dibuang_bukan_dipakai(self) -> None:
        db = _DbPalsu([_snap("15m", "UNCERTAIN"), _snap("1h", "RANGING")])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert [b.regime for b in peta["BTC/USDT"]] == ["RANGING"]

    @pytest.mark.asyncio
    async def test_yang_terbaru_per_horizon_yang_dipakai(self) -> None:
        """Barisnya datang terurut menurun; yang kedua dan seterusnya riwayat,
        dan riwayat urusan `stabilitas`."""
        db = _DbPalsu([
            _snap("15m", "BREAKOUT"),
            _snap("15m", "RANGING", umur=timedelta(minutes=30)),
        ])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert [b.regime for b in peta["BTC/USDT"]] == ["BREAKOUT"]

    @pytest.mark.asyncio
    async def test_satu_kueri_bukan_satu_per_simbol(self) -> None:
        """Fase ini berjalan tiap siklus atas dua puluh aset. Satu kueri bisa
        diterima, dua puluh tidak."""
        db = _DbPalsu([
            _snap("15m", "BREAKOUT", simbol="BTC/USDT"),
            _snap("15m", "RANGING", simbol="ETH/USDT"),
        ])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert len(db.sql) == 1
        assert set(peta) == {"BTC/USDT", "ETH/USDT"}

    @pytest.mark.asyncio
    async def test_simbol_tanpa_bacaan_segar_tidak_muncul(self) -> None:
        """Bukan muncul dengan peta kosong. `susun_peta(())` memulangkan
        primary `None`, dan itu keadaan yang sah - tapi simbol yang seluruh
        bacaannya basi tidak perlu diarak melewati mesin peringkat dulu."""
        db = _DbPalsu([_snap("15m", "BREAKOUT", umur=timedelta(days=3))])
        peta = await RouterRepository(db).peta_rezim(sekarang=SAAT)

        assert peta == {}


class TestRiwayatUntukStabilitas:
    @pytest.mark.asyncio
    async def test_hanya_15m(self) -> None:
        db = _DbPalsu([{"symbol": "BTC/USDT", "regime": "TRENDING"}])
        await RouterRepository(db).riwayat_15m(sekarang=SAAT)

        _, args = db.sql[0]

        assert "15m" in args

    @pytest.mark.asyncio
    async def test_dipotong_pada_batas(self) -> None:
        db = _DbPalsu([{"symbol": "BTC/USDT", "regime": "TRENDING"}] * 40)
        riwayat = await RouterRepository(db).riwayat_15m(sekarang=SAAT, batas=8)

        assert len(riwayat["BTC/USDT"]) == 8

    @pytest.mark.asyncio
    async def test_uncertain_dibuang_supaya_riwayatnya_merapat(self) -> None:
        """**Dibuang, bukan dihitung sebagai perpindahan.** "RANGING -> tidak
        tahu -> RANGING" bukan dua perpindahan rezim; itu alat ukurnya yang
        sesaat kehilangan pijakan. Menghitungnya membuat stabilitas terbaca
        nol pada pasar yang justru diam."""
        db = _DbPalsu([
            {"symbol": "BTC/USDT", "regime": r}
            for r in ("RANGING", "UNCERTAIN", "RANGING")
        ])
        riwayat = await RouterRepository(db).riwayat_15m(sekarang=SAAT)

        assert riwayat["BTC/USDT"] == ("RANGING", "RANGING")

    def test_riwayatnya_cukup_panjang_untuk_berarti(self) -> None:
        """**Bukan `BACAAN_REGIME` (tiga) dari Phase 16.** Pertanyaannya
        berbeda: yang tiga menjawab "apakah rezimnya baru saja berganti dari
        keadaan mapan", yang ini "seberapa sering ia berganti akhir-akhir ini".

        Tiga bacaan memberi dua pasangan, dan stabilitas dari dua pasangan cuma
        punya tiga nilai mungkin - 0, 50, 100 - terlalu kasar untuk
        menskalakan apa pun.
        """
        from aruna.db.repositories.konteks_pemicu import BACAAN_REGIME

        assert RIWAYAT_STABILITAS > BACAAN_REGIME
        assert RIWAYAT_STABILITAS - 1 >= 7


class TestPenolakanIkutTersimpan:
    @pytest.mark.asyncio
    async def test_tanpa_champion_tetap_dicatat(self) -> None:
        db = _DbPalsu()
        await _simpan(db, _kosong("keyakinan rezim 41% di bawah ambang 50%"))

        sql, args = db.sql[0]

        assert "alasan_kosong" in sql
        assert any("41" in str(a) for a in args)

    @pytest.mark.asyncio
    async def test_sebabnya_ikut_dalam_bentuk_yang_bisa_dihitung(self) -> None:
        """**Kalimatnya saja tidak cukup, dan itu terbukti sebelum dikomit.**
        Kalimat penolakan menyebut angkanya - "keyakinan rezim 20%",
        "keyakinan rezim 32%" - jadi mengelompokkan darinya membuat tiap
        penolakan jadi kelompoknya sendiri.

        Pertanyaan "berapa sering router menolak, dan kenapa" tidak boleh
        dijawab dengan LIKE '%keyakinan%'.
        """
        db = _DbPalsu()
        await _simpan(db, _kosong("keyakinan rezim 20% di bawah ambang 50%"))

        sql, args = db.sql[0]

        assert "kode_kosong" in sql
        assert AlasanKosong.KEYAKINAN_KURANG in args

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

        assert _kolom(db, "interval_hilang") == "1h,1d"

    @pytest.mark.asyncio
    async def test_konsensus_dan_jumlah_kandidat_ikut(self) -> None:
        """Bagian 17.31 - 17.32. Kolom, bukan kalimat di `alasan`, justru
        supaya "apakah pilihan yang terbelah berakhir lebih buruk" bisa
        ditanyakan kepada data alih-alih ditebak."""
        db = _DbPalsu()
        await _simpan(db, _terisi())

        assert _kolom(db, "konsensus") is not None
        assert _kolom(db, "kandidat_layak") is not None

    @pytest.mark.asyncio
    async def test_stabilitas_belum_terukur_disimpan_null(self) -> None:
        """``None`` berarti riwayatnya terlalu pendek, bukan "sangat tidak
        stabil". Menyimpannya sebagai nol akan membuat tiap aset yang baru
        dipantau terlihat berkedip terus."""
        db = _DbPalsu()
        await _simpan(db, _terisi(), stabil=None)

        assert _kolom(db, "regime_stability") is None

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
