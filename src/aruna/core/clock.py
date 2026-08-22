"""Time handling.

Two rules the rest of ARUNA depends on:

* every timestamp that leaves this module is timezone-aware; naive datetimes
  are rejected, because a naive timestamp is how look-ahead bugs (SPEC 24)
  become invisible;
* wall-clock time and elapsed time come from different sources - ``now_utc``
  for "when did this happen", ``monotonic`` for "how long did this take".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from aruna.core.enums import CryptoSession, IdxSession

JAKARTA = ZoneInfo("Asia/Jakarta")


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    """Current instant, UTC, timezone-aware."""
    return datetime.now(UTC)


def now_jakarta() -> datetime:
    """Current instant in WIB - the IDX exchange timezone."""
    return datetime.now(JAKARTA)


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC.  Naive input is a bug, not a default."""
    require_aware(value)
    return value.astimezone(UTC)


def to_jakarta(value: datetime) -> datetime:
    require_aware(value)
    return value.astimezone(JAKARTA)


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "naive datetime rejected: ARUNA timestamps must carry a timezone "
            "so that ordering across providers is unambiguous"
        )
    return value


def monotonic() -> float:
    """Monotonic seconds, for measuring durations only."""
    return time.monotonic()


def elapsed_ms(started: float) -> float:
    """Milliseconds since a :func:`monotonic` reading, rounded to 3 decimals."""
    return round((time.monotonic() - started) * 1000.0, 3)


def age_seconds(value: datetime, *, reference: datetime | None = None) -> float:
    """Seconds between ``value`` and now (or ``reference``).  Negative = future."""
    ref = reference or now_utc()
    return (to_utc(ref) - to_utc(value)).total_seconds()


def isoformat(value: datetime) -> str:
    """Canonical string form used in logs and snapshots.

    Always UTC. Storage, ordering and cross-provider comparison need one
    timezone, and a stored local time is ambiguous twice a year in half the
    world. What a *person* reads is a different question - see :func:`wib`.
    """
    return to_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def wib(value: datetime) -> str:
    """Local form for anything a person reads.

    The operator lives in WIB and reads these on a phone; making them convert
    from UTC in their head is a small tax charged on every message, and it is
    charged hardest at three in the morning when the arithmetic is worst.

    The suffix stays, because a bare timestamp with no zone is exactly the
    ambiguity this project refuses everywhere else.
    """
    return f"{to_jakarta(value):%Y-%m-%d %H:%M:%S} WIB"


# ---------------------------------------------------------------------------
# IDX exchange sessions (SPEC 3, 9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdxSessionWindows:
    """IDX regular-market session boundaries in WIB.

    Defaults follow the IDX regular market schedule with a shorter Friday
    session.  These are configuration, not truth: exchange hours change, and
    ARUNA holds no holiday calendar yet.  See :meth:`IdxCalendar.is_open` for
    exactly what is and is not accounted for.
    """

    pre_open_start: dtime = dtime(8, 45)
    session1_start: dtime = dtime(9, 0)
    session1_end_mon_thu: dtime = dtime(12, 0)
    session1_end_fri: dtime = dtime(11, 30)
    session2_start_mon_thu: dtime = dtime(13, 30)
    session2_start_fri: dtime = dtime(14, 0)
    session2_end: dtime = dtime(15, 49, 59)
    pre_close_end: dtime = dtime(16, 0)
    post_trading_end: dtime = dtime(16, 15)


class IdxCalendar:
    """Maps a moment in time onto an IDX session label.

    Known limitation (PHASE 1): weekends are handled, public holidays and
    exchange-declared non-trading days are **not** - that needs an IDX holiday
    source, which arrives with the IDX data provider in PHASE 2.  Until then
    :meth:`is_open` can return ``True`` on an Indonesian public holiday, so
    callers must treat it as a schedule hint and still require live data before
    claiming the market is trading (SPEC 3).
    """

    def __init__(self, windows: IdxSessionWindows | None = None) -> None:
        self.windows = windows or IdxSessionWindows()
        self._holidays: set[date] = set()

    def load_holidays(self, holidays: set[date]) -> None:
        """Install a holiday set once a source for it exists (PHASE 2)."""
        self._holidays = set(holidays)

    @property
    def has_holiday_calendar(self) -> bool:
        return bool(self._holidays)

    def is_trading_day(self, value: datetime) -> bool:
        local = to_jakarta(value)
        if local.weekday() >= 5:  # Saturday, Sunday
            return False
        return local.date() not in self._holidays

    def session(self, value: datetime | None = None) -> IdxSession:
        local = to_jakarta(value or now_utc())
        w = self.windows
        clock = local.timetz().replace(tzinfo=None)

        if not self.is_trading_day(local):
            return IdxSession.AFTER_MARKET

        is_friday = local.weekday() == 4
        s1_end = w.session1_end_fri if is_friday else w.session1_end_mon_thu
        s2_start = w.session2_start_fri if is_friday else w.session2_start_mon_thu

        if clock < w.pre_open_start:
            return IdxSession.PRE_MARKET
        if clock < w.session1_start:
            return IdxSession.OPENING
        if clock < s1_end:
            return IdxSession.MID_SESSION
        if clock < s2_start:
            # Lunch break: the exchange is not matching orders.
            return IdxSession.MID_SESSION
        if clock <= w.session2_end:
            return IdxSession.MID_SESSION
        if clock <= w.pre_close_end:
            return IdxSession.CLOSING
        return IdxSession.AFTER_MARKET

    def is_open(self, value: datetime | None = None) -> bool:
        """True when the regular market should be matching orders.

        Excludes the lunch break and the pre-opening auction.  Does not know
        about public holidays - see the class docstring.
        """
        local = to_jakarta(value or now_utc())
        if not self.is_trading_day(local):
            return False

        w = self.windows
        clock = local.timetz().replace(tzinfo=None)
        is_friday = local.weekday() == 4
        s1_end = w.session1_end_fri if is_friday else w.session1_end_mon_thu
        s2_start = w.session2_start_fri if is_friday else w.session2_start_mon_thu

        in_session1 = w.session1_start <= clock < s1_end
        in_session2 = s2_start <= clock <= w.session2_end
        return in_session1 or in_session2


#: Shared default calendar.  Replace via ``IdxCalendar(windows=...)`` if the
#: exchange changes its hours.
IDX_CALENDAR = IdxCalendar()

#: Berapa lama sebelum bel pembuka ARUNA mulai bekerja lagi untuk IDX.
#:
#: Diminta operator: "jalan lagi 30 menit sebelum market buka, dan fast
#: screening untuk tahu apa yang akan terjadi di market."
#:
#: Tiga puluh menit adalah jendela yang cukup untuk menarik candle penutupan
#: kemarin, membaca berita yang terbit semalam, dan menghitung indikatornya -
#: sebelum harga pertama hari itu terbentuk. Lebih pendek dari itu dan
#: penarikan datanya belum selesai saat bel berbunyi; lebih panjang dan ARUNA
#: memanggil provider untuk pasar yang belum bergerak sama sekali.
#:
#: Ia dimulai sebelum lelang pra-pembukaan bursa sendiri (08:45), dan itu
#: disengaja: yang dikerjakan di jendela ini adalah membaca dan menghitung,
#: bukan menunggu order matching.
IDX_WARMUP = timedelta(minutes=30)


def idx_active(value: datetime | None = None) -> bool:
    """Apakah pekerjaan IDX layak dijalankan sekarang?

    **Satu predikat untuk semua jalur IDX** - kutipan harga, penyegaran candle,
    pemindaian, dan penguncian prediksi. Dua salinan aturan ini akan berbeda
    pendapat tentang apakah bursa buka, dan satu-satunya yang mengungkapnya
    adalah tagihan rate limit atau prediksi yang tidak bisa diskor.

    Benar dari :data:`IDX_WARMUP` sebelum bel pembuka sampai akhir sesi
    pra-penutupan. Di luar itu salah - dan "salah" di sini berarti ARUNA
    benar-benar berhenti: tidak memanggil provider, tidak menghitung ulang,
    tidak mengunci prediksi baru.

    **Ujung belakangnya sengaja tidak dilebarkan.** Hanya jendela pemanasan di
    depan yang baru; sesudah pukul 16:00 predikat ini kembali persis seperti
    sebelumnya, dan itu bukan kelalaian. Sapuan penyelesaian di
    :meth:`~aruna.upkeep.candles.CandleRefresher.market_gate` justru bergantung
    pada gerbang ini **tertutup**: bar penutupan 15m dan 1h hari itu baru final
    sesudah sesi pasca-perdagangan, dan sapuan itulah satu-satunya yang
    menyimpannya. Melebarkan ujung belakang ke 16:15 menunda sapuannya sampai
    sesudah batas bar 15m - persis cacat yang pernah diukur dan diperbaiki di
    sana.

    **Istirahat siang dihitung tutup**, dan itu perilaku yang sudah ada
    sebelum jendela pemanasan ini - bukan keputusan baru. Bursa memang berhenti
    mencocokkan order sekitar satu setengah jam, jadi harga tidak bergerak dan
    memanggil provider di situ menulis ulang angka yang sama.

    Konsekuensinya disebut supaya tidak ditemukan sebagai kejutan: sesi kedua
    dimulai tanpa pemanasan. Alasan yang membenarkan tiga puluh menit sebelum
    bel pembuka - menarik data dan menghitung indikator sebelum harga pertama
    terbentuk - berlaku juga di sana, dan tidak dikerjakan di sini karena yang
    diminta adalah bel pembuka. Melebarkannya adalah keputusan tersendiri.

    Hari libur nasional tidak diketahui - lihat :class:`IdxCalendar`. Pada hari
    libur ini mengembalikan ``True`` dan ARUNA akan bekerja atas pasar yang
    tutup. Yang menahannya adalah gerbang data: harga tidak bergerak, dan
    ``market_open`` pada snapshot-nya memblokir penerbitan signal (SPEC 32).
    """
    moment = value or now_utc()
    local = to_jakarta(moment)
    if not IDX_CALENDAR.is_trading_day(local):
        return False

    if IDX_CALENDAR.is_open(moment) or IDX_CALENDAR.session(moment) in (
        IdxSession.OPENING,
        IdxSession.CLOSING,
    ):
        return True

    # Jendela pemanasan: dari IDX_WARMUP sebelum bel pembuka sampai bel itu
    # sendiri. Sesudahnya kalender yang menjawab, bukan aritmetika di sini.
    w = IDX_CALENDAR.windows
    buka = datetime.combine(local.date(), w.session1_start, tzinfo=local.tzinfo)
    mulai = (buka - IDX_WARMUP).timetz().replace(tzinfo=None)
    clock = local.timetz().replace(tzinfo=None)
    return mulai <= clock < w.session1_start


def idx_session(value: datetime | None = None) -> IdxSession:
    return IDX_CALENDAR.session(value)


def is_idx_open(value: datetime | None = None) -> bool:
    return IDX_CALENDAR.is_open(value)


# ---------------------------------------------------------------------------
# Crypto sessions (SPEC 9)
# ---------------------------------------------------------------------------

#: Non-overlapping UTC hour bands.  Real trading sessions overlap; ARUNA needs
#: one deterministic label per timestamp, so the bands are cut at the hours
#: where each region's volume dominates.  ``24H`` covers the quiet band.
_CRYPTO_BANDS: tuple[tuple[int, int, CryptoSession], ...] = (
    (0, 8, CryptoSession.ASIA),
    (8, 13, CryptoSession.EUROPE),
    (13, 21, CryptoSession.US),
    (21, 24, CryptoSession.H24),
)


def crypto_session(value: datetime | None = None) -> CryptoSession:
    """Dominant regional session for a UTC instant.  Crypto never closes."""
    hour = to_utc(value or now_utc()).hour
    for start, end, session in _CRYPTO_BANDS:
        if start <= hour < end:
            return session
    return CryptoSession.H24


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def deadline_from(started: datetime, duration: timedelta) -> datetime:
    """Absolute UTC deadline, used for horizon evaluation windows."""
    return to_utc(started) + duration


__all__ = [
    "IDX_CALENDAR",
    "JAKARTA",
    "IdxCalendar",
    "IdxSessionWindows",
    "age_seconds",
    "crypto_session",
    "deadline_from",
    "elapsed_ms",
    "idx_session",
    "is_idx_open",
    "isoformat",
    "monotonic",
    "now_jakarta",
    "now_utc",
    "require_aware",
    "to_jakarta",
    "to_utc",
    "wib",
]
