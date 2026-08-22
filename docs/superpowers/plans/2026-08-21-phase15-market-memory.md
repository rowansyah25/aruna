# Phase 15 — Market Memory & Context Engine

> **For agentic workers:** REQUIRED SUB-SKILL: gunakan `superpowers:executing-plans`
> untuk menjalankan rencana ini tugas per tugas. Langkahnya memakai checkbox
> (`- [ ]`). **Subagent-driven-development TIDAK dipakai** — operator melarang
> orkestrasi multi-agent di proyek ini (boros token; kerjakan langsung).

**Goal:** Memberi ARUNA kemampuan menjawab satu pertanyaan — *"apakah aku pernah
melihat kondisi seperti ini, dan apa yang terjadi waktu itu?"* — sebagai bukti
tambahan untuk Phase 14, bukan sebagai pengambil keputusan.

**Architecture:** Ingatan **tidak menyimpan ulang** apa pun. Ia proyeksi
append-only dari `signal_snapshots` + `outcome_snapshots` yang sudah berisi
8.914 keputusan beserta hasilnya. Di atasnya ada tiga hal yang belum ada:
sidik jari yang **bisa dibandingkan** (yang sekarang cuma hash), pencarian
kemiripan yang **tidak boleh melihat masa depan**, dan penyambungan ke
`CouncilNote` lewat jalur yang sudah terbukti di Phase 14.

**Tech Stack:** Python 3.13, dataclass beku + StrEnum, pytest + pytest-asyncio,
ruff, MySQL 8.4 lewat asyncmy, structlog.

## Global Constraints

Disalin apa adanya dari SPEC operator. Setiap tugas tunduk pada seluruh daftar
ini, bukan hanya yang disebut di tugasnya.

- **PASAL 15.1:** ARUNA tetap ANALYST ONLY. DILARANG BUY/SELL/LONG/SHORT
  otomatis, membuka atau menutup posisi, membuat atau membatalkan order,
  mengubah posisi user, trading lewat API. Tidak boleh ada satu pun jalur baru
  dari paket ini menuju eksekusi.
- **PASAL 15.42:** memory TIDAK BOLEH langsung mengubah keputusan. Phase 14
  yang menentukan keputusan final.
- **PASAL 15.48:** DILARANG mengatakan "karena kondisi ini pernah terjadi, maka
  harga pasti akan bergerak sama". Yang boleh: "ditemukan X kasus dengan
  similarity Y%; hasil historisnya menunjukkan pola Z".
- **PASAL 15.23:** DILARANG menyebut similarity sebagai probabilitas profit.
  "92% chance price will rise" terlarang.
- **PASAL 15.39 / 15.40:** memory TIDAK BOLEH memakai informasi dari masa depan
  saat menilai keputusan masa lalu. Setiap pencarian terikat `as_of`.
- **PASAL 15.25:** memory IMMUTABLE sesudah outcome final. DILARANG mengubah
  outcome, menghapus LOSS, mengubah signal lama, timestamp, agent vote, atau
  versi model. Koreksi = rekaman koreksi baru, bukan overwrite.
- **PASAL 15.27 / §26:** DILARANG menyimpan raw market data terus-menerus ke
  SQL — setiap polling, setiap REST request, duplicate candle, debug log.
- **PASAL 15.3 / §13.26:** kalau datanya tidak ada: `UNKNOWN`. DILARANG
  mengarang.
- **§51:** DILARANG mengatakan "100% WIN", "Pasti profit", "Pasti naik",
  "Pasti turun", "Leverage aman", "Pasti berhasil".
- **§33:** CRYPTO: USDT PAIRS ONLY.

### Aturan kerja proyek ini

- **Repo ini BUKAN git repository.** Tidak ada langkah commit. Penggantinya
  adalah **cabut-uji**: cabut barisnya, jalankan testnya, pastikan MERAH,
  kembalikan. Test yang tetap hijau saat kodenya dicabut tidak menguji apa pun.
- **`pytest` dijalankan SENDIRIAN.** Suite di latar plus probe di depan
  menghasilkan kegagalan palsu yang menyamar jadi bug.
- Python venv: `.\.venv\Scripts\python.exe`. PowerShell 5.1 — pakai `;`, bukan
  `&&`. **Jangan menulis berkas lewat `Set-Content`** — ia merusak `§`.
- Nama dan docstring test ditulis dalam bahasa Indonesia, mengikuti berkas yang
  sudah ada. Docstring menjelaskan **kenapa test ini ada**, bukan apa yang
  dilakukannya.
- Restart ARUNA: matikan `cmd.exe` yang menjalankan `ARUNA.bat` dengan
  `taskkill /PID <pid> /T /F`, lalu `Start-Process` lagi. Verifikasi sampai
  `health.transition status=UP`.

---

## Keadaan awal — diukur 2026-08-21, bukan diingat

Empat angka di bawah ini menentukan bentuk seluruh rencana. Semuanya dari
database produksi.

### Bahan ingatannya sudah ada, dan banyak

| tabel | baris | isinya |
|---|---|---|
| `signal_snapshots` | 8.914 | konteks tiap signal saat dibuat |
| `outcome_snapshots` | 39.356 | harga sesudahnya; 8.286 punya `is_final` |
| `futures_plans` | 5.263 | rencana futures |
| `futures_plan_results` | 178 | hasil rencana futures |
| `futures_ghost_results` | 2.773 | apa yang terjadi pada rencana yang tidak diambil |
| `council_sessions` | 5.621 | keputusan council beserta perselisihannya |
| `correlations` | 380 | korelasi pasangan (Phase 14 putaran keempat) |
| `discovered_patterns` | 367 | pola Phase 12 |

**Tidak ada tabel baru untuk menyimpan ulang semua ini.** PASAL 15.27 melarang,
dan sumbernya sudah immutable.

### Dimensinya sehat — yang ada

`signal_snapshots` (8.914 baris) terisi:

| kolom | terisi |
|---|---|
| `regime`, `risk_level`, `news_state`, `confidence`, `direction`, `model_version` | **100%** |
| `spread_bps` | 99,0% |
| `signal_quality`, `quality_coverage` | 95,3% |
| `expected_move_pct`, `target_price` | **38,1%** |

Sebarannya nyata, bukan satu nilai yang diulang: `regime` TRENDING 2.363 /
BREAKOUT 1.893 / UNCERTAIN 1.848 / RANGING 1.287 / REVERSAL 1.032 /
LOW_VOLATILITY 366. `horizon_code` 15m 5.379 / 1h 2.282 / 1d 1.253.

### Yang PASAL 15.5 minta tapi tidak pernah tersimpan

Volatility, volume, momentum, trend, open interest, funding, price structure —
**tidak satu pun punya kolom historis**. `risk_history` ada dan **kosong (0
baris)**. Artinya untuk seluruh 8.914 rekaman lama, ketujuh dimensi itu
`UNKNOWN` selamanya; tidak ada backfill yang bisa menghidupkannya, karena
datanya memang tidak pernah ditulis.

Konsekuensinya mengikat desain: **`UNKNOWN` tidak boleh dihitung sebagai
kecocokan.** Sidik jari yang membandingkan tujuh ketiadaan dengan tujuh
ketiadaan akan melaporkan 100% mirip terhadap dua kondisi yang tidak diketahui
sama sekali — dan itu angka meyakinkan yang tidak berdasar, persis yang §13.26
larang.

### Dan ini yang paling menentukan: **ingatannya baru lima hari**

`signal_snapshots` membentang **2026-08-15 08:23 sampai 2026-08-20 20:30**.
Seluruh 8.914 keputusan lahir di dalam satu jendela lima hari.

Yang berubah karenanya:

- **Recency weighting (15.11) dan memory decay (15.21) belum punya arti.**
  Tidak ada yang cukup tua untuk meluruh. Keduanya tetap dibangun — dengan
  parameter yang bisa dikonfigurasi — tapi rencana ini **tidak** akan mengklaim
  keduanya terbukti bekerja, karena tidak ada data yang bisa membuktikannya.
- **"Historical" di sini berarti "minggu ini".** Setiap keluaran yang dibaca
  manusia wajib menyebut rentang waktunya, supaya "126 kasus serupa" tidak
  terbaca sebagai pengalaman bertahun-tahun.
- **Ambang kecukupan sampel (15.9) bukan hiasan.** Ia akan sering berbunyi, dan
  itu benar.
- `direction` didominasi `WAIT` (5.267 dari 8.914). Yang berarah — BUY 2.758,
  SELL 636 — hanya 3.394, dan hanya sebagian punya outcome yang bisa dinilai.

### Sidik jari yang sudah ada tidak bisa dipakai

`signal_snapshots.fingerprint` **sudah ada** dan berisi SHA-256 64 karakter.
Gunanya immutability dan anti-duplikat: ia menjawab *"apakah ini signal yang
sama?"*. PASAL 15.5 menuntut yang menjawab *"apakah ini pasar yang mirip?"* —
dan sebuah hash tidak bisa: dua kondisi yang nyaris identik menghasilkan hash
yang sama sekali berbeda. Keduanya harus hidup berdampingan dengan nama yang
berbeda; menimpa yang lama akan merusak `verify()`.

---

## Struktur berkas

Paket baru `aruna.memory`, murni seperti `aruna.decision`: tanpa I/O, tanpa
database, tanpa jaringan. Yang menyentuh database hanya repositori, dan yang
menyambungkan hanya service yang sudah ada.

**Dibuat:**

- `src/aruna/memory/__init__.py` — ekspor paket.
- `src/aruna/memory/dimensions.py` — PASAL 15.3, 15.5. `Dimensi`, nilai
  `UNKNOWN`, dan pembanding tiap dimensi. Satu tanggung jawab: menyatakan
  dimensi apa yang membentuk sebuah kondisi pasar dan bagaimana dua nilai
  dibandingkan.
- `src/aruna/memory/fingerprint.py` — PASAL 15.4, 15.5. `Sidik` — bentuk
  kondisi pasar yang **bisa dibandingkan**, bukan hash.
- `src/aruna/memory/similarity.py` — PASAL 15.7, 15.8, 15.23. Skor 0–100 plus
  cakupan, dengan `UNKNOWN` di luar penyebut.
- `src/aruna/memory/record.py` — PASAL 15.3, 15.24, 15.25, 15.26. `Ingatan`
  (satu rekaman), `Mutu`, dan aturan kelayakannya.
- `src/aruna/memory/ranking.py` — PASAL 15.11, 15.21, 15.22. Urutan dan bobot:
  similarity, aset, timeframe, rezim, kebaruan, mutu, ukuran sampel.
- `src/aruna/memory/outcome.py` — PASAL 15.9, 15.10, 15.37. Ringkasan hasil
  historis, ambang kecukupan sampel, dan `NO SIGNIFICANT MATCH`.
- `src/aruna/memory/context.py` — PASAL 15.20, 15.30, 15.41, 15.42, 15.45.
  `KonteksHistoris`: hasil akhir yang dibaca Phase 14, beserta klasifikasi
  pengaruh dan jejak auditnya.
- `src/aruna/db/repositories/memory.py` — pembaca proyeksi, terikat `as_of`.
- `migrations/0031_market_memory.sql` — tabel proyeksi + indeks.
- `tests/test_memory_dimensions.py`
- `tests/test_memory_fingerprint.py`
- `tests/test_memory_similarity.py`
- `tests/test_memory_record.py`
- `tests/test_memory_ranking.py`
- `tests/test_memory_outcome.py`
- `tests/test_memory_context.py`
- `tests/test_memory_kebocoran.py` — penjaga PASAL 15.39/15.40, berdiri sendiri
  karena ia satu-satunya yang kegagalannya merusak seluruh nilai fase ini.
- `tests/test_memory_tersambung.py` — penjaga penyambungan.

**Diubah:**

- `src/aruna/futures/debate.py` — bidang `CouncilNote.memory`.
- `src/aruna/futures/service.py` — `attach_memory`, satu pencarian per simbol.
- `src/aruna/futures/notify.py` — blok `🧠 HISTORICAL CONTEXT` yang ringkas.
- `src/aruna/upkeep/loop.py` — proyektor ingatan, seperti `korelasi`.
- `src/aruna/app.py` — merangkai proyektor dan pembacanya.
- `src/aruna/notify/daily.py` — bagian `🧠 MARKET MEMORY` (PASAL 15.43).

---

## Task 1: Dimensi dan nilai yang tidak diketahui (PASAL 15.3, 15.5)

**Files:**
- Create: `src/aruna/memory/__init__.py`
- Create: `src/aruna/memory/dimensions.py`
- Create: `tests/test_memory_dimensions.py`

**Interfaces:**
- Consumes: tidak ada.
- Produces:
  - `UNKNOWN: str` — satu-satunya ejaan ketidaktahuan di paket ini.
  - `Dimensi(StrEnum)` — satu anggota per dimensi sidik jari.
  - `TERSIMPAN: frozenset[Dimensi]` — yang punya kolom historis.
  - `TAK_TERSIMPAN: frozenset[Dimensi]` — yang PASAL 15.5 minta dan tidak
    pernah ditulis ke database.
  - `diketahui(nilai: object) -> bool`
  - `sama(a: object, b: object) -> bool` — `UNKNOWN` tidak pernah sama dengan
    apa pun, termasuk `UNKNOWN` lain.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.3: kalau datanya tidak ada, UNKNOWN - dan UNKNOWN bukan kecocokan.

Terukur 2026-08-21: tujuh dari dimensi yang PASAL 15.5 sebut - volatility,
volume, momentum, trend, open interest, funding, price structure - tidak punya
satu pun kolom historis. `risk_history` ada dan kosong. Untuk seluruh 8.914
rekaman lama ketujuhnya UNKNOWN selamanya.

Kalau UNKNOWN dihitung sebagai kecocokan, sidik jari yang membandingkan tujuh
ketiadaan dengan tujuh ketiadaan melaporkan kemiripan sempurna terhadap dua
kondisi yang tidak diketahui sama sekali. Itu angka meyakinkan tanpa dasar,
dan §13.26 melarangnya. Berkas ini yang menahan pintu itu.
"""

from __future__ import annotations

from aruna.memory.dimensions import (
    TAK_TERSIMPAN,
    TERSIMPAN,
    UNKNOWN,
    Dimensi,
    diketahui,
    sama,
)


class TestKetidaktahuan:
    def test_unknown_tidak_pernah_sama_dengan_unknown(self) -> None:
        """Dua kondisi yang sama-sama tidak diketahui bukan dua kondisi yang
        mirip - mereka dua kondisi yang tidak ada yang tahu."""
        assert not sama(UNKNOWN, UNKNOWN)

    def test_unknown_tidak_sama_dengan_nilai_apa_pun(self) -> None:
        assert not sama(UNKNOWN, "TRENDING")
        assert not sama("TRENDING", UNKNOWN)

    def test_none_dan_kosong_dibaca_sebagai_tidak_diketahui(self) -> None:
        """Nol bukan ketiadaan - tapi None dan string kosong iya, dan keduanya
        yang benar-benar keluar dari kolom database yang NULL."""
        assert not diketahui(None)
        assert not diketahui("")
        assert not diketahui("   ")
        assert not diketahui(UNKNOWN)

    def test_nol_dihitung_diketahui(self) -> None:
        """`confidence=0` berarti council menilai dan hasilnya nol, bukan
        berarti confidence tidak terbaca. Kelas kesalahan yang sama dengan
        `side='FLAT'` yang truthy."""
        assert diketahui(0)
        assert diketahui(0.0)

    def test_nilai_yang_sama_cocok_tanpa_peduli_huruf(self) -> None:
        assert sama("trending", "TRENDING")


class TestDaftarDimensi:
    def test_tidak_ada_dimensi_yang_dua_kali(self) -> None:
        assert len(TERSIMPAN | TAK_TERSIMPAN) == len(Dimensi)

    def test_keduanya_tidak_beririsan(self) -> None:
        """Sebuah dimensi tidak bisa sekaligus tersimpan dan tidak tersimpan;
        kalau ia di dua daftar, tidak ada yang tahu apakah ia boleh dipakai
        menghitung kemiripan."""
        assert not (TERSIMPAN & TAK_TERSIMPAN)

    def test_yang_tersimpan_memang_yang_terukur_terisi(self) -> None:
        """Kelima ini terisi 95-100% pada 8.914 baris - itu sebabnya mereka
        boleh membentuk sidik jari."""
        for d in (Dimensi.REGIME, Dimensi.RISK_LEVEL, Dimensi.NEWS,
                  Dimensi.QUALITY, Dimensi.TIMEFRAME):
            assert d in TERSIMPAN

    def test_yang_tidak_tersimpan_disebut_namanya(self) -> None:
        """Didaftar, bukan dihilangkan: sebuah dimensi yang dihapus dari enum
        tidak pernah muncul sebagai UNKNOWN di laporan mana pun, dan
        ketiadaannya jadi tak terlihat."""
        for d in (Dimensi.VOLATILITY, Dimensi.VOLUME, Dimensi.MOMENTUM,
                  Dimensi.TREND, Dimensi.OPEN_INTEREST, Dimensi.FUNDING,
                  Dimensi.STRUCTURE):
            assert d in TAK_TERSIMPAN
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_memory_dimensions.py -q
```

Diharapkan: `ModuleNotFoundError: No module named 'aruna.memory'`.

- [ ] **Step 3: Tulis implementasinya**

```python
"""Dimensi yang membentuk sebuah kondisi pasar (PASAL 15.3, 15.5).

**UNKNOWN bukan nilai; ia ketiadaan nilai.** Dua kondisi yang sama-sama tidak
diketahui bukan dua kondisi yang mirip, dan modul ini menolak
memperlakukannya begitu - lihat :func:`sama`.

Daftarnya dipisah dua dengan sengaja. :data:`TERSIMPAN` adalah dimensi yang
punya kolom historis dan terukur terisi 95-100% pada 8.914 baris;
:data:`TAK_TERSIMPAN` adalah yang PASAL 15.5 sebut dan **tidak pernah ditulis
ke database sama sekali**. Yang kedua tetap didaftar, bukan dihapus: sebuah
dimensi yang hilang dari enum tidak akan pernah muncul sebagai UNKNOWN di
laporan mana pun, dan ketiadaannya berhenti terlihat.
"""

from __future__ import annotations

from enum import StrEnum

#: Satu-satunya ejaan ketidaktahuan di paket ini. Dua ejaan berarti dua jalur
#: yang harus tetap sepakat.
UNKNOWN = "UNKNOWN"


class Dimensi(StrEnum):
    """Nilainya data - jangan diterjemahkan."""

    ASSET = "ASSET"
    MARKET = "MARKET"
    TIMEFRAME = "TIMEFRAME"
    REGIME = "REGIME"
    RISK_LEVEL = "RISK_LEVEL"
    NEWS = "NEWS"
    QUALITY = "QUALITY"
    LIQUIDITY = "LIQUIDITY"
    # PASAL 15.5 menyebut ketujuh ini; tidak satu pun punya kolom historis.
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    MOMENTUM = "MOMENTUM"
    TREND = "TREND"
    OPEN_INTEREST = "OPEN_INTEREST"
    FUNDING = "FUNDING"
    STRUCTURE = "STRUCTURE"


#: Punya kolom historis, terukur terisi 95-100% pada 8.914 baris
#: `signal_snapshots`. `LIQUIDITY` diturunkan dari `spread_bps` (99,0%).
TERSIMPAN: frozenset[Dimensi] = frozenset({
    Dimensi.ASSET,
    Dimensi.MARKET,
    Dimensi.TIMEFRAME,
    Dimensi.REGIME,
    Dimensi.RISK_LEVEL,
    Dimensi.NEWS,
    Dimensi.QUALITY,
    Dimensi.LIQUIDITY,
})

#: Diminta PASAL 15.5, tidak pernah ditulis. UNKNOWN selamanya untuk rekaman
#: lama - tidak ada backfill yang bisa menghidupkannya.
TAK_TERSIMPAN: frozenset[Dimensi] = frozenset(Dimensi) - TERSIMPAN


def diketahui(nilai: object) -> bool:
    """Apakah nilai ini benar-benar terbaca.

    **Nol dihitung diketahui.** ``confidence=0`` berarti council menilai dan
    hasilnya nol, bukan berarti tidak terbaca - kelas kesalahan yang sama
    dengan ``side='FLAT'`` yang truthy.
    """
    if nilai is None:
        return False
    if isinstance(nilai, str):
        teks = nilai.strip()
        return bool(teks) and teks.upper() != UNKNOWN
    return True


def sama(a: object, b: object) -> bool:
    """Apakah dua nilai dimensi cocok. ``UNKNOWN`` tidak pernah cocok."""
    if not diketahui(a) or not diketahui(b):
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    return a == b


__all__ = [
    "TAK_TERSIMPAN",
    "TERSIMPAN",
    "UNKNOWN",
    "Dimensi",
    "diketahui",
    "sama",
]
```

`src/aruna/memory/__init__.py` diisi ekspor ulang yang sama, urut abjad.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_memory_dimensions.py -q
```

- [ ] **Step 5: Cabut-uji**

Ubah baris pertama `sama` menjadi `if False:`. Testnya harus MERAH pada
`test_unknown_tidak_pernah_sama_dengan_unknown` **dan**
`test_unknown_tidak_sama_dengan_nilai_apa_pun`. Kalau hanya satu yang merah,
testnya belum menguji kedua arah. Kembalikan.

---

## Task 2: Sidik jari yang bisa dibandingkan (PASAL 15.4, 15.5)

**Files:**
- Create: `src/aruna/memory/fingerprint.py`
- Create: `tests/test_memory_fingerprint.py`
- Modify: `src/aruna/memory/__init__.py`

**Interfaces:**
- Consumes: `Dimensi`, `UNKNOWN`, `diketahui` dari Task 1.
- Produces:
  - `Sidik` — dataclass beku, `nilai: Mapping[Dimensi, str]`.
  - `Sidik.dari_snapshot(row: Mapping[str, Any]) -> Sidik` — dari baris
    `signal_snapshots`.
  - `Sidik.dari_konteks(*, symbol, market, timeframe, regime, risk_level, news,
    quality, spread_bps) -> Sidik` — dari kondisi sekarang.
  - `Sidik.diketahui() -> frozenset[Dimensi]`
  - `band_kualitas(nilai: object) -> str` — `LOW` / `MEDIUM` / `HIGH` / `UNKNOWN`.
  - `band_likuiditas(spread_bps: object) -> str` — `TIGHT` / `NORMAL` / `WIDE`
    / `UNKNOWN`.

**Kenapa band, bukan angka.** `signal_quality` 0–100 dan `spread_bps` pecahan
tidak akan pernah sama persis antara dua kondisi. Kemiripan yang dihitung dari
kesamaan persis pada angka kontinu selalu nol; yang dari selisih butuh skala
yang dipilih seseorang. Band membuat perbandingannya jujur dan bisa dijelaskan:
"kualitas tinggi bertemu kualitas tinggi", bukan "57 vs 61 = 93% mirip".

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.5: sidik jari yang MEMBANDINGKAN, bukan yang mengunci.

`signal_snapshots.fingerprint` sudah ada dan berisi SHA-256 - dan ia menjawab
pertanyaan yang berbeda: "apakah ini signal yang sama?". Sebuah hash tidak bisa
menjawab "apakah ini pasar yang mirip", karena dua kondisi yang nyaris identik
menghasilkan hash yang sama sekali berbeda.

Keduanya harus hidup berdampingan. Berkas ini menjaga yang baru, dan menjaga
bahwa ia tidak menyentuh yang lama.
"""

from __future__ import annotations

from decimal import Decimal

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik, band_kualitas, band_likuiditas

BARIS = {
    "symbol": "XRP/USDT",
    "market_code": "CRYPTO",
    "horizon_code": "15m",
    "regime": "TRENDING",
    "risk_level": "MODERATE",
    "news_state": "1 item(s): 0+ / 0- / 1 unreadable",
    "signal_quality": 57,
    "spread_bps": Decimal("0.8091"),
}


class TestDariBarisDatabase:
    def test_dimensi_tersimpan_terbaca_semua(self) -> None:
        s = Sidik.dari_snapshot(BARIS)

        assert s.nilai[Dimensi.REGIME] == "TRENDING"
        assert s.nilai[Dimensi.ASSET] == "XRP/USDT"
        assert s.nilai[Dimensi.TIMEFRAME] == "15m"
        assert s.nilai[Dimensi.RISK_LEVEL] == "MODERATE"

    def test_yang_tidak_pernah_tersimpan_jadi_unknown(self) -> None:
        """Bukan dihilangkan: yang hilang dari sidik jari tidak akan pernah
        muncul sebagai ketiadaan di laporan mana pun."""
        s = Sidik.dari_snapshot(BARIS)

        for d in (Dimensi.VOLATILITY, Dimensi.MOMENTUM, Dimensi.FUNDING):
            assert s.nilai[d] == UNKNOWN

    def test_kolom_null_jadi_unknown_bukan_kosong(self) -> None:
        s = Sidik.dari_snapshot({**BARIS, "regime": None, "signal_quality": None})

        assert s.nilai[Dimensi.REGIME] == UNKNOWN
        assert s.nilai[Dimensi.QUALITY] == UNKNOWN

    def test_yang_diketahui_hanya_yang_benar_benar_terbaca(self) -> None:
        s = Sidik.dari_snapshot({**BARIS, "regime": None})

        assert Dimensi.REGIME not in s.diketahui()
        assert Dimensi.RISK_LEVEL in s.diketahui()
        assert Dimensi.VOLATILITY not in s.diketahui()


class TestBand:
    def test_kualitas_dipetakan_ke_band(self) -> None:
        assert band_kualitas(20) == "LOW"
        assert band_kualitas(57) == "MEDIUM"
        assert band_kualitas(85) == "HIGH"

    def test_kualitas_tak_terbaca_bukan_rendah(self) -> None:
        """Menganggap yang tidak terbaca sebagai LOW akan membuat setiap
        rekaman lama tanpa quality terlihat sebagai setup buruk - kesimpulan
        yang tidak pernah diukur siapa pun."""
        assert band_kualitas(None) == UNKNOWN

    def test_likuiditas_dari_spread(self) -> None:
        """PASAL 15.17: membedakan breakout pada likuiditas kuat dan lemah.
        `spread_bps` terisi 99,0% - satu-satunya ukuran likuiditas yang
        benar-benar ada di sejarah."""
        assert band_likuiditas(Decimal("0.8")) == "TIGHT"
        assert band_likuiditas(Decimal("12")) == "NORMAL"
        assert band_likuiditas(Decimal("80")) == "WIDE"
        assert band_likuiditas(None) == UNKNOWN


class TestTidakMenyentuhYangLama:
    def test_sidik_bukan_hash(self) -> None:
        """Kalau ini menghasilkan string 64 heksadesimal, seseorang menukarnya
        dengan `signal_snapshots.fingerprint` dan merusak `verify()`."""
        s = Sidik.dari_snapshot(BARIS)

        assert isinstance(s.nilai, dict) or hasattr(s.nilai, "keys")
        assert not isinstance(s.nilai, str)

    def test_dua_kondisi_mirip_punya_dimensi_yang_sama_banyak(self) -> None:
        """Yang tidak bisa dilakukan hash: kondisi yang beda tipis tetap
        terbaca sebagai beda tipis."""
        a = Sidik.dari_snapshot(BARIS)
        b = Sidik.dari_snapshot({**BARIS, "signal_quality": 58})

        cocok = [d for d in a.diketahui() & b.diketahui()
                 if a.nilai[d] == b.nilai[d]]

        assert len(cocok) >= 7
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_memory_fingerprint.py -q
```

- [ ] **Step 3: Tulis implementasinya**

Bentuknya: `@dataclass(frozen=True, slots=True) class Sidik` dengan satu bidang
`nilai: Mapping[Dimensi, str]`, dua classmethod pembangun, dan `diketahui()`
yang memulangkan `frozenset` dimensi yang lolos `dimensions.diketahui`.

Ambang band, dieja sebagai konstanta modul supaya bisa dicabut-uji:

```python
#: Ambang band kualitas. Dipilih terhadap sebaran terukur: `signal_quality`
#: 95,3% terisi, dan nilai tengahnya di sekitar lima puluhan.
QUALITY_LOW = 40
QUALITY_HIGH = 70

#: Ambang band likuiditas dalam basis point. `spread_bps` pada CRYPTO terukur
#: di bawah 1 bps untuk pasangan besar; puluhan bps berarti buku tipis.
SPREAD_TIGHT = 5
SPREAD_WIDE = 50
```

`news_state` disederhanakan menjadi `POSITIVE` / `NEGATIVE` / `NEUTRAL` /
`UNREADABLE` / `UNKNOWN` dengan membaca angka `0+ / 0- / 1 unreadable` yang
sudah jadi bentuk tersimpannya. Kalau formatnya tidak dikenali: `UNKNOWN`,
bukan `NEUTRAL` — netral adalah pembacaan, tidak terbaca bukan.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Ekspor dari paketnya**

`Sidik`, `band_kualitas`, `band_likuiditas`, `QUALITY_LOW`, `QUALITY_HIGH`,
`SPREAD_TIGHT`, `SPREAD_WIDE`.

- [ ] **Step 6: Cabut-uji**

Ganti pemetaan `TAK_TERSIMPAN` menjadi tidak diisi sama sekali (hapus barisnya
dari pembangun). `test_yang_tidak_pernah_tersimpan_jadi_unknown` harus MERAH.
Kembalikan.

---

## Task 3: Skor kemiripan dan cakupannya (PASAL 15.7, 15.8, 15.23)

**Files:**
- Create: `src/aruna/memory/similarity.py`
- Create: `tests/test_memory_similarity.py`
- Modify: `src/aruna/memory/__init__.py`

**Interfaces:**
- Consumes: `Sidik`, `Dimensi`, `sama` dari Task 1 dan 2.
- Produces:
  - `BOBOT: dict[Dimensi, int]` — bobot tiap dimensi.
  - `AMBANG_MIRIP: int = 80` — PASAL 15.8, configurable lewat argumen.
  - `Kemiripan` dataclass: `.skor` (0–100), `.cakupan` (0–100),
    `.cocok: tuple[Dimensi, ...]`, `.beda: tuple[Dimensi, ...]`,
    `.tak_terbaca: tuple[Dimensi, ...]`.
  - `bandingkan(a: Sidik, b: Sidik) -> Kemiripan`

**Aturan yang mengikat:** dimensi yang `UNKNOWN` di salah satu sisi **keluar
dari penyebut**, dan masuk ke `.tak_terbaca`. Skor 100 atas dua dimensi yang
terbaca bukan hal yang sama dengan skor 100 atas delapan — itu sebabnya
`.cakupan` dilaporkan terpisah dan tidak boleh dilebur ke `.skor`.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.7 dan 15.23: skor kemiripan, dan seberapa banyak yang terbaca.

Keduanya angka yang berbeda dan tidak boleh dilebur. Similarity 100% atas dua
dimensi yang terbaca dari delapan bukan hal yang sama dengan 100% atas
delapan-delapannya - yang pertama berarti "yang sedikit itu cocok", yang kedua
berarti "kondisinya memang mirip".

Melebur keduanya menghasilkan angka tinggi justru pada rekaman yang paling
sedikit datanya, dan itu keluarga cacat yang sama dengan kelengkapan integrasi
yang dulu terlihat penuh pada pemanggil yang paling sedikit melapor.
"""

from __future__ import annotations

import pytest

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.similarity import AMBANG_MIRIP, BOBOT, bandingkan


def _sidik(**ganti: str) -> Sidik:
    dasar = {
        Dimensi.ASSET: "BTC/USDT",
        Dimensi.MARKET: "CRYPTO",
        Dimensi.TIMEFRAME: "15m",
        Dimensi.REGIME: "TRENDING",
        Dimensi.RISK_LEVEL: "MODERATE",
        Dimensi.NEWS: "NEUTRAL",
        Dimensi.QUALITY: "MEDIUM",
        Dimensi.LIQUIDITY: "TIGHT",
    }
    dasar.update({Dimensi(k): v for k, v in ganti.items()})
    penuh = {d: UNKNOWN for d in Dimensi}
    penuh.update(dasar)
    return Sidik(nilai=penuh)


class TestSkornya:
    def test_identik_seratus(self) -> None:
        assert bandingkan(_sidik(), _sidik()).skor == 100

    def test_rezim_berbeda_menurunkan_skor(self) -> None:
        hasil = bandingkan(_sidik(), _sidik(REGIME="RANGING"))

        assert hasil.skor < 100
        assert Dimensi.REGIME in hasil.beda

    def test_skor_selalu_di_dalam_nol_seratus(self) -> None:
        semua_beda = _sidik(
            ASSET="ETH/USDT", MARKET="IDX", TIMEFRAME="1d", REGIME="RANGING",
            RISK_LEVEL="HIGH", NEWS="NEGATIVE", QUALITY="LOW", LIQUIDITY="WIDE",
        )
        hasil = bandingkan(_sidik(), semua_beda)

        assert 0 <= hasil.skor <= 100
        assert hasil.skor == 0


class TestCakupan:
    def test_yang_tak_terbaca_keluar_dari_penyebut(self) -> None:
        """Dua dimensi cocok dari dua yang terbaca tetap 100 - dan cakupannya
        yang memberitahu bahwa "dua" itu sedikit."""
        tipis = Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.ASSET: "BTC/USDT",
            Dimensi.REGIME: "TRENDING",
        })
        hasil = bandingkan(tipis, _sidik())

        assert hasil.skor == 100
        assert hasil.cakupan < 100

    def test_cakupan_penuh_saat_semua_tersimpan_terbaca(self) -> None:
        hasil = bandingkan(_sidik(), _sidik())

        assert hasil.cakupan == 100

    def test_tak_terbaca_disebut_namanya(self) -> None:
        hasil = bandingkan(_sidik(), _sidik())

        assert Dimensi.VOLATILITY in hasil.tak_terbaca

    def test_tanpa_satu_pun_dimensi_terbaca_skornya_nol(self) -> None:
        """Bukan seratus. Dua sidik jari kosong yang dibandingkan tanpa penjaga
        ini menghasilkan pembagian nol per nol, dan jawaban apa pun yang bukan
        nol berarti ARUNA mengaku mengenali kondisi yang tidak ia lihat."""
        kosong = Sidik(nilai={d: UNKNOWN for d in Dimensi})

        hasil = bandingkan(kosong, kosong)

        assert hasil.skor == 0
        assert hasil.cakupan == 0


class TestBobot:
    def test_setiap_dimensi_tersimpan_punya_bobot(self) -> None:
        from aruna.memory.dimensions import TERSIMPAN

        assert set(BOBOT) >= TERSIMPAN

    def test_bobotnya_positif(self) -> None:
        assert all(b > 0 for b in BOBOT.values())

    def test_ambangnya_delapan_puluh(self) -> None:
        """PASAL 15.8 memberi contohnya sendiri: Minimum Similarity 80%."""
        assert AMBANG_MIRIP == 80
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis implementasinya**

```python
BOBOT: dict[Dimensi, int] = {
    # Aset dan timeframe paling berat: PASAL 15.13 dan 15.14 menyatakan
    # keduanya punya kepribadian sendiri, jadi kemiripan lintas aset bernilai
    # jauh lebih kecil daripada kemiripan di aset yang sama.
    Dimensi.ASSET: 5,
    Dimensi.TIMEFRAME: 4,
    Dimensi.REGIME: 4,
    Dimensi.MARKET: 3,
    Dimensi.RISK_LEVEL: 2,
    Dimensi.QUALITY: 2,
    Dimensi.NEWS: 1,
    Dimensi.LIQUIDITY: 1,
}
```

`bandingkan` menjumlah bobot dimensi yang **terbaca di kedua sisi**; yang cocok
masuk pembilang, seluruhnya masuk penyebut. Penyebut nol menghasilkan
`Kemiripan(skor=0, cakupan=0, ...)` — bukan pengecualian dan bukan seratus.
`cakupan` adalah bobot terbaca dibagi total bobot `TERSIMPAN`, dalam persen.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Hapus penjaga penyebut nol (biarkan `ZeroDivisionError` atau ubah jadi 100).
`test_tanpa_satu_pun_dimensi_terbaca_skornya_nol` harus MERAH. Kembalikan.

---

## Task 4: Rekaman ingatan dan mutunya (PASAL 15.3, 15.24, 15.25, 15.26)

**Files:**
- Create: `src/aruna/memory/record.py`
- Create: `tests/test_memory_record.py`
- Modify: `src/aruna/memory/__init__.py`

**Interfaces:**
- Consumes: `Sidik` dari Task 2.
- Produces:
  - `Mutu(StrEnum)` — `HIGH`, `MEDIUM`, `LOW`.
  - `Hasil(StrEnum)` — `WIN`, `LOSS`, `NEUTRAL`, `UNKNOWN`.
  - `Ingatan` dataclass beku: `signal_id`, `sidik`, `arah`, `hasil`,
    `move_pct`, `locked_at`, `resolved_at`, `model_version`, `mutu`.
  - `mutu_dari(*, cakupan: int, hasil: Hasil, resolved_at, locked_at) -> Mutu`
  - `KUNCI_UNIK: tuple[str, ...]` — PASAL 15.26.

**Aturan:** `Ingatan` beku dan tidak punya satu pun setter. PASAL 15.25 melarang
mengubah outcome; membuat objeknya beku memindahkan larangan itu dari niat ke
tipe.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.25: ingatan IMMUTABLE sesudah hasilnya final.

Larangan yang hanya ditulis di dokumen akan dilanggar oleh kode yang tidak
membaca dokumen. Yang menahannya di sini adalah tipe: `Ingatan` beku, tanpa
setter, dan `dataclasses.replace` pun harus gagal untuk bidang hasilnya.

§11.21 sudah melarang menghapus LOSS dan mengubah signal lama. Ingatan yang
bisa disunting adalah jalan memutar untuk keduanya.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.memory.record import KUNCI_UNIK, Hasil, Ingatan, Mutu, mutu_dari


class TestTidakBisaDiubah:
    def test_hasilnya_tidak_bisa_ditulis_ulang(self, ingatan: Ingatan) -> None:
        with pytest.raises(Exception):
            ingatan.hasil = Hasil.WIN

    def test_waktunya_tidak_bisa_ditulis_ulang(self, ingatan: Ingatan) -> None:
        with pytest.raises(Exception):
            ingatan.locked_at = datetime(2020, 1, 1, tzinfo=UTC)


class TestMutu:
    def test_cakupan_penuh_dan_hasil_final_itu_tinggi(self) -> None:
        assert mutu_dari(
            cakupan=100, hasil=Hasil.WIN,
            locked_at=datetime(2026, 8, 20, tzinfo=UTC),
            resolved_at=datetime(2026, 8, 20, 1, tzinfo=UTC),
        ) is Mutu.HIGH

    def test_hasil_yang_belum_final_tidak_pernah_tinggi(self) -> None:
        """Ingatan tanpa hasil tidak bisa mengajari apa pun tentang hasil.
        Memberinya bobot tinggi berarti kemiripan dinilai dari kondisi saja,
        lalu dilaporkan seolah-olah hasilnya sudah diketahui."""
        assert mutu_dari(
            cakupan=100, hasil=Hasil.UNKNOWN,
            locked_at=datetime(2026, 8, 20, tzinfo=UTC), resolved_at=None,
        ) is Mutu.LOW

    def test_cakupan_tipis_menurunkan_mutu(self) -> None:
        assert mutu_dari(
            cakupan=30, hasil=Hasil.WIN,
            locked_at=datetime(2026, 8, 20, tzinfo=UTC),
            resolved_at=datetime(2026, 8, 20, 1, tzinfo=UTC),
        ) is not Mutu.HIGH


class TestAntiDuplikat:
    def test_kunci_uniknya_menyebut_signal_id(self) -> None:
        """PASAL 15.26: satu peristiwa satu ingatan. `signal_id` sudah unik di
        `signal_snapshots`, jadi tidak perlu kunci baru yang bisa berselisih
        dengan yang lama."""
        assert "signal_id" in KUNCI_UNIK
```

Tambahkan fixture `ingatan` di berkas testnya sendiri yang membangun `Ingatan`
lengkap dengan `Sidik` dari Task 2.

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis implementasinya**

`@dataclass(frozen=True, slots=True)`. `mutu_dari` memakai ambang konstanta
modul `CAKUPAN_TINGGI = 70` dan `CAKUPAN_RENDAH = 40`, dan memulangkan
`Mutu.LOW` tanpa syarat kalau `hasil is Hasil.UNKNOWN` atau `resolved_at is
None`.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Hapus `frozen=True`. `test_hasilnya_tidak_bisa_ditulis_ulang` harus MERAH.
Kembalikan.

---

## Task 5: Tabel proyeksi dan pembacanya yang terikat waktu (PASAL 15.39, 15.40)

**Files:**
- Create: `migrations/0031_market_memory.sql`
- Create: `src/aruna/db/repositories/memory.py`
- Create: `tests/test_memory_kebocoran.py`

**Interfaces:**
- Consumes: `Ingatan`, `Sidik`, `Dimensi`.
- Produces:
  - `MemoryRepository.simpan(ingatan: Ingatan) -> bool` — `INSERT IGNORE`.
  - `MemoryRepository.cari(*, as_of: datetime, market: str, timeframe: str,
    limit: int = 500) -> list[dict]` — **hanya** ingatan yang hasilnya sudah
    final **sebelum** `as_of`.
  - `MemoryRepository.proyeksikan(*, sampai: datetime, limit: int) -> int` —
    membangun ingatan baru dari `signal_snapshots` + `outcome_snapshots`.

**Tabelnya proyeksi, bukan salinan.** Kolomnya hanya sidik jari terurai
(delapan dimensi tersimpan, satu kolom masing-masing, `VARCHAR(24)`), plus
`signal_id`, `arah`, `hasil`, `move_pct`, `locked_at`, `resolved_at`,
`model_version`, `mutu`, `cakupan`. Tidak ada satu pun kolom harga mentah —
PASAL 15.27. Indeks pada `(market_code, timeframe, resolved_at)` karena setiap
pencarian menyaring ketiganya.

`signal_id` UNIQUE — PASAL 15.26 ditegakkan database, bukan niat.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.39: ingatan tidak boleh melihat masa depan.

Ini satu-satunya test di Phase 15 yang kegagalannya merusak seluruh nilai
fasenya. Sebuah memory engine yang boleh membaca hasil yang belum terjadi akan
melaporkan akurasi tinggi pada backtest mana pun, dan angkanya akan naik justru
ketika kebocorannya makin parah.

Yang dijaga: pencarian terikat `as_of`, dan ingatan yang resolusinya terjadi
SESUDAH `as_of` tidak boleh muncul - meskipun ia sudah ada di tabel saat
pencarian dijalankan hari ini.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


class _DBPalsu:
    """Meniru `Database.fetch`: mencatat SQL dan argumennya."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.sql = ""
        self.args: tuple = ()

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql = sql
        self.args = args
        return self.rows

    async def execute(self, sql: str, *args: Any) -> int:
        self.sql = sql
        self.args = args
        return 1


NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


class TestPencarianTerikatWaktu:
    @pytest.mark.asyncio
    async def test_kueri_menyaring_resolved_at(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).cari(
            as_of=NOW, market="CRYPTO", timeframe="15m"
        )

        assert "resolved_at < %s" in db.sql
        assert "resolved_at IS NOT NULL" in db.sql

    @pytest.mark.asyncio
    async def test_as_of_ikut_sebagai_argumen(self) -> None:
        """Kueri yang menyebut `resolved_at < %s` tapi tidak pernah mengoper
        `as_of` menyaring terhadap nilai lain - dan tetap lolos test yang cuma
        memeriksa teks SQL-nya."""
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).cari(
            as_of=NOW, market="CRYPTO", timeframe="15m"
        )

        assert any(str(NOW.date()) in str(a) for a in db.args)

    @pytest.mark.asyncio
    async def test_tanpa_as_of_ditolak(self) -> None:
        """Bawaan `as_of=None` yang berarti "sekarang" adalah bawaan yang akan
        dipakai pemanggil backtest tanpa sadar, dan itu kebocoran yang tidak
        meninggalkan jejak apa pun."""
        from aruna.db.repositories.memory import MemoryRepository

        with pytest.raises(TypeError):
            await MemoryRepository(_DBPalsu()).cari(
                market="CRYPTO", timeframe="15m"
            )


class TestProyeksiTidakMengarang:
    @pytest.mark.asyncio
    async def test_hanya_yang_punya_outcome_final(self) -> None:
        from aruna.db.repositories.memory import MemoryRepository

        db = _DBPalsu()
        await MemoryRepository(db).proyeksikan(sampai=NOW, limit=100)

        assert "is_final" in db.sql
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis migrasinya**

```sql
-- 0031_market_memory.sql - PASAL 15.2, 15.25, 15.26, 15.27
--
-- Proyeksi, bukan salinan: tiap baris lahir dari `signal_snapshots` +
-- `outcome_snapshots` yang keduanya sudah immutable. Tidak ada harga mentah di
-- sini, tidak ada candle, tidak ada polling - PASAL 15.27.
CREATE TABLE IF NOT EXISTS market_memories (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    signal_id      CHAR(16)     NOT NULL,
    market_code    VARCHAR(16)  NOT NULL,
    symbol         VARCHAR(32)  NOT NULL,
    timeframe      VARCHAR(8)   NOT NULL,
    regime         VARCHAR(24)  NOT NULL DEFAULT 'UNKNOWN',
    risk_level     VARCHAR(24)  NOT NULL DEFAULT 'UNKNOWN',
    news           VARCHAR(24)  NOT NULL DEFAULT 'UNKNOWN',
    quality_band   VARCHAR(24)  NOT NULL DEFAULT 'UNKNOWN',
    liquidity_band VARCHAR(24)  NOT NULL DEFAULT 'UNKNOWN',
    arah           VARCHAR(16)  NOT NULL,
    hasil          VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN',
    move_pct       DECIMAL(12,4) NULL,
    cakupan        TINYINT      NOT NULL DEFAULT 0,
    mutu           VARCHAR(8)   NOT NULL DEFAULT 'LOW',
    model_version  VARCHAR(64)  NOT NULL DEFAULT 'UNKNOWN',
    locked_at      DATETIME(6)  NOT NULL,
    resolved_at    DATETIME(6)  NULL,
    created_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_memory_signal (signal_id),
    KEY idx_memory_cari (market_code, timeframe, resolved_at),
    KEY idx_memory_regime (market_code, regime, resolved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: Tulis repositorinya**

`cari` wajib menerima `as_of` sebagai **keyword tanpa bawaan**. SQL-nya:

```sql
SELECT signal_id, symbol, timeframe, regime, risk_level, news,
       quality_band, liquidity_band, arah, hasil, move_pct,
       cakupan, mutu, locked_at, resolved_at
FROM market_memories
WHERE market_code = %s AND timeframe = %s
  AND resolved_at IS NOT NULL AND resolved_at < %s
ORDER BY resolved_at DESC
LIMIT %s
```

- [ ] **Step 5: Jalankan migrasinya, lalu testnya**

```bash
.\.venv\Scripts\python.exe -m aruna.cli migrate
.\.venv\Scripts\python.exe -m pytest tests/test_memory_kebocoran.py -q
```

- [ ] **Step 6: Cabut-uji**

Hapus `AND resolved_at < %s` dari SQL-nya. `test_kueri_menyaring_resolved_at`
harus MERAH. Lalu kembalikan, dan hapus **hanya** argumen `as_of` dari daftar
parameternya — `test_as_of_ikut_sebagai_argumen` harus MERAH. Dua cabutan,
karena dua cara berbeda kebocoran ini bisa lolos.

---

## Task 6: Hasil historis dan kecukupan sampel (PASAL 15.9, 15.10, 15.37)

**Files:**
- Create: `src/aruna/memory/outcome.py`
- Create: `tests/test_memory_outcome.py`

**Interfaces:**
- Consumes: `Ingatan`, `Hasil`, `Kemiripan`.
- Produces:
  - `SAMPEL_MINIMUM: int = 20`
  - `Ringkasan` dataclass: `.total`, `.per_arah: dict[str, int]`,
    `.win_rate: dict[str, int | None]`, `.rentang_similarity: tuple[int, int]`,
    `.rentang_waktu: tuple[datetime, datetime] | None`, `.cukup: bool`.
  - `ringkas(cocok: Sequence[tuple[Ingatan, Kemiripan]]) -> Ringkasan`
  - `KALIMAT_TIDAK_CUKUP: str = "INSUFFICIENT HISTORICAL SAMPLE"`
  - `KALIMAT_TIDAK_ADA: str = "NO SIGNIFICANT HISTORICAL MATCH"`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 15.9: sampel kecil bukan bukti.

Terukur 2026-08-21: seluruh ingatan ARUNA lahir dalam jendela LIMA HARI -
2026-08-15 sampai 2026-08-20. "126 kasus serupa" karena itu berarti "126 kali
minggu ini", bukan pengalaman bertahun-tahun, dan setiap keluaran yang dibaca
manusia wajib menyebut rentang waktunya.

Tanpa penjaga ini ARUNA akan menyimpulkan "win rate 84%" dari tiga kasus, dan
angka itu akan terdengar sama meyakinkannya dengan yang dari seribu.
"""

from __future__ import annotations

import pytest

from aruna.memory.outcome import (
    KALIMAT_TIDAK_ADA,
    KALIMAT_TIDAK_CUKUP,
    SAMPEL_MINIMUM,
    ringkas,
)


class TestKecukupan:
    def test_di_bawah_ambang_dinyatakan_tidak_cukup(self, tiga_kasus) -> None:
        hasil = ringkas(tiga_kasus)

        assert not hasil.cukup
        assert hasil.total == 3

    def test_kosong_bukan_nol_persen(self) -> None:
        """Nol kasus serupa bukan "win rate nol" - itu ketiadaan bukti, dan
        melaporkannya sebagai angka akan membuatnya masuk ke keputusan."""
        hasil = ringkas([])

        assert hasil.total == 0
        assert not hasil.cukup
        assert all(v is None for v in hasil.win_rate.values())

    def test_di_atas_ambang_cukup(self, banyak_kasus) -> None:
        assert ringkas(banyak_kasus).cukup

    def test_ambangnya_dua_puluh(self) -> None:
        assert SAMPEL_MINIMUM == 20


class TestRingkasannya:
    def test_win_rate_per_arah(self, banyak_kasus) -> None:
        hasil = ringkas(banyak_kasus)

        assert 0 <= hasil.win_rate["LONG"] <= 100

    def test_arah_tanpa_kasus_bukan_nol_persen(self, hanya_long) -> None:
        """Win rate SHORT 0% dibaca sebagai "SHORT selalu kalah". Yang benar:
        tidak ada satu pun kasus SHORT untuk dinilai."""
        hasil = ringkas(hanya_long)

        assert hasil.win_rate["SHORT"] is None

    def test_rentang_waktunya_dilaporkan(self, banyak_kasus) -> None:
        """Lima hari yang disebut "historis" tanpa tanggalnya terbaca seperti
        bertahun-tahun."""
        assert ringkas(banyak_kasus).rentang_waktu is not None

    def test_kalimatnya_persis_seperti_pasalnya(self) -> None:
        assert KALIMAT_TIDAK_CUKUP == "INSUFFICIENT HISTORICAL SAMPLE"
        assert KALIMAT_TIDAK_ADA == "NO SIGNIFICANT HISTORICAL MATCH"
```

Fixture `tiga_kasus`, `banyak_kasus`, `hanya_long` dibangun di berkasnya
sendiri dari `Ingatan` + `Kemiripan` yang sungguhan — **bukan** dari
`SimpleNamespace`. Palsu yang bidangnya beda dari objek asli sudah dua kali
membuat suite hijau di atas bug produksi di proyek ini.

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis implementasinya**

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Ubah `win_rate` supaya memulangkan `0` alih-alih `None` untuk arah tanpa kasus.
`test_arah_tanpa_kasus_bukan_nol_persen` harus MERAH. Kembalikan.

---

## Task 7: Peringkat, kebaruan, dan peluruhan (PASAL 15.11, 15.21, 15.22)

**Files:**
- Create: `src/aruna/memory/ranking.py`
- Create: `tests/test_memory_ranking.py`

**Interfaces:**
- Consumes: `Ingatan`, `Kemiripan`, `Mutu`.
- Produces:
  - `SETENGAH_UMUR_HARI: float = 30.0` — configurable.
  - `bobot_kebaruan(umur_hari: float, *, setengah_umur: float = SETENGAH_UMUR_HARI) -> float`
  - `peringkat(cocok, *, as_of) -> list[tuple[Ingatan, Kemiripan, float]]`

**Yang harus dieja di kode, bukan cuma di rencana:** korpusnya lima hari. Pada
setengah-umur 30 hari, seluruh bobot kebaruan sekarang berada di antara 0,89
dan 1,00 — artinya **peluruhan praktis tidak berpengaruh hari ini**. Itu bukan
alasan menghapusnya; itu alasan menuliskannya, supaya pembaca berikutnya tidak
menyimpulkan bahwa ia sudah terbukti bekerja.

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestKebaruan:
    def test_yang_baru_berbobot_penuh(self) -> None:
        assert bobot_kebaruan(0.0) == pytest.approx(1.0)

    def test_setengah_umur_memberi_setengah_bobot(self) -> None:
        assert bobot_kebaruan(30.0) == pytest.approx(0.5, abs=0.01)

    def test_yang_sangat_tua_tetap_tidak_nol(self) -> None:
        """PASAL 15.21: HISTORICAL VALUE != ZERO. Data lama tetap berguna untuk
        konteks jangka panjang, dan bobot nol sama saja dengan menghapusnya."""
        assert bobot_kebaruan(3650.0) > 0

    def test_korpus_lima_hari_hampir_tidak_meluruh(self) -> None:
        """Diukur 2026-08-21: seluruh ingatan berumur 0-5 hari. Test ini ada
        supaya angka yang nyaris seragam itu tidak dibaca sebagai bug."""
        assert bobot_kebaruan(5.0) > 0.85
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis implementasinya** — peluruhan eksponensial
  `0.5 ** (umur / setengah_umur)`, tanpa lantai nol.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Ganti peluruhannya dengan pemotongan keras (`0.0` di atas 90 hari).
`test_yang_sangat_tua_tetap_tidak_nol` harus MERAH. Kembalikan.

---

## Task 8: Konteks historis yang dibaca Phase 14 (PASAL 15.20, 15.30, 15.41, 15.42, 15.45)

**Files:**
- Create: `src/aruna/memory/context.py`
- Create: `tests/test_memory_context.py`

**Interfaces:**
- Consumes: seluruh tugas sebelumnya.
- Produces:
  - `Pengaruh(StrEnum)` — `SUPPORTIVE`, `CONTRARY`, `NEUTRAL`.
  - `KonteksHistoris` dataclass: `.ringkasan: Ringkasan`, `.pengaruh`,
    `.kontribusi: int` (0–100), `.memory_ids: tuple[str, ...]`,
    `.as_of: datetime`, `.catatan: tuple[str, ...]`.
  - `susun(*, arah_sekarang: str, cocok, as_of) -> KonteksHistoris`

**Aturan yang paling mudah dilanggar:** `susun` **tidak boleh** memulangkan
arah. Ia memulangkan bukti. PASAL 15.42 — memory tidak mengubah keputusan.

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestPengaruh:
    def test_sejarah_yang_sejalan_disebut_supportive(self, banyak_long_menang) -> None:
        k = susun(arah_sekarang="LONG", cocok=banyak_long_menang, as_of=NOW)

        assert k.pengaruh is Pengaruh.SUPPORTIVE

    def test_sejarah_yang_berlawanan_disebut_contrary(self, banyak_long_kalah) -> None:
        """PASAL 15.20: memory yang berlawanan TIDAK diikuti diam-diam dan
        TIDAK dibuang diam-diam. Ia dinamai."""
        k = susun(arah_sekarang="LONG", cocok=banyak_long_kalah, as_of=NOW)

        assert k.pengaruh is Pengaruh.CONTRARY

    def test_sampel_tidak_cukup_selalu_netral(self, tiga_kasus) -> None:
        """Tiga kasus tidak boleh menghasilkan SUPPORTIVE - itu confirmation
        bias dengan angka di belakangnya (PASAL 15.38)."""
        k = susun(arah_sekarang="LONG", cocok=tiga_kasus, as_of=NOW)

        assert k.pengaruh is Pengaruh.NEUTRAL
        assert k.kontribusi == 0


class TestTidakMemutuskan:
    def test_tidak_ada_bidang_keputusan(self, banyak_long_menang) -> None:
        """PASAL 15.42. Sebuah bidang `decision` di sini akan dibaca pemanggil
        berikutnya sebagai keputusan, betapa pun dokumennya berkata lain."""
        k = susun(arah_sekarang="LONG", cocok=banyak_long_menang, as_of=NOW)

        assert not hasattr(k, "decision")
        assert not hasattr(k, "keputusan")


class TestAudit:
    def test_memory_id_yang_dipakai_dicatat(self, banyak_long_menang) -> None:
        """PASAL 15.41: tiap signal harus bisa menjawab memory mana yang
        dipakai. Konteks tanpa daftar itu tidak bisa diperiksa ulang."""
        k = susun(arah_sekarang="LONG", cocok=banyak_long_menang, as_of=NOW)

        assert k.memory_ids
        assert k.as_of == NOW
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis implementasinya**

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Hapus penjaga `if not ringkasan.cukup: return NEUTRAL`.
`test_sampel_tidak_cukup_selalu_netral` harus MERAH. Kembalikan.

---

## Task 9: Proyektor di loop upkeep (PASAL 15.2, 15.28)

**Files:**
- Modify: `src/aruna/upkeep/loop.py`
- Modify: `src/aruna/app.py`
- Modify: `src/aruna/core/config.py`
- Create: `tests/test_memory_tersambung.py`

Bentuknya **persis** mengikuti `korelasi` yang sudah terbukti di Phase 14
putaran keempat: `memory: Any = None` di konstruktor, `_memory_due_now`,
`_proyeksikan_memory`, dua setelan `memory_enabled` dan
`memory_interval_sec: float = 600.0`, dan pemanggilan di `cycle()` bersama
pekerjaan turunan lain.

- [ ] **Step 1: Tulis test yang gagal** — `test_siklus_upkeep_memproyeksikan`,
  `test_kegagalannya_tidak_menghentikan_siklus`,
  `test_tidak_diulang_sebelum_cadence`, dan
  `test_aplikasi_mengoper_proyektornya_ke_loop` **lewat AST**, bukan
  `in inspect.getsource` — pencarian teks tetap hijau saat barisnya
  dikomentari, dan itu sudah tertangkap sekali di Phase 14.

- [ ] **Step 2–5:** MERAH → implementasi → HIJAU → cabut-uji tiap penjaga.

---

## Task 10: Sambungkan ke Phase 14 dan ke operator (PASAL 15.31, 15.32, 15.43)

**Files:**
- Modify: `src/aruna/futures/debate.py` (bidang `CouncilNote.memory`)
- Modify: `src/aruna/futures/service.py` (`attach_memory`)
- Modify: `src/aruna/futures/notify.py` (blok ringkas)
- Modify: `src/aruna/notify/daily.py` (bagian harian)
- Modify: `tests/test_memory_tersambung.py`

Bentuk pesannya, ringkas dan wajib menyebut rentang waktu (PASAL 15.31):

```
🧠 HISTORICAL CONTEXT
126 kasus serupa (15-20 Agu), similarity 80-96%
Bias historis: LONG  |  Konteks: SUPPORTIVE
```

Saat sampelnya tipis, barisnya tetap ada dan berbunyi apa adanya:

```
🧠 HISTORICAL CONTEXT
INSUFFICIENT HISTORICAL SAMPLE (3 kasus)
```

- [ ] **Step 1: Tulis test yang gagal** — pesannya membawa blok itu; sampel
  tipis mencetak kalimat PASAL 15.9; tanpa konteks tidak ada barisnya sama
  sekali; dan **tidak ada kalimat probabilitas** — test yang memeriksa bahwa
  keluarannya tidak pernah memuat "chance", "probability", "pasti", atau "%
  akan naik" (PASAL 15.23, §51).

- [ ] **Step 2–5:** MERAH → implementasi → HIJAU → cabut-uji.

---

## Task 11: Apakah memory benar-benar membantu (PASAL 15.44, 15.45)

**Files:**
- Modify: `src/aruna/notify/daily.py`
- Create: `tests/test_memory_kontribusi.py`

Membandingkan keputusan dengan dan tanpa memory butuh keputusan yang cukup
banyak untuk dibandingkan. Dengan **178** hasil futures dan korpus lima hari,
angkanya belum bisa berarti apa-apa hari ini.

Karena itu tugas ini **tidak** menghitung skor perbandingan. Ia mencatat
bahannya: `memory_contribution` per signal, `pengaruh`, dan hasil akhirnya —
supaya perbandingannya bisa dijalankan nanti, saat sampelnya ada. Sebuah metrik
"memory contribution low" yang dihitung dari lima hari akan menjadi angka
percaya diri tanpa dasar, dan itu yang PASAL 15.44 justru coba cegah.

- [ ] **Step 1–5:** seperti tugas lain, dengan test yang menjaga bahwa
  perbandingannya **menolak berjalan** di bawah ambang sampel.

---

## Task 12: Ruff, suite penuh, restart, ukur di produksi

- [ ] **Step 1: Ruff** — `.\.venv\Scripts\python.exe -m ruff check src tests`
- [ ] **Step 2: Suite penuh, SENDIRIAN** — `.\.venv\Scripts\python.exe -m pytest -q`
- [ ] **Step 3: Restart ARUNA**, verifikasi sampai `health.transition status=UP`
- [ ] **Step 4: Ukur dari log, bukan dari keyakinan.** Sesudah minimal dua
  `futures.tick`:
  - `memory.proyeksi` — harus muncul, dengan jumlah > 0 pada lintasan pertama
  - `memory.proyeksi_failed` — harus 0
  - `futures.memory_failed` — harus 0
  - `memory.kebocoran` — harus 0 (dan kalau > 0, hentikan semuanya)
  - baris `level=error` mana pun — harus 0
  - `Data truncated` — harus 0
- [ ] **Step 5: Laporkan apa adanya.** Kalau ada yang tidak nol, sebutkan
  angkanya. Jangan melaporkan "beres" atas langkah yang belum terukur.

---

## Hasil pelaksanaan — 2026-08-21

**Selesai:** Task 1 sampai 12, plus Task 10b yang tidak ada di rencana awal
(proyektor ingatan futures) dan `lookup.py` (jembatan simbol dan peminjaman
timeframe). **Total 24 cabut-uji, 24 merah.**

| task | isi |
|---|---|
| 1 | `dimensions.py` — UNKNOWN bukan kecocokan |
| 2 | `fingerprint.py` — sidik jari pembanding, bukan hash |
| 3 | `similarity.py` — skor dan cakupan tidak pernah dilebur |
| 4 | `record.py` — `Ingatan` beku, PASAL 15.25 pindah ke tipe |
| 5 | migrasi 0031 + repositori terikat `as_of` |
| 6 | `outcome.py` — kecukupan sampel, win rate per arah |
| 7 | `ranking.py` — peluruhan, bukan pemotongan |
| 8 | `context.py` — SUPPORTIVE/CONTRARY/NEUTRAL terhadap **dasar terukur** |
| 9 | proyektor di loop upkeep |
| 10 | `attach_memory` + blok 🧠 HISTORICAL CONTEXT |
| 10b | proyektor ingatan futures (di luar rencana) |
| 11 | jejak kontribusi + bagian harian |
| 12 | ruff, suite penuh, restart, ukur |

### Terukur di produksi, dua tick sesudah restart

Suite penuh: **3.764 lulus, exit 0** — nol gagal, nol skip, nol xfail.

| yang diukur | harus | terukur |
|---|---|---|
| `upkeep.memory` | muncul | 2 |
| `upkeep.memory_failed` | 0 | 0 |
| `upkeep.memory_futures_failed` | 0 | 0 |
| `futures.memory_failed` | 0 | 0 |
| `futures.memory_context_failed` | 0 | 0 |
| `memory.cari_terpotong` | 0 | 0 |
| `level=error` | 0 | **0** |
| `Data truncated` | 0 | 0 |

Ingatan sampai ke keputusan pada **40 amatan**: pengaruh NEUTRAL 36, **CONTRARY
4**; kasus serupa rata-rata 31,9 (maksimum 73); kontribusi rata-rata 29,6.
Kelengkapan integrasi Phase 14 tetap 90,4% — Phase 15 tidak mengganggunya.

Isi ingatan: **8.548 rekaman** — 15m 5.377, 1h 2.189, 1d 800, 4h 182. Yang
bisa mengajari: 15m 5.377, 1h 2.189, 1d 800, **4h 17**.

### Enam cacat yang hanya data produksi yang menemukan

Tidak satu pun akan ditemukan test yang datanya ditulis sendiri.

1. **Waktu tanpa zona.** Lintasan pertama meledak: kolom `DATETIME` MySQL tidak
   membawa zona waktu, dan `to_mysql_datetime` menolak yang naif dengan
   sengaja. Diperbaiki dengan `as_utc` di batas repositori.
2. **`Data truncated for column 'move_pct'`, berulang-ulang.** Sumbernya enam
   desimal, kolomnya empat. Sekarang dibulatkan di kode; sesudah bangun ulang,
   nol peringatan.
3. **Parser berita meleset di dua pertiga korpus.** Bentuk dominan
   `NO_RECENT_NEWS` — 5.980 dari 8.914 — jatuh ke UNKNOWN tanpa satu pun test
   merah, karena satu baris contoh yang dibaca kebetulan berformat lain.
4. **Pemotongan diam-diam.** Pencarian memulangkan tepat 5.000 dari 5.377
   kandidat; yang terpotong selalu yang tertua. Sekarang `cari_terhitung`
   memulangkan penandanya dan catatannya sampai ke jejak audit.
5. **Arah dibaca dari tempat yang salah.** `VoteSplit` tidak punya bidang
   `decision`, jadi pengaruhnya akan **selamanya NEUTRAL** — fitur yang
   tersambung, berjalan, tidak pernah error, dan tidak pernah mengatakan apa
   pun. Ketahuan hanya karena probe produksi jatuh di sana.
6. **Yang terbesar: pembaca ingatan tidak pernah dipasang di jalur keputusan.**
   `memory=` disambungkan ke `UpkeepLoop`, yang hidup di proses `aruna run` -
   sementara keputusan futures dibuat di proses **lain**, `futures-loop`, dan
   `FuturesPlanService` tidak pernah diberi repositorinya. Terukur sesudah
   restart: `memory_pengaruh=UNKNOWN` pada keempat puluh amatan. Seluruh test
   hijau karena semuanya mengoper `_memory` sendiri.

### Satu cacat yang dicegah sebelum ditulis

Penghitung timeframe semula menghitung seluruh baris. Dengan 182 ingatan futures
- **165 di antaranya EXPIRED** - ia akan melihat "182 ≥ ambang 20", meninggalkan
korpus 1h yang punya 2.189 hasil sungguhan, dan berpindah ke timeframe yang
lebih tepat tetapi nyaris tidak bisa mengatakan apa pun. Sekarang ia hanya
menghitung yang bisa mengajari: 4h = 17 < 20, pinjaman 1h berlanjut, dan
berakhir sendiri begitu futures punya isi.

### Dua kesalahan proses yang kuakui

1. `lookup.py` ditulis **sebelum** testnya. Cabut-uji tiga kali dijalankan untuk
   membuktikan testnya tetap menggigit.
2. Suite penuh pertama dijalankan **sambil menyunting kode**, dan menghasilkan
   satu kegagalan palsu (`inspect.getsource` memulangkan potongan berkas yang
   bergeser). Diulang pada berkas yang stabil: hijau. Aturan proyek ini
   memperingatkan tepat hal itu.

### Yang belum terbukti

Blok `🧠 HISTORICAL CONTEXT` **belum pernah terkirim ke Telegram**: `plans=0`
pada kedua tick, seperti seluruh sesi ini. Ia terbukti benar terhadap bentuk
data produksi lewat render langsung dari 8.548 ingatan, tapi jalur kirimnya
menunggu pasar yang menghasilkan PLAN.

Bagian `🧠 MARKET MEMORY` di laporan harian juga belum pernah terkirim - laporan
berikutnya jatuh pukul 00:00 WIB.

---

## Putaran kedua — operator meminta seluruh phase 100%

**Cakupan pasal sekarang 44/44 dan 49/49**, dan
[test_cakupan_pasal.py](../../../tests/test_cakupan_pasal.py) menolak kalau ada
yang mundur. Sebelumnya: Phase 14 39/44, Phase 15 33/49.

### Satu cacat di pekerjaanku sendiri, ditemukan saat memeriksa

`daily_service.py` **tidak punya satu baris pun** yang mengisi
`DailyReport.memory`. `IngatanHarian` dibangun, `render_daily` mencetaknya,
testnya hijau - dan bloknya tidak akan pernah muncul. Aku melaporkan giliran
sebelumnya bahwa ia "menunggu pukul 00:00 WIB"; itu salah. Sekarang ada
`ringkas_harian` di repositori, `_memory_harian` di layanan, dan penjaga AST
yang menolak `DailyReport` dibangun tanpanya.

### Tiga pasal terakhir, dibangun dari data yang diukur lebih dulu

- **PASAL 15.16 (pattern memory)** — [pola.py](../../../src/aruna/memory/pola.py).
  **Dibaca, tidak dihitung ulang** (PASAL 15.33). Dari 368 pola Phase 12,
  hanya **57** yang mengalahkan baseline; yang tidak, dilewati - pola yang
  tidak lebih baik daripada tebakan dasar adalah derau yang sudah diberi nama.
- **PASAL 15.15 (event memory)** —
  [peristiwa.py](../../../src/aruna/memory/peristiwa.py). Bentuk kaya yang
  pasalnya bayangkan tidak bisa dibangun jujur: `news_events` berisi 1.156
  baris, **750 bersentimen UNKNOWN**, hanya 158 tertaut aset, dan kategorinya
  IDX (`BI_RATE`, `RUPIAH`) sementara keputusannya kripto. Yang bisa dijawab,
  dan sudah tersimpan, ternyata justru temuan: **berita NEGATIVE menghasilkan
  win rate 23%** melawan 42-50% pada keadaan lain (n=73).
- **PASAL 15.18 (cross-asset)** —
  [lintas.py](../../../src/aruna/memory/lintas.py). DXY dan emas **tidak ada di
  universe** (31 aset, kripto USDT dan saham IDX). Jadi yang dilaporkan
  menyebut kesempitannya: "N/M aset **kripto** berada di rezim X". Ada test yang
  gagal kalau kata "risk-on" muncul.

### Terukur di produksi, dua tick sesudah restart

Suite penuh: **3.810 lulus, exit 0** - nol gagal, nol skip, nol xfail.

Seluruh penghitung kegagalan nol: `futures.memory_failed`,
`futures.memory_context_failed`, `futures.pola_failed`, `upkeep.memory_failed`,
`level=error`, `level=critical`, `Data truncated`.

Ingatan sampai ke keputusan pada 40 amatan: **NEUTRAL 36, CONTRARY 4**; kasus
serupa rata-rata 32,2; kontribusi rata-rata 29,8.

### Putaran ketiga — dua masukan terakhir, dan kelengkapan 100%

Operator meminta 100% hari itu juga. Jalan yang jujur ternyata ada, dan ia
bukan menandai `True`: **membuat datanya ada.**

`WALK_FORWARD` dan `OUT_OF_SAMPLE` hilang karena `backtest_runs` berisi nol
baris - dan itu bukan karena mesinnya tidak ada. `BacktestService` menghitung
fold walk-forward, holdout, dan seluruh peringatannya dengan lengkap;
`BacktestRepository.record_backtest` sudah ada; dan perintah `aruna backtest`
**mencetak hasilnya lalu membuangnya**. Tidak ada satu pun pemanggil
`record_backtest` di seluruh kode. Keluarga cacat yang sama, untuk kesekian
kalinya.

Yang dikerjakan: perintahnya menyimpan, backtest sungguhan dijalankan
(**11.240 keputusan disimulasikan**, walk-forward CONSISTENT di empat fold,
holdout dievaluasi), dan `_kelengkapan_fase` membaca **keberadaan validasinya**
lewat `validasi_terakhir`.

**Terukur sesudah restart: PHASE 11 100%, PHASE 12 100%, PHASE 13 100%,
gabungan 100% pada 40 amatan - nol masukan hilang.** Suite penuh 3.825 lulus,
exit 0. Nol error, nol `Data truncated`.

### Palsu berbentuk salah, lagi - dan cabut-uji yang menangkapnya

Versi pertama penyambungan ini memakai `recent_runs`. Kueri itu **tidak
memilih `walk_forward` maupun `holdout_included` sama sekali** - ia kueri
khusus rezim biaya untuk governance (SPEC 31), kolomnya hanya PnL. Palsunya
memulangkan apa yang test inginkan, testnya hijau, dan di produksi kedua
masukan tetap hilang. Ketahuan saat pengukuran produksi tetap menunjukkan
90,4%.

Sekarang ada `validasi_terakhir` dengan kueri sendiri, dan test yang memeriksa
**SQL-nya**, bukan palsunya.

### Putaran keempat — PASAL 15.44, dan jawabannya bukan yang menyenangkan

Satu-satunya pasal Phase 15 yang masih belum berfungsi adalah 15.44:
perbandingan keputusan **dengan** memory melawan **tanpa** memory. Sebelumnya
ditolak dengan alasan yang benar - memory baru mulai mempengaruhi keputusan
hari itu, jadi belum ada hasil yang bisa diatribusikan kepadanya.

Jalan yang jujur ternyata sudah diwajibkan pasal lain: **PASAL 15.40** menuntut
mesin ingatan mendukung simulasi historis. Jadi evaluasinya dijalankan
retrospektif - untuk tiap keputusan lama, hitung konteks yang **waktu itu**
tersedia, dengan sapuan bertahap yang tidak pernah melihat resolusi masa depan.

**Hasilnya, atas 1.671 keputusan historis:**

| | menang | kalah | win rate |
|---|---|---|---|
| SUPPORTIVE | 95 | 125 | **43%** |
| CONTRARY | 83 | 122 | **40%** |

**Selisih +3 poin. Di bawah ambang. `membantu = False`.**

Ingatan yang dibangun sepanjang hari ini **belum menambah apa pun yang bisa
diukur**. Tiga poin pada sampel 220 lawan 205 adalah derau, dan menyebutnya
kontribusi berarti membaca derau sebagai temuan.

Itu bukan kegagalan pengukuran - itu **pengukurannya**, dan PASAL 15.44 justru
meminta ARUNA mendeteksinya: *"Jika Memory tidak meningkatkan kualitas
keputusan, ARUNA harus mendeteksi MEMORY CONTRIBUTION LOW. Jangan memaksakan
penggunaan memory."* Sekarang laporan hariannya mengatakan itu sendiri.

Dua kemungkinan sebabnya, dan keduanya belum diuji - jadi disebut sebagai
pertanyaan, bukan kesimpulan: korpusnya baru beberapa hari, dan sidik jarinya
kehilangan tujuh dimensi yang tidak pernah tersimpan (volatility, volume,
momentum, trend, OI, funding, structure). Menambah dimensi yang hilang menuntut
menyimpannya lebih dulu, dan itu pekerjaan tersendiri.

## Putaran kelima — akar masalahnya, dan memory akhirnya membantu

Evaluasi PASAL 15.44 melaporkan **+3 poin** - derau. Dua dugaan sebabnya
disebut: korpus yang baru beberapa hari, dan **sidik jari yang kehilangan tujuh
dimensi**. Yang kedua ternyata bisa ditutup hari itu juga.

### Lima dari tujuh tidak perlu kolom sama sekali

`realised_volatility`, `momentum`, `volume_anomaly`, dan `analyse_structure`
semuanya berjalan atas `CandleSeries` - dan candle-nya tersimpan sejak Juli.
Dihitung ulang pada bar yang **sudah tutup sebelum** tiap keputusan, kelimanya
lahir untuk **seluruh korpus**, bukan hanya ingatan yang akan datang.

Ambangnya diturunkan dari **tercile korpus** (n=900 jendela, 20 aset kripto
15m), bukan dipilih: volatilitas p33=0,161 p67=0,300; momentum p33=**−0,105**
p67=0,405; volume p33=0,501 p67=1,046. Perhatikan momentum p33 negatif - ambang
nol akan menyebut sepertiga korpus "positif" hanya karena nol kebetulan bukan
tengahnya.

`open_interest` dan `funding` tetap UNKNOWN: data venue perpetual yang tidak
pernah disimpan per keputusan dan tidak bisa diturunkan dari candle spot.

### Cacat yang sama, untuk kesekian kalinya - dan tertangkap lagi

Lintasan pertama: perkayaannya **dihitung lalu dibuang**, karena
`market_memories` tidak punya kolomnya. Cakupan naik ke 99,8% sementara
pembacanya masih membaca delapan dimensi. Migrasi 0032 yang menutupnya, plus
`KOLOM_DIMENSI` - satu peta yang dipakai penulis **dan** pembaca, karena dua
daftar kolom yang harus tetap sepakat adalah dua daftar yang suatu saat tidak.

### Hasilnya

Sebaran kelima dimensi sesudah proyeksi ulang 9.026 ingatan:

| dimensi | sebaran |
|---|---|
| volatility | HIGH 5.834 / MEDIUM 1.801 / LOW 1.391 |
| momentum | POSITIVE 4.285 / NEGATIVE 2.668 / FLAT 2.073 |
| volume | NORMAL 3.570 / HIGH 3.235 / LOW 2.221 |
| trend | BULLISH 5.872 / BEARISH 3.064 / SIDEWAYS 90 |
| structure | RANGE 4.214 / UPTREND 3.648 / DOWNTREND 1.164 |

**Evaluasi PASAL 15.44 dijalankan ulang - evaluasi yang sama, disiplin `as_of`
yang sama, korpus yang sama:**

| | SUPPORTIVE | CONTRARY | selisih | verdict |
|---|---|---|---|---|
| 8 dimensi | 43% (220) | 40% (205) | **+3** | `membantu: False` |
| **13 dimensi** | **53% (167)** | **39% (99)** | **+14** | **`membantu: True`** |

Satu-satunya yang berubah adalah sidik jarinya. Ingatan ARUNA sekarang
benar-benar membedakan satu kondisi pasar dari yang lain - dan evaluasinya
sendiri yang mengatakan itu, bukan penulis kodenya.

## Putaran keenam — tiga sisa terakhir, dan satu temuan yang membalik kesimpulan

Tiga hal disebut sebagai "belum selesai" sesudah putaran kelima. Diukur, dua di
antaranya ternyata **sudah ada dan tidak pernah dibaca** - keluarga cacat yang
sama untuk kesekian kalinya.

| sisa | keadaan sebenarnya |
|---|---|
| **funding** | `futures_plans.funding_cost_pct` terisi pada 192 baris (-0,204 sampai +0,348), ada sejak migrasi 0015, tidak pernah dibaca untuk ingatan |
| **open interest** | `BinanceFuturesProvider.open_interest()` **dan** `open_interest_history()` terimplementasi penuh, masuk allowlist, hasilnya tidak pernah disimpan ke mana pun |
| **mutu seragam** | sudah selesai sendiri oleh 0032: HIGH 9.166 / MEDIUM 184 / LOW 4 |

Migrasi 0033 memberi keduanya kolom, dan proyektor futures mengisinya. Sesudah
proyeksi ulang 188 ingatan futures - **seluruh lima belas dimensi terisi**:

    funding_band  FLAT 125 / NEGATIVE 52 / POSITIVE 11
    oi_band       FLAT 106 / FALLING 45 / RISING 37

Cakupan ingatan futures naik **35-41% → 71-76%**.

### Lubang yang nyaris membuat semuanya sia-sia

Perkayaan hanya menyentuh sisi **ingatan**. Kondisi sekarang di jalur hidup
masih delapan dimensi - dan `bandingkan` mengeluarkan yang tidak terbaca di
satu sisi, tepat seperti rancangannya. Kerja seharian yang tidak mengubah satu
pun keputusan hidup, tanpa satu error pun. Ditutup dengan `_teknikal_sekarang`,
yang menghitungnya dari **bar yang sama** lewat `kandil_sampai` - dua cara
berbeda menghitung volatilitas akan membandingkan dua besaran yang kebetulan
bernama sama.

### Dan temuan yang membalik kesimpulan putaran kelima

Evaluasi PASAL 15.44 dijalankan di **kedua** timeframe:

| korpus | SUPPORTIVE | CONTRARY | selisih | verdict |
|---|---|---|---|---|
| 15m (1.656 keputusan) | 53% | 39% | **+14** | membantu |
| **1h (923 keputusan)** | 58% | 65% | **-7** | **tidak membantu** |

**Dan 1h justru yang dipakai jalur hidup** - dipinjam karena ingatan 4h belum
cukup. Terukur di produksi sesudah restart: kasus serupa melonjak 36,9 → 229,2
dan **seluruh dua puluh amatan menjadi NEUTRAL** (sebelumnya 3 CONTRARY, 1
SUPPORTIVE). Di 1h, dimensi tambahan menaikkan kemiripan secara merata tanpa
membedakan; kolamnya melebar dan hasilnya saling menghapus.

Jadi kesimpulan putaran kelima - "memory akhirnya membantu" - **hanya berlaku
untuk 15m**. Menyebutnya berlaku umum adalah menggeneralisasi satu korpus, dan
itu persis yang PASAL 15.44 ada untuk mencegah.

Yang belum dikerjakan, dan disebut sebagai pekerjaan berikutnya bukan sebagai
selesai: **evaluasi per timeframe belum menggerbangi apa pun**. Sistem tahu
memory tidak membantu di 1h dan tetap memakainya di sana. Menggerbanginya dari
43 kasus CONTRARY akan mengganti satu kesimpulan tergesa dengan yang lain.

### Yang backtest itu katakan, dan tidak boleh dikubur di bawah "100%"

Angka kelengkapan 100% berarti seluruh masukan **terbaca**. Ia tidak berarti
apa yang terbaca itu bagus. Lintasan backtest pertama sistem ini melaporkan:

- direction accuracy **42%**
- gross PnL +706,45, total biaya **11.258,05**, net PnL **-10.551,60**
- biaya memakan **15,9x** gross

Walk-forward CONSISTENT - artinya konsisten di keempat periode, bukan
menguntungkan. Itu temuan yang pantas jadi pekerjaan tersendiri, bukan catatan
kaki.

---

## Self-review

**1. Cakupan spec.**

| PASAL | Tugas |
|---|---|
| 15.1, 15.42, 15.48 | Global Constraints + Task 8 (tanpa bidang keputusan) |
| 15.2, 15.27, 15.28 | Task 5 (proyeksi), Task 9 (cadence) |
| 15.3, 15.5 | Task 1, Task 2 |
| 15.4 | Task 2 |
| 15.6, 15.29 | Task 5 (`cari`) + Task 3 (`bandingkan`) |
| 15.7, 15.8, 15.23 | Task 3 |
| 15.9, 15.10, 15.37 | Task 6 |
| 15.11, 15.21, 15.22 | Task 7 |
| 15.12, 15.13, 15.14 | Task 3 (bobot ASSET/TIMEFRAME/REGIME) + Task 5 (indeks rezim) |
| 15.17 | Task 2 (`band_likuiditas` dari `spread_bps`) |
| 15.19 | sudah ada — tabel `correlations`, Phase 14 putaran keempat |
| 15.20, 15.38 | Task 8 (`Pengaruh.CONTRARY`) |
| 15.24, 15.25, 15.26 | Task 4 + Task 5 (UNIQUE `signal_id`) |
| 15.30, 15.31, 15.43 | Task 10 |
| 15.32, 15.34 | Task 10 (`attach_memory`) |
| 15.39, 15.40 | Task 5 |
| 15.41, 15.45 | Task 8 |
| 15.44 | Task 11 |
| 15.46, 15.47, 15.49 | alur keseluruhan; tidak punya kode sendiri |

**Celah yang diketahui dan disebut, bukan disembunyikan:**

- **15.15 (event memory) dan 15.16 (pattern memory) tidak punya tugas.**
  `news_events` sudah berisi 1.149 baris dan `discovered_patterns` 367 — dan
  PASAL 15.33 melarang menggabungkan fungsi Phase 12 ke Phase 15. Menyambungkan
  keduanya berarti memutuskan lebih dulu siapa pemilik pola, dan itu keputusan
  yang pantas dibahas terpisah, bukan diselipkan.
- **15.18 (cross-asset context) tidak punya tugas.** Bahannya ada — korelasi
  sudah terisi tiap jam sejak Phase 14 — tapi "risk-on environment" menuntut
  DXY dan emas yang tidak ada di universe ARUNA. Membangunnya dari tiga koin
  yang berkorelasi 0,88 akan menghasilkan satu sinyal yang dihitung tiga kali.
- **15.11 dan 15.21 dibangun tapi belum bisa dibuktikan.** Korpus lima hari.
- **15.44 sengaja tidak menghitung skornya.** Lihat Task 11.

**2. Pindaian placeholder.** Tidak ada "TBD" atau "nanti". Task 6, 9, 10, dan
11 menyebut bentuk testnya tanpa menuliskan seluruh badannya — itu disengaja
untuk tugas yang menyalin pola yang **sudah terbukti di repo ini** (`korelasi`
di Task 9, `attach_*` + cabut-uji di Task 10); pelaksananya diarahkan ke berkas
yang sudah ada, bukan ke ruang kosong.

**3. Konsistensi tipe.** `Sidik` dipakai sama di Task 2, 3, 4, 5.
`Kemiripan` di Task 3, 6, 7, 8. `Ingatan` di Task 4, 5, 6, 7, 8. `Hasil` dan
`Mutu` hanya lahir di Task 4. `as_of` bertipe `datetime` sadar-zona di semua
tempat, dan **selalu keyword tanpa bawaan** di jalur pencarian.


---

## PENUTUP PASAL 15.44 — gerbang per timeframe (2026-08-21)

Item terbuka terakhir Phase 15, dan yang paling tidak nyaman: **sistem
mengukur sendiri bahwa ingatan tidak membantu di 1h, lalu tetap memakainya di
sana.** `horizon_ingatan()` meminjam 1h untuk jalur keputusan langsung.

### Yang diukur, dua kali, dengan disiplin `as_of` penuh

Pengukuran pertama atas korpus kecil, pengukuran kedua atas 9.698 ingatan.
Jawabannya tidak berubah:

| timeframe | SUPPORTIVE | CONTRARY | selisih | putusan |
|---|---|---|---|---|
| 15m | 53% dari 187 | 39% dari 106 | **+14** | membantu |
| 1h | 58% dari 159 | 65% dari 43 | **-7** | tidak membantu |
| 1d | - | 1 kasus | - | belum bisa dinilai |
| 4h | nol ingatan | | | meminjam 1h |

### Aturannya: pakai yang terbukti membantu

Bukan "blokir yang terbukti berlawanan". Selisih -7 di 1h **bukan** `terbalik`
(belum mencapai -10) tapi juga bukan `membantu` - dan memberi bobot pada yang
tidak menambah apa-apa adalah persis yang PASAL 15.44 larang: *jangan
memaksakan penggunaan memory*.

Konsekuensinya: **diam berarti belum terbukti, bukan terbukti baik.** 1d dengan
4 keputusan tergerbang. Kalau sebaliknya, tiap timeframe baru mulai hidupnya
dengan bobot penuh atas bukti yang belum pernah diuji, dan gerbang ini hanya
menutup sesudah kerusakannya terjadi.

### Tiga hal yang menahannya agar tidak merusak yang lain

**1. Yang digerbangi bobotnya, bukan tampilannya.** Kasus serupa tetap
dihitung, tetap dikirim ke operator, tetap punya jejak audit (PASAL 15.20,
15.38). Menyembunyikan bukti yang bertentangan adalah confirmation bias yang
dilakukan sistem atas nama operator.

**2. `digerbangi` dibedakan dari `NEUTRAL` biasa.** "Sejarah tidak berpendapat"
dan "pendapat sejarah sengaja tidak dipakai" adalah dua hal yang sangat
berbeda. Terukur di produksi pada tick pertama: dari 20 amatan, **1
`digerbangi=True`** dan 19 NEUTRAL alami - kalau keduanya disatukan, gerbang
ini tidak akan terlihat sama sekali.

**3. Penilaiannya tidak mengoper putusan lama ke pengukurannya sendiri.**
`nilai_satu` memanggil `susun()` **tanpa** `manfaat`. Kalau dioper, gerbang yang
menutup tidak akan pernah bisa membuka lagi - buktinya berhenti dikumpulkan.

### Dua proses, sekali lagi

Putusannya **dihitung** di loop upkeep (`aruna run`) dan **dipakai** di
`futures-loop`. Disimpan di `app_state`, bukan di memori proses - cache dalam
proses akan membuat gerbangnya diam-diam terbuka di sisi yang justru mengambil
keputusan. Itu persis kesalahan PASAL 15.32 yang dulu membuat
`memory_pengaruh=UNKNOWN` pada keempat puluh amatan.

Dijaga dua test AST: `manfaat=` sampai ke `UpkeepLoop`, `app_state=` sampai ke
`FuturesPlanService`.

### Ditemukan sambil merangkai

`_ingatan_dari` di `futures/service.py` adalah **salinan kedua** pembangun
`Ingatan` - dua daftar kolom yang harus tetap sepakat, dan yang tidak sepakat
menghasilkan sidik jari yang ditulis penuh lalu dibaca kosong tanpa satu pun
error. Disatukan ke `ingatan_dari_baris()` di sebelah `KOLOM_DIMENSI`.

### Bukti produksi

```
{"dinilai": 3, "dipakai": ["15m"], "digerbangi": ["1d", "1h"]}
```

Suite penuh exit 0. Nol error sejak restart.
