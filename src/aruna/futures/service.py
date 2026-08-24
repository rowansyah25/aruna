"""Futures plan service: council verdict -> plan -> stored (FUTURES SPEC 8-14).

The seam between the two halves of ARUNA. The council decides a direction from
evidence; F1-F4 decide whether a leveraged position can be built around that
direction; this module runs the second on the output of the first and stores
what came out - **including, and especially, the refusals.**

Storing the refusals is not bookkeeping. FUTURES SPEC 48 wants a daily account
of what ARUNA did, and a day of two plans and forty refusals is a day it mostly
said no. A store that only kept the plans would describe a different system from
the one that ran, and would make the refusals invisible to the learning layer
that has to judge whether saying no was right.

**ARUNA never executes.** This module reads market data and writes rows. There
is no venue call that could place, cancel or modify an order, change leverage or
margin mode, or move funds (FUTURES SPEC 3, 50).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.enums import Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.futures.binance import BinanceFuturesProvider
from aruna.futures.debate import CouncilNote, note_of
from aruna.futures.discipline import review
from aruna.futures.models import PositionSide, side_of
from aruna.futures.plan import FuturesPlan, PlanVerdict, build_plan

log = get_logger("aruna.futures.service")

#: Structural levels handed to the target engine. More than this and the
#: nearest few stop being the ones the market actually reacts at.
MAX_STRUCTURE_LEVELS = 5

#: Rows of history the discipline engine reads.
#:
#: Generous on purpose. Most rows are refusals and WAITs, which never resolve,
#: so a window sized for "the last few trades" would be filled entirely by
#: plans that cannot carry an outcome and the losing streak would stay
#: invisible for a different reason than the one just fixed.
DISCIPLINE_HISTORY = 200

#: Versi model yang menghasilkan plan futures.
#:
#: Dieja sekali dan dipakai dua tempat - baris yang disimpan dan baris yang
#: dicetak di pesan. Dua konstanta yang mengeja versi yang sama boleh berbeda,
#: dan kalau berbeda maka pesan Telegram akan menyebut versi yang bukan versi
#: yang tercatat di database - persis jenis ketidakcocokan yang membuat rekam
#: jejak tidak bisa dibaca sebagai rekam jejak.
FUTURES_MODEL_VERSION = "futures-f5"

#: Berapa simbol yang boleh berjalan bersamaan dalam satu tick.
#:
#: Terukur, bukan dipilih: satu simbol menghabiskan 1117 ms, dan 1102 ms di
#: antaranya menunggu jaringan. Yang menunggu bisa menunggu bersamaan.
#:
#: Enam adalah kompromi antara dua hal yang sama-sama nyata. Terlalu kecil dan
#: dua puluh simbol tetap memakan puluhan detik. Terlalu besar dan tiap simbol
#: melepas enam panggilan sekaligus - dua puluh simbol tanpa pagar berarti
#: seratus dua puluh permintaan serentak, dan itu bentuk burst yang
#: diperingatkan ``BinanceFuturesProvider.snapshot``: menabrak rate limit di
#: tengah set menghasilkan snapshot separuh.
#:
#: Dua puluh, jadi satu tick adalah satu gelombang.
#:
#: Angka ini pernah enam, dipilih untuk menjaga burst tetap kecil. Burst-nya
#: kemudian diukur lewat header ``x-mbx-used-weight-1m`` yang dikembalikan
#: bursa: satu simbol berharga delapan bobot dari batas 2400 per menit, jadi
#: dua puluh simbol sekitar 160 - tujuh persen. Kehati-hatian yang menahan di
#: enam ternyata menjaga jarak dari batas yang jauhnya tiga belas kali lipat.
#:
#: Pagarnya tetap ada dan tetap berarti: ia yang membuat jumlah permintaan
#: beredar berbanding lurus dengan angka ini alih-alih dengan panjang daftar
#: simbol. Menambah simbol jadi lima puluh tanpa pagar akan melipatgandakan
#: burst-nya; dengan pagar, ia tetap dua puluh.
PLAN_CONCURRENCY = 20


@dataclass(frozen=True, slots=True)
class PlanRun:
    """One pass over the requested symbols."""

    plans: tuple[FuturesPlan, ...] = field(default_factory=tuple)
    stored: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    #: Penilaian council per simbol, dibawa ke notifikasi (bagian PENILAIAN).
    #: Dikunci dengan simbol perpetual, sama seperti ``plans``, supaya keduanya
    #: bisa dipasangkan tanpa menerjemahkan ejaan simbol dua kali.
    councils: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def notes(self) -> dict[str, Any]:
        return {n.symbol: n for n in self.councils}

    def by_verdict(self, verdict: PlanVerdict) -> tuple[FuturesPlan, ...]:
        return tuple(p for p in self.plans if p.verdict is verdict)

    @property
    def actionable(self) -> tuple[FuturesPlan, ...]:
        return self.by_verdict(PlanVerdict.PLAN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered": len(self.plans),
            "plans": len(self.by_verdict(PlanVerdict.PLAN)),
            "refused": len(self.by_verdict(PlanVerdict.REFUSED)),
            "waited": len(self.by_verdict(PlanVerdict.WAIT)),
            "no_signal": len(self.by_verdict(PlanVerdict.NO_SIGNAL)),
            "stored": self.stored,
            "errors": list(self.errors),
        }


class FuturesPlanService:
    def __init__(
        self,
        *,
        deliberation: Any,
        council: Any,
        store: Any,
        universe: Any,
        provider: BinanceFuturesProvider | None = None,
        ingest: Any = None,
        council_store: Any = None,
        model_version: str = FUTURES_MODEL_VERSION,
        pembelajaran: Any = None,
        #: Pembaca ingatan pasar (PASAL 15.32). Opsional dan bawaan ``None``
        #: dengan alasan yang sama seperti ``pembelajaran``: keputusan tanpa
        #: konteks historis tetap keputusan yang sah (PASAL 15.37), bukan
        #: keputusan yang gagal.
        memory: Any = None,
        #: Katalog pola Phase 12 (PASAL 15.16). Opsional: konteks tanpa bagian
        #: pola tetap konteks yang sah, bukan konteks yang gagal.
        pola_store: Any = None,
        #: ``app_state``, untuk membaca putusan PASAL 15.44 per timeframe.
        #: Opsional: tanpa ini gerbangnya tidak pernah menutup, dan ingatan
        #: berperilaku seperti sebelum gerbang ada.
        app_state: Any = None,
        #: Penyimpan funding dan open interest sebagai deret
        #: (:class:`~aruna.db.repositories.futures_metrics.
        #: FuturesMetricsRepository`). ``None`` mengembalikan keadaan lama:
        #: kedua angka diambil tiap siklus lalu dibuang, dan dua pemicu bagian
        #: 16.2 tidak pernah bisa menyala.
        metrik: Any = None,
        concurrency: int = PLAN_CONCURRENCY,
    ) -> None:
        self._metrik = metrik
        self._deliberation = deliberation
        self._council = council
        self._store = store
        self._universe = universe
        self._provider = provider
        self._ingest = ingest
        self._council_store = council_store
        self._model_version = model_version
        #: Pembaca Phase 12/13 (PASAL 14.40, 14.41). Opsional dan bawaan
        #: ``None``: pemanggil lama menghasilkan keputusan tanpa lapisan itu,
        #: bukan keputusan yang gagal.
        self._pembelajaran = pembelajaran
        self._memory = memory
        self._pola_store = pola_store
        self._app_state = app_state
        self._concurrency = concurrency
        #: Provider yang dimiliki service ini ketika tidak ada yang disuntikkan.
        #: Dibuat sekali, dipakai lagi tiap tick - lihat :meth:`_venue`.
        self._owned: BinanceFuturesProvider | None = None

    async def refresh_evidence(self, horizon: Any, symbols: list[str]) -> list[str]:
        """Pull fresh candles before the council reads them.

        Without this the loop re-derives the same answer from frozen bars. It
        did exactly that for eleven hours: every interval's newest candle was
        from the last manual ``aruna fetch``, the background ingest writes
        *snapshots* rather than candles, and BTC came back SELL 0.699 on every
        single tick - one distinct decision across the whole run. (Measured
        while spot was still IDR-quoted; the frozen-candle failure it describes
        has nothing to do with the quote currency.)

        Failures are returned, not raised. Stale evidence is caught by the
        evidence-age gate in :func:`~aruna.futures.plan.build_plan`, which is
        where a refusal belongs; a fetch that could not reach the venue should
        not also end the tick that would have reported it.
        """
        if self._ingest is None:
            return ["no ingestor wired: candles are not being refreshed"]

        ingestor = self._ingest.ingestor(Market.CRYPTO)
        if ingestor is None:
            return ["no crypto ingestor: candles are not being refreshed"]

        spot = tuple(_spot_symbol(s) for s in symbols)

        # **Berurutan, dan itu sudah dicoba sebaliknya.**
        #
        # Penarikan candle dua puluh simbol menghabiskan 5,03 detik dan
        # berurutan di dalam `backfill`, jadi ia satu-satunya yang menghalangi
        # tick turun ke bawah lima detik. Versi paralelnya ditulis, diukur, dan
        # dicabut lagi: enam dari dua puluh simbol gagal dengan
        # ``OperationalError 1213: Deadlock found``. Upsert candle yang
        # bersamaan saling mengunci di InnoDB.
        #
        # Yang ditukar kalau dibiarkan bukan sekadar pesan error: simbol yang
        # gagal disegarkan tetap dianalisis, dari bar lama. Itu persis kegagalan
        # yang metode ini dibangun untuk mencegah - lihat catatan di atas soal
        # sebelas jam dengan satu keputusan yang sama.
        #
        # Bentuk yang benar ada, dan ia bukan ini: pisahkan penarikan dari
        # penulisan, tarik bersamaan, tulis berurutan. Penarikan adalah 90%
        # waktunya dan aman dijalankan bersamaan; penulisan cepat dan harus
        # bergiliran. Tapi keduanya menyatu di dalam `backfill`, yang dipakai
        # juga oleh penyegar candle upkeep untuk crypto spot dan IDX - jadi
        # memisahkannya adalah perubahan pada jalur bersama, bukan di sini.
        try:
            result = await ingestor.backfill((horizon,), symbols=spot)
        except ArunaError as exc:
            return [f"candle refresh failed: {exc}"]
        return list(result.failures)

    async def _simpan_metrik(self, snapshot: Any) -> None:
        """Simpan funding dan open interest sebagai deret (bagian 16.2).

        **Kenapa di sini.** Kedua angka ini diambil tiap siklus lalu dibuang,
        dan akibatnya dua dari tiga belas pemicu bagian 16.2 tidak pernah bisa
        menyala. Ini satu-satunya titik di seluruh ARUNA yang memegangnya.

        **Kegagalannya tidak pernah menyentuh rencana.** Yang dikerjakan fungsi
        ini pencatatan untuk fase lain; sebuah `INSERT` yang gagal tidak boleh
        membatalkan keputusan yang sedang dibuat di atasnya.
        """
        if self._metrik is None:
            return
        try:
            await self._metrik.simpan(snapshot)
        except Exception:
            log.exception("futures.metrik_gagal", symbol=snapshot.symbol)

    async def _resolve_asset(self, perpetual: str) -> Any:
        """Find the spot asset whose analysis backs this perpetual.

        Refuses rather than inventing one. A perpetual ARUNA does not already
        follow in spot has no technical history behind it, so a council verdict
        for it would be a verdict on nothing.
        """
        spot = _spot_symbol(perpetual)
        asset = await self._universe.find(Market.CRYPTO, spot)
        if asset is None:
            raise ArunaError(
                f"{perpetual} maps to {spot}, which is not in ARUNA's universe. "
                "The council has no evidence for it, so no plan is built - seed "
                "the symbol first rather than planning without analysis"
            )
        return asset

    async def _venue(self) -> BinanceFuturesProvider:
        """Satu adapter bursa untuk seluruh umur service, bukan satu per tick.

        **Kenapa ini penting, dan bukan sekadar kerapian.** Adapter menyimpan
        dua hal yang hanya berharga kalau ia hidup lebih lama dari satu tick:

        * spesifikasi kontrak seluruh bursa - 1,08 MB, satu unduhan yang dipakai
          dua puluh simbol (lihat ``BinanceFuturesProvider._exchange_info``);
        * kunci "leverageBracket butuh kredensial", yang menghapus dua puluh
          perjalanan sia-sia ke bursa per tick sesudah penolakan pertama.

        Versi sebelumnya membuat adapter baru pada setiap ``plan()`` dan
        menutupnya di akhir. Keduanya karena itu lahir kosong tiap tick, dan
        keduanya membayar ongkos penuh lagi - terukur: ``contract`` tetap 1,16
        detik rata-rata padahal cache-nya sudah ada dan bekerja. Cache yang
        dibuang sebelum sempat dipakai kedua kali bukan cache.

        Kolam koneksi HTTP-nya juga ikut hidup, jadi tick kedua tidak mengulang
        jabat tangan TLS ke bursa.
        """
        if self._provider is not None:
            return self._provider
        if self._owned is None:
            self._owned = BinanceFuturesProvider()
            await self._owned.open()
        return self._owned

    async def aclose(self) -> None:
        """Tutup adapter yang dimiliki service ini, kalau ada.

        Pemanggil yang menyuntikkan providernya sendiri memiliki dan menutup
        miliknya sendiri; yang itu tidak disentuh di sini.
        """
        if self._owned is not None:
            await self._owned.close()
            self._owned = None

    async def plan(
        self,
        symbols: list[str],
        *,
        horizon: Any,
        equity: Decimal,
        risk_pct: Decimal | None = None,
        reference: datetime | None = None,
    ) -> PlanRun:
        """Run the council, build a plan from its verdict, store the result."""
        provider = await self._venue()

        plans: list[FuturesPlan] = []
        councils: list[Any] = []
        errors: list[str] = []
        stored = 0
        now = reference or now_utc()

        try:
            # Penyegaran candle berangkat SEKARANG dan berjalan berbarengan
            # dengan pengambilan snapshot.
            #
            # Aturannya tidak berubah: tidak ada candle yang dibaca sebelum
            # penyegaran selesai. Yang berubah adalah apa yang boleh terjadi
            # sementara ia berjalan. `snapshot` mengambil mark, funding, open
            # interest, order book dan spesifikasi kontrak dari bursa - tidak
            # satupun berasal dari tabel candle - jadi menunggunya selesai dulu
            # hanyalah menganggur.
            #
            # `_plan_one` yang menahan gerbangnya: ia menunggu tugas ini
            # sebelum `build_context`, yang adalah pembaca candle pertama.
            # Terukur: 1,6 detik penyegaran yang dulu berdiri sendiri, sekarang
            # bersembunyi di balik ~1,1 detik pengambilan snapshot.
            tugas_segar = asyncio.create_task(
                self.refresh_evidence(horizon, symbols)
            )

            # Simbol berjalan bersamaan, dengan pagar.
            #
            # Terukur: satu simbol menghabiskan 1117 ms, dan 1102 ms di
            # antaranya adalah menunggu jaringan. Council-nya - sebelas agent,
            # tiga ronde, sanggahan dan veto - dua milidetik. Dua puluh simbol
            # berurutan karena itu 22 detik yang hampir seluruhnya diam.
            #
            # **Yang TIDAK diparalelkan: enam panggilan di dalam satu
            # snapshot.** ``BinanceFuturesProvider.snapshot`` menyatakan
            # urutannya disengaja - burst yang menabrak rate limit di tengah set
            # menghasilkan snapshot separuh, dan snapshot separuh adalah input
            # tidak koheren yang justru dicari FUTURES SPEC 46. Alasan itu masih
            # berlaku, jadi tiap simbol tetap mengumpulkan miliknya sendiri
            # secara berurutan; yang tumpang tindih hanya antar simbol.
            #
            # Pagarnya nyata, bukan hiasan: tanpa batas, dua puluh simbol
            # melepas seratus dua puluh permintaan sekaligus, dan itu bentuk
            # burst yang sama yang diperingatkan docstring tadi.
            batas = asyncio.Semaphore(max(1, self._concurrency))

            # PASAL 14.41, sekali untuk seluruh tick: berapa yang sudah
            # dipertaruhkan hari ini sama untuk tiap simbol, dan menanyakannya
            # dua puluh kali adalah dua puluh kueri dengan jawaban yang sama.
            jatah = await self._jatah_hari_ini(equity, now)

            # PASAL 15.32, sekali untuk seluruh tick. Ingatan yang boleh
            # dilihat dan win rate dasarnya sama untuk tiap simbol; yang
            # berbeda per simbol hanyalah kemiripannya, dan itu perhitungan
            # murni tanpa database.
            bahan_ingatan = await self._bahan_ingatan(horizon, now, symbols)

            async def _satu(symbol: str) -> Any:
                async with batas:
                    return await self._plan_one(
                        provider,
                        symbol,
                        horizon=horizon,
                        equity=equity,
                        risk_pct=risk_pct,
                        now=now,
                        segar=tugas_segar,
                        jatah=jatah,
                        bahan_ingatan=bahan_ingatan,
                    )

            hasil = await asyncio.gather(
                *(_satu(symbol) for symbol in symbols), return_exceptions=True
            )

            # Dicatat di depan daftar, bukan di belakang: bar yang gagal
            # disegarkan menjelaskan kenapa simbol-simbol di bawahnya
            # berperilaku aneh, jadi ia harus terbaca lebih dulu. Tugasnya pasti
            # sudah selesai di sini - tiap `_plan_one` menunggunya - tapi
            # `await` ini tetap ditulis supaya benar juga ketika `symbols`
            # kosong dan tidak ada yang pernah menunggunya.
            errors[:0] = [
                f"evidence: {problem}" for problem in await tugas_segar
            ]

            # Disimpan berurutan sesudahnya, bukan di dalam tugas yang
            # bersamaan: `stored` dan `errors` adalah keadaan bersama, dan
            # penulisan database yang saling menyalip akan menukar urutan
            # baris tanpa ada yang memintanya.
            for symbol, planned in zip(symbols, hasil, strict=True):
                if isinstance(planned, BaseException):
                    if not isinstance(planned, ArunaError):
                        raise planned
                    # One symbol's failure is not the run's. Recorded, not
                    # swallowed and not fatal.
                    errors.append(f"{symbol}: {planned}")
                    log.warning(
                        "futures.plan_failed", symbol=symbol, error=str(planned)[:200]
                    )
                    continue

                plan, session_id, note = planned
                plans.append(plan)
                councils.append(note)
                if self._store is not None:
                    try:
                        await self._store.save(
                            plan,
                            model_version=self._model_version,
                            # The link between a plan and the argument that
                            # produced it. The column has existed since
                            # migration 0015 and was written NULL every time,
                            # so a loss autopsy could reach the outcome and the
                            # forecast but never the debate - which is the only
                            # part there is anything to learn from.
                            council_session_id=session_id,
                        )
                        stored += 1
                    except ArunaError as exc:
                        errors.append(f"{symbol}: not stored: {exc}")

        finally:
            # Kalau tick ini berakhir lewat pengecualian, tugas penyegaran bisa
            # masih berjalan. Membiarkannya menghasilkan "Task exception was
            # never retrieved" di log - keluhan tentang keluhan, yang menutupi
            # kesalahan aslinya.
            if not tugas_segar.done():
                tugas_segar.cancel()
            with suppress(asyncio.CancelledError):
                await tugas_segar

        return PlanRun(
            plans=tuple(plans),
            stored=stored,
            errors=tuple(errors),
            councils=tuple(councils),
        )

    async def _jatah_hari_ini(self, equity: Decimal, now: datetime) -> Any:
        """Jatah risiko hari ini (PASAL 14.41), atau ``None`` kalau tak terbaca.

        **Satu kueri per tick, bukan per simbol.** Berapa yang sudah
        dipertaruhkan hari ini tidak berbeda antara BTCUSDT dan ETHUSDT, dan
        dua puluh simbol berarti dua puluh kueri dengan jawaban yang sama.

        Harinya dibatasi tengah malam WIB - sama dengan laporan harian, karena
        dua definisi "hari ini" di satu sistem berarti dua angka yang tidak
        pernah cocok dan tidak ada yang tahu mana yang benar.

        Kegagalannya diisolasi: satu baris keterangan yang hilang jauh lebih
        murah daripada rencana yang membawa entry dan stop.
        """
        from zoneinfo import ZoneInfo

        from aruna.futures.risk import jatah_harian

        try:
            awal = now.astimezone(ZoneInfo("Asia/Jakarta")).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            terpakai = await self._store.risiko_terpakai_since(awal)
            return jatah_harian(equity=equity, terpakai=terpakai)
        except Exception:
            log.exception("futures.jatah_harian_failed")
            return None

    async def _bahan_ingatan(
        self, horizon: Any, now: datetime, simbol: Sequence[str] = ()
    ) -> Any:
        """Ingatan yang boleh dilihat pada tick ini (PASAL 15.32, 15.39).

        **Satu kueri per tick, bukan per simbol.** Daftar ingatan dan win rate
        dasarnya sama untuk seluruh simbol; yang berbeda per simbol hanya
        kemiripannya, dan itu perhitungan murni.

        Timeframe-nya dipilih :func:`aruna.memory.lookup.horizon_ingatan`.
        Terukur 2026-08-21: ingatan pada 4h berjumlah **nol**, jadi hari ini ia
        meminjam 1h - dan ``dipinjam`` yang dipulangkannya wajib sampai ke
        pesan operator (PASAL 15.14).

        Kegagalannya diisolasi: ingatan adalah bukti tambahan, dan rencana yang
        membawa entry dan stop tidak boleh jatuh karenanya.
        """
        repo = getattr(self, "_memory", None)
        if repo is None:
            return None
        try:
            from aruna.memory.lookup import horizon_ingatan
            from aruna.memory.outcome import SAMPEL_MINIMUM, ringkas
            from aruna.memory.similarity import bandingkan

            tersedia = await repo.hitung_per_timeframe(
                as_of=now, market=Market.CRYPTO.value
            )
            timeframe, dipinjam = horizon_ingatan(
                horizon, tersedia=tersedia, minimum=SAMPEL_MINIMUM
            )
            if timeframe is None:
                return None

            rows, terpotong = await repo.cari_terhitung(
                as_of=now, market=Market.CRYPTO.value, timeframe=timeframe,
                limit=MEMORY_KANDIDAT,
            )
            daftar = [_ingatan_dari(r) for r in rows]

            catatan: list[str] = []
            if dipinjam:
                catatan.append(f"ingatan {timeframe} (belum ada di {horizon})")
            if terpotong:
                catatan.append(f"kandidat dipotong pada {MEMORY_KANDIDAT}")

            return _BahanIngatan(
                daftar=tuple(daftar),
                # Dasar dihitung dari daftar yang SAMA, jadi ia tunduk pada
                # `as_of` yang sama - titik banding yang bocor sama buruknya
                # dengan pencarian yang bocor.
                dasar=ringkas([(i, bandingkan(i.sidik, i.sidik)) for i in daftar]),
                timeframe=timeframe,
                dipinjam=dipinjam,
                catatan=tuple(catatan),
                as_of=now,
                # Ingatan terbaru per simbol - `rows` sudah urut
                # `resolved_at DESC`, jadi yang pertama per simbol yang
                # terbaru. Tidak ada kueri tambahan (PASAL 15.18).
                lintas_baris=tuple(
                    {"symbol": r["symbol"], "regime": r["regime"]}
                    for r in rows
                ),
                pola=await self._pola_phase12(),
                teknikal=await self._teknikal_sekarang(
                    repo, timeframe, now, simbol
                ),
                # PASAL 15.44. Dibaca sekali per tick dari `app_state`, bukan
                # dihitung di sini: penilaiannya adalah sapuan O(n^2) atas
                # ribuan ingatan dan hidup di fase upkeep harian.
                manfaat=await self._manfaat_timeframe(timeframe),
            )
        except Exception:
            log.exception("futures.memory_failed")
            return None

    async def _manfaat_timeframe(self, timeframe: str) -> Any:
        """Putusan PASAL 15.44 untuk satu timeframe, atau ``None``.

        Dibaca dari ``app_state`` karena yang **menghitungnya** adalah loop
        upkeep di ``aruna run`` sementara yang **memakainya** adalah proses ini.
        Menyimpannya di memori proses akan membuat gerbangnya diam-diam terbuka
        di sisi yang justru mengambil keputusan - persis kesalahan yang sempat
        membuat ingatan tersambung ke proses yang salah.

        ``None`` ketika belum pernah dinilai, dan ``None`` tidak menggerbangi
        apa pun: penilaian yang hilang bukan penilaian yang buruk.
        """
        state = getattr(self, "_app_state", None)
        if state is None:
            return None
        try:
            from aruna.memory.manfaat import KUNCI_STATE, dari_json

            return dari_json(await state.get(KUNCI_STATE)).get(timeframe)
        except Exception:
            log.exception("futures.manfaat_failed")
            return None

    async def _teknikal_sekarang(
        self, repo: Any, timeframe: str, now: datetime, simbol: Sequence[str]
    ) -> dict[str, dict[Any, str]]:
        """Dimensi teknikal kondisi sekarang, per simbol (PASAL 15.5).

        Dihitung dari **bar yang sama** yang dipakai memperkaya ingatan - lewat
        ``kandil_sampai``, jadi batas SPEC 24 dan PASAL 15.39 berlaku sama di
        kedua sisi. Membandingkan volatilitas yang dihitung dua cara berbeda
        akan membandingkan dua besaran yang kebetulan bernama sama.

        Satu kueri candle per simbol per tick. Kegagalan satu simbol tidak
        menghentikan yang lain: hasilnya sekadar sidik jari yang lebih tipis.
        """
        from aruna.core.enums import Horizon
        from aruna.memory.lookup import simbol_pasar
        from aruna.memory.teknikal import dimensi_teknikal

        hasil: dict[str, dict[Any, str]] = {}
        for s in simbol:
            pasar = simbol_pasar(s)
            try:
                from aruna.analysis.series import CandleSeries, InsufficientData

                baris = await repo.kandil_sampai(
                    symbol=pasar, timeframe=timeframe, sampai=now, limit=200
                )
                if len(baris) < 60:
                    continue
                seri = CandleSeries.from_rows(
                    baris, market=Market.CRYPTO, symbol=pasar,
                    interval=Horizon(timeframe),
                )
                hasil[s] = dimensi_teknikal(seri)
            except (InsufficientData, ValueError):
                continue
            except Exception:
                log.exception("futures.teknikal_failed", symbol=s)
                continue
        return hasil

    async def _pola_phase12(self) -> tuple[Any, ...]:
        """Katalog pola Phase 12 (PASAL 15.16), dibaca sekali per tick.

        **Dibaca, tidak dihitung ulang.** PASAL 15.33 memisahkan keduanya:
        Phase 12 menemukan pola, Phase 15 mengingatnya. Menghitung ulang di
        sini akan menghasilkan dua katalog yang bisa berselisih.

        Repositorinya opsional - pemanggil tanpa `learning12` menghasilkan
        konteks tanpa bagian pola, bukan konteks yang gagal.
        """
        repo = getattr(self, "_pola_store", None)
        if repo is None:
            return ()
        try:
            from aruna.learning.adaptive import LEARNING_VERSION
            from aruna.memory.pola import dari_baris

            # Versi **mesin pembelajaran**, bukan versi aplikasi. Pola
            # tersimpan di bawah `learn-12.0`; mencarinya dengan versi app
            # menghasilkan nol baris dari tabel berisi 368 - kosong yang
            # terbaca seperti "belum ada pola", bukan seperti kunci salah.
            # Kesalahan yang sama persis pernah terjadi di `PembacaPembelajaran`.
            baris = await repo.notable_patterns(model_version=LEARNING_VERSION)
            return tuple(p for p in (dari_baris(r) for r in baris or ()) if p)
        except Exception:
            log.exception("futures.pola_failed")
            return ()

    async def _plan_one(
        self,
        provider: BinanceFuturesProvider,
        symbol: str,
        *,
        horizon: Any,
        equity: Decimal,
        risk_pct: Decimal | None,
        now: datetime,
        segar: Any = None,
        jatah: Any = None,
        bahan_ingatan: Any = None,
    ) -> tuple[FuturesPlan, int | None, CouncilNote]:
        """The plan, the id of the council session behind it, and its note."""
        snapshot = await provider.snapshot(symbol)
        await self._simpan_metrik(snapshot)

        # The council reads spot evidence for the same asset. Its verdict is a
        # direction, nothing more - the entry, stop, size, leverage and
        # liquidation all come from the futures layer, which can and does refuse
        # a direction the council was happy with.
        asset = await self._resolve_asset(symbol)

        # **Gerbang candle.** Di bawah baris ini, semuanya membaca tabel candle;
        # di atasnya, tidak ada yang membacanya. `segar` adalah tugas penyegaran
        # yang `plan()` lepas berbarengan dengan snapshot di atas, dan menunggu
        # di sini - bukan sebelum snapshot - yang membuat kedua penantian itu
        # tumpang tindih.
        #
        # Menghapus baris ini tidak akan membuat apa pun error. Ia hanya akan
        # membuat council membaca bar tick sebelumnya, diam-diam, dan itu
        # kegagalan sebelas jam yang dijelaskan di `refresh_evidence`.
        if segar is not None:
            await segar
        context = await self._deliberation.build_context(
            asset, Market.CRYPTO, horizon
        )
        if context is None:
            raise ArunaError(
                f"no decision context for {symbol}: the council cannot form a "
                "view, so there is no direction to build a plan around"
            )
        verdict = self._council.convene(context)

        # Stored, not discarded. The loop ran roughly two hundred councils
        # before this line existed and kept none of them: `council_sessions`
        # held nothing newer than the last manual `aruna council` run, so the
        # argument behind every plan was gone the moment the tick ended - and
        # SPEC 25's loss autopsy has nothing to read without it.
        session_id = None
        if self._council_store is not None:
            try:
                session_id = await self._council_store.save(asset.id, verdict)
            except ArunaError as exc:
                log.warning("futures.council_not_stored", error=str(exc)[:200])

        # Penilaiannya ikut dibawa keluar, bukan dikirim sebagai pesan sendiri.
        # Dulu baris ini mendorong log perdebatan lengkap ke Telegram sebagai
        # notifikasi terpisah - dua pesan untuk satu peristiwa - dan operator
        # meminta yang kedua dihentikan. Yang dipertahankan adalah isinya:
        # confidence, disagreement, dan hasil pemilihan sekarang tercetak di
        # dalam pesan plan (lihat `_penilaian` di aruna.futures.notify).
        #
        # Dikunci dengan `symbol` - simbol perpetual - dan bukan
        # `verdict.symbol`, yang adalah ejaan spot. Keduanya menyebut aset yang
        # sama, dan memakai yang salah membuat pencariannya tidak pernah cocok.
        note = note_of(verdict, symbol=symbol)

        # PHASE 13: faktor risiko yang HANYA ada di konteks council -
        # volatilitas, rezim, berita, korelasi. Terukur: cakupan penilaian
        # risiko naik dari 62% ke 87% ketika keduanya digabung, dan 62% adalah
        # angka yang tepat di ambang - satu faktor hilang dan risikonya
        # berhenti bisa dinilai sama sekali.
        #
        # Dititipkan di catatan council karena catatan itu SUDAH mengalir ke
        # notifier, sementara konteksnya berhenti di sini. Jalur kedua untuk
        # penumpang yang sama berarti dua jalur yang harus tetap sepakat.
        #
        # Kegagalannya diisolasi: satu faktor risiko yang tidak terbaca tidak
        # boleh menghentikan rencana yang membawa entry, stop dan target.
        try:
            from aruna.risk.context_readings import readings_from_context

            bacaan = readings_from_context(context)
            if bacaan:
                note = replace(note, risk_readings=dict(bacaan))
        except Exception:
            log.exception("futures.risk_context_failed", symbol=symbol)

        note = attach_regime(note, context, symbol=symbol)
        # PASAL 14.41. Dihitung sekali di `plan()` dan dibagikan ke seluruh
        # simbol tick ini - lihat `_jatah_hari_ini` untuk kenapa.
        note = attach_jatah(note, jatah)
        # PASAL 15.32: ingatan memberi konteks, Phase 14 yang memutuskan.
        #
        # Bahannya sudah dibaca sekali di `plan()` - yang terjadi di sini murni
        # perbandingan, tanpa satu pun kueri. Membacanya per simbol berarti
        # dua puluh kali kueri yang sama dengan jawaban yang sama.
        note = attach_memory(
            note,
            _konteks_historis(
                bahan_ingatan, note, symbol=symbol,
                # Dari **vonis**, bukan dari `note.split` - `VoteSplit` tidak
                # punya bidang `decision`, dan membacanya dari sana mengoper
                # string kosong selamanya: pengaruh yang selalu NEUTRAL, tanpa
                # satu pun error yang menyebutkannya.
                arah=str(getattr(getattr(verdict, "decision", None), "value", "")
                         or getattr(verdict, "decision", "") or ""),
            ),
        )
        # Dibaca bertahan: test double yang dibangun lewat `__new__` tidak
        # memiliki bidang opsional ini, dan penyambungannya sudah dijaga
        # sendiri oleh `test_dipanggil_dari_jalur_hidup` dan
        # `test_loop_futures_menerimanya` - jadi `getattr` di sini menoleransi
        # palsu yang sederhana tanpa menyembunyikan rangkaian yang putus.
        note = await attach_pembelajaran(
            note,
            getattr(self, "_pembelajaran", None),
            market=Market.CRYPTO,
            interval=horizon,
        )
        note = attach_decision_readings(note, context, verdict, symbol=symbol)
        note = await attach_timeframes(
            note, self._deliberation, asset, horizon=horizon, symbol=symbol
        )

        # Spot evidence and the perpetual are now both priced in USDT
        # (PASAL 6), so this ratio sits near 1.0 instead of near 1/17,800. The
        # rebase is kept anyway, for two reasons that survived the currency
        # change.
        #
        # First, near 1.0 is not 1.0: a perpetual trades at a basis to spot,
        # premium or discount, and a stop derived from a spot support level
        # belongs at the mark's equivalent of that level, not at the spot
        # number. Second, and more important, `_rebase_ratio` returns None when
        # either reference price is missing, and every figure derived from it
        # then becomes None too - which makes the plan refuse instead of
        # placing a stop at an invented distance.
        #
        # It was originally written for a units bug worth recording: when spot
        # was quoted in IDR, an unconverted ATR put the stop millions of
        # "dollars" from the entry, the risk budget divided by that distance
        # floored the quantity to zero, and every plan was refused as "below
        # the venue minimum" - a units error wearing the costume of a risk
        # decision. That bug is gone with the IDR quote. The guard it left
        # behind is not, and deleting it would restore the failure mode the
        # second reason above describes.
        rebase = _rebase_ratio(context, snapshot)
        atr = _atr_of(context, rebase)
        invalidation, levels = _structure_of(
            context, side_of(verdict.decision), rebase
        )

        funding_history: list[Any] = []
        if snapshot.funding is not None:
            try:
                funding_history = await provider.funding_history(symbol, limit=100)
            except ArunaError:
                # A missing history means the trend factors report insufficient
                # sample, which is what they should say. It is not fatal.
                funding_history = []

        # FUTURES SPEC 34. Read from the stored record, which already holds
        # every plan and every refusal with a timestamp. Absent a store there is
        # no record to read, and the plan says nothing about sequence rather
        # than pretending the sequence was clean.
        discipline = None
        history: list[Any] = []
        if self._store is not None:
            try:
                history = await self._store.recent(limit=DISCIPLINE_HISTORY)
                discipline = review(
                    history=history,
                    reference=now,
                    horizon_hours=_hours_of(horizon),
                )
            except ArunaError as exc:
                log.warning("futures.discipline_unavailable", error=str(exc)[:200])

        plan = build_plan(
            signal_id=_signal_id(symbol, horizon, now),
            decision=verdict.decision,
            snapshot=snapshot,
            equity=equity,
            atr=atr,
            horizon_hours=_hours_of(horizon),
            invalidation_level=invalidation,
            structure_levels=levels,
            funding_history=funding_history,
            risk_pct=risk_pct,
            hostile_regime=_hostile(context),
            discipline=discipline,
            # The newest settled bar behind the council's verdict. Without it
            # the plan cannot tell whether the direction is as fresh as the
            # price it is quoted against.
            evidence_as_of=context.as_of,
            reference=now,
        )

        # A second pass, now that the numbers exist.
        #
        # The first review can only see the sequence: it runs before the plan
        # is built, so `proposed_risk` and `proposed_leverage` are None and the
        # three escalation patterns skip on their own second conjunct. Revenge,
        # size escalation and leverage escalation were therefore unreachable in
        # production - a discipline engine that could only ever report
        # overtrading, while still saying `clean: true` about everything else.
        #
        # The size and leverage are only known after the gates have run, so the
        # honest order is: build, then judge the sequence the built plan sits
        # in. Caveats are not part of the fingerprint, so replacing them here
        # changes nothing that must not change.
        if plan.actionable and history and plan.size_detail is not None:
            second = review(
                history=history,
                reference=now,
                horizon_hours=_hours_of(horizon),
                proposed_risk=plan.size_detail.risk_amount,
                proposed_leverage=plan.leverage,
            )
            if second.findings:
                plan = replace(
                    plan,
                    caveats=tuple(
                        dict.fromkeys((*plan.caveats, *second.as_caveats()))
                    ),
                )

        # PASAL 14.39, dan **sebelum** kedua pembacanya di bawah. Terukur di
        # produksi 2026-08-21: mutunya dihitung di baris terakhir fungsi ini
        # dan tetap dilaporkan hilang, karena `observe_decision` dan
        # `catat_jejak` sama-sama membaca `note.quality` lebih dulu. Kode yang
        # benar, dipanggil dari jalur hidup, hasilnya tetap nol.
        note = attach_quality(
            note, context=context, verdict=verdict, plan=plan, now=now_utc()
        )
        observe_decision(
            context=context, verdict=verdict, plan=plan, note=note, symbol=symbol
        )
        catat_jejak(
            context=context, verdict=verdict, plan=plan, note=note,
            model_version=self._model_version,
        )
        # PASAL 14.29, dan sengaja DI SINI - bukan bersama rezim dan skor di
        # atas. Penjelasan disusun untuk arah yang sudah diputuskan, dan arah
        # itu baru ada sesudah rencananya jadi. Menyusunnya lebih awal berarti
        # menjelaskan keputusan yang belum diambil.
        note = attach_explanation(note, verdict, context, plan)
        return plan, session_id, note


def _spot_symbol(perpetual: str) -> str:
    """``BTCUSDT`` -> ``BTC/USDT``, the symbol ARUNA's universe knows.

    The council's evidence is the spot analysis ARUNA already runs; the
    perpetual is the instrument the plan is built for. Mapping here rather than
    duplicating the universe keeps one source of truth for what ARUNA follows.

    Since PASAL 6 moved spot to USDT this is nearly the identity function, and
    the temptation is to delete it. It stays because the quote loop is doing
    real work: ``BTCUSDC`` and ``BTCBUSD`` are separate perpetuals that must
    still resolve to the one ``BTC/USDT`` spot series ARUNA tracks. Without the
    loop those two would look up ``BTCUSDC/USDT`` and be refused as unknown.
    """
    base = perpetual.upper()
    for quote in ("USDT", "USDC", "BUSD"):
        if base.endswith(quote):
            base = base[: -len(quote)]
            break
    return f"{base}/USDT"


def _signal_id(symbol: str, horizon: Any, moment: datetime) -> str:
    import hashlib

    code = getattr(horizon, "value", str(horizon))
    basis = f"{symbol}|{code}|{moment.isoformat()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _hours_of(horizon: Any) -> float:
    duration = getattr(horizon, "duration", None)
    if duration is not None:
        return duration.total_seconds() / 3600
    return 4.0


def _rebase_ratio(context: Any, snapshot: Any) -> Decimal | None:
    """Restate a spot-quoted price on the perpetual's own price scale.

    **A proportion transfers between price scales; an absolute figure does
    not.** An ATR of 308 USDT is 0.49% of a 63,000 USDT spot price; against a
    mark of 63,150 the same 0.49% is 309.5 USDT. The percentage is the part
    that means the same thing on both scales, and the ratio is what carries it
    across.

    Both sides are USDT since PASAL 6, so the ratio is close to 1 and the
    correction is small - it is the perpetual's basis to spot, not a currency
    conversion. Small is not zero, and it moves with funding.

    Returns ``None`` when either reference price is missing, which makes the
    caller treat the derived figures as unavailable rather than pass an
    unrebased one through.
    """
    spot = getattr(context.state, "last_price", None)
    mark = snapshot.reference_price
    if spot is None or mark is None or spot <= 0 or mark <= 0:
        return None
    return Decimal(str(mark)) / Decimal(str(spot))


def _atr_of(context: Any, rebase: Decimal | None) -> Decimal | None:
    """ATR from the technical snapshot, rebased, or ``None``.

    ``None`` makes the plan refuse rather than place a stop at a round number,
    which is the correct outcome: every risk figure downstream is derived from
    the stop, so an invented stop makes all of them arithmetic on an invention.
    An unconvertible ATR is treated the same way, for the same reason.
    """
    value = context.value("atr")
    if value is None or value <= 0 or rebase is None:
        return None
    return Decimal(str(value)) * rebase


def _structure_of(
    context: Any, side: PositionSide, rebase: Decimal | None
) -> tuple[Decimal | None, tuple[Decimal, ...]]:
    """The level that must hold, and the levels ahead.

    A long is invalidated below support and targets resistance; a short is the
    mirror. Handing the engine the wrong side's levels would place the stop
    where the target belongs.

    Rebased for the same reason the ATR is: a support level measured on the
    spot series sits at the mark's equivalent price, not at the spot number,
    and a missing reference makes the levels unavailable rather than
    approximate.
    """
    structure = context.structure
    if structure is None or rebase is None:
        return None, ()

    support = tuple(Decimal(str(lvl.price)) * rebase for lvl in structure.support)
    resistance = tuple(
        Decimal(str(lvl.price)) * rebase for lvl in structure.resistance
    )

    if side is PositionSide.LONG:
        invalidation = max(support) if support else None
        ahead = tuple(sorted(resistance))[:MAX_STRUCTURE_LEVELS]
    elif side is PositionSide.SHORT:
        invalidation = min(resistance) if resistance else None
        ahead = tuple(sorted(support, reverse=True))[:MAX_STRUCTURE_LEVELS]
    else:
        return None, ()
    return invalidation, ahead


#: Timeframe yang dibaca bersamaan (PASAL 14.4).
#:
#: Empat, dan bukan lima. ``1m`` sengaja tidak ikut meskipun candle-nya paling
#: banyak tersimpan: struktur satu menit hampir seluruhnya derau, dan sebuah
#: baris "1m SHORT" yang berumur satu menit akan tampil sejajar dengan konteks
#: harian di peta yang sama.
#:
#: Yang tersisa memetakan langsung ke PASAL 14.5: ``1d`` konteks tren di atas
#: horizon, ``4h`` horizon keputusannya, ``1h`` dan ``15m`` waktu masuk.
MTF_INTERVALS: tuple[str, ...] = ("15m", "1h", "4h", "1d")


async def attach_timeframes(
    note: Any, deliberation: Any, asset: Any, *, horizon: Any, symbol: str = ""
) -> Any:
    """Titipkan peta lintas timeframe di catatan council (PASAL 14.4).

    Kegagalannya diisolasi seperti penumpang lain di catatan yang sama: peta
    yang tidak terbaca berarti satu blok keterangan yang hilang, bukan rencana
    yang batal.
    """
    try:
        from aruna.core.enums import Horizon
        from aruna.decision.timeframes import Lintas

        intervals = tuple(
            Horizon(x) for x in MTF_INTERVALS if x != horizon.value
        )
        lain = await deliberation.timeframe_readings(
            asset, Market.CRYPTO, intervals
        )
        # Timeframe horizonnya sendiri sudah dianalisis untuk council; ia
        # dibaca ulang di sini supaya peta memuat barisnya. Tanpa baris itu
        # `Lintas` tidak punya keputusan sama sekali - lihat PASAL 14.7.
        sendiri = await deliberation.timeframe_readings(
            asset, Market.CRYPTO, (horizon,)
        )
        semua = (*lain, *sendiri)
        if not semua:
            return note
        return replace(
            note,
            lintas=Lintas(horizon=horizon.value, readings=tuple(semua)),
        )
    except Exception:
        log.exception("futures.timeframes_failed", symbol=symbol)
        return note


def observe_decision(
    *, context: Any, verdict: Any, plan: Any, note: Any, symbol: str
) -> None:
    """Catat bagaimana keputusan ini benar-benar disusun (PASAL 14.3, 14.25).

    **Mengukur, dan hanya mengukur.** Tidak ada satu pun jalur dari fungsi ini
    yang bisa membatalkan rencana atau menahan pesan - lihat catatan di
    :mod:`aruna.decision.observe` untuk kenapa gerbang ketiga tidak boleh
    dipasang sebelum dua yang pertama diukur.

    Keluarannya satu baris log terstruktur per simbol per tick. Itu sekitar dua
    ribu baris sehari pada dua puluh simbol - banyak untuk dibaca manusia, dan
    tepat untuk dijumlahkan. Yang dicari darinya adalah distribusi: langkah
    mana yang tidak pernah ada, dan seberapa sering daftar periksanya akan
    menahan seandainya ia jadi gerbang.

    Seluruhnya dibungkus. Sebuah pengamat yang menjatuhkan rencana adalah
    kebalikan dari gunanya.
    """
    try:
        from aruna.decision.observe import amati

        amatan = amati(
            context=context, verdict=verdict, plan=plan, note=note
        )
        log.info(
            "decision.observed",
            symbol=symbol,
            **amatan.summary(),
            **_kelengkapan_fase(context=context, verdict=verdict,
                                plan=plan, note=note),
            # PASAL 15.41: tiap signal harus bisa menjawab memory mana yang
            # dipakai dan seberapa besar sumbangannya.
            **_jejak_memory(getattr(note, "memory", None)),
        )
    except Exception:
        log.exception("futures.decision_observe_failed", symbol=symbol)


def _validasi(note: Any, bidang: str) -> bool:
    """Apakah validasi model luring benar-benar pernah dijalankan (PASAL 14.40).

    Dibaca dari lintasan backtest yang **tersimpan**, bukan dari keberadaan
    mesinnya: sebuah mesin backtest yang lengkap dan tidak pernah dijalankan
    tidak memvalidasi apa pun, dan itu keadaan sistem ini sampai 2026-08-21.

    ``holdout_included`` nol bukan kekurangan data melainkan pilihan: SPEC 38
    menyisihkan ekor holdout justru supaya ia tidak dilihat saat memilih
    varian. Backtest yang menyisihkannya **belum** menguji di luar sampel, dan
    menandainya hadir akan mengklaim validasi yang sengaja tidak dilakukan.
    """
    try:
        lari = getattr(getattr(note, "pembelajaran", None), "backtest", None)
        if not isinstance(lari, dict):
            return False
        return bool(lari.get(bidang))
    except Exception:  # noqa: BLE001 - lihat aruna.decision.observe
        return False


def _kelengkapan_fase(
    *, context: Any, verdict: Any, plan: Any, note: Any
) -> dict[str, Any]:
    """Berapa banyak masukan Phase 11/12/13 yang benar-benar sampai (14.39-14.41).

    Dibaca dari **bukti yang ada di objeknya**, sama seperti
    :func:`aruna.decision.observe.amati` - sebuah lapisan yang berjalan dan
    tidak meninggalkan jejak apa pun tidak bisa dibedakan dari lapisan yang
    tidak berjalan, dan untuk tujuan pengukuran ini keduanya memang sama.

    **Yang hilang disebut namanya**, bukan cuma dihitung: angka gabungan memberi
    tahu bahwa ada yang tidak sampai; hanya namanya yang memberi tahu apa yang
    harus dicari.

    Daftarnya panjang dan sebagian besar akan kosong hari ini. Itu bukan
    kegagalan pengukuran - itu hasilnya, dan hasil itu yang menentukan lapisan
    mana yang pantas disambungkan berikutnya.
    """
    from aruna.decision.integration import Masukan, periksa

    def _ada(obj: Any, *jalan: str) -> bool:
        try:
            nilai: Any = obj
            for nama in jalan:
                if nilai is None:
                    return False
                nilai = getattr(nilai, nama, None)
            return nilai is not None
        except Exception:  # noqa: BLE001 - lihat aruna.decision.observe
            return False

    def _kunci(obj: Any, bidang: str, kunci: str) -> bool:
        """Apakah sebuah **kunci** ada di dalam peta bacaan.

        Sebagian lapisan sampai ke keputusan sebagai baris di dalam ``dict``,
        bukan sebagai atribut - ``risk_readings`` salah satunya. Membacanya
        lewat :func:`_ada` mencari atribut yang tidak akan pernah ada, dan
        melaporkan lapisan yang berjalan sebagai lapisan yang hilang.

        Nilai **nol dihitung ada**: ``news_risk: 0.0`` berarti berita sudah
        dinilai dan hasilnya nol, bukan berarti berita tidak terbaca. Kelas
        kesalahan yang sama dengan ``confidence=0`` dan ``side='FLAT'``.
        """
        try:
            peta = getattr(obj, bidang, None)
            return isinstance(peta, dict) and peta.get(kunci) is not None
        except Exception:  # noqa: BLE001 - lihat aruna.decision.observe
            return False

    def _isi(note: Any, bidang: str) -> bool:
        """Apakah bidang snapshot Phase 12/13 ini benar-benar berisi.

        Dinilai dari **isinya**, bukan dari keberadaan snapshotnya: sebuah
        ``Pembelajaran()`` kosong adalah pembacaan yang berhasil dan tidak
        menemukan apa-apa, dan melaporkannya sebagai "terbaca" akan membuat
        Phase 12 terlihat penuh justru ketika ia tidak punya satu pun baris.
        """
        try:
            snapshot = getattr(note, "pembelajaran", None)
            return bool(getattr(snapshot, bidang, None))
        except Exception:  # noqa: BLE001 - lihat aruna.decision.observe
            return False

    def _ditanya(context: Any) -> bool:
        """Apakah pertanyaan performa strategi Phase 12 benar-benar diajukan.

        ``Selection`` yang memulangkan ``strategy=None`` beserta daftar
        ``rejected`` adalah bukti tabel performanya **dibaca** - itu yang
        menolak kandidatnya. Membaca ``strategy.evidence``, yang hanya terisi
        kalau ada kandidat yang menang, melaporkan Phase 12 sebagai tidak
        terbaca justru pada saat ia bekerja dengan benar.

        Yang benar-benar berarti "tidak ditanya" adalah ``None``: pemilihnya
        tidak dirangkai sama sekali.
        """
        return getattr(context, "strategy", None) is not None

    tersedia: dict[Masukan, bool] = {
        # PASAL 14.39 - Phase 11
        Masukan.SIGNAL_QUALITY: _ada(note, "quality"),
        Masukan.AGENT_RELIABILITY: _ada(verdict, "judgement"),
        Masukan.MARKET_REGIME: _ada(context, "regime"),
        Masukan.DATA_FRESHNESS: _ada(plan, "evidence_as_of")
        or _ada(context, "as_of"),
        Masukan.ANOMALY_DETECTION: _ada(plan, "integrity"),
        Masukan.CONFIDENCE_CALIBRATION: _ada(note, "confidence"),
        Masukan.AGENT_ACCOUNTABILITY: _ada(verdict, "opinions"),
        # PASAL 14.40 - Phase 12, lewat snapshot yang dibaca sekali per jendela.
        Masukan.PATTERN_DISCOVERY: _isi(note, "patterns"),
        Masukan.STRATEGY_PERFORMANCE: _ditanya(context),
        Masukan.AGENT_SPECIALIZATION: _isi(note, "specialists"),
        Masukan.CHAMPION: _isi(note, "champion"),
        Masukan.CHALLENGER: _isi(note, "challenger"),
        # **Yang dibaca adalah keberadaan validasinya, bukan angkanya.**
        #
        # Keduanya sempat ditulis `False` tanpa syarat, dengan alasan yang
        # benar waktu itu: `backtest_runs` berisi nol baris sepanjang umur
        # sistem, karena `aruna backtest` menghitung fold walk-forward lalu
        # membuangnya. Alasannya sudah tidak berlaku - perintahnya sekarang
        # menyimpan, dan backtest sungguhan sudah dijalankan atas 11.240
        # keputusan.
        #
        # Peringatan lama tetap berlaku dan dijaga test terpisah: angka
        # backtest tidak berubah antar tick, jadi ia **tidak boleh** masuk ke
        # pesan. Net PnL backtest yang tercetak di sebelah entry akan terbaca
        # operator sebagai perkiraan hasil rencana ini.
        Masukan.WALK_FORWARD: _validasi(note, "walk_forward"),
        Masukan.OUT_OF_SAMPLE: _validasi(note, "holdout_included"),
        Masukan.DRIFT_DETECTION: _isi(note, "drift"),
        Masukan.LEARNING_RESULTS: _ada(context, "strategy"),
        # PASAL 14.41 - Phase 13
        Masukan.RISK_SCORE: _ada(note, "risk_readings"),
        Masukan.RISK_REWARD: _ada(plan, "net_rr")
        or _ada(plan, "economics", "net_rr"),
        Masukan.SL_QUALITY: _ada(plan, "stop_detail"),
        Masukan.TP_QUALITY: _ada(plan, "target"),
        Masukan.LEVERAGE_ANALYSIS: _ada(plan, "leverage_decision"),
        Masukan.LIQUIDATION_RISK: _ada(plan, "liquidation"),
        # Mesinnya ada sejak Phase 4 dan tabelnya terisi; ``context.correlation``
        # tidak pernah diisi di mana pun - termasuk jalur spot. Snapshot yang
        # membacanya.
        Masukan.CORRELATION_RISK: _isi(note, "correlation")
        or _ada(context, "correlation"),
        Masukan.EXPOSURE: _ada(plan, "notional"),
        # Keduanya sampai sebagai baris di dalam `risk_readings`, bukan sebagai
        # atribut konteks. Versi pertama mencari `context.volatility` - yang
        # tidak pernah ada di ARUNA - dan melaporkan Phase 13 jauh lebih kosong
        # daripada kenyataannya.
        Masukan.VOLATILITY: _kunci(note, "risk_readings", "volatility"),
        Masukan.NEWS_RISK: _kunci(note, "risk_readings", "news_risk")
        or _ada(context, "news"),
        # Dititipkan di catatan council, bukan di konteks: konteksnya berhenti
        # di service ini sementara catatannya sudah mengalir ke notifier - dan
        # jatah harian adalah angka yang operatornya perlu baca, bukan cuma
        # angka yang dihitung. Bacaan konteks dipertahankan sebagai jalur kedua
        # yang sah, bukan sebagai jaring pengaman untuk yang pertama.
        Masukan.DAILY_RISK_BUDGET: _ada(note, "risk_budget")
        or _ada(context, "risk_budget"),
    }
    # **Rencana yang berhenti sebelum sizing tidak dituduh kehilangan sizing.**
    #
    # Terukur pada 2026-08-20: laporan yang sama menyebut Phase 13 27% pada
    # rencana WAIT dan 73% pada rencana PLAN. Selisihnya bukan perbedaan
    # perakitan - keenam masukan di bawah lahir bersama ukuran posisi, dan
    # rencana yang menolak sebelum sizing memang tidak punya satu pun.
    #
    # Dinilai dari **entry**, bukan dari nama vonisnya: yang menentukan adalah
    # sejauh mana rencananya berjalan, dan sebuah nama vonis baru tidak boleh
    # diam-diam mengubah arti angka ini.
    sizing = _ada(plan, "entry")
    tak_berlaku: set[Masukan] = set() if sizing else {
        Masukan.RISK_REWARD,
        Masukan.SL_QUALITY,
        Masukan.TP_QUALITY,
        Masukan.LEVERAGE_ANALYSIS,
        Masukan.LIQUIDATION_RISK,
        Masukan.EXPOSURE,
    }
    hasil = periksa(tersedia, tak_berlaku=tak_berlaku)
    return {
        "integrasi_pct": hasil.pct,
        "integrasi_fase": {f.value: p for f, p in hasil.per_fase.items()},
        "integrasi_hilang": [m.value for m in hasil.hilang],
        # Dilaporkan terpisah, bukan disembunyikan: pembaca berikutnya harus
        # bisa melihat berapa banyak yang dikeluarkan dari penyebut, atau
        # "tidak berlaku" berubah menjadi tempat sampah yang tak terperiksa.
        "integrasi_tak_berlaku": [m.value for m in hasil.tak_berlaku],
    }


#: Peran agent -> jenis bukti (PASAL 14.29).
#:
#: Dipetakan, bukan ditebak dari namanya: ``Sumber`` tidak punya anggota untuk
#: berita maupun fundamental, dan menambahkannya hanya supaya pemetaannya rapi
#: akan mengubah daftar jenis bukti demi kenyamanan penulis kode.
#:
#: Yang tidak ada di sini jatuh ke ``AGENT`` - jujur, dan tidak menaikkan
#: hitungan sumber secara palsu: sembilan agent yang semuanya jatuh ke ``AGENT``
#: tetap satu sumber, dan PASAL 14.29 menuntut dua yang berbeda.
#: Satu tempat yang memutuskan, bukan dua. Versi pertama memakai bawaan
#: ``dict.get(..., "AGENT")`` **dan** bawaan ``getattr(Sumber, nama, AGENT)``,
#: dan yang kedua diam-diam menangkap apa pun yang lolos dari yang pertama -
#: jadi peta ini bisa dikosongkan seluruhnya tanpa satu test pun merah.
def _sumber_peran(peran: str):
    from aruna.decision.explanation import Sumber

    return {
        "STRUCTURE": Sumber.STRUKTUR,
        "VOLUME": Sumber.VOLUME,
        "MOMENTUM": Sumber.MOMENTUM,
        "REGIME": Sumber.REZIM,
        "RISK": Sumber.RISIKO,
    }.get(peran, Sumber.AGENT)


def _penjelasan(verdict: Any, arah: Any, context: Any) -> Any:
    """Susun KENAPA LONG / KENAPA SHORT dari opini agent (PASAL 14.29).

    Opini yang **searah** dengan keputusan menjadi alasan; yang melawan menjadi
    ``against``. Keduanya dicetak di blok yang sama - bukti yang melawan dan
    harus dicari sendiri sama saja dengan bukti yang tidak disebutkan.

    Kalimat yang ditolak :func:`check_text` - kosong, generik, atau memuat klaim
    terlarang PASAL 51 - **dilewati satu per satu**, bukan menjatuhkan seluruh
    blok. Satu agent yang menulis "terlihat bagus" tidak boleh menghapus delapan
    alasan yang sungguhan.

    Memulangkan ``None`` kalau syarat dua sumber tidak terpenuhi. Itu bukan
    kegagalan: sebuah keputusan yang seluruh dukungannya datang dari satu jenis
    bukti memang belum punya penjelasan berlapis, dan mencetaknya seolah punya
    akan membuat PASAL 14.29 menjadi formulir.
    """
    from aruna.decision.explanation import (
        Alasan,
        ExplanationError,
        Penjelasan,
        Sumber,
    )

    searah: list[Any] = []
    melawan: list[Any] = []
    for opini in getattr(verdict, "opinions", ()) or ():
        peran = str(getattr(getattr(opini, "role", None), "value", "") or "")
        sumber = _sumber_peran(peran)
        keputusan = str(
            getattr(getattr(opini, "decision", None), "value", "") or ""
        )
        setuju = keputusan in {"BUY", "LONG"} if arah.value == "LONG" else (
            keputusan in {"SELL", "SHORT"}
        )
        lawan = keputusan in {"BUY", "SELL", "LONG", "SHORT"} and not setuju
        if not setuju and not lawan:
            # Agent yang menunggu atau abstain tidak punya bukti berarah untuk
            # disumbangkan ke sisi mana pun.
            continue
        for kalimat in getattr(opini, "reasoning", ()) or ():
            try:
                alasan = Alasan(source=sumber, text=str(kalimat))
            except ExplanationError as exc:
                # Layak dicatat: ini kalimat tulisan ARUNA sendiri, dan yang
                # ditolak berarti ada lapisan yang menulis klaim terlarang.
                log.warning("futures.reason_rejected", sebab=str(exc))
                continue
            (searah if setuju else melawan).append(alasan)

    strategi = getattr(getattr(context, "strategy", None), "strategy", None)
    nama_strategi = str(getattr(strategi, "code", None) or strategi or "").strip()
    if nama_strategi:
        with suppress(ExplanationError):
            searah.append(
                Alasan(
                    source=Sumber.STRATEGI,
                    text=f"strategi {nama_strategi} cocok dengan rezim ini",
                )
            )

    try:
        return Penjelasan(
            decision=arah, reasons=tuple(searah), against=tuple(melawan)
        )
    except ExplanationError:
        # Kurang dari dua sumber berbeda. Bukan kegagalan - lihat docstring.
        return None


def attach_explanation(note: Any, verdict: Any, context: Any, plan: Any) -> Any:
    """Titipkan penjelasan PASAL 14.29 di catatan council."""
    try:
        from aruna.decision.final import FinalError, arah_dari

        try:
            arah = arah_dari(getattr(plan, "side", None))
        except FinalError:
            return note
        return replace(
            note,
            explanation=_penjelasan(verdict, arah, context),
            # Menumpang perjalanan yang sama, karena sumbernya sama: strategi
            # Phase 12 hanya ada di konteks council, dan PASAL 14.37 butuh
            # namanya di jalur notifikasi.
            strategy=_nama_strategi(context),
        )
    except Exception:
        log.exception("futures.explanation_failed")
        return note


#: Berapa kandidat ingatan dibaca per tick. Terukur 2026-08-21: 5.377 ingatan
#: 15m dan 2.189 di 1h, jadi enam ribu memuat seluruhnya hari ini. Kalau suatu
#: saat tercapai, `cari_terhitung` mencatat `memory.cari_terpotong` dan
#: catatannya sampai ke jejak audit - tidak ada pemotongan yang diam.
MEMORY_KANDIDAT = 6000


@dataclass(frozen=True, slots=True)
class _BahanIngatan:
    """Ingatan yang boleh dilihat pada satu tick, dibaca sekali."""

    daftar: tuple[Any, ...]
    dasar: Any
    timeframe: str
    dipinjam: bool
    catatan: tuple[str, ...]
    as_of: datetime
    #: Ingatan terbaru per simbol, untuk konteks lintas aset (PASAL 15.18).
    lintas_baris: tuple[dict[str, Any], ...] = ()
    #: Katalog pola Phase 12 (PASAL 15.16). Dibaca sekali per tick.
    pola: tuple[Any, ...] = ()
    #: Dimensi teknikal kondisi **sekarang**, per simbol (PASAL 15.5).
    #:
    #: Tanpa ini kelimanya hanya ada di sisi ingatan, dan `bandingkan`
    #: mengeluarkannya dari penyebut karena tidak terbaca di satu sisi -
    #: perkayaan yang tidak pernah sampai ke satu pun keputusan hidup.
    teknikal: dict[str, dict[Any, str]] = field(default_factory=dict)
    #: Putusan PASAL 15.44 untuk timeframe yang dipakai tick ini, atau ``None``
    #: kalau belum pernah dinilai.
    #:
    #: Terukur 2026-08-21: ingatan membantu di 15m (+14) dan **tidak** di 1h
    #: (-7) - dan 1h justru yang dipinjam jalur keputusan ini. Tanpa bidang
    #: ini, ARUNA memberi bobot pada bukti yang evaluasinya sendiri bilang
    #: tidak menambah apa-apa.
    manfaat: Any = None


def _ingatan_dari(row: dict[str, Any]) -> Any:
    """Satu baris ``market_memories`` menjadi :class:`Ingatan`.

    Mendelegasikan ke pembangun di repositori, tempat ``KOLOM_DIMENSI``
    tinggal. Salinan kedua di sini pernah ada, dan dua pembangun yang harus
    tetap sepakat adalah dua yang suatu saat tidak - yang tidak sepakat
    menghasilkan sidik jari yang ditulis penuh lalu dibaca kosong.
    """
    from aruna.db.repositories.memory import ingatan_dari_baris

    return ingatan_dari_baris(row)


def _konteks_historis(bahan: Any, note: Any, *, symbol: str, arah: str) -> Any:
    """Konteks historis untuk satu simbol (PASAL 15.6, 15.30).

    Murni: tidak ada satu pun kueri di sini. Bahannya sudah dibaca sekali oleh
    :meth:`FuturesPlanService._bahan_ingatan`.

    Sidik jari kondisi sekarang disusun dari apa yang **benar-benar ada** di
    catatan council. Yang tidak terbaca menjadi ``UNKNOWN``, dan
    :func:`aruna.memory.similarity.bandingkan` mengeluarkannya dari penyebut -
    itu sebabnya cakupan dilaporkan terpisah dari skor.
    """
    if bahan is None or not bahan.daftar:
        return None
    try:
        from aruna.memory.context import susun
        from aruna.memory.dimensions import Dimensi
        from aruna.memory.fingerprint import Sidik
        from aruna.memory.lookup import simbol_pasar
        from aruna.memory.ranking import peringkat
        from aruna.memory.similarity import AMBANG_MIRIP, bandingkan

        bacaan = getattr(note, "risk_readings", None) or {}
        sidik = Sidik.dari_konteks(
            # Dijembatani: ingatan mengeja `BTC/USDT`, futures `BTCUSDT`.
            # Nol ingatan bersimbol perpetual - terukur 2026-08-21.
            symbol=simbol_pasar(symbol),
            market=Market.CRYPTO.value,
            timeframe=bahan.timeframe,
            regime=getattr(note, "regime", None),
            risk_level=bacaan.get("risk_level"),
            news=bacaan.get("news_state"),
            quality=getattr(note, "quality", None),
            spread_bps=bacaan.get("spread_bps"),
        )
        # Kelima dimensi teknikal kondisi sekarang, dihitung sekali per tick di
        # `_bahan_ingatan`. Tanpa baris ini keduanya tidak pernah bertemu:
        # ingatan punya tiga belas dimensi, kondisi sekarang delapan, dan
        # `bandingkan` mengeluarkan yang tidak terbaca di satu sisi.
        sidik = sidik.dengan(bahan.teknikal.get(symbol, {}))

        cocok = []
        for ingatan in bahan.daftar:
            mirip = bandingkan(sidik, ingatan.sidik)
            if mirip.skor >= AMBANG_MIRIP:
                cocok.append((ingatan, mirip))
        urut = peringkat(cocok, as_of=bahan.as_of)

        from aruna.memory.lintas import baca_lintas
        from aruna.memory.peristiwa import baca_peristiwa
        from aruna.memory.pola import cocokkan

        return susun(
            arah_sekarang=arah,
            cocok=[(i, m) for i, m, _ in urut],
            dasar=bahan.dasar,
            as_of=bahan.as_of,
            catatan=bahan.catatan,
            # PASAL 15.18: berapa aset kripto lain yang serezim. Dibaca dari
            # ingatan terbaru tiap simbol - tidak ada kueri tambahan.
            lintas=baca_lintas(
                bahan.lintas_baris,
                rezim_sekarang=sidik.nilai.get(Dimensi.REGIME),
            ),
            # PASAL 15.16: pola Phase 12 yang menerangkan kondisi ini. DIBACA,
            # tidak dihitung ulang - PASAL 15.33 memisahkan keduanya.
            pola=cocokkan(
                bahan.pola, symbol=symbol, timeframe=bahan.timeframe, arah=arah
            ),
            # PASAL 15.15: nasib keputusan lama pada keadaan berita seperti
            # sekarang. Terukur: berita NEGATIVE menghasilkan win rate 23%
            # melawan 42-50% pada keadaan lain.
            peristiwa=baca_peristiwa(
                bahan.daftar, keadaan=sidik.nilai.get(Dimensi.NEWS)
            ),
            # PASAL 15.44: apakah ingatan terbukti membantu di timeframe ini.
            # Yang digerbangi bobotnya terhadap keputusan, bukan haknya
            # dilihat - seluruh bukti di atas tetap disusun dan tetap dikirim.
            manfaat=bahan.manfaat,
        )
    except Exception:
        log.exception("futures.memory_context_failed", symbol=symbol)
        return None


def _jejak_memory(konteks: Any) -> dict[str, Any]:
    """Jejak audit ingatan untuk satu keputusan (PASAL 15.41, 15.45).

    Menumpang ``decision.observed`` yang sudah satu baris per simbol per tick -
    baris log kedua berarti dua jalur yang harus tetap sepakat.

    **Selalu memulangkan seluruh bidangnya**, termasuk saat ingatan tidak
    terbaca. Bidang yang hilang membuat "tidak ada ingatan" tidak bisa
    dibedakan dari "fasenya tidak jalan" - keluarga cacat yang sama dengan
    ``upkeep.news`` yang dulu hanya dicatat saat ada isinya.
    """
    if konteks is None:
        return {
            "memory_pengaruh": "UNKNOWN",
            "memory_kontribusi": 0,
            "memory_kasus": 0,
            "memory_ids": [],
            "memory_digerbangi": False,
        }
    try:
        return {
            "memory_pengaruh": konteks.pengaruh.value,
            "memory_kontribusi": konteks.kontribusi,
            "memory_kasus": konteks.ringkasan.total,
            # Dibatasi di `susun` - PASAL 14.30 pernah menghasilkan satu baris
            # log 6.000 karakter di proyek ini.
            "memory_ids": list(konteks.memory_ids),
            # PASAL 15.44. Tanpa bidang ini, jejak audit mencatat `NEUTRAL`
            # tanpa cara membedakan sejarah yang memang diam dari pendapat
            # sejarah yang sengaja tidak diberi bobot - dan seluruh guna
            # gerbang ini adalah bisa dilihat kapan ia bekerja.
            "memory_digerbangi": bool(getattr(konteks, "digerbangi", False)),
        }
    except Exception:
        log.exception("futures.memory_jejak_failed")
        return {
            "memory_pengaruh": "UNKNOWN",
            "memory_kontribusi": 0,
            "memory_kasus": 0,
            "memory_ids": [],
            "memory_digerbangi": False,
        }


def attach_memory(note: Any, konteks: Any) -> Any:
    """Titipkan konteks historis Phase 15 di catatan council (PASAL 15.32).

    ``None`` menghasilkan catatan apa adanya. Ingatan yang tidak terbaca bukan
    ingatan yang kosong: PASAL 15.37 menyatakan ARUNA tetap menganalisis normal
    tanpa kecocokan historis, dan yang dilarang adalah **mengarang** bukti
    historis - bukan berjalan tanpanya.
    """
    if konteks is None:
        return note
    try:
        return replace(note, memory=konteks)
    except Exception:
        log.exception("futures.memory_attach_failed")
        return note


def attach_jatah(note: Any, jatah: Any) -> Any:
    """Titipkan jatah risiko hari ini di catatan council (PASAL 14.41).

    ``None`` menghasilkan catatan apa adanya, bukan catatan dengan jatah nol:
    nol berarti ARUNA belum mempertaruhkan apa pun hari ini - sebuah
    pengukuran - sementara tidak terbaca berarti tidak ada yang tahu (§13.26).
    """
    if jatah is None:
        return note
    try:
        return replace(note, risk_budget=jatah)
    except Exception:
        log.exception("futures.jatah_attach_failed")
        return note


async def attach_pembelajaran(
    note: Any, pembaca: Any, *, market: Any, interval: Any
) -> Any:
    """Titipkan snapshot Phase 12/13 di catatan council (PASAL 14.40, 14.41).

    Terukur di produksi 2026-08-20: Phase 12 hanya 22% sampai ke keputusan.
    Pattern discovery, spesialisasi agent, champion, challenger, dan drift
    semuanya sudah dibangun dan tersimpan, dan tidak satu pun dibaca oleh
    lapisan yang memutuskan.

    Pembacanya sendiri yang menyimpan cache lima menit, jadi memanggilnya per
    simbol tidak menghasilkan kueri per simbol - lihat
    :class:`aruna.learning.snapshot.PembacaPembelajaran`.

    Pemanggil tanpa pembaca menghasilkan catatan apa adanya, bukan kegagalan:
    sebuah lapisan pembelajaran yang menjadi syarat agar council bisa
    memutuskan akan mengubah kegagalan pembelajaran menjadi kegagalan analisis.
    """
    if pembaca is None:
        return note
    try:
        from aruna.learning.snapshot import Pembelajaran

        hasil = await pembaca.baca(market=market, interval=interval)
        if not isinstance(hasil, Pembelajaran):
            return note
        return replace(note, pembelajaran=hasil)
    except Exception:
        log.exception("futures.pembelajaran_failed")
        return note


def _mutu_signal(*, context: Any, verdict: Any, plan: Any, now: Any) -> Any:
    """Signal quality PASAL 11.1 untuk rencana futures ini, atau ``None``.

    Memakai penilai yang **sama** dengan jalur spot - bukan salinan. Dua penilai
    mutu untuk satu sistem akan berselisih pada hari salah satunya diperbaiki,
    dan yang kalah dalam perselisihan seperti itu selalu yang tidak diuji.

    ``None`` kalau tidak bisa dihitung, dan itu bukan kegagalan: mutu yang
    dihitung dari ketiadaan bukti adalah angka yang dikarang (§13.26), dan ia
    akan tercetak seolah-olah ARUNA mengukurnya.

    **Memulangkan laporannya, bukan skornya.** Versi sebelumnya berakhir dengan
    ``float(skor.score)`` - dua puluh faktor dihitung, satu angka disimpan,
    sembilan belas dibuang di baris terakhir. Lima di antaranya adalah keyakinan
    yang bagian 18.17 wajibkan disebut terpisah.
    """
    if context is None:
        return None
    try:
        from aruna.signals.quality import score_signal

        jam = float(getattr(plan, "horizon_hours", 0) or 0)
        return score_signal(
            context=context,
            split=getattr(verdict, "split", None),
            opinions=getattr(verdict, "opinions", None),
            entry=getattr(plan, "entry", None),
            stop=getattr(plan, "stop", None),
            target=getattr(plan, "target", None),
            now=now,
            horizon_sec=jam * 3600 if jam else 3600.0,
        )
    except Exception:
        log.exception("futures.quality_failed")
        return None


def attach_quality(
    note: Any, *, context: Any, verdict: Any, plan: Any, now: Any
) -> Any:
    """Titipkan signal quality di catatan council (PASAL 14.39)."""
    try:
        return replace(
            note,
            mutu=_mutu_signal(
                context=context, verdict=verdict, plan=plan, now=now
            ),
        )
    except Exception:
        log.exception("futures.attach_quality_failed")
        return note


def _nama_strategi(context: Any) -> str:
    """Kode strategi yang dipilih, atau kalimat kosong kalau tidak ada.

    Phase 12 memulangkan ``Selection`` yang **boleh** berisi ``strategy=None``:
    itu keadaan yang wajar dan sering - "tidak ada yang terbukti lebih baik dari
    rata-rata". Kalimat kosong adalah laporan yang benar untuk itu.
    """
    pilihan = getattr(context, "strategy", None)
    strategi = getattr(pilihan, "strategy", None)
    if strategi is None:
        return ""
    return str(getattr(strategi, "code", None) or strategi or "").strip()


def _ringkas(nilai: Any, *, batas: int = 160) -> str:
    """Ringkas sebuah objek jadi satu baris yang muat di log.

    **Terukur pada pengukuran pertama:** menulis ``repr`` penuh sembilan
    ``AgentOpinion`` beserta seluruh ``EvidenceRef``-nya menghasilkan satu baris
    log sepanjang lebih dari enam ribu karakter, sebelas kali per tick, sembilan
    puluh enam tick sehari. Itu bukan jejak yang bisa dibaca; itu berkas log
    yang tidak bisa dibuka.

    Yang dipotong adalah **panjangnya, bukan keberadaannya**: jumlah anggota
    tetap dilaporkan, jadi "sembilan agent" tidak pernah berubah jadi "tidak ada
    agent". Yang hilang hanya rinciannya, dan rinciannya sudah tersimpan utuh di
    `council_votes` dan `agent_objections`.
    """
    if nilai is None:
        return "UNKNOWN"
    if isinstance(nilai, (list, tuple)):
        if not nilai:
            return "0"
        return f"{len(nilai)}: " + "; ".join(
            str(x)[:40] for x in nilai[:3]
        )[:batas]
    teks = str(nilai).strip()
    if not teks:
        return "UNKNOWN"
    return teks[:batas]


def catat_jejak(
    *,
    context: Any,
    verdict: Any,
    plan: Any,
    note: Any,
    model_version: str = "",
) -> None:
    """Satu baris yang cukup untuk menyusun ulang keputusannya (PASAL 14.30).

    **Kenapa log, bukan tabel.** Dua puluh tiga bidang per rencana per lima
    belas menit adalah ribuan baris sehari; §26 melarang INSERT tiap tick ke
    SQL, dan tabel baru menuntut migrasi plus pembacanya. Log terstruktur sudah
    punya keduanya. Kalau kelak terbukti dipakai, memindahkannya ke tabel
    adalah pekerjaan yang jelas - sebaliknya tidak.

    **Yang tidak tersedia ditulis UNKNOWN** (§13.26), bukan nol dan bukan
    kalimat kosong: sebuah jejak yang terlihat lengkap sambil bohong lebih
    buruk daripada jejak yang mengaku bolong.

    Rencana tanpa arah tidak dijejaki sama sekali. Jejak PASAL 14.30 adalah
    jejak sebuah *keputusan*, dan rencana WAIT belum memutuskan apa pun -
    mencatatnya hanya mengisi log dengan baris yang seluruh bidang berarahnya
    UNKNOWN.
    """
    try:
        from aruna.decision.final import FinalError, arah_dari
        from aruna.decision.trail import Jejak, record, required_fields

        try:
            arah = arah_dari(getattr(plan, "side", None))
        except FinalError:
            return

        def _teks(nilai: Any) -> str:
            teks = str(nilai) if nilai is not None else ""
            return teks.strip() or "UNKNOWN"

        stop_detail = getattr(plan, "stop_detail", None)
        lintas = getattr(note, "lintas", None)
        sumber: dict[Jejak, str] = {
            Jejak.SIGNAL_ID: _teks(getattr(plan, "signal_id", None)),
            Jejak.TIMESTAMP: _teks(getattr(plan, "created_at", None)),
            Jejak.ASSET: _teks(getattr(plan, "symbol", None)),
            Jejak.MARKET: "FUTURES",
            Jejak.TIMEFRAMES: _ringkas(
                tuple(
                    f"{b.interval}:{b.decision.value}"
                    for b in (getattr(lintas, "readings", ()) or ())
                )
            ),
            Jejak.REGIME: _teks(getattr(note, "regime", None)),
            # **Suaranya ada di catatan council, bukan di vonisnya.** Versi
            # pertama membacanya dari `verdict.split`, dan seluruh sebelas jejak
            # pada pengukuran pertama melaporkan UNKNOWN - lapisan yang jelas
            # berjalan tercatat sebagai lapisan yang hilang.
            Jejak.AGENT_VOTES: _ringkas(getattr(note, "split", None)),
            Jejak.AGENT_ARGUMENTS: _ringkas(
                tuple(
                    f"{o.role.value}:{o.decision.value}"
                    for o in (getattr(verdict, "opinions", ()) or ())
                )
            ),
            Jejak.PROTESTS: _ringkas(
                tuple(
                    f"{o.accuser.value}->{o.target.value}:{o.ground}"
                    for o in (
                        getattr(
                            getattr(verdict, "protest", None), "objections", ()
                        )
                        or ()
                    )
                )
            ),
            Jejak.VETO: _ringkas(
                getattr(getattr(verdict, "veto", None), "vetoes", None)
            ),
            Jejak.COUNCIL_DECISION: _teks(
                getattr(getattr(verdict, "decision", None), "value", None)
            ),
            # Signal quality SPEC 11 lahir di jalur signal, bukan di jalur
            # futures - rencana perpetual tidak pernah punya angkanya. UNKNOWN
            # di sini adalah laporan yang benar (§13.26), bukan bidang yang
            # lupa diisi.
            Jejak.SIGNAL_QUALITY: _teks(getattr(note, "quality", None)),
            # Keyakinan nol adalah pengukuran, bukan ketiadaan - jadi ia lewat
            # `_teks` yang hanya menolak `None`, bukan lewat kebenarannya.
            Jejak.CONFIDENCE: _teks(getattr(note, "confidence", None)),
            Jejak.RISK_SCORE: _ringkas(getattr(note, "risk_readings", None)),
            Jejak.STRATEGY: _ringkas(
                getattr(getattr(context, "strategy", None), "strategy", None)
                or getattr(context, "strategy", None)
            ),
            # **``FuturesPlan`` tidak punya bidang ini.** Versinya dipegang
            # service dan diberikan ke `save()` sebagai argumen terpisah, jadi
            # membacanya dari rencana menghasilkan UNKNOWN selamanya - dan
            # PASAL 14.30 menuntutnya untuk bisa menyusun ulang keputusan.
            Jejak.MODEL_VERSION: _teks(model_version),
            Jejak.DECISION_SCORE: _ringkas(getattr(note, "decision_readings", None)),
            Jejak.FINAL_DECISION: arah.value,
            Jejak.ENTRY: _teks(getattr(plan, "entry", None)),
            Jejak.SL: _teks(getattr(plan, "stop", None)),
            Jejak.TP: _teks(getattr(plan, "target", None)),
            Jejak.INVALIDATION: _teks(
                getattr(stop_detail, "invalidation", None)
            ),
            Jejak.EXPIRATION: _teks(getattr(plan, "horizon_hours", None)),
        }
        rekaman = record(arah, {j: sumber[j] for j in required_fields(arah)})
        log.info(
            "decision.trail",
            **{j.name.lower(): nilai for j, nilai in rekaman.values},
        )
    except Exception:
        # Sebuah pencatat yang menjatuhkan rencana adalah kebalikan dari
        # gunanya - sama seperti `observe_decision` di atas.
        log.exception("decision.trail_failed")


def attach_decision_readings(
    note: Any, context: Any, verdict: Any, *, symbol: str = ""
) -> Any:
    """Titipkan komponen Decision Score di catatan council (PASAL 14.16).

    **Terukur, dan angkanya menentukan bagaimana ini dipakai.** Pada bentuk
    data yang realistis, cakupan komponen berarah berkisar 30% sampai 89%, dan
    kasus yang paling sering di produksi - struktur belum menentukan trennya -
    berhenti di 52%: di bawah ambang cakupan, jadi skornya tidak bisa disebut
    skor sama sekali. Bahkan pada cakupan 89% dengan tren jelas, sebuah setup
    tanpa penembusan hanya mencapai +29 dari ambang +60.

    Karena itu skor ini **dicetak sebagai keterangan dan tidak menggerbang apa
    pun**. Menjadikannya syarat kirim akan membungkam ARUNA hampir sepenuhnya,
    dan yang terlihat bukan pasar yang sepi melainkan sebuah ambang yang tidak
    pernah diuji terhadap kenyataan.

    Potongan risiko dan berita tidak dihitung di sini - lihat
    :attr:`aruna.futures.debate.CouncilNote.decision_readings`.
    """
    try:
        from aruna.decision.context_readings import readings_from_analysis
        from aruna.decision.score import Arah
        from aruna.notify.verdict import public_decision

        teknis = getattr(context, "technical", None)
        arah = Arah(public_decision(verdict.decision))
        bacaan = readings_from_analysis(
            structure=getattr(teknis, "structure", None),
            readings=getattr(teknis, "readings", None),
            decision=arah,
            split=note.split,
        )
    except Exception:
        log.exception("futures.decision_readings_failed", symbol=symbol)
        return note
    return replace(note, decision_readings=dict(bacaan)) if bacaan else note


def attach_regime(note: Any, context: Any, *, symbol: str = "") -> Any:
    """Titipkan rezim pasar di catatan council (PASAL 14.26).

    Rezimnya sudah dihitung Phase 3 dan sudah dipakai :func:`_hostile` di modul
    ini, tapi tidak pernah sampai ke pembacanya - jadi operator melihat entry,
    stop, dan target tanpa tahu pasar macam apa yang menghasilkannya.

    **Fungsi tersendiri, bukan beberapa baris di dalam ``_plan_one``.** Versi
    pertama menulisnya di sana, dan tidak ada satu pun test yang bisa
    menjangkaunya tanpa membangun seluruh konteks council - yang berarti
    penyambungannya bisa dicabut tanpa satu test pun berubah merah. Itu persis
    keluarga cacat yang paling sering muncul di sistem ini: kode yang ditulis,
    diekspor, diuji, dan tidak pernah dilewati jalur hidup.

    Kegagalannya diisolasi: satu bidang rezim yang bentuknya tak terduga tidak
    boleh menghentikan rencana yang membawa angka keputusan.
    """
    try:
        nama = _regime_name(context)
    except Exception:
        log.exception("futures.regime_read_failed", symbol=symbol)
        return note
    return replace(note, regime=nama) if nama else note


def _regime_name(context: Any) -> str:
    """Nama rezim pasar, atau string kosong kalau tidak terbaca.

    Kosong dan bukan ``"UNKNOWN"``: ``UNCERTAIN`` adalah nama rezim yang
    sungguhan di sistem ini, dan sebuah rezim yang mengaku tidak diketahui
    terbaca hampir sama dengan rezim yang memang bernama tidak pasti. Yang satu
    berarti Phase 3 tidak melapor; yang lain berarti Phase 3 melapor bahwa
    pasarnya sedang tidak jelas. Pemanggil yang menerima string kosong tidak
    mencetak barisnya sama sekali.
    """
    regime = getattr(context, "regime", None)
    if regime is None:
        return ""
    return str(getattr(regime.regime, "value", regime.regime) or "")


def _hostile(context: Any) -> bool:
    """Regimes in which leverage is exposed to more than its own thesis."""
    regime = context.regime
    if regime is None:
        return False
    name = getattr(regime.regime, "value", str(regime.regime))
    return name in {"HIGH_VOLATILITY", "CHOPPY", "UNCERTAIN"}


__all__ = [
    "MAX_STRUCTURE_LEVELS",
    "FuturesPlanService",
    "PlanRun",
    "attach_decision_readings",
    "attach_regime",
    "observe_decision",
]
