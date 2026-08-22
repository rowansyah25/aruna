"""Batas MiroFish: apa yang ia harus jawab, dan apa yang ia tidak boleh lakukan.

**MiroFish tidak ada.** Dicari 2026-08-22 di seluruh repo: tidak ada pustaka
terpasang, tidak ada ``MIROFISH_*`` di konfigurasi, tidak ada dokumen
antarmukanya - dan tidak ada kemampuan LLM apa pun di ARUNA. Setiap agent di
sistem ini deterministik dan berbasis aturan.

Adapter ke antarmuka yang belum pernah dilihat adalah karangan. Jadi yang ada
di sini **batasnya**: satu ``Protocol`` yang mengeja pertanyaan yang MiroFish
harus sanggup jawab, dan jalur DEGRADED yang sudah bekerja tanpa dia. Ketika
mesinnya tersedia, implementasinya masuk tanpa menyentuh apa pun di sekitarnya.

**Bagian 16.16 dieja di sini karena di sinilah ia akan dilanggar.** Sebuah
berkas bernama "adapter ke mesin simulasi" adalah tempat paling wajar bagi
seseorang menambahkan ``execute()`` tanpa merasa melanggar apa pun. MiroFish
**TIDAK**: vote LONG, vote SHORT, execute trades, override Risk, override
Quality, override Master Orchestrator, execute Binance orders. Ia **HANYA**:
SIMULATE, COMPARE, REPORT, PROVIDE SCENARIO EVIDENCE.

**Bagian 16.12 dan 16.13.** MiroFish yang gagal atau kehabisan waktu tidak
menghentikan ARUNA - ia menjadi ``DEGRADED``, dan mesin skenario internal tetap
berjalan. Hasil yang telat **dibuang, bukan dipakai**: skenario yang tiba
sesudah keadaannya berubah adalah keterangan tentang pasar yang sudah lewat.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from aruna.scenario.models import Skenario

log = structlog.get_logger(__name__)

__all__ = [
    "TIMEOUT_DETIK",
    "HasilAdapter",
    "ScenarioEngineInterface",
    "StatusSimulasi",
    "coba_simulasi",
]


#: Bagian 16.13. Simulasi yang lebih lama dari ini dianggap kehabisan waktu.
#:
#: Tiga puluh detik: horizon terpendek ARUNA adalah lima belas menit, dan
#: simulasi yang memakan lebih dari sepertiga menit sudah menggambarkan pasar
#: yang berbeda dari yang memicunya.
TIMEOUT_DETIK = 30.0


class StatusSimulasi(StrEnum):
    """Keadaan satu percobaan simulasi.

    ``DEGRADED`` dan ``TIMEOUT`` dibedakan dengan sengaja: yang pertama berarti
    mesinnya tidak ada, yang kedua berarti ia ada dan terlalu lambat. Keduanya
    menghasilkan nol skenario eksternal, dan menyatukannya membuat "MiroFish
    belum dipasang" tidak bisa dibedakan dari "MiroFish sedang bermasalah".
    """

    OK = "OK"
    DEGRADED = "DEGRADED"
    TIMEOUT = "TIMEOUT"
    GAGAL = "GAGAL"


@dataclass(frozen=True, slots=True)
class HasilAdapter:
    """Apa yang kembali dari satu percobaan, termasuk saat tidak ada apa-apa."""

    status: StatusSimulasi
    skenario: tuple[Skenario, ...] = ()
    catatan: str = ""

    @property
    def terpakai(self) -> bool:
        """Hanya ``OK`` yang boleh dipakai.

        Bagian 16.13 mengejanya: jangan menggunakan hasil yang sudah stale.
        Hasil sebagian dari simulasi yang kehabisan waktu terlihat seperti
        bukti dan bukan bukti.
        """
        return self.status is StatusSimulasi.OK


@runtime_checkable
class ScenarioEngineInterface(Protocol):
    """Apa yang mesin simulasi eksternal harus sanggup jawab.

    Satu metode, dan sengaja hanya satu: tiap metode tambahan adalah permukaan
    yang harus diperiksa lagi terhadap bagian 16.16 saat mesinnya diisi.
    """

    async def simulasikan(
        self, *, pertanyaan: str, masukan: dict[str, object]
    ) -> tuple[Skenario, ...]:
        """Kemungkinan-kemungkinan perkembangan, berikut cara membantahnya.

        Memulangkan tuple kosong ketika tidak ada yang bisa dikatakan - bukan
        satu skenario bernama "tidak tahu" berbobot 100, yang akan terhitung
        sebagai keyakinan penuh oleh siapa pun yang menjumlahkannya.
        """
        ...


async def coba_simulasi(
    mesin: ScenarioEngineInterface | None,
    *,
    pertanyaan: str,
    masukan: dict[str, object],
    timeout: float = TIMEOUT_DETIK,
) -> HasilAdapter:
    """Satu percobaan ke mesin eksternal. **Tidak pernah melempar.**

    Ini seluruh isi bagian 16.12 dalam satu kalimat: MiroFish yang gagal tidak
    boleh menghentikan ARUNA. Fungsi ini memulangkan :class:`HasilAdapter`
    untuk tiap keadaan - tidak ada, lambat, meledak - dan pemanggilnya
    melanjutkan dengan mesin internal tanpa perlu membungkusnya dengan ``try``.

    Menangkap :class:`BaseException` dan bukan :class:`Exception`, kecuali
    :class:`asyncio.CancelledError`: mesin pihak ketiga yang belum pernah
    dilihat bisa melempar apa saja, termasuk yang tidak turun dari ``Exception``,
    dan satu lemparan yang lolos di sini menjatuhkan siklus yang seluruh pasal
    ini ada untuk melindunginya. Pembatalan diteruskan karena pembatalan bukan
    kegagalan mesin - ia perintah untuk berhenti, dan menelannya membuat
    penghentian ARUNA menggantung.
    """
    if mesin is None:
        return HasilAdapter(
            status=StatusSimulasi.DEGRADED,
            catatan="mesin simulasi eksternal tidak terpasang",
        )

    try:
        skenario = await asyncio.wait_for(
            mesin.simulasikan(pertanyaan=pertanyaan, masukan=masukan),
            timeout=timeout,
        )
    except TimeoutError:
        # Bagian 16.13: hasil yang telat DIBUANG, bukan dipakai. `wait_for`
        # sudah membatalkan tugasnya, jadi tidak ada hasil sebagian yang bisa
        # tergoda dipungut - dan itu yang diinginkan: skenario yang tiba
        # sesudah keadaannya berubah adalah keterangan tentang pasar yang lewat.
        log.warning("scenario.simulation_timeout", timeout=timeout)
        return HasilAdapter(
            status=StatusSimulasi.TIMEOUT,
            catatan=f"SIMULATION TIMEOUT setelah {timeout:.0f} detik",
        )
    except asyncio.CancelledError:
        raise
    except BaseException as galat:  # noqa: BLE001 - lihat docstring
        log.warning("scenario.simulation_failed", galat=repr(galat))
        return HasilAdapter(
            status=StatusSimulasi.GAGAL,
            catatan=f"simulasi gagal: {galat!r}",
        )

    return HasilAdapter(status=StatusSimulasi.OK, skenario=tuple(skenario))
