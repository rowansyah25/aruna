"""Fase manfaat tidak boleh membekukan event loop (akar `stream.silent`).

**Lahir dari penelusuran produksi, 2026-08-22.** Gejalanya terlihat seperti
masalah jaringan: `stream.silent - connected but no message; reconnecting`,
lima belas kali dalam lima jam, disertai `stream:binance-spot: DOWN` dan
"tidak satu pun dari 20 simbol mengirim kutipan dalam 151 detik terakhir".

Buktinya justru menunjuk ke dalam. Jeda dari `stream.connected` ke
`stream.silent` selalu 146-218 detik padahal ambangnya 90 detik - timer
`asyncio` yang menyala terlambat berarti loop-nya tidak terjadwal. Dan log
memperlihatkannya telanjang:

    -138.1s  upkeep.retensi
    -  0.0s  manfaat.dinilai      <- stempel yang sama persis
    -  0.0s  stream.silent        <- stempel yang sama persis

Tidak ada satu baris log pun selama 138 detik di antaranya.

Terukur pada korpus produksi: `nilai_satu` untuk 15m memakan **154,1 detik**
atas 2.567 ingatan, dan tugas detak yang seharusnya berdenyut tiap 50 milidetik
diam **154,2 detik**. Sesudah dipindah ke thread: jeda terbesar **0,29 detik**,
durasi total praktis tak berubah (214,6 -> 211,9 detik).

Yang diuji di sini bukan kecepatannya - fase ini memang lama dan tidak apa-apa.
Yang diuji: selama ia berjalan, loop-nya **tetap bernapas**.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from aruna.memory.evaluasi import Evaluasi
from aruna.upkeep.manfaat import PenilaiManfaat

NOW = datetime(2026, 8, 22, tzinfo=UTC)

#: Jauh lebih pendek dari 154 detik sungguhan, dan itu gunanya seam `penilai`:
#: klaim "tidak memblokir" tidak bisa dibuktikan kalau satu-satunya cara
#: menjalankannya adalah dengan korpus penuh.
BEBAN_DETIK = 0.6

#: Denyut yang seharusnya terjadi selama beban di atas, kalau loop hidup.
#: Longgar dengan sengaja - yang diuji hidup atau mati, bukan presisi jadwal.
DENYUT_MINIMUM = 5


class _MemoryPalsu:
    def __init__(self, timeframes: tuple[str, ...]) -> None:
        self._tf = timeframes

    async def ingatan_berarah(self, *, timeframe, as_of, limit):
        return [object()] if timeframe in self._tf else []


class _StatePalsu:
    def __init__(self) -> None:
        self.ditulis: list[tuple[str, str]] = []

    async def set(self, kunci, nilai, *, actor):
        self.ditulis.append((kunci, actor))


def _penilai_lambat(ingatan):
    """CPU-bound, persis seperti `nilai_satu` yang sungguhan.

    `time.sleep` **tidak** dipakai: ia melepas GIL, jadi loop akan tetap
    bernapas walau kodenya dipanggil langsung - dan test-nya lulus atas bug
    yang seharusnya ditangkapnya. Yang dipakai putaran yang benar-benar
    membakar CPU.
    """
    batas = time.monotonic() + BEBAN_DETIK
    x = 0
    while time.monotonic() < batas:
        x += 1
    return Evaluasi(mendukung_menang=0, mendukung_kalah=0, melawan_menang=0,
                    melawan_kalah=0), x


def _bakar(detik: float, *, napas: bool) -> int:
    """Bakar CPU dengan Python murni, dengan atau tanpa melepas GIL berkala.

    Meniru bentuk `nilai_satu`: putaran ketat tanpa I/O. `napas` adalah
    satu-satunya perbedaan antara kedua test di :class:`TestMelepasGil`.
    """
    batas = time.monotonic() + detik
    n = 0
    while time.monotonic() < batas:
        n += 1
        if napas and n % 2000 == 0:
            time.sleep(0)
    return n


async def _detak(berhenti: asyncio.Event) -> tuple[int, float]:
    """Berapa kali loop terjadwal, dan jeda terlamanya."""
    denyut = 0
    terlama = 0.0
    sebelum = time.monotonic()
    while not berhenti.is_set():
        await asyncio.sleep(0.02)
        kini = time.monotonic()
        terlama = max(terlama, kini - sebelum)
        sebelum = kini
        denyut += 1
    return denyut, terlama


def _penilai(timeframes=("15m",), fungsi=None) -> PenilaiManfaat:
    return PenilaiManfaat(
        memory=_MemoryPalsu(timeframes),
        app_state=_StatePalsu(),
        timeframes=timeframes,
        penilai=fungsi or _penilai_lambat,
    )


@pytest.mark.asyncio
class TestLoopTetapBernapas:
    async def test_denyut_berlanjut_selama_penilaian(self) -> None:
        """Inti seluruh perbaikan. Kalau `nilai_satu` dipanggil langsung,
        denyutnya berhenti sama sekali selama beban berjalan."""
        berhenti = asyncio.Event()
        detak = asyncio.create_task(_detak(berhenti))

        await _penilai().nilai(now=NOW)

        berhenti.set()
        denyut, terlama = await detak

        assert denyut >= DENYUT_MINIMUM, f"loop cuma terjadwal {denyut} kali"
        assert terlama < BEBAN_DETIK, f"loop beku {terlama:.2f}s dari {BEBAN_DETIK}s"

    async def test_beberapa_timeframe_tidak_menumpuk_blokade(self) -> None:
        """Empat timeframe berurutan adalah empat blokade berurutan pada versi
        lama - itu yang membuat totalnya 214 detik di produksi."""
        berhenti = asyncio.Event()
        detak = asyncio.create_task(_detak(berhenti))

        await _penilai(timeframes=("15m", "1h", "4h")).nilai(now=NOW)

        berhenti.set()
        _, terlama = await detak

        assert terlama < BEBAN_DETIK

    async def test_hasilnya_tetap_benar(self) -> None:
        """Pindah ke thread tidak boleh mengubah apa yang dihitung - kalau
        berubah, yang diperbaiki bukan blokadenya melainkan gejalanya."""
        hasil = await _penilai().nilai(now=NOW)

        assert "15m" in hasil
        assert hasil["15m"].timeframe == "15m"

    async def test_putusannya_tetap_ditulis(self) -> None:
        """Jalur simpannya lewat `await` sesudah thread selesai; kalau
        urutannya kacau, putusannya tidak pernah sampai ke `app_state` dan
        gerbang per timeframe berhenti bekerja tanpa satu pun galat."""
        state = _StatePalsu()
        p = PenilaiManfaat(
            memory=_MemoryPalsu(("15m",)),
            app_state=state,
            timeframes=("15m",),
            penilai=_penilai_lambat,
        )

        await p.nilai(now=NOW)

        assert state.ditulis
        assert state.ditulis[0][1] == "upkeep.manfaat"


@pytest.mark.asyncio
class TestBentukPenjaganya:
    async def test_beban_ujinya_benar_benar_membakar_cpu(self) -> None:
        """Penjaga yang bebannya memakai `time.sleep` akan lulus atas bug yang
        seharusnya ditangkapnya - `sleep` melepas GIL. Ini memastikan bebannya
        memang menahan loop kalau dipanggil langsung."""
        berhenti = asyncio.Event()
        detak = asyncio.create_task(_detak(berhenti))
        await asyncio.sleep(0.05)

        _penilai_lambat([])  # dipanggil LANGSUNG, bukan lewat thread

        berhenti.set()
        _, terlama = await detak

        assert terlama >= BEBAN_DETIK * 0.5, (
            "beban uji tidak memblokir loop; penjaganya tidak menguji apa pun"
        )


@pytest.mark.asyncio
class TestMelepasGil:
    """Thread saja tidak cukup, dan itu kesalahanku sendiri yang setengah jalan.

    Memindahkan sapuan ke `to_thread` menghapus seluruh lima belas
    `stream.silent` - lalu memunculkan empat `keepalive ping timeout` yang
    sebelumnya **nol**. Python murni memegang GIL terus-menerus, jadi event
    loop hanya mendapat sisa jadwal; pustaka `websockets` menunggu pong dalam
    dua puluh detik dan menutup koneksi sendiri saat terlewat.

    Terukur sesudah `time.sleep(0)` berkala, di bawah kontensi menyerupai
    produksi: jeda loop terbesar **0,11 detik**, biaya durasi 3%.
    """

    async def test_thread_tanpa_napas_menahan_loop(self) -> None:
        """Setengah dari perbandingannya: begini rasanya tanpa pelepasan GIL.

        Kalau test ini mulai hijau tanpa perubahan kode, berarti asumsi GIL-nya
        tidak lagi berlaku di runtime ini - dan pasangannya di bawah berhenti
        membuktikan apa pun."""
        berhenti = asyncio.Event()
        detak = asyncio.create_task(_detak(berhenti))
        await asyncio.sleep(0.05)

        await asyncio.to_thread(_bakar, BEBAN_DETIK, napas=False)

        berhenti.set()
        _, terlama = await detak

        assert terlama > 0.05, (
            "loop tidak tertahan sama sekali; asumsi kontensi GIL tidak berlaku"
        )

    async def test_napas_berkala_membebaskan_loop(self) -> None:
        """Beban yang sama, hanya dengan `time.sleep(0)` berkala."""
        berhenti = asyncio.Event()
        detak = asyncio.create_task(_detak(berhenti))
        await asyncio.sleep(0.05)

        await asyncio.to_thread(_bakar, BEBAN_DETIK, napas=True)

        berhenti.set()
        denyut, terlama = await detak

        assert denyut >= DENYUT_MINIMUM
        assert terlama < 0.25, f"loop tertahan {terlama:.2f}s walau ada napas"


class TestBentuknyaTerkunci:
    """Kelas terpisah karena testnya sinkron - `@pytest.mark.asyncio` pada
    fungsi biasa diterima diam-diam lalu diperingatkan, dan peringatan yang
    menumpuk berhenti dibaca."""

    def test_memakai_to_thread_bukan_panggilan_langsung(self) -> None:
        """Penjaga AST. Perilakunya sudah diuji di atas; ini menahan
        seseorang menukarnya kembali menjadi panggilan langsung karena
        "lebih sederhana" - dan test perilaku di atas berjalan pada beban
        0,6 detik, yang cukup pendek untuk lolos di mesin cepat."""
        import ast
        import inspect

        from aruna.upkeep import manfaat

        pohon = ast.parse(inspect.getsource(manfaat))
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "to_thread" in dipanggil

    def test_nilai_satu_melepas_gil_berkala(self) -> None:
        """Penjaga AST atas `nilai_satu` sendiri. Mekanismenya sudah diuji di
        `TestMelepasGil`; ini yang memastikan `nilai_satu` benar-benar
        memakainya - bukan cuma bahwa ia bisa bekerja."""
        import ast
        import inspect

        from aruna.upkeep.manfaat import NAPAS_TIAP, nilai_satu

        pohon = ast.parse(inspect.getsource(nilai_satu).lstrip())
        panggil = {
            f"{n.func.value.id}.{n.func.attr}"
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        }

        assert "time.sleep" in panggil
        assert 0 < NAPAS_TIAP <= 500
