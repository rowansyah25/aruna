# Phase 16 — Scenario Simulation Engine

> **Untuk pelaksana:** jalankan tugas per tugas. **Subagent-driven TIDAK dipakai** —
> operator melarang orkestrasi multi-agent di proyek ini.

**Goal:** ARUNA menghasilkan **bukti skenario** — beberapa kemungkinan
perkembangan berikut pemicu, invalidasi, dan kerapuhannya — untuk dipakai
Phase 14, tanpa pernah menjadi keputusan itu sendiri.

**Architecture:** Mesin skenario internal yang deterministik, dibangun di atas
bukti yang ARUNA sudah punya (regime, struktur, volume, funding, OI,
likuidasi, berita, konsensus agent). Dipicu peristiwa, bukan tiap scan.
MiroFish tidak dibangun — yang dibangun adalah **batas** tempat ia nanti
dicolok.

**Tech Stack:** Python 3.13, MySQL 8.4 lewat asyncmy, pytest, ruff.

---

## PERINGATAN YANG DICATAT, BUKAN DIDIAMKAN

**Phase 15 §30 melarang Phase 16 dimulai sebelum Intelligence Gate lulus, dan
§35 menaruh ACCURACY di urutan pertama kelulusan itu.**

Checklist 31 gate memang sudah lulus dan §31 sudah punya 25 skenario. Yang
belum ada adalah **angka akurasi pada kode hari ini**: 81 prediksi berarah
dikunci, 21 terselesaikan, 6 benar. Selang kepercayaan 95%-nya membentang
kira-kira 14%–50% — mencakup "jauh lebih buruk" dan "lebih baik dari garis
dasar" sekaligus. Ia tidak membuktikan apa pun.

Angka terakhir yang berarti: **44,2% melawan garis dasar "selalu BUY" 56,6%**,
diukur pada kode yang memutuskan di atas bukti basi — kode yang sudah tidak
ada.

Spec Phase 16 sendiri juga berstatus **FUTURE IMPLEMENTATION**, dan Phase 15
§29 melarang MiroFish dibangun sebelum ini.

**Operator diberi tahu ketiganya dan memutuskan tetap maju.** Itu keputusannya
untuk diambil. Yang tidak boleh terjadi adalah keputusan itu tidak tercatat,
jadi ia tercatat di sini.

**Konsekuensi praktisnya:** Phase 16 membangun lapisan bukti di atas analis
yang akurasinya belum terbukti. Kalau nanti akurasi ternyata tetap di bawah
garis dasar, skenario yang dihasilkan akan menjadi keterangan yang rapi tentang
tebakan yang buruk. Itu risiko yang disengaja, bukan yang terlewat.

---

## MIROFISH TIDAK ADA — DAN ITU BUKAN KELALAIAN

Dicari 2026-08-22 di seluruh repo:

| yang dicari | hasil |
|---|---|
| pustaka `mirofish` | tidak terpasang |
| `MIROFISH_*` di `.env` | tidak ada |
| dokumen antarmukanya | tidak ada |
| **kemampuan LLM apa pun** | **tidak ada** — nol `openai`, `anthropic`, `autogen`, `crewai`, `langchain`, `langgraph` |

Setiap agent ARUNA deterministik dan berbasis aturan. Satu-satunya penyebutan
MiroFish di repo adalah `scenario.py` yang ditulis untuk Phase 15 §29, dan
isinya larangan mengimplementasikannya.

**Adapter ke antarmuka yang belum pernah dilihat adalah karangan.** Jadi yang
dibangun rencana ini adalah **batasnya** — `Protocol` yang mengeja apa yang
MiroFish nanti harus sanggup jawab, plus jalur DEGRADED (§16.12) yang sudah
bekerja saat ia tidak ada. Ketika endpoint atau pustakanya tersedia,
implementasinya masuk tanpa menyentuh apa pun di sekitarnya.

Yang **dibangun penuh**: §16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9,
16.10, 16.11, 16.12, 16.13, 16.14, 16.15, 16.17, 16.18, 16.19, 16.20.

Yang **menunggu MiroFish**: pemanggilan simulasinya sendiri.

---

## Global Constraints

Disalin apa adanya dari spec, dan **tidak boleh dilanggar meski operator minta
"penuh"** — karena spec Phase 16 sendiri yang menetapkannya:

- **§16.16** MiroFish **TIDAK**: vote LONG, vote SHORT, execute trades,
  override Risk, override Quality, override Master Orchestrator, execute
  Binance orders. Ia **HANYA**: SIMULATE, COMPARE, REPORT, PROVIDE SCENARIO
  EVIDENCE.
- **§16.18** Phase 16 tidak menghasilkan FINAL LONG atau FINAL SHORT. Ia
  menghasilkan **SCENARIO EVIDENCE**. Keputusan final tetap di Phase 14.
- **§16.20** Tidak ada auto execution.
- **§16.1** Hasil simulasi berlabel **SIMULATION EVIDENCE**, bukan FACT dan
  bukan GUARANTEED PREDICTION.
- **§16.6** Scenario weight **bukan** probabilitas pasar terkalibrasi. Ia
  keluaran simulasi relatif.
- **§16.19** Jangan mengubah model hanya karena satu kegagalan simulasi.
- ARUNA tetap **ANALYST ONLY**.

### Aturan kerja proyek ini

- **Bukan git repository.** Tidak ada commit. Penggantinya **cabut-uji**:
  cabut barisnya, jalankan testnya, pastikan MERAH, kembalikan.
- **`pytest` sendirian**, tanpa menyunting kode saat ia berjalan.
- Test dan docstring bahasa Indonesia; docstring menjelaskan **kenapa**.
- Berkas migrasi yang ditulis tapi belum diterapkan **menghentikan ARUNA** —
  terapkan lewat `aruna migrate`, jangan tinggalkan menggantung.
- `.\.venv\Scripts\python.exe`. PowerShell 5.1, pakai `;`. Jangan menulis
  berkas lewat `Set-Content`.

---

## Struktur berkas

`scenario.py` yang ada sekarang (Phase 15 §29) **dipromosikan menjadi paket**.
Isinya yang sekarang - `ScenarioEngineInterface` dan `Kemungkinan` - pindah dan
tumbuh; penjaga anti-eksekusinya ikut, karena §16.16 menuntut hal yang sama.

**Dibuat:**

- `src/aruna/scenario/__init__.py` — permukaan publik
- `src/aruna/scenario/models.py` — §16.7, §16.15: `Skenario` dengan sebelas
  bidang, `Kerapuhan`, `Invalidasi`, `HasilSkenario`
- `src/aruna/scenario/pemicu.py` — §16.2: peristiwa apa yang membangunkan
  simulasi dalam
- `src/aruna/scenario/masukan.py` — §16.3: rakit masukan yang **sudah
  divalidasi**; tolak dump mentah
- `src/aruna/scenario/pertanyaan.py` — §16.4: susun pertanyaan simulasi yang
  spesifik
- `src/aruna/scenario/mesin.py` — §16.5, §16.8: mesin skenario internal
- `src/aruna/scenario/banding.py` — §16.9, §16.10: dominansi, konflik, risiko,
  kerapuhan
- `src/aruna/scenario/bukti.py` — §16.1, §16.18: bungkus jadi SCENARIO EVIDENCE
- `src/aruna/scenario/adapter.py` — §16.12, §16.13, §16.16: batas MiroFish,
  timeout, DEGRADED
- `src/aruna/scenario/evaluasi.py` — §16.19: bandingkan skenario dengan hasil
- `src/aruna/db/repositories/scenario.py` — penyimpanan
- `migrations/0037_scenario.sql`

**Diubah:**

- `src/aruna/upkeep/loop.py` — fase simulasi berpemicu
- `src/aruna/app.py`, `src/aruna/cli.py` — perangkaian
- `src/aruna/futures/service.py` — bukti skenario menumpang jalur yang sama
  dengan bukti ingatan

**Dihapus:** `src/aruna/scenario.py` (jadi paket).

---

## Task 1: Bentuk skenario (§16.7, §16.15)

**Files:** buat `src/aruna/scenario/models.py`, `tests/test_scenario_models.py`

**Produces:**
- `Skenario` beku: `scenario_id`, `market`, `asset`, `timestamp`, `nama`,
  `deskripsi`, `kondisi_awal`, `pemicu`, `perkembangan`, `invalidasi`, `risiko`,
  `keyakinan`, `bobot`, `bukti`, `versi_simulasi`
- `Kerapuhan(StrEnum)`: `RAPUH`, `KOKOH`
- `HasilSkenario(StrEnum)`: `BENAR`, `SALAH`, `SEBAGIAN`, `BELUM`

**Yang ditegaskan testnya:**

- [ ] Sebelas bidang §16.15 ada, dieja satu per satu supaya penghapusan salah
      satunya gagal keras
- [ ] `bobot` **bukan** probabilitas: tidak ada metode bernama `probability`,
      `chance`, atau `peluang_profit`, dan `to_dict()` menyertakan label
      `SIMULATION EVIDENCE` (§16.1)
- [ ] Skenario **tanpa** `invalidasi` ditolak konstruktornya (§16.11) — skenario
      yang tidak bisa salah bukan skenario
- [ ] `RAPUH` ketika `invalidasi` bergantung pada satu syarat tunggal (§16.10)
- [ ] Tidak ada bidang `direction`/`decision`/`LONG`/`SHORT` (§16.18) — dijaga
      AST, bukan pencarian teks

**Cabut-uji:** buang penolakan skenario tanpa invalidasi → MERAH.

---

## Task 2: Pemicu (§16.2)

**Files:** buat `src/aruna/scenario/pemicu.py`, `tests/test_scenario_pemicu.py`

**Produces:**
- `Peristiwa(StrEnum)` — tiga belas pemicu yang spec sebut
- `deteksi(konteks) -> frozenset[Peristiwa]`
- `layak_simulasi(peristiwa) -> bool`

**Yang ditegaskan testnya:**

- [ ] Scan normal menghasilkan **kosong** — §16.2 melarang MiroFish di tiap scan
- [ ] Tiap pemicu yang buktinya tersimpan bisa dihasilkan; yang tidak
      (`cross-market conflict` tanpa data lintas pasar) **disebut lewat test**,
      bukan dihilangkan diam-diam
- [ ] `strong disagreement antar-agent` memakai `council_sessions.disagreement`
      yang sudah ada, bukan ambang baru
- [ ] Ambangnya dipinjam dari yang sudah terukur — `ANOMALY_VOLUME_RATIO`,
      `EXTREME_RATE`, `SIGNIFICANT_PCT`, `HIGH_VOL_RASIO` — **bukan angka baru**
      yang dipas-paskan (§32 Phase 15)

**Cabut-uji:** buat `deteksi` selalu memulangkan satu pemicu → test scan normal
MERAH.

---

## Task 3: Masukan tervalidasi (§16.3)

**Files:** buat `src/aruna/scenario/masukan.py`, `tests/test_scenario_masukan.py`

- [ ] Hanya bidang yang spec sebut yang lolos; sisanya dibuang
- [ ] Data ber-`DataQuality` buruk **ditolak masuk** — simulasi di atas data
      basi menghasilkan skenario yang rapi dan salah
- [ ] Ukuran muatan berbatas (§16.14): dump mentah ditolak dengan pesan yang
      menyebut ukurannya

**Cabut-uji:** longgarkan penolakan mutu → MERAH.

---

## Task 4: Pertanyaan simulasi (§16.4)

**Files:** buat `src/aruna/scenario/pertanyaan.py`, `tests/test_scenario_pertanyaan.py`

- [ ] Pertanyaan menyebut kondisi konkret, bukan "apakah X akan naik"
- [ ] Penjaga menolak bentuk ya/tidak — dijaga daftar pola, dan alasannya
      ditulis: pertanyaan ya/tidak memaksa simulasi berpihak sebelum ia
      mensimulasikan apa pun

**Cabut-uji:** buang penjaga bentuk ya/tidak → MERAH.

---

## Task 5: Mesin skenario internal (§16.5, §16.8)

**Files:** buat `src/aruna/scenario/mesin.py`, `tests/test_scenario_mesin.py`

- [ ] Selalu menghasilkan **minimal tiga**: bullish continuation, bearish
      reversal, false breakout/alternative
- [ ] Skenario tambahan (§16.5) hanya muncul ketika buktinya ada — likuidasi
      berantai butuh data likuidasi, news-driven butuh berita
- [ ] **Efek orde-dua** (§16.8): rantai konsekuensi tersimpan sebagai
      `perkembangan`, bukan satu kalimat
- [ ] Bobot menjumlah 100 **dan** dilabeli relatif (§16.6)
- [ ] Deterministik: masukan sama → skenario sama. Mesin skenario yang
      berubah-ubah tanpa sebab tidak bisa dievaluasi (§16.19)

**Cabut-uji:** kurangi menjadi dua skenario → MERAH.

---

## Task 6: Perbandingan dan kerapuhan (§16.9, §16.10)

**Files:** buat `src/aruna/scenario/banding.py`, `tests/test_scenario_banding.py`

- [ ] `dominansi`, `konflik`, `risiko`, `kerapuhan` dihitung atas **seluruh**
      skenario, bukan yang terbaik saja (§16.9 mengejanya)
- [ ] Dominansi tipis dilaporkan sebagai konflik, bukan sebagai pemenang
- [ ] Skenario yang bergantung pada satu syarat ditandai `RAPUH` (§16.10)

**Cabut-uji:** ambil skenario berbobot tertinggi saja → MERAH.

---

## Task 7: Batas MiroFish, timeout, DEGRADED (§16.12, §16.13, §16.16)

**Files:** buat `src/aruna/scenario/adapter.py`, `tests/test_scenario_adapter.py`

- [ ] `Protocol` mengeja apa yang MiroFish harus jawab — **tanpa implementasi**
- [ ] Tidak ada metode eksekusi apa pun; dijaga AST (§16.16)
- [ ] MiroFish absen → status `DEGRADED`, dan **mesin internal tetap jalan**
      (§16.12)
- [ ] Timeout menghasilkan `SIMULATION TIMEOUT`, dan hasil yang telat
      **dibuang, bukan dipakai** (§16.13)
- [ ] Kegagalan adapter tidak pernah menjatuhkan siklus

**Cabut-uji:** buat kegagalan adapter melempar → MERAH.

---

## Task 8: Bukti skenario (§16.1, §16.18)

**Files:** buat `src/aruna/scenario/bukti.py`, `tests/test_scenario_bukti.py`

- [ ] Keluarannya `BuktiSkenario`, dan **tidak punya** bidang arah apa pun
- [ ] Label `SIMULATION EVIDENCE` melekat, tidak bisa dilepas
- [ ] Penjaga AST menolak `Decision`, `direction`, `LONG`, `SHORT` di seluruh
      paket `scenario` — sama seperti penjaga kalibrator dan finalizer

**Cabut-uji:** tambahkan bidang `direction` → MERAH.

---

## Task 9: Penyimpanan berbatas (§16.14, §16.15)

**Files:** buat `src/aruna/db/repositories/scenario.py`,
`migrations/0037_scenario.sql`, `tests/test_scenario_repo.py`

- [ ] Satu baris per skenario dengan sebelas bidang §16.15
- [ ] **Bukan** menyimpan seluruh aktivitas simulasi — pelajaran Phase 15.1:
      `market_snapshots` 62% basis data karena tiap amatan ditulis
- [ ] Retensi ikut `RENCANA` yang sudah ada; skenario **bukan** tabel
      terlindung §31 karena ia bukan keputusan
- [ ] Migrasi diterapkan di tugas yang sama — berkas menggantung menghentikan
      ARUNA

**Cabut-uji:** buang batas penyimpanan → MERAH.

---

## Task 10: Perangkaian berpemicu (§16.17)

**Files:** ubah `src/aruna/upkeep/loop.py`, `src/aruna/app.py`,
`src/aruna/cli.py`; buat `tests/test_scenario_terpasang.py`

- [ ] Fase simulasi berjalan **hanya** saat pemicu menyala
- [ ] Penjaga AST: `scenario=` sampai ke loop, dan pemicunya dipanggil
- [ ] Batas konkurensi dan antrean (§16.14)
- [ ] Nol simulasi **dicatat** — nol karena tidak ada peristiwa dan nol karena
      fasenya mati terlihat sama dari luar

**Cabut-uji:** cabut `scenario=` dari `app.py` → MERAH.

---

## Task 11: Pelacakan hasil dan evaluasi diri (§16.19)

**Files:** buat `src/aruna/scenario/evaluasi.py`, `tests/test_scenario_evaluasi.py`

- [x] Bandingkan skenario dengan hasil pasar → `BENAR` / `SALAH` / `SEBAGIAN`
- [x] Skenario yang **invalidasinya terpicu** dinilai terpisah dari yang
      arahnya salah — keduanya kegagalan yang berbeda
- [x] Ambang sampel sebelum ada angka yang dilaporkan; satu kegagalan simulasi
      **tidak** mengubah apa pun (§16.19)

**Cabut-uji:** buang ambang sampel → MERAH.

### Butir kedua sempat lulus di atas konstanta (2026-08-23)

Pembedaannya ada di `evaluasi.py` sejak awal — `Putusan.diinvalidasi`,
`Putusan.gagal_jujur`, `Akurasi.diinvalidasi` — lengkap dengan testnya. Tapi
`nilai_dari_pasar`, **satu-satunya penilai yang dipanggil produksi**, menuliskan
`diinvalidasi=False` di keenam jalurnya, dan `catat_hasil` tidak punya kolom
untuk menyimpannya. Jadi `gagal_jujur` selalu `False`, `Akurasi.diinvalidasi`
selalu nol, dan 928 baris `SALAH` tersimpan tanpa satu pun bisa dipisahkan.

Yang membedakannya dari tiga kejadian sebelumnya di proyek ini: fungsinya
**dipanggil**. Yang tidak pernah diberikan adalah masukan pembedanya. Test
unitnya hijau karena ia menguji `nilai_satu()` — jalur yang menerima syarat
terpicu sebagai parameter, dan yang produksi tidak pakai.

Ditutup dengan urutan yang benar — hitung dulu, baru simpan:

1. `kerumunan.invalidasi_terpicu(nama, jejak)`, memakai `AMBANG_ARAH` dan
   `AMBANG_SEPI` yang sama dengan `klasifikasi_jejak`. Syarat batal yang
   memakai garis berbeda dari garis yang mendefinisikan keluarganya menjawab
   pertanyaan yang lain.
2. `bool | None` — `None` berarti **tidak bisa diperiksa** (syarat yang
   menyebut volume, order book, atau berita), bukan "tidak terpicu".
3. Migrasi 0040, `diinvalidasi TINYINT NULL`; baris lama tetap NULL karena
   mereka memang dinilai kode yang tidak pernah memeriksanya.
4. `ringkas_peringatan()` + `upkeep.skenario_peringatan`, dilaporkan
   **terpisah** dari akurasi: satu angka yang menjumlahkan "salah dan
   memperingatkan" dengan "salah dan diam" akan MEMBAIK ketika skenario
   berhenti menyebutkan syarat batalnya.

Terukur sebelum deploy: keenam keluarga yang benar-benar muncul di 2.899 baris
tersimpan **semuanya bisa diperiksa** — `News-Driven Reversal` dan
`High Volatility` tidak pernah dihasilkan, jadi kolomnya akan terisi penuh.

**Cabut-uji:** `diinvalidasi = False` di `nilai_dari_pasar` → MERAH ·
`diinvalidasi=None` di penilai → MERAH · `_laporkan_peringatan` dicabut → MERAH.

---

## Task 12: Ruff, suite penuh, restart, ukur

- [x] `ruff check src tests`
- [x] Suite penuh, sendirian
- [x] `aruna migrate` (0037–0040), lalu restart, verifikasi lewat **StartTime**
- [x] Ukur: berapa pemicu menyala, berapa simulasi jalan, berapa skenario
      tersimpan, ukuran basis data, `level=error` harus 0
- [x] Laporkan apa adanya, termasuk kalau pemicunya tidak pernah menyala

### Terukur 2026-08-23 01:20

**Skenario tersimpan: 3.048 baris**, seluruhnya `INTERNAL`. `internal-2` 2.559,
`internal-1` 489 (mesin lama, berhenti 2026-08-21 21:37).

**Penilaian diri (§16.19).** 1.480 dinilai: SALAH 68,0%, BENAR 20,0%,
SEBAGIAN 12,0%.

Angka 68% itu **tidak boleh dibaca sendirian**, dan itu justru gunanya kolom
`diinvalidasi` yang baru:

| versi | salah | memperingatkan | tak terperiksa |
|---|---|---|---|
| internal-2 | 680 | **40/51 (78%)** | 629 |
| internal-1 | 326 | belum ada | 326 |

Dari skenario salah yang syarat batalnya bisa diperiksa, 78% **memperingatkan
lebih dulu** - mesin menyebutkan syarat batalnya dan syarat itu terjadi. Itu
simulasi yang bekerja, bukan yang meleset. Sampelnya masih 51; 629 sisanya NULL
karena dinilai kode yang belum memeriksanya sama sekali.

### internal-2 melewati ambang sampel — dan pembobotannya di bawah acak

Terukur 2026-08-23 01:43, saat `simulasi` melewati `MINIMUM_DINILAI`=200 dan
angkanya dilepas untuk pertama kali:

| versi | simulasi | skenario/simulasi | cakupan | teratas | acak |
|---|---|---|---|---|---|
| internal-1 | 163 | 3,00 | 112/163 (ditahan) | **0/163** | 33,3% |
| internal-2 | 222 | 4,82 | **202/222 = 91,0%** | **27/222 = 12,2%** | 20,7% |

**Cakupan 91% dan teratas 12,2% menunjuk ke satu kesimpulan: kosakata mesinnya
benar, urutannya yang salah.** Keluarga yang benar-benar terjadi ADA di antara
skenario yang dihasilkan pada 202 dari 222 simulasi - mesinnya tahu
kemungkinannya. Tapi skenario berbobot tertinggi hanya benar 27 kali, sementara
menebak acak di antara 4,82 skenario akan benar sekitar 46 kali.

Selisihnya bukan derau: simpangan bakunya sekitar 6,1, jadi 27 berada kira-kira
**3,1 simpangan baku DI BAWAH** tebakan acak. Pembobotannya tidak sekadar tidak
membantu - ia sistematis menaruh keluarga yang benar di peringkat bawah.

Ini **produk pertama bagian 16.19**, dan persis gunanya ia ada. Rencana ini
menyatakan sejak awal bahwa bobot tidak terkalibrasi (§16.6) dan yang dinilai
cuma urutannya - sekarang urutannya sudah dinilai.

#### Mekanismenya ditemukan 2026-08-23, dan bukan bug kode

Ditelusuri atas 260 simulasi `internal-2` yang sudah dinilai:

| keluarga | pangsa BOBOT | pangsa yang BENAR-BENAR terjadi |
|---|---|---|
| False Breakout | **7,2 rata-rata (maks 12)** | **46,2%** |
| Bullish Continuation | 31,1 | 16,9% |
| Sideways | 29,8 | 8,1% |
| Bearish Reversal | 26,4 | 28,8% |

`False Breakout` **nol dari 260** kali diberi bobot tertinggi. Ia tidak pernah
seri dan tidak pernah kalah tipis - jaraknya 15 poin. Dan skenario yang benar
paling sering duduk di **peringkat 4** (104 dari 260), bukan tersebar merata.
Itu tanda pembalikan sistematis.

Sebabnya bukan cacat di `mesin.py`. `LANTAI_WAJIB` bekerja sesuai janjinya, dan
bobot memang dihitung sebagai pangsa lintasan. Yang tidak berlaku adalah
asumsinya: **pangsa lintasan di kisi premis bukan frekuensi pasar.** Mesin
kerumunan jarang menghasilkan perjalanan pulang-pergi melewati titik awal -
`klasifikasi_jejak` menuntut lonjakan >= `AMBANG_ARAH` lalu kembali melewati
nol dalam dua belas ronde - sementara di pasar, tembusan kecil yang gagal
justru kejadian paling biasa.

Jadi ada dua hal yang berbeda yang disamakan: bobot berarti "berapa banyak
kombinasi premis mendarat di sini", sementara Task 11 menilainya sebagai
"seberapa mungkin ini terjadi".

**Memperbaikinya adalah perubahan model, bukan perbaikan bug**, dan proyek ini
sudah punya jalurnya: `ModelProposal` (SPEC 44) - hipotesis tertulis, dijalankan
`SHADOWED`, divalidasi **out-of-sample** melawan minimal 100 prediksi
terselesaikan dengan ambang sigma yang naik mengikuti jumlah varian, lalu
disetujui manusia yang namanya tercatat. Menyetel bobotnya sekarang atas 260
amatan in-sample adalah persis yang `Verdict.WITHIN_NOISE` ada untuk menolak.

**Pemicu: 8 dari 13 pernah menyala.**

```
PERUBAHAN_REGIME         999    BREAKOUT_BESAR          921
SELISIH_PENDAPAT_TAJAM   861    EFEK_ORDE_DUA           795
VOLATILITAS_ABNORMAL     473    KETIDAKPASTIAN_TINGGI   357
VOLUME_EKSTREM           343    ANOMALI_OPEN_INTEREST     5
```

`ANOMALI_OPEN_INTEREST` menyala sesudah `futures_metrics` punya siklus kedua -
anomali OI adalah PERUBAHAN, dan perubahan butuh dua titik.

Yang belum pernah menyala, dan sebabnya masing-masing berbeda:

* `BERITA_BESAR`, `BREAKDOWN_BESAR`, `ANOMALI_FUNDING` - tersambung, kondisinya
  belum terjadi.
* `LONJAKAN_LIKUIDASI` - buntu, dan diuji sebagai mati.
* `KONFLIK_LINTAS_PASAR` - tersambung 2026-08-22 dan masih nol. **Sebabnya
  belum bisa dibedakan**, jadi `arah_kohort` ditambahkan ke log tiap siklus:
  selalu `None` berarti lantainya yang salah; berarah tapi pemicunya diam
  berarti memang tidak ada aset yang melawan.

**Selektivitas.** Diadu terkendali atas 660 titik aset-bar yang identik:
aturan lama 21,8% (4,4 dari 20 per bar), aturan baru 15,9% (3,2) - **27% lebih
sedikit**, konsisten di kedelapan jam. Perbandingan sebelum/sesudah restart
(10,2 → 5,1 dari 20) **tidak dipakai**: garis dasarnya jatuh di jam paling
bergolak dalam data dan pembandingnya di periode tenang.

**Basis data: 415 MB.** `market_snapshots` 146,8 MB (35%, 420.550 baris) masih
yang terbesar - tabel yang sama yang jadi pelajaran Phase 15.1.
`scenario_evidence` tidak masuk delapan besar.

**`level=error` bukan nol, dan itu dicatat.** Dua sebab, keduanya di luar
kendali fase skenario:

* `daily.silence_failed` - tiap malam 00:00 WIB, query laporan harian melewati
  `max_statement_time`. Berulang 21 dan 22 Agustus. Ditandai sebagai tugas
  terpisah.
* `upkeep.skenario_nilai_failed` - `ZeroDivisionError`, satu menit sesudah
  kolom `diinvalidasi` dipasang: seluruh 928 baris SALAH yang ada NULL, jadi
  penyebut "yang bisa diperiksa" nol. Diperbaiki di `_bagian`, bukan di
  pemanggilnya.

### Dua bug lama dibuktikan BERHENTI, bukan sekadar tak terlihat

"Terakhir terlihat jam sembilan" tidak membuktikan apa pun sendirian - fasenya
mungkin cuma berhenti berjalan. Yang membuktikan: kejadiannya berhenti
**sementara fase yang sama terus berjalan**.

| bug | gagal terakhir | fase berjalan sesudahnya |
|---|---|---|
| `skenario.nilai_gagal` (zona waktu) | jam 09, 80x | **44 sapuan, nol gagal** |
| `upkeep.scenario_failed` (`max()` kosong) | jam 11, 54x | **1.055 siklus, nol gagal** |

### Gerbang sampel yang bocor (2026-08-23)

`_laporkan_peringatan` mengoper ``cukup=True`` tanpa syarat dan mencetak
"102/131 = 77,9%" seolah sudah mapan - sementara modul yang **sama** menahan
angka akurasi sampai sampelnya cukup. Bagian 16.19 menuntut ambang sampel
sebelum ANGKA APA PUN dilaporkan, bukan sebelum sebagian angka.

Ambangnya **bukan** `MINIMUM_DINILAI`: itu menjaga akurasi, yang penyebutnya
SIMULASI, sementara ini pangsa di antara SKENARIO yang salah. Meminjam ambang
untuk pertanyaan berbeda sudah dua kali menjadi bug di proyek ini.
:data:`MINIMUM_PERINGATAN` = 100, dan aritmetikanya yang memilih: galat baku
sebuah pangsa paling besar di ``p=0,5``, yaitu ``0,5/sqrt(n)`` - pada seratus
itu lima poin, cukup untuk membedakan 78% dari 50%.

---

## Self-review

**Cakupan spec.** §16.1 Task 1+8 · §16.2 Task 2 · §16.3 Task 3 · §16.4 Task 4 ·
§16.5 Task 5 · §16.6 Task 1+5 · §16.7 Task 1 · §16.8 Task 5 · §16.9 Task 6 ·
§16.10 Task 6 · §16.11 Task 1 · §16.12 Task 7 · §16.13 Task 7 · §16.14 Task
3+9+10 · §16.15 Task 1+9 · §16.16 Task 7+8 · §16.17 Task 10 · §16.18 Task 8 ·
§16.19 Task 11 · §16.20 Task 7+8+12.

**Celah yang disebut, bukan disembunyikan:**

- **Pemanggilan MiroFish** menunggu mesin yang belum ada. Batasnya dibangun;
  isinya tidak.
- ~~**`cross-market conflict`** (§16.2) butuh data lintas pasar yang belum
  dikumpulkan bersamaan.~~ **Ditutup 2026-08-22.** Bacaan harfiahnya — CRYPTO
  melawan IDX pada satu titik waktu — hampir tidak pernah tersedia karena IDX
  tutup saat sebagian besar pemindaian crypto berjalan, dan menunggunya berarti
  membiarkan pemicunya mati selamanya. Yang dipakai: **aset yang bergerak
  melawan kohortnya**, dihitung dari 20 aset yang sudah dipegang fase skenario
  sekaligus. Kedua bacaan ditulis berdampingan di `Peristiwa` supaya tidak ada
  yang mengira yang pertama sudah terpenuhi.
- ~~**`liquidation spike`** (§16.2) tinggal satu-satunya yang tanpa sumber.~~
  **Ditutup 2026-08-23, dan `TANPA_SUMBER_DATA` sekarang KOSONG.** Bacaan
  harfiahnya memang tidak tersedia — Binance menarik endpoint REST-nya dan
  stream `forceOrder` di jaringan ini menerima koneksi tanpa mengirim data.
  Tapi likuidasi punya sidik jari yang terbaca dari dua deret yang **sudah**
  disimpan: gerak harga keras bersamaan dengan open interest yang MENYUSUT.
  Uang baru membuka posisi; uang yang lari menutupnya.

  Bukan konsep baru — ARUNA sudah memakainya di
  `futures.openinterest.EXHAUSTION`, dan ambangnya dipinjam dari pertanyaan
  yang sama (`SIGNIFICANT_PCT` = "pergeseran nyata pada berapa posisi yang
  terbuka"). Dua arah, karena long yang terlempar dan short yang tertekan
  sama-sama penutupan paksa; memilih satu berarti menyelundupkan arah ke dalam
  pemicu, dan §16.18 menutup itu.

  Daftarnya dibiarkan berdiri walau kosong: menghapusnya menghilangkan tempat
  bertanya "apakah masih ada pemicu tanpa sumber", dan pertanyaan itu perlu
  jawaban yang bisa diperiksa — bukan disimpulkan dari ketiadaan.
- **Bobot skenario tidak terkalibrasi**, dan §16.6 memang menyatakannya. Ia
  tidak akan pernah dibandingkan dengan hasil sebagai probabilitas — hanya
  urutannya yang dinilai (Task 11).

**Konsistensi tipe.** `Skenario` lahir di Task 1 dan dipakai Task 5, 6, 8, 9,
11. `Peristiwa` hanya di Task 2. `BuktiSkenario` hanya di Task 8.
`HasilSkenario` di Task 1, dinilai di Task 11.

---

## Hasil pelaksanaan — 2026-08-22

Task 1–12 selesai. Tiap task diverifikasi lewat cabut-uji: perbaikannya dicabut,
testnya dipastikan MERAH, lalu dikembalikan. Repositori ini bukan git, jadi
cabut-uji adalah satu-satunya bukti bahwa testnya menguji sesuatu.

**Yang terukur di produksi**, sesudah restart 2026-08-22 03:45:41 (diverifikasi
lewat `StartTime`, bukan jumlah proses):

| Ukuran | Nilai |
|---|---|
| Siklus fase skenario berjalan | 3 |
| Aset dipertimbangkan per siklus | 20 |
| Pemicu menyala | 1 dari 3 siklus (DOGE/USDT, `BREAKOUT_BESAR`) |
| Skenario tersimpan | 3 |
| Jumlah bobot | 100 |
| `level=error` sejak restart | 0 |
| Basis data | 337,6 MB (tak berubah) |

Skenario DOGE/USDT: False Breakout 38, Bullish Continuation 37, Bearish Reversal
25. Selisih teratas **1**, jauh di bawah `AMBANG_DOMINAN` = 10, jadi
`bandingkan()` melaporkannya sebagai **konflik** dan bukan pemenang — tembusan
tanpa konfirmasi volume memang ambigu, dan itu yang seharusnya dikatakan.

**Dua cacat ditemukan saat pelaksanaan, keduanya di kode yang kutulis sendiri:**

1. **BOM UTF-8 di `src/aruna/scenario/__init__.py`.** Python mengimpornya
   diam-diam; `ast.parse` menolaknya. Seluruh suite hijau kecuali satu test SQL
   yang gagal dengan pesan tentang karakter tak tercetak — enam penjaga AST
   Phase 16 sendiri rapuh terhadapnya. Dua berkas test lama ternyata ber-BOM
   juga, tanpa satu pun penjaga menyentuhnya. Ditambahkan
   `tests/test_berkas_bersih.py` yang menamai masalahnya langsung dan mencakup
   `tests/` juga.
2. **`pemicu` 245 karakter di kolom VARCHAR(255).** Terukur lewat tulisan
   sungguhan ke tabel, bukan lewat test dengan double. Sisa sepuluh karakter,
   dan repositori memotongnya diam-diam dengan `[:255]`. Migrasi 0038
   memperlebar ke 512, pemotongan diam-diamnya diganti peringatan, dan sebuah
   test menghitung kasus terburuk **dari enum-nya sendiri** supaya pemicu
   keempat belas gagal di CI alih-alih terpotong di produksi.

**Celah yang tetap terbuka, disebut apa adanya:**

- **Enam dari tiga belas pemicu §16.2 yang tersambung.** Yang lahir dari
  pemindai. Tujuh sisanya tidak: regime dan mutu dihitung di jalur keputusan
  *sesudah* fase ini berjalan; `disagreement` lahir di council yang digelar per
  bar; funding dan open interest hidup di proses `futures-loop` terpisah;
  likuidasi dan konflik lintas-pasar datanya memang belum ada sama sekali.
  Ditulis di docstring `upkeep/skenario.py` karena "pemicunya tidak menyala" dan
  "pemicunya tidak tersambung" terlihat identik di log.
- **MiroFish tetap tidak ada.** Yang dibangun batasnya — `Protocol` tanpa
  implementasi dan jalur `DEGRADED` yang berjalan tiap siklus, bukan cabang yang
  belum pernah diambil.
- **Akurasi §16.19 belum punya satu angka pun**, dan tidak akan punya sebelum
  200 skenario tuntas. Ambangnya dipinjam dari PASAL 15.44, bukan dipilih ulang.

**§34 Phase 15 berlaku di sini:** Phase 16 tidak disebut "COMPLETE" karena
kodenya berjalan. Yang bisa dikatakan: seluruh dua puluh pasalnya punya kode dan
test, jalur produksinya terbukti sekali dari pemicu sampai baris tersimpan, dan
tiga celah di atas belum tertutup.

---

## Lanjutan — §16.19 disambungkan, 2026-08-22

**Celah terbesar ternyata bukan yang kucatat di atas.** `aruna.scenario.evaluasi`
punya **nol pemanggil** di seluruh `src/`; begitu juga `belum_dinilai`,
`catat_hasil`, dan `ringkas_akurasi` di repositorinya. §16.19 ditulis, diuji,
diekspor - dan tidak pernah berjalan. Tiap skenario tersimpan dengan `hasil`
NULL selamanya.

Cacat yang sama sudah muncul tiga kali sebelumnya di proyek ini, dan semuanya
lulus test unitnya.

**Bagaimana skenario dinilai sekarang.** Bukan dengan membaca kalimat
invalidasinya - kalimat tidak bisa diperiksa mesin. Yang diperiksa **bentuk
jalan harganya**: candle sesudah skenario lahir diubah menjadi jejak dalam
satuan ATR, lalu diklasifikasikan dengan `klasifikasi_jejak` — fungsi yang
**sama persis** dengan yang melahirkan keluarganya. Klasifikator yang berbeda
antara menghasilkan dan menilai membuat angkanya mengukur sesuatu yang lain.

Jendelanya dua belas bar 15m: pemindai bekerja pada 15m dan mesin kerumunan
berjalan dua belas ronde, jadi tiga jam adalah horizon yang memang
disimulasikan — bukan angka yang dipilih terpisah.

**Tiga putusan, dan garisnya:** BENAR = keluarga yang sama; SEBAGIAN = keluarga
berbeda tapi arah yang keluarga itu klaim tetap terjadi (hanya berlaku bagi dua
keluarga yang memang mengklaim arah); SALAH = selain itu. Keluarga yang
mengklaim **bentuk** tidak punya SEBAGIAN — bentuk terjadi atau tidak.

**Dua bug ditemukan saat menyambungkannya, keduanya lolos dari 27 test:**

1. `belum_dinilai` memulangkan DATETIME telanjang dari MySQL, dibandingkan
   dengan `close_time` candle yang sadar-zona. **40 dari 40 gagal** dengan
   `can't compare offset-naive and offset-aware datetimes`. Double-nya
   memulangkan daftar kosong — bentuk yang tidak pernah bisa memperlihatkannya.
2. Candle diambil sebagai **bar terbaru**, bukan jendela di sekitar kelahiran
   skenarionya. Skenario berumur tiga belas jam mendapat jendela yang mulai
   empat jam **sesudah** ia lahir: 40 dari 40 dilaporkan belum bisa dinilai, dan
   tunggakan lama tidak akan pernah terkuras. Ditambahkan
   `MarketDataRepository.candles_between`.

**Terukur di produksi sesudah perbaikan:** `diperiksa=40 dinilai=40 belum=0
gagal=0`.

**Angka §16.19 pertama, dan ia ditahan dengan benar:**

| | |
|---|---|
| Dinilai | 40 (`internal-1`) |
| BENAR / SEBAGIAN / SALAH | 13 / 0 / 27 |
| Akurasi dilaporkan | **DITAHAN** — butuh 200, baru 40 |

Per keluarga: Bullish Continuation 13/13 BENAR, Bearish Reversal 0/13, False
Breakout 0/14. Pola itu **bukan mutu model** — keempat puluhnya lahir dari
segelintir simulasi pada satu episode pasar yang bergerak naik, jadi satu
keluarga benar seluruhnya dan sisanya salah seluruhnya. Persis kenapa ambang
dua ratus sampel ada, dan ia bekerja: tidak satu pun angka akurasi dilaporkan.

`SEBAGIAN` belum pernah menyala di produksi — seluruh yang benar adalah
kecocokan persis. Jalurnya ada dan diuji, tapi belum teruji oleh pasar.

---

## Lanjutan — tiga pemicu §16.2 disambungkan, 2026-08-22

**Aku sempat melaporkan tujuh dari tiga belas pemicu "butuh data dari jalur atau
proses lain", dan untuk tiga di antaranya itu salah.** Jalur keputusan menghitung
regime, skor mutu, dan selisih pendapat council setiap kali ia berjalan, lalu
menuliskannya ke `signal_snapshots` (14.449 baris) dan `council_sessions` (8.688
baris). Yang tidak ada cuma pembacanya.

Ditambahkan `KonteksPemicuRepository`: tiga kueri per siklus, dibatasi umur satu
jam. Pemicu tersambung naik dari **enam menjadi sembilan** dari tiga belas.

**Yang tetap tanpa sumber, diperiksa langsung di skema:** funding rate dan open
interest tidak ada di tabel mana pun — `futures_plans.funding_cost_pct` adalah
biaya turunan atas horizon sebuah rencana, bukan rate-nya, dan `futures-loop`
memakai keduanya di memori tanpa menuliskannya. Bersama likuidasi dan konflik
lintas-pasar, empat pemicu masih mati.

**Empat bug ditemukan sesudah menyambungkannya, semuanya di produksi:**

1. **`max() iterable argument is empty`** — kunci satu-simulasi-per-bar
   diturunkan dari stempel peristiwa pemindai, yang mengandaikan tiap pemicu
   lahir dari sana. Delapan `upkeep.scenario_failed` dalam lima menit. Kuncinya
   sekarang bar siklus, yang selalu ada.
2. **Skenario tanpa kondisi.** Pemicu yang menyala tanpa peristiwa pemindai
   menghasilkan pertanyaan tanpa kondisi konkret, dan §16.4 menolaknya — jadi
   pemicunya menyala lalu mati di langkah berikutnya. Kondisi sekarang disusun
   dari apa yang menyala, dengan angkanya: *"mutu sinyal 42 di bawah ambang 60"*.
3. **`PERUBAHAN_REGIME` menyala 15/15.** Pembacanya mengambil dua regime
   *berbeda* terakhir dalam jendela satu jam, tanpa peduli kapan peralihannya
   terjadi — dan mencampur horizon 15m dengan 1d. Diperbaiki: satu horizon, tiga
   bacaan berurutan, dan perubahan hanya dilaporkan kalau keadaan sebelumnya
   **mapan**. Turun ke 14/54, lalu 7/21.
4. **`SELISIH_PENDAPAT_TAJAM` menyala 45/54.** Bukan kebasian — bacaannya satu
   menit. Aku meminjam `HIGH_DISAGREEMENT` dengan alasan yang benar untuk
   pertanyaan yang salah: ia ambang council untuk memutuskan kapan ronde
   adversarial digelar, bukan untuk "selisihnya luar biasa". Terukur atas 2.527
   sesi dalam 24 jam: median **0,29**, dan ambang 0,40 menyaring **37%**.
   Diganti `AMBANG_SELISIH_TAJAM = HIGH_DISAGREEMENT * 2` — digandakan, bukan
   angka lepas, supaya tetap satu sumber. Menyaring 11%.

**Terukur sesudah keempatnya:** `menyala` turun dari **20/20** menjadi **3–4 dari
20** aset per siklus, nol error. §16.2 kembali menjadi saringan alih-alih
stempel.

---

## Lanjutan — akurasi yang berarti, 2026-08-22

Tunggakan `internal-1` habis: **489/489 dinilai**, melewati ambang. Angka
akurasi pertama keluar — **22,9%** — dan angka itu **menyesatkan**.

**Kenapa.** Tiap simulasi menghasilkan beberapa skenario dan hanya satu keluarga
yang benar-benar terjadi, jadi "pangsa skenario yang BENAR" dibatasi dari atas
oleh `1/N`. `internal-1` menghasilkan 3,0 skenario per simulasi, sehingga batas
atas strukturalnya **33,3%**. Dua puluh tiga persen terlihat seperti mutu tanpa
menjadi mutu — keluarga kesalahan yang sama dengan "win rate 17%" yang ternyata
bug label.

**Dua ukuran yang berarti**, dihitung per **simulasi**, bukan per skenario:

| | `internal-1` (bobot tangan) | `internal-2` (kerumunan) |
|---|---|---|
| Simulasi | 163 | 36 |
| Skenario per simulasi | 3,0 | 4,2 |
| **Cakupan** — keluarga nyata ada di antara skenarionya | 112/163 = 68,7% | 31/36 = 86,1% |
| **Teratas** — yang berbobot tertinggi ternyata benar | **0/163 = 0,0%** | 11/36 = 30,6% |
| Tebakan acak sebagai pembanding | 33,3% | 23,8% |

**Nol dari seratus enam puluh tiga**, dan itu bukan artefak pengukuran —
diperiksa langsung. `internal-1` memberi bobot tertinggi ke **False Breakout di
seluruh 163 simulasi** (38 lawan 37), sementara pasar ternyata Bullish
Continuation di 112 di antaranya. Kalah satu poin, sistematis, setiap kali.

Penyebabnya satu aturan tangan yang ditulis dan dibela dengan komentar:

```python
if nama == "False Breakout" and Peristiwa.VOLUME_EKSTREM not in pemicu:
    bobot += _GESER   # "tembusan tanpa volume adalah bentuk paling khas tembusan palsu"
```

Volume ekstrem jarang menyala, jadi bonus itu praktis selalu diberikan. Mesin
kerumunan yang menggantikannya lebih baik di kedua sumbu — tapi **36 simulasi
jauh di bawah ambang**, dan angkanya ditahan.

**Funding dan open interest disimpan — dua pemicu terakhir yang bisa ditutup.**

Keduanya diambil `futures-loop` tiap siklus, dipakai untuk rencananya, lalu
dibuang. `futures_plans.funding_cost_pct` bukan gantinya: biaya turunan atas
horizon sebuah rencana, bukan rate-nya. Diperiksa langsung di skema — tidak ada
satu tabel pun yang memuatnya.

Ditambahkan migrasi 0039 `futures_metrics`, penulisnya di `futures-loop`,
pembacanya di `KonteksPemicuRepository`, dan aturan retensi tiga puluh hari.
**Deret, bukan potret**: anomali open interest adalah PERUBAHAN, dan satu baris
yang ditimpa terus tidak akan pernah bisa menjawab "naik berapa persen".

Yang paling mudah patah di sini bukan penyimpanannya melainkan **jembatan
antar-prosesnya**: penulisnya hidup di `futures-loop` dengan bentuk venue
(`BTCUSDT`), pembacanya di `aruna run` dengan bentuk kanonik (`BTC/USDT`).
Jembatan yang salah membuat pemicunya diam selamanya tanpa satu pun galat.
Diverifikasi di produksi: 20 baris ditulis, 20 simbol terbaca dalam bentuk
kanonik, seluruhnya membawa funding.

**Pemicu tersambung: 6 → 9 → 11 dari 13.** Yang tersisa mati dua, dan keduanya
memang tanpa sumber: likuidasi (Binance menarik endpoint REST-nya) dan konflik
lintas-pasar (butuh pembacaan dua pasar pada satu titik waktu).

**`ringkas_akurasi` juga tidak pernah punya pemanggil.** Penilaian berhenti di
basis data sementara §16.19 menutup dengan "Gunakan untuk evaluasi". Ditambahkan
`ringkas_per_simulasi` dan pelaporannya ke log operator, dengan pecahannya
selalu ditulis utuh supaya "11/36" tidak pernah terbaca sebagai "31%" oleh mata
yang buru-buru.
