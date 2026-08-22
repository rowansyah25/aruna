"""Menilai apakah ingatan benar-benar membantu, per timeframe (PASAL 15.44).

Simulasi historis, bukan pengamatan langsung. PASAL 15.44 meminta perbandingan
keputusan **dengan** memory melawan **tanpa** memory; memory baru mulai
mempengaruhi keputusan pada 2026-08-21, jadi belum ada satu pun hasil yang bisa
diatribusikan kepadanya. Yang dilakukan di sini adalah apa yang PASAL 15.40
justru wajibkan: untuk tiap keputusan lama, hitung konteks yang **waktu itu**
tersedia, lalu bandingkan nasibnya.

**Disiplin ``as_of`` di sini bukan kehati-hatian umum.** Tanpanya evaluasi ini
akan selalu melaporkan bahwa memory sangat membantu, dan angkanya akan naik
justru ketika kebocorannya makin parah. Yang menahannya: ingatan hanya masuk
kumpulan "tersedia" ketika ``resolved_at``-nya sudah lewat sebelum keputusan
yang sedang dinilai dikunci.

**Ongkosnya nyata.** Perbandingan kemiripan berjalan O(n^2) atas ribuan
ingatan, karena itu fase ini harian dan berbatas - bukan per keputusan, dan
bukan per tick.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from aruna.core.logging import get_logger
from aruna.memory.evaluasi import evaluasi_pengaruh
from aruna.memory.manfaat import KUNCI_STATE, Manfaat, dari_json, ke_json

log = get_logger(__name__)

__all__ = ["MINIMUM_TERSEDIA", "TIMEFRAME_DINILAI", "PenilaiManfaat", "nilai_satu"]


#: Timeframe yang dinilai. Yang tidak disebut di sini tidak pernah punya
#: putusan, dan tanpa putusan gerbangnya tidak menutup - ingatan berperilaku
#: seperti sebelum gerbang ada.
TIMEFRAME_DINILAI = ("15m", "1h", "4h", "1d")

#: Berapa ingatan harus sudah tersedia sebelum sebuah keputusan lama layak
#: dinilai.
#:
#: Dua ratus. Keputusan paling awal di korpus hanya punya segelintir ingatan
#: sebelumnya, dan konteks yang disusun dari sepuluh kasus mengukur keberuntungan
#: - lalu keberuntungan itu masuk ke angka yang menentukan apakah seluruh mesin
#: ingatan dipakai.
MINIMUM_TERSEDIA = 200

#: Tiap berapa target GIL dilepas sejenak supaya event loop dapat jendela.
#:
#: Lima puluh: pada korpus 15m yang 2.567 ingatan, itu sekitar lima puluh
#: jendela sepanjang sapuan seratus lima puluh empat detik - satu tiap tiga
#: detik. Ambang keepalive `websockets` dua puluh detik, jadi jaraknya punya
#: margin hampir tujuh kali lipat.
#:
#: Angkanya bisa dinaikkan sampai jarak antar-jendela mendekati dua puluh
#: detik; di bawah itu ia cuma menambah panggilan tanpa menambah keamanan.
NAPAS_TIAP = 50

#: Lama tiap napas, detik. **Bukan nol, dan itu penting di Windows.**
#:
#: `time.sleep(0)` memetakan ke `Sleep(0)`, yang hanya menyerahkan giliran ke
#: thread berprioritas sama yang KEBETULAN sudah siap - ia tidak menjamin thread
#: utama benar-benar dijadwalkan. Terukur di produksi 2026-08-22: dengan napas
#: nol, tujuh `keepalive ping timeout` tetap terjadi, seluruhnya di dalam
#: jendela manfaat.
#:
#: Satu milidetik adalah tidur sungguhan: penjadwal OS wajib memberi thread lain
#: kesempatan. Biayanya lima puluh satu milidetik sepanjang sapuan seratus lima
#: puluh empat detik - sepertiga per mil.
NAPAS_DETIK = 0.001


def nilai_satu(
    ingatan: list[Any], *, minimum_tersedia: int = MINIMUM_TERSEDIA
) -> tuple[Any, int]:
    """Evaluasi PASAL 15.44 untuk satu timeframe, dan berapa keputusan dinilai.

    ``ingatan`` harus urut ``locked_at`` menaik dan hanya berisi yang berarah
    (BUY/SELL) dengan hasil final - keputusan tanpa arah tidak punya sisi untuk
    didukung atau dilawan.

    Murni: tidak ada satu pun kueri di sini, supaya angkanya bisa diuji tanpa
    basis data.
    """
    from aruna.memory.context import susun
    from aruna.memory.outcome import ringkas
    from aruna.memory.similarity import AMBANG_MIRIP, bandingkan

    urut_resolusi = sorted(ingatan, key=lambda i: i.resolved_at)
    tersedia: list[Any] = []
    #: Sejajar dengan `tersedia`: tiap ingatan berikut perbandingannya dengan
    #: dirinya sendiri. Tumbuh bersamanya, tidak pernah dihitung ulang.
    dasar_cocok: list[tuple[Any, Any]] = []
    j = 0
    pasangan: list[tuple[Any, Any]] = []

    for nomor, target in enumerate(ingatan):
        # **Lepaskan GIL sejenak.** Fungsi ini dijalankan lewat `to_thread`, dan
        # thread saja tidak cukup: Python murni memegang GIL terus-menerus, jadi
        # event loop hanya mendapat sisa jadwal alih-alih jendela yang pasti.
        #
        # Terukur di produksi 2026-08-22, dan ini kesalahanku sendiri yang
        # setengah jalan: memindahkan sapuan ini ke thread menghapus seluruh
        # lima belas `stream.silent`, lalu memunculkan empat
        # `keepalive ping timeout` yang sebelumnya nol. Pustaka `websockets`
        # menunggu pong dalam dua puluh detik; kontensi GIL menundanya lewat
        # batas itu dan pustakanya menutup koneksi sendiri.
        #
        # Lamanya :data:`NAPAS_DETIK` dan **bukan nol** - lihat catatan di
        # konstanta itu. Napas nol sempat dicoba dan tidak cukup: tujuh
        # `keepalive ping timeout` tetap terjadi.
        if nomor % NAPAS_TIAP == 0:
            time.sleep(NAPAS_DETIK)

        # Sapuan bertahap yang tidak pernah melihat ke depan: hanya ingatan
        # yang resolusinya SUDAH terjadi sebelum keputusan ini dikunci.
        while (
            j < len(urut_resolusi)
            and urut_resolusi[j].resolved_at < target.locked_at
        ):
            masuk = urut_resolusi[j]
            tersedia.append(masuk)
            # Perbandingan sebuah ingatan DENGAN DIRINYA SENDIRI, dihitung
            # sekali saat ia masuk - bukan sekali per target.
            #
            # Versi sebelumnya menulis
            # `ringkas([(i, bandingkan(i.sidik, i.sidik)) for i in tersedia])`
            # di dalam putaran target. Nilainya tidak bergantung pada `target`
            # sama sekali, jadi seluruh daftar dihitung ulang tiap iterasi -
            # separuh dari seluruh panggilan `bandingkan` adalah pengulangan
            # yang jawabannya sudah diketahui.
            #
            # Terprofil pada 900 ingatan: 739.098 panggilan `bandingkan`,
            # 99,6% dari seluruh waktu. Kira-kira separuhnya dari baris itu.
            dasar_cocok.append((masuk, bandingkan(masuk.sidik, masuk.sidik)))
            j += 1
        if len(tersedia) < minimum_tersedia:
            continue

        cocok = [
            (lain, m)
            for lain in tersedia
            if (m := bandingkan(target.sidik, lain.sidik)).skor >= AMBANG_MIRIP
        ]
        if not cocok:
            continue

        dasar = ringkas(dasar_cocok)
        # **Tanpa** `manfaat` di sini, dan itu bukan kelalaian: yang diukur
        # adalah pengaruh yang mesin ini hasilkan, dan mengoper putusan lama
        # ke dalam pengukurannya sendiri akan mengunci jawabannya pada
        # jawaban kemarin - gerbang yang menutup lalu tidak pernah bisa
        # membuka lagi karena buktinya berhenti dikumpulkan.
        konteks = susun(
            arah_sekarang=target.arah, cocok=cocok, dasar=dasar,
            as_of=target.locked_at,
        )
        pasangan.append((konteks.pengaruh, target.hasil))

    return evaluasi_pengaruh(pasangan), len(pasangan)


class PenilaiManfaat:
    """Menghitung putusan tiap timeframe dan menyimpannya di ``app_state``."""

    def __init__(
        self,
        *,
        memory: Any,
        app_state: Any,
        timeframes: tuple[str, ...] = TIMEFRAME_DINILAI,
        batas: int = 4000,
        penilai: Any = None,
    ) -> None:
        self._memory = memory
        self._state = app_state
        self._timeframes = timeframes
        #: Fungsi penilai, disuntikkan supaya perilaku "tidak memblokir loop"
        #: bisa diuji tanpa menunggu dua setengah menit sungguhan.
        #:
        #: Seam ini lahir dari bug produksi, bukan dari kenyamanan: klaim bahwa
        #: sebuah fase tidak memblokir event loop tidak bisa dibuktikan kalau
        #: satu-satunya cara menjalankannya adalah dengan korpus penuh.
        self._penilai = penilai or nilai_satu
        #: Ingatan terbaru yang ikut dinilai per timeframe. Berbatas karena
        #: perbandingannya kuadratik; yang dipotong dicatat, supaya "dinilai
        #: dari seluruh korpus" tidak pernah diklaim tanpa benar.
        self._batas = batas

    async def terakhir_dinilai(self) -> datetime | None:
        """Kapan penilaian terakhir benar-benar terjadi, dari ``app_state``.

        **Jawabannya sudah tersimpan sejak dulu** - tiap :class:`Manfaat`
        membawa ``dinilai_pada`` dan seluruhnya ditulis ke ``app_state`` di
        akhir :meth:`nilai`. Yang tidak ada cuma pembacanya, dan tanpa pembaca
        itu ``UpkeepStats.last_manfaat_at`` mulai dari ``None`` tiap kali proses
        menyala.

        Akibatnya terukur 2026-08-22: sapuan yang seharusnya sehari sekali
        berjalan lagi di siklus pertama **tiap restart**. Pada hari dengan
        belasan restart, sapuan harian dibayar belasan kali - dan tiap satu
        menahan siklus pertama selama satu setengah menit, saat seluruh horizon
        jatuh tempo bersamaan.

        Memulangkan ``None`` untuk keadaan yang berbeda-beda - belum pernah
        dinilai, state-nya rusak, basis datanya tak terbaca - dan ketiganya
        memang menuntut hal yang sama: nilai sekarang. Yang tidak boleh adalah
        **melempar**: penilaian ini bukan syarat ARUNA berjalan.
        """
        try:
            mentah = await self._state.get(KUNCI_STATE)
        except Exception as galat:  # noqa: BLE001 - lihat docstring
            log.warning("manfaat.state_tak_terbaca", galat=repr(galat))
            return None

        return max(
            (m.dinilai_pada for m in dari_json(mentah).values()), default=None
        )

    async def nilai(self, *, now: datetime) -> dict[str, Manfaat]:
        hasil: dict[str, Manfaat] = {}
        for tf in self._timeframes:
            ingatan = await self._memory.ingatan_berarah(
                timeframe=tf, as_of=now, limit=self._batas
            )
            if not ingatan:
                log.info("manfaat.kosong", timeframe=tf)
                continue
            # **Di thread, bukan di event loop.** Terukur 2026-08-22 pada
            # korpus produksi: `nilai_satu` untuk 15m memakan **154 detik** atas
            # 2.567 ingatan, dan selama itu event loop tidak terlayani sama
            # sekali - tugas detak yang seharusnya berdenyut tiap 50 milidetik
            # diam 154,2 detik.
            #
            # Akibatnya bukan cuma lambat. `ws.recv()` di aliran Binance
            # dibungkus `asyncio.wait_for(..., 90s)`; timer itu kedaluwarsa di
            # tengah blokade dan menyala begitu loop bernapas lagi, sehingga
            # aliran yang sehat dilaporkan `stream.silent` dan disambung ulang.
            # Lima belas kali dalam lima jam. Pemeriksaan kesehatan, ingest,
            # dan setiap fase lain sama butanya selama jendela itu.
            #
            # GIL tidak membatalkan perbaikan ini: Python melepas GIL tiap
            # `sys.setswitchinterval()` (5 ms bawaan), jadi loop terjadwal
            # teratur alih-alih tidak sama sekali. Yang hilang sebagian
            # kecepatan; yang didapat mata ARUNA tetap terbuka.
            evaluasi, dinilai = await asyncio.to_thread(self._penilai, ingatan)
            m = Manfaat(
                timeframe=tf,
                evaluasi=evaluasi,
                dinilai_pada=now,
                dinilai_dari=dinilai,
            )
            hasil[tf] = m
            log.info(
                "manfaat.dinilai",
                timeframe=tf,
                ingatan=len(ingatan),
                keputusan_dinilai=dinilai,
                selisih=evaluasi.selisih,
                dipakai=m.dipakai,
                ringkas=evaluasi.ringkas(),
            )

        if hasil:
            await self._state.set(
                KUNCI_STATE, ke_json(hasil), actor="upkeep.manfaat"
            )
        return hasil
