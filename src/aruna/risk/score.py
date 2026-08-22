"""Risk Score 0-100, dan alasannya (PASAL 13.2, 13.22, 13.26).

Satu angka yang menggabungkan tujuh belas faktor. Cara menggabungkannya
menentukan apakah ia berguna atau sekadar angka yang terlihat ilmiah, jadi
tiga keputusan ditulis di sini alih-alih tersembunyi di dalam rumus:

**1. Bobotnya eksplisit dan bisa dibantah.** :data:`FAKTOR` adalah tabel, bukan
ekspresi aritmetika. PASAL 13.29 meminta risk score dikalibrasi terhadap
outcome, dan itu hanya mungkin kalau bobotnya bisa dibaca, dibandingkan, dan
diubah lewat proposal (PASAL 12.19, 12.20) - bukan ditemukan kembali oleh siapa
pun yang membaca kodenya nanti.

**2. Faktor yang tidak terukur TIDAK dihitung nol.** Nol berarti "tidak
berisiko", dan itu kebalikan dari kebenarannya. Faktor yang hilang dikeluarkan
dari rata-rata tertimbang **dan** mengurangi cakupan skornya. PASAL 13.26
melarang mengarang; mengisi kekosongan dengan angka yang menguntungkan adalah
bentuk mengarang yang paling sulit terlihat, karena hasilnya selalu masuk akal.

**3. Skor dari terlalu sedikit faktor bukan skor.** Kalau cakupannya di bawah
:data:`MIN_COVERAGE`, yang dikembalikan adalah ``UNKNOWN`` - bukan angka
disertai peringatan kecil. Sebuah "Risk Score 32/100" yang dihitung dari tiga
dari tujuh belas faktor akan dibaca sebagai 32, apa pun yang tertulis di
sebelahnya.

**Arah tiap faktor ditulis, tidak ditebak.** Volatilitas tinggi menaikkan
risiko; likuiditas tinggi menurunkannya. Sebuah tabel yang menyimpulkan arah
dari nama kolom akan sesekali salah, dan salahnya adalah menyebut setup
berbahaya sebagai aman.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Cakupan minimum sebelum sebuah angka boleh disebut Risk Score.
#:
#: Enam puluh persen bobot terukur. Di bawahnya, skornya lebih menggambarkan
#: faktor mana yang kebetulan tersedia daripada risiko setup-nya - dan dua
#: setup dengan risiko sangat berbeda bisa menghasilkan angka yang sama hanya
#: karena keduanya kehilangan faktor yang berbeda.
MIN_COVERAGE = 0.60


class RiskLevel(StrEnum):
    """Kategori PASAL 13.2. Ambangnya persis seperti yang tertulis di sana."""

    VERY_LOW = "VERY LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY HIGH"
    #: Bukan tingkat risiko - ia ketiadaan penilaian. Dipisahkan dari
    #: VERY_HIGH dengan sengaja: "tidak tahu" dan "sangat berbahaya" menuntut
    #: tindakan yang berbeda dari operator.
    UNKNOWN = "UNKNOWN"

    @property
    def mark(self) -> str:
        return {
            RiskLevel.VERY_LOW: "🟢",
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.VERY_HIGH: "🔴",
            RiskLevel.UNKNOWN: "⚪",
        }[self]


def categorise(score: float | None) -> RiskLevel:
    """Skor ke kategori, menurut ambang PASAL 13.2."""
    if score is None:
        return RiskLevel.UNKNOWN
    if score <= 20:
        return RiskLevel.VERY_LOW
    if score <= 40:
        return RiskLevel.LOW
    if score <= 60:
        return RiskLevel.MEDIUM
    if score <= 80:
        return RiskLevel.HIGH
    return RiskLevel.VERY_HIGH


@dataclass(frozen=True, slots=True)
class Faktor:
    """Satu faktor risiko: namanya, bobotnya, dan apa artinya nilai tinggi."""

    key: str
    label: str
    weight: float
    #: Kalimat yang dicetak ketika faktor ini MENAIKKAN risiko.
    concern: str
    #: Kalimat ketika ia justru menurunkan risiko.
    comfort: str


#: Tujuh belas faktor PASAL 13.2, dengan bobotnya.
#:
#: Bobotnya adalah **tebakan awal yang jujur disebut tebakan**, bukan hasil
#: kalibrasi - belum ada cukup outcome untuk mengkalibrasinya (PASAL 13.29
#: meminta sample yang cukup, dan sejarah ARUNA masih berumur hari). Yang bisa
#: dijamin sekarang hanyalah urutannya masuk akal: jarak likuidasi dan kualitas
#: stop diberi bobot terbesar karena keduanya menentukan seberapa besar
#: kerugian bisa terjadi, bukan seberapa sering.
FAKTOR: tuple[Faktor, ...] = (
    Faktor("liquidation_distance", "jarak likuidasi", 3.0,
           "likuidasi terlalu dekat dengan entry",
           "jarak likuidasi lega"),
    Faktor("stop_quality", "kualitas stop", 3.0,
           "stop tidak berpijak pada struktur pasar",
           "stop berpijak pada struktur"),
    Faktor("risk_reward", "risk/reward", 2.5,
           "imbalan tidak sepadan dengan risikonya",
           "imbalan sepadan dengan risikonya"),
    Faktor("volatility", "volatilitas", 2.0,
           "volatilitas tinggi",
           "volatilitas terkendali"),
    Faktor("leverage", "leverage", 2.0,
           "leverage memperbesar kerugian melebihi kenyamanan",
           "leverage konservatif"),
    Faktor("liquidity", "likuiditas", 2.0,
           "likuiditas tipis",
           "likuiditas memadai"),
    Faktor("tp_quality", "kualitas target", 1.5,
           "target tidak realistis untuk timeframe ini",
           "target realistis untuk timeframe ini"),
    Faktor("market_regime", "rezim pasar", 1.5,
           "rezim pasar tidak mendukung arah ini",
           "rezim pasar mendukung"),
    Faktor("correlation", "korelasi", 1.5,
           "korelasi tinggi dengan posisi lain",
           "korelasi rendah"),
    Faktor("data_quality", "mutu data", 1.5,
           "mutu data meragukan",
           "data bersih"),
    Faktor("spread", "spread", 1.0,
           "spread lebar",
           "spread sempit"),
    Faktor("slippage", "slippage", 1.0,
           "slippage diperkirakan besar",
           "slippage kecil"),
    Faktor("news_risk", "risiko berita", 1.0,
           "ada berita berdampak",
           "tidak ada berita berdampak"),
    Faktor("funding", "funding", 1.0,
           "funding ekstrem",
           "funding wajar"),
    Faktor("open_interest", "open interest", 1.0,
           "open interest menumpuk searah",
           "open interest seimbang"),
    Faktor("liquidation_activity", "aktivitas likuidasi", 1.0,
           "likuidasi beruntun sedang terjadi",
           "tidak ada kaskade likuidasi"),
    Faktor("signal_quality", "kualitas signal", 1.0,
           "kualitas signal rendah",
           "kualitas signal tinggi"),
)

_BY_KEY = {f.key: f for f in FAKTOR}
_TOTAL_WEIGHT = sum(f.weight for f in FAKTOR)

#: Faktor yang, ketika ekstrem, TIDAK boleh dirata-ratakan hilang.
#:
#: **Kenapa rata-rata saja tidak cukup.** Terukur pada rencana sungguhan:
#: likuidasi 90 dan leverage 80 dengan sisanya aman menghasilkan 32/100 LOW.
#: Aritmetikanya benar dan kesimpulannya berbahaya - likuidasi yang terlalu
#: dekat menghabiskan modal, bukan menurunkan ekspektasi, dan modal yang habis
#: tidak bisa dikompensasi oleh spread yang sempit.
#:
#: Dua faktor saja yang punya sifat itu. `liquidation_distance` karena ia
#: mengakhiri posisi tanpa peduli arah analisisnya benar; `data_quality` karena
#: PASAL 13.26 menyatakan signal harus ditahan kalau data penting tidak bisa
#: dipercaya - dan risiko yang dihitung dari data yang tidak dipercaya adalah
#: angka tentang tidak ada.
#:
#: Ambangnya tinggi dengan sengaja. Veto yang sering berbunyi berhenti
#: dibedakan dari skor biasa, dan yang hilang justru kemampuannya menyela.
FATAL: dict[str, float] = {
    "liquidation_distance": 85.0,
    "data_quality": 85.0,
}


@dataclass(frozen=True, slots=True)
class Penilaian:
    """Skor risiko beserta apa yang menyusunnya."""

    score: float | None
    level: RiskLevel
    #: Bobot terukur dibagi seluruh bobot. Selalu dilaporkan.
    coverage: float
    #: Faktor yang menaikkan risiko, terurut dari yang paling berpengaruh.
    concerns: tuple[str, ...] = field(default_factory=tuple)
    comforts: tuple[str, ...] = field(default_factory=tuple)
    #: Faktor yang tidak terukur. Disebut namanya, bukan dihitung nol.
    unknown: tuple[str, ...] = field(default_factory=tuple)
    #: Faktor yang sendirian membuat setup ini tidak layak, apa pun skornya.
    #:
    #: Kosong pada hampir semua rencana - lihat :data:`FATAL`.
    vetoes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.score is not None

    @property
    def vetoed(self) -> bool:
        return bool(self.vetoes)

    def line(self) -> str:
        if not self.usable:
            return (
                f"{RiskLevel.UNKNOWN.mark} RISIKO TIDAK BISA DINILAI - "
                f"hanya {self.coverage:.0%} faktor terukur "
                f"(butuh {MIN_COVERAGE:.0%})"
            )
        if self.vetoed:
            # **Angkanya tetap dicetak apa adanya.** Menaikkannya ke 90 supaya
            # cocok dengan vonisnya akan menyembunyikan justru hal yang paling
            # perlu dilihat: rata-ratanya memang rendah, dan rata-rata yang
            # rendah tidak menyelamatkan setup ini.
            return (
                f"{RiskLevel.VERY_HIGH.mark} {self.score:.0f}/100 rata-rata, "
                f"TAPI {RiskLevel.VERY_HIGH.value} - {self.vetoes[0]}"
            )
        return (
            f"{self.level.mark} {self.score:.0f}/100 {self.level.value} "
            f"({self.coverage:.0%} faktor terukur)"
        )

    def report(self) -> list[str]:
        """Blok RISK ANALYSIS (PASAL 13.22), sebagai baris."""
        baris = ["🛡 RISK ANALYSIS", "", self.line()]
        if self.vetoed:
            # Ditaruh paling atas, sebelum apa pun yang meringankan. Daftar
            # "yang meringankan" yang dibaca lebih dulu akan melunakkan vonis
            # yang justru tidak boleh dilunakkan.
            baris += ["", "TIDAK LAYAK DIAMBIL:"]
            baris += [f"  ⛔ {t}" for t in self.vetoes]
        if self.comforts:
            baris += ["", "Yang meringankan:"]
            baris += [f"  ✓ {t}" for t in self.comforts[:4]]
        if self.concerns:
            baris += ["", "Yang memberatkan:"]
            baris += [f"  ⚠️ {t}" for t in self.concerns[:4]]
        if self.unknown:
            # Disebut, tidak didiamkan. Faktor yang hilang dari laporan
            # terbaca seperti faktor yang aman.
            baris += ["", "Tidak terukur:"]
            baris += [f"  ? {t}" for t in self.unknown[:6]]
        return baris


def assess(readings: dict[str, float | None]) -> Penilaian:
    """Gabungkan pembacaan faktor menjadi satu skor risiko.

    Tiap nilai adalah 0-100 di mana **tinggi berarti lebih berisiko**, dan
    ``None`` berarti tidak terukur. Penyeragaman arah itu dilakukan pemanggil,
    bukan di sini: yang tahu apakah likuiditas 0.8 itu bagus atau buruk adalah
    lapisan yang mengukurnya, dan menebaknya di sini akan sesekali membalik
    tanda pada faktor yang paling penting.

    Kunci yang tidak dikenal diabaikan dengan diam - tabel faktor bisa
    bertambah, dan pemanggil lama tidak boleh pecah karenanya. Kunci yang
    dikenal tapi bernilai None masuk ke ``unknown``.
    """
    terukur: list[tuple[Faktor, float]] = []
    hilang: list[str] = []

    for f in FAKTOR:
        nilai = readings.get(f.key)
        if nilai is None:
            hilang.append(f.label)
            continue
        terukur.append((f, max(0.0, min(100.0, float(nilai)))))

    bobot_terukur = sum(f.weight for f, _ in terukur)
    coverage = bobot_terukur / _TOTAL_WEIGHT if _TOTAL_WEIGHT else 0.0

    # **Veto dihitung SEBELUM gerbang cakupan, dan itu perbaikan atas bentuk
    # sebelumnya.** Versi pertama keluar lebih dulu saat cakupannya tipis, jadi
    # sebuah faktor fatal yang SUDAH terukur ikut terbuang bersama skornya -
    # likuidasi 95 diabaikan karena faktor LAIN tidak terukur.
    #
    # Itu terbalik. Cakupan yang tipis membatalkan kemampuan menyimpulkan
    # "aman"; ia tidak membatalkan kemampuan melihat satu hal yang mematikan.
    # Ditemukan oleh test, bukan oleh operator.
    veto = tuple(
        f.concern for f, v in terukur
        if f.key in FATAL and v >= FATAL[f.key]
    )

    if coverage < MIN_COVERAGE or not terukur:
        return Penilaian(
            score=None,
            level=RiskLevel.VERY_HIGH if veto else RiskLevel.UNKNOWN,
            coverage=coverage,
            unknown=tuple(hilang),
            vetoes=veto,
        )

    skor = sum(f.weight * v for f, v in terukur) / bobot_terukur

    # Diurutkan menurut sumbangan terhadap skor - bobot dikali nilai - bukan
    # menurut nilai saja. Faktor bernilai 90 dengan bobot 1.0 menyumbang lebih
    # sedikit daripada faktor bernilai 60 dengan bobot 3.0, dan daftar yang
    # mengurutkan menurut nilai akan menaruh yang salah di puncak.
    berat = sorted(terukur, key=lambda p: -(p[0].weight * p[1]))
    ringan = sorted(terukur, key=lambda p: p[0].weight * p[1])

    return Penilaian(
        score=round(skor, 1),
        level=RiskLevel.VERY_HIGH if veto else categorise(skor),
        coverage=coverage,
        concerns=tuple(f.concern for f, v in berat if v > 50),
        comforts=tuple(f.comfort for f, v in ringan if v <= 50),
        unknown=tuple(hilang),
        vetoes=veto,
    )


def weight_of(key: str) -> float:
    """Bobot satu faktor. Nol untuk kunci yang tidak dikenal."""
    f = _BY_KEY.get(key)
    return f.weight if f else 0.0


__all__ = [
    "FAKTOR",
    "MIN_COVERAGE",
    "Faktor",
    "Penilaian",
    "RiskLevel",
    "assess",
    "categorise",
    "weight_of",
]
