"""Kosakata publik ARUNA, dan blok hasil pemilihan (PASAL 1, 3, 15).

Di dalam sistem ada empat keputusan: ``BUY``, ``SELL``, ``WAIT``,
``NO_SIGNAL``. Yang keluar ke operator hanya tiga: **LONG**, **SHORT**,
**NO SIGNAL**.

``WAIT`` tidak pernah keluar, dan itu bukan soal gaya bahasa. ``WAIT`` di
dalam sistem berarti "council belum melihat cukup bukti untuk berpihak" -
sebuah keadaan internal yang berguna untuk pencatatan dan penilaian. Dibaca di
layar ponsel, kata yang sama berbunyi seperti instruksi: *tunggu dulu, sebentar
lagi ada*. Tidak ada yang akan datang. Tidak ada sesuatu yang sedang ditunggu.
Yang benar adalah: tidak ada signal.

Perbedaannya bukan sepele. "Tunggu" membuat pembaca menahan diri sambil terus
memantau, siap masuk saat aba-aba berikutnya. "Tidak ada signal" membuatnya
pergi. Yang kedua itulah yang sebenarnya dimaksud ARUNA, dan yang pertama
membuat operator menunggui sesuatu yang tidak akan pernah dikirim.

Karena itu penerjemahannya satu arah dan tidak bisa dilewati: ``WAIT`` menjadi
``NO SIGNAL``, dan ada penjaga yang menolak mengirim pesan yang masih memuat
kosakata internal.

**ARUNA MENGANALISIS SAJA.** Blok di sini adalah analisis, bukan perintah.
Tidak ada order yang dikirim, tidak ada leverage yang diubah, tidak ada dana
yang berpindah (PASAL 20).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aruna.core.enums import AgentRole, Decision

#: PASAL 1. Tiga keluaran, tidak lebih.
LONG = "LONG"
SHORT = "SHORT"
NO_SIGNAL = "NO SIGNAL"

#: Setiap ``Decision`` internal punya satu terjemahan publik, termasuk yang
#: tidak berarah. Peta yang lengkap dipilih daripada ``.get(..., default)``:
#: sebuah anggota enum baru harus membuat ini gagal keras, bukan diam-diam
#: menjadi "NO SIGNAL" dan mengubur keputusan yang belum pernah dipikirkan.
PUBLIC_DECISION: dict[Decision, str] = {
    Decision.BUY: LONG,
    Decision.SELL: SHORT,
    Decision.WAIT: NO_SIGNAL,
    Decision.NO_SIGNAL: NO_SIGNAL,
    Decision.UNKNOWN_MARKET: NO_SIGNAL,
}

#: Warna di judul, supaya arahnya terbaca sebelum kalimat pertama.
MARK: dict[str, str] = {LONG: "🟢", SHORT: "🔴", NO_SIGNAL: "🟡"}

#: Penanda pesan uji coba (huruf besar, di atas dan di bawah).
#:
#: Sebuah pesan tes yang terbaca sebagai signal asli adalah kegagalan paling
#: mahal yang bisa dihasilkan lapisan ini: operator bertindak atas angka yang
#: sengaja dikarang untuk memeriksa tata letak. Karena itu penandanya bukan
#: catatan kecil di kaki pesan - ia baris pertama yang terbaca, dan diulang di
#: baris terakhir, karena notifikasi ponsel sering memotong bagian tengahnya.
#: Kalimatnya ditulis operator, dan diulang dua kali di dalam satu baris karena
#: itulah yang dimintanya - pembacaan sekilas di layar kunci harus menabrak
#: kata "TEST" berapa pun bagian pesan yang terpotong.
TEST_BANNER = "INI BUKAN SINYAL INI TEST BUKAN SINYAL INI TEST"

#: Kosakata internal yang tidak boleh muncul di pesan keluar (PASAL 1, 15).
#: Dicocokkan sebagai kata utuh: "SELL" harus tertangkap, "SELLING" tidak, dan
#: "WAITING" di kalimat bahasa Inggris bukan urusan aturan ini.
_INTERNAL_WORDS = ("WAIT", "BUY", "SELL", "NO_SIGNAL", "UNKNOWN_MARKET")
_INTERNAL_RE = re.compile(
    r"(?<![A-Za-z_])(" + "|".join(_INTERNAL_WORDS) + r")(?![A-Za-z_])"
)


class InternalVocabularyLeak(ValueError):
    """Sebuah pesan keluar masih memuat kosakata internal."""


def public_decision(decision: Decision | str) -> str:
    """Terjemahkan satu keputusan internal ke kosakata publik.

    Menerima ``str`` juga karena keputusan sudah tersimpan sebagai string di
    beberapa baris database, dan memaksa pemanggil membangun ulang enum-nya
    hanya untuk mencetak satu kata akan menyebarkan konversi itu ke mana-mana.
    """
    if isinstance(decision, str):
        try:
            decision = Decision(decision)
        except ValueError as exc:
            raise InternalVocabularyLeak(
                f"keputusan tidak dikenal: {decision!r}"
            ) from exc
    try:
        return PUBLIC_DECISION[decision]
    except KeyError as exc:  # pragma: no cover - dijaga test kelengkapan
        raise InternalVocabularyLeak(
            f"{decision!r} belum punya terjemahan publik"
        ) from exc


def guard_public(text: str) -> str:
    """Tolak pesan yang masih memuat kosakata internal (PASAL 1, 15).

    Ini penjaga, bukan penerjemah. Ia sengaja **tidak** memperbaiki teksnya:
    sebuah blok yang lolos dengan "WAIT" diganti diam-diam adalah blok yang
    kalimat di sekitarnya kemungkinan besar juga salah - "menunggu konfirmasi",
    "sampai ada aba-aba" - dan penerjemahan kata tunggal akan menyembunyikan
    itu sambil terlihat seperti perbaikan.
    """
    found = _INTERNAL_RE.search(text)
    if found is not None:
        raise InternalVocabularyLeak(
            f"menolak mengirim pesan yang memuat kosakata internal "
            f"{found.group(1)!r} (PASAL 1: yang keluar hanya LONG, SHORT, "
            f"NO SIGNAL)"
        )
    return text


@dataclass(frozen=True, slots=True)
class VoteSplit:
    """Siapa di sisi mana, bukan berapa banyak (PASAL 3).

    ``abstain`` berdiri sendiri dan tidak dilebur ke ``kontra``. Agent yang
    tidak punya bukti untuk dinilai tidak sedang menolak apa pun; memasukkannya
    ke KONTRA akan membuat data yang hilang terbaca sebagai perlawanan, dan
    membuat setiap feed yang mati terlihat seperti council yang terbelah
    (PASAL 49).
    """

    setuju: tuple[str, ...]
    kontra: tuple[str, ...]
    abstain: tuple[str, ...] = ()

    @property
    def total(self) -> str:
        return f"{len(self.setuju)} VS {len(self.kontra)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "setuju": list(self.setuju),
            "kontra": list(self.kontra),
            "abstain": list(self.abstain),
            "total": self.total,
        }


def _label(role: AgentRole | str) -> str:
    return role.value if isinstance(role, AgentRole) else str(role)


def vote_split(opinions: Any, decision: Decision | str) -> VoteSplit:
    """Bagi pendapat agent menjadi SETUJU dan KONTRA terhadap putusan akhir.

    Yang dibandingkan adalah **kosakata publik**, bukan enum internal. Seorang
    agent yang bilang ``WAIT`` sementara council memutuskan ``NO_SIGNAL``
    sepakat pada apa yang sampai ke operator - keduanya berarti tidak ada
    posisi - dan mencatatnya sebagai KONTRA akan menampilkan perpecahan yang
    tidak pernah terjadi.
    """
    target = public_decision(decision)
    setuju: list[str] = []
    kontra: list[str] = []
    abstain: list[str] = []
    for opinion in opinions:
        name = _label(getattr(opinion, "role", "?"))
        if getattr(opinion, "abstained", False):
            abstain.append(name)
        elif public_decision(opinion.decision) == target:
            setuju.append(name)
        else:
            kontra.append(name)
    return VoteSplit(tuple(setuju), tuple(kontra), tuple(abstain))


def render_votes(
    split: VoteSplit, *, heading: str | None = "HASIL PEMILIHAN:"
) -> list[str]:
    """Blok HASIL PEMILIHAN, persis seperti template PASAL 3.

    ``heading`` bisa diganti atau dimatikan karena blok hasil memakai judul
    sendiri, "HASIL PEMILIHAN AWAL:". Tanpa ini judulnya tercetak dua kali
    beruntun - terlihat seperti pesan yang tertempel dua kali.

    Nilai ditulis rata kiri di baris sendiri, tanpa indentasi - itu bentuk yang
    ditetapkan operator, dan bentuknya bukan selera. Versi sebelumnya menambah
    dua spasi di depan tiap nilai "supaya rapi"; hasilnya pesan yang tidak lagi
    cocok dengan template yang jadi acuan pembacanya.
    """
    lines = [heading, ""] if heading else []

    # **Kosong sama sekali berarti tidak tercatat, bukan nol lawan nol.**
    #
    # Terlihat di layar operator: sebuah keputusan LONG dengan confidence 38%
    # disertai "SETUJU: (tidak ada) / KONTRA: (tidak ada) / TOTAL: 0 VS 0".
    # Dibaca apa adanya, itu berarti sebelas agent memutuskan LONG tanpa satu
    # pun dari mereka berpendapat - yang mustahil, dan yang membuat seluruh
    # pesan tidak bisa dipercaya.
    #
    # Yang sebenarnya terjadi: barisnya tidak membawa suaranya, dan pemanggil
    # jatuh ke `VoteSplit((), ())`. "Tidak diukur" dicetak sebagai "nol"
    # adalah bentuk karangan yang paling halus - ia tidak menambah apa pun,
    # hanya mengubah ketiadaan menjadi pengukuran (PASAL 4).
    if not split.setuju and not split.kontra and not split.abstain:
        return [
            *lines,
            "SUARA AGENT:",
            "TIDAK TERCATAT untuk keputusan ini",
        ]

    lines += ["SETUJU:"]
    lines += list(split.setuju) or ["(tidak ada)"]
    lines += ["", "KONTRA:"]
    lines += list(split.kontra) or ["(tidak ada)"]
    if split.abstain:
        # Hanya muncul kalau memang ada yang abstain. Bagian ini tidak ada di
        # template, dan ditambahkan karena alternatifnya berbohong: agent yang
        # tidak punya bukti untuk dinilai tidak sedang menolak apa pun, dan
        # memasukkannya ke KONTRA membuat feed yang mati terbaca sebagai
        # council yang terbelah.
        lines += ["", "ABSTAIN:"]
        lines += list(split.abstain)
    lines += ["", "TOTAL:", split.total]
    return lines


def _leverage_lines(leverage: Any, liquidation: Any) -> list[str]:
    """Leverage, dan harga likuidasi yang harus menyertainya.

    **Leverage tidak pernah dikirim sendirian.** Angka itu memberi tahu
    seberapa besar posisinya dan sama sekali tidak memberi tahu seberapa jauh
    ia boleh salah sebelum bursa menutupnya paksa. Dua angka itu hanya berarti
    kalau dibaca bersama: 10x tanpa harga likuidasi terbaca seperti "modal
    dikali sepuluh" dan menyembunyikan bahwa gerakan sepuluh persen melawan
    posisi sudah menghabiskannya.

    Kalau harga likuidasinya tidak bisa dihitung, itu **dikatakan** - bukan
    dicetak sebagai tanda hubung yang mudah dilewati mata, dan bukan alasan
    untuk menyembunyikan leverage-nya. Pembaca berhak tahu keduanya: bahwa
    posisinya berleverage, dan bahwa batas paksanya tidak diketahui.
    """
    if leverage is None:
        return []
    lines = ["", "LEVERAGE:", f"{leverage}x"]
    if liquidation is None:
        lines += [
            "",
            "HARGA LIKUIDASI:",
            "TIDAK BISA DIHITUNG",
            "Leverage tanpa angka ini tidak memberi tahu seberapa jauh",
            "posisi boleh salah sebelum ditutup paksa bursa.",
        ]
    else:
        lines += ["", "HARGA LIKUIDASI:", f"{liquidation}"]
    return lines


def render_analysis(
    *,
    symbol: str,
    decision: Decision | str,
    split: VoteSplit,
    confidence: float | None = None,
    entry: Any = None,
    stop: Any = None,
    target: Any = None,
    timeframe: str | None = None,
    reward_risk: Any = None,
    leverage: Any = None,
    liquidation: Any = None,
    reason: str | None = None,
    model_version: str | None = None,
    test_mode: bool = False,
) -> str:
    """Blok ARUNA ANALYSIS (PASAL 3, 4, 5).

    ``model_version`` adalah versi model yang menghasilkan keputusan ini, dan
    ia dicetak pada setiap blok berarah.

    **Tanpa itu, rekam jejaknya tidak bisa dibaca sebagai rekam jejak.** Sebuah
    daftar menang-kalah yang mencampur beberapa versi model mengukur rata-rata
    dari hal-hal yang berbeda, dan rata-rata itu akan terlihat stabil justru
    ketika satu versi memburuk dan versi lain menutupinya. Versinya sudah
    tersimpan di setiap baris ``signal_snapshots`` sejak lama; yang belum ada
    hanyalah menyebutkannya kepada pembacanya.

    Angka entry/stop/target hanya dicetak kalau **ketiganya** ada. Sebuah blok
    LONG dengan entry dan target tapi tanpa stop adalah setengah rencana, dan
    setengah rencana lebih berbahaya daripada tidak ada rencana: ia memberi
    tahu pembaca ke mana harus berharap tanpa memberi tahu di mana ia salah.

    Seluruh blok lewat :func:`guard_public` sebelum dikembalikan.
    """
    kata = public_decision(decision)
    lines: list[str] = []
    if test_mode:
        lines += [TEST_BANNER, ""]
    lines += [
        f"{MARK[kata]} ARUNA ANALYSIS",
        "",
        symbol,
        "",
        "FINAL DECISION:",
        kata,
        "",
    ]
    lines += render_votes(split)

    if confidence is not None:
        lines += ["", "CONFIDENCE:", f"{confidence * 100:.0f}%"]

    # **Di luar cabang mana pun, dan itu perbaikan atas bentuk sebelumnya.**
    #
    # Dulu baris ini berada DI DALAM cabang yang punya entry/stop/target, jadi
    # pesan yang kehilangan level juga kehilangan timeframe - dua kali tidak
    # berguna sekaligus. Dan tanpa timeframe, sebuah arah tidak bisa
    # dieksekusi siapa pun: LONG lima belas menit dan LONG satu hari adalah dua
    # keputusan yang berbeda dengan stop yang berbeda.
    #
    # Ketiadaannya dikatakan, bukan didiamkan. Baris yang hilang terbaca
    # seperti "terserah", dan terserah adalah jawaban yang paling mahal.
    lines += [
        "",
        "TIMEFRAME:",
        f"{timeframe}" if timeframe else "TIDAK TERCATAT",
    ]

    lengkap = entry is not None and stop is not None and target is not None
    if kata is not NO_SIGNAL and lengkap:
        lines += [
            "",
            "ENTRY:",
            f"{entry}",
            "",
            "STOP LOSS:",
            f"{stop}",
            "",
            "TAKE PROFIT:",
            f"{target}",
        ]
        if reward_risk is not None:
            lines += ["", "R:R:", f"{reward_risk}"]
        lines += _leverage_lines(leverage, liquidation)
        # Satu baris, di bawah angkanya, karena di situlah ia dibaca. Operator
        # meminta angka-angka ini ada di pesan dan meminta statusnya jelas:
        # acuan, bukan perintah.
        lines += ["", "CATATAN:", "ENTRY / SL / TP / LEVERAGE = ACUAN SAJA"]
    elif kata is not NO_SIGNAL:
        # Dikatakan, bukan dihilangkan. Blok berarah tanpa angka yang diam soal
        # ketiadaannya terbaca seperti "masuk sekarang, terserah di mana".
        lines += [
            "",
            "ENTRY / STOP LOSS / TAKE PROFIT:",
            "TIDAK TERSEDIA - level tidak bisa diukur dari data yang ada",
        ]

    if reason:
        lines += ["", "REASON:", reason]

    if model_version:
        lines += ["", "MODEL:", model_version]

    lines += ["", "ARUNA ANALYST ONLY", "EXECUTION: USER"]
    if test_mode:
        lines += ["", TEST_BANNER]
    return guard_public("\n".join(lines))


__all__ = [
    "LONG",
    "MARK",
    "NO_SIGNAL",
    "PUBLIC_DECISION",
    "SHORT",
    "InternalVocabularyLeak",
    "VoteSplit",
    "guard_public",
    "public_decision",
    "render_analysis",
    "render_votes",
    "vote_split",
]
