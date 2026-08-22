"""Hasil satu prediksi, didorong ke Telegram (PASAL 11, 12).

Sebelum modul ini ada, ``format_result`` punya tepat satu pemanggil: perintah
CLI ``aruna signal``. Prediksi diskor tiap menit oleh loop upkeep dan hasilnya
masuk database dengan rapi, lalu berhenti di situ. Operator diberi tahu saat
ARUNA berpendapat dan tidak pernah diberi tahu saat ARUNA ternyata salah.

Itu bukan kekurangan fitur. Sistem yang mengumumkan tebakannya dan diam soal
hasilnya adalah sistem yang track record-nya hanya berisi bagian yang enak
dibaca, dan pembacanya akan mengingat sepuluh signal yang dikirim tanpa pernah
tahu tujuh di antaranya kalah.

**Kalah dikirim dengan cara yang sama persis dengan menang.** Tidak ada
peredam, tidak ada penundaan, tidak ada pengelompokan yang membuat LOSS lebih
sepi daripada WIN.

**Suara agent: yang tercatat saja.** PASAL 11 meminta hasil pemilihan awal ikut
dikirim. Yang tersimpan per sesi council hanyalah agregat - berapa agent ikut,
berapa keberatan diajukan; tabel ``agent_decisions`` kosong, jadi nama siapa
berada di sisi mana tidak ada. Maka yang dicetak adalah agregat itu, disebut
sebagai agregat. Menyusun daftar nama dari data yang tidak menyimpannya akan
menghasilkan pesan yang terlihat persis seperti yang diminta dan isinya karangan
(PASAL 6: semua statistik harus berdasarkan data aktual yang tersimpan).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from aruna.core.clock import wib
from aruna.core.logging import get_logger
from aruna.notify.verdict import (
    NO_SIGNAL,
    TEST_BANNER,
    VoteSplit,
    guard_public,
    public_decision,
    render_votes,
)

log = get_logger("aruna.notify.result")

#: Apakah signal SPOT didorong ke Telegram.
#:
#: **Mati, atas keputusan operator pada 2026-08-20:** *"Spot gausah di kirim
#: sinyalnya dan gausah pakai stop loss dan jangan kirim signal"*.
#:
#: Perilakunya sudah begitu sebelum konstanta ini ada, dan itu justru
#: masalahnya. Signal spot lahir tanpa ``stop``, dan gerbang "bisa dieksekusi"
#: di bawah menahannya - 521 kali dalam satu hari, masing-masing menulis satu
#: baris log yang berbunyi seperti temuan. Bukan temuan: itu perilaku yang
#: diminta, tercapai lewat jalan yang tidak pernah menyebutkannya.
#:
#: Sebuah keputusan yang hanya tersirat dari bidang yang kosong akan berbalik
#: sendiri pada hari seseorang mengisi bidang itu - tanpa ada yang menyadari
#: bahwa sebuah keputusan baru saja dibatalkan. Karena itu ia dieja di sini.
#:
#: **Yang TIDAK berhenti:** signal spot tetap dikunci, disimpan, diresolusi,
#: dan tetap masuk laporan harian beserta kekalahannya. §11.21 melarang
#: menghapus catatan, bukan melarang diam. Yang berhenti hanya pengirimannya.
#:
#: Jangan menyalakannya lagi tanpa menanyakan operator: yang akan berangkat
#: adalah pesan berisi arah tanpa batas rugi, dan itu bentuk paling berbahaya
#: dari setengah fakta yang bisa dikirim sistem ini.
SPOT_PUSH_AKTIF = False

#: Hasil yang dianggap menang dan kalah (PASAL 4, 10).
#:
#: Diambil dari kelas outcome ARUNA. Yang tidak ada di sini - misalnya
#: prediksi yang benar arahnya tapi tidak menyentuh target - **tidak** dipaksa
#: masuk salah satu sisi; ia dikirim apa adanya dengan kelasnya sendiri, karena
#: menyebut sesuatu WIN atau LOSS yang bukan keduanya adalah memalsukan catatan.
#: Dua ejaan, dan keduanya nyata.
#:
#: ``TARGET_REACHED`` adalah kelas menang mesin outcome **spot**
#: (:class:`aruna.signals.models.OutcomeClass`); ``TARGET_HIT`` adalah kelas
#: menang mesin **futures** (:class:`aruna.futures.learning.PlanOutcome`).
#: Daftar ini semula hanya memuat yang kedua, sementara satu-satunya pemanggil
#: :func:`render_result` adalah jalur spot.
#:
#: Akibatnya tidak pernah terlihat sebagai kegagalan: setiap prediksi spot yang
#: menang keluar sebagai 🟡 dengan tulisan ``RESULT: TARGET_REACHED`` - nama
#: pengenal internal, bukan kata "WIN" - dan pesannya tetap terkirim, tetap
#: rapi, tetap salah. Sebuah daftar yang mengeja nama dari enum lain tidak bisa
#: gagal dengan berisik; ia hanya berhenti cocok.
WIN_CLASSES = ("TARGET_REACHED", "TARGET_HIT", "WIN")
LOSS_CLASSES = ("STOPPED_OUT", "LIQUIDATED", "LOSS")


def classify(outcome_class: str) -> str | None:
    """``WIN``, ``LOSS``, atau ``None`` kalau bukan keduanya."""
    if outcome_class in WIN_CLASSES:
        return "WIN"
    if outcome_class in LOSS_CLASSES:
        return "LOSS"
    return None


#: Kelas outcome SPEC 23, diterjemahkan ke kalimat yang bisa dibaca orang.
#:
#: Sebelum tabel ini ada, kelas yang bukan menang maupun kalah dicetak apa
#: adanya ke layar operator: "RESULT: WRONG_FROM_START". Itu nama pengenal di
#: dalam mesin - huruf besar, garis bawah, bahasa Inggris - dan PASAL 1 dan 3
#: melarang kosakata internal keluar.
#:
#: Yang diterjemahkan hanya katanya. Kelasnya tetap dicetak di dalam kurung,
#: karena ia yang tersimpan dan yang akan dicari orang saat mencocokkan pesan
#: dengan barisnya di database.
#: Kelas menang ikut punya kalimatnya. Kalau hanya yang kalah dijelaskan,
#: asimetri yang baru saja dihapus kembali lewat pintu lain: kekalahan
#: terlihat butuh keterangan, kemenangan terlihat berdiri sendiri.
OUTCOME_PUBLIC: dict[str, str] = {
    "WRONG_FROM_START": "arahnya salah sejak awal",
    "RIGHT_THEN_REVERSED": "sempat benar, lalu berbalik",
    "RIGHT_DIRECTION_BAD_TIMING": "arahnya benar, waktunya tidak",
    "TARGET_NOT_REACHED": "arahnya benar, target tidak tercapai",
    "HORIZON_MISMATCH": "horizonnya tidak cocok dengan geraknya",
    "NO_POSITION": "tidak ada posisi yang diambil",
    "TARGET_REACHED": "target tercapai",
    "TARGET_HIT": "target tercapai",
    "STOPPED_OUT": "kena stop",
    "LIQUIDATED": "ditutup paksa bursa",
}


def public_outcome(outcome_class: str) -> str:
    """Kelas outcome, dalam kalimat.

    Yang tidak ada di tabel dikembalikan apa adanya - lebih baik satu nama
    internal terlihat dan diperbaiki daripada diam-diam dipetakan ke kalimat
    yang salah.
    """
    kalimat = OUTCOME_PUBLIC.get(outcome_class)
    return f"{kalimat} ({outcome_class})" if kalimat else outcome_class


#: Angka di belakang koma yang masih berarti untuk sebuah harga.
#:
#: Delapan, karena itu presisi terhalus yang benar-benar dikutip venue yang
#: dibaca ARUNA - satoshi pada pasangan crypto. Lebih dari itu adalah sisa
#: pembagian Decimal, bukan harga.
MAX_DECIMALS = 8


def _harga(value: Any) -> str:
    """Harga, tanpa nol berekor dan tanpa presisi yang tidak dimilikinya.

    Terlihat di layar operator: ``ENTRY: 0.995500000000`` dan
    ``TP: 0.992561339549``. Yang pertama adalah harga bursa dengan sepuluh nol
    di belakangnya karena kolomnya ``DECIMAL(30,12)``; yang kedua adalah hasil
    hitungan yang membawa seluruh presisi Decimal.

    Dua aturan, dan keduanya sengaja tidak menebak apa pun tentang venue: buang
    nol berekornya, lalu potong di :data:`MAX_DECIMALS`.

    **Versi pertama mencoba lebih pintar dan salah.** Ia membulatkan target dan
    stop ke presisi entry, dengan alasan keduanya diturunkan dari harga itu.
    Terlihat masuk akal sampai entry-nya ``1.0000``: nol berekornya dibuang
    lebih dulu, presisinya terbaca nol angka di belakang koma, dan target
    ``1.00005`` dibulatkan menjadi ``1``. Padding penyimpanan dan presisi
    kutipan tidak bisa dibedakan pada kolom ``DECIMAL(30,12)``, jadi menyimpulkan
    yang satu dari yang lain akan sesekali menghapus angka yang benar - dan
    kesalahannya justru paling besar pada harga yang paling bulat.
    """
    if value is None:
        return "-"
    try:
        angka = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)

    tegak = angka.normalize()
    if -tegak.as_tuple().exponent > MAX_DECIMALS:
        tegak = angka.quantize(
            Decimal(1).scaleb(-MAX_DECIMALS), rounding=ROUND_HALF_UP
        ).normalize()
    # normalize() menulis bilangan bulat besar dalam notasi eksponen (6.3E+4),
    # yang untuk harga terbaca seperti salah ketik.
    if tegak == tegak.to_integral_value():
        tegak = tegak.quantize(Decimal(1))
    return f"{tegak:f}"


def _uang(value: Any, *, bertanda: bool = True) -> str:
    """Satu angka uang, dua desimal.

    ``bertanda`` menambahkan ``+`` di depan angka positif, supaya untung dan
    rugi terbaca berbeda dalam satu pandangan. Baris biaya mematikannya: ia
    selalu potongan, tanda minusnya ditulis pemanggil, dan membiarkan keduanya
    menghasilkan ``-+3.67``.
    """
    try:
        angka = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    if bertanda and angka > 0:
        return f"+{angka:f}"
    return f"{angka:f}"


def _baris_ekonomi(economics: tuple | None) -> list[str]:
    """Kotor, biaya, bersih - kenapa arah yang benar bisa tetap kalah.

    Tanpa blok ini pesan hasil bisa berbunyi "LOSS - arahnya benar, target tidak
    tercapai", dan dua bagian kalimat itu saling membantah di mata pembacanya.
    Keduanya benar: harganya memang bergerak ke arah yang diprediksi, dan
    geraknya lebih kecil daripada ongkos masuk-keluarnya.

    Terukur pada DOT/USDT yang memicu keluhan ini: masuk 0,747, keluar 0,749 -
    naik - kotor +2,68, biaya 3,67, bersih -0,99. Enam prediksi DOT berturut
    dalam satu jam, semuanya kalah, dan empat di antaranya arahnya benar atau
    datar. Yang diberitahukan angka ini bukan "ARUNA salah" tapi "geraknya
    harus lebih besar dari 3,67 sebelum ada gunanya" - dan itu keterangan yang
    tidak bisa disimpulkan operator dari kata LOSS saja.

    Ditampilkan pada MENANG maupun KALAH. Blok yang hanya muncul saat kalah
    akan mengembalikan asimetri yang PASAL 11 minta dihapus, lewat pintu lain:
    kekalahan terlihat butuh pembelaan, kemenangan terlihat berdiri sendiri.
    """
    if not economics:
        return []
    try:
        kotor, biaya, bersih = economics
    except (TypeError, ValueError):
        return []
    return [
        "",
        "HITUNGAN (paper):",
        f"kotor  {_uang(kotor)}",
        f"biaya  -{_uang(abs(Decimal(str(biaya))), bertanda=False)}",
        f"bersih {_uang(bersih)}",
    ]


def render_result(
    *,
    symbol: str,
    decision: Any,
    outcome_class: str,
    signal_id: str,
    entry: Any = None,
    target: Any = None,
    stop: Any = None,
    trigger: str | None = None,
    votes: VoteSplit | None = None,
    council: dict[str, Any] | None = None,
    now: datetime | None = None,
    test_mode: bool = False,
    trade_result: str | None = None,
    model_version: str | None = None,
    economics: tuple | None = None,
) -> str:
    """Blok ARUNA RESULT (PASAL 11, 12).

    ``trade_result`` adalah hasil paper trade - ``WIN``, ``LOSS`` atau
    ``BREAKEVEN`` - dan ia yang menentukan, bukan kelas outcome.

    **Keduanya menjawab pertanyaan berbeda, dan menyamakannya menyanjung.**
    Kelas outcome SPEC 23 menjawab *bagaimana* prediksinya meleset; tidak satu
    pun nilainya berarti "kalah". Jadi selama pesan ini membacanya sebagai
    putusan menang-kalah, setiap kemenangan keluar sebagai 🟢 WIN sementara
    setiap kekalahan keluar sebagai 🟡 dengan kalimat - kemenangan terlihat
    tegas, kekalahan terlihat samar, dan spec-nya meminta kalah dikirim dengan
    cara yang sama persis dengan menang.

    Tanpa ``trade_result`` perilakunya kembali ke cara lama, karena prediksi
    yang tidak menghasilkan posisi memang tidak punya hasil perdagangan.
    """
    hasil = trade_result if trade_result in ("WIN", "LOSS") else classify(outcome_class)
    # Kuning untuk yang bukan menang maupun kalah - BREAKEVEN, atau kelas
    # seperti EXPIRED, yang memang tidak berpihak ke mana pun.
    tanda = "🟢" if hasil == "WIN" else "🔴" if hasil == "LOSS" else "🟡"
    arah = public_decision(decision)

    lines: list[str] = []
    if test_mode:
        lines += [TEST_BANNER, ""]
    lines += [
        f"{tanda} ARUNA RESULT",
        "",
        symbol,
        "",
        "DECISION:",
        arah,
    ]
    if entry is not None:
        lines += ["", "ENTRY:", _harga(entry)]
    if target is not None:
        lines += ["", "TP:", _harga(target)]
    if stop is not None:
        lines += ["", "SL:", _harga(stop)]

    # Satu baris, seperti template lama. Putusannya di depan - itu yang dicari
    # mata - dan sebabnya menempel di belakangnya, bukan di blok terpisah yang
    # akan membuat kekalahan punya bagian yang tidak dimiliki kemenangan.
    kalimat = OUTCOME_PUBLIC.get(outcome_class)
    if hasil and kalimat:
        putusan = f"{hasil} - {kalimat} ({outcome_class})"
    elif hasil:
        putusan = hasil
    else:
        putusan = public_outcome(outcome_class)
    lines += ["", "RESULT:", putusan]
    lines += _baris_ekonomi(economics)
    if trigger:
        lines += ["", "TRIGGER:", trigger]

    lines += ["", "HASIL PEMILIHAN AWAL:", ""]
    if votes is not None:
        # Tanpa judul: blok ini sudah punya judulnya sendiri satu baris di
        # atas, dan render_votes yang membawa judulnya sendiri akan mencetak
        # "HASIL PEMILIHAN AWAL:" lalu "HASIL PEMILIHAN:" beruntun.
        lines += render_votes(votes, heading=None)
    elif council:
        # Agregat, dan disebut agregat. Judul "SETUJU:" dengan daftar kosong di
        # bawahnya akan terbaca sebagai "tidak ada yang setuju" - kebalikan dari
        # kebenarannya, yang adalah "tidak dicatat".
        lines += [
            f"{council.get('participating_agents', '?')} dari "
            f"{council.get('total_agents', '?')} agent ikut memutuskan",
            f"{council.get('objection_count', 0)} keberatan diajukan, "
            f"{council.get('correction_count', 0)} diterima",
        ]
        if council.get("minority_prevailed"):
            lines.append("judge memihak minoritas atas dasar bobot bukti")
        lines += [
            "",
            "Suara tiap agent tidak tersimpan untuk sesi ini,",
            "jadi nama siapa di sisi mana tidak bisa ditampilkan.",
        ]
    else:
        lines.append("Tidak ada catatan pemilihan untuk prediksi ini.")

    if model_version:
        lines += ["", "MODEL:", model_version]

    lines += ["", "Signal ID:", signal_id]
    if now is not None:
        lines += ["", wib(now)]
    lines += ["", "ARUNA ANALYST ONLY", "EXECUTION: USER"]
    if test_mode:
        lines += ["", TEST_BANNER]
    return guard_public("\n".join(lines))


#: Kunci baris hasil yang dipakai untuk MEMUTUSKAN, bukan untuk dicetak.
#:
#: ``render_result`` menerima kwarg dan akan meledak pada nama yang tidak
#: dikenalnya. Itu perilaku yang benar - ia menangkap salah ketik - tapi berarti
#: tiap keterangan baru yang ikut menumpang di baris ini harus disebut di sini.
#: Sebuah himpunan dengan namanya sendiri, bukan satu perbandingan di tengah
#: pemahaman dict, supaya penambah berikutnya melihat tempatnya.
BUKAN_UNTUK_RENDER = frozenset({"extra", "published"})

#: Paling banyak sekian hasil per siklus. Satu pass resolve bisa menutup
#: puluhan prediksi sekaligus - biasanya sesudah ARUNA mati semalam - dan lima
#: puluh notifikasi beruntun adalah cara tercepat membuat operator mematikan
#: notifikasi ARUNA seluruhnya.
MAX_PER_CYCLE = 5


@dataclass(slots=True)
class ResultNotifier:
    """Mendorong hasil prediksi yang baru diskor."""

    sender: Any
    max_per_cycle: int = MAX_PER_CYCLE
    #: Pass pertama sesudah proses menyala hanya dicatat, tidak dikirim.
    #:
    #: Saat ARUNA mati semalam, prediksi tetap jatuh tempo. Pass resolusi
    #: pertama sesudah menyala menutup semuanya sekaligus - puluhan hasil dari
    #: kemarin dan kemarin lusa, semuanya "baru" bagi notifier yang ingatannya
    #: kosong. Operator melihatnya sebagai muntahan pesan tiap kali ARUNA
    #: dinyalakan ulang, dan meminta itu berhenti.
    #:
    #: Yang hilang hanya pemberitahuannya, dan hanya untuk satu pass. Semua
    #: hasilnya tetap tersimpan, tetap masuk hitungan win rate, tetap terbaca
    #: lewat ``/today`` dan laporan harian. Jumlah yang dibungkam dicatat ke
    #: log, bukan dibuang diam-diam.
    warmup: bool = True
    #: Penyimpanan signal, untuk membaca jejak pengiriman yang sebenarnya.
    #:
    #: ``None`` mematikan penyaringan ini sepenuhnya - dan itu arah kegagalan
    #: yang benar: tanpa penyimpanan, semua hasil tetap dikirim. Kebalikannya
    #: akan membungkam kabar bahwa ARUNA salah gara-gara perakitan yang belum
    #: lengkap (PASAL 11.21).
    store: Any = None
    _seen: set[str] = field(default_factory=set)

    async def _jejak_kirim(
        self, baris: list[dict[str, Any]]
    ) -> dict[str, int | None] | None:
        """Signal mana yang benar-benar terkirim, dan pesan mana yang membawanya.

        **Gagal terbuka**, dan arah kegagalannya sama disengajanya dengan yang
        di :meth:`aruna.signals.service.SignalService.published_ids`:
        ``None`` berarti "tidak bisa dicek", dan pemanggil mengirim semuanya.
        Kebalikannya jauh lebih berbahaya - satu bug pencarian akan membungkam
        setiap kabar bahwa ARUNA salah (PASAL 11.21).
        """
        cari = getattr(self.store, "pushed_message_ids", None)
        if cari is None:
            return None
        try:
            return await cari([str(r.get("signal_id")) for r in baris])
        except Exception:
            log.exception("result.push_lookup_failed")
            return None

    async def _kirim_hasil(
        self, teks: str, balas: int | None, *, row: dict[str, Any] | None = None
    ) -> bool:
        """Kirim hasil, membalas pesan signalnya kalau id-nya diketahui.

        Operator: *"seharusnya sinyal dulu terus reply chat yang mana hasil
        resultnya"*. Tanpa balasan, sebuah RESULT untuk BTC di antara dua puluh
        simbol menuntut pembacanya menggulir mencari signal mana yang dimaksud -
        dan saat hasilnya datang, signalnya sudah berjam-jam di atas.

        **Jenisnya disebutkan, bukan ditebak** (PASAL 14.38). WIN dan LOSS
        adalah dua jenis terpisah, dan §11.21 melarang menyembunyikan LOSS -
        menyatukan keduanya di bawah satu jenis akan membuat pembungkaman salah
        satunya tidak terlihat di mana pun.
        """
        from aruna.decision.channel import Jenis, allow

        hasil = ""
        if row is not None:
            mentah = str(row.get("trade_result") or "")
            hasil = mentah if mentah in ("WIN", "LOSS") else (
                classify(str(row.get("outcome_class") or "")) or ""
            )
        # Hasil yang tidak terbaca menang atau kalah tetap kabar tentang sebuah
        # prediksi yang selesai - dikirim sebagai LOSS, bukan dibungkam. Arah
        # ketidaktahuannya disengaja: §11.21 melarang menyembunyikan yang buruk,
        # jadi yang tidak jelas diperlakukan sebagai yang buruk.
        allow(Jenis.WIN if hasil == "WIN" else Jenis.LOSS)

        kirim = getattr(self.sender, "send_id", None)
        if kirim is None or not balas:
            return bool(await self.sender.send(teks))
        return await kirim(teks, reply_to=balas) is not None

    async def announce(self, results: list[dict[str, Any]], *, now: datetime) -> int:
        """Kirim hasil yang belum pernah dikirim. Mengembalikan jumlahnya."""
        if not self._can_send():
            # Sengaja SEBELUM warmup dipadamkan. Tanpa bot Telegram, pass ini
            # tidak pernah bisa mengirim apa pun - memakainya sebagai "pass
            # pertama" berarti backlog sesungguhnya lewat tanpa dibungkam,
            # lalu ikut terkirim di pass berikutnya.
            return 0

        if self.warmup:
            self.warmup = False
            self._seen.update(str(r["signal_id"]) for r in results)
            if results:
                log.info("result.warmup_suppressed", count=len(results))
            return 0

        # Hasil dari keputusan untuk TIDAK mengambil posisi bukan menang dan
        # bukan kalah - tidak ada posisi yang bisa jadi keduanya. ``format_result``
        # sudah lama tahu ini: "A WAIT is never labelled CORRECT or WRONG."
        #
        # Terukur saat ditemukan: 514 prediksi diskor dalam 24 jam, 426 di
        # antaranya tanpa arah. Delapan puluh tiga persen pesan hasil akan
        # berbunyi "DECISION: NO SIGNAL" - persis yang operator minta hentikan,
        # lewat jalur ketiga yang tidak tertutup saat dua jalur lain ditutup.
        #
        # Yang hilang cuma pemberitahuannya. Outcome-nya tetap tersimpan, tetap
        # masuk hitungan, dan tetap terbaca lewat /today.
        berarah = [
            r for r in results
            if public_decision(r.get("decision")) is not NO_SIGNAL
        ]

        # **Hasil dari prediksi yang tidak pernah diumumkan tidak didorong.**
        #
        # Operator melaporkannya begini: "belum ada signal, tiba-tiba result
        # semua". Terukur dalam dua belas jam: 73 prediksi berarah diskor tanpa
        # pernah dipublikasikan - ditahan karena bukti basi, cooldown, atau
        # duplikat - lawan 28 yang dipublikasikan. Ketiganya didorong dengan
        # cara yang sama, jadi mayoritas pesan hasil adalah kabar tentang
        # prediksi yang tidak pernah ada di layar siapa pun. Sebuah RESULT tanpa
        # SIGNAL-nya tidak bisa dipakai untuk apa-apa: tidak ada yang bisa
        # diperiksa ulang, dan tidak ada yang bisa dipelajari darinya.
        #
        # **Ini bukan pintu untuk menyembunyikan kekalahan (PASAL 11.21).**
        # ``published`` diputuskan saat prediksi dikunci, jauh sebelum ada yang
        # tahu ia menang atau kalah, jadi penyaringan ini tidak bisa
        # memilih-milih hasil. Yang hilang hanya dorongannya - barisnya tetap
        # tersimpan, tetap masuk hitungan win rate, tetap terbaca lewat
        # ``/today`` dan laporan harian, dan jumlah yang diredam dicatat.
        #
        # Baris tanpa keterangan ``published`` tetap dikirim. Arah kegagalan itu
        # disengaja: satu pencarian yang gagal tidak boleh membungkam kabar
        # bahwa ARUNA salah.
        diumumkan = [r for r in berarah if r.get("published") is not False]
        diredam = len(berarah) - len(diumumkan)
        if diredam:
            log.info("result.unpublished_suppressed", count=diredam)

        baru = [r for r in diumumkan if r.get("signal_id") not in self._seen]
        if not baru:
            return 0

        # **Jejak pengiriman yang sebenarnya, bukan niat menerbitkan.**
        #
        # ``published`` menjawab "layak diterbitkan" dan ditulis saat prediksi
        # dikunci. Sesudah itu ada gerbang kedua: signal tanpa entry, stop,
        # target, atau timeframe tidak didorong. Terukur: 80 signal ditahan
        # gerbang itu dengan barisnya tetap ``published = TRUE``, jadi hasil
        # dari kedelapan puluhnya tetap sampai - operator melihat RESULT untuk
        # signal yang tidak pernah ada di layarnya.
        #
        # Yang tidak ada di peta ini tidak pernah terkirim. Nilainya boleh
        # ``None`` - terkirim tanpa id tercatat - dan itu tetap dikirim, hanya
        # tanpa balasan.
        terkirim_map = await self._jejak_kirim(baru)
        if terkirim_map is not None:
            sebelum = len(baru)
            baru = [r for r in baru if str(r.get("signal_id")) in terkirim_map]
            hilang = sebelum - len(baru)
            if hilang:
                log.info("result.never_pushed_suppressed", count=hilang)
            if not baru:
                return 0

        terkirim = 0
        for row in baru[: self.max_per_cycle]:
            teks = render_result(now=now, **{
                k: v for k, v in row.items() if k not in BUKAN_UNTUK_RENDER
            })
            balas = (
                (terkirim_map or {}).get(str(row.get("signal_id")))
                if terkirim_map is not None
                else None
            )
            if await self._kirim_hasil(teks, balas, row=row):
                # Distempel SETELAH berhasil. Menstempel duluan berarti satu
                # kegagalan jaringan menghapus hasil itu selamanya - dan hasil
                # yang hilang justru cenderung yang kalah, karena kalah lebih
                # sering datang berombongan.
                self._seen.add(str(row["signal_id"]))
                terkirim += 1
            else:
                log.warning("result.undelivered", signal_id=row.get("signal_id"))

        sisa = len(baru) - self.max_per_cycle
        if sisa > 0:
            # Dikatakan, bukan dibuang diam-diam. Batas yang tidak terlihat
            # membuat sepuluh kekalahan terbaca sebagai lima.
            log.info("result.deferred", count=sisa)
        return terkirim

    def _can_send(self) -> bool:
        ready = getattr(self.sender, "ready", None)
        return True if ready is None else bool(ready())


#: Jarak minimum antara dua pesan SIGNAL untuk simbol yang sama.
SIGNAL_COOLDOWN = timedelta(hours=1)


@dataclass(slots=True)
class SignalNotifier:
    """Mendorong prediksi yang baru dikunci (PASAL 12A).

    **Hanya LONG dan SHORT.** ``NO SIGNAL`` tidak didorong - PASAL 11 dan 12
    dari spec Daily Report menyebutnya eksplisit sebagai hal yang tidak boleh
    membanjiri Telegram. Verdict yang tidak berarah tetap dicatat di database
    dan tetap muncul di ``/today``; yang dihilangkan hanya dorongannya.
    """

    sender: Any
    cooldown: timedelta = SIGNAL_COOLDOWN
    #: Penyimpanan signal, untuk mencatat apa yang BENAR-BENAR terkirim.
    #:
    #: Kolom ``published`` tidak bisa menjawab itu: ia ditulis saat prediksi
    #: dikunci, sebelum gerbang "bisa dieksekusi" di bawah ada. Terukur: 80
    #: signal ditahan gerbang itu sementara barisnya tetap ``published =
    #: TRUE``, dan hasil dari kedelapan puluhnya tetap didorong - persis yang
    #: dikeluhkan operator sebagai "result tanpa sinyal".
    store: Any = None
    _last: dict[str, datetime] = field(default_factory=dict)

    def due(self, symbol: str, now: datetime) -> bool:
        last = self._last.get(symbol)
        return last is None or now - last >= self.cooldown

    async def announce(self, signals: list[dict[str, Any]], *, now: datetime) -> int:
        from aruna.notify.verdict import NO_SIGNAL, render_analysis

        if not SPOT_PUSH_AKTIF:
            # Dicatat sekali per siklus, bukan sekali per signal. Terukur pada
            # 2026-08-20: gerbang lama menulis 521 baris dalam satu hari untuk
            # perilaku yang ternyata memang diminta - log yang melatih
            # pembacanya melewati baris, dan yang hilang berikutnya bukan baris
            # ini.
            if signals:
                log.info(
                    "signal.spot_push_mati",
                    count=len(signals),
                    sebab="keputusan operator 2026-08-20: spot tidak dikirim",
                )
            return 0

        if not self._can_send():
            return 0

        terkirim = 0
        for row in signals:
            arah = public_decision(row["decision"])
            if arah is NO_SIGNAL or arah == NO_SIGNAL:
                continue
            symbol = str(row["symbol"])
            if not self.due(symbol, now):
                continue

            # **Hanya signal yang bisa dieksekusi yang dikirim.**
            #
            # Sebuah arah tanpa entry, stop dan target bukan signal; tidak ada
            # yang bisa dilakukan dengannya. Sebelum gerbang ini ada, ARUNA
            # mendorong "FINAL DECISION: LONG" disertai baris "ENTRY / STOP
            # LOSS / TAKE PROFIT: TIDAK TERSEDIA" - pesan yang menyuruh
            # bertindak dan menolak mengatakan di mana.
            #
            # Timeframe ikut jadi syarat, dan bukan formalitas: LONG lima belas
            # menit dan LONG satu hari menuntut stop yang berbeda, jadi arah
            # tanpa timeframe sama tidak bisa dieksekusinya dengan arah tanpa
            # stop.
            #
            # Yang hilang hanya dorongannya. Verdictnya tetap tersimpan, tetap
            # masuk hitungan win rate, dan tetap terbaca lewat /today - PASAL
            # 11.21 melarang menghapus catatan, bukan melarang diam.
            #
            # **Ini menahan SELURUH signal spot, dan itu disengaja.**
            #
            # Terukur pada 2026-08-20: 104 dari 104 penahanan berbunyi
            # ``missing=stop`` - tidak satu pun karena entry, target, atau
            # timeframe. Sebabnya bukan kerusakan: jalur prediksi spot tidak
            # pernah punya stop loss. Ia menyimpan entry dan target saja, dan
            # penilai hasilnya tidak memakai stop sama sekali - hanya target
            # tercapai, tidak tercapai, atau arah salah sejak awal.
            #
            # Operator diberi tiga pilihan pada 2026-08-20: memberi stop
            # simetris dan memakainya untuk menilai (win rate berubah artinya),
            # membebaskan spot dari syarat stop (pesannya berhenti berpura-pura
            # jadi instruksi), atau membiarkannya hening. **Ia memilih
            # hening.** Yang sampai ke Telegram hanyalah rencana futures, yang
            # memang punya entry, stop, target, leverage, dan harga likuidasi.
            #
            # Jangan "perbaiki" ini menjadi longgar tanpa menanyakannya lagi:
            # diamnya bukan cacat yang belum ketahuan, ia keputusan yang sudah
            # diambil.
            kurang = [
                nama
                for nama, nilai in (
                    ("entry", row.get("entry")),
                    ("stop", row.get("stop")),
                    ("target", row.get("target")),
                    ("timeframe", row.get("timeframe")),
                )
                if nilai is None
            ]
            if kurang:
                log.info(
                    "signal.not_actionable",
                    symbol=symbol,
                    missing=",".join(kurang),
                    detail="tidak didorong: tidak ada yang bisa dieksekusi",
                )
                continue

            teks = render_analysis(
                symbol=symbol,
                decision=row["decision"],
                split=row.get("split") or VoteSplit((), ()),
                confidence=row.get("confidence"),
                entry=row.get("entry"),
                stop=row.get("stop"),
                target=row.get("target"),
                timeframe=row.get("timeframe"),
                reward_risk=row.get("reward_risk"),
                # Diteruskan apa adanya, termasuk saat likuidasinya None.
                # `render_analysis` yang memutuskan cara mengatakannya - dan ia
                # menolak mencetak leverage tanpa menyebut batas paksanya.
                leverage=row.get("leverage"),
                liquidation=row.get("liquidation"),
                model_version=row.get("model_version"),
            )
            pesan = await self._kirim(teks)
            if pesan is None:
                log.warning("signal.undelivered", symbol=symbol)
                continue
            self._last[symbol] = now
            terkirim += 1
            await self._catat_terkirim(row, pesan, now)
        return terkirim

    async def _kirim(self, teks: str) -> int | None:
        """Kirim dan kembalikan id pesannya, atau ``None`` kalau gagal.

        Pengirim yang tidak mengenal ``send_id`` menghasilkan ``0`` - terkirim,
        id tidak diketahui. Itu berbeda dari ``None``, dan perbedaannya
        menentukan apakah hasilnya nanti dibungkam atau sekadar tidak bisa
        membalas.
        """
        from aruna.decision.channel import Jenis, allow

        # PASAL 14.38: jenisnya disebutkan di jalur kirimnya sendiri. Sebuah
        # jalur baru yang lupa menyebutkannya tidak akan lolos penjaganya.
        allow(Jenis.SIGNAL)

        kirim = getattr(self.sender, "send_id", None)
        if kirim is None:
            return 0 if await self.sender.send(teks) else None
        return await kirim(teks)

    async def _catat_terkirim(
        self, row: dict[str, Any], message_id: int, now: datetime
    ) -> None:
        """Tulis jejak pengirimannya. Kegagalannya tidak membatalkan kiriman.

        Pesannya sudah sampai ke operator; sebuah tulisan yang gagal berarti
        hasilnya nanti tidak akan didorong - kehilangan yang jauh lebih kecil
        daripada mengulang pengiriman atau menjatuhkan siklusnya.
        """
        sid = row.get("signal_id")
        if self.store is None or not sid:
            return
        catat = getattr(self.store, "mark_pushed", None)
        if catat is None:
            return
        try:
            await catat(str(sid), message_id=message_id or None, at=now)
        except Exception:
            log.exception("signal.push_not_recorded", signal_id=str(sid))

    def _can_send(self) -> bool:
        ready = getattr(self.sender, "ready", None)
        return True if ready is None else bool(ready())


__all__ = [
    "LOSS_CLASSES",
    "MAX_PER_CYCLE",
    "SIGNAL_COOLDOWN",
    "SPOT_PUSH_AKTIF",
    "WIN_CLASSES",
    "ResultNotifier",
    "SignalNotifier",
    "classify",
    "render_result",
]
