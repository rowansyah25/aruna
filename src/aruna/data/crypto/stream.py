"""Binance spot WebSocket: harga yang datang sendiri, bukan yang ditanyakan.

PASAL 2 menaruh streaming di atas polling, dan alasannya bukan gaya: sebuah
poll lima detik memutuskan kapan ARUNA boleh tahu, sementara pasar memutuskan
kapan ada yang layak diketahui. Di antara dua poll, apa pun boleh terjadi dan
tidak ada jejaknya.

**Tidak ada satu baris pun yang ditulis ke SQL dari sini** (PASAL 26). Aliran
ini hidup di memori: satu kutipan terakhir per simbol, ditimpa terus. Yang
masuk database tetap hasil analisis, bukan pita pengamatan.

**Futures TIDAK dilayani modul ini, dan itu bukan kelalaian.** Diukur pada
jaringan ini: `wss://fstream.binance.com` menerima koneksi, menjawab perintah
SUBSCRIBE dengan ``{"result": null, "id": 1}`` - jadi kanal dua arahnya hidup -
lalu tidak mengirim satu pesan data pun selama 30 detik, sementara spot pada
saat yang sama mengalir normal. Kanal kendali yang menjawab sambil data yang
ditahan bukan pipa yang rusak; itu keputusan di sisi venue atas alamat keluar
ini. Futures karena itu tetap REST, dan disebut REST di setiap tempat operator
membacanya - menyebutnya streaming akan menjadikan angka latensi sebuah
karangan (SPEC 4, 49).

Tiga sifat yang menjadi rangka modul ini:

**Putus itu normal, diam itu tidak.** PASAL 3 melarang mengklaim nol gap, dan
jaringan memang putus. Yang dilarang adalah putus tanpa ada yang tahu. Setiap
pemutusan dicatat, disambung ulang dengan backoff, dan selama itu simbolnya
ditandai tidak sehat - bukan dibiarkan memegang harga terakhir seolah masih
mengalir.

**Harga basi bukan harga.** PASAL 4: setiap kutipan membawa ``received_at``,
dan pembaca menanyakan umurnya, bukan mempercayainya. Sebuah aliran yang
menggantung - tersambung, tidak dikirimi apa-apa - terlihat persis seperti
pasar yang sepi kalau umurnya tidak pernah ditanyakan. Itu bentuk kegagalan
yang paling mahal, karena ia terlihat sehat.

**Sambung ulang bukan lanjut.** PASAL 9 menuntut snapshot REST sesudah
tersambung kembali, karena apa pun yang terlewat selama putus tidak akan
dikirim ulang oleh venue. Melanjutkan begitu saja berarti menambal lubang
dengan harga yang kebetulan datang berikutnya.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aruna.core.clock import monotonic, now_utc
from aruna.core.logging import get_logger

log = get_logger("aruna.data.crypto.stream")

SOURCE = "binance-spot-ws"

#: Alamat aliran gabungan Binance spot. Satu koneksi untuk semua simbol -
#: Binance mengizinkan sampai 1024 stream per koneksi, dan satu koneksi berarti
#: satu tempat yang bisa putus, bukan lima.
STREAM_URL = "wss://stream.binance.com:9443/stream"

#: Jeda sambung ulang, detik. Naik dua kali lipat sampai batas.
RECONNECT_MIN_SEC = 1.0
RECONNECT_MAX_SEC = 60.0

#: Selama ini tidak ada satu pesan pun, koneksinya dianggap menggantung dan
#: diputus sengaja supaya siklus sambung-ulang berjalan.
#:
#: Binance mengirim ping tiap tiga menit dan pustaka `websockets` menjawabnya
#: sendiri, jadi koneksi bisa terlihat "hidup" tanpa satu pun data pasar - dan
#: itu persis bentuk kegagalan yang diukur pada futures di jaringan ini.
#: Nilainya jauh di atas jeda antar-perdagangan pasangan teramai, sehingga
#: pasar yang benar-benar sepi tidak dipotong tanpa sebab.
SILENT_TIMEOUT_SEC = 90.0

#: Keepalive pustaka `websockets`, dilonggarkan dengan sengaja.
#:
#: **Ini pemeriksa kedua, bukan yang pertama.** Yang mendeteksi aliran
#: menggantung adalah :data:`SILENT_TIMEOUT_SEC` di atas - milik ARUNA, dengan
#: alasan yang tertulis. Keepalive pustaka memeriksa hal yang sama dengan
#: ambang bawaan dua puluh detik, dan pada mesin ini ambang itu tidak bisa
#: dipenuhi.
#:
#: Terukur 2026-08-22. Mesinnya punya **dua core fisik** dan menjalankan enam
#: proses Python plus MySQL. Fase `upkeep.manfaat` menyapu korpus ingatan
#: selama seratus lima puluh empat detik dengan Python murni; selama itu thread
#: event loop harus mengantre CPU di belakang semuanya. Tujuh
#: `keepalive ping timeout` terjadi, seluruhnya di dalam jendela itu - dan
#: keduanya, melepas GIL tiap lima puluh target maupun tidur satu milidetik,
#: tidak menghapusnya. Kelangkaan CPU tidak bisa diperbaiki dengan menyerahkan
#: CPU.
#:
#: Enam puluh detik memberi ruang untuk jendela itu sambil tetap menemukan
#: koneksi yang benar-benar mati dalam delapan puluh detik - masih di bawah
#: `SILENT_TIMEOUT_SEC`, sehingga pemeriksa ARUNA sendiri tetap yang terakhir
#: bicara.
#:
#: **Yang ini menutup gejalanya, bukan akarnya.** Akarnya sapuan kuadratik itu
#: sendiri: 3,3 juta panggilan `bandingkan` untuk 2.567 ingatan. Memperkecilnya
#: adalah pekerjaan tersendiri, dan sampai itu dikerjakan, ARUNA memang melambat
#: sekali sehari selama tiga menit.
PING_INTERVAL_SEC = 20.0
PING_TIMEOUT_SEC = 60.0


@dataclass(frozen=True, slots=True)
class StreamedQuote:
    """Satu kutipan dari aliran, beserta kapan ARUNA benar-benar menerimanya.

    ``event_time`` milik venue dan ``received_at`` milik mesin ini. Keduanya
    disimpan karena selisihnya adalah satu-satunya cara membedakan "pasar
    sepi" dari "aliran tertinggal": stempel venue yang lama pada pesan yang
    baru saja tiba berarti venue-nya yang telat, sedangkan ``received_at``
    yang lama berarti tidak ada yang tiba sama sekali.
    """

    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    event_time: datetime | None
    received_at: datetime

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


def _dec(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _ms(raw: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=now_utc().tzinfo)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class BinanceSpotStream:
    """Satu koneksi, banyak simbol, keadaan hanya di memori.

    ``connect`` disuntikkan supaya seluruh perilaku - sambung ulang, backoff,
    deteksi menggantung - bisa diuji tanpa jaringan dan tanpa menunggu waktu
    nyata. Tanpa itu satu-satunya cara mengujinya adalah mencabut kabel, dan
    perilaku yang hanya bisa diuji begitu adalah perilaku yang tidak pernah
    diuji.
    """

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        url: str = STREAM_URL,
        connect: Any = None,
        sleep: Any = None,
        silent_timeout_sec: float = SILENT_TIMEOUT_SEC,
        ping_interval_sec: float = PING_INTERVAL_SEC,
        ping_timeout_sec: float = PING_TIMEOUT_SEC,
        snapshot: Any = None,
    ) -> None:
        self._symbols = tuple(dict.fromkeys(symbols))
        self._url = url
        self._connect = connect
        self._sleep = sleep or asyncio.sleep
        self._silent_timeout = silent_timeout_sec
        self._ping_interval = ping_interval_sec
        self._ping_timeout = ping_timeout_sec
        #: Awaitable ``(symbol) -> StreamedQuote | None``, dipanggil sesudah
        #: tersambung. Tanpa ini modul hanya BERJANJI melakukan reconciliation
        #: PASAL 9 di docstring-nya; dengan ini ia melakukannya.
        self._snapshot = snapshot
        self._snapshots_taken = 0
        self._snapshot_failures = 0

        self._quotes: dict[str, StreamedQuote] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._connected = False
        self._connected_since: datetime | None = None
        self._disconnects = 0
        self._messages = 0
        self._last_message_mono: float | None = None
        self._last_error: str | None = None

    # ---- what the operator and the readers ask -------------------------

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def connected(self) -> bool:
        return self._connected

    def latest(self, symbol: str) -> StreamedQuote | None:
        return self._quotes.get(symbol.upper())

    def age_sec(self, symbol: str, *, now: datetime | None = None) -> float | None:
        """Umur kutipan terakhir, atau ``None`` kalau belum pernah ada satu pun.

        ``None`` sengaja bukan angka besar: "belum pernah menerima" dan
        "menerima lama sekali" adalah dua keadaan berbeda, dan menyamakannya
        adalah nol yang berarti tidak tahu (SPEC 4).
        """
        quote = self.latest(symbol)
        if quote is None:
            return None
        return ((now or now_utc()) - quote.received_at).total_seconds()

    def is_fresh(self, symbol: str, max_age_sec: float, *, now: datetime | None = None) -> bool:
        """PASAL 4: aliran yang tidak sehat tidak boleh melahirkan signal."""
        if not self._connected:
            return False
        age = self.age_sec(symbol, now=now)
        return age is not None and age <= max_age_sec

    def state(self) -> dict[str, Any]:
        """Untuk health. Angka apa adanya, tanpa vonis."""
        now = now_utc()
        return {
            "source": SOURCE,
            "connected": self._connected,
            "connected_since": (
                self._connected_since.isoformat() if self._connected_since else None
            ),
            "symbols": list(self._symbols),
            "disconnects": self._disconnects,
            "messages": self._messages,
            "snapshots": self._snapshots_taken,
            "snapshot_failures": self._snapshot_failures,
            "last_error": self._last_error,
            "ages_sec": {
                symbol: self.age_sec(symbol, now=now) for symbol in self._symbols
            },
        }

    # ---- driving it ----------------------------------------------------

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="aruna-binance-stream")
        log.info("stream.started", source=SOURCE, symbols=len(self._symbols))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._connected = False
        log.info("stream.stopped", source=SOURCE, messages=self._messages)

    def _stream_path(self) -> str:
        names = "/".join(f"{s.lower().replace('/', '')}@bookTicker" for s in self._symbols)
        return f"{self._url}?streams={names}"

    async def _run(self) -> None:
        delay = RECONNECT_MIN_SEC
        while not self._stopping.is_set():
            try:
                await self._session()
                # A clean return means the venue closed on us; that is still a
                # disconnect and still earns a backoff, or a venue that closes
                # instantly becomes a hot loop.
                delay = RECONNECT_MIN_SEC
            except asyncio.CancelledError:
                raise
            except ModuleNotFoundError as exc:
                # **Bukan gangguan jaringan, dan menunggu tidak menyembuhkannya.**
                # Terjadi di VPS 2026-08-23: `websockets` tidak dideklarasikan
                # di `pyproject.toml` dan hanya hadir di mesin pengembang sebagai
                # dependensi transitif `yfinance`. Tertangkap `except Exception`
                # di bawah, gejalanya menjadi `stream.disconnected` tiap belasan
                # detik - terbaca persis seperti ISP yang memblokir Binance,
                # yang memang pernah terjadi di sini.
                #
                # Umpan tanpa penjaga tetap tidak boleh mati, jadi ia tidak
                # dilempar. Yang berubah: levelnya ERROR, namanya menyebut
                # sebabnya, dan mundurnya langsung ke maksimum alih-alih
                # menghajar setiap beberapa detik untuk sesuatu yang tidak akan
                # berubah sampai ada yang memasangnya.
                self._last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.error(
                    "stream.dependensi_hilang",
                    source=SOURCE,
                    error=self._last_error,
                    perbaikan="pip install -e . (websockets ada di dependencies)",
                )
                delay = RECONNECT_MAX_SEC
            except Exception as exc:  # noqa: BLE001 - an unattended feed must not die
                self._last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("stream.disconnected", source=SOURCE, error=self._last_error)
            finally:
                if self._connected:
                    self._disconnects += 1
                self._connected = False
                self._connected_since = None

            if self._stopping.is_set():
                return
            await self._sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SEC)

    async def _session(self) -> None:
        if self._connect is None:
            import websockets

            connect = websockets.connect
            # Hanya untuk pustaka sungguhan. `connect` yang disuntikkan test
            # tidak perlu tahu soal keepalive, dan memaksanya menerima kwargs
            # ini akan membuat setiap test palsu menirukan detail yang tidak
            # sedang diujinya.
            pengaturan: dict[str, Any] = {
                "ping_interval": self._ping_interval,
                "ping_timeout": self._ping_timeout,
            }
        else:
            connect = self._connect
            pengaturan = {}

        async with connect(self._stream_path(), **pengaturan) as ws:
            self._connected = True
            self._connected_since = now_utc()
            self._last_message_mono = monotonic()
            log.info("stream.connected", source=SOURCE, symbols=len(self._symbols))

            # PASAL 9. Whatever moved while the socket was down is gone: a
            # venue does not replay a stream on reconnect. Carrying on from the
            # next message that happens to arrive patches the hole with a price
            # that has no claim to it, and the gap leaves no trace. So the
            # first thing a fresh connection does is ask REST where the market
            # actually is.
            #
            # Taken on the FIRST connection too, not only on reconnects: the
            # gap between "the process started" and "the first message" is the
            # same hole with a different name.
            await self._reconcile()

            while not self._stopping.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._silent_timeout)
                except TimeoutError:
                    # Connected and silent. Treated as a fault rather than as a
                    # quiet market: this is exactly the shape futures shows on
                    # this network, and a hung stream that keeps its last price
                    # is worse than one that admits it is down.
                    log.warning(
                        "stream.silent",
                        source=SOURCE,
                        seconds=self._silent_timeout,
                        detail="connected but no message; reconnecting",
                    )
                    return
                self._absorb(raw)

    async def _reconcile(self) -> None:
        """Ambil harga REST untuk setiap simbol, sekali, sesudah tersambung.

        Kegagalannya sengaja tidak mematikan sesi. Aliran yang sudah hidup
        lebih berharga daripada snapshot yang gagal, dan menolak melanjutkan
        akan menukar kekurangan satu titik data dengan kehilangan seluruhnya.
        Yang gagal dicatat sebagai angka, bukan ditelan - operator harus bisa
        melihat bahwa lubangnya tidak pernah ditutup.

        Hasil snapshot TIDAK menimpa kutipan aliran yang lebih baru. Sesudah
        sambung ulang, pesan pertama bisa tiba mendahului balasan REST, dan
        menimpanya akan memundurkan harga - persis kebalikan dari maksud
        reconciliation.
        """
        if self._snapshot is None:
            return
        for symbol in self._symbols:
            try:
                quote = await self._snapshot(symbol)
            except Exception as exc:  # noqa: BLE001 - a probe must not kill the feed
                self._snapshot_failures += 1
                log.warning(
                    "stream.snapshot_failed",
                    source=SOURCE,
                    symbol=symbol,
                    error=f"{type(exc).__name__}: {exc}"[:160],
                )
                continue
            if quote is None:
                self._snapshot_failures += 1
                continue
            existing = self._quotes.get(symbol.upper())
            if existing is not None and existing.received_at >= quote.received_at:
                continue
            self._quotes[symbol.upper()] = quote
            self._snapshots_taken += 1

    def _absorb(self, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return

        venue_symbol = data.get("s")
        if not isinstance(venue_symbol, str):
            return
        symbol = self._canonical(venue_symbol)
        if symbol is None:
            return

        bid = _dec(data.get("b"))
        ask = _dec(data.get("a"))
        if bid is None and ask is None:
            # Nothing usable. Dropped rather than stored as an empty quote,
            # which would refresh `received_at` and make a broken feed look
            # current (PASAL 10).
            return

        self._messages += 1
        self._last_message_mono = monotonic()
        self._quotes[symbol] = StreamedQuote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=None,
            event_time=_ms(data.get("E")),
            received_at=now_utc(),
        )

    def _canonical(self, venue_symbol: str) -> str | None:
        """Venue form back to the canonical one this system stores.

        Matched against the subscribed set rather than split by guessing where
        the quote asset begins - the subscription is the only list that is
        certainly right, and a wrong split here would file BTCUSDT's price
        under a symbol nothing reads.
        """
        target = venue_symbol.upper()
        for symbol in self._symbols:
            if symbol.upper().replace("/", "") == target:
                return symbol.upper()
        return None


__all__ = [
    "RECONNECT_MAX_SEC",
    "RECONNECT_MIN_SEC",
    "SILENT_TIMEOUT_SEC",
    "SOURCE",
    "STREAM_URL",
    "BinanceSpotStream",
    "StreamedQuote",
]
