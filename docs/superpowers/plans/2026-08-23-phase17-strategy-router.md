# Phase 17 — Adaptive Market Regime & Strategy Router

> **Untuk pelaksana:** jalankan tugas per tugas. **Subagent-driven TIDAK dipakai** —
> operator melarang orkestrasi multi-agent di proyek ini.

**Goal:** ARUNA memilih strategi berdasarkan rezim pasar yang terukur, dengan
bukti, dan menolak memilih ketika buktinya tidak cukup.

**Architecture:** Router yang membaca rezim dari tabel `regimes` yang sudah ada,
menilai kecocokan tiap strategi di katalog `strategies` terhadap rezim itu, lalu
memberi peringkat memakai performa historis yang **di-slice ulang** supaya
per-rezim berarti. Tidak ada mesin baru: yang dibangun adalah lapisan pemilihan
di atas empat sumber yang sudah tersimpan.

**Tech Stack:** Python 3.13, MySQL 8.4 lewat asyncmy, pytest, ruff.

---

## PERINGATAN YANG DICATAT, BUKAN DIDIAMKAN

### 1. Spec menuntut angka yang mustahil ada di desain sekarang

§17.37 minta performa strategi **per rezim**, dan mencontohkan:

```
TRENDING UP:  Trend Following 87%,  Mean Reversion 54%
RANGE:        Trend Following 54%,  Mean Reversion 74%
```

Diperiksa langsung di `strategy_performance` 2026-08-23:

| strategy | slice | W/L |
|---|---|---|
| STR-005 | `regime=ALL` | 188 / 726 |
| STR-005 | `regime=TRENDING` | **188 / 726** |
| STR-002 | `regime=ALL` | 546 / 1605 |
| STR-002 | `regime=BREAKOUT` | **546 / 1605** |

Identik. Sebabnya struktural, bukan bug data:
`learning.strategies.classify()` **menurunkan strategi DARI rezim**, jadi
sebuah strategi hanya pernah muncul pada satu rezim. "Performa Trend Following
saat RANGE" tidak ada dan tidak bisa ada — karena saat RANGE, tidak ada
prediksi yang pernah diberi label Trend Following.

**Konsekuensinya menentukan seluruh Phase 17.** Router yang memeringkat
memakai angka ini akan memeringkat satu kandidat melawan dirinya sendiri.
Task 3 menangani ini, dan caranya bukan menambah kolom melainkan mengubah apa
yang di-slice.

### 2. Angka nyatanya jauh dari contoh spec

Spec memakai contoh 78–91% win rate. Yang tersimpan:

```
STR-002  25,4%   (2.151 sampel)
STR-005  20,6%   (914 sampel)
```

Itu bukan alasan menunda Phase 17 — justru sebaliknya, router yang memilih
di antara strategi yang semuanya di bawah 30% harus **sanggup memulangkan
NONE**, dan §17.29 sudah memintanya. Tapi contoh angka di spec tidak boleh
dikutip sebagai target.

### 3. Yang SUDAH ada, dan tidak boleh dibangun ulang

| yang diminta spec | yang sudah ada |
|---|---|
| §17.11 Strategy Registry | `learning/strategies.py` + tabel `strategies` (7 baris) |
| §17.12 Strategy Metadata | `Strategy`: code, name, conditions, preferred_regimes, preferred_horizons, status |
| §17.13 Strategy Status | `StrategyStatus` |
| §17.3–17.5 Regime + confidence | tabel `regimes`: regime, confidence, trend, breakout, reasons, alternatives, evidence_used/available |
| §17.6 Regime Evidence | `regimes.reasons` (JSON) |
| §17.25 Strategy Drift | tabel `drift_checks` |
| §17.36 Performance | `strategy_performance` dengan ci_low/ci_high/evidence/net_pnl/max_drawdown |

**Phase 17 tidak menulis satu pun dari itu ulang.** Yang dibangun: pembacaan
rezim multi-timeframe, penilaian kecocokan, peringkat, dan penolakan.

### 4. Yang TIDAK dibangun rencana ini, dan sebabnya

* **§17.33–17.35 debat/protes/veto agent atas strategi.** Council sudah punya
  mesin protes penuh untuk keputusan arah. Menyalinnya untuk strategi berarti
  dua mesin protes yang harus tetap sepakat. Ditunda sampai router terbukti
  memilih sesuatu yang layak diperdebatkan.
* **§17.41–17.43 walk-forward dan out-of-sample.** `backtest/walkforward.py`
  sudah ada. Menyambungkannya adalah pekerjaan tersendiri dengan gerbangnya
  sendiri.
* **§17.7 tujuh timeframe.** Tabel `regimes` diisi untuk interval yang
  benar-benar dipindai. Rencana ini memakai yang tersedia dan **melaporkan
  mana yang tidak ada**, bukan mengarang.

---

## Global Constraints

- ARUNA **ANALYST ONLY** (§17.1). Tidak ada order, tidak ada perubahan
  leverage, tidak ada dana berpindah. Dijaga AST, bukan janji di docstring.
- Router memulangkan **NONE** ketika tidak ada yang cocok (§17.29). Dilarang
  memaksa memilih.
- Regime confidence di bawah ambang → strategy confidence turun; terlalu
  rendah → tidak ada strategi pilihan (§17.30).
- **Sample size mempengaruhi confidence** (§17.23). Win rate 95% dari 8 sampel
  tidak boleh mengalahkan 82% dari 1.200.
- **No look-ahead** (§17.43). Penilaian pada suatu titik waktu hanya boleh
  memakai baris yang `as_of`/`computed_at`-nya tidak melewati titik itu.
- Historical record tidak pernah ditulis ulang (§17.9, §17.27, §17.44).
- Database ringan (§17.52): simpan keputusan, bukan tiap perhitungan.
- Ambang yang dipinjam harus dipinjam dari **pertanyaan yang sama**. Sudah tiga
  kali jadi bug di proyek ini.

---

## Struktur berkas

**Dibuat:**

- `src/aruna/router/__init__.py` — permukaan publik
- `src/aruna/router/rezim.py` — §17.3–17.10: baca rezim multi-timeframe dari
  tabel `regimes`, tentukan primary vs secondary, hitung stabilitas
- `src/aruna/router/kecocokan.py` — §17.14–17.15: skor kecocokan strategi
  terhadap rezim, dengan faktor yang dieja satu per satu
- `src/aruna/router/peringkat.py` — §17.17–17.18, §17.21–17.23: champion,
  challenger, penyesuaian risiko dan sampel
- `src/aruna/router/putusan.py` — §17.29–17.30, §17.46: bentuk keluaran router,
  termasuk NONE
- `src/aruna/db/repositories/router.py` — penyimpanan pilihan router
- `migrations/0041_router_pilihan.sql`

**Diubah:**

- `src/aruna/learning/strategies.py` — Task 3: slice performa yang berarti
- `src/aruna/upkeep/loop.py` — fase router
- `src/aruna/app.py` — perangkaian

---

## Task 1: Rezim multi-timeframe, primary vs secondary (§17.3–17.8)

**Files:** buat `src/aruna/router/rezim.py`, `tests/test_router_rezim.py`

**Interfaces:**
- Consumes: tabel `regimes` (asset_id, interval_code, as_of, regime,
  confidence, reasons)
- Produces:
  - `BacaanRezim(interval: str, regime: str, confidence: float, alasan: tuple[str, ...])`
  - `PetaRezim(primary: str | None, primary_confidence: float, sekunder: tuple[str, ...], per_interval: tuple[BacaanRezim, ...], interval_hilang: tuple[str, ...])`
  - `susun_peta(bacaan: tuple[BacaanRezim, ...]) -> PetaRezim`

- [ ] **Step 1: Tulis test yang gagal**

```python
def test_horizon_panjang_menentukan_primary() -> None:
    """§17.8: pullback 5m tidak boleh terbaca sebagai perubahan tren besar."""
    peta = susun_peta((
        BacaanRezim("5m", "RANGING", 70.0, ("konsolidasi",)),
        BacaanRezim("1h", "TRENDING_BULLISH", 85.0, ("higher high",)),
        BacaanRezim("4h", "TRENDING_BULLISH", 88.0, ("higher low",)),
    ))

    assert peta.primary == "TRENDING_BULLISH"
    assert "RANGING" in peta.sekunder
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_router_rezim.py`
Expected: FAIL — `ImportError: cannot import name 'susun_peta'`

- [ ] **Step 3: Implementasi minimal**

```python
#: Bobot tiap interval saat menentukan primary. Horizon panjang lebih berat
#: karena §17.8 justru menuntut pullback pendek TIDAK terbaca sebagai
#: perubahan tren. Angkanya kebijakan, bukan pengukuran - yang bisa
#: dipertahankan urutannya, bukan jaraknya.
BOBOT_INTERVAL: dict[str, float] = {
    "5m": 0.5, "15m": 1.0, "30m": 1.2, "1h": 1.6, "4h": 2.0, "1d": 2.4,
}


def susun_peta(bacaan: tuple[BacaanRezim, ...]) -> PetaRezim:
    if not bacaan:
        return PetaRezim(None, 0.0, (), (), ())

    skor: dict[str, float] = {}
    for b in bacaan:
        bobot = BOBOT_INTERVAL.get(b.interval, 1.0)
        skor[b.regime] = skor.get(b.regime, 0.0) + bobot * (b.confidence / 100)

    primary = max(skor, key=lambda r: skor[r])
    dukung = [b for b in bacaan if b.regime == primary]
    percaya = sum(b.confidence for b in dukung) / len(dukung)
    sekunder = tuple(sorted({b.regime for b in bacaan if b.regime != primary}))
    hilang = tuple(i for i in BOBOT_INTERVAL if i not in {b.interval for b in bacaan})
    return PetaRezim(primary, percaya, sekunder, bacaan, hilang)
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_router_rezim.py`

- [ ] **Step 5: Test bahwa interval yang hilang DILAPORKAN, bukan didiamkan**

```python
def test_interval_yang_tidak_ada_dilaporkan() -> None:
    """Rezim yang disimpulkan dari dua interval sementara enam diminta bukan
    kesimpulan yang sama kuatnya - dan bedanya harus terlihat."""
    peta = susun_peta((BacaanRezim("15m", "RANGING", 60.0, ()),))

    assert "4h" in peta.interval_hilang
    assert "1d" in peta.interval_hilang
```

- [ ] **Step 6: Commit**

```bash
git add src/aruna/router/rezim.py tests/test_router_rezim.py
git commit -m "Router: peta rezim multi-timeframe, primary vs sekunder"
```

**Cabut-uji:** samakan seluruh `BOBOT_INTERVAL` menjadi 1.0 → test
`test_horizon_panjang_menentukan_primary` MERAH.

---

## Task 2: Stabilitas rezim (§17.10)

**Files:** ubah `src/aruna/router/rezim.py`, `tests/test_router_rezim.py`

**Interfaces:**
- Produces: `stabilitas(riwayat: tuple[str, ...]) -> float` — 0..100

- [ ] **Step 1: Tulis test yang gagal**

```python
def test_rezim_yang_diam_stabil() -> None:
    assert stabilitas(("TRENDING_BULLISH",) * 8) == 100.0


def test_rezim_yang_berkedip_tidak_stabil() -> None:
    """Terukur di Phase 16: classifier 15m berpindah pada 30,6% bacaan
    berurutan. Router yang tidak menghitungnya akan memilih strategi atas
    rezim yang sudah berganti sebelum sinyalnya terbit."""
    berkedip = ("TRENDING_BULLISH", "RANGING") * 4

    assert stabilitas(berkedip) < 40.0
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

Expected: FAIL — `NameError: name 'stabilitas' is not defined`

- [ ] **Step 3: Implementasi**

```python
def stabilitas(riwayat: tuple[str, ...]) -> float:
    """Berapa persen bacaan berurutan yang TIDAK berpindah.

    Riwayat lebih pendek dari dua tidak bisa menjawab - dan ``0.0`` akan
    terbaca sebagai "sangat tidak stabil", yang jauh lebih dramatis daripada
    "belum bisa diukur". Karena itu kurang dari dua memulangkan ``0.0`` dan
    pemanggil WAJIB memeriksa panjangnya lebih dulu; lihat
    `PetaRezim.stabilitas_terukur`.
    """
    if len(riwayat) < 2:
        return 0.0
    tetap = sum(1 for a, b in zip(riwayat, riwayat[1:], strict=False) if a == b)
    return round(100.0 * tetap / (len(riwayat) - 1), 1)
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Commit**

```bash
git add src/aruna/router/rezim.py tests/test_router_rezim.py
git commit -m "Router: stabilitas rezim dari riwayat bacaan"
```

**Cabut-uji:** ubah `stabilitas` menjadi selalu `100.0` → test berkedip MERAH.

---

## Task 3: Slice performa yang benar-benar menyaring (§17.16, §17.37)

**Ini task terpenting di rencana ini.** Tanpanya router memeringkat kandidat
melawan dirinya sendiri.

**Files:** ubah `src/aruna/learning/strategies.py`,
`tests/test_router_performa.py`

**Interfaces:**
- Produces: `SLICE_BERARTI: frozenset[str]` — dimensi yang benar-benar
  memisahkan; `performa_relevan(rows, *, regime) -> dict[str, Slice]`

- [ ] **Step 1: Tulis test yang membuktikan masalahnya ada**

```python
def test_slice_regime_tidak_menyaring_apa_pun() -> None:
    """**Temuan 2026-08-23.** `classify()` menurunkan strategi DARI rezim,
    jadi sebuah strategi hanya pernah muncul pada satu rezim - dan
    `regime=X` selalu identik dengan `regime=ALL`.

    Terukur di produksi: STR-005 regime=ALL 188/726, regime=TRENDING
    188/726. Router yang memeringkat memakai angka ini memeringkat satu
    kandidat melawan dirinya sendiri.
    """
    from aruna.learning.strategies import classify

    rezim = ("TRENDING_BULLISH", "RANGING", "BREAKOUT", "REVERSAL")
    per_strategi: dict[str, set[str]] = {}
    for r in rezim:
        per_strategi.setdefault(classify(r), set()).add(r)

    ganda = {k: v for k, v in per_strategi.items() if len(v) > 1}
    assert not ganda, (
        "kalau sebuah strategi bisa muncul di lebih dari satu rezim, "
        f"slice per-rezim sudah berarti dan task ini tidak perlu: {ganda}"
    )
```

- [ ] **Step 2: Jalankan — HIJAU, dan itu yang membuktikan masalahnya**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_router_performa.py -k tidak_menyaring`
Expected: PASS — pemetaannya memang satu-ke-satu.

- [ ] **Step 3: Tulis test untuk slice yang BERARTI**

```python
def test_slice_yang_dipakai_router_memisahkan_sesuatu() -> None:
    """Dimensi yang berarti adalah yang bisa berbeda untuk strategi yang sama:
    aset, timeframe, dan sesi. Rezim tidak, karena ia yang MELAHIRKAN
    strateginya."""
    from aruna.learning.strategies import SLICE_BERARTI

    assert "regime" not in SLICE_BERARTI
    assert {"asset", "horizon"} <= SLICE_BERARTI
```

- [ ] **Step 4: Jalankan, pastikan MERAH**

Expected: FAIL — `ImportError: cannot import name 'SLICE_BERARTI'`

- [ ] **Step 5: Implementasi**

```python
#: Dimensi yang benar-benar memisahkan performa satu strategi.
#:
#: **`regime` sengaja TIDAK ada di sini**, dan itu temuan bukan kelalaian.
#: :func:`classify` menurunkan strategi DARI rezim, jadi tiap strategi hanya
#: pernah muncul pada satu rezim - `regime=TRENDING` dan `regime=ALL`
#: memulangkan baris yang sama persis. Terukur 2026-08-23: STR-005
#: 188W/726L pada keduanya.
#:
#: Yang tersisa memang memisahkan: strategi yang sama dipakai pada aset
#: berbeda, horizon berbeda, dan sesi berbeda, dan hasilnya boleh berbeda.
SLICE_BERARTI: frozenset[str] = frozenset({"asset", "horizon", "session"})
```

- [ ] **Step 6: Jalankan, pastikan HIJAU**

- [ ] **Step 7: Commit**

```bash
git add src/aruna/learning/strategies.py tests/test_router_performa.py
git commit -m "Slice performa: regime tidak menyaring, aset/horizon/sesi menyaring"
```

**Cabut-uji:** tambahkan `"regime"` ke `SLICE_BERARTI` → test MERAH.

---

## Task 4: Skor kecocokan (§17.14–17.15, §17.21–17.23)

**Files:** buat `src/aruna/router/kecocokan.py`, `tests/test_router_kecocokan.py`

**Interfaces:**
- Consumes: `PetaRezim` (Task 1), `stabilitas` (Task 2), `SLICE_BERARTI` (Task 3)
- Produces:
  - `Kecocokan(kode: str, skor: int, alasan: tuple[str, ...], sampel: int)`
  - `nilai(strategi, *, peta, performa, stabil) -> Kecocokan`

- [ ] **Step 1: Tulis test yang gagal**

```python
def test_rezim_cocok_menaikkan_skor() -> None:
    peta = PetaRezim("TRENDING_BULLISH", 85.0, (), (), ())
    cocok = nilai(_strategi(preferred=("TRENDING_BULLISH",)),
                  peta=peta, performa=None, stabil=90.0)
    tidak = nilai(_strategi(preferred=("RANGING",)),
                  peta=peta, performa=None, stabil=90.0)

    assert cocok.skor > tidak.skor
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

Expected: FAIL — `ImportError: cannot import name 'nilai'`

- [ ] **Step 3: Test perlindungan sampel — §17.23**

```python
def test_sampel_kecil_tidak_mengalahkan_sampel_besar() -> None:
    """§17.23 dengan angkanya sendiri: 95% dari 8 sampel melawan 82% dari
    1.200. Selang kepercayaan yang pertama membentang hampir separuh sumbu."""
    peta = PetaRezim("TRENDING_BULLISH", 85.0, (), (), ())
    tipis = nilai(_strategi(), peta=peta,
                  performa=Slice(win_rate=0.95, sample_size=8), stabil=90.0)
    tebal = nilai(_strategi(), peta=peta,
                  performa=Slice(win_rate=0.82, sample_size=1200), stabil=90.0)

    assert tebal.skor > tipis.skor
```

- [ ] **Step 4: Implementasi**

```python
#: Sampel minimum sebelum win rate ikut menaikkan skor.
#:
#: Dipinjam dari `governance.proposal.MIN_VALIDATION_SAMPLE` - pertanyaannya
#: SAMA: "berapa hasil terselesaikan sebelum sebuah angka performa berarti".
#: Meminjam ambang untuk pertanyaan yang berbeda sudah tiga kali jadi bug di
#: proyek ini, jadi yang ini disebut sumbernya.
from aruna.governance.proposal import MIN_VALIDATION_SAMPLE

def nilai(strategi, *, peta, performa, stabil) -> Kecocokan:
    alasan: list[str] = []
    skor = 50

    if peta.primary in strategi.preferred_regimes:
        skor += 25
        alasan.append(f"rezim {peta.primary} ada di preferred_regimes")
    else:
        skor -= 20
        alasan.append(f"rezim {peta.primary} BUKAN preferensi strategi ini")

    # Keyakinan rezim dan stabilitasnya menskalakan, tidak menambah: rezim
    # yang benar tapi tidak yakin bukan bukti yang lebih kuat daripada rezim
    # yang salah - ia bukti yang lebih LEMAH atas hal yang sama.
    skala = (peta.primary_confidence / 100) * (stabil / 100)
    skor = int(50 + (skor - 50) * skala)
    alasan.append(f"diskalakan keyakinan {peta.primary_confidence:.0f}% "
                  f"dan stabilitas {stabil:.0f}%")

    if performa is not None and performa.sample_size >= MIN_VALIDATION_SAMPLE:
        skor += int((performa.win_rate - 0.5) * 40)
        alasan.append(f"win rate {performa.win_rate:.1%} atas "
                      f"{performa.sample_size} sampel")
    elif performa is not None:
        alasan.append(f"{performa.sample_size} sampel di bawah "
                      f"{MIN_VALIDATION_SAMPLE} - win rate TIDAK dihitung")

    return Kecocokan(strategi.code, max(0, min(100, skor)), tuple(alasan),
                     performa.sample_size if performa else 0)
```

- [ ] **Step 5: Jalankan, pastikan HIJAU**

- [ ] **Step 6: Commit**

```bash
git add src/aruna/router/kecocokan.py tests/test_router_kecocokan.py
git commit -m "Router: skor kecocokan dengan perlindungan sampel"
```

**Cabut-uji:** buang gerbang `sample_size >= MIN_VALIDATION_SAMPLE` → test
sampel kecil MERAH.

---

## Task 5: Peringkat, champion, challenger, dan NONE (§17.17–17.18, §17.29–17.30)

**Files:** buat `src/aruna/router/peringkat.py`, `src/aruna/router/putusan.py`,
`tests/test_router_putusan.py`

**Interfaces:**
- Consumes: `Kecocokan` (Task 4)
- Produces:
  - `PutusanRouter(champion: Kecocokan | None, challenger: Kecocokan | None, alasan_kosong: str)`
  - `pilih(kandidat: tuple[Kecocokan, ...], *, peta) -> PutusanRouter`
  - `AMBANG_LAYAK: int`, `AMBANG_KEYAKINAN_REZIM: float`

- [ ] **Step 1: Tulis test yang gagal — §17.29**

```python
def test_tidak_ada_yang_cocok_memulangkan_none() -> None:
    """§17.29: dilarang memaksa memilih strategi hanya supaya sistem
    menghasilkan LONG/SHORT."""
    peta = PetaRezim("UNCERTAIN", 41.0, (), (), ())
    hasil = pilih((Kecocokan("STR-001", 30, (), 900),), peta=peta)

    assert hasil.champion is None
    assert "41" in hasil.alasan_kosong
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

Expected: FAIL — `ImportError: cannot import name 'pilih'`

- [ ] **Step 3: Implementasi**

```python
#: Skor kecocokan minimum sebelum sebuah strategi boleh menjadi champion.
#:
#: Enam puluh, dan itu **kebijakan bukan pengukuran** - ditulis begitu supaya
#: tidak ada yang mengutipnya sebagai temuan. Yang bisa dipertahankan: ia
#: harus DI ATAS 50, karena 50 adalah skor sebuah strategi yang rezimnya tidak
#: cocok maupun tidak bertentangan.
AMBANG_LAYAK = 60

#: Keyakinan rezim minimum sebelum strategi apa pun dipilih (§17.30).
#:
#: Dipinjam dari `signals.quality.MIN_QUALITY`, dan pertanyaannya sama:
#: "berapa keyakinan minimum sebelum sebuah pembacaan boleh dipakai
#: memutuskan". Disebut sumbernya, bukan diketik ulang.
AMBANG_KEYAKINAN_REZIM = float(MIN_QUALITY)


def pilih(kandidat, *, peta) -> PutusanRouter:
    if peta.primary is None:
        return PutusanRouter(None, None, "rezim tidak terbaca")
    if peta.primary_confidence < AMBANG_KEYAKINAN_REZIM:
        return PutusanRouter(
            None, None,
            f"keyakinan rezim {peta.primary_confidence:.0f}% di bawah ambang "
            f"{AMBANG_KEYAKINAN_REZIM:.0f}%",
        )

    layak = sorted(
        (k for k in kandidat if k.skor >= AMBANG_LAYAK),
        key=lambda k: (-k.skor, k.kode),
    )
    if not layak:
        tertinggi = max((k.skor for k in kandidat), default=0)
        return PutusanRouter(
            None, None,
            f"skor tertinggi {tertinggi} di bawah ambang {AMBANG_LAYAK}",
        )
    return PutusanRouter(layak[0], layak[1] if len(layak) > 1 else None, "")
```

- [ ] **Step 4: Test challenger — §17.18**

```python
def test_challenger_disimpan_kalau_ada() -> None:
    peta = PetaRezim("TRENDING_BULLISH", 85.0, (), (), ())
    hasil = pilih((Kecocokan("STR-001", 91, (), 900),
                   Kecocokan("STR-005", 84, (), 900)), peta=peta)

    assert hasil.champion.kode == "STR-001"
    assert hasil.challenger.kode == "STR-005"
```

- [ ] **Step 5: Test strategi DISABLED tidak pernah terpilih — §17.13**

```python
def test_disabled_tidak_pernah_jadi_champion() -> None:
    """§17.13: strategi DISABLED tidak boleh dipilih Router. Disaring di
    hulu - kandidat yang tidak layak tidak boleh sampai ke peringkat, karena
    peringkat yang memuatnya akan mencetaknya di log sebagai 'hampir
    terpilih'."""
    from aruna.router.peringkat import kandidat_aktif
    from aruna.learning.strategies import StrategyStatus

    hidup = kandidat_aktif((
        _strategi(code="STR-001", status=StrategyStatus.ACTIVE),
        _strategi(code="STR-009", status=StrategyStatus.DISABLED),
    ))

    assert [s.code for s in hidup] == ["STR-001"]
```

- [ ] **Step 6: Jalankan, pastikan HIJAU**

- [ ] **Step 7: Commit**

```bash
git add src/aruna/router/peringkat.py src/aruna/router/putusan.py tests/test_router_putusan.py
git commit -m "Router: champion, challenger, dan penolakan yang jujur"
```

**Cabut-uji:** buang gerbang `AMBANG_KEYAKINAN_REZIM` → test NONE MERAH.

---

## Task 6: Penjaga ANALYST ONLY (§17.1)

**Files:** buat `tests/test_router_analis_saja.py`

- [ ] **Step 1: Tulis penjaga AST**

```python
def test_router_tidak_punya_kosakata_eksekusi() -> None:
    """§17.1. Dijaga AST, bukan pencarian teks: kata 'order' dan 'LONG'
    muncul di docstring yang MENJELASKAN larangannya, dan pencarian teks
    sudah tiga kali tersandung prosanya sendiri di proyek ini."""
    import ast
    from pathlib import Path

    terlarang = {"buy", "sell", "place_order", "cancel_order",
                 "set_leverage", "close_position", "open_position"}
    akar = Path(__file__).resolve().parent.parent / "src" / "aruna" / "router"

    for berkas in akar.rglob("*.py"):
        pohon = ast.parse(berkas.read_text(encoding="utf-8"))
        nama = {
            n.name.lower()
            for n in ast.walk(pohon)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        panggil = {
            n.func.attr.lower()
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert not (nama | panggil) & terlarang, berkas
```

- [ ] **Step 2: Jalankan, pastikan HIJAU**

- [ ] **Step 3: Commit**

```bash
git add tests/test_router_analis_saja.py
git commit -m "Router: penjaga AST ANALYST ONLY"
```

**Cabut-uji:** tambahkan `def place_order(): ...` di `router/putusan.py` →
MERAH. Hapus lagi.

---

## Task 7: Penyimpanan pilihan router (§17.27, §17.44, §17.52)

**Files:** buat `migrations/0041_router_pilihan.sql`,
`src/aruna/db/repositories/router.py`, `tests/test_router_repo.py`

- [ ] **Step 1: Migrasi**

```sql
-- Satu baris per PILIHAN, bukan per perhitungan (bagian 17.52).
--
-- Router berjalan tiap siklus atas dua puluh aset; menyimpan tiap peringkat
-- akan mengulang pelajaran `market_snapshots` yang menjadi 62% basis data
-- dengan nol pembaca.
CREATE TABLE router_pilihan (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    market_code       VARCHAR(16)  NOT NULL,
    asset             VARCHAR(32)  NOT NULL,
    dipilih_pada      DATETIME(6)  NOT NULL,
    regime_primary    VARCHAR(24)  NULL,
    regime_confidence DECIMAL(6,3) NULL,
    regime_stability  DECIMAL(6,3) NULL,
    champion          VARCHAR(32)  NULL,
    champion_skor     TINYINT      NULL,
    challenger        VARCHAR(32)  NULL,
    challenger_skor   TINYINT      NULL,
    -- Kosong berarti ada champion. Terisi berarti TIDAK ada, dan sebabnya
    -- ada di sini - "tidak ada strategi" tanpa alasan tidak bisa dibantah.
    alasan_kosong     VARCHAR(255) NULL,
    alasan            JSON         NULL,
    versi_router      VARCHAR(32)  NOT NULL,
    created_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    UNIQUE KEY uq_router (asset, dipilih_pada),
    KEY idx_router_baca (asset, dipilih_pada DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

- [ ] **Step 2: Terapkan migrasi**

Run: `.\.venv\Scripts\python.exe -m aruna.cli migrate`
Expected: `[applied] 0041_router_pilihan`

- [ ] **Step 3: Test bahwa NONE ikut tersimpan**

```python
@pytest.mark.asyncio
async def test_tidak_ada_champion_tetap_dicatat() -> None:
    """Nol karena tidak ada yang cocok dan nol karena fasenya mati terlihat
    sama dari luar - dan yang pertama normal sementara yang kedua bug."""
    db = _DbPalsu()
    await RouterRepository(db).simpan(
        _putusan_kosong(alasan="keyakinan rezim 41% di bawah ambang 60%")
    )

    assert "alasan_kosong" in db.sql[0][0]
    assert any("41" in str(a) for a in db.sql[0][1])
```

- [ ] **Step 4: Jalankan, pastikan MERAH lalu implementasi lalu HIJAU**

- [ ] **Step 5: Commit**

```bash
git add migrations/0041_router_pilihan.sql src/aruna/db/repositories/router.py tests/test_router_repo.py
git commit -m "Router: simpan pilihan, termasuk ketika tidak ada"
```

**Cabut-uji:** buang kolom `alasan_kosong` dari INSERT → test MERAH.

---

## Task 8: Perangkaian ke loop (§17.19, §17.53)

**Files:** ubah `src/aruna/upkeep/loop.py`, `src/aruna/app.py`,
`tests/test_router_terpasang.py`

- [ ] **Step 1: Penjaga AST bahwa router benar-benar DIPANGGIL**

```python
def test_router_sampai_ke_loop() -> None:
    """Cacat yang sudah lima kali muncul di proyek ini: kode ditulis, diuji,
    diekspor, lalu tidak pernah dipanggil. Penjaga AST, bukan pencarian teks."""
    import ast
    import inspect

    from aruna.upkeep.loop import UpkeepLoop

    sumber = inspect.getsource(UpkeepLoop)
    pohon = ast.parse(sumber)
    dipanggil = {
        n.func.attr
        for n in ast.walk(pohon)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }

    assert "_router" in {
        n.name for n in ast.walk(pohon)
        if isinstance(n, ast.AsyncFunctionDef)
    } or "router" in str(dipanggil).lower()
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Sambungkan fase router di `UpkeepLoop`, dengan `router=` dari `app.py`**

- [ ] **Step 4: Test bahwa kegagalan router TIDAK menjatuhkan siklus**

```python
@pytest.mark.asyncio
async def test_router_gagal_tidak_menjatuhkan_siklus() -> None:
    """Router menghasilkan BUKTI, bukan keputusan - kegagalannya tidak boleh
    menjatuhkan siklus yang menghasilkan keputusan sungguhan."""

    class _Meledak:
        async def jalankan(self, *a, **k):
            raise RuntimeError("router jatuh")

    hasil = await _loop(router=_Meledak()).sekali()

    assert hasil is not None
```

- [ ] **Step 5: Jalankan, pastikan HIJAU**

- [ ] **Step 6: Commit**

```bash
git add src/aruna/upkeep/loop.py src/aruna/app.py tests/test_router_terpasang.py
git commit -m "Router tersambung ke siklus upkeep"
```

**Cabut-uji:** cabut `router=` dari `app.py` → penjaga AST MERAH.

---

## Task 9: Ruff, suite penuh, restart, ukur

- [ ] `.\.venv\Scripts\python.exe -m ruff check src tests`
- [ ] Suite penuh, sendirian
- [ ] `aruna migrate`, restart, verifikasi lewat **StartTime**
- [ ] Ukur: berapa aset dapat champion, berapa NONE, sebaran skor, apakah
      `alasan_kosong` yang paling sering masuk akal
- [ ] Laporkan apa adanya, termasuk kalau router **tidak pernah** memilih
      siapa pun — itu hasil yang sah mengingat win rate tertinggi di katalog
      sekarang 25,4%

---

## Self-review

**Cakupan spec.** §17.1 Task 6 · §17.2 Task 4 · §17.3–17.6 Task 1 · §17.7–17.8
Task 1 · §17.9 Task 7 (kolom `dipilih_pada`, baris tidak pernah ditimpa) ·
§17.10 Task 2 · §17.11–17.13 sudah ada + Task 5 (filter DISABLED) · §17.14–17.15
Task 4 · §17.16 Task 3 · §17.17–17.18 Task 5 · §17.19 Task 8 · §17.21–17.23
Task 4 · §17.27 Task 7 · §17.29–17.30 Task 5 · §17.44 Task 7 (`versi_router`) ·
§17.46 Task 5 · §17.52 Task 7 · §17.53 Task 8.

**Celah yang disebut, bukan disembunyikan:**

- **§17.20 memori Phase 15** tidak disambungkan di rencana ini. Mesin
  kemiripan sudah ada dan mahal (sapuan kuadratik yang sempat memblokir loop
  154 detik). Menyambungkannya menuntut gerbang kinerjanya sendiri.
- **§17.24–17.25 drift** memakai `drift_checks` yang sudah ada; router membaca
  statusnya, tidak menghitung ulang.
- **§17.31–17.35 konsensus, konflik, debat, protes, veto** ditunda — alasannya
  di PERINGATAN.
- **§17.36 metrik lengkap** sebagian sudah ada di `strategy_performance`
  (`net_pnl`, `max_drawdown`, `ci_low/ci_high`). Expectancy dan profit factor
  belum, dan tidak ditambahkan sampai ada yang membacanya.
- **§17.38–17.39 performa per aset/timeframe** butuh Task 3 lebih dulu; slice
  yang berarti sudah dieja di sana, pengisiannya milik Phase 12.

**Konsistensi tipe.** `BacaanRezim` dan `PetaRezim` lahir di Task 1, dipakai
Task 4 dan 5. `Kecocokan` lahir di Task 4, dipakai Task 5 dan 7. `stabilitas`
lahir di Task 2, dipakai Task 4. `SLICE_BERARTI` lahir di Task 3, dipakai
Task 4.
