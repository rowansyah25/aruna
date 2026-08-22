# Phase 14 — Penyelesaian Decision Intelligence

> **For agentic workers:** REQUIRED SUB-SKILL: gunakan `superpowers:executing-plans`
> untuk menjalankan rencana ini tugas per tugas. Langkahnya memakai checkbox
> (`- [ ]`). **Subagent-driven-development TIDAK dipakai** — operator melarang
> orkestrasi multi-agent di proyek ini (boros token; kerjakan langsung).

**Goal:** Menutup dua belas PASAL Phase 14 yang belum punya kode, dan
menyambungkan delapan modul `aruna.decision` yang sudah ditulis, sudah diuji,
dan tidak pernah dilewati jalur hidup.

**Architecture:** Paket `aruna.decision` sudah berisi enam belas modul murni —
tanpa I/O, tanpa database, tanpa jaringan. Rencana ini menambah tiga modul
sejenis (`final`, `integration`, `engine`) lalu menyambungkan semuanya lewat
dua titik yang sudah ada: `aruna.futures.service` (penyusun rencana) dan
`aruna.futures.notify` / `aruna.notify.result` (pengirim pesan). Tidak ada
lapisan baru; yang ditambah adalah pemanggil untuk kode yang sudah ada.

**Tech Stack:** Python 3.13, dataclass beku + StrEnum, pytest + pytest-asyncio,
ruff, MySQL 8.4 lewat asyncmy, structlog.

## Global Constraints

Disalin apa adanya dari SPEC operator. Setiap tugas tunduk pada seluruh daftar
ini, bukan hanya yang disebut di tugasnya.

- **PASAL 14.1 / §41:** ARUNA DILARANG melakukan BUY, SELL, membuka LONG,
  membuka SHORT, menutup posisi, membuat order, membatalkan order, mengubah
  leverage user, mengubah posisi user, melakukan trading melalui API. Tidak
  boleh ada satu pun jalur baru dari paket ini menuju eksekusi.
- **PASAL 14.2 / 14.43:** keputusan final hanya `LONG`, `SHORT`, atau
  `NO SIGNAL`. `WAIT` DILARANG sebagai keputusan final.
- **§3:** Binance API READ ONLY / MARKET DATA ONLY.
- **§51:** DILARANG mengatakan "100% WIN", "Pasti profit", "Pasti naik",
  "Pasti turun", "Leverage aman", "Pasti berhasil".
- **§11.21 / §6:** DILARANG menghapus LOSS, menyembunyikan LOSS, mengubah
  signal lama, mengubah Entry/SL/TP lama, memanipulasi confidence,
  memanipulasi win rate, cherry picking.
- **PASAL 14.24 / §12.1:** sesudah signal terbit — jangan mengubah direction,
  entry, SL, TP, confidence. Historical record IMMUTABLE.
- **§13.26:** DILARANG mengarang liquidation price, funding rate, spread,
  slippage, correlation, volatility, risk score, position size. Kalau datanya
  tidak ada: `UNKNOWN`.
- **§11.16 / §12.26:** DILARANG AUTO MODEL MODIFICATION.
- **§4 / §26:** DILARANG memakai data lama seolah-olah realtime; DILARANG
  INSERT setiap market tick ke SQL.
- **§33:** CRYPTO: USDT PAIRS ONLY.

### Aturan kerja proyek ini

- **Repo ini BUKAN git repository.** Tidak ada langkah commit. Penggantinya
  adalah **cabut-uji**: cabut barisnya, jalankan testnya, pastikan MERAH,
  kembalikan. Test yang tetap hijau saat kodenya dicabut tidak menguji apa pun.
- **`pytest` dijalankan SENDIRIAN.** Suite di latar plus probe di depan
  menghasilkan kegagalan palsu yang menyamar jadi bug.
- Python venv: `.\.venv\Scripts\python.exe`. PowerShell 5.1 — pakai `;`, bukan
  `&&`.
- Nama dan docstring test ditulis dalam bahasa Indonesia, mengikuti berkas
  yang sudah ada. Docstring menjelaskan **kenapa test ini ada**, bukan apa yang
  dilakukannya.
- Restart ARUNA: `taskkill /PID <cmd ARUNA.bat> /T /F` lalu
  `Start-Process ARUNA.bat`. `ARUNA.bat` tidak punya sub-perintah `stop`.

---

## Keadaan awal — diukur, bukan diingat

Enam belas modul ada di `src/aruna/decision/`. Yang **sudah dilewati jalur
hidup** (diimpor dari luar paket):

| modul | pemanggil |
|---|---|
| `timeframes` | `agents/service.py:124`, `futures/service.py:722` |
| `output` (KAKI) | `futures/notify.py:40` |
| `score` | `futures/notify.py:355,455`, `futures/service.py:800` |
| `invalidation` | `futures/notify.py:454` |
| `observe` | `futures/service.py:768` |
| `context_readings` | `futures/service.py:799` |

Yang **belum pernah dipanggil dari luar paketnya** — delapan modul:
`channel`, `consistency`, `explanation`, `lifecycle`, `outcome`, `silence`,
`timing`, `trail`. (`audit` dan `hierarchy` hanya tersentuh lewat `observe`,
sebagai pengamatan, bukan sebagai gerbang.)

PASAL yang **tidak punya kode sama sekali**: 14.9, 14.10, 14.11, 14.12, 14.13,
14.14, 14.15, 14.39, 14.40, 14.41, 14.42, 14.43.

Catatan penting untuk pelaksana: 14.9–14.12 (debate, protest, veto, council)
**sudah dibangun di Phase 5 dan Phase 6**. Yang diminta Phase 14 bukan
membangunnya lagi, melainkan menyatakan bahwa keputusan final memakainya dan
membuktikan itu terjadi. Membangun ulang akan menghasilkan council kedua yang
tidak sepakat dengan yang pertama.

---

## Struktur berkas

**Dibuat:**

- `src/aruna/decision/final.py` — PASAL 14.2, 14.43. Bentuk keputusan final dan
  larangan `WAIT`. Satu tanggung jawab: mengubah apa pun yang dikeluarkan
  lapisan bawah menjadi `LONG` / `SHORT` / `NO SIGNAL`, dan menolak menerima
  penundaan sebagai jawaban.
- `src/aruna/decision/integration.py` — PASAL 14.13, 14.14, 14.15, 14.39,
  14.40, 14.41. Daftar masukan yang wajib dibaca dari Phase 11, 12, dan 13,
  plus laporan mana yang benar-benar ada. Tidak menghitung apa pun sendiri.
- `src/aruna/decision/engine.py` — PASAL 14.42. Urutan lengkap sebagai satu
  daftar yang bisa dijalani dan diperiksa.
- `tests/test_decision_final.py`
- `tests/test_decision_integration.py`
- `tests/test_decision_engine.py`
- `tests/test_decision_tersambung.py` — penjaga untuk seluruh penyambungan.

**Diubah:**

- `src/aruna/decision/__init__.py` — ekspor modul baru.
- `src/aruna/futures/service.py` — sambungkan `trail`, `lifecycle`,
  `consistency`, `integration`.
- `src/aruna/futures/notify.py` — sambungkan `final`, `timing`, `explanation`,
  `channel`.
- `src/aruna/notify/result.py` — sambungkan `channel`, `outcome`.
- `src/aruna/upkeep/loop.py` — sambungkan `silence` ke laporan harian.

---

## Task 1: Keputusan final tanpa WAIT (PASAL 14.2, 14.43)

**Files:**
- Create: `src/aruna/decision/final.py`
- Create: `tests/test_decision_final.py`
- Modify: `src/aruna/decision/__init__.py`

**Interfaces:**
- Consumes: `aruna.decision.score.Arah` (`LONG` / `SHORT` / `NO_SIGNAL`),
  `aruna.decision.timing.Timing`, `aruna.decision.timing.Rencana`.
- Produces:
  - `FinalError(ValueError)`
  - `TERLARANG: frozenset[str]` — token yang tidak boleh jadi keputusan final.
  - `finalize(raw: object, *, timing: Timing | None = None) -> Rencana`
  - `arah_dari(raw: object) -> Arah`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 14.2 dan 14.43: WAIT bukan keputusan.

Operator yang menerima "WAIT" tetap tidak tahu harus berbuat apa. Yang boleh
menunggu adalah *waktu masuknya* - keputusannya sendiri harus LONG, SHORT, atau
NO SIGNAL. Bedanya bukan tata bahasa: "WAIT" menyerahkan kembali pertanyaannya
kepada operator, sedangkan "LONG, tunggu pullback" menjawabnya dan menambahkan
syarat.
"""

from __future__ import annotations

import pytest

from aruna.decision.final import TERLARANG, FinalError, arah_dari, finalize
from aruna.decision.score import Arah
from aruna.decision.timing import Timing


class TestBentukKeputusan:
    @pytest.mark.parametrize(
        ("masukan", "arah"),
        [
            ("BUY", Arah.LONG),
            ("LONG", Arah.LONG),
            ("SELL", Arah.SHORT),
            ("SHORT", Arah.SHORT),
            ("NO_SIGNAL", Arah.NO_SIGNAL),
            ("NO SIGNAL", Arah.NO_SIGNAL),
        ],
    )
    def test_arah_yang_dikenali(self, masukan: str, arah: Arah) -> None:
        assert arah_dari(masukan) is arah

    def test_wait_ditolak_bukan_diterjemahkan(self) -> None:
        """Menerjemahkan WAIT diam-diam menjadi NO SIGNAL akan menyembunyikan
        lapisan yang masih mengeluarkan penundaan. Yang dibutuhkan adalah
        kesalahan yang terlihat, supaya pemanggilnya diperbaiki."""
        with pytest.raises(FinalError):
            arah_dari("WAIT")

    def test_flat_juga_ditolak(self) -> None:
        """``side='FLAT'`` adalah bentuk WAIT yang lain di jalur futures, dan
        ia truthy - kelas kesalahan yang sudah empat kali muncul di sistem
        ini."""
        with pytest.raises(FinalError):
            arah_dari("FLAT")

    def test_yang_tidak_dikenali_ditolak(self) -> None:
        with pytest.raises(FinalError):
            arah_dari("MUNGKIN")

    def test_daftar_terlarangnya_tidak_kosong(self) -> None:
        assert "WAIT" in TERLARANG
        assert "FLAT" in TERLARANG


class TestPenundaanPindahKeTiming:
    def test_long_boleh_menunggu_pullback(self) -> None:
        """PASAL 14.43 memberi contohnya sendiri: Decision LONG, Entry Timing
        WAIT FOR PULLBACK."""
        r = finalize("BUY", timing=Timing.PULLBACK)

        assert r.decision is Arah.LONG
        assert r.timing is Timing.PULLBACK

    def test_no_signal_tidak_boleh_membawa_timing(self) -> None:
        """Waktu masuk untuk posisi yang tidak diambil adalah keterangan yang
        tidak menerangkan apa pun, dan ia terbaca sebagai ajakan."""
        with pytest.raises(FinalError):
            finalize("NO SIGNAL", timing=Timing.NOW)

    def test_arah_tanpa_timing_tetap_sah(self) -> None:
        assert finalize("SELL").decision is Arah.SHORT
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_final.py -q
```

Diharapkan: `ModuleNotFoundError: No module named 'aruna.decision.final'`.

- [ ] **Step 3: Tulis implementasi minimalnya**

```python
"""Bentuk keputusan final (PASAL 14.2, 14.43).

ARUNA wajib memberi keputusan yang jelas: **LONG, SHORT, atau NO SIGNAL**.
``WAIT`` dilarang - bukan karena katanya buruk, melainkan karena ia
mengembalikan pertanyaannya kepada operator. "Tunggu" tidak memberitahu apa pun
tentang apa yang sedang dilihat ARUNA.

Yang boleh menunggu adalah **waktu masuknya**. "LONG, tunggu pullback" adalah
keputusan plus syarat; "WAIT" adalah ketiadaan keputusan yang berpakaian
seperti keputusan.

Modul ini **menolak**, tidak menerjemahkan. Sebuah lapisan yang masih
mengeluarkan ``WAIT`` harus terlihat sebagai kesalahan, bukan diam-diam
dibetulkan di hilir - karena yang diam-diam dibetulkan tidak pernah diperbaiki.
"""

from __future__ import annotations

from aruna.decision.score import Arah
from aruna.decision.timing import Rencana, Timing


class FinalError(ValueError):
    """Keputusan final yang bukan LONG, SHORT, atau NO SIGNAL."""


#: Token yang pernah muncul sebagai "keputusan" di sistem ini dan tidak satu
#: pun menjawab pertanyaan operator.
#:
#: ``FLAT`` ikut di sini dengan sengaja: itu bentuk WAIT di jalur futures, dan
#: ia truthy - sebuah nilai yang ada, sah, dan artinya persis "tidak berarah".
TERLARANG: frozenset[str] = frozenset({"WAIT", "FLAT", "HOLD", "NETRAL"})

_PETA: dict[str, Arah] = {
    "BUY": Arah.LONG,
    "LONG": Arah.LONG,
    "SELL": Arah.SHORT,
    "SHORT": Arah.SHORT,
    "NO_SIGNAL": Arah.NO_SIGNAL,
    "NO SIGNAL": Arah.NO_SIGNAL,
}


def arah_dari(raw: object) -> Arah:
    """Ubah apa pun yang dikeluarkan lapisan bawah menjadi satu dari tiga."""
    if isinstance(raw, Arah):
        return raw
    teks = str(getattr(raw, "value", raw) or "").strip().upper()
    if teks in TERLARANG:
        raise FinalError(
            f"{teks!r} bukan keputusan final - PASAL 14.43 hanya mengizinkan "
            "LONG, SHORT, atau NO SIGNAL. Penundaan masuk ke entry timing."
        )
    if teks not in _PETA:
        raise FinalError(f"keputusan tidak dikenali: {teks!r}")
    return _PETA[teks]


def finalize(raw: object, *, timing: Timing | None = None) -> Rencana:
    """Keputusan final beserta waktu masuknya, kalau ada arahnya."""
    arah = arah_dari(raw)
    if arah is Arah.NO_SIGNAL and timing is not None:
        raise FinalError(
            "NO SIGNAL tidak punya waktu masuk - posisinya tidak diambil"
        )
    return Rencana(decision=arah, timing=timing)


__all__ = ["TERLARANG", "FinalError", "arah_dari", "finalize"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_final.py -q
```

- [ ] **Step 5: Ekspor dari paketnya**

Di `src/aruna/decision/__init__.py`, tambahkan import dan masukkan
`"TERLARANG"`, `"FinalError"`, `"arah_dari"`, `"finalize"` ke `__all__`
(daftarnya urut abjad — sisipkan di tempatnya, jangan ditempel di akhir).

- [ ] **Step 6: Cabut-uji**

Ganti `if teks in TERLARANG:` menjadi `if False:`. Jalankan
`tests/test_decision_final.py`. Harus MERAH pada
`test_wait_ditolak_bukan_diterjemahkan`. Kembalikan barisnya.

---

## Task 2: Integrasi Phase 11 / 12 / 13 (PASAL 14.13–14.15, 14.39–14.41)

**Files:**
- Create: `src/aruna/decision/integration.py`
- Create: `tests/test_decision_integration.py`
- Modify: `src/aruna/decision/__init__.py`

**Interfaces:**
- Consumes: tidak ada dari tugas sebelumnya.
- Produces:
  - `Fase(StrEnum)` — `SEBELAS`, `DUA_BELAS`, `TIGA_BELAS`
  - `Masukan(StrEnum)` — satu anggota per baris di PASAL 14.39/14.40/14.41
  - `WAJIB: dict[Fase, tuple[Masukan, ...]]`
  - `Kelengkapan` dataclass: `.hadir`, `.hilang`, `.pct`
  - `periksa(tersedia: Mapping[Masukan, bool]) -> Kelengkapan`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 14.39-14.41: apa yang WAJIB dibaca dari tiga fase sebelumnya.

Ketiga pasal itu berupa daftar - bukan rumus. Nilainya bukan pada menghitung
sesuatu, melainkan pada membuat "lapisan ini tidak terbaca" menjadi angka yang
muncul di log, bukan ketiadaan yang tidak ada yang menyadarinya.

Ini keluarga cacat yang paling sering muncul di sistem ini: kode yang ditulis,
diekspor, diuji, dan tidak pernah dilewati jalur hidup. Daftar ini yang
membuatnya terlihat.
"""

from __future__ import annotations

from aruna.decision.integration import (
    WAJIB,
    Fase,
    Masukan,
    periksa,
)


class TestDaftarnya:
    def test_tiga_fase_semuanya_punya_daftar(self) -> None:
        assert set(WAJIB) == set(Fase)

    def test_tidak_ada_daftar_yang_kosong(self) -> None:
        """Fase tanpa masukan wajib berarti fase yang boleh diabaikan, dan
        tidak satu pun dari ketiganya begitu."""
        for fase, daftar in WAJIB.items():
            assert daftar, fase

    def test_risk_score_wajib_dari_phase_13(self) -> None:
        assert Masukan.RISK_SCORE in WAJIB[Fase.TIGA_BELAS]

    def test_signal_quality_wajib_dari_phase_11(self) -> None:
        assert Masukan.SIGNAL_QUALITY in WAJIB[Fase.SEBELAS]

    def test_strategy_performance_wajib_dari_phase_12(self) -> None:
        assert Masukan.STRATEGY_PERFORMANCE in WAJIB[Fase.DUA_BELAS]

    def test_setiap_masukan_dimiliki_tepat_satu_fase(self) -> None:
        """Masukan yang muncul di dua daftar akan dihitung dua kali, dan
        kelengkapannya jadi angka yang tidak berarti."""
        semua = [m for daftar in WAJIB.values() for m in daftar]

        assert len(semua) == len(set(semua))

    def test_setiap_anggota_enum_terpakai(self) -> None:
        """Anggota yang tidak masuk daftar mana pun tidak pernah diperiksa -
        ia ada di kode dan tidak ada di pengukuran."""
        semua = {m for daftar in WAJIB.values() for m in daftar}

        assert semua == set(Masukan)


class TestKelengkapan:
    def _semua(self, nilai: bool) -> dict[Masukan, bool]:
        return {m: nilai for m in Masukan}

    def test_semuanya_hadir(self) -> None:
        hasil = periksa(self._semua(True))

        assert hasil.hilang == ()
        assert hasil.pct == 100

    def test_semuanya_hilang(self) -> None:
        hasil = periksa(self._semua(False))

        assert hasil.hadir == ()
        assert hasil.pct == 0

    def test_yang_tidak_disebut_dihitung_hilang(self) -> None:
        """Masukan yang tidak dilaporkan sama sekali bukan masukan yang hadir.
        Menganggapnya hadir akan membuat kelengkapan terlihat penuh justru pada
        pemanggil yang paling sedikit melapor."""
        hasil = periksa({})

        assert hasil.pct == 0
        assert len(hasil.hilang) == len(Masukan)

    def test_sebagian(self) -> None:
        tersedia = self._semua(False)
        tersedia[Masukan.RISK_SCORE] = True
        hasil = periksa(tersedia)

        assert hasil.hadir == (Masukan.RISK_SCORE,)
        assert 0 < hasil.pct < 100

    def test_urutannya_tetap(self) -> None:
        """Laporan yang urutannya berubah tiap pemanggilan tidak bisa
        dibandingkan antar tick."""
        a = periksa(self._semua(False)).hilang
        b = periksa(self._semua(False)).hilang

        assert a == b
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_integration.py -q
```

- [ ] **Step 3: Tulis implementasinya**

```python
"""Apa yang wajib dibaca dari Phase 11, 12, dan 13 (PASAL 14.39-14.41).

PASAL 14.13, 14.14, dan 14.15 menjelaskan *cara* memakainya; PASAL 14.39-14.41
mendaftar *apa*-nya. Modul ini memuat daftarnya dan tidak menghitung apa pun -
angka-angkanya lahir di fasenya masing-masing, dan menghitung ulang di sini
akan menghasilkan dua sumber yang bisa berselisih.

Gunanya satu: membuat "lapisan ini tidak terbaca" menjadi angka. Sebuah fase
yang tidak pernah sampai ke keputusan tidak meninggalkan jejak apa pun kalau
tidak ada yang mendaftarnya, dan yang tidak terdaftar tidak pernah ditanyakan.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Fase(StrEnum):
    SEBELAS = "PHASE 11"
    DUA_BELAS = "PHASE 12"
    TIGA_BELAS = "PHASE 13"


class Masukan(StrEnum):
    """Nilainya data - jangan diterjemahkan.

    Satu anggota per baris di PASAL 14.39, 14.40, dan 14.41, dieja apa adanya.
    """

    # PASAL 14.39 - Phase 11
    SIGNAL_QUALITY = "SIGNAL_QUALITY"
    AGENT_RELIABILITY = "AGENT_RELIABILITY"
    MARKET_REGIME = "MARKET_REGIME"
    DATA_FRESHNESS = "DATA_FRESHNESS"
    ANOMALY_DETECTION = "ANOMALY_DETECTION"
    CONFIDENCE_CALIBRATION = "CONFIDENCE_CALIBRATION"
    AGENT_ACCOUNTABILITY = "AGENT_ACCOUNTABILITY"

    # PASAL 14.40 - Phase 12
    PATTERN_DISCOVERY = "PATTERN_DISCOVERY"
    STRATEGY_PERFORMANCE = "STRATEGY_PERFORMANCE"
    AGENT_SPECIALIZATION = "AGENT_SPECIALIZATION"
    CHAMPION = "CHAMPION"
    CHALLENGER = "CHALLENGER"
    WALK_FORWARD = "WALK_FORWARD"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    DRIFT_DETECTION = "DRIFT_DETECTION"
    LEARNING_RESULTS = "LEARNING_RESULTS"

    # PASAL 14.41 - Phase 13
    RISK_SCORE = "RISK_SCORE"
    RISK_REWARD = "RISK_REWARD"
    SL_QUALITY = "SL_QUALITY"
    TP_QUALITY = "TP_QUALITY"
    LEVERAGE_ANALYSIS = "LEVERAGE_ANALYSIS"
    LIQUIDATION_RISK = "LIQUIDATION_RISK"
    CORRELATION_RISK = "CORRELATION_RISK"
    EXPOSURE = "EXPOSURE"
    VOLATILITY = "VOLATILITY"
    NEWS_RISK = "NEWS_RISK"
    DAILY_RISK_BUDGET = "DAILY_RISK_BUDGET"


WAJIB: dict[Fase, tuple[Masukan, ...]] = {
    Fase.SEBELAS: (
        Masukan.SIGNAL_QUALITY,
        Masukan.AGENT_RELIABILITY,
        Masukan.MARKET_REGIME,
        Masukan.DATA_FRESHNESS,
        Masukan.ANOMALY_DETECTION,
        Masukan.CONFIDENCE_CALIBRATION,
        Masukan.AGENT_ACCOUNTABILITY,
    ),
    Fase.DUA_BELAS: (
        Masukan.PATTERN_DISCOVERY,
        Masukan.STRATEGY_PERFORMANCE,
        Masukan.AGENT_SPECIALIZATION,
        Masukan.CHAMPION,
        Masukan.CHALLENGER,
        Masukan.WALK_FORWARD,
        Masukan.OUT_OF_SAMPLE,
        Masukan.DRIFT_DETECTION,
        Masukan.LEARNING_RESULTS,
    ),
    Fase.TIGA_BELAS: (
        Masukan.RISK_SCORE,
        Masukan.RISK_REWARD,
        Masukan.SL_QUALITY,
        Masukan.TP_QUALITY,
        Masukan.LEVERAGE_ANALYSIS,
        Masukan.LIQUIDATION_RISK,
        Masukan.CORRELATION_RISK,
        Masukan.EXPOSURE,
        Masukan.VOLATILITY,
        Masukan.NEWS_RISK,
        Masukan.DAILY_RISK_BUDGET,
    ),
}

#: Urutan tetap untuk laporan - supaya dua tick bisa dibandingkan.
_URUT: tuple[Masukan, ...] = tuple(
    m for fase in Fase for m in WAJIB[fase]
)


@dataclass(frozen=True, slots=True)
class Kelengkapan:
    hadir: tuple[Masukan, ...]
    hilang: tuple[Masukan, ...]

    @property
    def pct(self) -> int:
        total = len(self.hadir) + len(self.hilang)
        if not total:
            return 0
        return round(len(self.hadir) * 100 / total)


def periksa(tersedia: Mapping[Masukan, bool]) -> Kelengkapan:
    """Mana yang benar-benar sampai ke keputusan, dan mana yang tidak.

    Yang tidak dilaporkan dihitung **hilang**, bukan hadir: pemanggil yang
    paling sedikit melapor justru yang paling perlu terlihat.
    """
    hadir = tuple(m for m in _URUT if tersedia.get(m, False))
    hilang = tuple(m for m in _URUT if not tersedia.get(m, False))
    return Kelengkapan(hadir=hadir, hilang=hilang)


__all__ = ["WAJIB", "Fase", "Kelengkapan", "Masukan", "periksa"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_integration.py -q
```

- [ ] **Step 5: Ekspor dari paketnya**

Tambahkan ke `__init__.py`: `Fase`, `Kelengkapan`, `Masukan`, dan `periksa`
(ekspor `WAJIB` dari `hierarchy` sudah ada — **beri nama lain**:
`from aruna.decision.integration import WAJIB as WAJIB_INTEGRASI`. Dua nama
`WAJIB` di satu namespace akan saling menimpa diam-diam.)

- [ ] **Step 6: Cabut-uji**

Ubah `tersedia.get(m, False)` menjadi `tersedia.get(m, True)` di baris `hilang`.
Jalankan testnya. Harus MERAH pada `test_yang_tidak_disebut_dihitung_hilang`.
Kembalikan.

---

## Task 3: Urutan lengkap sebagai satu daftar (PASAL 14.42)

**Files:**
- Create: `src/aruna/decision/engine.py`
- Create: `tests/test_decision_engine.py`
- Modify: `src/aruna/decision/__init__.py`

**Interfaces:**
- Consumes: `aruna.decision.hierarchy.Tahap` (14 tahap PASAL 14.3).
- Produces:
  - `Langkah(StrEnum)` — 23 langkah PASAL 14.42, urut.
  - `ALUR: tuple[Langkah, ...]`
  - `SESUDAH_TERBIT: frozenset[Langkah]`
  - `posisi(langkah: Langkah) -> int`
  - `sebelum(a: Langkah, b: Langkah) -> bool`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""PASAL 14.42: alur lengkap, dan bahwa ia sejalan dengan PASAL 14.3.

Dua pasal menyebut urutan yang sama dengan rincian berbeda - 14.3 empat belas
tahap, 14.42 dua puluh tiga langkah. Kalau keduanya boleh berkembang sendiri,
suatu saat sistem ini akan punya dua urutan resmi yang berselisih, dan tidak
ada yang tahu mana yang dijalankan.
"""

from __future__ import annotations

from aruna.decision.engine import ALUR, SESUDAH_TERBIT, Langkah, posisi, sebelum
from aruna.decision.hierarchy import Tahap


class TestAlurnya:
    def test_setiap_langkah_muncul_sekali(self) -> None:
        assert len(ALUR) == len(set(ALUR)) == len(Langkah)

    def test_risk_sebelum_keputusan_final(self) -> None:
        """PASAL 14.3: tidak boleh melewati risk validation."""
        assert sebelum(Langkah.RISK_ANALYSIS, Langkah.FINAL_DECISION)

    def test_validasi_data_paling_awal(self) -> None:
        assert posisi(Langkah.DATA_VALIDATION) < posisi(Langkah.MARKET_REGIME)

    def test_telegram_sesudah_keputusan_final(self) -> None:
        """Pesan yang disusun sebelum keputusannya selesai adalah pesan yang
        bisa mendahului perubahan keputusannya."""
        assert sebelum(Langkah.FINAL_DECISION, Langkah.TELEGRAM)

    def test_pembelajaran_paling_akhir(self) -> None:
        for fase in (Langkah.PHASE_11, Langkah.PHASE_12, Langkah.PHASE_13):
            assert sebelum(Langkah.OUTCOME, fase)


class TestSejalanDenganPasal143:
    """Penjaga antara dua pasal yang menyebut urutan yang sama."""

    #: Tahap PASAL 14.3 -> langkah PASAL 14.42 yang mewakilinya.
    PETA = {
        Tahap.DATA_VALIDITY: Langkah.DATA_VALIDATION,
        Tahap.DATA_FRESHNESS: Langkah.DATA_FRESHNESS,
        Tahap.MARKET_REGIME: Langkah.MARKET_REGIME,
        Tahap.MTF: Langkah.MULTI_TIMEFRAME,
        Tahap.AGENTS: Langkah.AGENT_ANALYSIS,
        Tahap.PROTEST: Langkah.PROTEST,
        Tahap.COUNCIL: Langkah.COUNCIL,
        Tahap.QUALITY: Langkah.SIGNAL_QUALITY,
        Tahap.STRATEGY: Langkah.HISTORICAL_PERFORMANCE,
        Tahap.RISK: Langkah.RISK_ANALYSIS,
        Tahap.RR: Langkah.RR,
        Tahap.INVALIDATION: Langkah.INVALIDATION,
        Tahap.HORIZON: Langkah.DECISION_HORIZON,
        Tahap.FINAL: Langkah.FINAL_DECISION,
    }

    def test_setiap_tahap_punya_langkahnya(self) -> None:
        assert set(self.PETA) == set(Tahap)

    def test_urutannya_tidak_bertentangan(self) -> None:
        """Kalau 14.3 bilang A sebelum B, 14.42 tidak boleh bilang sebaliknya."""
        tahap = list(Tahap)
        for i in range(len(tahap) - 1):
            a, b = self.PETA[tahap[i]], self.PETA[tahap[i + 1]]
            assert sebelum(a, b), f"{a} harus sebelum {b}"


class TestSesudahTerbit:
    def test_telegram_dan_sesudahnya(self) -> None:
        assert Langkah.TELEGRAM in SESUDAH_TERBIT
        assert Langkah.OUTCOME in SESUDAH_TERBIT

    def test_keputusan_finalnya_sendiri_bukan(self) -> None:
        """PASAL 14.24: yang tidak boleh diubah adalah signal yang SUDAH
        terbit. Memasukkan keputusan finalnya sendiri ke daftar ini akan
        membekukan angka sebelum ia selesai dihitung."""
        assert Langkah.FINAL_DECISION not in SESUDAH_TERBIT

    def test_tidak_semuanya_sesudah_terbit(self) -> None:
        assert len(SESUDAH_TERBIT) < len(Langkah)
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_engine.py -q
```

- [ ] **Step 3: Tulis implementasinya**

```python
"""Alur lengkap satu keputusan (PASAL 14.42).

PASAL 14.3 menyebut empat belas tahap; PASAL 14.42 menyebut dua puluh tiga
langkah dari data mentah sampai pembelajaran. Keduanya urutan yang sama dengan
kekasaran berbeda, dan modul ini memuat yang lebih halus.

Modul ini **tidak menjalankan** apa pun. Ia daftar berurut, dan gunanya adalah
menjadikan "langkah ini dilewati" sebagai pertanyaan yang bisa dijawab -
:mod:`aruna.decision.observe` yang menjawabnya dari keputusan yang sudah jadi.

Sebuah mesin yang benar-benar menjalankan urutan ini akan menjadi gerbang
ketiga di jalur yang sudah punya dua, dan dua-duanya terukur akan membungkam
ARUNA hampir sepenuhnya kalau dipasang apa adanya.
"""

from __future__ import annotations

from enum import StrEnum


class Langkah(StrEnum):
    """Nilainya data - jangan diterjemahkan. Urutan deklarasinya = urutan alur."""

    MARKET_DATA = "MARKET DATA"
    DATA_VALIDATION = "DATA VALIDATION"
    DATA_FRESHNESS = "DATA FRESHNESS"
    MARKET_REGIME = "MARKET REGIME"
    MULTI_TIMEFRAME = "MULTI-TIMEFRAME"
    STRATEGY_MATCH = "STRATEGY MATCH"
    AGENT_ANALYSIS = "AGENT ANALYSIS"
    PROTEST = "PROTEST"
    COUNTER_ARGUMENT = "COUNTER ARGUMENT"
    VETO_CHECK = "VETO CHECK"
    COUNCIL = "COUNCIL"
    SIGNAL_QUALITY = "SIGNAL QUALITY"
    HISTORICAL_PERFORMANCE = "HISTORICAL PERFORMANCE"
    RISK_ANALYSIS = "RISK ANALYSIS"
    RR = "R/R"
    SL_TP_VALIDATION = "SL / TP VALIDATION"
    INVALIDATION = "INVALIDATION"
    EXPIRATION = "EXPIRATION"
    DECISION_HORIZON = "DECISION HORIZON"
    FINAL_QUALITY_GATE = "FINAL QUALITY GATE"
    FINAL_DECISION = "FINAL DECISION"
    TELEGRAM = "TELEGRAM"
    OUTCOME = "OUTCOME"
    PHASE_11 = "PHASE 11"
    PHASE_12 = "PHASE 12"
    PHASE_13 = "PHASE 13"


ALUR: tuple[Langkah, ...] = tuple(Langkah)

_INDEKS: dict[Langkah, int] = {l: i for i, l in enumerate(ALUR)}

#: Langkah yang terjadi **sesudah** signal terbit.
#:
#: PASAL 14.24 dan §12.1: apa pun yang dihitung di sini tidak boleh mengubah
#: direction, entry, SL, TP, atau confidence yang sudah dikirim. Batas ini
#: dieja supaya pembaca berikutnya tidak perlu menebaknya dari urutan.
SESUDAH_TERBIT: frozenset[Langkah] = frozenset({
    Langkah.TELEGRAM,
    Langkah.OUTCOME,
    Langkah.PHASE_11,
    Langkah.PHASE_12,
    Langkah.PHASE_13,
})


def posisi(langkah: Langkah) -> int:
    return _INDEKS[langkah]


def sebelum(a: Langkah, b: Langkah) -> bool:
    return _INDEKS[a] < _INDEKS[b]


__all__ = ["ALUR", "SESUDAH_TERBIT", "Langkah", "posisi", "sebelum"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

Kalau `test_urutannya_tidak_bertentangan` merah, **jangan ubah testnya** —
itu tandanya urutan `Langkah` benar-benar berselisih dengan `Tahap`, dan yang
salah adalah urutan deklarasinya.

- [ ] **Step 5: Ekspor dari paketnya**

`ALUR`, `Langkah`, `SESUDAH_TERBIT`, `posisi`, `sebelum`.

- [ ] **Step 6: Cabut-uji**

Tukar urutan `RISK_ANALYSIS` dan `FINAL_DECISION` di deklarasi enum. Testnya
harus MERAH pada `test_risk_sebelum_keputusan_final`. Kembalikan.

---

## Task 4: Sambungkan `final` ke jalur hidup (PASAL 14.2, 14.43)

**Files:**
- Modify: `src/aruna/futures/notify.py`
- Create: `tests/test_decision_tersambung.py`

**Interfaces:**
- Consumes: `finalize`, `arah_dari`, `FinalError` dari Task 1.
- Produces: baris `KEPUTUSAN FINAL:` di pesan futures.

**Latar:** hari ini `_alert` mencetak `SIDE: LONG`. `SIDE` adalah sisi posisi,
bukan keputusan — dan rencana `WAIT` menyimpan `side='FLAT'`. Rencana WAIT
memang tidak pernah dikirim (`_send` menyaring `PlanVerdict.PLAN` saja), jadi
tidak ada pelanggaran yang sampai ke operator hari ini. Yang belum ada adalah
**penjaganya**: kalau suatu saat penyaring itu dilonggarkan, `FLAT` akan
terbaca operator sebagai keputusan.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""Penjaga penyambungan: modul yang ditulis, diuji, dan tidak pernah dipanggil.

Keluarga cacat ini sudah berkali-kali muncul di sistem ini - bagian PENILAIAN
pernah hilang dari pesan tanpa error dan tanpa log, dan seluruh unit testnya
tetap hijau. Berkas ini menguji **pemanggilnya**, bukan yang dipanggil.
"""

from __future__ import annotations

import inspect

import pytest


class TestFinalDipakaiPesanFutures:
    def test_notify_mengimpor_final(self) -> None:
        from aruna.futures import notify

        sumber = inspect.getsource(notify)
        assert "from aruna.decision.final import" in sumber

    def test_pesannya_membawa_keputusan_final(self) -> None:
        from tests.test_futures_notify_pasal1426 import NOW, FakePlan, note
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), NOW, note=note())

        assert "KEPUTUSAN FINAL:" in teks
        assert "LONG" in teks

    def test_flat_tidak_pernah_tercetak_sebagai_keputusan(self) -> None:
        """``side='FLAT'`` ada, truthy, dan artinya persis "tidak berarah"."""
        from tests.test_futures_notify_pasal1426 import (
            NOW, FakePlan, FakeSide, note,
        )
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(side=FakeSide("FLAT")), NOW, note=note())

        assert "KEPUTUSAN FINAL: FLAT" not in teks
        assert "KEPUTUSAN FINAL: WAIT" not in teks

    def test_kegagalannya_tidak_menghentikan_pesan(self) -> None:
        """Yang hilang saat arahnya tak dikenali adalah satu baris keterangan -
        bukan pesan yang membawa entry dan stop."""
        from tests.test_futures_notify_pasal1426 import (
            NOW, FakePlan, FakeSide, note,
        )
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(side=FakeSide("MUNGKIN")), NOW, note=note())

        assert "64120" in teks
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_tersambung.py -q
```

- [ ] **Step 3: Tambahkan `_keputusan_final` di `notify.py`**

Letakkan di dekat `_invalidation`, ikuti bentuk penjaganya yang sudah ada
(fungsi memulangkan `list[str]`, pengecualian dicatat lalu dikembalikan
kosong).

```python
def _keputusan_final(plan: Any) -> list[str]:
    """PASAL 14.2 dan 14.43: LONG, SHORT, atau NO SIGNAL - tidak pernah WAIT.

    ``SIDE`` yang sudah dicetak adalah sisi posisi, bukan keputusan. Keduanya
    sama pada rencana yang terbit, dan berbeda persis pada rencana yang tidak -
    di situ ``side`` bernilai ``FLAT``, sebuah nilai yang ada dan truthy dan
    artinya "tidak berarah".
    """
    from aruna.decision.final import FinalError, arah_dari

    try:
        arah = arah_dari(getattr(plan, "side", None))
    except FinalError as exc:
        log.warning("futures.final_decision_unknown", sebab=str(exc))
        return []
    except Exception:  # noqa: BLE001 - satu baris keterangan, bukan pesannya
        log.exception("futures.final_decision_failed")
        return []
    return [f"KEPUTUSAN FINAL: {arah.value}"]
```

Panggil dari `_alert` tepat sebelum baris `SIDE:` disusun, dengan pola
`baris.extend(_keputusan_final(plan))` yang sudah dipakai blok lain.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_tersambung.py tests/test_futures_notify_pasal1426.py tests/test_futures_notify.py -q
```

- [ ] **Step 5: Cabut-uji**

Hapus baris `baris.extend(_keputusan_final(plan))` dari `_alert`. Testnya harus
MERAH pada `test_pesannya_membawa_keputusan_final`. Kembalikan.

---

## Task 5: Sambungkan `timing` ke pesan (PASAL 14.19, 14.20, 14.43)

**Files:**
- Modify: `src/aruna/futures/notify.py`
- Modify: `tests/test_decision_tersambung.py`

**Interfaces:**
- Consumes: `aruna.decision.timing.Timing`, `finalize` dari Task 1.
- Produces: baris `ENTRY TIMING:` di pesan futures.

**Sumber datanya — jangan dikarang (§13.26).** Waktu masuk dibaca dari jarak
harga acuan ke entry yang direncanakan, yang keduanya sudah ada di `plan`:

- entry sama dengan harga acuan (dalam satu tick) → `Timing.NOW`
- entry lebih baik daripada acuan (lebih rendah untuk LONG, lebih tinggi untuk
  SHORT) → `Timing.PULLBACK`
- entry lebih buruk daripada acuan → `Timing.BREAKOUT`
- `reference_price` atau `entry` tidak ada → **tidak ada baris**, bukan tebakan

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestTimingDiPesan:
    def _plan(self, **kw):
        from tests.test_futures_notify_pasal1426 import FakePlan

        return FakePlan(**kw)

    def test_entry_di_bawah_acuan_untuk_long_adalah_pullback(self) -> None:
        from decimal import Decimal

        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(self._plan(
            reference_price=Decimal("64500"), entry=Decimal("64120"),
        )))

        assert Timing.PULLBACK.value in baris

    def test_entry_sama_dengan_acuan_adalah_masuk_sekarang(self) -> None:
        from decimal import Decimal

        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(self._plan(
            reference_price=Decimal("64120"), entry=Decimal("64120"),
        )))

        assert Timing.NOW.value in baris

    def test_short_dibalik(self) -> None:
        """Salah tanda di sini memberi operator waktu masuk yang berlawanan
        dengan posisinya."""
        from decimal import Decimal

        from tests.test_futures_notify_pasal1426 import FakeSide
        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(self._plan(
            side=FakeSide("SHORT"),
            reference_price=Decimal("63800"), entry=Decimal("64120"),
        )))

        assert Timing.PULLBACK.value in baris

    def test_tanpa_acuan_tidak_menebak(self) -> None:
        """§13.26: kalau datanya tidak ada, tidak ada barisnya - bukan
        MASUK SEKARANG yang kebetulan terbaca seperti ajakan."""
        from aruna.futures.notify import _entry_timing

        assert _entry_timing(self._plan(reference_price=None)) == []
```

Tambahkan `reference_price: Decimal | None = Decimal("64120")` ke `FakePlan`
di `tests/test_futures_notify_pasal1426.py` (bidang ini ada di `FuturesPlan`
yang sungguhan — periksa `src/aruna/futures/plan.py:146`).

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis `_entry_timing` di `notify.py`**

```python
def _entry_timing(plan: Any) -> list[str]:
    """PASAL 14.19/14.20: waktu masuk, bukan keputusan.

    PASAL 14.43 memisahkan keduanya dengan tegas: keputusannya LONG, waktu
    masuknya boleh menunggu. Angkanya dibaca dari jarak entry ke harga acuan -
    keduanya sudah ada di rencana, dan tidak ada yang dikarang di sini (§13.26).
    """
    from aruna.decision.final import FinalError, arah_dari
    from aruna.decision.score import Arah
    from aruna.decision.timing import Timing

    acuan = getattr(plan, "reference_price", None)
    entry = getattr(plan, "entry", None)
    if acuan is None or entry is None:
        return []
    try:
        arah = arah_dari(getattr(plan, "side", None))
    except FinalError:
        return []
    if arah is Arah.NO_SIGNAL:
        return []

    tick = getattr(plan, "tick_size", None) or Decimal(0)
    selisih = entry - acuan
    if abs(selisih) <= tick:
        timing = Timing.NOW
    elif (selisih < 0) is (arah is Arah.LONG):
        timing = Timing.PULLBACK
    else:
        timing = Timing.BREAKOUT
    return [f"ENTRY TIMING: {timing.value}"]
```

Panggil dari `_alert` tepat sesudah `_keputusan_final`.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Balik syaratnya menjadi `(selisih < 0) is not (arah is Arah.LONG)`. Harus MERAH
pada `test_short_dibalik` **dan** `test_entry_di_bawah_acuan_untuk_long_adalah_pullback`.
Kalau hanya salah satu yang merah, testnya belum menguji kedua arah.

---

## Task 6: Sambungkan `trail` ke penyimpanan (PASAL 14.30)

**Files:**
- Modify: `src/aruna/futures/service.py`
- Modify: `tests/test_decision_tersambung.py`

**Interfaces:**
- Consumes: `aruna.decision.trail.Jejak`, `record`, `require_reconstructable`.
- Produces: `log.info("decision.trail", ...)` satu baris per rencana terbit.

**Kenapa log, bukan tabel:** dua puluh tiga bidang per rencana per lima belas
menit adalah 1.920 baris sehari. §26 melarang INSERT tiap tick ke SQL, dan
tabel baru menuntut migrasi plus pembacanya. Log terstruktur sudah punya
keduanya. Kalau nanti terbukti dipakai, memindahkannya ke tabel adalah
pekerjaan yang jelas — sebaliknya tidak.

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestJejakTercatat:
    def test_service_memakai_trail(self) -> None:
        import inspect

        from aruna.futures import service

        assert "from aruna.decision.trail import" in inspect.getsource(service)

    def test_jejaknya_lengkap_untuk_rencana_berarah(self) -> None:
        """PASAL 14.30: keputusan harus bisa direkonstruksi. Jejak yang bolong
        adalah keputusan yang tidak bisa diperiksa ulang - dan §11.21 melarang
        mengubah signal lama, jadi kesempatan mencatatnya cuma sekali."""
        from aruna.decision.score import Arah
        from aruna.decision.trail import record, require_reconstructable, required_fields

        nilai = {j: "x" for j in required_fields(Arah.LONG)}

        assert require_reconstructable(record(Arah.LONG, nilai))

    def test_jejak_bolong_ditolak(self) -> None:
        import pytest

        from aruna.decision.score import Arah
        from aruna.decision.trail import (
            TrailError, record, require_reconstructable, required_fields,
        )

        wajib = required_fields(Arah.LONG)
        nilai = {j: "x" for j in wajib[:-1]}

        with pytest.raises(TrailError):
            require_reconstructable(record(Arah.LONG, nilai))
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tambahkan `_catat_jejak` di `futures/service.py`**

Letakkan di sebelah `observe_decision` yang sudah ada dan panggil dari tempat
yang sama. Isi tiap `Jejak` dari objek yang sudah ada di tangan; yang tidak
tersedia diisi `"UNKNOWN"` (§13.26), **tidak** dikarang dan **tidak**
dikosongkan diam-diam.

```python
def _catat_jejak(plan: Any, *, context: Any, verdict: Any, note: Any) -> None:
    """PASAL 14.30: satu baris yang cukup untuk menyusun ulang keputusannya."""
    from aruna.decision.final import FinalError, arah_dari
    from aruna.decision.trail import Jejak, record, required_fields

    try:
        arah = arah_dari(getattr(plan, "side", None))
    except FinalError:
        return
    except Exception:  # noqa: BLE001
        log.exception("decision.trail_failed")
        return

    sumber: dict[Jejak, str] = {
        Jejak.SIGNAL_ID: str(getattr(plan, "signal_id", "") or "UNKNOWN"),
        Jejak.TIMESTAMP: str(getattr(plan, "created_at", "") or "UNKNOWN"),
        Jejak.ASSET: str(getattr(plan, "symbol", "") or "UNKNOWN"),
        Jejak.MARKET: "FUTURES",
        Jejak.TIMEFRAMES: str(getattr(getattr(note, "lintas", None), "readings", "") or "UNKNOWN"),
        Jejak.REGIME: str(getattr(note, "regime", "") or "UNKNOWN"),
        Jejak.AGENT_VOTES: str(getattr(verdict, "split", "") or "UNKNOWN"),
        Jejak.AGENT_ARGUMENTS: str(getattr(verdict, "opinions", "") or "UNKNOWN"),
        Jejak.PROTESTS: str(getattr(verdict, "protest", "") or "UNKNOWN"),
        Jejak.VETO: str(getattr(verdict, "veto", "") or "UNKNOWN"),
        Jejak.COUNCIL_DECISION: str(getattr(getattr(verdict, "decision", None), "value", "") or "UNKNOWN"),
        Jejak.SIGNAL_QUALITY: str(getattr(note, "quality", "") or "UNKNOWN"),
        Jejak.CONFIDENCE: str(getattr(note, "confidence", "UNKNOWN")),
        Jejak.RISK_SCORE: str(getattr(note, "risk_readings", "") or "UNKNOWN"),
        Jejak.STRATEGY: str(getattr(context, "strategy", "") or "UNKNOWN"),
        Jejak.MODEL_VERSION: str(getattr(plan, "model_version", "") or "UNKNOWN"),
        Jejak.DECISION_SCORE: str(getattr(note, "decision_readings", "") or "UNKNOWN"),
        Jejak.FINAL_DECISION: arah.value,
        Jejak.ENTRY: str(getattr(plan, "entry", "") or "UNKNOWN"),
        Jejak.SL: str(getattr(plan, "stop", "") or "UNKNOWN"),
        Jejak.TP: str(getattr(plan, "target", "") or "UNKNOWN"),
        Jejak.INVALIDATION: str(getattr(getattr(plan, "stop_detail", None), "invalidation", "") or "UNKNOWN"),
        Jejak.EXPIRATION: str(getattr(plan, "horizon_hours", "") or "UNKNOWN"),
    }
    dipakai = {j: sumber[j] for j in required_fields(arah)}
    log.info("decision.trail", **{
        j.name.lower(): v for j, v in record(arah, dipakai).values
    })
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Hapus satu pasangan dari `sumber` (misalnya `Jejak.SL`). `required_fields`
akan melempar `KeyError` — itu bukan yang diuji. Ganti pendekatannya: ubah
`require_reconstructable` agar selalu memulangkan rekamannya, lalu pastikan
`test_jejak_bolong_ditolak` MERAH. Kembalikan.

---

## Task 7: Sambungkan `channel` ke pengirim (PASAL 14.38)

**Files:**
- Modify: `src/aruna/notify/result.py`
- Modify: `tests/test_decision_tersambung.py`

**Interfaces:**
- Consumes: `aruna.decision.channel.Jenis`, `allow`, `DILARANG`.
- Produces: setiap pengiriman melewati `allow(Jenis...)`.

PASAL 14.38 mendaftar apa yang **tidak** boleh dikirim: notifikasi mati, spam,
pengulangan tanpa kabar baru. `channel.allow` sudah memuat aturannya; yang
belum ada adalah pemanggilnya.

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestKanalDipakai:
    def test_result_memakai_channel(self) -> None:
        import inspect

        from aruna.notify import result

        assert "from aruna.decision.channel import" in inspect.getsource(result)

    def test_jenis_yang_dilarang_ditolak(self) -> None:
        import pytest

        from aruna.decision.channel import CATATAN_MATI, ChannelError, allow

        for nama in CATATAN_MATI:
            with pytest.raises(ChannelError):
                allow(nama)

    def test_signal_dan_hasil_diizinkan(self) -> None:
        from aruna.decision.channel import Jenis, allow

        assert allow(Jenis.SIGNAL) is Jenis.SIGNAL
        assert allow(Jenis.WIN) is Jenis.WIN
        assert allow(Jenis.LOSS) is Jenis.LOSS
```

Periksa lebih dulu bentuk `CATATAN_MATI` di `src/aruna/decision/channel.py` —
kalau isinya kalimat, bukan `Jenis`, sesuaikan testnya dengan bentuk yang
sungguhan, **jangan** ubah modulnya agar cocok dengan test.

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Panggil `allow` di `SignalNotifier._kirim` dan
  `ResultNotifier._kirim_hasil`**

```python
from aruna.decision.channel import Jenis, allow
...
allow(Jenis.SIGNAL)   # di _kirim
allow(Jenis.WIN if menang else Jenis.LOSS)   # di _kirim_hasil
```

`allow` melempar untuk jenis yang dilarang; biarkan melempar — pengiriman yang
dilarang PASAL 14.38 memang harus berhenti, bukan tercatat lalu tetap terkirim.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_decision_tersambung.py tests/test_hasil_terpublikasi.py -q
```

- [ ] **Step 5: Cabut-uji**

Hapus panggilan `allow(Jenis.SIGNAL)`. `test_result_memakai_channel` harus
MERAH. Kembalikan.

---

## Task 8: Sambungkan `silence` ke laporan harian (PASAL 14.27, 14.33)

> **BELUM DIKERJAKAN — 2026-08-20.** Diperiksa saat pelaksanaan dan ternyata
> lebih besar daripada yang tertulis di bawah. `silence.evaluate` menuntut
> `Diam(symbol, reason, move_pct)`, dan `move_pct` **tidak ada di mana pun**:
> `signals.withheld_reason` menyimpan alasannya, tapi signal yang ditahan tidak
> pernah diresolusi, jadi gerak harga sesudahnya belum pernah diukur. Yang
> dibutuhkan adalah query repositori baru yang menggabungkan `signals`
> (published=FALSE) → `signal_snapshots` (reference_price, resolves_at, aset) →
> `candles` (ekstrem selama horizon), plus siklus ujinya sendiri terhadap MySQL
> yang hidup.
>
> Dikerjakan setengah jalan akan lebih buruk daripada tidak sama sekali: sebuah
> `move_pct` yang salah hitung membuat "diam ARUNA benar 90%" - angka yang
> meyakinkan dan tidak berdasar, dan PASAL 14.33 justru melarang memakai angka
> ini untuk menurunkan ambang. Dipindahkan ke rencana berikutnya bersama empat
> modul yang masih diam.

**Files:**
- Modify: `src/aruna/upkeep/loop.py`
- Modify: `tests/test_decision_tersambung.py`

**Interfaces:**
- Consumes: `aruna.decision.silence.Diam`, `evaluate`, `GERAK_BERARTI_PCT`.
- Produces: bagian `NO SIGNAL` di laporan harian.

PASAL 14.33 meminta analisis peluang yang terlewat: berapa NO SIGNAL yang
ternyata benar, dan berapa yang melewatkan gerakan berarti. `silence.evaluate`
sudah menghitungnya dari daftar `Diam`; yang belum ada adalah yang menyusun
daftar itu dari penahanan yang sudah tercatat.

Sumber datanya: `signals.withheld_reason` + gerakan harga sesudahnya. Keduanya
sudah tersimpan. **Jangan** membuat tabel baru.

- [ ] **Step 1: Tulis test yang gagal**

```python
class TestDiamDilaporkan:
    def test_loop_memakai_silence(self) -> None:
        import inspect

        from aruna.upkeep import loop

        assert "from aruna.decision.silence import" in inspect.getsource(loop)

    def test_no_signal_yang_benar_dihitung(self) -> None:
        from decimal import Decimal

        from aruna.decision.silence import Diam, Vonis, evaluate

        lap = evaluate([
            Diam("BTCUSDT", "quality gate", Decimal("0.4")),
            Diam("ETHUSDT", "quality gate", Decimal("5.0")),
        ])

        assert lap.evidence.total == 2
        assert len(lap.missed) == 1

    def test_gerakan_yang_tidak_diketahui_bukan_benar(self) -> None:
        """§13.26: gerakan yang belum terukur adalah UNKNOWN. Menghitungnya
        sebagai "NO SIGNAL benar" akan membuat ARUNA terlihat selalu tepat
        justru pada hari yang datanya paling tipis."""
        from aruna.decision.silence import Diam, evaluate

        lap = evaluate([Diam("BTCUSDT", "quality gate", None)])

        assert lap.unknown == 1
```

Sesuaikan nama bidang (`lap.evidence.total`, `lap.unknown`) dengan bentuk
`Laporan` yang sungguhan di `src/aruna/decision/silence.py` sebelum menjalankan.

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Susun daftarnya di `upkeep/loop.py`**

Di fungsi yang menyusun laporan harian, kumpulkan penahanan hari itu dari
repositori signal, pasangkan dengan gerakan harga horizonnya, dan panggil
`evaluate`. Cetak hasilnya sebagai satu bagian pendek — jumlah, berapa yang
terlewat, dan alasan terbanyak.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Cabut-uji**

Hapus panggilan `evaluate`. Harus MERAH. Kembalikan.

---

## Task 9: Ruff, suite penuh, restart, ukur di produksi

**Files:** tidak ada yang diubah kecuali temuan.

- [ ] **Step 1: Ruff**

```bash
.\.venv\Scripts\python.exe -m ruff check src tests
```

- [ ] **Step 2: Suite penuh, SENDIRIAN**

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

Jangan menjalankan probe atau pytest lain bersamaan. Suite di latar plus probe
di depan menghasilkan kegagalan palsu yang menyamar jadi bug.

- [ ] **Step 3: Restart ARUNA**

```bash
taskkill /PID <pid cmd.exe ARUNA.bat> /T /F
```

lalu

```bash
powershell -Command "Start-Process cmd.exe -ArgumentList '/c','\"C:\laragon\www\aianalis\ARUNA.bat\"' -WindowStyle Minimized"
```

- [ ] **Step 4: Ukur dari log, bukan dari keyakinan**

Tunggu minimal dua `futures.tick` (interval 900 detik). Lalu hitung dari
`logs/aruna.log` sejak baris pertama sesudah restart:

- `futures.final_decision_failed` — harus 0
- `futures.final_decision_unknown` — kalau > 0, satu lapisan masih
  mengeluarkan arah yang tidak dikenali; kejar sumbernya
- `decision.trail` — harus muncul satu baris per rencana yang terbit
- `decision.trail_failed` — harus 0
- baris `level=error` mana pun — harus 0
- `Data truncated` — harus 0

- [ ] **Step 5: Laporkan apa adanya**

Kalau ada yang tidak nol, sebutkan angkanya. Jangan melaporkan "beres" atas
langkah yang belum terukur.

---

## Self-review

**1. Cakupan spec.** PASAL yang belum punya kode: 14.9, 14.10, 14.11, 14.12,
14.13, 14.14, 14.15, 14.39, 14.40, 14.41, 14.42, 14.43.

| PASAL | Tugas |
|---|---|
| 14.2, 14.43 | Task 1 (modul), Task 4 (sambung) |
| 14.13, 14.14, 14.15, 14.39, 14.40, 14.41 | Task 2 |
| 14.42 | Task 3 |
| 14.19, 14.20 | Task 5 |
| 14.30 | Task 6 |
| 14.38 | Task 7 |
| 14.27, 14.33 | Task 8 |

**Celah yang diketahui dan disebut, bukan disembunyikan:**

- **14.9–14.12 tidak punya tugas sendiri.** Debate, protest, veto, dan council
  sudah dibangun di Phase 5 dan Phase 6, dan `observe.py` sudah membaca
  keempatnya (`Tahap.AGENTS`, `PROTEST`, `COUNCIL`). Yang diminta Phase 14
  adalah keputusan final memakainya — Task 6 mencatat keempatnya ke jejak, dan
  di situlah buktinya muncul. Membangun ulang akan menghasilkan council kedua
  yang tidak sepakat dengan yang pertama.
- **Empat modul masih belum tersambung sesudah rencana ini:** `consistency`
  (14.35–14.37), `explanation` (14.29), `lifecycle` (14.23), `outcome` (14.31).
  Keempatnya butuh keadaan yang bertahan antar tick — keputusan sebelumnya per
  simbol — dan itu keputusan penyimpanan yang pantas dibahas terpisah, bukan
  diselipkan. **Rencana ini tidak menyelesaikan Phase 14 seluruhnya**; ia
  menutup dua belas PASAL tanpa kode dan menyambungkan empat dari delapan modul
  yang diam. Sisanya rencana kedua.

---

## Hasil pelaksanaan — 2026-08-20

**Selesai dan terbukti:** Task 1 sampai 7.

| Task | Isi | Cabut-uji |
|---|---|---|
| 1 | `decision/final.py` — PASAL 14.2, 14.43 | 3 merah |
| 2 | `decision/integration.py` — 14.13–14.15, 14.39–14.41 | 3 merah |
| 3 | `decision/engine.py` — 14.42 | 2 merah |
| 4 | `KEPUTUSAN FINAL:` di pesan futures | 2 merah |
| 5 | `ENTRY TIMING:` — 14.19, 14.20 | 3 merah |
| 6 | `decision.trail` per rencana — 14.30 | 3 merah |
| 7 | `channel.allow` di dua jalur kirim — 14.38 | — |
| 6b | perbaikan jejak sesudah pengukuran produksi | 7 merah |

**Total 20 cabut-uji, 20 merah.** Ruff bersih, suite penuh hijau dua kali,
dijalankan sendirian.

### Yang ditemukan pengukuran produksi, dan tidak akan ditemukan test

Jejak PASAL 14.30 menyala 11 kali pada tick pertama — lalu barisnya dibaca, dan
tiga cacat muncul sekaligus:

1. **Satu baris lebih dari 6.000 karakter.** ``repr`` penuh sembilan
   ``AgentOpinion`` beserta seluruh ``EvidenceRef``-nya, 11× per tick, 96 tick
   sehari. Bukan jejak yang bisa dibaca — berkas log yang tidak bisa dibuka.
   Sesudah `_ringkas`: **1.363 karakter**, jumlah anggotanya tetap dilaporkan.
2. **``model_version`` selalu UNKNOWN.** ``FuturesPlan`` tidak punya bidang itu;
   versinya dipegang service dan diberikan ke ``save()`` sebagai argumen
   terpisah. Sekarang `futures-f5`.
3. **``agent_votes`` selalu UNKNOWN.** ``split`` ada di ``CouncilNote``, bukan di
   vonisnya — kelas kesalahan yang sama dengan ``note.strategy`` yang ternyata
   ada di ``context``.

Ketiganya lolos seluruh unit test karena ``UNKNOWN`` adalah keluaran yang sah:
ia terbaca seperti "datanya memang tidak ada", bukan seperti "dibaca dari tempat
yang salah". Hanya membaca baris sungguhannya yang membedakan keduanya.

### Empat kali test yang kutulis tidak menguji apa pun

Cabut-uji menemukan keempatnya:

1. `TERLARANG` redundan — `"WAIT"` toh tidak ada di `_PETA`. Diperkuat menjadi
   memeriksa **pesannya**: penundaan menyebut PASAL 14.43, nilai asing tidak.
2. `"KEPUTUSAN FINAL: FLAT" not in teks` — hijau bahkan sebelum barisnya ada.
   Diganti pemanggilan langsung.
3. Test "baris muat di log" memakai bahan pendek — hijau atas pemotong yang
   dicabut. Diganti bahan sepanjang yang sungguhan.
4. Test "versi model ikut jalur hidup" mencari teks di seluruh `_plan_one`, yang
   sudah memuat `model_version=self._model_version` di panggilan `save()`-nya.
   Dipersempit ke dalam panggilan `catat_jejak` saja.

### Dua kali test double berbentuk salah

`FakeStop` menaruh harga di bidang `invalidation` — dan itu yang membuat bug
`futures.invalidation_block_failed` lolos ke produksi. `_bahan()` memakai
`opinions=("a",)` — tuple string tanpa `.role`, jadi pencatat meledak, penjaga
luar menelannya, dan seluruh test lulus sambil menguji jalur kegagalan.

---

## Putaran kedua — 2026-08-20, sesudah operator meminta sisanya dikerjakan

**Selesai:** Task 8 (`silence`), plus `explanation`, `consistency`, `lifecycle`.

| PASAL | Isi | Cabut-uji |
|---|---|---|
| 14.32, 14.33 | `db/repositories/diam.py` — gerak pasar sesudah ARUNA diam, masuk laporan harian | 8 merah |
| 14.29 | blok KENAPA LONG/SHORT dari opini agent, dengan sumber | 5 merah |
| 14.35–14.37 | penahan duplikat + blok pembalikan di jalur kirim | 3 merah |
| 14.23 | ingatan pendapat menua lewat `Umur`, bukan disimpan selamanya | 2 merah |

**Total 38 cabut-uji, 38 merah.** Ruff bersih, suite penuh hijau.

### Test lama yang menangkap cacat baru

`test_it_speaks_again_after_the_horizon` merah begitu penahan duplikat
tersambung — dan ia benar. Aku mengingat pendapat lama **selamanya**, jadi
PASAL 14.37 berubah dari perlindungan duplikat menjadi pembungkaman permanen.
Perbaikannya justru menyambungkan `lifecycle`: ingatan menua sesuai horizonnya
sendiri lewat `Umur`.

Turunannya: horizon yang tidak dikenal tidak bisa ditua-kan, jadi ingatannya
tidak pernah habis. `Umur` benar menolak menebak masa berlaku; yang salah
adalah memakai ketidaktahuan itu sebagai alasan diam. Gerbang ini sekarang
**gagal ke arah mengirim** — satu duplikat yang lolos jauh lebih murah daripada
satu simbol yang berhenti bicara tanpa jejak.

### Dua kali dua penjaga menutupi satu sama lain

Cabut-uji menemukan keduanya. Sebuah penjaga yang selalu tertutup penjaga lain
tidak bisa dicabut dan membuat satu test pun merah — dan itu berarti tidak ada
yang tahu mana dari keduanya yang sebenarnya bekerja.

1. Sumber alasan dipetakan dua kali: bawaan `dict.get(..., AGENT)` **dan**
   bawaan `getattr(Sumber, nama, AGENT)`. Petanya bisa dikosongkan seluruhnya
   tanpa satu test merah. Sekarang satu fungsi, satu bawaan.
2. Pembalikan tanpa bukti dijaga dua kali - `return None` lebih awal, dan
   `except ConsistencyError` di bawahnya. Yang lebih awal dibuang; yang tersisa
   mencatat sebab yang persis, dan testnya sekarang memeriksa **nama peristiwa
   lognya**, bukan cuma bahwa pesannya tetap terkirim.

### Belum terbukti di produksi

`KEPUTUSAN FINAL:` dan `ENTRY TIMING:` **belum pernah dikirim ke Telegram**.
Keduanya hanya muncul pada rencana bervonis `PLAN`, dan `plans=0` pada seluruh
tick sesudah restart. Pasar sedang tidak menghasilkan PLAN; itu keadaan, bukan
kerusakan.

Yang bisa dilakukan tanpa menunggu, dan sudah dilakukan: menyusun pesannya dari
**tiga baris `futures_plans` bervonis PLAN yang sungguhan** (SUIUSDT, 04:57 /
05:12 / 05:27 UTC). Ketiganya menghasilkan `KEPUTUSAN FINAL: SHORT`,
`ENTRY TIMING: MASUK SEKARANG`, `TIMEFRAME: 4h`, kaki ANALYST ONLY utuh, tanpa
satu pun pengecualian.

Itu membuktikan penyusun pesannya benar terhadap bentuk data produksi - **bukan**
bahwa jalur kirimnya berjalan. Bedanya disebut supaya tidak disalahbaca.

---

## Putaran ketiga — `outcome` (PASAL 14.31, 14.34)

**Modul kedelapan, yang terakhir diam, sekarang hidup.** `catat_hasil` di
[resolve.py](../../../src/aruna/futures/resolve.py) dipanggil tepat sesudah
`save_result`, dan mengirim nasib tiap rencana ke pembelajaran.

Empat keputusan yang dieja, bukan disimpulkan:

- **`LIQUIDATED` masuk kolom LOSS**, tidak punya kategori sendiri. §11.21
  melarang menyembunyikan LOSS, dan likuidasi adalah kekalahan yang paling
  buruk - memberinya kategori sendiri akan mengeluarkannya dari kolom kalah.
- **`move_pct` adalah gerak pasar apa adanya**, positif berarti harga naik.
  Yang membalik tandanya untuk SHORT adalah modul outcome. Membaliknya di sini
  juga akan membaliknya dua kali, dan kekalahan besar tercatat sebagai
  kemenangan besar di dalam data yang dipelajari Phase 12.
- **Signal palsu tanpa sebab tidak dikirim** (PASAL 14.34) - tapi
  peringatannya **menyebut hasilnya**, supaya penahanan itu tidak terbaca
  seperti kekalahan yang menghilang. Kerugiannya sendiri tetap tersimpan di
  `futures_plan_results` dan tetap masuk laporan harian.
- **`OPEN` bukan akhir** dan tidak dicatat sama sekali.

### Cacat yang hanya terlihat dari log produksi

Pengukuran pertama sesudah restart mencatat APTUSDT dengan
`move_pct = -0.5952539839308117342444545285` - dua puluh delapan angka di
belakang koma untuk persentase yang dinilai terhadap ambang dua persen. Bukan
salah hitung, tapi tetap cacat, dan kelas yang sama dengan jejak PASAL 14.30
dan tiga kolom DECIMAL yang pernah terpotong MySQL.

Diperbaiki dengan `_SKALA_GERAK`, testnya ditulis lebih dulu dan memakai angka
produksinya sendiri. Pengukuran sesudahnya: XRPUSDT, `move_pct = 2.04`.

### `integration` ikut disambungkan, dan ia langsung menjawab sesuatu

Modul kesembilan yang diam - `integration` (PASAL 14.39–14.41) - tetap tidak
dipanggil siapa pun sesudah putaran kedua. Sekarang `observe_decision`
menghitungnya tiap simbol tiap tick, dan pengukuran pertama di produksi:

| fase | kelengkapan |
|---|---|
| PHASE 11 | **86%** |
| PHASE 12 | **11%** |
| PHASE 13 | **36%** |
| gabungan | 41% |

Itu bukan angka kosmetik. **Phase 12 hampir tidak sampai ke keputusan sama
sekali** - satu dari sembilan masukan yang PASAL 14.40 sebut. Pattern
discovery, agent specialization, champion, challenger, walk-forward,
out-of-sample, drift detection: tidak satu pun terbaca di jalur futures.
Pengukuran ini yang menentukan lapisan mana yang pantas disambungkan
berikutnya, dan sekarang pertanyaannya punya angka.

**48 cabut-uji, 48 merah, nol yang tidak menggigit.** Ruff bersih, suite penuh
exit 0.

---

## Verifikasi ulang — 2026-08-21 (Task 9)

Tidak ada satu baris kode pun yang diubah di putaran ini. Seluruhnya pengukuran.

**Test.** Ruff bersih (`src` dan `tests`). Berkas Phase 14 dijalankan sendirian
lebih dulu — `final`, `integration`, `engine`, `tersambung` (134 lulus), lalu
enam belas berkas `test_decision_*` sisanya (385 lulus). Suite penuh, sendirian:
**3.558 lulus, exit 0** — nol gagal, nol error, nol skip, nol xfail. Suite itu
memakan lebih dari 25 menit; angkanya disebut supaya yang berikutnya tahu
bahwa ia bukan langkah dua menit.

**Restart tidak diperlukan.** ARUNA hidup sejak 01.51.53 WIB, sesudah perubahan
kode terakhir (01.24.40). Prosesnya sudah menjalankan kode yang baru saja diuji;
me-restart hanya akan membuang dua tick yang sudah terukur.

**Produksi — dua `futures.tick` sesudah restart** (18:52:05Z dan 19:07:20Z):

| yang diminta Task 9 Step 4 | harus | terukur |
|---|---|---|
| `futures.final_decision_failed` | 0 | 0 |
| `futures.final_decision_unknown` | 0 | 0 |
| `decision.trail` | satu baris per rencana berarah | 18 (9 per tick) |
| `decision.trail_failed` | 0 | 0 |
| `Data truncated` | 0 | 0 |
| `level=error` | 0 | **1** — bukan Phase 14, lihat di bawah |

**Kelengkapan integrasi naik jauh sejak putaran ketiga** (40 amatan,
`decision.observed`):

| fase | putaran ketiga | sekarang |
|---|---|---|
| PHASE 11 | 86% | **100%** |
| PHASE 12 | 11% | **78%** |
| PHASE 13 | 36% | **65%** |
| gabungan | 41% | **82%** |

Yang hilang tinggal empat, dan sama pada keempat puluh amatan:
`WALK_FORWARD` dan `OUT_OF_SAMPLE` — sengaja `False`, validasi model luring
bukan masukan per-keputusan — plus **`CORRELATION_RISK` dan
`DAILY_RISK_BUDGET`, dua yang benar-benar belum tersambung.** Itu daftar
pekerjaan berikutnya, dan sekarang ia hanya dua baris panjang.

**Yang masih belum terbukti, dan tidak akan diaku beres:** `plans=0` pada kedua
tick, jadi `KEPUTUSAN FINAL:` dan `ENTRY TIMING:` **masih belum pernah terkirim
ke Telegram** — keadaan yang sama persis dengan putaran kedua. 18 jejak dengan
`plans=0` bukan kejanggalan: sembilan rencana per tick punya arah yang dikenali
lalu ditahan gerbang di hilir, dan rencana ber-`side='FLAT'` memang tidak
meninggalkan jejak.

**Satu error itu, apa adanya:** `upkeep.interval_refresh_failed` pada 18:52:00Z,
tujuh detik sesudah start — deadlock MySQL (1213) saat `INSERT INTO candles`.
Bukan jalur Phase 14, dan bukan baru: sepanjang berkas log berjalan ada 5
kejadian yang sama plus deadlock kedua yang muncul sebagai peringatan
`futures.council_not_stored`. Disebut di sini karena Task 9 menuntut nol, dan
angkanya bukan nol — perbaikannya pekerjaan tersendiri.

---

## Putaran keempat — dua masukan terakhir PASAL 14.41

Operator meminta keduanya disambungkan sesudah membaca pengukuran di atas.
Keduanya diam karena sebab yang sama sekali berbeda, dan itu yang menentukan
bentuk perbaikannya.

### `CORRELATION_RISK` — semuanya ada kecuali yang menjalankannya

Akarnya **bukan** pembacanya. `PembacaPembelajaran._correlation` sudah dirangkai
di `app.py:434`, dan sejak restart terakhir ia tidak melempar satu kali pun.
Tabel `correlations`: **nol baris**. Satu-satunya pemanggil `build_matrix` adalah
perintah CLI `aruna correlate`, yang diketik manusia — dan tidak ada manusia yang
mengetiknya tiap jam.

Yang ditambah: [upkeep/korelasi.py](../../../src/aruna/upkeep/korelasi.py) —
`PenyegarKorelasi`, dihitung dari candle **tersimpan** (nol permintaan jaringan),
sejam sekali, dirangkai di `app.py` dan dipanggil `UpkeepLoop.cycle`.

Satu keputusan yang dieja supaya tidak berulang: `HORIZON_KEPUTUSAN` sekarang
satu tempat dengan dua pembaca — `supervisor.default_children` mengopernya
sebagai `--horizon`, penyegar menghitung untuk interval yang sama. Korelasi 1h
yang tersimpan rapi sementara futures merencanakan 4h adalah tabel terisi yang
tidak pernah terbaca: cacat yang sama persis dengan yang baru saja ditutup, cuma
pindah satu interval.

**Terbukti di produksi tanpa restart** — pembacanya sudah hidup di proses yang
berjalan, jadi mengisi tabelnya cukup. Penyegar yang sama dijalankan sekali
terhadap database sungguhan: 20 aset, 190 pasangan, nol dilewati. Tick futures
berikutnya:

| | sebelum | sesudah |
|---|---|---|
| simbol yang kehilangan `CORRELATION_RISK` | 20 dari 20 | **0 dari 20** |
| PHASE 13 | 65% | **82,7%** |
| gabungan | 82% | **86,4%** |

### `DAILY_RISK_BUDGET` — tidak punya apa-apa

Berbeda dari tetangganya: `risk_budget` tidak ada di kode, tidak di config,
tidak di database. Batasnya karena itu **ditetapkan operator — 3% equity** —
dan bukan disimpulkan; §13.26 melarang mengarang angka risiko, dan plafon yang
dipilih penulis kode adalah persis itu. Angkanya dipilih terhadap laju terukur:
5–7 simbol per hari pada 0,5% per ide.

Yang ditambah: `JatahHarian` + `jatah_harian` di
[futures/risk.py](../../../src/aruna/futures/risk.py),
`FuturesRepository.risiko_terpakai_since`, bidang `CouncilNote.risk_budget`,
`attach_jatah` di service (satu kueri per tick, bukan per simbol), dan baris
`JATAH RISIKO HARI INI:` di pesan futures.

**Ia melapor, tidak menahan.** Jalur ini sudah punya dua gerbang terukur, dan
yang ketiga akan membungkam ARUNA hampir sepenuhnya — lihat catatan di
`decision/engine.py`.

### Dua cacat yang hanya angka produksi yang menemukan

1. **Kuerinya menghitung satu ide sebelas kali.** Versi pertama menjumlahkan
   `futures_plans` apa adanya: 3.099 USDT untuk 2026-08-20 terhadap jatah 300 —
   **1033%**. Bukan jatah yang jebol; rencana yang sama disusun ulang tiap lima
   belas menit. Hari itu 55 baris PLAN lahir dari **lima** simbol, dan **satu**
   yang benar-benar terkirim. Sekarang dihitung dari `futures_plan_delivery`:
   penahan duplikat PASAL 14.35–14.37 sudah memastikan tiap setup dikirim
   sekali, dan yang dipertaruhkan operator adalah yang sampai kepadanya.
   Sesudah perbaikan: **16,7% terpakai — 50,00 dari 300,00**.
2. **24 angka di belakang koma.** `SUM(quantity * ABS(entry - stop))`
   memulangkan `49.998650000000000000000000` untuk USDT yang dinilai terhadap
   ratusan — kelas yang sama dengan `move_pct` 28 digit dan jejak PASAL 14.30
   yang pernah 6.000 karakter. Dibulatkan di `__post_init__`, bukan saat
   dicetak, supaya `sisa` dan `pct_terpakai` ikut bersih.

### Dua test yang kutulis ternyata tidak menguji apa pun

Cabut-uji menemukan keduanya, dan keduanya keluarga yang sama: **memeriksa teks,
bukan yang dikerjakan.**

1. `"korelasi=" in inspect.getsource(app)` tetap hijau ketika barisnya
   dikomentari — sebuah penjaga yang membaca komentar sebagai kode. Diganti
   pemeriksaan AST atas argumen `UpkeepLoop(...)` yang sungguhan.
2. `PlanVerdict.PLAN.value in db.args` tetap hijau pada kueri yang menjumlahkan
   **seluruh** vonis: nilainya ada di daftar argumen tanpa satu baris pun
   tersaring olehnya. Sekarang SQL-nya sendiri yang diperiksa.

**11 cabut-uji, 11 merah.** 614 test pada jalur yang tersentuh: hijau. Ruff
bersih pada seluruh berkas yang diubah.

### Restart dan pengukuran — operator meminta penutupannya

ARUNA dinyalakan ulang 2026-08-20T20:11:58Z. Dua tick futures sesudahnya
(20:12:16Z dan 20:27:55Z), 40 amatan `decision.observed`:

| fase | rencana ditulis | sebelum putaran ini | sesudah |
|---|---|---|---|
| PHASE 11 | 86% | 100% | **100%** |
| PHASE 12 | 11% | 78% | **78%** |
| PHASE 13 | 36% | 65% | **100%** |
| gabungan | 41% | 82% | **90,4%** |

Yang hilang tinggal `WALK_FORWARD` dan `OUT_OF_SAMPLE` — keduanya sengaja
`False` dengan sebab yang dieja di `_kelengkapan_fase`: validasi model luring,
bukan masukan per-keputusan. **Tidak ada lagi lapisan yang diam karena tidak
punya pembaca.**

Seluruh penghitung kegagalan nol sepanjang kedua tick: `decision.trail_failed`,
`futures.final_decision_failed`, `futures.final_decision_unknown`,
`futures.jatah_harian_failed`, `futures.jatah_attach_failed`,
`upkeep.korelasi_failed`, `level=error`, `level=critical`, `Data truncated`.
16 `decision.trail`, 2 `upkeep.korelasi` (190 pasangan tiap kali).

### Satu cacat lagi, ditemukan siklus pertama sesudah restart

`korelasi.tidak_cukup_aset` untuk IDX dengan **nol aset** — dan itu bukan data
yang kurang: IDX tidak punya bar 4h sama sekali, `horizons_for_market`
menyebutnya langsung. Peringatan tiap jam yang tidak akan pernah bisa
diperbaiki oleh data akan berhenti dibaca, lalu menutupi peringatan yang
berarti sesuatu. Pasar yang tidak menawarkan horizonnya sekarang dilewati;
siklus sesudah perbaikan melaporkan `pasar=1`, tanpa peringatan.

Satu alarm yang **salah** dan disebut supaya tidak diwariskan: korelasi terlihat
menyegar tiap 15 menit, bukan sejam. Sebabnya bukan cadence — ARUNA dimatikan
dan dinyalakan ulang 20:26:35→20:27:32 oleh sesi lain, dan tiap proses baru
memang menghitung sekali di siklus pertamanya.

### Yang tetap belum terbukti

`KEPUTUSAN FINAL:`, `ENTRY TIMING:`, dan `JATAH RISIKO HARI INI:` **belum pernah
muncul di pesan yang sungguhan terkirim**. `plans=0` pada kedua tick, dan
sepanjang sejarah `futures_plan_delivery` hanya ada **satu** rencana yang benar-
benar sampai ke operator. Ketiganya terbukti benar terhadap bentuk data
produksi lewat test dan kueri langsung; jalur kirimnya menunggu pasar yang
menghasilkan PLAN. Itu keadaan, bukan kerusakan — dan bedanya disebut supaya
tidak disalahbaca sebagai "sudah jalan".

---

### Catatan lama (sudah tidak berlaku)

`outcome` (PASAL 14.31) **masih diam**. Ia butuh jalur resolusi -
`futures_plan_results` - dan `Catatan` menuntut sebab untuk tiap LOSS, yang
lahir di loss autopsy Phase 8. Menyambungkannya berarti satu siklus repositori
plus ujinya sendiri, dan menempelkannya di ujung putaran ini akan mengulang
persis kesalahan yang sudah dua kali terjadi hari ini: kode yang tersambung
tanpa pernah dibaca keluarannya.

**Belum:** Task 8 (`silence`), dan empat modul yang masih diam.

**Dua kali test yang kutulis ternyata tidak menguji apa pun**, dan cabut-uji
yang menemukannya:

1. `TERLARANG` mula-mula redundan — `"WAIT"` toh tidak ada di `_PETA`, jadi ia
   ditolak dengan atau tanpa daftarnya. Testnya diperkuat menjadi memeriksa
   **pesannya**: penundaan menyebut PASAL 14.43, nilai asing tidak. Bedanya
   nyata bagi yang membaca log — yang pertama berarti ada lapisan yang harus
   diperbaiki, yang kedua berarti enum baru atau data rusak.
2. `test_flat_tidak_pernah_tercetak_sebagai_keputusan` mula-mula berbunyi
   `"KEPUTUSAN FINAL: FLAT" not in teks` — hijau bahkan sebelum barisnya ada.
   Diganti menjadi pemanggilan langsung `_keputusan_final`.

**Satu tempat di mana spec-ku kalah dari kode yang sudah ada:** rencana ini
menulis `finalize("SELL")` tanpa waktu masuk sebagai sah. `timing.Rencana`
menolaknya — PASAL 14.19 mewajibkan arah punya waktu masuk, dan PASAL 14.20
mewajibkan waktu masuk yang menunggu menyebut syaratnya. Modulnya benar;
testnya yang diperbaiki.

**2. Pindaian placeholder.** Tidak ada "TBD", "nanti", atau "tambahkan
penanganan yang sesuai". Tiga tempat sengaja meminta pelaksana memeriksa bentuk
yang sungguhan lebih dulu (`CATATAN_MATI` di Task 7, bidang `Laporan` di Task 8,
`reference_price` di Task 5) — itu bukan placeholder melainkan penjaga terhadap
cacat yang baru saja terjadi di proyek ini: test double yang bentuknya salah
membuat bug lolos ke produksi.

**3. Konsistensi tipe.** `Arah` (LONG/SHORT/NO_SIGNAL) dipakai sama di Task 1,
4, 5, 6. `arah_dari` menerima `object` dan memulangkan `Arah` di semua
pemanggilnya. `Timing` hanya muncul di Task 1 dan 5, dengan nilai yang sama.
`WAJIB` bentrok nama antara `hierarchy` dan `integration` — sudah ditangani di
Task 2 Step 5.
