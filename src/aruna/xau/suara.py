"""Batas terjemahan antara kosakata dewan dan kosakata XAU.

**Ini satu-satunya berkas di ``aruna/xau/`` yang boleh menyebut
``Decision.WAIT``.**  ``test_kosakata_xau`` menegakkannya dengan memindai
seluruh paket.

Alasannya ada di dua aturan yang keduanya benar.  ``AgentOpinion.validate()``
mewajibkan sebuah agen yang abstain mengembalikan ``WAIT`` - itu penanda milik
mesin dewan, yang dipakai bersama crypto dan futures.  Spec XAU melarang
``WAIT`` sebagai kosakata keputusan yang sampai ke operator.  Keduanya bicara
tentang hal berbeda, jadi yang dibutuhkan bukan mengubah salah satunya -
menyunting ``AgentOpinion`` akan menyentuh jalur futures, yang dilarang -
melainkan satu tempat yang menerjemahkan, dan hanya satu.

**Netral bukan setengah menentang.**  Seorang agen yang menahan diri karena
buktinya tipis tidak sedang membantah apa pun.  Menghitungnya sebagai
kontradiksi akan membuat setiap kondisi sepi terlihat seperti perselisihan, dan
gerbang kontradiksi akan menolak justru saat pasar paling tenang.

**Kontradiksi ``None`` berarti tidak terukur.**  Kalau seluruh agen netral,
tidak ada perselisihan untuk diukur - dan itu berbeda dari perselisihan yang
diukur lalu hasilnya nol.  Pemanggil yang menyamakan keduanya akan meloloskan
sinyal yang tidak seorang pun mendukungnya, karena "kontradiksi 0" terbaca
sebagai kesepakatan bulat.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.agents.base import AgentOpinion
from aruna.agents.deliberation import Deliberation
from aruna.core.enums import AgentRole, Decision


class Suara(StrEnum):
    """Sikap satu agen terhadap arah yang diusulkan."""

    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    NEUTRAL = "NEUTRAL"


def suara_terhadap(opinion: AgentOpinion, arah: Decision) -> Suara:
    """Sikap ``opinion`` terhadap ``arah``.

    ``arah`` harus BUY atau SELL: merekap terhadap NO_SIGNAL tidak punya arti,
    dan diam-diam memulangkan NEUTRAL akan menyembunyikan bug pemanggil di
    balik hasil yang terlihat masuk akal.
    """
    if not arah.is_directional:
        raise ValueError(
            f"arah harus BUY atau SELL untuk merekap suara, bukan {arah.value}"
        )
    if opinion.abstained or not opinion.decision.is_directional:
        return Suara.NEUTRAL
    return Suara.AGREE if opinion.decision is arah else Suara.DISAGREE


@dataclass(frozen=True, slots=True)
class SuaraAgen:
    """Sikap satu agen, beserta apa yang ia katakan sendiri.

    ``suara`` dan ``decision`` menjawab pertanyaan berbeda dan keduanya perlu
    disimpan.  ``suara`` adalah SIKAP terhadap arah yang diusulkan - bahan
    untuk mengukur kontradiksi.  ``decision`` adalah apa yang agen itu sendiri
    katakan - bahan untuk menilai agennya di Rencana 3.

    Menyimpan sikapnya saja membuat seluruh baris NEUTRAL terbaca seolah
    agennya mengembalikan "NEUTRAL", yang bukan sebuah :class:`Decision`.
    """

    role: AgentRole
    suara: Suara
    decision: Decision
    confidence: float
    abstained: bool


@dataclass(frozen=True, slots=True)
class RekapSuara:
    """Hitungan sikap seluruh agen terhadap satu arah."""

    setuju: int
    menentang: int
    netral: int
    rincian: tuple[SuaraAgen, ...]
    #: Jumlah keyakinan yang SUDAH dikalikan keandalan tiap agen.  Nol berarti
    #: belum ada bobot terukur - modul XAU baru punya bobot setelah sepuluh
    #: hasil terselesaikan, dan sebelum itu tiap suara bernilai sama.
    bobot_setuju: float = 0.0
    bobot_menentang: float = 0.0

    @property
    def bersuara(self) -> int:
        """Agen yang mengambil arah.  Netral tidak termasuk."""
        return self.setuju + self.menentang

    @property
    def berbobot(self) -> bool:
        """``True`` kalau keandalan agen sudah terukur dan ikut menimbang."""
        return (self.bobot_setuju + self.bobot_menentang) > 0.0

    @property
    def kontradiksi(self) -> float | None:
        """0 = bulat, 1 = terbelah rata.  ``None`` = tidak ada yang bersuara.

        Diukur hanya di antara yang BERSUARA - lihat docstring modul.

        **Dihitung dari BOBOT begitu keandalan terukur.**  Sebelum ada bobot,
        tiap suara bernilai satu; sesudahnya, perbedaan pendapat dari agen yang
        terbukti membaca pasar dengan benar berbobot lebih daripada perbedaan
        pendapat dari agen yang sering meleset.  Menghitung keduanya sama berat
        berarti koreksi diri tidak pernah mengubah satu keputusan pun - dan
        bobot yang tidak mengubah apa-apa lebih buruk daripada tak ada bobot,
        karena laporannya mengaku menyetel diri.
        """
        if self.berbobot:
            total = self.bobot_setuju + self.bobot_menentang
            return 2 * min(self.bobot_setuju, self.bobot_menentang) / total
        if self.bersuara == 0:
            return None
        minoritas = min(self.setuju, self.menentang)
        return 2 * minoritas / self.bersuara


def rekap(
    deliberation: Deliberation,
    arah: Decision,
    bobot: dict[str, float] | None = None,
) -> RekapSuara:
    """Rekap sikap seluruh agen ronde satu terhadap ``arah``.

    ``bobot`` adalah keandalan terukur per agen, hasil koreksi diri.  Kosong
    berarti belum terukur dan tiap suara bernilai sama - keadaan modul XAU
    sampai sepuluh hasil pertama terselesaikan.
    """
    rincian = tuple(
        SuaraAgen(
            role=o.role,
            suara=suara_terhadap(o, arah),
            decision=o.decision,
            confidence=o.confidence,
            abstained=o.abstained,
        )
        for o in deliberation.opinions
    )
    hitung = [s.suara for s in rincian]
    peta = bobot or {}
    # Keyakinan dikalikan keandalan. Dijumlahkan HANYA saat ada bobot terukur:
    # menjumlahkan keyakinan mentah tanpa keandalan akan membuat agen yang
    # percaya diri menang atas agen yang benar.
    terbobot = {
        Suara.AGREE: 0.0,
        Suara.DISAGREE: 0.0,
    }
    if peta:
        for s in rincian:
            if s.suara in terbobot:
                terbobot[s.suara] += s.confidence * peta.get(s.role.value, 1.0)

    return RekapSuara(
        setuju=hitung.count(Suara.AGREE),
        menentang=hitung.count(Suara.DISAGREE),
        netral=hitung.count(Suara.NEUTRAL),
        rincian=rincian,
        bobot_setuju=terbobot[Suara.AGREE],
        bobot_menentang=terbobot[Suara.DISAGREE],
    )


def ke_keputusan_xau(decision: Decision) -> Decision:
    """Kosakata dewan → kosakata XAU.  ``WAIT`` tidak pernah lolos dari sini."""
    return Decision.NO_SIGNAL if decision is Decision.WAIT else decision


__all__ = [
    "RekapSuara",
    "Suara",
    "SuaraAgen",
    "ke_keputusan_xau",
    "rekap",
    "suara_terhadap",
]
