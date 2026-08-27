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
class RekapSuara:
    """Hitungan sikap seluruh agen terhadap satu arah."""

    setuju: int
    menentang: int
    netral: int
    rincian: tuple[tuple[AgentRole, Suara], ...]

    @property
    def bersuara(self) -> int:
        """Agen yang mengambil arah.  Netral tidak termasuk."""
        return self.setuju + self.menentang

    @property
    def kontradiksi(self) -> float | None:
        """0 = bulat, 1 = terbelah rata.  ``None`` = tidak ada yang bersuara.

        Diukur hanya di antara yang BERSUARA - lihat docstring modul.
        """
        if self.bersuara == 0:
            return None
        minoritas = min(self.setuju, self.menentang)
        return 2 * minoritas / self.bersuara


def rekap(deliberation: Deliberation, arah: Decision) -> RekapSuara:
    """Rekap sikap seluruh agen ronde satu terhadap ``arah``."""
    rincian = tuple((o.role, suara_terhadap(o, arah)) for o in deliberation.opinions)
    hitung = [s for _role, s in rincian]
    return RekapSuara(
        setuju=hitung.count(Suara.AGREE),
        menentang=hitung.count(Suara.DISAGREE),
        netral=hitung.count(Suara.NEUTRAL),
        rincian=rincian,
    )


def ke_keputusan_xau(decision: Decision) -> Decision:
    """Kosakata dewan → kosakata XAU.  ``WAIT`` tidak pernah lolos dari sini."""
    return Decision.NO_SIGNAL if decision is Decision.WAIT else decision


__all__ = ["RekapSuara", "Suara", "ke_keputusan_xau", "rekap", "suara_terhadap"]
