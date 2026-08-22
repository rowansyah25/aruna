"""Push notifications for the futures loop (FUTURES SPEC 48, 50, 51).

The properties worth protecting here are all about restraint: what the push
does NOT contain, and how rarely it fires. A notification that always arrives
is ignored exactly when it finally matters, and one that arrives carrying an
entry price has skipped the step where the reader decides to look.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.futures.notify import PlanNotifier
from aruna.futures.plan import ForbiddenClaim, PlanVerdict

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def isoformat_of(moment: datetime) -> str:
    from aruna.core.clock import isoformat

    return isoformat(moment)


class _Sender:
    def __init__(self, *, ok: bool = True) -> None:
        self.sent: list[str] = []
        self._ok = ok

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self._ok


def _plan(symbol="BTCUSDT", verdict=PlanVerdict.PLAN, **overrides):
    base = {
        "symbol": symbol,
        "verdict": verdict,
        "side": SimpleNamespace(value="LONG"),
        "entry": Decimal("63000"),
        "stop": Decimal("61800"),
        "target": Decimal("64500"),
        "quantity": Decimal("0.416"),
        "leverage": 10,
        "margin_mode": SimpleNamespace(value="ISOLATED"),
        "liquidation": SimpleNamespace(price=Decimal("56927.71084337349")),
        "net_rr": Decimal("1.17"),
        "tick_size": Decimal("0.10"),
        "caveats": (),
    }
    return SimpleNamespace(**(base | overrides))


def _notifier(sender=None, hours=4.0):
    return PlanNotifier(sender=sender or _Sender(), horizon_hours=hours)


class TestOnlyPlansAreAnnounced:
    @pytest.mark.asyncio
    async def test_a_plan_is_announced(self) -> None:
        sender = _Sender()
        assert await _notifier(sender).announce([_plan()], now=NOW) == 1
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "verdict", [PlanVerdict.REFUSED, PlanVerdict.WAIT, PlanVerdict.NO_SIGNAL]
    )
    async def test_nothing_else_is(self, verdict) -> None:
        """Ninety-six refusals a day is a notification nobody reads."""
        sender = _Sender()
        assert await _notifier(sender).announce(
            [_plan(verdict=verdict)], now=NOW
        ) == 0
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_a_mixed_tick_announces_only_the_plan(self) -> None:
        sender = _Sender()
        plans = [
            _plan("BTCUSDT", PlanVerdict.PLAN),
            _plan("ETHUSDT", PlanVerdict.REFUSED),
            _plan("SOLUSDT", PlanVerdict.WAIT),
        ]
        assert await _notifier(sender).announce(plans, now=NOW) == 1
        assert "BTCUSDT" in sender.sent[0]
        assert len(sender.sent) == 1


class TestTheCooldown:
    """Re-planning every fifteen minutes means one setup can clear sixteen
    times in a four-hour window. Sixteen messages about one idea is the same
    noise arriving by a different route."""

    @pytest.mark.asyncio
    async def test_the_same_symbol_is_not_announced_twice_in_one_horizon(
        self,
    ) -> None:
        sender = _Sender()
        notifier = _notifier(sender, hours=4.0)
        await notifier.announce([_plan()], now=NOW)
        for minutes in (15, 30, 120, 239):
            await notifier.announce(
                [_plan()], now=NOW + timedelta(minutes=minutes)
            )
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_it_speaks_again_after_the_horizon(self) -> None:
        sender = _Sender()
        notifier = _notifier(sender, hours=4.0)
        await notifier.announce([_plan()], now=NOW)
        await notifier.announce([_plan()], now=NOW + timedelta(hours=4))
        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_the_cooldown_is_per_symbol(self) -> None:
        """A quiet BTC must not silence a fresh ETH."""
        sender = _Sender()
        notifier = _notifier(sender)
        await notifier.announce([_plan("BTCUSDT")], now=NOW)
        await notifier.announce(
            [_plan("ETHUSDT")], now=NOW + timedelta(minutes=15)
        )
        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_an_undelivered_message_does_not_retry_into_a_burst(self) -> None:
        """The plan is stored either way; the message is what failed.

        Rolling the cooldown back would turn one undelivered message into a
        flood the moment the network returned.
        """
        sender = _Sender(ok=False)
        notifier = _notifier(sender)
        await notifier.announce([_plan()], now=NOW)
        await notifier.announce([_plan()], now=NOW + timedelta(minutes=15))
        assert len(sender.sent) == 1


class TestTheAlertCarriesTheDecisionNumbers:
    """The operator asked for entry, stop and leverage in the push itself,
    having been told what that trades away. These assert they are there."""

    @pytest.mark.asyncio
    async def test_entry_stop_and_leverage_are_all_present(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        text = sender.sent[0]
        assert "ENTRY:" in text and "63000.00" in text
        assert "STOP:" in text and "61800.00" in text
        assert "LEVERAGE:" in text and "10x" in text
        assert "TARGET:" in text and "64500.00" in text
        assert "LONG" in text

    @pytest.mark.asyncio
    async def test_leverage_never_travels_without_a_liquidation_price(self) -> None:
        """A leverage figure alone says how large the position is and not how
        far wrong it may go before the exchange closes it."""
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        text = sender.sent[0]
        assert "LIQUIDATION:" in text
        assert "56927.70" in text  # snapped to the venue tick, not 23 digits
        assert text.index("LEVERAGE:") < text.index("LIQUIDATION:")

    @pytest.mark.asyncio
    async def test_prices_are_at_the_venue_tick_not_the_division(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        assert "56927.71084337349" not in sender.sent[0]

    @pytest.mark.asyncio
    async def test_the_stop_is_explained_not_just_listed(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        assert "Stop adalah tempat ide ini terbukti salah" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_caveats_travel_with_the_numbers(self) -> None:
        sender = _Sender()
        plan = _plan(caveats=("funding is a projection", "liquidity is thin"))
        await _notifier(sender).announce([plan], now=NOW)
        text = sender.sent[0]
        assert "YANG MELEMAHKAN INI:" in text
        assert "funding is a projection" in text

    @pytest.mark.asyncio
    async def test_a_long_caveat_list_is_trimmed_and_says_so(self) -> None:
        """All of them would push the decision numbers off a phone screen,
        which defeats the reason the numbers are here."""
        sender = _Sender()
        plan = _plan(caveats=tuple(f"caveat {i}" for i in range(10)))
        await _notifier(sender).announce([plan], now=NOW)
        text = sender.sent[0]
        assert "caveat 0" in text
        assert "caveat 9" not in text
        # Deliberately does NOT point at /plans: that command renders no
        # caveats, so the old wording sent the reader somewhere the rest of
        # them were not - and they would believe they had seen the whole list.
        assert "caveat lagi tidak ditampilkan di sini" in text
        assert "/plans" not in text.split("YANG MELEMAHKAN INI:")[1]

    @pytest.mark.asyncio
    async def test_each_plan_gets_its_own_message(self) -> None:
        """Two symbols' numbers in one message is how a reader acts on the
        wrong one."""
        sender = _Sender()
        plans = [_plan("BTCUSDT"), _plan("ETHUSDT")]
        assert await _notifier(sender).announce(plans, now=NOW) == 2
        assert len(sender.sent) == 2
        assert "BTCUSDT" in sender.sent[0]
        assert "ETHUSDT" in sender.sent[1]

    @pytest.mark.asyncio
    async def test_it_states_that_nothing_is_an_instruction(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        # The disclaimer is hard-wrapped for a phone screen, so the sentences
        # are matched against the unwrapped text rather than line by line.
        text = " ".join(sender.sent[0].split())
        assert "Tidak ada apa pun di sini yang merupakan instruksi" in text
        assert "ARUNA tidak memasang order" in text
        assert "Mau bertindak, mengabaikan, atau menimpanya adalah keputusan Anda" in (
            text
        )

    @pytest.mark.asyncio
    async def test_a_forbidden_claim_is_refused_on_the_push_path_too(self) -> None:
        """The rendered plan is guarded and the JSON is guarded; the push is
        the only message that arrives unasked, so it is guarded as well."""
        from aruna.futures.notify import _guard

        with pytest.raises(ForbiddenClaim):
            _guard("ARUNA FUTURES\n\npasti profit")


def _note(symbol="BTCUSDT", *, confidence=0.62, disagreement=0.31, reasons=()):
    from aruna.futures.debate import CouncilNote
    from aruna.notify.verdict import VoteSplit

    return CouncilNote(
        symbol=symbol,
        confidence=confidence,
        disagreement=disagreement,
        split=VoteSplit(("TECHNICAL", "MACRO"), ("STRUCTURE",)),
        reasons=tuple(reasons),
    )


class TestSatuGayaDenganArunaResult:
    """Operator: "foto satu sama dua apa bedanya, kenapa ga pakai satu format".

    Dua pesan dari satu sistem yang terlihat berasal dari dua sistem: yang satu
    kolom sejajar tanpa penanda warna, yang lain label bertingkat dengan 🟡.
    """

    @pytest.mark.asyncio
    async def test_long_bertanda_hijau(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        assert sender.sent[0].startswith("🟢 ARUNA FUTURES")

    @pytest.mark.asyncio
    async def test_short_bertanda_merah(self) -> None:
        sender = _Sender()
        plan = _plan(side=SimpleNamespace(value="SHORT"))
        await _notifier(sender).announce([plan], now=NOW)
        assert sender.sent[0].startswith("🔴 ARUNA FUTURES")

    @pytest.mark.asyncio
    async def test_sisi_asing_tidak_diberi_warna_arah(self) -> None:
        """Penanda hijau pada sisi yang tidak dikenali adalah tebakan yang
        terbaca sebagai pernyataan."""
        sender = _Sender()
        plan = _plan(side=SimpleNamespace(value="SESUATU_BARU"))
        await _notifier(sender).announce([plan], now=NOW)
        assert sender.sent[0].startswith("🟡 ARUNA FUTURES")

    @pytest.mark.asyncio
    async def test_label_dan_nilai_satu_baris(self) -> None:
        """Kolom sejajar, bukan bertingkat.

        Tata letak ini sempat diubah menjadi label-di-barisnya-sendiri demi
        keseragaman dengan ARUNA RESULT, lalu dikembalikan setelah operator
        melihat keduanya: bertingkat mendorong sebagian angka keputusan keluar
        satu layar ponsel.
        """
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        teks = sender.sent[0]

        assert "SIDE:        LONG" in teks
        assert "ENTRY:       63000.00" in teks
        assert "LEVERAGE:    10x  ISOLATED" in teks

    @pytest.mark.asyncio
    async def test_likuidasi_tetap_di_baris_setelah_leverage(self) -> None:
        """Dua angka yang hanya berarti bersama-sama tidak boleh terpisah:
        leverage sendirian mengatakan seberapa besar posisinya, bukan seberapa
        jauh ia boleh salah sebelum bursa menutupnya."""
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        baris = sender.sent[0].splitlines()

        i = next(n for n, b in enumerate(baris) if b.startswith("LEVERAGE:"))
        assert baris[i + 1].startswith("LIQUIDATION:"), baris[i : i + 3]


class TestPenandaUjiCoba:
    """Operator: "kalau kamu test fitur kasih peringatan, jangan pakai data
    real, baru kalau selesai test pakai data real lagi."

    Ini jalur yang paling berbahaya untuk diuji tanpa penanda: pesannya membawa
    entry, stop, leverage dan harga likuidasi.
    """

    @pytest.mark.asyncio
    async def test_penanda_di_kedua_ujung(self) -> None:
        from aruna.notify.verdict import TEST_BANNER

        sender = _Sender()
        await PlanNotifier(sender=sender, test_mode=True).announce(
            [_plan()], now=NOW
        )
        baris = sender.sent[0].splitlines()

        assert baris[0] == TEST_BANNER
        assert baris[-1] == TEST_BANNER

    @pytest.mark.asyncio
    async def test_pesan_sungguhan_tidak_ditandai(self) -> None:
        """Penanda yang muncul di pesan asli mengajari operator
        mengabaikannya - dan penanda yang diabaikan tidak menjaga apa pun."""
        from aruna.notify.verdict import TEST_BANNER

        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        assert TEST_BANNER not in sender.sent[0]

    def test_defaultnya_mati(self) -> None:
        assert PlanNotifier(sender=_Sender()).test_mode is False

    def test_perintah_notify_test_ikut_menguji_pesan_futures(self) -> None:
        """Mode uji coba yang tidak pernah dipanggil tidak melindungi apa pun.

        Sampai ini ada, blok ARUNA FUTURES adalah satu-satunya pesan yang tidak
        pernah ikut ``notify-test`` - jadi satu-satunya yang tata letaknya tidak
        pernah dilihat sebelum sampai ke ponsel.
        """
        from aruna.cli import _contoh_futures
        from aruna.notify.verdict import TEST_BANNER, VoteSplit

        teks = _contoh_futures(VoteSplit(("TECHNICAL",), ("RISK",)))

        assert teks.splitlines()[0] == TEST_BANNER
        assert teks.splitlines()[-1] == TEST_BANNER
        assert "ARUNA FUTURES" in teks

    def test_perintahnya_benar_benar_mencetak_pesan_futures(self, capsys) -> None:
        """Memanggil pembuat contohnya sendiri hanya membuktikan ia bekerja.

        Yang harus dibuktikan adalah ``notify-test`` memanggilnya - kalau tidak,
        blok ini kembali menjadi satu-satunya pesan yang tidak pernah dilihat
        sebelum sampai ke ponsel.
        """
        import argparse

        from aruna.cli import cmd_notify_test

        cmd_notify_test(argparse.Namespace(print_only=True))
        keluar = capsys.readouterr().out

        assert "ARUNA FUTURES" in keluar
        assert "LIQUIDATION:" in keluar

    def test_semua_contoh_bertanda_uji_coba(self, capsys) -> None:
        """Satu pesan tanpa penanda di antara empat yang bertanda adalah persis
        pesan yang akan disangka sungguhan."""
        import argparse

        from aruna.cli import cmd_notify_test
        from aruna.notify.verdict import TEST_BANNER

        cmd_notify_test(argparse.Namespace(print_only=True))
        blok = [
            b.strip() for b in capsys.readouterr().out.split("-" * 40) if b.strip()
        ]

        assert len(blok) >= 5
        for b in blok:
            assert b.splitlines()[0] == TEST_BANNER, b.splitlines()[0]

    def test_contohnya_tidak_memakai_simbol_pasar_sungguhan(self) -> None:
        """Operator: "jangan pakai data real". Angka karangan pada nama aset
        yang nyata tetap terbaca sebagai kabar tentang aset itu."""
        from aruna.cli import _contoh_futures
        from aruna.notify.verdict import VoteSplit

        teks = _contoh_futures(VoteSplit((), ()))
        # Baris simbolnya saja, bukan seluruh pesan: "ISOLATED" memuat "SOL",
        # dan pencarian substring atas seluruh teks akan menuduh kata margin
        # mode sebagai nama aset.
        judul = next(b for b in teks.splitlines() if "ARUNA FUTURES" in b)
        for nyata in ("BTC", "ETH", "SOL", "XRP", "BNB"):
            assert nyata not in judul, nyata
        assert "CONTOH" in judul

    def test_semua_contoh_memakai_simbol_karangan(self, capsys) -> None:
        """Bukan hanya yang futures. Satu ticker sungguhan di antara lima pesan
        uji coba adalah persis pesan yang akan disangka nyata - mata menangkap
        namanya lebih dulu daripada membaca penandanya."""
        import argparse

        from aruna.cli import cmd_notify_test

        cmd_notify_test(argparse.Namespace(print_only=True))
        keluar = capsys.readouterr().out

        judul = [
            b for b in keluar.splitlines()
            if "ARUNA FUTURES" in b or "PERPETUAL" in b
        ]
        assert len(judul) >= 5
        for baris in judul:
            for nyata in ("BTC", "ETH", "SOL", "XRP", "BNB"):
                assert nyata not in baris, baris

    def test_penanda_menyebut_test_dua_kali(self) -> None:
        """Diminta begitu: pembacaan sekilas di layar kunci harus menabrak kata
        itu berapa pun bagian pesan yang terpotong."""
        from aruna.notify.verdict import TEST_BANNER

        assert TEST_BANNER.count("TEST") == 2
        assert TEST_BANNER.upper() == TEST_BANNER


class TestPenilaianIkutDiPesanPlan:
    """Operator: "penilaian belum muncul di aruna futures btcusdt".

    Pesan ini dulu memuat angka posisi saja. Angka tanpa penilaian terbaca
    lebih pasti daripada yang sebenarnya - entry yang sama terlihat sama
    meyakinkan pada council yang bulat maupun yang menang tipis.
    """

    @pytest.mark.asyncio
    async def test_confidence_dan_disagreement_ikut(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()], now=NOW, notes={"BTCUSDT": _note()}
        )
        teks = sender.sent[0]
        assert "PENILAIAN:" in teks
        assert "62%" in teks
        assert "0.31" in teks

    @pytest.mark.asyncio
    async def test_hasil_pemilihan_ikut(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()], now=NOW, notes={"BTCUSDT": _note()}
        )
        teks = sender.sent[0]
        assert "SETUJU" in teks and "KONTRA" in teks
        assert "TECHNICAL" in teks and "STRUCTURE" in teks

    @pytest.mark.asyncio
    async def test_buffer_likuidasi_ikut(self) -> None:
        """Ada di render lengkap sejak lama dan tidak pernah ada di push."""
        sender = _Sender()
        plan = _plan(buffer=SimpleNamespace(band="SEMPIT", score=42))
        await _notifier(sender).announce([plan], now=NOW)
        assert "BUFFER LIKUIDASI: SEMPIT (42/100)" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_perdebatan_disebut_kalau_memang_ada(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()],
            now=NOW,
            notes={"BTCUSDT": _note(reasons=("2 veto diajukan, semuanya ditolak",))},
        )
        teks = sender.sent[0]
        assert "YANG DIPERDEBATKAN:" in teks
        assert "2 veto diajukan" in teks

    @pytest.mark.asyncio
    async def test_council_bulat_tidak_disebut_berdebat(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()], now=NOW, notes={"BTCUSDT": _note(reasons=())}
        )
        assert "YANG DIPERDEBATKAN:" not in sender.sent[0]

    @pytest.mark.asyncio
    async def test_disagreement_tinggi_dijelaskan(self) -> None:
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()], now=NOW, notes={"BTCUSDT": _note(disagreement=0.88)}
        )
        assert "cara yang sangat berbeda" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_tanpa_catatan_plan_tetap_dikirim(self) -> None:
        """Menahan angkanya karena satu bagian hilang akan menghilangkan
        justru yang diminta operator."""
        sender = _Sender()
        assert await _notifier(sender).announce([_plan()], now=NOW) == 1
        assert "ENTRY:" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_tanpa_catatan_tidak_mencetak_angka_karangan(self) -> None:
        """"CONFIDENCE: -" terbaca seperti nol, dan nol adalah pernyataan."""
        sender = _Sender()
        await _notifier(sender).announce([_plan()], now=NOW)
        teks = sender.sent[0]
        assert "CONFIDENCE" not in teks
        assert "tidak tersedia" in teks

    @pytest.mark.asyncio
    async def test_catatan_simbol_lain_tidak_dipakai(self) -> None:
        """Council mengeja BTC/USDT, plan bernama BTCUSDT. Kalau kuncinya
        salah, bagian ini diam-diam hilang - bukan error, hanya tidak ada."""
        sender = _Sender()
        await _notifier(sender).announce(
            [_plan()], now=NOW, notes={"BTC/USDT": _note(symbol="BTC/USDT")}
        )
        assert "CONFIDENCE" not in sender.sent[0]


class _Build:
    """Pembangun laporan yang mencatat jendela yang diminta padanya."""

    def __init__(self, text: str | None = "report") -> None:
        self.text = text
        self.windows: list[tuple[datetime, datetime]] = []

    async def __call__(self, awal: datetime, akhir: datetime) -> str | None:
        self.windows.append((awal, akhir))
        return self.text


class _State:
    """``app_state`` seukuran yang dibutuhkan: satu kunci, satu nilai."""

    def __init__(self, stored: dict | None = None) -> None:
        self.stored = stored
        self.writes: list[dict] = []

    async def get(self, key: str):
        return self.stored

    async def set(self, key: str, value: dict, *, actor: str) -> None:
        self.stored = value
        self.writes.append(value)


def _wib(y, m, d, hh, mm=0):
    from aruna.futures.notify import WIB

    return datetime(y, m, d, hh, mm, tzinfo=WIB)


class TestTheDailyReport:
    """Penutup hari, bukan penanda menyala.

    Tiga keluhan operator berujung ke satu metode: laporan futures datang pada
    setiap kelahiran ulang proses alih-alih pada penutup hari.
    """

    @pytest.mark.asyncio
    async def test_menjelang_tengah_malam_wib_dikirim(self) -> None:
        sender, build = _Sender(), _Build()
        assert await _notifier(sender).daily(build, now=_wib(2026, 8, 18, 23, 59)) is True
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_di_luar_jendela_tidak_dikirim(self) -> None:
        """Ini cacat yang dikeluhkan: laporan datang saat proses menyala.

        Penjaga proses menghidupkan ulang loop futures tiap dua puluh empat
        jam, dan sebelumnya tick pertama setiap proses selalu lolos - penanda
        "sudah terkirim" dimulai kosong dan tidak ada syarat waktu sama sekali.
        """
        sender, build = _Sender(), _Build()
        siang = _wib(2026, 8, 18, 10, 30)
        assert await _notifier(sender).daily(build, now=siang) is False
        assert sender.sent == []
        # Dan tidak menyentuh database untuk laporan yang tidak jadi dikirim.
        assert build.windows == []

    @pytest.mark.asyncio
    async def test_terlambat_lewat_tengah_malam_masih_dikirim(self) -> None:
        """Loop berdetak tiap 900 detik, jadi menit 23:59 sering terlewat."""
        sender, build = _Sender(), _Build()
        assert await _notifier(sender).daily(build, now=_wib(2026, 8, 19, 0, 30)) is True
        awal, akhir = build.windows[0]
        assert (awal.date().isoformat(), akhir.date().isoformat()) == (
            "2026-08-18", "2026-08-19",
        )

    @pytest.mark.asyncio
    async def test_terlalu_terlambat_tidak_dikirim_lagi(self) -> None:
        """Jendelanya menutup. Tanpa penutup, proses yang menyala jam sepuluh
        pagi akan menemukan "kemarin belum dilaporkan" dan mengirimnya."""
        sender, build = _Sender(), _Build()
        assert await _notifier(sender).daily(build, now=_wib(2026, 8, 19, 3, 0)) is False
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_sekali_saja_walau_ditanya_berkali_kali(self) -> None:
        sender, build = _Sender(), _Build()
        notifier = _notifier(sender)
        assert await notifier.daily(build, now=_wib(2026, 8, 18, 23, 59)) is True
        assert await notifier.daily(build, now=_wib(2026, 8, 19, 0, 5)) is False
        assert await notifier.daily(build, now=_wib(2026, 8, 19, 0, 40)) is False
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_hari_berikutnya_dikirim_lagi(self) -> None:
        sender, build = _Sender(), _Build()
        notifier = _notifier(sender)
        await notifier.daily(build, now=_wib(2026, 8, 18, 23, 59))
        assert await notifier.daily(build, now=_wib(2026, 8, 19, 23, 59)) is True
        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_jendelanya_satu_hari_wib_penuh(self) -> None:
        """Bukan "dua puluh empat jam terakhir". Jendela bergulir berarti
        angkanya tidak pernah benar-benar direset di batas hari."""
        build = _Build()
        await _notifier().daily(build, now=_wib(2026, 8, 18, 23, 59))
        awal, akhir = build.windows[0]
        assert (awal.hour, awal.minute) == (0, 0)
        assert akhir - awal == timedelta(days=1)
        assert awal.date().isoformat() == "2026-08-18"

    @pytest.mark.asyncio
    async def test_restart_tidak_mengirim_ulang_hari_yang_sama(self) -> None:
        """Penanda di memori hilang tiap proses mati. Yang di app_state tidak."""
        state = _State()
        sender_a, build = _Sender(), _Build()
        lama = PlanNotifier(sender=sender_a, state=state)
        assert await lama.daily(build, now=_wib(2026, 8, 18, 23, 59)) is True
        assert state.stored == {"date": "2026-08-18"}

        # Proses baru, ingatan kosong, jendela yang sama.
        sender_b = _Sender()
        baru = PlanNotifier(sender=sender_b, state=state)
        assert await baru.daily(build, now=_wib(2026, 8, 19, 0, 20)) is False
        assert sender_b.sent == []

    @pytest.mark.asyncio
    async def test_gagal_kirim_tidak_menstempel(self) -> None:
        """Satu kegagalan jaringan tidak boleh menghapus laporan hari itu."""
        state = _State()
        gagal = _Sender(ok=False)
        notifier = PlanNotifier(sender=gagal, state=state)
        assert await notifier.daily(_Build(), now=_wib(2026, 8, 18, 23, 59)) is False
        assert state.stored is None

        berhasil = _Sender()
        notifier.sender = berhasil
        assert await notifier.daily(_Build(), now=_wib(2026, 8, 18, 23, 59)) is True

    @pytest.mark.asyncio
    async def test_hari_kosong_tidak_dikirim_dan_tidak_distempel(self) -> None:
        state = _State()
        sender = _Sender()
        notifier = PlanNotifier(sender=sender, state=state)
        assert await notifier.daily(_Build(None), now=_wib(2026, 8, 18, 23, 59)) is False
        assert sender.sent == []
        assert state.stored is None


class TestJendelaHarian:
    """``due_day`` sendirian, tanpa pengirim."""

    def test_sebelum_jendela_kosong(self) -> None:
        from aruna.futures.notify import due_day

        assert due_day(_wib(2026, 8, 18, 23, 58)) is None

    def test_tepat_di_pembukaan_terisi(self) -> None:
        from aruna.futures.notify import due_day

        assert due_day(_wib(2026, 8, 18, 23, 59)).isoformat() == "2026-08-18"

    def test_setelah_tengah_malam_masih_hari_kemarin(self) -> None:
        """Tanggal yang sama sebelum dan sesudah tengah malam - itulah yang
        membuat keterlambatan tidak pernah menghasilkan laporan kedua."""
        from aruna.futures.notify import due_day

        assert due_day(_wib(2026, 8, 19, 1, 58)).isoformat() == "2026-08-18"

    def test_setelah_masa_tenggang_kosong(self) -> None:
        from aruna.futures.notify import due_day

        assert due_day(_wib(2026, 8, 19, 2, 0)) is None

    def test_dihitung_dengan_jam_wib_bukan_utc(self) -> None:
        """23:59 UTC adalah 06:59 WIB keesokan harinya - di luar jendela."""
        from aruna.futures.notify import due_day

        assert due_day(datetime(2026, 8, 18, 23, 59, tzinfo=UTC)) is None
        # Dan 16:59 UTC adalah 23:59 WIB, yang justru di dalamnya.
        assert due_day(datetime(2026, 8, 18, 16, 59, tzinfo=UTC)) is not None


class TestTheSenderNeverLeaksTheToken:
    """SPEC 43. The token lives in the URL, which is the shape that leaked a
    credential into logs/aruna.log once already."""

    def test_the_url_is_not_stored_on_the_instance(self) -> None:
        from aruna.notify.telegram.sender import TelegramSender

        sender = TelegramSender(token="8123456789:AAF-secret", chat_id="1")
        assert "8123456789:AAF-secret" not in repr(vars(sender)).replace(
            "_token", ""
        ).replace("'8123456789:AAF-secret'", "", 1)

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_scrubbed(self) -> None:

        from aruna.core.redaction import MASK, configure_redactor
        from aruna.notify.telegram.sender import TelegramSender

        token = "8123456789:AAF-verysecret-token-value"
        configure_redactor({token})

        captured: list[str] = []

        class _Log:
            def warning(self, _event, **fields):
                captured.append(str(fields))

        sender = TelegramSender(token=token, chat_id="1")
        import aruna.notify.telegram.sender as module

        original, module.log = module.log, _Log()
        try:
            # An unroutable host: httpx raises with the request URL attached.
            sender._api_root = "http://127.0.0.1:1"
            assert await sender.send("hello") is False
        finally:
            module.log = original

        joined = " ".join(captured)
        assert token not in joined
        if token[:12] in joined:  # only if the URL made it into the message
            assert MASK in joined

    @pytest.mark.asyncio
    async def test_an_unconfigured_sender_sends_nothing(self) -> None:
        from aruna.notify.telegram.sender import TelegramSender

        sender = TelegramSender(token="", chat_id="")
        assert sender.configured is False
        assert await sender.send("anything") is False


class TestLongMessages:
    def test_a_long_report_is_cut_on_a_line_and_says_so(self) -> None:
        """Cutting mid-number turns a truncated report into a wrong one."""
        from aruna.notify.telegram.sender import MAX_MESSAGE_CHARS, _truncate

        text = "\n".join(f"line {i} with some content" for i in range(500))
        out = _truncate(text)
        assert len(out) <= MAX_MESSAGE_CHARS
        assert "[truncated" in out
        assert "/plans" in out
