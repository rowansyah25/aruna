"""Hasil dan signal yang didorong ke Telegram (PASAL 11, 12).

Cacat aslinya: ``format_result`` punya tepat satu pemanggil, perintah CLI.
Prediksi diskor tiap menit dan hasilnya berhenti di database - operator diberi
tahu saat ARUNA berpendapat dan tidak pernah diberi tahu saat ARUNA salah.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.core.enums import Decision
from aruna.notify.result import (
    MAX_PER_CYCLE,
    WIN_CLASSES,
    ResultNotifier,
    SignalNotifier,
    classify,
    render_result,
)
from aruna.notify.verdict import TEST_BANNER, InternalVocabularyLeak, VoteSplit

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class _Sender:
    def __init__(self, *, ok: bool = True, ready: bool | None = None) -> None:
        self.ok = ok
        self._ready = ready
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


class _SenderBelumSiap(_Sender):
    def ready(self) -> bool:
        return False


def _row(**kwargs):
    base = {
        "symbol": "BTC/USDT",
        "decision": Decision.BUY,
        "outcome_class": "TARGET_HIT",
        "signal_id": "abc123",
    }
    base.update(kwargs)
    return base


class TestKelasHasil:
    def test_menang_dan_kalah(self) -> None:
        assert classify("TARGET_HIT") == "WIN"
        assert classify("STOPPED_OUT") == "LOSS"

    def test_likuidasi_itu_kalah(self) -> None:
        """Ditutup paksa bursa lebih buruk daripada kena stop. Menaruhnya di
        luar hitungan akan membuang justru kekalahan terburuk."""
        assert classify("LIQUIDATED") == "LOSS"

    def test_yang_bukan_keduanya_tidak_dipaksa(self) -> None:
        """Menyebut sesuatu WIN atau LOSS yang bukan keduanya adalah
        memalsukan catatan."""
        assert classify("EXPIRED") is None
        assert classify("DIRECTION_ONLY") is None


class TestBlokHasil:
    def test_menang_hijau_kalah_merah(self) -> None:
        menang = render_result(**_row())
        kalah = render_result(**_row(outcome_class="STOPPED_OUT"))

        assert menang.startswith("🟢 ARUNA RESULT")
        assert kalah.startswith("🔴 ARUNA RESULT")

    def test_kalah_tidak_diperhalus(self) -> None:
        """Tidak ada peredam, tidak ada penundaan, tidak ada yang membuat LOSS
        lebih sepi daripada WIN."""
        kalah = render_result(**_row(outcome_class="STOPPED_OUT"))
        assert "RESULT:\nLOSS" in kalah

    def test_arah_pakai_kosakata_publik(self) -> None:
        teks = render_result(**_row(decision=Decision.SELL))
        assert "DECISION:\nSHORT" in teks
        assert "SELL" not in teks

    def test_kelas_lain_dicetak_apa_adanya(self) -> None:
        teks = render_result(**_row(outcome_class="EXPIRED"))
        assert "RESULT:\nEXPIRED" in teks
        assert "🟡 ARUNA RESULT" in teks

    def test_signal_id_ikut(self) -> None:
        assert "abc123" in render_result(**_row())

    def test_suara_asli_ditampilkan_kalau_ada(self) -> None:
        split = VoteSplit(setuju=("TECHNICAL",), kontra=("RISK",))
        teks = render_result(**_row(votes=split))

        assert "TECHNICAL" in teks
        assert "1 VS 1" in teks
        # Judul blok ini "HASIL PEMILIHAN AWAL:", dan hanya itu. Sebelum
        # diperbaiki, render_votes menempelkan judulnya sendiri tepat di
        # bawahnya - dua judul beruntun, terlihat seperti pesan yang tertempel
        # dua kali.
        assert teks.count("HASIL PEMILIHAN") == 1

    def test_format_hasil_tanpa_indentasi(self) -> None:
        teks = render_result(**_row(
            votes=VoteSplit(setuju=("TECHNICAL",), kontra=("RISK",)),
            entry="63000", target="66000", stop="61500", trigger="TP HIT",
        ))
        for baris in teks.splitlines():
            assert baris == baris.lstrip(), repr(baris)

    def test_agregat_disebut_agregat(self) -> None:
        """Tabel agent_decisions kosong: nama siapa di sisi mana tidak ada.
        Judul "SETUJU:" dengan daftar kosong akan terbaca sebagai "tidak ada
        yang setuju" - kebalikan dari kebenarannya, yaitu "tidak dicatat"."""
        teks = render_result(**_row(council={
            "participating_agents": 7, "total_agents": 9,
            "objection_count": 3, "correction_count": 1,
        }))

        assert "7 dari 9 agent" in teks
        assert "tidak tersimpan" in teks
        assert "SETUJU:" not in teks

    def test_tanpa_catatan_apa_pun_dikatakan(self) -> None:
        teks = render_result(**_row())
        assert "Tidak ada catatan pemilihan" in teks

    def test_lolos_penjaga_kosakata(self) -> None:
        for kelas in ("TARGET_HIT", "STOPPED_OUT", "LIQUIDATED"):
            assert render_result(**_row(outcome_class=kelas))

    def test_kosakata_internal_tetap_ditolak(self) -> None:
        try:
            render_result(**_row(decision="WAIT", outcome_class="EXPIRED"))
        except InternalVocabularyLeak:  # pragma: no cover - jalur aman
            raise AssertionError("WAIT seharusnya diterjemahkan, bukan ditolak") from None


class TestHargaTidakDicetakMentah:
    """Terlihat di layar operator: ``ENTRY: 0.995500000000``.

    Sepuluh nol di belakangnya datang dari kolom ``DECIMAL(30,12)``; dua belas
    angka di belakang koma pada harga yang bergerak per 0,0001 bukan ketelitian,
    ia menyiratkan ketelitian yang tidak dimiliki angkanya.
    """

    def test_nol_berekor_dibuang(self) -> None:
        teks = render_result(**_row(entry="0.995500000000"))
        assert "ENTRY:\n0.9955" in teks
        assert "0.995500000000" not in teks

    def test_presisi_dipotong_di_batas(self) -> None:
        teks = render_result(**_row(
            entry="0.9955", target="0.992561339549", stop="0.998439201113",
        ))
        assert "TP:\n0.99256134" in teks
        assert "SL:\n0.9984392" in teks

    def test_pembulatannya_setengah_ke_atas(self) -> None:
        teks = render_result(**_row(target="1.000000005"))
        assert "TP:\n1.00000001" in teks

    def test_presisi_entry_tidak_dipakai_menebak_yang_lain(self) -> None:
        """Versi pertama membulatkan target ke presisi entry. Pada entry
        ``1.0000`` - yang nol berekornya adalah padding kolom, bukan pernyataan
        soal tick - presisinya terbaca nol dan target ``1.00005`` menjadi
        ``1``. Padding penyimpanan dan presisi kutipan tidak bisa dibedakan.
        """
        teks = render_result(**_row(entry="1.0000", target="1.00005"))
        assert "TP:\n1.00005" in teks

    def test_bilangan_bulat_tidak_jadi_notasi_eksponen(self) -> None:
        """``Decimal('63000').normalize()`` menulis ``6.3E+4``, yang untuk
        harga terbaca seperti salah ketik."""
        teks = render_result(**_row(entry="63000.00"))
        assert "ENTRY:\n63000" in teks
        assert "E+" not in teks

    def test_yang_bukan_angka_dibiarkan(self) -> None:
        assert "ENTRY:\ntidak diketahui" in render_result(
            **_row(entry="tidak diketahui")
        )


class TestKosakataHasilTidakBocor:
    """PASAL 1 dan 3. ``RESULT: WRONG_FROM_START`` adalah nama pengenal di
    dalam mesin - huruf besar, garis bawah, bahasa Inggris - dan ia sampai ke
    layar operator."""

    def test_kelas_diterjemahkan(self) -> None:
        teks = render_result(**_row(outcome_class="WRONG_FROM_START"))
        assert "arahnya salah sejak awal" in teks

    def test_kelasnya_tetap_disebut(self) -> None:
        """Yang tersimpan tetap terlihat, supaya pesan masih bisa dicocokkan
        dengan barisnya di database."""
        teks = render_result(**_row(outcome_class="WRONG_FROM_START"))
        assert "(WRONG_FROM_START)" in teks

    @pytest.mark.parametrize(
        "kelas",
        ["WRONG_FROM_START", "RIGHT_THEN_REVERSED", "RIGHT_DIRECTION_BAD_TIMING",
         "TARGET_NOT_REACHED", "HORIZON_MISMATCH", "NO_POSITION"],
    )
    def test_setiap_kelas_spec_23_punya_kalimatnya(self, kelas: str) -> None:
        from aruna.notify.result import OUTCOME_PUBLIC

        assert kelas in OUTCOME_PUBLIC

    def test_semua_kelas_enum_tercakup(self) -> None:
        """Kelas baru yang lupa diterjemahkan harus muncul sebagai suite merah,
        bukan sebagai nama internal di layar operator."""
        from aruna.notify.result import LOSS_CLASSES, OUTCOME_PUBLIC, WIN_CLASSES
        from aruna.signals.models import OutcomeClass

        tercakup = set(OUTCOME_PUBLIC) | set(WIN_CLASSES) | set(LOSS_CLASSES)
        assert {c.value for c in OutcomeClass} <= tercakup

    def test_menang_dan_kalah_tetap_satu_kata(self) -> None:
        """WIN dan LOSS sudah kata biasa. Menerjemahkannya jadi kalimat akan
        memperhalus kekalahan, dan itu justru yang dilarang."""
        assert "RESULT:\nWIN" in render_result(**_row())
        assert "RESULT:\nLOSS" in render_result(**_row(outcome_class="STOPPED_OUT"))


class TestKalahDikirimSepertiMenang:
    """Kelas outcome menjawab **bagaimana** prediksinya meleset. Tidak satu pun
    nilainya berarti "kalah".

    Selama pesan ini membacanya sebagai putusan menang-kalah, setiap kemenangan
    keluar 🟢 WIN dan setiap kekalahan keluar 🟡 dengan kalimat - kemenangan
    tegas, kekalahan samar. Spec-nya meminta kalah dikirim dengan cara yang sama
    persis dengan menang.
    """

    def test_kalah_merah_dan_berkata_loss(self) -> None:
        teks = render_result(**_row(
            outcome_class="WRONG_FROM_START", trade_result="LOSS",
        ))
        assert teks.startswith("🔴 ARUNA RESULT")
        assert "RESULT:\nLOSS - arahnya salah sejak awal (WRONG_FROM_START)" in teks

    def test_menang_hijau_dengan_bentuk_yang_sama(self) -> None:
        """Bentuknya harus identik. Kalau kekalahan diberi keterangan dan
        kemenangan tidak, asimetrinya kembali lewat pintu lain."""
        menang = render_result(**_row(
            outcome_class="TARGET_REACHED", trade_result="WIN",
        ))
        kalah = render_result(**_row(
            outcome_class="WRONG_FROM_START", trade_result="LOSS",
        ))
        pola = lambda t: [  # noqa: E731 - dipakai sekali, di sini
            b for b in t.splitlines() if b.startswith(("WIN", "LOSS"))
        ]
        assert len(pola(menang)) == len(pola(kalah)) == 1
        assert pola(menang)[0].count(" - ") == pola(kalah)[0].count(" - ") == 1

    def test_hasil_trade_mengalahkan_kelas_outcome(self) -> None:
        """``RIGHT_THEN_REVERSED`` tidak ada di daftar kalah mana pun, jadi
        tanpa hasil trade ia tercetak kuning - padahal posisinya rugi."""
        teks = render_result(**_row(
            outcome_class="RIGHT_THEN_REVERSED", trade_result="LOSS",
        ))
        assert teks.startswith("🔴 ARUNA RESULT")
        assert "RESULT:\nLOSS" in teks

    def test_menang_menurut_trade_walau_target_tak_tercapai(self) -> None:
        """Keluar di atas harga masuk sesudah ongkos tetap untung, target atau
        bukan. Memaksanya jadi bukan-menang akan memalsukan catatan ke arah
        yang lain."""
        teks = render_result(**_row(
            outcome_class="TARGET_NOT_REACHED", trade_result="WIN",
        ))
        assert teks.startswith("🟢 ARUNA RESULT")

    def test_breakeven_tidak_dipaksa_ke_salah_satu_sisi(self) -> None:
        teks = render_result(**_row(
            outcome_class="TARGET_NOT_REACHED", trade_result="BREAKEVEN",
        ))
        assert teks.startswith("🟡 ARUNA RESULT")
        assert "LOSS" not in teks and "WIN" not in teks

    def test_tanpa_hasil_trade_kembali_ke_cara_lama(self) -> None:
        """Prediksi yang tidak menghasilkan posisi memang tidak punya hasil
        perdagangan."""
        teks = render_result(**_row(outcome_class="EXPIRED"))
        assert "RESULT:\nEXPIRED" in teks


class TestDuaEjaanUntukSatuKemenangan:
    """``TARGET_REACHED`` (spot) dan ``TARGET_HIT`` (futures).

    ``WIN_CLASSES`` semula hanya memuat ejaan futures, sementara satu-satunya
    pemanggil ``render_result`` adalah jalur spot - jadi tidak satu pun prediksi
    spot yang menang pernah dicetak sebagai "WIN". Kegagalannya tidak berisik:
    pesannya tetap terkirim, tetap rapi, dan tetap salah.
    """

    def test_kelas_menang_spot_dihitung_menang(self) -> None:
        assert classify("TARGET_REACHED") == "WIN"

    def test_kelas_menang_futures_juga(self) -> None:
        assert classify("TARGET_HIT") == "WIN"

    def test_kemenangan_spot_bertanda_hijau(self) -> None:
        teks = render_result(**_row(outcome_class="TARGET_REACHED"))
        assert teks.startswith("🟢 ARUNA RESULT")
        assert "RESULT:\nWIN - target tercapai (TARGET_REACHED)" in teks

    def test_ejaannya_diambil_dari_enum_bukan_diketik(self) -> None:
        """Yang membuat cacat ini bertahan adalah daftar yang mengeja nama dari
        enum lain. Test ini merah kalau enum-nya berganti nama."""
        from aruna.signals.models import OutcomeClass

        assert OutcomeClass.TARGET_REACHED.value in WIN_CLASSES


class TestPenandaUjiCoba:
    """Pesan tes yang terbaca sebagai signal asli membuat operator bertindak
    atas angka yang sengaja dikarang untuk memeriksa tata letak."""

    def test_penanda_di_baris_pertama(self) -> None:
        teks = render_result(**_row(test_mode=True))
        assert teks.splitlines()[0] == TEST_BANNER

    def test_penanda_di_baris_terakhir(self) -> None:
        """Notifikasi ponsel sering memotong bagian tengah pesan; yang tersisa
        di layar kunci adalah kedua ujungnya."""
        teks = render_result(**_row(test_mode=True))
        assert teks.splitlines()[-1] == TEST_BANNER

    def test_huruf_besar_semua(self) -> None:
        assert TEST_BANNER.upper() == TEST_BANNER

    def test_pesan_biasa_tidak_ditandai(self) -> None:
        """Penanda yang muncul di pesan sungguhan mengajari operator
        mengabaikannya - dan penanda yang diabaikan tidak menjaga apa pun."""
        assert TEST_BANNER not in render_result(**_row())

    def test_analysis_juga_ditandai(self) -> None:
        from aruna.notify.verdict import render_analysis

        teks = render_analysis(
            symbol="BTC/USDT", decision=Decision.BUY,
            split=VoteSplit(setuju=("A",), kontra=()), test_mode=True,
        )
        assert teks.splitlines()[0] == TEST_BANNER
        assert teks.splitlines()[-1] == TEST_BANNER


class TestDorongHasil:
    async def test_hasil_baru_terkirim(self) -> None:
        sender = _Sender()
        n = await ResultNotifier(sender=sender, warmup=False).announce([_row()], now=NOW)

        assert n == 1
        assert len(sender.sent) == 1

    async def test_tidak_dikirim_dua_kali(self) -> None:
        sender = _Sender()
        notifier = ResultNotifier(sender=sender, warmup=False)

        await notifier.announce([_row()], now=NOW)
        await notifier.announce([_row()], now=NOW + timedelta(minutes=1))
        assert len(sender.sent) == 1

    async def test_gagal_kirim_boleh_dicoba_lagi(self) -> None:
        """Menstempel duluan berarti satu kegagalan jaringan menghapus hasil
        itu selamanya - dan yang hilang cenderung yang kalah, karena kalah
        datang berombongan."""
        sender = _Sender(ok=False)
        notifier = ResultNotifier(sender=sender, warmup=False)

        assert await notifier.announce([_row()], now=NOW) == 0
        sender.ok = True
        assert await notifier.announce([_row()], now=NOW) == 1

    async def test_ledakan_dibatasi(self) -> None:
        """Satu pass resolve bisa menutup puluhan prediksi sekaligus."""
        sender = _Sender()
        banyak = [_row(signal_id=f"id{i}") for i in range(20)]
        n = await ResultNotifier(sender=sender, warmup=False).announce(banyak, now=NOW)

        assert n == MAX_PER_CYCLE

    async def test_sisanya_menyusul_siklus_berikutnya(self) -> None:
        sender = _Sender()
        notifier = ResultNotifier(sender=sender, warmup=False)
        banyak = [_row(signal_id=f"id{i}") for i in range(8)]

        await notifier.announce(banyak, now=NOW)
        await notifier.announce(banyak, now=NOW + timedelta(minutes=1))
        assert len(sender.sent) == 8

    async def test_pass_pertama_hanya_dicatat(self) -> None:
        """Operator: "saat restart jangan hasil kemarin kemarin dimunculin".

        Saat ARUNA mati semalam, prediksi tetap jatuh tempo. Pass resolusi
        pertama sesudah menyala menutup semuanya sekaligus, dan semuanya
        terlihat "baru" bagi notifier yang ingatannya kosong.
        """
        sender = _Sender()
        notifier = ResultNotifier(sender=sender)
        kemarin = [_row(signal_id=f"lama{i}") for i in range(30)]

        assert await notifier.announce(kemarin, now=NOW) == 0
        assert sender.sent == []

    async def test_yang_dibungkam_tidak_menyusul_belakangan(self) -> None:
        """Kalau cuma ditunda, muntahannya datang satu siklus kemudian."""
        sender = _Sender()
        notifier = ResultNotifier(sender=sender)
        kemarin = [_row(signal_id=f"lama{i}") for i in range(30)]

        await notifier.announce(kemarin, now=NOW)
        await notifier.announce(kemarin, now=NOW + timedelta(minutes=1))
        assert sender.sent == []

    async def test_hasil_sesudahnya_tetap_terkirim(self) -> None:
        """Yang dibungkam hanya satu pass. Hasil berikutnya tetap sampai -
        kalau tidak, ini bukan meredam melainkan mematikan."""
        sender = _Sender()
        notifier = ResultNotifier(sender=sender)

        await notifier.announce([_row(signal_id="lama")], now=NOW)
        n = await notifier.announce(
            [_row(signal_id="baru")], now=NOW + timedelta(minutes=1)
        )
        assert n == 1
        assert len(sender.sent) == 1

    async def test_tanpa_bot_belum_dihitung_sebagai_pass_pertama(self) -> None:
        """Tanpa tujuan pengiriman, pass ini tidak pernah bisa mengirim apa
        pun. Memakainya sebagai "pass pertama" berarti backlog sesungguhnya
        lewat tanpa dibungkam, lalu ikut terkirim di pass berikutnya."""

        class _Belum:
            def __init__(self) -> None:
                self.sent: list[str] = []
                self.siap = False

            def ready(self) -> bool:
                return self.siap

            async def send(self, text: str) -> bool:
                self.sent.append(text)
                return True

        sender = _Belum()
        notifier = ResultNotifier(sender=sender)
        kemarin = [_row(signal_id=f"lama{i}") for i in range(10)]

        assert await notifier.announce(kemarin, now=NOW) == 0
        sender.siap = True
        assert await notifier.announce(kemarin, now=NOW) == 0
        assert sender.sent == []

    async def test_hasil_tanpa_arah_tidak_dikirim(self) -> None:
        """Terukur: 514 prediksi diskor dalam 24 jam, 426 di antaranya tanpa
        arah. Delapan puluh tiga persen pesan hasil berbunyi "DECISION: NO
        SIGNAL" - lewat jalur ketiga yang tidak tertutup saat dua jalur lain
        ditutup.
        """
        sender = _Sender()
        n = await ResultNotifier(sender=sender, warmup=False).announce(
            [_row(decision=Decision.WAIT), _row(decision=Decision.NO_SIGNAL)],
            now=NOW,
        )
        assert n == 0
        assert sender.sent == []

    async def test_hasil_berarah_tetap_dikirim(self) -> None:
        """Yang dihilangkan hanya yang tanpa arah. Menang dan kalah dari posisi
        yang benar-benar ada tetap sampai."""
        sender = _Sender()
        n = await ResultNotifier(sender=sender, warmup=False).announce(
            [_row(decision=Decision.SELL, outcome_class="STOPPED_OUT")], now=NOW
        )
        assert n == 1
        assert "🔴 ARUNA RESULT" in sender.sent[0]

    async def test_campuran_hanya_yang_berarah_lewat(self) -> None:
        sender = _Sender()
        n = await ResultNotifier(sender=sender, warmup=False).announce(
            [
                _row(signal_id="a", decision=Decision.WAIT),
                _row(signal_id="b", decision=Decision.BUY),
                _row(signal_id="c", decision=Decision.NO_SIGNAL),
            ],
            now=NOW,
        )
        assert n == 1

    async def test_batas_per_siklus_dihitung_setelah_disaring(self) -> None:
        """Kalau batasnya dihitung sebelum penyaringan, satu siklus penuh
        WAIT akan menghabiskan jatahnya dan tidak mengirim apa pun - lalu
        hasil berarah di siklus itu tertunda tanpa alasan."""
        sender = _Sender()
        banyak = [
            _row(signal_id=f"w{i}", decision=Decision.WAIT) for i in range(20)
        ] + [_row(signal_id="menang", decision=Decision.BUY)]

        n = await ResultNotifier(sender=sender, warmup=False).announce(banyak, now=NOW)
        assert n == 1
        assert "abc123" not in sender.sent[0]

    async def test_diam_kalau_belum_ada_bot(self) -> None:
        sender = _SenderBelumSiap()
        assert await ResultNotifier(sender=sender, warmup=False).announce([_row()], now=NOW) == 0
        assert sender.sent == []


class TestKabelKeJalurHidup:
    """Cacat berulang di repo ini: kode ditulis, diekspor, diuji, tidak pernah
    dicapai jalur yang benar-benar jalan."""

    def test_resolver_menyerahkan_pasangan_sinyal_dan_hasil(self) -> None:
        """`outcomes` saja memuat hasil tanpa simbol, tanpa entry, tanpa
        target - tidak cukup untuk memberi tahu siapa pun."""
        import inspect

        from aruna.signals import service as svc

        assert "scored" in inspect.getsource(svc.ResolveResult)
        assert "result.scored.append" in inspect.getsource(svc.SignalService)

    def test_loop_mendorong_hasil(self) -> None:
        import inspect

        from aruna.upkeep import loop as loop_module

        source = inspect.getsource(loop_module.UpkeepLoop._resolve)
        assert "_announce_results" in source

    def test_loop_mendorong_signal(self) -> None:
        import inspect

        from aruna.upkeep import loop as loop_module

        source = inspect.getsource(loop_module.UpkeepLoop._lock)
        assert "_announce_signals" in source

    def test_app_membangun_keduanya(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "results=ResultNotifier" in source
        assert "signals=SignalNotifier" in source

    def test_signal_yang_ditahan_tidak_didorong(self) -> None:
        """Sumbernya `published`, bukan `signals`. Call yang ARUNA sendiri
        putuskan untuk tidak dipublikasikan lalu didorong ke ponsel operator
        membatalkan keputusan menahan diri itu di satu-satunya tempat yang
        dibaca orang."""
        import inspect

        from aruna.upkeep import loop as loop_module

        source = inspect.getsource(loop_module.UpkeepLoop._announce_signals)
        assert 'getattr(result, "published"' in source
        assert 'getattr(result, "signals"' not in source


def _baris(**ganti):
    """Baris signal yang bisa dieksekusi.

    Level dan timeframe ikut sebagai bawaan sejak signal tanpa keduanya
    berhenti didorong: operator meminta hanya signal valid yang dikirim, dan
    fixture tanpa level menguji jalur yang sekarang memang tidak ada.
    """
    dasar = {
        "symbol": "BTC/USDT",
        "decision": Decision.BUY,
        "timeframe": "4h",
        "entry": "63000",
        "stop": "61500",
        "target": "66000",
    }
    dasar.update(ganti)
    return dasar


@pytest.fixture(autouse=True)
def _spot_push_dinyalakan(monkeypatch):
    """Nyalakan pengiriman spot untuk kelas-kelas yang mengujinya.

    Operator mematikannya pada 2026-08-20 - lihat
    :data:`aruna.notify.result.SPOT_PUSH_AKTIF`. Jalur kirimnya **tidak
    dihapus**: ia utuh, teruji, dan tinggal menunggu keputusan yang berbeda.
    Test-test ini menguji jalur itu, jadi mereka menyalakannya sendiri.

    Menghapusnya alih-alih menyalakannya akan membuang satu-satunya bukti bahwa
    jalur itu masih bekerja - dan hari seseorang menyalakannya kembali, tidak
    ada yang tersisa untuk mengatakan apakah ia masih benar.
    """
    from aruna.notify import result as modul

    monkeypatch.setattr(modul, "SPOT_PUSH_AKTIF", True)


class TestDorongSignal:
    async def test_long_dikirim(self) -> None:
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [_baris(confidence=0.7)], now=NOW
        )
        assert n == 1
        assert "FINAL DECISION:\nLONG" in sender.sent[0]

    async def test_timeframe_selalu_tercetak(self) -> None:
        """Operator: 'timeframe wajib'.

        LONG lima belas menit dan LONG satu hari menuntut stop yang berbeda,
        jadi arah tanpa timeframe tidak bisa dieksekusi siapa pun.
        """
        sender = _Sender()
        await SignalNotifier(sender=sender).announce([_baris()], now=NOW)

        assert "TIMEFRAME:\n4h" in sender.sent[0]

    async def test_tanpa_level_tidak_dikirim_sama_sekali(self) -> None:
        """Operator: 'kalau ga di dorong sebagai sinyal gausah di kirim'.

        Sebelum gerbang ini ada, ARUNA mendorong 'FINAL DECISION: LONG'
        disertai 'ENTRY / STOP LOSS / TAKE PROFIT: TIDAK TERSEDIA' - pesan yang
        menyuruh bertindak dan menolak mengatakan di mana.
        """
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [{"symbol": "BTC/USDT", "decision": Decision.BUY}], now=NOW
        )

        assert n == 0
        assert sender.sent == []

    async def test_tanpa_timeframe_juga_tidak_dikirim(self) -> None:
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [_baris(timeframe=None)], now=NOW
        )

        assert n == 0

    async def test_level_separuh_tidak_dikirim(self) -> None:
        """Entry tanpa stop bukan setengah signal; ia bukan signal."""
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [_baris(stop=None)], now=NOW
        )

        assert n == 0

    async def test_angka_acuan_ikut_terkirim(self) -> None:
        """Operator minta entry, take profit dan leverage ada di pesannya."""
        sender = _Sender()
        await SignalNotifier(sender=sender).announce([{
            "symbol": "BTC/USDT", "decision": Decision.BUY, "timeframe": "4h",
            "entry": "63000", "stop": "61500", "target": "66000",
            "leverage": 10, "liquidation": "57200",
        }], now=NOW)

        teks = sender.sent[0]
        assert "ENTRY:\n63000" in teks
        assert "TAKE PROFIT:\n66000" in teks
        assert "LEVERAGE:\n10x" in teks
        assert "HARGA LIKUIDASI:\n57200" in teks
        assert "ENTRY / SL / TP / LEVERAGE = ACUAN SAJA" in teks

    async def test_leverage_tanpa_likuidasi_tetap_diperingatkan(self) -> None:
        sender = _Sender()
        await SignalNotifier(sender=sender).announce([{
            "symbol": "BTC/USDT", "decision": Decision.BUY, "timeframe": "4h",
            "entry": "63000", "stop": "61500", "target": "66000",
            "leverage": 10,
        }], now=NOW)

        assert "TIDAK BISA DIHITUNG" in sender.sent[0]

    async def test_no_signal_tidak_didorong(self) -> None:
        """PASAL 11 dan 12 spec Daily Report menyebutnya eksplisit sebagai hal
        yang tidak boleh membanjiri Telegram."""
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [{"symbol": "BTC/USDT", "decision": Decision.NO_SIGNAL}], now=NOW
        )
        assert n == 0
        assert sender.sent == []

    async def test_wait_juga_tidak_didorong(self) -> None:
        sender = _Sender()
        n = await SignalNotifier(sender=sender).announce(
            [{"symbol": "BTC/USDT", "decision": Decision.WAIT}], now=NOW
        )
        assert n == 0

    async def test_satu_simbol_tidak_dispam(self) -> None:
        sender = _Sender()
        notifier = SignalNotifier(sender=sender)
        baris = [_baris()]

        await notifier.announce(baris, now=NOW)
        await notifier.announce(baris, now=NOW + timedelta(minutes=5))
        assert len(sender.sent) == 1

    async def test_sesudah_cooldown_boleh_lagi(self) -> None:
        sender = _Sender()
        notifier = SignalNotifier(sender=sender)
        baris = [_baris()]

        await notifier.announce(baris, now=NOW)
        await notifier.announce(baris, now=NOW + timedelta(hours=2))
        assert len(sender.sent) == 2

    async def test_gagal_kirim_tidak_memulai_cooldown(self) -> None:
        sender = _Sender(ok=False)
        notifier = SignalNotifier(sender=sender)
        baris = [_baris()]

        await notifier.announce(baris, now=NOW)
        sender.ok = True
        assert await notifier.announce(baris, now=NOW) == 1

