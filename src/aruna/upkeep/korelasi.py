"""Hitung korelasi pasangan dan simpan, supaya keputusan bisa membacanya.

**PASAL 15.19 (correlation memory) juga dijawab di sini.** Korelasi disimpan
beserta timeframe, ukuran sampel (``overlap``), dan waktu perhitungannya - dan
disegarkan tiap jam, karena korelasi bukan konstanta. Terukur: BTC-ETH 0,84 dan
ETH-SOL 0,88 pada 4h.


PASAL 14.41 mendaftar ``CORRELATION_RISK`` sebagai masukan wajib Phase 13, dan
mesinnya sudah ada sejak Phase 4: :mod:`aruna.analysis.correlation` menghitung
koefisiennya, :class:`~aruna.db.repositories.fundamental.CorrelationRepository`
menyimpannya, dan :class:`~aruna.learning.snapshot.PembacaPembelajaran`
membacanya ke dalam catatan council.

Yang tidak ada di antara ketiganya adalah **yang menjalankannya**. Satu-satunya
pemanggil ``build_matrix`` adalah perintah CLI ``aruna correlate`` - dijalankan
manusia, dan terukur pada 2026-08-21 tidak pernah dijalankan sama sekali: tabel
``correlations`` kosong, nol baris, sementara empat puluh amatan berturut-turut
melaporkan ``CORRELATION_RISK`` hilang.

**Dihitung dari candle tersimpan, bukan dari venue.** Tidak ada satu pun
permintaan jaringan di sini; bar yang dipakai adalah bar yang sudah disegarkan
:class:`~aruna.upkeep.candles.CandleRefresher`. Karena itu ia boleh berjalan
lebih sering daripada ongkos jaringan mengizinkan - dan tetap tidak boleh, lihat
``correlation_interval_sec``: bar 4h berubah tiap empat jam, dan menghitung
ulang tiap menit adalah menghitung ulang jawaban yang sama.

**Yang tipis dilewati dan disebut namanya** (§13.26). Korelasi dari sepuluh bar
adalah angka yang terlihat seperti pengukuran dan bukan pengukuran; ia akan
masuk ke keputusan dengan bobot yang sama seperti angka dari lima ratus bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aruna.analysis.correlation import build_matrix
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.clock import now_utc
from aruna.core.enums import Horizon, Market, horizons_for_market
from aruna.core.logging import get_logger

log = get_logger("aruna.upkeep.korelasi")

#: Horizon yang benar-benar dipakai loop futures untuk merencanakan.
#:
#: Satu tempat, dua pembaca: :func:`aruna.supervisor.default_children` mengoper
#: nilainya sebagai ``--horizon``, dan penyegar ini menghitung untuk interval
#: yang sama. Dua angka yang berdiri sendiri akan berselisih diam-diam, dan
#: korelasi 1h yang tersimpan rapi sementara futures merencanakan 4h adalah
#: tabel terisi yang tidak pernah terbaca - persis cacat yang modul ini ada
#: untuk menutupnya, cuma pindah satu interval.
HORIZON_KEPUTUSAN: Horizon = Horizon.H4

#: Bar minimum sebelum sebuah aset boleh ikut dihitung.
#:
#: Sama dengan ambang yang dipakai ``aruna correlate``, dan bukan kebetulan:
#: dua ambang untuk perhitungan yang sama akan menghasilkan dua daftar aset
#: yang berbeda dari sumber yang sama.
MIN_CANDLE = 25

#: Berapa bar dibaca per aset. Cukup panjang untuk koefisien yang stabil,
#: cukup pendek untuk tetap menggambarkan pasar yang sekarang.
BAR_DIBACA = 200


@dataclass(frozen=True, slots=True)
class HasilKorelasi:
    """Apa yang benar-benar terjadi pada satu pasar, termasuk yang tidak."""

    market: Market
    interval: Horizon
    aset: int
    pairs: int
    stored: int
    #: Aset yang tidak ikut, beserta sebabnya. Disebut, bukan dihitung: hanya
    #: namanya yang memberitahu apa yang harus dicari.
    dilewati: tuple[str, ...] = ()


class PenyegarKorelasi:
    """Satu lintasan: baca candle tersimpan, hitung matriks, simpan.

    Kolaboratornya duck-typed dengan alasan yang sama seperti
    :class:`~aruna.upkeep.loop.UpkeepLoop`: yang dibutuhkan hanya
    ``universe.assets``, ``market_data.candles`` dan ``store.save``, dan
    menuntut kelas konkretnya akan membuat lintasan ini tidak bisa diuji tanpa
    database di belakangnya.
    """

    def __init__(
        self,
        *,
        universe: Any,
        market_data: Any,
        store: Any,
        markets: tuple[Market, ...] = (Market.CRYPTO,),
        interval: Horizon = HORIZON_KEPUTUSAN,
        limit: int = BAR_DIBACA,
    ) -> None:
        self._universe = universe
        self._market_data = market_data
        self._store = store
        self._markets = markets
        self._interval = interval
        self._limit = limit

    @property
    def interval(self) -> Horizon:
        """Interval yang dihitung - dibaca test penyambungan, bukan ditebak."""
        return self._interval

    async def refresh(self, *, now: datetime | None = None) -> tuple[HasilKorelasi, ...]:
        """Hitung dan simpan untuk tiap pasar. Kegagalan satu pasar diisolasi."""
        saat = now or now_utc()
        hasil: list[HasilKorelasi] = []
        for market in self._markets:
            # **Pasar yang tidak menawarkan horizonnya dilewati diam-diam.**
            #
            # Terukur pada siklus pertama sesudah restart 2026-08-21: IDX
            # memulangkan `korelasi.tidak_cukup_aset` dengan nol aset. Bukan
            # data yang kurang - IDX tidak punya bar 4h sama sekali, dan
            # `horizons_for_market` menyebutnya langsung. Peringatan tiap jam
            # yang tidak akan pernah bisa diperbaiki oleh data akan berhenti
            # dibaca, lalu menutupi peringatan yang berarti sesuatu.
            if self._interval not in horizons_for_market(market):
                continue
            hasil.append(await self._satu_pasar(market, saat))
        return tuple(hasil)

    async def _satu_pasar(self, market: Market, saat: datetime) -> HasilKorelasi:
        seri: dict[str, CandleSeries] = {}
        dilewati: list[str] = []

        for aset in await self._universe.assets(market=market, enabled_only=True):
            baris = await self._market_data.candles(
                aset.id, self._interval, limit=self._limit
            )
            if len(baris) < MIN_CANDLE:
                dilewati.append(f"{aset.symbol}: {len(baris)} bar, butuh {MIN_CANDLE}")
                continue
            try:
                seri[aset.symbol] = CandleSeries.from_rows(
                    baris, market=market, symbol=aset.symbol, interval=self._interval
                )
            except InsufficientData as exc:
                dilewati.append(f"{aset.symbol}: {exc}")

        if len(seri) < 2:
            # Tidak disimpan, dan itu keputusan: sebuah matriks kosong yang
            # ditulis akan menimpa baris kemarin yang masih berarti dengan
            # ketiadaan hari ini, dan pembacanya tidak bisa membedakan keduanya.
            log.warning(
                "korelasi.tidak_cukup_aset",
                market=market.value,
                interval=self._interval.value,
                aset=len(seri),
                dilewati=len(dilewati),
            )
            return HasilKorelasi(
                market=market,
                interval=self._interval,
                aset=len(seri),
                pairs=0,
                stored=0,
                dilewati=tuple(dilewati),
            )

        matrix = build_matrix(
            seri, interval=self._interval.value, computed_at=saat
        )
        stored = await self._store.save(matrix, market=market)
        log.info(
            "korelasi.disimpan",
            market=market.value,
            interval=self._interval.value,
            aset=len(seri),
            pairs=len(matrix.pairs),
            stored=stored,
            dilewati=list(dilewati),
        )
        return HasilKorelasi(
            market=market,
            interval=self._interval,
            aset=len(seri),
            pairs=len(matrix.pairs),
            stored=stored,
            dilewati=tuple(dilewati) + tuple(matrix.skipped),
        )


__all__ = [
    "BAR_DIBACA",
    "HORIZON_KEPUTUSAN",
    "MIN_CANDLE",
    "HasilKorelasi",
    "PenyegarKorelasi",
]
