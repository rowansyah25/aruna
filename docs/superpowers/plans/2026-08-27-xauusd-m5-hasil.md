# XAUUSD M5 — Rencana 3: Hasil

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tiap sinyal XAU yang terbit dinilai benar atau salah — pada **dua sumbu yang terpisah** — supaya nanti ada yang bisa dipelajari, dan supaya angkanya jujur sejak baris pertama.

**Architecture:** Resolver berdiri sendiri, membaca `xau_predictions` yang horizonnya sudah lewat dan menarik ulang jalur harga M5 dari sumber yang sama. Tidak menyentuh futures.

---

## Global Constraints

- **Simpan seluruh hasil. Jangan menghapus LOSS.**
- **Jangan menyatakan target 80–90% tercapai** sebelum ada bukti out-of-sample dan walk-forward. Kalau hasilnya 72%, tampilkan 72%.
- Dilarang: look-ahead bias, future leakage, memakai outcome sebelum prediction, mengubah histori agar win rate naik.
- **JANGAN MERUSAK FUTURES.**

---

## Dua pelajaran yang sudah dibayar mahal, dipasang sejak baris pertama

**Satu sumbu tidak cukup.** `migrations/0044_futures_arah.sql` — ditulis 2026-08-25 setelah 218 baris hasil futures diukur:

```
EXPIRED      201   (92,2%)
STOPPED_OUT   14
TARGET_HIT     3
```

Taksonomi lama hanya bertanya *level apa yang tersentuh lebih dulu* — pertanyaan **eksekusi**. Pertanyaan yang tak pernah ditanyakan, *apakah arahnya benar*, adalah pertanyaan **ramalan**. Sembilan dari sepuluh plan mendarat di satu ember yang secara eksplisit menyatakan dirinya tidak menjawabnya, jadi jalur futures tidak punya akurasi arah sama sekali selama berbulan-bulan.

Bedanya menentukan **apa yang harus diperbaiki**:

| | |
|---|---|
| arah benar + stop kena | stop-nya terlalu ketat |
| arah salah + target kena | beruntung, bukan bukti apa pun |
| arah salah + stop kena | agennya yang salah baca |

Menyatukannya jadi satu angka menghapus tepat perbedaan itu. `xau_results` karena itu punya **dua kolom sejak awal**: `arah_benar` dan `level_tersentuh`.

**`NO SIGNAL` tidak bisa menang dan tidak bisa kalah.** Catatan `winrate-17-persen-itu-bug-label`: WAIT pernah dicatat kalah di jalur lain, dan win rate yang dilaporkan 17% padahal akurasi sesungguhnya 44,2%. Sebuah `NO SIGNAL` tidak menyatakan arah, jadi tidak ada hasil yang bisa membenarkan atau menyalahkannya — mengklaim "seharusnya untung" berarti mengarang panggilan yang tidak pernah ARUNA buat.

Maka: **hanya baris berarah yang punya hasil.** `xau_results` tidak akan pernah memuat baris untuk `NO_SIGNAL`, dan itu ditegakkan constraint, bukan kebiasaan.

---

## Task 1: `xau_results` dan resolver

**Files:**
- Create: `migrations/0047_xau_hasil.sql`, `src/aruna/xau/resolve.py`
- Modify: `src/aruna/db/repositories/xau.py`
- Test: `tests/test_xau_resolve.py`

**Interfaces:**
- `class LevelTersentuh(StrEnum)` — `TARGET`, `STOP`, `TIDAK_SATU_PUN`
- `@dataclass(frozen=True, slots=True) class HasilXau` — `prediction_id: int`, `arah_benar: bool | None`, `level_tersentuh: LevelTersentuh`, `harga_tutup: Decimal`, `gerak_pct: Decimal`, `bar_dipakai: int`
- `def nilai_hasil(sinyal, geometri, arah, jalur: list[Candle], *, horizon_bar: int) -> HasilXau | None`
- `HORIZON_BAR: int = 48`

**Aturan penilaian, dan kenapa masing-masing:**

1. **Stop lebih dulu kalau keduanya tersentuh di satu bar.** Sebuah bar M5 punya `high` dan `low` tapi tidak punya urutan di dalamnya. Menganggap target duluan akan mengarang keberuntungan; menganggap stop duluan hanya membuat angkanya pesimis, dan angka pesimis yang salah lebih aman daripada angka optimis yang salah.
2. **`arah_benar` diukur pada tutup horizon**, bukan pada level yang tersentuh. Itu yang membuatnya pertanyaan ramalan dan bukan pertanyaan eksekusi.
3. **Jalur harga kurang dari horizon → `None`.** Belum selesai, bukan `TIDAK_SATU_PUN`. Yang kedua adalah hasil; yang pertama adalah ketiadaan hasil.

- [ ] **Step 1–6:** tulis test → merah → implementasi → hijau → cabut-uji aturan "stop lebih dulu" → commit.

---

## Task 2: Pelaporan yang jujur

**Files:** `src/aruna/xau/laporan.py`, `tests/test_xau_laporan.py`

- `akurasi_arah` dan `hit_rate` **dilaporkan terpisah**, tidak pernah digabung jadi satu angka.
- Keduanya `None` saat sampelnya nol — bukan 0%.
- Laporan menyebut jumlah sampel di sebelah tiap angka. Sebuah akurasi tanpa penyebutnya tidak bisa dibantah.
- Laporan menyebut **gerbang spread TIDAK AKTIF**, bukan lulus.

---

## Yang TIDAK dikerjakan sekarang, dan kenapa

`xau_training_samples`, belah TRAIN/VALIDATION/OUT-OF-SAMPLE, walk-forward, dan `xau_model_versions` **menunggu data**.

Per 2026-08-27 ada **tiga** keputusan XAU, ketiganya `NO_SIGNAL`, dan nol yang berarah. Membelah nol sampel menjadi tiga bagian menghasilkan tiga bagian kosong, dan pipeline yang berjalan di atasnya akan melaporkan angka yang terlihat seperti hasil. Itu persis yang spec larang.

Bangun pembelajarannya setelah ada sinyal berarah yang **selesai horizonnya** — dan jumlahnya cukup untuk membedakan 72% dari 90%, yang butuh puluhan, bukan belasan.
