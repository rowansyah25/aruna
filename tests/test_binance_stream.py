"""Aliran WebSocket spot (PASAL 2, 3, 4, 9, 10, 26).

Yang diuji di sini bukan "apakah bisa tersambung" - itu terbukti sendiri di
produksi. Yang diuji adalah perilaku saat GAGAL, karena itulah yang tidak
pernah terlihat sampai ia terjadi pada jam tiga pagi: putus, menggantung,
pesan cacat, dan harga lama yang masih dipegang setelah aliran mati.

Semua tanpa jaringan dan tanpa menunggu waktu nyata. Perilaku sambung-ulang
yang hanya bisa diuji dengan mencabut kabel adalah perilaku yang tidak pernah
diuji.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.data.crypto.stream import (
    PING_INTERVAL_SEC,
    PING_TIMEOUT_SEC,
    RECONNECT_MAX_SEC,
    RECONNECT_MIN_SEC,
    SILENT_TIMEOUT_SEC,
    BinanceSpotStream,
    StreamedQuote,
)

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
SYMBOLS = ("BTC/USDT", "ETH/USDT")


def book(symbol: str = "BTCUSDT", bid: str = "64000.10", ask: str = "64000.20") -> str:
    return json.dumps(
        {"stream": f"{symbol.lower()}@bookTicker",
         "data": {"s": symbol, "b": bid, "a": ask, "E": 1786956000000}}
    )


class _Socket:
    """Satu sesi WebSocket. ``script`` habis -> sesi berakhir."""

    def __init__(self, script: list[str | Exception], hang: bool = False) -> None:
        self._script = list(script)
        self._hang = hang
        self.closed = False

    async def recv(self) -> str:
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self._hang:
            await asyncio.sleep(3600)  # menggantung: tersambung, tidak bicara
        raise ConnectionError("venue closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        self.closed = True


class _Connector:
    """Menggantikan websockets.connect. Mencatat setiap percobaan."""

    def __init__(self, sessions: list[_Socket | Exception]) -> None:
        self._sessions = list(sessions)
        self.urls: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, url: str, **kw):
        self.urls.append(url)
        self.kwargs.append(kw)
        if not self._sessions:
            return _Socket([], hang=True)
        item = self._sessions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestKeepalivePustaka:
    """Pemeriksa kedua, dan urutannya yang penting.

    Yang mendeteksi aliran menggantung adalah `SILENT_TIMEOUT_SEC` milik ARUNA,
    dengan alasan yang tertulis di modulnya. Keepalive pustaka `websockets`
    memeriksa hal yang sama dengan ambang bawaan dua puluh detik.

    Terukur 2026-08-22 pada mesin ini - dua core fisik, enam proses Python,
    MySQL: fase `upkeep.manfaat` menyapu korpus ingatan selama 154 detik dengan
    Python murni, dan tujuh `keepalive ping timeout` terjadi seluruhnya di dalam
    jendela itu. Melepas GIL tiap lima puluh target tidak menghapusnya; tidur
    satu milidetik juga tidak. Kelangkaan CPU tidak bisa diperbaiki dengan
    menyerahkan CPU.
    """

    def test_lebih_longgar_dari_bawaan_pustaka(self) -> None:
        assert PING_TIMEOUT_SEC > 20.0

    def test_tetap_lebih_ketat_dari_pendeteksi_aruna(self) -> None:
        """Kalau keepalive pustaka menyala lebih lambat daripada
        `SILENT_TIMEOUT_SEC`, ia berhenti menjadi cadangan dan menjadi kode
        mati - dan cadangan yang tidak pernah menyala tidak menjaga apa pun."""
        assert PING_INTERVAL_SEC + PING_TIMEOUT_SEC < SILENT_TIMEOUT_SEC

    @pytest.mark.asyncio
    async def test_tidak_dipaksakan_ke_connect_yang_disuntikkan(self) -> None:
        """Test palsu tidak boleh dipaksa menirukan detail keepalive yang
        tidak sedang diujinya."""
        connector = _Connector([_Socket([], hang=True)])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()
        await stream.stop()

        assert connector.kwargs
        assert connector.kwargs[0] == {}


def _stream(connector: _Connector, sleeps: list[float], **kw) -> BinanceSpotStream:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    return BinanceSpotStream(
        SYMBOLS, connect=connector, sleep=sleep, silent_timeout_sec=0.05, **kw
    )


async def _settle() -> None:
    for _ in range(40):
        await asyncio.sleep(0)


class TestHargaMasuk:
    async def test_kutipan_tersimpan_di_memori(self) -> None:
        connector = _Connector([_Socket([book()], hang=True)])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()

        quote = stream.latest("BTC/USDT")
        assert quote is not None
        assert quote.bid == Decimal("64000.10")
        assert quote.ask == Decimal("64000.20")
        assert quote.mid == Decimal("64000.15")
        await stream.stop()

    async def test_simbol_yang_tidak_dilanggan_diabaikan(self) -> None:
        """Memecah simbol venue dengan menebak akan menaruh harga di bawah
        nama yang tidak dibaca siapa pun."""
        connector = _Connector([_Socket([book("DOGEUSDT")], hang=True)])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()

        assert stream.latest("DOGE/USDT") is None
        await stream.stop()

    async def test_url_melanggan_semua_simbol(self) -> None:
        connector = _Connector([_Socket([], hang=True)])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()

        assert "btcusdt@bookTicker" in connector.urls[0]
        assert "ethusdt@bookTicker" in connector.urls[0]
        await stream.stop()


class TestPesanCacatDitolak:
    """PASAL 10: data rusak tidak boleh masuk, dan tidak boleh menyegarkan
    umur - aliran rusak yang terlihat mutakhir lebih berbahaya daripada yang
    terlihat mati."""

    @pytest.mark.parametrize(
        "raw",
        [
            "bukan json",
            json.dumps({"data": {"s": "BTCUSDT"}}),  # tanpa harga
            json.dumps({"data": {"s": "BTCUSDT", "b": "abc", "a": "def"}}),
            json.dumps({"data": {"s": "BTCUSDT", "b": "NaN", "a": "NaN"}}),
            json.dumps({"data": "bukan dict"}),
            json.dumps({"tanpa": "data"}),
        ],
    )
    async def test_pesan_cacat_tidak_menghasilkan_kutipan(self, raw: str) -> None:
        connector = _Connector([_Socket([raw], hang=True)])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()

        assert stream.latest("BTC/USDT") is None
        assert stream.state()["messages"] == 0
        await stream.stop()


class TestPutusDanSambungUlang:
    """PASAL 3 dan 9. Putus itu normal; putus tanpa ada yang tahu tidak."""

    async def test_sesi_yang_mati_disambung_ulang(self) -> None:
        connector = _Connector([
            _Socket([book()]),               # lalu ConnectionError
            _Socket([book(bid="64100.00")], hang=True),
        ])
        sleeps: list[float] = []
        stream = _stream(connector, sleeps)
        await stream.start()
        await _settle()

        assert len(connector.urls) >= 2, connector.urls
        assert stream.latest("BTC/USDT").bid == Decimal("64100.00")
        assert stream.state()["disconnects"] >= 1
        await stream.stop()

    async def test_jeda_naik_dua_kali_lipat(self) -> None:
        """Tanpa backoff, venue yang menolak cepat jadi hot loop."""
        connector = _Connector([
            _Socket([]), _Socket([]), _Socket([]), _Socket([], hang=True),
        ])
        sleeps: list[float] = []
        stream = _stream(connector, sleeps)
        await stream.start()
        await _settle()

        assert sleeps[:3] == [
            RECONNECT_MIN_SEC,
            RECONNECT_MIN_SEC * 2,
            RECONNECT_MIN_SEC * 4,
        ], sleeps
        await stream.stop()

    async def test_pustaka_hilang_bukan_gangguan_jaringan(
        self, monkeypatch
    ) -> None:
        """**Bug VPS, 2026-08-23.** `websockets` tidak dideklarasikan di
        `pyproject.toml`; ia hanya hadir di mesin pengembang sebagai dependensi
        transitif `yfinance`. Instalasi bersih menghasilkan
        `ModuleNotFoundError`, yang tertangkap `except Exception` dan tercatat
        sebagai `stream.disconnected` tiap belasan detik.

        Bentuk kegagalannya persis seperti ISP yang memblokir Binance - dan itu
        memang pernah terjadi di mesin ini, jadi diagnosis salahnya sangat
        mungkin. Modul yang hilang tidak pulih dengan menunggu.
        """
        from aruna.data.crypto import stream as modul

        dicatat: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul.log, "error", lambda nama, **kw: dicatat.append((nama, kw))
        )
        monkeypatch.setattr(
            modul.log, "warning", lambda nama, **kw: dicatat.append((nama, kw))
        )

        connector = _Connector([
            ModuleNotFoundError("No module named 'websockets'"),
            _Socket([book()], hang=True),
        ])
        sleeps: list[float] = []
        stream = _stream(connector, sleeps)
        await stream.start()
        await _settle()

        nama = [n for n, _ in dicatat]
        assert "stream.dependensi_hilang" in nama
        assert "stream.disconnected" not in nama
        # Langsung ke mundur maksimum: menghajar tiap beberapa detik untuk
        # sesuatu yang tidak akan berubah sampai ada yang memasangnya cuma
        # membanjiri log yang seharusnya dibaca.
        assert sleeps[0] == RECONNECT_MAX_SEC, sleeps
        await stream.stop()

    async def test_gagal_menyambung_tidak_mematikan_loop(self) -> None:
        connector = _Connector([
            ConnectionError("venue unreachable"),
            _Socket([book()], hang=True),
        ])
        stream = _stream(connector, [])
        await stream.start()
        await _settle()

        assert stream.running
        assert stream.latest("BTC/USDT") is not None
        await stream.stop()

    async def test_aliran_menggantung_diputus_sendiri(self) -> None:
        """Bentuk kegagalan yang paling mahal: tersambung, senyap, dan
        terlihat sehat. Persis yang diukur pada futures di jaringan ini."""
        connector = _Connector([
            _Socket([], hang=True),                      # senyap
            _Socket([book(bid="64200.00")], hang=True),  # sesi pengganti
        ])
        stream = _stream(connector, [])
        await stream.start()
        for _ in range(200):
            await asyncio.sleep(0.005)
            if stream.latest("BTC/USDT") is not None:
                break

        assert len(connector.urls) >= 2, "aliran menggantung tidak pernah diputus"
        assert stream.latest("BTC/USDT").bid == Decimal("64200.00")
        await stream.stop()


class TestSnapshotSesudahSambungUlang:
    """PASAL 9. Venue tidak mengulang aliran yang terlewat.

    Sebelum ini ada, docstring modulnya sudah menyatakan reconciliation
    sebagai rangka sementara kodenya tidak melakukannya - janji tanpa kode,
    persis yang dilarang aturan C.
    """

    @staticmethod
    def _quote(symbol: str, bid: str, at: datetime) -> StreamedQuote:
        return StreamedQuote(
            symbol=symbol.upper(), bid=Decimal(bid), ask=Decimal(bid),
            last=None, event_time=None, received_at=at,
        )

    async def test_snapshot_diambil_sesudah_tersambung(self) -> None:
        diminta: list[str] = []

        async def snapshot(symbol: str) -> StreamedQuote:
            diminta.append(symbol)
            return self._quote(symbol, "63000", NOW)

        connector = _Connector([_Socket([], hang=True)])
        stream = _stream(connector, [], snapshot=snapshot)
        await stream.start()
        await _settle()

        assert diminta == list(SYMBOLS), diminta
        assert stream.latest("BTC/USDT").bid == Decimal("63000")
        assert stream.state()["snapshots"] == 2
        await stream.stop()

    async def test_snapshot_diulang_tiap_sambung_ulang(self) -> None:
        panggilan = {"n": 0}

        async def snapshot(symbol: str) -> StreamedQuote:
            panggilan["n"] += 1
            return self._quote(symbol, "63000", NOW)

        connector = _Connector([_Socket([]), _Socket([], hang=True)])
        stream = _stream(connector, [], snapshot=snapshot)
        await stream.start()
        await _settle()

        # Dua sesi x dua simbol.
        assert panggilan["n"] == 4, panggilan
        await stream.stop()

    async def test_snapshot_tidak_menimpa_kutipan_yang_lebih_baru(self) -> None:
        """Sesudah sambung ulang, pesan pertama bisa mendahului balasan REST.
        Menimpanya akan memundurkan harga."""
        async def snapshot(symbol: str) -> StreamedQuote:
            return self._quote(symbol, "1", NOW - timedelta(minutes=5))

        connector = _Connector([_Socket([book(bid="64000.10")], hang=True)])
        stream = _stream(connector, [], snapshot=snapshot)
        await stream.start()
        await _settle()

        assert stream.latest("BTC/USDT").bid == Decimal("64000.10")
        await stream.stop()

    async def test_snapshot_gagal_tidak_mematikan_aliran(self) -> None:
        async def snapshot(symbol: str) -> StreamedQuote:
            raise ConnectionError("rest unreachable")

        connector = _Connector([_Socket([book()], hang=True)])
        stream = _stream(connector, [], snapshot=snapshot)
        await stream.start()
        await _settle()

        assert stream.connected
        assert stream.latest("BTC/USDT") is not None
        assert stream.state()["snapshot_failures"] == 2
        await stream.stop()


class TestKesegaran:
    """PASAL 4: harga basi bukan harga, dan aliran mati tidak boleh memegang
    harga terakhir seolah masih mengalir."""

    def test_belum_pernah_menerima_bukan_umur_besar(self) -> None:
        stream = BinanceSpotStream(SYMBOLS)
        assert stream.age_sec("BTC/USDT") is None
        assert stream.is_fresh("BTC/USDT", 5.0) is False

    def test_umur_dihitung_dari_penerimaan(self) -> None:
        stream = BinanceSpotStream(SYMBOLS)
        stream._quotes["BTC/USDT"] = StreamedQuote(
            symbol="BTC/USDT", bid=Decimal(1), ask=Decimal(2), last=None,
            event_time=None, received_at=NOW - timedelta(seconds=30),
        )
        stream._connected = True

        assert stream.age_sec("BTC/USDT", now=NOW) == 30.0
        assert stream.is_fresh("BTC/USDT", 60.0, now=NOW) is True
        assert stream.is_fresh("BTC/USDT", 10.0, now=NOW) is False

    def test_terputus_berarti_tidak_segar_walau_harga_baru(self) -> None:
        """Harga semenit lalu dari aliran yang sudah mati bukan harga
        sekarang - ia hanya kebetulan angkanya masih muda."""
        stream = BinanceSpotStream(SYMBOLS)
        stream._quotes["BTC/USDT"] = StreamedQuote(
            symbol="BTC/USDT", bid=Decimal(1), ask=Decimal(2), last=None,
            event_time=None, received_at=NOW,
        )
        stream._connected = False

        assert stream.age_sec("BTC/USDT", now=NOW) == 0.0
        assert stream.is_fresh("BTC/USDT", 5.0, now=NOW) is False


class TestTidakMenyentuhSQL:
    def test_modulnya_tidak_mengimpor_database(self) -> None:
        """PASAL 26: SQL untuk memori analisis, bukan pita pengamatan.
        Diuji di tingkat impor supaya tidak bisa masuk diam-diam nanti."""
        from pathlib import Path

        source = Path("src/aruna/data/crypto/stream.py").read_text(encoding="utf-8")
        for terlarang in ("aruna.db", "repositories", "INSERT", "execute("):
            assert terlarang not in source, terlarang
