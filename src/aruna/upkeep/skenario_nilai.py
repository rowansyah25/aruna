"""Menilai skenario terhadap pasar yang sudah bergerak (bagian 16.19).

**Berkas ini ada karena bagian 16.19 sebelumnya tidak pernah berjalan.**
`aruna.scenario.evaluasi` ditulis, diuji, dan diekspor - lalu tidak dipanggil
siapa pun. Begitu juga `belum_dinilai`, `catat_hasil`, dan `ringkas_akurasi` di
repositorinya. Skenario tersimpan dengan ``hasil`` NULL selamanya, dan angka
akurasi yang seluruh pasalnya tuntut tidak pernah ada.

Itu cacat yang sama yang sudah tiga kali muncul di proyek ini:
`AdaptiveLearningService` yang hanya berjalan lewat perintah manual, pembersih
retensi yang lengkap dan tidak pernah menyapu, penilai PASAL 15.44 yang
menghitung putusan yang tidak pernah ditulis. Semuanya lulus test unitnya.

**Bagaimana sebuah skenario dinilai.** Bukan dengan membaca kalimat
invalidasinya - kalimat tidak bisa diperiksa mesin. Yang diperiksa **bentuk
jalan harganya**: candle sesudah skenario lahir diubah menjadi jejak dalam
satuan ATR, lalu diklasifikasikan dengan
:func:`~aruna.scenario.kerumunan.klasifikasi_jejak` - fungsi yang **sama persis**
dengan yang melahirkan keluarga skenarionya. Klasifikator yang berbeda antara
menghasilkan dan menilai membuat angkanya mengukur sesuatu yang lain.

**Jendelanya dua belas bar 15m.** Pemindai bekerja pada 15m
(:class:`~aruna.scanner.service.ScannerService`) dan mesin kerumunan berjalan
dua belas ronde (:data:`~aruna.scenario.kerumunan.RONDE`), jadi tiga jam adalah
horizon yang sama yang disimulasikan - bukan angka yang dipilih terpisah.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aruna.core.enums import Horizon, Market
from aruna.core.logging import get_logger
from aruna.scenario.evaluasi import (
    MINIMUM_DINILAI,
    MINIMUM_TITIK,
    nilai_dari_pasar,
)
from aruna.scenario.models import HasilSkenario, Invalidasi, Skenario

log = get_logger(__name__)

__all__ = [
    "BAR_HORIZON",
    "BATAS_PER_SAPUAN",
    "INTERVAL_NILAI",
    "PenilaiSkenario",
]

#: Interval candle yang dipakai menilai. Sama dengan yang dipindai.
INTERVAL_NILAI = Horizon.M15

#: Berapa bar sesudah skenario lahir yang membentuk horizonnya.
#:
#: Dua belas, sama dengan :data:`~aruna.scenario.kerumunan.RONDE`. Bukan angka
#: terpisah: yang dinilai harus horizon yang benar-benar disimulasikan, dan
#: menilai tiga jam atas simulasi dua belas ronde 15m adalah pertanyaan yang
#: sama diajukan dua kali dengan jawaban yang berbeda.
BAR_HORIZON = 12

#: Berapa skenario yang dinilai dalam satu sapuan.
#:
#: Tiap satu butuh satu pembacaan candle, jadi ini yang menahan sapuan pertama -
#: yang menemukan seluruh tunggakan sekaligus - dari menahan siklus. Sisanya
#: terambil sapuan berikutnya; tunggakan menyusut, tidak menumpuk.
BATAS_PER_SAPUAN = 40

#: Berapa bar sebelum skenario lahir yang dipakai menghitung ATR.
_BAR_ATR = 20


class PenilaiSkenario:
    """Menilai skenario yang horizonnya sudah lewat, lalu menuliskannya."""

    def __init__(self, *, repo: Any, market_data: Any, universe: Any) -> None:
        self._repo = repo
        self._market = market_data
        self._universe = universe
        #: Simbol yang sudah dicari id-nya. Satu sapuan menilai sampai empat
        #: puluh skenario dari segelintir simbol yang sama; mencarinya berulang
        #: adalah kueri yang jawabannya tidak berubah.
        self._id_aset: dict[tuple[str, str], Any] = {}

    async def nilai(self, *, now: datetime) -> dict[str, int]:
        """Satu sapuan. Tidak pernah melempar.

        Bagian 16.19 menghasilkan **catatan**, bukan keputusan. Kegagalannya
        tidak boleh menjatuhkan siklus yang menghasilkan keputusan sungguhan.
        """
        batas_waktu = now - timedelta(
            minutes=15 * (BAR_HORIZON + 1)
        )
        hitung = {"diperiksa": 0, "dinilai": 0, "belum": 0, "gagal": 0}

        try:
            baris = await self._repo.belum_dinilai(
                sampai=batas_waktu, limit=BATAS_PER_SAPUAN
            )
        except Exception:
            log.exception("skenario.antrean_gagal")
            hitung["gagal"] += 1
            return hitung

        for r in baris:
            hitung["diperiksa"] += 1
            try:
                putusan = await self._satu(r, now=now)
            except Exception:
                log.exception("skenario.nilai_gagal", scenario_id=r.get("scenario_id"))
                hitung["gagal"] += 1
                continue

            if putusan is None or putusan.hasil is HasilSkenario.BELUM:
                hitung["belum"] += 1
                continue

            try:
                ditulis = await self._repo.catat_hasil(
                    r["scenario_id"],
                    putusan.hasil,
                    pada=now,
                    diinvalidasi=putusan.diinvalidasi,
                )
            except Exception:
                log.exception("skenario.tulis_gagal", scenario_id=r["scenario_id"])
                hitung["gagal"] += 1
                continue

            if ditulis:
                hitung["dinilai"] += 1

        # Dicatat tiap sapuan, termasuk yang nol. Nol karena tidak ada yang
        # jatuh tempo dan nol karena fasenya tidak pernah dipanggil terlihat
        # sama dari luar - dan yang pertama normal sementara yang kedua persis
        # bug yang berkas ini ada untuk memperbaikinya.
        log.info("upkeep.skenario_nilai", **hitung)
        if hitung["dinilai"]:
            await self._laporkan()
        return hitung

    async def _laporkan(self) -> None:
        """Ukuran yang berarti, ke log operator (bagian 16.19).

        **Tanpa ini seluruh penilaian berhenti di basis data.** Bagian 16.19
        menutup dengan "Gunakan untuk evaluasi", dan angka yang tidak sampai ke
        siapa pun tidak dipakai siapa pun - `ringkas_akurasi` sudah ada sejak
        awal dan tidak pernah punya satu pun pemanggil.

        Yang dilaporkan **bukan** pangsa skenario yang BENAR. Tiap simulasi
        menghasilkan beberapa skenario dan hanya satu keluarga yang terjadi,
        jadi angka itu dibatasi ``1/N`` dan terlihat seperti mutu tanpa menjadi
        mutu - terukur 2026-08-22: 22,9% dengan batas atas struktural 33,3%.
        """
        try:
            ringkas = await self._repo.ringkas_per_simulasi()
        except Exception:
            log.exception("skenario.ringkas_gagal")
            return

        for r in ringkas:
            n = int(r["simulasi"] or 0)
            if not n:
                continue
            cukup = n >= MINIMUM_DINILAI
            log.info(
                "upkeep.skenario_akurasi",
                versi=r["versi_simulasi"],
                simulasi=n,
                # Keluarga yang benar-benar terjadi ADA di antara skenarionya.
                # Menguji kosakata mesin.
                cakupan=_bagian(r["cakupan"], n, cukup),
                # Skenario BERBOBOT TERTINGGI yang ternyata benar. Menguji
                # pembobotannya - dan pembandingnya tebakan acak.
                teratas=_bagian(r["teratas"], n, cukup),
                minimum=MINIMUM_DINILAI,
                cukup_sampel=cukup,
            )

        await self._laporkan_peringatan()

    async def _laporkan_peringatan(self) -> None:
        """Dari yang SALAH, berapa yang sempat memperingatkan (bagian 16.19).

        Dilaporkan **terpisah** dari akurasi, bukan dilipat ke dalamnya. Satu
        angka yang menjumlahkan "salah dan memperingatkan" dengan "salah dan
        diam" akan membaik ketika skenario berhenti menyebutkan syarat batalnya
        - persis arah yang salah.
        """
        try:
            baris = await self._repo.ringkas_peringatan()
        except Exception:
            log.exception("skenario.peringatan_gagal")
            return

        for r in baris:
            salah = int(r["salah"] or 0)
            if not salah:
                continue
            diperiksa = salah - int(r["tak_terperiksa"] or 0)
            log.info(
                "upkeep.skenario_peringatan",
                versi=r["versi_simulasi"],
                salah=salah,
                # Salah, TAPI syarat batalnya terpicu: mesinnya bekerja.
                memperingatkan=_bagian(r["memperingatkan"], diperiksa, True),
                # Salah TANPA peringatan: meleset, dan invalidasinya sia-sia.
                diam=_bagian(r["diam"], diperiksa, True),
                # Baris lama, dinilai kode yang belum memeriksanya sama sekali.
                tak_terperiksa=int(r["tak_terperiksa"] or 0),
            )



    async def _satu(self, baris: dict[str, Any], *, now: datetime):
        """Putusan untuk satu baris, atau ``None`` kalau candle-nya tak cukup."""
        lahir = baris["dibuat_pada"]
        jejak = await self._jejak(
            baris["asset"], baris.get("market_code", ""), lahir=lahir
        )
        if jejak is None:
            return None

        return nilai_dari_pasar(
            _skenario_dari(baris),
            jejak=jejak,
            horizon_selesai=True,
        )

    async def _jejak(
        self, asset: str, market: str, *, lahir: datetime
    ) -> tuple[float, ...] | None:
        """Jalan harga sesudah skenario lahir, dalam satuan ATR.

        ATR dihitung dari bar **sebelum** skenarionya lahir. Memakai bar
        sesudahnya berarti menormalkan gerakan dengan ukuran yang gerakan itu
        sendiri ikut membentuk - lintasan yang meledak akan terlihat tenang
        karena penyebutnya ikut meledak.
        """
        aset_id = await self._aset_id(asset, market)
        if aset_id is None:
            return None

        # Jendela di sekitar KELAHIRANNYA, bukan bar terbaru.
        #
        # Terukur 2026-08-22: mengambil tiga puluh enam bar terbaru untuk
        # skenario berumur tiga belas jam menghasilkan jendela yang mulai empat
        # jam **sesudah** skenarionya lahir, dan empat puluh dari empat puluh
        # dilaporkan belum bisa dinilai. Tunggakan lama tidak akan pernah
        # terkuras dengan cara itu.
        lebar = timedelta(minutes=15)
        bar = await self._market.candles_between(
            aset_id,
            INTERVAL_NILAI,
            mulai=lahir - lebar * (_BAR_ATR + 2),
            sampai=lahir + lebar * (BAR_HORIZON + 2),
        )
        if not bar:
            return None

        sebelum = [b for b in bar if b.get("close_time") and b["close_time"] <= lahir]
        sesudah = [b for b in bar if b.get("close_time") and b["close_time"] > lahir]
        if len(sebelum) < 2 or len(sesudah) < MINIMUM_TITIK - 1:
            return None

        atr = _atr(sebelum[-_BAR_ATR:])
        if not atr:
            return None

        dasar = _angka(sebelum[-1].get("close"))
        if dasar is None:
            return None

        jejak = [0.0]
        for b in sesudah[:BAR_HORIZON]:
            tutup = _angka(b.get("close"))
            if tutup is None:
                continue
            jejak.append((tutup - dasar) / atr)
        return tuple(jejak)

    async def _aset_id(self, asset: str, market: str) -> Any:
        kunci = (market, asset)
        if kunci in self._id_aset:
            return self._id_aset[kunci]

        try:
            pasar = Market(market)
        except ValueError:
            self._id_aset[kunci] = None
            return None

        catatan = await self._universe.find(pasar, asset)
        # Disimpan walau ``None``: aset yang sudah dinonaktifkan tidak akan
        # muncul lagi, dan mencarinya ulang tiap sapuan adalah kueri yang
        # jawabannya sudah diketahui.
        self._id_aset[kunci] = getattr(catatan, "id", None)
        return self._id_aset[kunci]


def _bagian(nilai: Any, total: int, cukup: bool) -> str:
    """Pecahan apa adanya, dan persennya hanya kalau sampelnya cukup.

    Pecahannya selalu ditulis supaya "7/17" tidak pernah terbaca sebagai "41%"
    oleh mata yang buru-buru.

    **Penyebut nol adalah keadaan yang sah di sini, bukan kesalahan pemanggil.**
    Terjadi di produksi 2026-08-23, satu menit sesudah kolom `diinvalidasi`
    dipasang: seluruh 928 baris SALAH yang sudah ada dinilai oleh kode yang
    belum memeriksanya, jadi semuanya NULL dan yang bisa diperiksa berjumlah
    nol. Dijaga di sini, bukan di pemanggilnya - penjaga yang menempel pada satu
    pemanggil membiarkan pemanggil berikutnya menulis ulang bug yang sama.
    """
    n = int(nilai or 0)
    if total <= 0:
        return f"{n}/0 (belum ada)"
    if not cukup:
        return f"{n}/{total} (ditahan)"
    return f"{n}/{total} = {n / total:.1%}"


def _angka(nilai: Any) -> float | None:
    try:
        keluar = float(nilai)
    except (TypeError, ValueError):
        return None
    return keluar if keluar == keluar and abs(keluar) != float("inf") else None


def _atr(bar: list[dict[str, Any]]) -> float | None:
    """Rentang sejati rata-rata. ``None`` kalau tidak bisa dihitung.

    ``None`` dan bukan angka bawaan: ATR yang dikarang membuat tiap jejak
    terskala salah, dan jejak yang terskala salah diklasifikasikan salah tanpa
    satu pun tanda.
    """
    rentang: list[float] = []
    sebelumnya: float | None = None
    for b in bar:
        tinggi, rendah = _angka(b.get("high")), _angka(b.get("low"))
        if tinggi is None or rendah is None:
            continue
        span = [tinggi - rendah]
        if sebelumnya is not None:
            span.append(abs(tinggi - sebelumnya))
            span.append(abs(rendah - sebelumnya))
        rentang.append(max(span))
        sebelumnya = _angka(b.get("close"))
    if not rentang:
        return None
    rata = sum(rentang) / len(rentang)
    return rata if rata > 0 else None


def _skenario_dari(baris: dict[str, Any]) -> Skenario:
    """Bentuk minimal yang dibutuhkan penilai, dari baris database.

    Hanya `scenario_id` dan `nama` yang benar-benar dipakai
    :func:`~aruna.scenario.evaluasi.nilai_dari_pasar`; sisanya diisi seadanya
    supaya `Skenario` bisa dibentuk. Invalidasinya tidak boleh kosong -
    `Skenario` menolaknya, dan penolakan itu yang menjaga bagian 16.11 tetap
    berlaku.
    """
    return Skenario(
        scenario_id=baris["scenario_id"],
        market=baris.get("market_code", "UNKNOWN"),
        asset=baris["asset"],
        timestamp=baris["dibuat_pada"],
        nama=baris["nama"],
        deskripsi="",
        kondisi_awal=(),
        pemicu="",
        perkembangan=(),
        invalidasi=Invalidasi(syarat=("dibaca dari basis data",)),
        risiko="UNKNOWN",
        keyakinan=0.0,
        bobot=int(baris.get("bobot") or 0),
        bukti=(),
        versi_simulasi=baris.get("versi_simulasi", "UNKNOWN"),
    )
