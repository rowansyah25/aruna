"""Push notifications for the futures loop (FUTURES SPEC 48, 50, 51).

Two messages, and nothing else:

* an **alert** when a plan clears every gate - side, entry, stop, target,
  leverage, liquidation price, size and net reward-to-risk;
* a **daily report**, once per day, of what the loop did.

**The numbers are in the push because the operator asked for them there**, with
the trade-off stated first: a message that arrives unasked with prices in it
reads as an instruction, and the step where the reader chooses to look is the
step it removes. FUTURES SPEC 50 and 51 govern the words; they cannot govern a
phone buzzing at three in the morning. That is a decision about how the operator
wants to be told, and it is theirs.

What is not negotiable is what travels *with* those numbers. **Leverage never
goes without the liquidation price** - on its own it says how large the position
is and not how far wrong it may go before the exchange closes it, which is the
most dangerous half-fact this system can produce. The stop's meaning goes with
it too, and so do the caveats.

**Only PLAN verdicts.** Pushing every refusal would be about ninety-six
messages a day, and a notification that always fires is ignored exactly when it
finally matters.

**And at most one per symbol per horizon.** Re-planning every fifteen minutes
means one setup can clear sixteen times in a four-hour window; sixteen messages
about one idea is the same noise arriving by a different route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from aruna.core.clock import JAKARTA, wib
from aruna.core.logging import get_logger
from aruna.decision.output import KAKI
from aruna.futures.plan import (
    FORBIDDEN_CLAIMS,
    ForbiddenClaim,
    PlanVerdict,
    _price,
)
from aruna.notify.verdict import (
    LONG,
    MARK,
    NO_SIGNAL,
    SHORT,
    TEST_BANNER,
    render_votes,
)
from aruna.signals.pemisahan import pisahkan, render_terpisah

#: Sisi posisi futures ke kosakata publik PASAL 1. Dieja, bukan ditebak dari
#: namanya: ``PositionSide`` kebetulan mengeja LONG dan SHORT sama, dan
#: kebetulan bukan jaminan - sisi baru yang tidak terpetakan akan jatuh ke
#: penanda netral, bukan ke warna yang salah.
SIDE_MARK: dict[str, str] = {"LONG": LONG, "SHORT": SHORT}

log = get_logger("aruna.futures.notify")

#: Zona waktu operator. **Didefinisikan di :mod:`aruna.core.clock`.**
#:
#: Empat modul menuliskan ``ZoneInfo("Asia/Jakarta")`` sendiri-sendiri sampai
#: 2026-08-23, tiga di antaranya sambil mengimpor `wib` dari modul yang sama
#: yang sudah punya :data:`~aruna.core.clock.JAKARTA`. Satu zona waktu yang
#: ditulis di empat tempat adalah empat tempat yang harus diubah kalau ARUNA
#: kelak melayani operator di zona lain.
WIB = JAKARTA

#: Menit terakhir hari WIB. Laporan harian dibuka di sini, bukan pada tick mana
#: pun yang kebetulan lewat: operator memintanya "jam 23:59 - 00:00", dan
#: laporan yang datang di jam acak tidak bisa dibaca sebagai penutup hari.
DAILY_OPENS = time(23, 59)

#: Berapa lama jendela itu tetap terbuka setelah dibuka.
#:
#: Loop berdetak tiap 900 detik. Sebuah jendela selebar satu menit akan
#: terlewat pada hampir setiap hari - tick jam 23:52 dan tick berikutnya jam
#: 00:07, dan menit yang diminta lewat di antaranya tanpa ada yang melihat.
#: Jadi jendelanya dibuka pukul 23:59 dan tetap terbuka dua jam, supaya
#: laporannya terlambat sedikit alih-alih hilang.
#:
#: Batas atasnya sama pentingnya dengan batas bawahnya. Tanpa penutup, sebuah
#: proses yang menyala pukul sepuluh pagi akan menemukan "kemarin belum
#: dilaporkan" dan langsung mengirim laporan kemarin - persis kebiasaan yang
#: membuat setiap restart terasa seperti spam.
DAILY_GRACE = timedelta(hours=2)

#: Kunci di ``app_state``. Nilainya ``{"date": "YYYY-MM-DD"}``, tanggal WIB
#: hari yang **dilaporkan**, bukan tanggal saat mengirim.
FUTURES_DAILY_KEY = "futures_daily_sent"


def due_day(now: datetime) -> date | None:
    """Hari WIB mana yang jatuh tempo dilaporkan sekarang, kalau ada.

    Mengembalikan ``None`` di luar jendela - dan itulah yang membuat restart
    tengah hari tidak mengirim apa-apa.
    """
    lokal = now.astimezone(WIB)
    buka = lokal.replace(
        hour=DAILY_OPENS.hour, minute=DAILY_OPENS.minute, second=0, microsecond=0
    )
    if lokal >= buka:
        return lokal.date()
    kemarin = buka - timedelta(days=1)
    if lokal - kemarin < DAILY_GRACE:
        # Sudah lewat tengah malam: yang dilaporkan tetap hari kemarin, hari
        # yang baru saja selesai. Tanggalnya sama dengan yang akan dipakai
        # seandainya laporannya terkirim tepat pukul 23:59, jadi keterlambatan
        # tidak pernah menghasilkan laporan kedua untuk hari yang sama.
        return kemarin.date()
    return None


def day_window(day: date) -> tuple[datetime, datetime]:
    """Batas satu hari WIB penuh, sebagai dua momen dengan zona waktu."""
    awal = datetime.combine(day, time(0, 0), tzinfo=WIB)
    return awal, awal + timedelta(days=1)


def _guard(text: str) -> str:
    """FUTURES SPEC 51, enforced on the push path too.

    The rendered plan is guarded and the JSON is guarded; a notification that
    skipped the check would be the one message that reaches the reader without
    passing it - and the only one that arrives unasked.
    """
    lowered = text.lower()
    for claim in FORBIDDEN_CLAIMS:
        if claim in lowered:
            raise ForbiddenClaim(
                f"menolak mengirim notifikasi yang memuat {claim!r} "
                "(FUTURES SPEC 51)"
            )
    return text


@dataclass(slots=True)
class PlanNotifier:
    """Decides what to say, and how rarely to say it."""

    sender: Any
    horizon_hours: float = 4.0
    #: Tempat menyimpan tanggal laporan terakhir supaya bertahan melewati
    #: restart. Tanpa ini penanda hanya ada di memori, dan penjaga proses
    #: memang mematikan lalu menyalakan proses ini setiap dua puluh empat jam.
    state: Any = None
    #: Menandai setiap pesan sebagai uji coba.
    #:
    #: Operator: "kalau kamu test fitur kasih peringatan, jangan pakai data
    #: real, baru kalau selesai test pakai data real lagi."
    #:
    #: Ini jalur yang paling berbahaya untuk diuji tanpa penanda: pesan plan
    #: membawa entry, stop, leverage dan harga likuidasi, dan sebuah percobaan
    #: yang tidak dibedakan dari plan sungguhan adalah cara termudah membuat
    #: seseorang bertindak atas angka yang sengaja dikarang untuk memeriksa
    #: tata letak. Defaultnya mati - pesan sungguhan tidak boleh membawa
    #: penanda, karena penanda yang muncul pada pesan asli mengajari operator
    #: mengabaikannya.
    test_mode: bool = False
    #: Versi model yang dicetak di setiap pesan plan.
    #:
    #: Diambil dari sumber yang sama dengan yang disimpan ke database, bukan
    #: dieja ulang di sini: pesan yang menyebut versi berbeda dari baris yang
    #: tercatat akan membuat keduanya tidak bisa dipasangkan.
    model_version: str | None = None
    #: Penyimpanan rencana, untuk menulis dan membaca jejak pengirimannya.
    #:
    #: ``None`` mematikan penyaringan hasil sepenuhnya - dan itu arah kegagalan
    #: yang benar: tanpa penyimpanan, semua hasil tetap dikirim. Kebalikannya
    #: akan membungkam kabar bahwa ARUNA salah gara-gara perakitan yang belum
    #: lengkap (§11.21).
    store: Any = None
    #: Last time a summary went out, per symbol.
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    #: Hasil yang sudah dikabarkan, seumur proses.
    #:
    #: Resolver membaca ulang jendela yang sama tiap tick; tanpa penanda ini
    #: satu hasil dikirim setiap lima belas menit selamanya.
    _hasil_terkirim: set[str] = field(default_factory=set)
    #: Signal terakhir yang terbit per simbol (PASAL 14.35 - 14.37), beserta
    #: kapan ia terbit - keduanya, karena yang satu tanpa yang lain tidak bisa
    #: menjawab "apakah ini masih berlaku".
    #:
    #: Seumur proses, bentuk yang sama dengan tiga peredam lain di sistem ini.
    #: Keadaan yang disimpan di tempat lain akan menghidupkan kembali
    #: pembungkaman lama sesudah restart, atau membungkam pendapat baru karena
    #: pendapat lama pernah ada. Restart memang saat yang tepat untuk
    #: mendengarkan semuanya sekali lagi.
    _terakhir: dict[str, tuple[Any, datetime]] = field(default_factory=dict)
    #: Tanggal WIB hari terakhir yang sudah dilaporkan, ``YYYY-MM-DD``.
    _last_daily: str | None = None

    def due(self, symbol: str, now: datetime) -> bool:
        """Has this symbol's cooldown elapsed?

        The cooldown is the horizon itself, not a number chosen for it: within
        one horizon a setup that keeps clearing is the same setup, and the
        reader has already been told about it.
        """
        last = self._last_sent.get(symbol)
        if last is None:
            return True
        return now - last >= timedelta(hours=self.horizon_hours)

    async def announce(
        self, plans: list[Any], *, now: datetime, notes: dict[str, Any] | None = None
    ) -> int:
        """Announce the plans that cleared. Returns how many were announced.

        ``notes`` memetakan simbol ke penilaian council-nya. Yang tidak punya
        catatan tetap dikirim, tanpa bagian PENILAIAN - sebuah plan tanpa
        penilaian tetap plan, dan menahan angkanya karena satu bagian hilang
        akan menghilangkan justru yang diminta operator.

        **One message per plan, carrying its numbers.** The operator asked for
        entry, stop and leverage in the notification itself, having been told
        what that trades away: a message that arrives unasked with prices in it
        reads as an instruction, and the step where the reader chooses to look
        is the step being removed. That is their call to make, and it is made.

        What the numbers cannot be sent without is the **liquidation price**. A
        leverage figure alone is the most dangerous half-fact this system can
        produce - it tells the reader how large the position is and not how far
        the exchange lets it move first.
        """
        fresh = [
            plan
            for plan in plans
            if plan.verdict is PlanVerdict.PLAN and self.due(plan.symbol, now)
        ]
        if not fresh:
            return 0

        catatan = notes or {}
        terkirim = 0
        for plan in fresh:
            catatan_plan = catatan.get(plan.symbol)
            putusan = self._konsistensi(plan, catatan_plan, now)
            if putusan is not None and not putusan.sends:
                # PASAL 14.37. Cooldown TIDAK disetel di sini: yang menahan
                # bukan waktu melainkan ketiadaan kabar baru, dan menyetel
                # cooldown akan membungkam pendapat yang benar-benar berubah
                # beberapa detik kemudian.
                log.info(
                    "futures.duplikat_ditahan",
                    symbol=plan.symbol, sebab=putusan.reason,
                )
                continue

            self._last_sent[plan.symbol] = now
            teks = _alert(
                plan,
                now,
                note=catatan_plan,
                test_mode=self.test_mode,
                model_version=self.model_version,
            )
            if putusan is not None and putusan.reversal is not None:
                # PASAL 14.36: keputusan lama, keputusan baru, alasan, bukti
                # baru - dan pernyataan bahwa signal lama TIDAK disunting.
                teks = "\n".join([teks, "", *putusan.reversal.report()])
            self._ingat(plan, catatan_plan, now)
            terkirim += 1
            pesan = await self._kirim_rencana(_guard(teks))
            if pesan is None:
                # The plan is already stored; the message is the part that
                # failed. The cooldown is deliberately NOT rolled back -
                # retrying on the next tick would turn one undelivered message
                # into a burst the moment the network returns.
                log.warning("futures.notify_undelivered", symbol=plan.symbol)
                continue
            # Jejaknya ditulis hanya sesudah pesannya benar-benar berangkat.
            # Tanpa baris ini hasilnya nanti tidak akan pernah didorong.
            await self._catat_terkirim(plan, pesan, now)
        return terkirim

    async def _kirim_rencana(self, teks: str) -> int | None:
        """Kirim dan kembalikan id pesannya, atau ``None`` kalau gagal.

        Pengirim yang tidak mengenal ``send_id`` menghasilkan ``0`` - terkirim,
        id tidak diketahui. Itu berbeda dari ``None``, dan perbedaannya
        menentukan apakah hasilnya nanti dibungkam atau sekadar tidak bisa
        membalas.
        """
        kirim = getattr(self.sender, "send_id", None)
        if kirim is None:
            return 0 if await self.sender.send(teks) else None
        return await kirim(teks)

    def _kedaluwarsa(self, symbol: str, now: datetime) -> Any:
        """Pendapat terakhir untuk simbol ini - kalau masih berlaku (PASAL 14.23).

        Masa berlakunya **horizonnya sendiri**, dibaca lewat
        :class:`aruna.decision.lifecycle.Umur`. Tanpa ini pendapat lama diingat
        selamanya, dan perlindungan duplikat PASAL 14.37 berubah menjadi
        pembungkaman permanen: sebuah setup empat jam yang masih berlaku besok
        lusa akan menahan setiap pendapat baru tentang aset itu.

        Yang kedaluwarsa dipindahkan ke EXPIRED lewat
        :func:`aruna.decision.lifecycle.move`, bukan dihapus. Keadaan yang
        dihapus tidak bisa dibedakan dari keadaan yang belum pernah ada, dan
        keduanya berarti hal yang berbeda bagi ``evaluate``.
        """
        from dataclasses import replace as _replace

        from aruna.decision.lifecycle import State, Umur, move

        simpan = self._terakhir.get(symbol)
        if simpan is None:
            return None
        terakhir, terbit = simpan
        umur = Umur(published_at=terbit, horizon=terakhir.horizon)
        if umur.window is None:
            # Horizon yang tidak dikenal tidak bisa ditua-kan, dan ingatan yang
            # tidak pernah menua membungkam simbol itu selamanya. ``Umur``
            # benar menolak menebak masa berlakunya; yang salah adalah memakai
            # ketidaktahuan itu sebagai alasan diam.
            #
            # Arah kegagalannya dipilih: satu duplikat yang lolos jauh lebih
            # murah daripada satu simbol yang berhenti bicara tanpa jejak.
            log.warning(
                "futures.horizon_tak_dikenal",
                symbol=symbol, horizon=terakhir.horizon,
            )
            return None
        if not umur.expired(now):
            return terakhir
        if terakhir.state is State.EXPIRED:
            return terakhir
        sudah = _replace(terakhir, state=move(terakhir.state, State.EXPIRED))
        self._terakhir[symbol] = (sudah, terbit)
        return sudah

    def _konsistensi(self, plan: Any, note: Any, now: datetime) -> Any:
        """Apakah pendapat ini layak dikirim (PASAL 14.35 - 14.37).

        Memulangkan ``None`` kalau tidak bisa dinilai - dan pemanggil mengirim.
        **Arah kegagalannya disengaja**: sebuah bug di penjaga ini akan
        membungkam signal, dan signal yang hilang tanpa jejak jauh lebih buruk
        daripada satu duplikat yang lolos.

        Pembalikan **tidak ditahan**. PASAL 14.35 melarang ARUNA berbalik tanpa
        sebab, bukan melarang ARUNA mengabarkan bahwa ia sudah berbalik: seorang
        operator yang memegang LONG sementara ARUNA diam-diam berpikir SHORT
        berada dalam keadaan yang jauh lebih berbahaya daripada yang menerima
        satu pesan pembalikan. Yang dituntut pasal itu - alasan dan bukti baru -
        dipenuhi dengan mencetaknya, bukan dengan membungkamnya.
        """
        try:
            from aruna.decision.consistency import (
                ConsistencyError,
                Pembalikan,
                Terakhir,
                evaluate,
            )
            from aruna.decision.final import FinalError, arah_dari

            try:
                arah = arah_dari(getattr(plan, "side", None))
            except FinalError:
                return None

            horizon = _timeframe_of(plan)
            strategi = _strategi_of(note)
            sebelumnya: Terakhir | None = self._kedaluwarsa(plan.symbol, now)

            pembalikan = None
            if sebelumnya is not None and sebelumnya.live and (
                arah is not sebelumnya.decision
            ):
                bukti = tuple(
                    getattr(getattr(note, "explanation", None), "reasons", ())
                    or ()
                )
                try:
                    pembalikan = Pembalikan(
                        previous=sebelumnya.decision,
                        new=arah,
                        reason=(
                            f"council berbalik dari {sebelumnya.decision.value} "
                            f"ke {arah.value} pada horizon {horizon}"
                        ),
                        new_evidence=bukti,
                    )
                except (ConsistencyError, ValueError):
                    # Tanpa bukti baru yang bisa disebutkan, PASAL 14.36 tidak
                    # bisa dipenuhi. Dicatat, lalu dibiarkan kosong - dan
                    # ``evaluate`` di bawah akan menolak pembalikan tanpa
                    # catatan, yang ditangkap penjaga di bawahnya lagi.
                    #
                    # SATU penjaga, bukan dua. Versi pertama juga memulangkan
                    # ``None`` di sini, dan karena penjaga di bawah menangkap
                    # hal yang sama, tidak satu pun dari keduanya bisa dicabut
                    # dan membuat sebuah test merah.
                    log.warning(
                        "futures.pembalikan_tanpa_bukti", symbol=plan.symbol
                    )

            return evaluate(
                arah,
                horizon=horizon,
                strategy=strategi,
                previous=sebelumnya,
                reversal=pembalikan,
            )
        except ConsistencyError as exc:
            log.warning("futures.konsistensi_ditolak", sebab=str(exc))
            return None
        except Exception:
            log.exception("futures.konsistensi_gagal")
            return None

    def _ingat(self, plan: Any, note: Any, now: datetime) -> None:
        """Catat pendapat yang barusan terbit, untuk dibandingkan berikutnya."""
        try:
            from aruna.decision.consistency import Terakhir
            from aruna.decision.final import arah_dari
            from aruna.decision.lifecycle import State

            self._terakhir[plan.symbol] = (
                Terakhir(
                    decision=arah_dari(getattr(plan, "side", None)),
                    horizon=_timeframe_of(plan),
                    strategy=_strategi_of(note),
                    state=State.PUBLISHED,
                ),
                now,
            )
        except Exception:
            log.exception("futures.ingat_gagal")

    async def results(self, hasil: list[Any], *, now: datetime) -> int:
        """Kabarkan nasib rencana yang horizonnya sudah lewat (PASAL 14.31).

        **Ini yang tidak pernah ada.** ``PlanNotifier`` punya ``announce`` dan
        ``daily``, dan tidak satu pun yang mengabarkan hasil - jadi rencana
        dikabarkan, diskor, disimpan, dan operator tidak pernah tahu bagaimana
        akhirnya. Terukur: nol pesan hasil futures pernah terkirim.

        **Hasil tanpa rencananya tidak dikirim.** Operator sudah mengeluhkan
        bentuk kegagalan itu di jalur signal - *"tiba tiba result aja tanpa
        sinyal kan aneh"* - dan jalur ini tidak boleh mengulanginya.

        **Tapi penjaganya gagal TERBUKA.** Tanpa penyimpanan, atau saat
        pencariannya gagal, semua hasil tetap dikirim. Arah itu disengaja:
        §11.21 melarang menyembunyikan LOSS, dan satu bug pencarian yang
        membungkam akan menghapus setiap kabar bahwa ARUNA salah.
        """
        selesai = [
            r for r in hasil
            if str(getattr(getattr(r, "outcome", None), "value", "")) != "OPEN"
            and str(getattr(r, "signal_id", "")) not in self._hasil_terkirim
        ]
        if not selesai:
            return 0

        terkirim_map = await self._jejak_kirim(selesai)
        if terkirim_map is not None:
            sebelum = len(selesai)
            selesai = [
                r for r in selesai
                if str(getattr(r, "signal_id", "")) in terkirim_map
            ]
            hilang = sebelum - len(selesai)
            if hilang:
                log.info("futures.result_never_pushed_suppressed", count=hilang)

        terkirim = 0
        for r in selesai:
            sid = str(getattr(r, "signal_id", ""))
            teks = _hasil_alert(r, now, model_version=self.model_version)
            balas = (terkirim_map or {}).get(sid)
            if not await self._kirim_hasil(_guard(teks), balas, r):
                log.warning("futures.result_undelivered", signal_id=sid)
                continue
            # Ditandai hanya sesudah benar-benar terkirim. Menandainya lebih
            # dulu akan menghapus hasil yang gagal kirim untuk selamanya.
            self._hasil_terkirim.add(sid)
            terkirim += 1
        return terkirim

    async def _jejak_kirim(self, hasil: list[Any]) -> dict[str, int | None] | None:
        """Rencana mana yang benar-benar terkirim. ``None`` = tidak bisa dicek."""
        cari = getattr(self.store, "pushed_message_ids", None)
        if cari is None:
            return None
        try:
            return await cari([str(getattr(r, "signal_id", "")) for r in hasil])
        except Exception:
            log.exception("futures.push_lookup_failed")
            return None

    async def _kirim_hasil(
        self, teks: str, balas: int | None, hasil: Any
    ) -> bool:
        """Kirim hasil, membalas pesan rencananya kalau id-nya diketahui.

        **Jenisnya disebutkan menurut hasilnya** (PASAL 14.38). WIN dan LOSS
        adalah dua jenis terpisah, dan menyatukannya di bawah satu jenis akan
        membuat pembungkaman salah satunya tidak terlihat di mana pun - persis
        cacat yang sudah diperbaiki di jalur signal.
        """
        from aruna.decision.channel import Jenis, allow

        menang = str(
            getattr(getattr(hasil, "outcome", None), "value", "")
        ) == "TARGET_HIT"
        allow(Jenis.WIN if menang else Jenis.LOSS)
        kirim = getattr(self.sender, "send_id", None)
        if kirim is None or not balas:
            return bool(await self.sender.send(teks))
        return await kirim(teks, reply_to=balas) is not None

    async def _catat_terkirim(
        self, plan: Any, message_id: int | None, now: datetime
    ) -> None:
        """Tulis jejak pengiriman rencananya. Kegagalannya tidak membatalkannya.

        Pesannya sudah sampai ke operator; sebuah tulisan yang gagal berarti
        hasilnya nanti tidak akan didorong - kehilangan yang lebih kecil
        daripada mengulang pengiriman atau menjatuhkan siklusnya.
        """
        sid = str(getattr(plan, "signal_id", "") or "")
        catat = getattr(self.store, "mark_pushed", None)
        if catat is None or not sid:
            return
        try:
            await catat(sid, message_id=message_id or None, at=now)
        except Exception:
            log.exception("futures.push_not_recorded", signal_id=sid)

    async def daily(self, build: Any, *, now: datetime) -> bool:
        """Kirim laporan penutup hari, sekali per hari WIB.

        ``build`` dipanggil dengan ``(awal, akhir)`` - batas hari WIB yang
        dilaporkan - dan hanya dipanggil kalau laporannya memang akan dikirim.
        Versi sebelumnya menyusun laporan lebih dulu lalu bertanya apakah boleh
        mengirim, jadi setiap tick membangun laporan lengkap dari database
        untuk kemudian membuangnya sembilan puluh lima kali sehari.

        Tiga hal yang salah pada versi sebelumnya, dan ketiganya yang dikeluhkan
        operator:

        **Tidak ada syarat waktu sama sekali.** Yang ada hanya "tanggalnya
        berbeda dari terakhir kali", dan penanda terakhir kali dimulai kosong -
        jadi tick **pertama** setiap proses selalu lolos. Laporan harian datang
        pada saat proses menyala, bukan pada penutup hari.

        **Penandanya cuma di memori.** Penjaga proses menjalankan ulang loop
        futures setiap dua puluh empat jam, dan setiap restart manual juga
        menghapusnya. Setiap kali proses hidup, laporan kemarin dikirim lagi.

        **Jendelanya bergulir, bukan harian.** Laporannya dibangun dari "dua
        puluh empat jam terakhir" terhitung dari saat kirim, jadi angkanya tidak
        pernah benar-benar direset di batas hari - dua laporan berturut-turut
        bisa memuat perdagangan yang sama.
        """
        hari = due_day(now)
        if hari is None:
            return False

        tanggal = hari.isoformat()
        if self._last_daily == tanggal:
            return False
        if self.state is not None and self._last_daily is None:
            simpan = await self.state.get(FUTURES_DAILY_KEY)
            if simpan and simpan.get("date"):
                self._last_daily = str(simpan["date"])
                if self._last_daily == tanggal:
                    return False

        awal, akhir = day_window(hari)
        report = await build(awal, akhir)
        if not report:
            # Hari tanpa satu pun verdict. Tidak dikirim, dan **tidak**
            # distempel: kalau tick berikutnya masih di dalam jendela dan
            # datanya sudah ada, laporannya masih bisa berangkat.
            return False

        if not await self.sender.send(_guard(report)):
            # Distempel hanya setelah berhasil. Menstempel duluan berarti satu
            # kegagalan jaringan menghapus laporan hari itu selamanya, dan
            # laporan yang hilang tidak meninggalkan jejak apa pun.
            log.warning("futures.daily_undelivered", date=tanggal)
            return False

        self._last_daily = tanggal
        if self.state is not None:
            await self.state.set(
                FUTURES_DAILY_KEY, {"date": tanggal}, actor="aruna-futures"
            )
        log.info("futures.daily_sent", date=tanggal)
        return True


#: Caveats carried in the push. The rest are one /plans away; sending all of
#: them would push the decision numbers off a phone screen, which defeats the
#: reason the numbers are here at all.
MAX_PUSHED_CAVEATS = 3


def _timeframe_of(plan: Any) -> str:
    """Timeframe rencana ini, dari ``horizon_hours``.

    **Versi pertama menebak nama atributnya dan meleset.** Ia mencoba
    ``horizon``, ``interval``, lalu ``timeframe`` - dan ``FuturesPlan``
    menyimpannya sebagai ``horizon_hours``, sebuah angka. Ketiganya meleset,
    fallback-nya berbunyi, dan operator menerima "TIMEFRAME: TIDAK TERCATAT"
    pada rencana yang horizonnya diketahui persis.

    Pelajarannya bukan "tambah nama keempat": menebak bentuk data alih-alih
    membacanya menghasilkan kode yang terlihat berhati-hati dan salah di
    setiap cabangnya. Sekarang ia membaca satu bidang yang memang ada.

    Angkanya diterjemahkan ke satuan yang dipakai operator, bukan dicetak
    mentah: "0.25 jam" adalah cara paling bertele-tele menulis lima belas
    menit.
    """
    jam = getattr(plan, "horizon_hours", None)
    if jam is None:
        return "TIDAK TERCATAT"
    try:
        angka = float(jam)
    except (TypeError, ValueError):
        return "TIDAK TERCATAT"
    if angka <= 0:
        return "TIDAK TERCATAT"
    if angka < 1:
        return f"{round(angka * 60)}m"
    if angka % 24 == 0:
        hari = round(angka / 24)
        return "1d" if hari == 1 else f"{hari}d"
    # Jam bulat ditulis tanpa koma; 1.5 jam tetap perlu komanya.
    return f"{angka:g}h"


def _decision_score(note: Any, faktor: dict[str, float]) -> str:
    """Satu baris Decision Score (PASAL 14.16), atau kosong.

    **Keterangan, bukan gerbang.** Terukur pada bentuk data realistis: cakupan
    komponen berarah 30%-89%, dan kasus paling sering di produksi berhenti di
    52% - di bawah ambang cakupan. Menjadikan skor ini syarat kirim akan
    membungkam ARUNA hampir sepenuhnya. Lihat ``attach_decision_readings`` di
    :mod:`aruna.futures.service`.

    Barisnya tidak dicetak kalau skornya tidak bisa dinilai. Sebuah
    "DECISION SCORE: tidak bisa dinilai" di antara angka-angka keputusan hanya
    menambah baris tanpa menambah apa pun yang bisa dipakai - berbeda dengan
    baris RISIKO, yang ketiadaannya justru pernyataan tentang setup-nya.
    """
    bacaan = dict(getattr(note, "decision_readings", None) or {})
    if not bacaan:
        # Jalan pintas, **bukan penjaga**: tanpa baris ini hasilnya sama persis
        # - cakupan nol tidak pernah bisa dinilai. Yang dihemat adalah impor
        # dan penjumlahan pada setiap rencana yang tidak punya catatan council.
        # Disebut apa adanya supaya tidak ada yang mengira ia menahan sesuatu.
        return ""
    try:
        from aruna.decision.score import MAX_ARAH, score

        bacaan.update(faktor)
        s = score(bacaan)
        if not s.usable:
            return ""
        # Angkanya tidak pernah berdiri sendiri (PASAL 14.16): "69" yang
        # dicetak polos akan dibaca sebagai 69 persen.
        return f"{s.value:+.0f} dari {MAX_ARAH:.0f} (bukan peluang profit)"
    except Exception:
        log.exception("futures.decision_score_failed")
        return ""


def _risiko(plan: Any, note: Any = None) -> str:
    """Satu baris Risk Score (PASAL 13.2), atau pengakuan bahwa ia tak terukur.

    Kegagalannya diisolasi: penilaian risiko membaca banyak bagian rencana, dan
    satu bentuk yang tak terduga di salah satunya tidak boleh menghentikan
    pesan yang membawa entry, stop dan target. Yang hilang saat ia gagal adalah
    satu baris keterangan - bukan seluruh signal.
    """
    try:
        from aruna.risk import assess
        from aruna.risk.context_readings import merge
        from aruna.risk.futures_readings import readings_from_plan

        # Rencana lebih dulu, konteks menyusul. Keduanya bisa mengukur mutu
        # data - rencana dari gerbang integritas SPEC 46 yang sudah memutuskan,
        # konteks dari snapshot - dan yang lebih dekat ke keputusannya menang.
        dari_konteks = getattr(note, "risk_readings", None) or {}
        gabung = merge(readings_from_plan(plan), dari_konteks)
        return assess(gabung).line()
    except Exception:
        log.exception("futures.risk_score_failed")
        return "tidak bisa dinilai"


def _potongan(plan: Any, note: Any = None) -> dict[str, float]:
    """Potongan risiko dan berita untuk Decision Score, dari Phase 13.

    Diambil dari penilaian yang sama dengan baris RISIKO di atas, bukan
    dihitung ulang: dua angka risiko dari bahan yang sama adalah dua angka yang
    harus tetap sepakat, dan mereka tidak akan.
    """
    try:
        from aruna.risk import assess
        from aruna.risk.context_readings import merge
        from aruna.risk.futures_readings import readings_from_plan

        dari_konteks = getattr(note, "risk_readings", None) or {}
        gabung = merge(readings_from_plan(plan), dari_konteks)
        keluar: dict[str, float] = {}
        nilai = assess(gabung).score
        if nilai is not None:
            keluar["risk"] = max(0.0, min(1.0, nilai / 100.0))
        berita = gabung.get("news_risk")
        if berita is not None:
            keluar["news"] = max(0.0, min(1.0, float(berita) / 100.0))
        return keluar
    except Exception:
        log.exception("futures.decision_penalties_failed")
        return {}


def _invalidation(plan: Any) -> list[str]:
    """Blok INVALIDATION (PASAL 14.21, 14.26).

    Levelnya **bukan angka baru**: ia ``stop_detail.price``, stop yang sudah
    dihitung. Contoh di PASAL 14.26 menegaskan bacaan itu - di sana
    invalidation dan stop loss adalah angka yang sama, 63.780.

    **Versi pertama membaca ``stop_detail.invalidation`` dan itu salah.**
    Bidang itu bertipe ``str``, bukan ``Decimal``: ia kalimat yang menjelaskan
    *kenapa* stopnya di situ - "1.5 ATR melawan posisi, tanpa level struktur
    untuk dijadikan sandaran". Aku membaca namanya dan menyimpulkan tipenya
    tanpa memeriksa, dan hasilnya ``TypeError`` yang tercatat empat kali
    sebagai ERROR di produksi. Pelajaran yang sudah tertulis di
    ``_timeframe_of`` beberapa baris di atas, diulang.

    Kalimatnya tidak dibuang - ia justru pendamping yang tepat: level
    menyatakan **di mana** tesisnya runtuh, kalimat menyatakan **kenapa di
    situ**.

    Dibangun lewat :class:`aruna.decision.invalidation.Invalidasi` dan bukan
    dirangkai sebagai teks, supaya penjaga tandanya ikut berlaku: sebuah LONG
    yang dibatalkan oleh penutupan **di atas** levelnya adalah salah tanda, dan
    salah tanda di sini berarti signal yang sedang salah tidak pernah
    dibatalkan.

    Kegagalannya diisolasi seperti blok risiko. Yang hilang saat ia gagal
    adalah satu blok keterangan - bukan pesan yang membawa entry dan stop.
    """
    detail = getattr(plan, "stop_detail", None)
    level = getattr(detail, "price", None) or getattr(plan, "stop", None)
    if level is None:
        return []
    sebab = str(getattr(detail, "invalidation", "") or "").strip()
    try:
        from aruna.decision.invalidation import SISI_MEMATIKAN, Ambang, Invalidasi
        from aruna.decision.score import Arah

        # Dieja, bukan disimpulkan dari "bukan LONG berarti SHORT" - persis
        # pelajaran yang sudah tertulis di `SIDE_MARK` di atas. Versi pertama
        # baris ini memakai `if ... == "LONG" else Arah.SHORT`, jadi sisi yang
        # tidak dikenal menghasilkan syarat pembatalan ke arah yang salah:
        # sebuah blok yang terlihat benar dan menyebut level yang tidak akan
        # pernah membatalkan apa pun. Ditemukan oleh test, bukan oleh operator.
        arah = {"LONG": Arah.LONG, "SHORT": Arah.SHORT}.get(plan.side.value)
        if arah is None:
            log.warning("futures.invalidation_side_unknown", side=plan.side.value)
            return []
        inval = Invalidasi(
            decision=arah,
            levels=(
                Ambang(_timeframe_of(plan), SISI_MEMATIKAN[arah], level),
            ),
            notes=(sebab,) if sebab else (),
        )
        return ["", *inval.report()]
    except Exception:
        log.exception("futures.invalidation_block_failed")
        return []


def _keputusan_final(plan: Any) -> list[str]:
    """PASAL 14.2 dan 14.43: LONG, SHORT, atau NO SIGNAL - tidak pernah WAIT.

    ``SIDE`` yang sudah dicetak di atas adalah **sisi posisi**, bukan
    keputusan. Keduanya sama pada rencana yang terbit, dan berbeda persis pada
    rencana yang tidak: di situ ``side`` bernilai ``FLAT`` - sebuah nilai yang
    ada, truthy, dan artinya "tidak berarah".

    Hari ini rencana WAIT tidak pernah dikirim: ``FuturesNotifier.send``
    menyaring ``PlanVerdict.PLAN`` saja. Baris ini bukan menambal pelanggaran
    yang sedang terjadi - ia penjaga untuk saat penyaring itu dilonggarkan,
    karena tanpa penjaganya ``FLAT`` akan terbaca operator sebagai keputusan.

    ``FLAT`` dicatat sebagai **peringatan**, bukan jejak pengecualian: ia
    keadaan yang wajar, dan log yang penuh alarm palsu berhenti dibaca.
    """
    from aruna.decision.final import FinalError, arah_dari

    try:
        arah = arah_dari(getattr(plan, "side", None))
    except FinalError as exc:
        log.warning("futures.final_decision_unknown", sebab=str(exc))
        return []
    except Exception:
        # Sengaja selebar mungkin: yang hilang saat ia gagal adalah satu baris
        # keterangan, bukan pesan yang membawa entry dan stop.
        log.exception("futures.final_decision_failed")
        return []
    return [f"KEPUTUSAN FINAL: {arah.value}"]


def _bukti_tambahan(konteks: Any) -> list[str]:
    """Pola Phase 12, hasil pada keadaan berita, dan konteks lintas aset.

    PASAL 15.16, 15.15, dan 15.18. Ketiganya **bukti tambahan** - bukan
    keputusan - dan yang tidak terbaca tidak dicetak sebagai nol (§13.26).
    """
    baris: list[str] = []
    for bagian, nama in (
        (getattr(konteks, "pola", None), "pola"),
        (getattr(konteks, "peristiwa", None), "peristiwa"),
        (getattr(konteks, "lintas", None), "lintas"),
    ):
        if bagian is None:
            continue
        teks = bagian.ringkas()
        if teks:
            baris.append(f"  {nama}: {teks}")
    return baris


def _konteks_historis(note: Any) -> list[str]:
    """Blok 🧠 HISTORICAL CONTEXT, ringkas (PASAL 15.31).

    **Ringkas dengan sengaja.** PASAL 15.31 melarang menampilkan puluhan
    historical case; yang berguna bagi operator adalah jumlahnya, rentang
    kemiripannya, rentang waktunya, dan apakah sejarah sejalan atau melawan.

    **Rentang waktunya wajib.** Terukur 2026-08-21: seluruh ingatan ARUNA lahir
    dalam beberapa hari. "147 kasus serupa" tanpa tanggalnya terbaca seperti
    pengalaman bertahun-tahun, dan itu membuat bukti tipis terdengar tebal.

    **Yang melawan tetap dicetak** (PASAL 15.20, 15.38). Menyembunyikan konteks
    yang bertentangan adalah confirmation bias yang dilakukan sistem atas nama
    operator.

    Tidak ada satu pun kalimat peluang di sini - PASAL 15.23 dan 15.48
    melarangnya, dan yang menahannya bukan niat melainkan tidak adanya jalur
    dari angka mana pun ke kata "chance".
    """
    konteks = getattr(note, "memory", None)
    if konteks is None:
        return []
    try:
        r = konteks.ringkasan
        baris = ["", "🧠 HISTORICAL CONTEXT"]

        # PASAL 15.44. Sejarah punya pendapat di sini, dan pendapatnya sengaja
        # tidak diberi bobot karena timeframe ini terukur tidak membantu.
        #
        # Dicetak, bukan didiamkan: tanpa baris ini operator melihat NEUTRAL
        # dan menyimpulkan sejarah tidak berpendapat - dua hal yang sangat
        # berbeda. Alasannya berikut angkanya ikut di `konteks.catatan`.
        if getattr(konteks, "digerbangi", False):
            baris.append("(bobot dimatikan: memory belum terbukti di timeframe ini)")

        if r.kalimat:
            # Ketiga bukti tambahan **tidak bergantung** pada kecocokan
            # kemiripan: pola Phase 12, hasil pada keadaan berita, dan konteks
            # lintas aset dihitung dari korpus, bukan dari kasus serupa.
            # Membuangnya di sini akan menyembunyikan bukti yang ada hanya
            # karena bukti yang lain tidak ada.
            return (
                baris
                + [f"{r.kalimat} ({r.total} kasus)"]
                + _bukti_tambahan(konteks)
                + [f"  - {c}" for c in konteks.catatan]
            )

        rendah, tinggi = r.rentang_similarity
        waktu = ""
        if r.rentang_waktu:
            awal, akhir = r.rentang_waktu
            waktu = f", {awal:%d %b}-{akhir:%d %b}"
        baris.append(
            f"{r.total} kasus serupa (similarity {rendah}-{tinggi}%{waktu})"
        )

        arah = " | ".join(
            f"{nama} {r.per_arah[nama]}" for nama in ("LONG", "SHORT")
            if r.per_arah.get(nama)
        )
        menang = " | ".join(
            f"{nama} {r.win_rate[nama]}%" for nama in ("LONG", "SHORT")
            if r.win_rate.get(nama) is not None
        )
        if arah:
            baris.append(f"  arah historis: {arah}")
        if menang:
            baris.append(f"  hasil historis: {menang}")
        baris.append(
            f"  konteks: {konteks.pengaruh.value} "
            f"(kontribusi {konteks.kontribusi})"
        )

        baris += _bukti_tambahan(konteks)
        baris += [f"  - {c}" for c in konteks.catatan]
        return baris
    except Exception:
        log.exception("futures.memory_block_failed")
        return []


def _jatah_risiko(note: Any) -> list[str]:
    """PASAL 14.41: berapa jatah risiko hari ini yang sudah terpakai.

    Angka ini **tidak menahan apa pun** - ARUNA melapor, operator memutuskan.
    Sebuah gerbang ketiga di jalur yang sudah punya dua akan membungkam ARUNA
    hampir sepenuhnya; lihat catatan di :mod:`aruna.decision.engine`.

    Tidak terbaca berarti **tidak ada barisnya**, bukan nol: nol berarti ARUNA
    belum mempertaruhkan apa pun hari ini, dan itu kalimat yang berbeda (§13.26).
    """
    jatah = getattr(note, "risk_budget", None)
    if jatah is None:
        return []
    try:
        return ["", f"JATAH RISIKO HARI INI: {jatah.ringkas()}"]
    except Exception:
        log.exception("futures.jatah_risiko_failed")
        return []


def _entry_timing(plan: Any) -> list[str]:
    """PASAL 14.19/14.20: waktu masuk, bukan keputusan.

    PASAL 14.43 memisahkan keduanya dengan tegas - keputusannya LONG, waktu
    masuknya boleh menunggu. Itu satu-satunya bentuk "menunggu" yang
    diperbolehkan, dan ia menempel pada arah yang sudah diputuskan.

    Angkanya dibaca dari jarak entry ke harga acuan; keduanya sudah ada di
    rencana dan tidak ada yang dikarang di sini (§13.26). Kalau salah satunya
    tidak tersedia, barisnya **tidak dicetak** - "MASUK SEKARANG" yang lahir
    dari ketiadaan data terbaca persis seperti ajakan.
    """
    from decimal import Decimal

    from aruna.decision.final import arah_dari
    from aruna.decision.score import Arah
    from aruna.decision.timing import Timing

    acuan = getattr(plan, "reference_price", None)
    entry = getattr(plan, "entry", None)
    if acuan is None or entry is None:
        return []
    try:
        arah = arah_dari(getattr(plan, "side", None))
    except Exception:  # noqa: BLE001 - lihat _keputusan_final
        # Tanpa catatan log di sini dengan sengaja: `_keputusan_final` sudah
        # mencatat sebab yang sama beberapa baris sebelumnya, dan dua baris
        # untuk satu kejadian membuat penghitungan di log jadi dobel.
        return []
    if arah is Arah.NO_SIGNAL:
        return []

    tick = getattr(plan, "tick_size", None) or Decimal(0)
    selisih = entry - acuan
    if abs(selisih) <= tick:
        timing = Timing.NOW
    elif (selisih < 0) is (arah is Arah.LONG):
        # Entry lebih baik daripada harga sekarang: lebih rendah untuk LONG,
        # lebih tinggi untuk SHORT. Harganya harus kembali dulu.
        timing = Timing.PULLBACK
    else:
        # Entry lebih buruk daripada harga sekarang - rencananya menunggu
        # harga menembus, bukan kembali.
        timing = Timing.BREAKOUT
    return [f"ENTRY TIMING: {timing.value}"]


#: Nama yang dipakai operator untuk tiap akhir, dan tandanya.
#:
#: ``LIQUIDATED`` punya barisnya sendiri dan **tidak** dilebur ke LOSS. Ia
#: kekalahan terburuk yang bisa dihasilkan sistem ini - posisi ditutup paksa
#: bursa, stop-nya jadi hiasan - dan menyebutnya "LOSS" saja menghapus
#: perbedaan antara kena stop dan kehabisan margin.
_AKHIR_LABEL: dict[str, tuple[str, str]] = {
    "TARGET_HIT": ("🏆", "WIN"),
    "STOPPED_OUT": ("🔴", "LOSS"),
    "LIQUIDATED": ("💀", "LOSS - LIQUIDATED"),
    "EXPIRED": ("⏱", "EXPIRED"),
}


def _hasil_alert(
    hasil: Any, now: datetime, *, model_version: str | None = None
) -> str:
    """Pesan ARUNA FUTURES RESULT (PASAL 14.31).

    Angkanya dibawa apa adanya. Tidak ada satu pun cabang di sini yang membaca
    hasilnya dan menuliskan sesuatu yang lain - itulah bentuk teknis dari
    larangan menyembunyikan LOSS (§11.21).
    """
    nama = str(getattr(getattr(hasil, "outcome", None), "value", "") or "")
    tanda, label = _AKHIR_LABEL.get(nama, ("⚪", nama or "UNKNOWN"))
    sisi = str(getattr(getattr(hasil, "side", None), "value", "") or "")
    entry = getattr(hasil, "entry", None)
    keluar = getattr(hasil, "exit_price", None)

    baris = [
        f"{tanda} ARUNA FUTURES RESULT - {getattr(hasil, 'symbol', '')}",
        "",
        f"HASIL:       {label}",
        f"SIDE:        {sisi}",
        f"ENTRY:       {_price(entry, None)}",
        f"EXIT:        {_price(keluar, None)}",
    ]

    gerak = _gerak_pct(entry, keluar, sisi)
    if gerak is not None:
        # Bertanda menurut ARAH POSISI: positif berarti rencananya benar.
        # Gerak pasar mentah akan membuat SHORT yang menang terbaca negatif.
        baris.append(f"GERAK:       {gerak:+.2f}% (menurut arah posisi)")

    terburuk = getattr(hasil, "max_adverse_pct", None)
    if terburuk is not None:
        baris.append(f"TERJAUH LAWAN: {Decimal(terburuk):+.2f}%")
    if getattr(hasil, "touched_liquidation", False):
        # Disebut walau hasilnya bukan LIQUIDATED: harga menyentuh level itu
        # berarti rencananya selamat karena urutan, bukan karena marginnya cukup.
        baris.append("⚠️ harga sempat menyentuh level likuidasi")

    temuan = tuple(getattr(hasil, "findings", ()) or ())
    if temuan:
        baris += ["", "CATATAN:"]
        baris += [f"  - {t}" for t in temuan[:4]]

    if model_version:
        baris += ["", f"MODEL:       {model_version}"]
    baris += ["", wib(now), "", *KAKI]
    return "\n".join(baris)


def _gerak_pct(entry: Any, keluar: Any, sisi: str) -> Decimal | None:
    """Gerak dari entry ke exit, bertanda menurut arah posisi."""
    if entry is None or keluar is None:
        return None
    try:
        e, k = Decimal(entry), Decimal(keluar)
        if e <= 0:
            return None
        arah = Decimal(-1) if sisi == "SHORT" else Decimal(1)
        return ((k - e) / e * 100 * arah).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - satu baris keterangan, bukan pesannya
        return None


def _strategi_of(note: Any) -> str:
    """Nama strategi yang dipakai, atau kalimat kosong.

    Dipakai membandingkan pendapat baru dengan yang lama (PASAL 14.37), jadi
    yang penting bukan namanya melainkan **stabilitasnya**: dua tick dengan
    strategi yang sama harus menghasilkan teks yang sama, atau setiap tick akan
    terlihat seperti strategi baru dan perlindungan duplikatnya tidak pernah
    menahan apa pun.
    """
    strategi = getattr(note, "strategy", None)
    return str(getattr(strategi, "code", None) or strategi or "").strip()


def _kenapa(note: Any) -> list[str]:
    """Blok KENAPA LONG / KENAPA SHORT (PASAL 14.29).

    Disusun di :func:`aruna.futures.service.attach_explanation`, tempat opini
    agent - dan karenanya **sumber** tiap alasan - masih ada. Notifier hanya
    mencetaknya.

    Tidak dicetak kalau tidak tersusun. Sebuah keputusan yang seluruh
    dukungannya datang dari satu jenis bukti memang belum punya penjelasan
    berlapis, dan sebuah blok "KENAPA LONG" berisi satu baris terbaca seperti
    penjelasan padahal ia satu klaim.
    """
    penjelasan = getattr(note, "explanation", None)
    if penjelasan is None:
        return []
    try:
        return ["", *penjelasan.report()]
    except Exception:
        log.exception("futures.explanation_block_failed")
        return []


def _kalibrasi(note: Any) -> list[str]:
    """Vonis kalibrasi Phase 12 (bagian 18.45), atau tidak ada baris.

    **Kalibrasi menjawab pertanyaan yang tidak dijawab angka mana pun di
    atasnya:** apakah keyakinan yang barusan dicetak berarti apa yang
    dikatakannya. Terukur pada 2026-08-24 atas 491 prediksi yang sudah selesai:
    ARUNA terlalu percaya diri di pita 50-65%, 65-80%, dan 80-96% - yang berarti
    "CONFIDENCE 81%" di pesan ini secara historis menghasilkan akurasi yang
    lebih rendah dari 81%.

    Menyembunyikan itu sambil tetap mencetak angkanya adalah bentuk kebohongan
    yang paling mudah dipertahankan: setiap angkanya benar, dan gabungannya
    menyesatkan.

    Baris ini hilang kalau vonisnya belum pernah diukur. Yang tidak dicetak
    adalah ketiadaannya - bukan "GOOD", yang akan menjadi klaim tentang sesuatu
    yang belum pernah diperiksa.
    """
    vonis = str(getattr(getattr(note, "pembelajaran", None), "kalibrasi", "") or "")
    return [f"  KALIBRASI:        {vonis}"] if vonis else []


def _penilaian(plan: Any, note: Any) -> list[str]:
    """Seberapa kuat dasar plan ini, dan seberapa sepakat yang memutuskannya.

    Buffer likuidasi datang dari plan dan selalu ada. Sisanya datang dari
    council; kalau catatannya tidak ada, bagiannya tidak dicetak - **bukan**
    dicetak dengan tanda hubung. Baris "CONFIDENCE: -" terbaca seperti nol,
    dan nol adalah pernyataan tentang keyakinan, bukan pengakuan bahwa
    angkanya tidak sampai ke sini.
    """
    lines: list[str] = ["", "PENILAIAN:"]

    # Risk Score PHASE 13, di atas penilaian council - ia menjawab "layak
    # diambil?", dan itu pertanyaan yang dibaca lebih dulu daripada "seberapa
    # sepakat mereka?".
    #
    # Ketiadaannya ikut dicetak. Sebuah rencana yang faktornya terlalu sedikit
    # untuk dinilai bukan rencana berisiko rendah, dan baris yang hilang
    # terbaca persis begitu.
    lines.append(f"  RISIKO:           {_risiko(plan, note)}")

    # PASAL 14.26. Dicetak hanya kalau memang terbaca - lihat
    # `_regime_name` di aruna.futures.service untuk kenapa string kosong
    # lebih jujur daripada "UNKNOWN" di sini.
    rezim = getattr(note, "regime", "") if note is not None else ""
    if rezim:
        lines.append(f"  REZIM PASAR:      {rezim}")

    skor = _decision_score(note, _potongan(plan, note))

    buffer = getattr(plan, "buffer", None)
    if buffer is not None:
        lines.append(f"  BUFFER LIKUIDASI: {buffer.band} ({buffer.score}/100)")
    if note is None:
        lines.append("  Penilaian council tidak tersedia untuk plan ini.")
        return lines

    lines.append(f"  DISAGREEMENT:     {note.disagreement:.2f}")
    lines += _kalibrasi(note)

    # Bagian 18.17. Menggantikan baris CONFIDENCE dan DECISION SCORE yang
    # dulu berdiri sendiri di sini - keduanya ada di dalam blok ini sekarang,
    # bersama lima keyakinan lain yang sudah dihitung sejak lama dan tidak
    # pernah sampai ke pembacanya.
    lines += render_terpisah(
        pisahkan(
            mutu=getattr(note, "mutu", None),
            confidence=note.confidence,
            decision=skor,
        )
    )
    if note.high_disagreement:
        lines.append(
            "  Para agent membaca pasar yang sama dengan cara yang sangat "
            "berbeda."
        )
    if note.debated:
        lines.append("")
        lines.append("  YANG DIPERDEBATKAN:")
        lines += [f"    - {r}" for r in note.reasons]

    lines.append("")
    lines += [f"  {baris}" if baris else "" for baris in render_votes(note.split)]
    return lines


def _alert(
    plan: Any,
    now: datetime,
    *,
    note: Any = None,
    test_mode: bool = False,
    model_version: str | None = None,
) -> str:
    """One plan, with the figures the operator asked to see.

    Liquidation sits directly under leverage, deliberately. Those two numbers
    only mean anything together: 10x says how large the position is, and the
    liquidation price says how far wrong it may go before the exchange closes
    it whatever the stop says.

    **PENILAIAN ada di sini karena tidak ada di tempat lain.** Pesan ini dulu
    memuat angka posisi saja - side, entry, stop, target, leverage, size - dan
    tidak satu pun keterangan tentang seberapa yakin ARUNA atau seberapa
    terbelah council yang memutuskannya. Angka tanpa penilaian terbaca lebih
    pasti daripada yang sebenarnya: entry 118.400 terlihat sama meyakinkan pada
    council yang bulat maupun pada council yang menang tipis.

    **Kolom sejajar, bukan bertingkat.** Tata letak ini pernah diubah menjadi
    label-di-barisnya-sendiri supaya seragam dengan ARUNA RESULT, lalu
    dikembalikan setelah operator melihat keduanya berdampingan: kolom sejajar
    memuat delapan angka keputusan dalam satu layar ponsel, dan bertingkat
    mendorong sebagian di antaranya keluar layar. Keseragaman kalah oleh angka
    yang harus terlihat sekaligus.

    Yang tetap dari perubahan itu adalah penanda warnanya - hijau untuk LONG,
    merah untuk SHORT - karena itu aturan terpisah dan tidak menyentuh tata
    letak.
    """
    tick = getattr(plan, "tick_size", None)
    liquidation = plan.liquidation.price if plan.liquidation else None
    tanda = MARK.get(SIDE_MARK.get(plan.side.value, NO_SIGNAL), MARK[NO_SIGNAL])

    lines: list[str] = []
    if test_mode:
        lines += [TEST_BANNER, ""]
    lines += [
        f"{tanda} ARUNA FUTURES - {plan.symbol}",
        "",
        # PASAL 14.2/14.43 lebih dulu daripada SIDE: yang pertama dibaca
        # operator adalah keputusannya, bukan mekanik posisinya.
        *_keputusan_final(plan),
        *_entry_timing(plan),
        f"SIDE:        {plan.side.value}",
        # **Timeframe, dan ia wajib.**
        #
        # Sebelum baris ini ada, pesan futures tidak pernah menyebutkannya sama
        # sekali - `horizon_hours` hanya dipakai menghitung cooldown. Operator
        # menerima "LONG AVAXUSDT entry 6.3170 stop 6.1800" tanpa cara apa pun
        # untuk tahu apakah itu ide lima belas menit atau empat jam, dan stop
        # yang benar untuk keduanya sangat berbeda.
        #
        # Ditaruh di atas, bersama SIDE, karena ia bagian dari apa keputusannya
        # - bukan keterangan tambahan di bawah angka.
        f"TIMEFRAME:   {_timeframe_of(plan)}",
        f"ENTRY:       {_price(plan.entry, tick)}",
        f"STOP:        {_price(plan.stop, tick)}",
        f"TARGET:      {_price(plan.target, tick)}",
        "",
        f"LEVERAGE:    {plan.leverage}x  {plan.margin_mode.value}",
        f"LIQUIDATION: {_price(liquidation, tick)}",
        f"SIZE:        {plan.quantity}",
        f"NET R:R      {plan.net_rr}",
    ]
    lines += _jatah_risiko(note)
    lines += _konteks_historis(note)
    lines += _penilaian(plan, note)
    lines += _kenapa(note)
    lines += _invalidation(plan)
    if model_version:
        lines += ["", f"MODEL:       {model_version}"]
    lines += ["", wib(now)]

    caveats = list(getattr(plan, "caveats", ()) or ())
    if caveats:
        lines += ["", "YANG MELEMAHKAN INI:"]
        lines += [f"  - {c}" for c in caveats[:MAX_PUSHED_CAVEATS]]
        if len(caveats) > MAX_PUSHED_CAVEATS:
            # Deliberately does NOT say "kirim /plans". It used to, and /plans
            # renders no caveats at all - so the message directed the reader to
            # a place the rest of them were not. Pointing somewhere empty is
            # worse than not pointing: the reader believes they have seen the
            # whole list once they get there.
            lines.append(
                f"  ({len(caveats) - MAX_PUSHED_CAVEATS} caveat lagi tidak "
                "ditampilkan di sini)"
            )

    lines += [
        "",
        "Stop adalah tempat ide ini terbukti salah. Kalau stop kena, idenya",
        "memang salah, dan itulah hasil yang sudah direncanakan.",
        "",
        "Tidak ada apa pun di sini yang merupakan instruksi. ARUNA tidak",
        "memasang order, tidak mengubah setting leverage atau margin, dan",
        "tidak memindahkan dana. Mau bertindak, mengabaikan, atau menimpanya",
        "adalah keputusan Anda.",
        "",
        # PASAL 14.26 dan 14.44, dua baris dan bukan satu paragraf lagi.
        #
        # Paragraf di atas mengatakan hal yang sama dan tetap ada - ia
        # menjelaskan. Yang ditambahkan di sini adalah bentuk yang **selamat
        # dari pembacaan sekilas**: notifikasi ponsel memotong bagian tengah
        # pesan, dan sebuah pesan berisi entry, stop, dan leverage yang
        # terpotong sebelum paragraf itu sampai terbaca sebagai perintah.
        *KAKI,
    ]
    if test_mode:
        # Di kedua ujung, seperti ARUNA RESULT dan ARUNA ANALYSIS: notifikasi
        # ponsel memotong bagian tengah pesan, dan yang tersisa di layar kunci
        # adalah awal dan akhirnya.
        lines.append("")
        lines.append(TEST_BANNER)
    return "\n".join(lines)



__all__ = [
    "DAILY_GRACE",
    "DAILY_OPENS",
    "FUTURES_DAILY_KEY",
    "MAX_PUSHED_CAVEATS",
    "WIB",
    "PlanNotifier",
    "day_window",
    "due_day",
]
