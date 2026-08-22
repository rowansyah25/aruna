"""Agent mana pandai pada keadaan yang mana (PASAL 12.5).

Council menjalankan sebelas agent pada setiap keputusan, dan sampai modul ini
ada, satu-satunya pertanyaan yang bisa dijawab tentang mereka adalah "seberapa
sering agent ini benar" - satu angka per agent, dirata-rata melintasi pasar
yang trending, yang sideways, dan yang sedang berbalik arah.

Angka itu menyembunyikan hal yang paling berguna. Seorang agent yang membaca
tren dengan sangat baik dan berbalik arah dengan sangat buruk terlihat
sedang-sedang saja - dan bobotnya ikut sedang-sedang saja di kedua keadaan, di
mana ia seharusnya didengarkan pada yang satu dan diabaikan pada yang lain.

**Yang TIDAK dilakukan modul ini: mengubah bobot.** PASAL 11.16 melarang
modifikasi model otomatis. Yang dihasilkan di sini adalah pengukuran; kalau
pengukuran itu menyarankan bobot berubah, jalurnya lewat proposal dan
persetujuan operator (PASAL 12.19, 12.20), bukan lewat modul ini.

**Kesepakatan diukur dengan kosakata publik.** Seorang agent yang bilang WAIT
sementara council memutuskan NO_SIGNAL sepakat pada apa yang sampai ke
operator. Alasan lengkapnya ada di ``CouncilRepository._save_votes``; yang
penting di sini: kolom ``agreed_with_council`` sudah menghitungnya begitu, dan
modul ini membacanya, bukan menghitung ulang dengan aturan yang berbeda.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from aruna.learning.evidence import Evidence, EvidenceLevel

#: Selisih minimum antara rezim terbaik dan terburuk sebelum seorang agent
#: boleh disebut spesialis.
#:
#: Lima belas poin persentase. Di bawah itu, perbedaannya lebih kecil daripada
#: lebar selang kepercayaan pada sample yang realistis - artinya "lebih baik di
#: TRENDING" tidak bisa dibedakan dari "kebetulan lebih banyak menang di
#: TRENDING".
SPESIALIS_GAP = 0.15


@dataclass(frozen=True, slots=True)
class Vote:
    """Satu suara agent pada satu sesi yang hasilnya sudah diketahui."""

    role: str
    regime: str
    #: Suaranya searah dengan keputusan council. Dibaca dari kolom yang sudah
    #: menghitungnya, bukan disimpulkan ulang di sini.
    agreed: bool
    abstained: bool
    #: Prediksi council itu akhirnya menang.
    won: bool


@dataclass(frozen=True, slots=True)
class RegimeSkill:
    """Seberapa sering seorang agent benar dalam satu rezim."""

    regime: str
    evidence: Evidence

    @property
    def accuracy(self) -> float | None:
        return self.evidence.win_rate


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Profil satu agent: akurasinya, dipecah per rezim."""

    role: str
    per_regime: tuple[RegimeSkill, ...] = field(default_factory=tuple)

    @property
    def overall(self) -> Evidence:
        from aruna.learning.evidence import pooled

        return pooled([s.evidence for s in self.per_regime])

    @property
    def measured(self) -> tuple[RegimeSkill, ...]:
        """Rezim yang sample-nya cukup untuk dibicarakan."""
        return tuple(s for s in self.per_regime if s.evidence.conclusive)

    @property
    def best(self) -> RegimeSkill | None:
        diukur = self.measured
        return max(diukur, key=lambda s: s.accuracy or 0.0) if diukur else None

    @property
    def worst(self) -> RegimeSkill | None:
        diukur = self.measured
        return min(diukur, key=lambda s: s.accuracy or 0.0) if diukur else None

    @property
    def specialty(self) -> str | None:
        """Rezim yang agent ini jelas lebih baik padanya, atau None.

        None jauh lebih sering daripada yang diharapkan, dan itu benar. Sebuah
        label spesialis menuntut tiga hal sekaligus: dua rezim yang keduanya
        bersample cukup, jarak di antaranya melebihi
        :data:`SPESIALIS_GAP`, dan selang keduanya tidak bertindihan. Yang
        ketiga yang paling sering menggagalkannya - dan tanpa syarat itu,
        "spesialis reversal" hanyalah rezim yang kebetulan menang lebih sering.
        """
        terbaik, terburuk = self.best, self.worst
        if terbaik is None or terburuk is None or terbaik is terburuk:
            return None
        a, b = terbaik.accuracy, terburuk.accuracy
        if a is None or b is None or a - b < SPESIALIS_GAP:
            return None
        # Selangnya harus terpisah. Dua rentang yang bertindihan adalah dua
        # angka yang belum bisa dibedakan, seberapa jauh pun titik tengahnya.
        if terbaik.evidence.interval[0] <= terburuk.evidence.interval[1]:
            return None
        return terbaik.regime

    def line(self) -> str:
        """Satu baris untuk laporan. Selalu menyebut sample."""
        khusus = self.specialty
        judul = f"{self.role}: {self.overall.label(noun='benar')}"
        if khusus is None:
            if not self.measured:
                return f"{judul} - belum ada rezim bersample cukup"
            return f"{judul} - belum ada spesialisasi yang terbukti"
        return f"{judul} - lebih kuat di {khusus}"


def build_profiles(votes: Iterable[Vote]) -> tuple[AgentProfile, ...]:
    """Ukur tiap agent, dipecah per rezim.

    **Yang dihitung: seberapa sering suara agent ini ada di sisi yang benar.**
    Sebuah suara dihitung benar ketika ia searah dengan council DAN council
    menang, atau ketika ia menentang council DAN council kalah. Bentuk kedua
    itu yang membuat angka ini berarti - tanpa ia, seorang agent bisa mencapai
    akurasi sempurna dengan selalu setuju, dan papan peringkatnya akan
    memberi peringkat tertinggi kepada agent yang tidak pernah berpendapat.

    Abstain tidak dihitung sama sekali. Ia bukan benar dan bukan salah; ia
    adalah tidak menyatakan apa-apa, dan memasukkannya ke penyebut menghukum
    agent yang jujur mengaku tidak tahu.
    """
    ember: dict[tuple[str, str], list[bool]] = {}
    for v in votes:
        if v.abstained:
            continue
        benar = v.agreed == v.won
        ember.setdefault((v.role, v.regime), []).append(benar)

    per_role: dict[str, list[RegimeSkill]] = {}
    for (role, regime), hasil in ember.items():
        bukti = Evidence(
            wins=sum(1 for b in hasil if b),
            losses=sum(1 for b in hasil if not b),
        )
        per_role.setdefault(role, []).append(RegimeSkill(regime, bukti))

    profil = [
        AgentProfile(
            role=role,
            per_regime=tuple(
                sorted(skills, key=lambda s: (-s.evidence.total, s.regime))
            ),
        )
        for role, skills in per_role.items()
    ]
    profil.sort(key=lambda p: (-p.overall.total, p.role))
    return tuple(profil)


def specialists(profiles: Iterable[AgentProfile]) -> dict[str, str]:
    """Agent yang spesialisasinya terbukti, dipetakan ke rezimnya.

    Kosong adalah hasil yang sah dan akan sering terjadi. Sebuah peta kosong
    berarti "belum terbukti", bukan "semua agent sama saja" - dan pemanggilnya
    harus memperlakukan keduanya berbeda.
    """
    return {
        p.role: khusus
        for p in profiles
        if (khusus := p.specialty) is not None
    }


def summary(profiles: Iterable[AgentProfile]) -> str:
    daftar = list(profiles)
    ahli = specialists(daftar)
    terukur = sum(1 for p in daftar if p.measured)
    if not daftar:
        return "belum ada suara agent yang bisa dinilai"
    return (
        f"{len(daftar)} agent, {terukur} punya rezim bersample cukup, "
        f"{len(ahli)} spesialisasi terbukti"
    )


__all__ = [
    "SPESIALIS_GAP",
    "AgentProfile",
    "EvidenceLevel",
    "RegimeSkill",
    "Vote",
    "build_profiles",
    "specialists",
    "summary",
]
