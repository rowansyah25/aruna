"""Pengirim yang tidak bisa mengirim harus mengaku, bukan menjawab "siap".

**Cacat yang ditemukan 2026-08-25 dari layar operator, bukan dari suite.**

`ArunaApplication._start_telegram` membuat `TelegramBot` SEBELUM memanggil
`start()`, dan `start()` pada instalasi tanpa token mencatat `telegram.disabled`
lalu langsung `return` tanpa menyalakan `_started`. Jadi `self.bot` selalu bukan
`None`, dan `_LateSender.ready()` - yang cuma menguji `is not None` - selalu
menjawab ya.

Akibatnya penjaga di `DailyReportService.due` dan `ResearchDigestService.due`
tidak pernah menyala. Keduanya ditulis PERSIS untuk mencegah gejala ini;
komentar yang pertama bahkan menyebut "peringatan itu tiap lima belas detik
selamanya". Terukur atas 29 jam log:

* `daily.undelivered` 550 kali, `daily.sent` NOL;
* `research.undelivered` 550 kali, `research.sent` NOL;
* `build()` yang mendahului pengiriman memakan p50 26,1 detik per siklus -
  4 jam 3 menit total, 45% dari seluruh waktu siklus upkeep.

Waktu itulah yang membuat siklus upkeep p50 64 detik, yang membuat candle 1m
tidak mungkin dijaga dalam toleransi 3 menit, dan yang membuat penjaga
"loop macet" berteriak. Satu cacat, tiga gejala.

Keluarga cacatnya sama dengan `test_app_coroutine_ditunggu`: kode yang benar,
ada di tempatnya, dan tidak pernah berjalan - karena satu pertanyaan di
perakitan dijawab dengan "ada objeknya" ketika yang ditanya "bisa mengirim".
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aruna.app import _LateSender
from aruna.notify.daily import CouncilScore, MarketBlock, SelfCorrection, Tally
from aruna.notify.daily_service import DailyReportService

# 00:03 WIB tanggal 18 = 17:03 UTC tanggal 17 - sudah lewat tengah malam WIB,
# jadi laporannya memang jatuh tempo kalau ada tujuannya.
LEWAT_TENGAH_MALAM = datetime(2026, 8, 17, 17, 3, tzinfo=UTC)


class TestLateSenderMenjawabKemampuanBukanKeberadaan:
    def test_tanpa_bot_tidak_siap(self) -> None:
        assert _LateSender(lambda: None).ready() is False

    def test_bot_yang_belum_start_tidak_siap(self) -> None:
        """**Ini bentuk yang sebenarnya ada di produksi.** Objeknya dibangun,
        `start()` menemui instalasi tanpa token dan pulang lebih awal."""
        mati = SimpleNamespace(started=False)

        assert _LateSender(lambda: mati).ready() is False

    def test_bot_yang_sudah_start_siap(self) -> None:
        """Pasangannya, supaya test di atas tidak bisa lulus dengan
        membungkam semuanya."""
        hidup = SimpleNamespace(started=True)

        assert _LateSender(lambda: hidup).ready() is True

    def test_yang_tidak_bisa_ditanya_dianggap_tidak_siap(self) -> None:
        """Untuk pertanyaan KEMAMPUAN, "tidak bisa tahu" harus berarti "jangan
        klaim bisa".

        Bawaan sebelumnya kebalikannya, dan justru bawaan itu yang dipakai
        produksi setiap lima belas detik.
        """
        entah = SimpleNamespace()

        assert _LateSender(lambda: entah).ready() is False


class _Repo:
    """Repositori yang MENGHITUNG - biayanya yang jadi soal, bukan isinya."""

    def __init__(self) -> None:
        self.dibangun = 0

    async def futures(self, *, start, end):
        self.dibangun += 1
        return MarketBlock("FUTURES / PERPETUAL", "🔮", Tally(total=1, win=1))

    async def agents(self):
        return ()

    async def council(self, *, start, end):
        return CouncilScore()

    async def correction(self, *, start, end, model_version):
        return SelfCorrection(model_version=model_version)


class _Pengirim:
    def __init__(self, *, siap: bool) -> None:
        self._siap = siap
        self.dikirim: list[str] = []

    def ready(self) -> bool:
        return self._siap

    async def send(self, text: str) -> bool:
        self.dikirim.append(text)
        # Tujuan yang tidak siap tidak pernah berhasil menerima.
        return self._siap


class TestLaporanTidakDibangunTanpaTujuan:
    """Yang mahal bukan `send` yang gagal - itu instan. Yang mahal `build()`
    yang mendahuluinya, dan ia dibayar penuh sebelum kegagalan diketahui."""

    @pytest.mark.asyncio
    async def test_tanpa_tujuan_database_tidak_disentuh(self) -> None:
        repo = _Repo()
        layanan = DailyReportService(
            repo=repo, sender=_Pengirim(siap=False)
        )  # type: ignore[arg-type]

        terkirim = await layanan.run(LEWAT_TENGAH_MALAM)

        assert terkirim is False
        assert repo.dibangun == 0, (
            "laporan dibangun untuk tujuan yang tidak ada - inilah 26 detik "
            "per siklus yang terukur di produksi"
        )

    @pytest.mark.asyncio
    async def test_dengan_tujuan_laporannya_tetap_berangkat(self) -> None:
        """Pasangannya. Tanpa ini, perbaikan di atas bisa "lulus" dengan
        mematikan laporan harian sepenuhnya."""
        repo = _Repo()
        pengirim = _Pengirim(siap=True)
        layanan = DailyReportService(
            repo=repo, sender=pengirim
        )  # type: ignore[arg-type]

        terkirim = await layanan.run(LEWAT_TENGAH_MALAM)

        assert terkirim is True
        assert repo.dibangun == 1
        assert pengirim.dikirim

    @pytest.mark.asyncio
    async def test_diulang_tiap_siklus_tetap_tidak_membangun(self) -> None:
        """Satu panggilan tidak menunjukkan pengulangan.

        Biayanya baru terlihat sebagai biaya kalau ia berulang - dan justru
        pengulangan itulah yang memakan 4 jam.
        """
        repo = _Repo()
        layanan = DailyReportService(
            repo=repo, sender=_Pengirim(siap=False)
        )  # type: ignore[arg-type]

        for _ in range(5):
            await layanan.run(LEWAT_TENGAH_MALAM)

        assert repo.dibangun == 0
