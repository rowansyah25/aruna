"""Penilaian council atas satu simbol (FUTURES SPEC 8-14, 48).

Council menjalankan tiga ronde: setiap agent memberi pendapatnya sendiri, lalu
saling menyanggah, lalu menjawab sanggahan itu. Judge menimbang bukti, bukan
jumlah suara.

**Ini pernah menjadi pesan Telegram tersendiri, dan sekarang tidak lagi.**
Modul ini dulu merender log perdebatan lengkap - siapa menyanggah siapa, atas
dasar apa - lalu mendorongnya sebagai notifikasi terpisah dari alert plan.
Operator meminta itu dihentikan, dan angkanya menjelaskan kenapa: loop
menjalankan satu council per simbol per tick, jadi satu setup menghasilkan dua
pesan tentang satu peristiwa yang sama.

Yang tersisa bukan penghapusan melainkan pemindahan. Penilaiannya - confidence,
disagreement, hasil pemilihan, dan apakah benar terjadi perdebatan - sekarang
ikut **di dalam** pesan plan, tempat pembacanya sedang melihat angka yang
dinilai. Sesi council-nya sendiri tetap tersimpan utuh dan tetap terbaca lewat
``/council``; yang hilang hanya pesan keduanya.

**Apa yang dihitung sebagai perdebatan** tetap sama, dan tetap diputuskan dari
peristiwa, bukan dari ambang yang dikarang:

* **veto diajukan** - ada agent yang mencoba memblokir keputusan;
* **minoritas menang** - judge memihak sisi yang jumlahnya lebih sedikit,
  karena buktinya lebih kuat;
* **ada yang mengubah pendapat** - sanggahan diterima, bukan dijawab balik.

Council yang sepakat bulat bukan perdebatan, dan tidak disebut sebagai
perdebatan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.notify.verdict import VoteSplit, vote_split

#: Disagreement di atas ini disebut, tapi tidak dengan sendirinya memicu
#: pengiriman. Angka tinggi tanpa satu pun pendapat berubah artinya para agent
#: memang melihat pasar berbeda, bukan bahwa terjadi perdebatan.
#:
#: **Diberi nama ulang 2026-08-23, dan angkanya TIDAK berubah.** Sampai hari itu
#: konstanta ini bernama ``HIGH_DISAGREEMENT``, sama persis dengan
#: :data:`~aruna.council.protest.HIGH_DISAGREEMENT` yang bernilai 0,4 - nama
#: yang sama untuk dua pertanyaan yang berbeda atas metrik yang sama:
#:
#: * 0,40 di ``council.protest`` menjawab "haruskah ronde review adversarial
#:   dijalankan?" - itu ambang TINDAKAN.
#: * 0,75 di sini menjawab "apakah selisih ini layak disebut ke pembaca?" - itu
#:   ambang PENUTURAN.
#:
#: Keduanya sah. Yang tidak sah adalah keduanya bisa diimpor dengan nama yang
#: sama, sehingga yang mengimpor "HIGH_DISAGREEMENT" mendapat angka yang berbeda
#: tergantung modul mana yang kebetulan diraih. Itu sudah terjadi: bagian 16.2
#: menurunkan ambangnya dari yang 0,4, dan tidak ada apa pun di nama itu yang
#: memberi tahu bahwa ada 0,75 di tempat lain yang menjawab pertanyaan lebih
#: dekat.
SELISIH_LAYAK_DISEBUT = 0.75


@dataclass(frozen=True, slots=True)
class DebateSummary:
    symbol: str
    interval: str
    decision: str
    confidence: float
    objections: int
    corrections: int
    veto_raised: int
    veto_upheld: int
    minority_prevailed: bool
    disagreement: float
    #: Alasan debat ini dianggap layak dikirim. Kosong = tidak dikirim.
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def notable(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "decision": self.decision,
            "confidence": self.confidence,
            "objections": self.objections,
            "corrections": self.corrections,
            "veto_raised": self.veto_raised,
            "veto_upheld": self.veto_upheld,
            "minority_prevailed": self.minority_prevailed,
            "disagreement": self.disagreement,
            "notable": self.notable,
            "reasons": list(self.reasons),
        }


def summarise(verdict: Any) -> DebateSummary:
    """Baca satu sesi council dan putuskan apakah ada perdebatan di dalamnya."""
    protest = verdict.protest
    veto = verdict.veto
    judgement = verdict.judgement

    corrections = sum(1 for r in protest.rebuttals if r.conceded)
    raised = len(veto.vetoes)
    upheld = len(veto.upheld)

    reasons: list[str] = []
    if raised:
        reasons.append(
            f"{raised} veto diajukan"
            + (f", {upheld} dikuatkan" if upheld else ", semuanya ditolak")
        )
    if getattr(judgement, "minority_prevailed", False):
        reasons.append(
            "minoritas menang: judge memihak sisi yang jumlah agent-nya lebih "
            "sedikit karena buktinya lebih kuat"
        )
    if corrections:
        reasons.append(
            f"{corrections} agent mengubah pendapat setelah disanggah"
        )

    return DebateSummary(
        symbol=verdict.symbol,
        interval=verdict.interval,
        decision=verdict.decision.value,
        confidence=verdict.confidence,
        objections=len(protest.objections),
        corrections=corrections,
        veto_raised=raised,
        veto_upheld=upheld,
        minority_prevailed=bool(getattr(judgement, "minority_prevailed", False)),
        disagreement=float(protest.disagreement),
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class CouncilNote:
    """Penilaian satu sesi council, dibawa ke pesan plan.

    Simbolnya adalah simbol **perpetual** - ``BTCUSDT`` - dan bukan simbol spot
    yang dibaca council - ``BTC/USDT``. Keduanya menyebut aset yang sama dengan
    dua ejaan, dan pesan plan dicari berdasarkan yang pertama. Versi pertama
    kode ini memakai ``verdict.symbol`` apa adanya, yang berarti pencarian
    penilaian tidak akan pernah cocok dan bagian PENILAIAN tidak akan pernah
    muncul - tanpa error, tanpa log, hanya bagian yang hilang.
    """

    symbol: str
    confidence: float
    disagreement: float
    split: VoteSplit
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: Pembacaan faktor risiko dari konteks council (PHASE 13, PASAL 13.31).
    #:
    #: Dititipkan di sini dan bukan dijadikan jalur sendiri karena catatan ini
    #: SUDAH mengalir dari `_plan_one` - tempat konteks council masih ada - ke
    #: notifier, yang cuma memegang rencana. Menambah jalur kedua untuk
    #: penumpang yang sama berarti dua jalur yang harus tetap sepakat.
    #:
    #: Kosong berarti konteksnya tidak terbaca, dan penilaian risikonya jatuh
    #: kembali ke faktor yang bisa diukur dari rencana saja - 62% cakupan
    #: alih-alih 87%. Itu perilaku yang benar, bukan kegagalan diam.
    risk_readings: dict[str, float] = field(default_factory=dict)
    #: Rezim pasar saat keputusan dibuat (PASAL 14.26).
    #:
    #: Menumpang jalur yang sama dengan ``risk_readings``, dan karena alasan
    #: yang sama: rezimnya hanya ada di konteks council, sementara notifier
    #: cuma memegang rencana. Kosong berarti tidak terbaca - dan baris rezim
    #: tidak dicetak sama sekali, bukan dicetak sebagai "UNKNOWN". Rezim yang
    #: mengaku tidak tahu dirinya sendiri terbaca seperti rezim yang bernama
    #: UNCERTAIN, dan keduanya berarti hal yang sangat berbeda.
    regime: str = ""
    #: Peta lintas timeframe (PASAL 14.4 - 14.8), atau ``None``.
    #:
    #: Menumpang jalur yang sama dengan pembacaan risiko dan rezim: ketiganya
    #: hanya bisa dibaca di tempat konteks council masih ada, dan ketiganya
    #: dibutuhkan di tempat yang hanya memegang rencana.
    lintas: Any = None
    #: Komponen berarah Decision Score (PASAL 14.16), sudah bertanda.
    #:
    #: Yang disimpan di sini hanya komponen yang **hanya ada di konteks
    #: council** - tren, struktur, momentum, volume, kesepakatan. Potongan
    #: risiko dan berita TIDAK ikut: keduanya dihitung di jalur notifikasi dari
    #: penilaian Phase 13 yang sudah ada di sana, dan menghitungnya dua kali
    #: berarti dua angka yang harus tetap sepakat.
    decision_readings: dict[str, float] = field(default_factory=dict)
    #: Penjelasan berlapis PASAL 14.29, atau ``None`` kalau tidak bisa disusun.
    #:
    #: Menumpang jalur yang sama dengan keempat penumpang di atas, dan karena
    #: alasan yang sama: **sumber** tiap alasan hanya ada di opini agent, dan
    #: opini agent hanya ada di tempat vonis council masih dipegang. ``reasons``
    #: yang sudah ada di catatan ini cuma kalimat tanpa asal - dan PASAL 14.29
    #: menuntut dua sumber BERBEDA, yang tidak bisa dihitung dari kalimat saja.
    explanation: Any = None
    #: Kode strategi Phase 12 yang dipilih, atau kalimat kosong.
    #:
    #: Dibutuhkan PASAL 14.37 untuk membedakan "pendapat yang sama diulang"
    #: dari "strategi berbeda menghasilkan pendapat yang sama". Tanpanya
    #: perbandingan strategi membandingkan dua kalimat kosong dan selalu
    #: menjawab "sama" - penjaga yang mati diam-diam, bukan penjaga yang
    #: melonggar.
    strategy: str = ""
    #: Snapshot Phase 12 dan Phase 13 (PASAL 14.40, 14.41), atau ``None``.
    #:
    #: Menumpang jalur yang sama dengan penumpang lain di atas, dan karena
    #: alasan yang sama - tapi satu hal membedakannya: isinya **tidak** dibaca
    #: per simbol. Ia dibaca sekali per jendela lima menit dan dibagikan ke
    #: seluruh simbol pada tick itu, karena pattern discovery dan drift tidak
    #: berubah antara BTCUSDT dan ETHUSDT.
    pembelajaran: Any = None
    #: :class:`~aruna.signals.quality.QualityScore` PASAL 11.1 untuk rencana
    #: ini, atau ``None``.
    #:
    #: Sepanjang 2026-08-20 ia dilaporkan hilang dari Phase 11, dan sempat
    #: disebut "tidak berlaku untuk futures" - kesimpulan yang salah.
    #: ``score_signal`` menerima konteks, opini agent, entry, stop, target dan
    #: horizon; jalur futures memegang kelimanya. Yang tidak ada bukan datanya,
    #: melainkan pemanggilnya.
    #:
    #: **Yang disimpan adalah laporannya, bukan skornya.** Sampai 2026-08-24
    #: bidang ini bertipe ``float``: ``score_signal`` menghitung dua puluh
    #: faktor, dan pemanggilnya membuang sembilan belas di baris berikutnya.
    #: Bagian 18.17 menuntut tujuh keyakinan disebut terpisah, dan lima di
    #: antaranya adalah faktor yang dibuang di sana - jadi kepatuhannya mustahil
    #: selama bidang ini hanya membawa satu angka.
    mutu: Any = None
    #: Jatah risiko hari ini (PASAL 14.41), atau ``None`` kalau tidak terbaca.
    #:
    #: Menumpang jalur yang sama dengan penumpang lain di atas, dan seperti
    #: ``pembelajaran`` ia **tidak** dibaca per simbol: satu kueri per tick,
    #: dibagikan ke seluruh simbol, karena berapa yang sudah dipertaruhkan hari
    #: ini tidak berbeda antara BTCUSDT dan ETHUSDT.
    risk_budget: Any = None
    #: Konteks historis Phase 15 (PASAL 15.32), atau ``None`` kalau ingatan
    #: tidak terbaca.
    #:
    #: Menumpang jalur yang sama dengan penumpang lain di atas. Ia **bukti**,
    #: bukan keputusan - PASAL 15.42 menyatakan keputusan final tetap milik
    #: Phase 14, dan :class:`~aruna.memory.context.KonteksHistoris` sengaja
    #: tidak punya satu pun bidang yang bisa dibaca sebagai arah.
    memory: Any = None

    @property
    def quality(self) -> float | None:
        """Skor 0-100 dari :attr:`mutu`, atau ``None``.

        Turunan, bukan bidang tersimpan - supaya "skor mutu" dan "laporan mutu"
        tidak bisa berselisih. Yang membacanya: sidik jari ingatan (PASAL
        15.32), jejak keputusan, dan gerbang kelengkapan masukan.
        """
        return None if self.mutu is None else getattr(self.mutu, "score", None)

    @property
    def debated(self) -> bool:
        return bool(self.reasons)

    @property
    def high_disagreement(self) -> bool:
        return self.disagreement >= SELISIH_LAYAK_DISEBUT


def note_of(verdict: Any, *, symbol: str | None = None) -> CouncilNote:
    """Baca satu sesi council menjadi penilaian yang bisa dicetak."""
    summary = summarise(verdict)
    return CouncilNote(
        symbol=symbol or summary.symbol,
        confidence=summary.confidence,
        disagreement=summary.disagreement,
        split=vote_split(verdict.opinions, summary.decision),
        reasons=summary.reasons,
    )


__all__ = [
    "SELISIH_LAYAK_DISEBUT",
    "CouncilNote",
    "DebateSummary",
    "note_of",
    "summarise",
]

