"""Bahan belajar ARUNA datang dari keputusan HIDUP, bukan dari hasil spot.

**Cacat yang tidak bisa dilihat dari mana pun.** `agent_outcomes()` berlabuh di
`signal_snapshots` -> `signals` -> `paper_results`. Ketiganya berhenti tumbuh
ketika jalur spot dicabut (2026-08-25), tapi kuerinya tidak pernah gagal - ia
memulangkan baris beku yang sama selamanya. Jadi `build_reliability` menghitung
ulang angka yang identik tiap siklus, pengali agen tidak pernah lagi bergerak,
dan seluruh mesin perbaikan-diri berputar di tempat.

Tabelnya terisi, kuerinya sukses, loop-nya jalan. Yang mati cuma pertumbuhannya,
dan tidak ada satu pun galat yang menyebutnya. Terukur: dua snapshot
`agent_reliability` berurutan sama persis sampai empat desimal.

Sumber penggantinya sudah ada dan tumbuh tiap siklus - `council_sessions` dan
`judge_decisions` - dan gerak harga sesudahnya ada di `candles`. Yang dijaga di
sini bukan angkanya melainkan sifat yang membuat angkanya berarti.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.db.repositories.learning import LearningRepository, _gerak_satu_bar

SAAT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


class _DbPalsu:
    """Database yang MENGHITUNG kuerinya.

    Hitungan itu yang jadi pokok salah satu test: versi pertama menanyakan dua
    kueri per sesi dan tidak pernah selesai.
    """

    def __init__(self, sesi: list[dict], candles: list[dict]) -> None:
        self._sesi = sesi
        self._candles = candles
        self.kueri: list[str] = []

    async def fetch(self, sql: str, *args):
        self.kueri.append(sql)
        if "council_sessions" in sql:
            return list(self._sesi)
        if "FROM candles" in sql:
            return list(self._candles)
        return []


def _sesi(waktu: datetime, arah: str = "BUY", symbol: str = "BTC/USDT") -> dict:
    return {
        "id": 1,
        "market_code": "CRYPTO",
        "symbol": symbol,
        "interval_code": "1d",
        # MySQL memulangkan datetime tanpa zona; bentuknya harus ditiru, karena
        # justru di situ versi pertama meledak.
        "decided_at": waktu.replace(tzinfo=None),
        "council_decision": arah,
        "weights": '[{"role": "TECHNICAL", "decision": "BUY"},'
                   ' {"role": "VOLUME", "decision": "SELL"}]',
    }


def _candle(waktu: datetime, tutup: float, symbol: str = "BTC/USDT") -> dict:
    return {
        "market_code": "CRYPTO",
        "symbol": symbol,
        "interval_code": "1d",
        "close_time": waktu.replace(tzinfo=None),
        "close": tutup,
    }


class TestGerakSatuBar:
    def test_belum_ada_bar_berikutnya_berarti_belum_terukur(self) -> None:
        """**Bukan nol, dan bukan salah.** Keputusan yang bar berikutnya belum
        tutup adalah keputusan yang belum sempat terbukti. Menghitungnya
        sebagai kegagalan menghukum tiap keputusan terbaru justru karena ia
        terbaru - dan yang terbaru selalu yang paling banyak."""
        deret = [(SAAT - timedelta(days=1), 100.0)]

        assert _gerak_satu_bar(deret, SAAT) is None

    def test_deret_kosong_bukan_nol(self) -> None:
        assert _gerak_satu_bar([], SAAT) is None
        assert _gerak_satu_bar(None, SAAT) is None

    def test_keputusan_sebelum_bar_pertama_belum_terukur(self) -> None:
        """Tidak ada bar yang sudah tutup sebelum keputusan ini, jadi tidak ada
        harga acuan. Memakai bar SESUDAHNYA sebagai acuan akan membaca harga
        yang belum ada saat keputusan diambil."""
        deret = [(SAAT + timedelta(days=1), 100.0),
                 (SAAT + timedelta(days=2), 110.0)]

        assert _gerak_satu_bar(deret, SAAT) is None

    def test_naik_dan_turun_terbaca_dengan_tandanya(self) -> None:
        naik = [(SAAT - timedelta(days=1), 100.0), (SAAT + timedelta(days=1), 110.0)]
        turun = [(SAAT - timedelta(days=1), 100.0), (SAAT + timedelta(days=1), 90.0)]

        assert _gerak_satu_bar(naik, SAAT) == pytest.approx(10.0)
        assert _gerak_satu_bar(turun, SAAT) == pytest.approx(-10.0)

    def test_harga_acuan_nol_tidak_membagi_nol(self) -> None:
        deret = [(SAAT - timedelta(days=1), 0.0), (SAAT + timedelta(days=1), 5.0)]

        assert _gerak_satu_bar(deret, SAAT) is None


class TestSumbernyaKeputusanHidup:
    @pytest.mark.asyncio
    async def test_tidak_menyentuh_tabel_spot_yang_sudah_mati(self) -> None:
        """`signal_snapshots`, `signals`, `paper_results` berhenti tumbuh saat
        spot dicabut. Kuerinya tidak gagal - ia memulangkan baris beku."""
        db = _DbPalsu(
            [_sesi(SAAT - timedelta(days=2))],
            [_candle(SAAT - timedelta(days=3), 100.0),
             _candle(SAAT - timedelta(days=1), 110.0)],
        )

        await LearningRepository(db).agent_outcomes()  # type: ignore[arg-type]

        digabung = " ".join(db.kueri)
        for mati in ("paper_results", "signal_snapshots"):
            assert mati not in digabung, (
                f"masih berlabuh di {mati}, yang tidak tumbuh lagi"
            )
        assert "council_sessions" in digabung
        assert "judge_decisions" in digabung

    @pytest.mark.asyncio
    async def test_tiap_agen_dinilai_atas_yang_IA_dukung(self) -> None:
        """TECHNICAL bilang BUY, VOLUME bilang SELL, pasar naik. Keduanya tidak
        boleh mendapat nilai yang sama."""
        db = _DbPalsu(
            [_sesi(SAAT - timedelta(days=2), arah="BUY")],
            [_candle(SAAT - timedelta(days=3), 100.0),
             _candle(SAAT - timedelta(days=1), 110.0)],
        )

        rows = await LearningRepository(db).agent_outcomes()  # type: ignore[arg-type]

        per_agen = {r["agent"]: r for r in rows}
        assert per_agen["TECHNICAL"]["agent_decision"] == "BUY"
        assert per_agen["VOLUME"]["agent_decision"] == "SELL"
        # Council BUY dan pasar naik, jadi keputusannya benar. Yang membedakan
        # kedua agen ditangani `build_reliability` lewat agent_decision.
        assert all(r["direction_correct"] for r in rows)

    @pytest.mark.asyncio
    async def test_sesi_yang_belum_punya_bar_berikutnya_dilewati(self) -> None:
        db = _DbPalsu(
            [_sesi(SAAT)],
            [_candle(SAAT - timedelta(days=1), 100.0)],
        )

        rows = await LearningRepository(db).agent_outcomes()  # type: ignore[arg-type]

        assert rows == []

    @pytest.mark.asyncio
    async def test_pasar_turun_membuat_BUY_salah(self) -> None:
        """Pasangan arah, supaya test di atas tidak bisa lulus dengan selalu
        menjawab benar."""
        db = _DbPalsu(
            [_sesi(SAAT - timedelta(days=2), arah="BUY")],
            [_candle(SAAT - timedelta(days=3), 100.0),
             _candle(SAAT - timedelta(days=1), 90.0)],
        )

        rows = await LearningRepository(db).agent_outcomes()  # type: ignore[arg-type]

        assert rows
        assert not any(r["direction_correct"] for r in rows)


class TestBiayaTidakTumbuhBersamaSesi:
    """Pass ini berjalan di dalam loop upkeep, jadi biayanya bukan detail.

    **Versi pertama menanyakan dua kueri per sesi dan tidak pernah selesai** -
    enam ribu perjalanan bolak-balik, tiap satunya pemindaian tabel penuh
    karena `close_time` tidak punya indeks. Diukur sesudah dibatch: 16.863
    baris bahan belajar dalam 2,6 detik.

    Yang dikunci bukan detiknya - itu bergantung mesin - melainkan bentuknya:
    jumlah kueri tidak boleh tumbuh bersama jumlah sesi.
    """

    @pytest.mark.asyncio
    async def test_jumlah_kueri_tidak_ikut_bertambah(self) -> None:
        candles = [
            _candle(SAAT - timedelta(days=40) + timedelta(days=i), 100.0 + i)
            for i in range(40)
        ]

        sedikit = _DbPalsu([_sesi(SAAT - timedelta(days=20))], candles)
        banyak = _DbPalsu(
            [_sesi(SAAT - timedelta(days=20 - i % 10)) for i in range(200)],
            candles,
        )

        await LearningRepository(sedikit).agent_outcomes()  # type: ignore[arg-type]
        await LearningRepository(banyak).agent_outcomes()  # type: ignore[arg-type]

        assert len(banyak.kueri) == len(sedikit.kueri), (
            f"{len(sedikit.kueri)} kueri untuk 1 sesi tapi "
            f"{len(banyak.kueri)} untuk 200 - biayanya tumbuh bersama sesi"
        )
        assert len(banyak.kueri) <= 2
