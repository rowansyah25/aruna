# PHASE 15 — INTELLIGENCE GATE: STATUS **NOT READY**

Diaudit 2026-08-21 terhadap spec INTELLIGENCE MATURITY & DECISION ENGINE
(35 bagian, 31 gate). Bagian 34 melarang menyatakan COMPLETE karena kodenya
jalan; yang berikut adalah pemeriksaan tiap gate terhadap **bukti produksi**,
bukan terhadap keberadaan kode.

**Empat gate GAGAL, dua tidak bisa diverifikasi, dan seluruh bagian 31
(required testing) belum dikerjakan.**

---

## DIPERBAIKI SESUDAH AUDIT INI

### ✅ Gate 8 — confidence calibration (2026-08-21)

`Kalibrator` memetakan keyakinan yang **dinyatakan** ke keyakinan yang
**terbukti**. Mesin pengukurnya sudah benar sejak lama; yang tidak pernah ada
adalah petanya.

Terukur di produksi sesudah restart: rata-rata **mentah 0,683 → dinyatakan
0,506** pada 42 keputusan berarah. Bekerja dua arah - pita bawah justru
dinaikkan (35% → 53%), karena di sana ARUNA terukur *under*confident.

**Dua cacat lain terbongkar saat memasangnya**, dan hanya karena angka
produksi tidak bisa direproduksi:

* `measured_history()` dan `review()` menyaring populasi **berbeda** - yang
  satu memakai `published`, yang lain tidak. Laporan yang dilaporkan ke
  operator dan laporan yang menggerakkan keputusan mengukur hal berbeda, dan
  yang salah justru yang menggerakkan keputusan. Disatukan ke
  `_klaim_terkalibrasi`.
* `limit=500` menyisakan 67 baris sesudah penyaringan - tiga dari empat pita
  kekurangan sampel. Dinaikkan ke `SAMPEL_KALIBRASI = 5000` (777 baris,
  keempat pita terukur).

Akibatnya terlihat di verdict: dari `OVERCONFIDENT in 35-50%, 50-65%, 65-80%,
80-96%` menjadi `OVERCONFIDENT in 65-80%, 80-96%`. ARUNA menghukum keyakinan
rendah dan menengahnya sendiri berdasarkan laporan yang salah hitung.

`confidence_raw` disimpan terpisah (migrasi 0035): kalibrasi yang mengukur
keluarannya sendiri akan melaporkan semuanya baik-baik saja pada putaran kedua.

### ✅ Gate 25 & 27 — tidak ada WAIT (2026-08-21)

`decision/finalizer.py`. Dipasang di batas antara analisis internal dan
keputusan tersimpan, sesudah kedua gerbang (veto SPEC 19, no-trade SPEC 33).

**Yang tidak berubah: apa yang operator lihat.** `PUBLIC_DECISION` sudah
memetakan `WAIT → "NO SIGNAL"` sejak lama, jadi kata itu tidak pernah sampai ke
Telegram. Peringatan di versi audit ini - "ARUNA jadi 60% diam" - **salah**, dan
dikoreksi di sini.

Yang tetap utuh: `WAIT` sah sebagai suara agent (bagian 25 mengizinkan
uncertainty internal), judge tetap boleh menyimpulkannya, dan
`agreed_with_council` tidak tersentuh karena ia membandingkan kosakata publik.

Sebabnya pindah ke `SebabDiam`: `DIBLOKIR_VETO`, `DIBLOKIR_NO_TRADE`,
`TIDAK_ADA_SETUP`, `INPUT_TAK_TERPERCAYA` - persis beda yang `veto.py` jaga
(*"input tidak bisa dipercaya"* vs *"tidak ada setup sekarang"*).

**Cabut-uji pertama tidak menggigit**, dan itu temuan tentang testnya sendiri:
penjaga AST hanya memeriksa `finalkan` dipanggil, bukan hasilnya dipakai.
Diganti dengan test yang menjalankan `Council.convene` sungguhan atas pasar
datar. Cabut-uji kedua merah di tiga test - dan sekaligus membongkar impor
melingkar `council.session → aruna.decision → aruna.signals → council.session`.

### ✅ Gate 1 — taksonomi regime berarah (2026-08-21)

`TRENDING_BULLISH`, `TRENDING_BEARISH`, `BREAKDOWN` masuk. Classifier memakai
arah yang **sudah ada di tangannya** - `regime.py` memilih `TRENDING` untuk
`UPTREND` maupun `DOWNTREND` di dua cabang bersebelahan, lalu membuang bedanya.

Terbukti di produksi: `TRENDING_BULLISH` 27, `BREAKDOWN` 4, `BREAKOUT` 8,
**`TRENDING` tanpa arah nol**.

**Koreksi terhadap audit ini:** `HIGH_VOLATILITY` **sudah ada** di enum. Audit
bilang "tidak ada" karena nol baris memakainya - sebabnya berbeda dan lebih
menarik: ia dipilih dengan bobot 2,0 dan 1,5, sementara `TRENDING` mendapat
2,5 + 1,5 = 4,0 dan selalu menang argmax. Terdefinisi, bisa dihitung, dan
secara struktural tak pernah bisa menang. **Belum diperbaiki.**

**Kompatibilitas 9.897 ingatan lama.** `Regime.keluarga` memetakan halus ke
kasar, dan `dimensions.sama()` mencocokkan bentuk kasar dengan turunannya -
tapi **tidak** mencocokkan naik dengan turun, karena kalau begitu tidak ada
yang bertambah dari pemisahan ini.

Terukur di dua tick berurutan: `memory_kasus` rata 301,4 → 224,0 dengan
maksimum 546 → 533. Kecocokan lintas generasi **bekerja** - kalau gagal,
angkanya runtuh ke nol, bukan turun 26%. Turunnya sendiri tidak bisa
diatribusikan: sebagian memang seharusnya turun (ingatan bearish berhenti
mencemari kondisi bullish), tapi dua tick n=20 berjarak sepuluh menit juga
beda kondisi pasarnya.

**Batas jujur yang tidak bisa dihilangkan:** `TRENDING` aman karena ia nama
kasar yang classifier tak pakai lagi - baris lama bisa dikenali sebagai lama.
`BREAKOUT` tidak: bagian 2 memasangkannya dengan `BREAKDOWN`, jadi ia kini
berarti "ke atas", sementara **2.261 baris lama** memakainya untuk kedua arah.
Yang sebenarnya breakdown akan terbaca breakout naik, dan arahnya tidak
tersimpan di mana pun.

**Cabut-uji membongkar kelemahan testnya sendiri.** Mengembalikan cabang
STRUKTUR ke `Regime.TRENDING` tidak membuat satu pun test berbasis deret harga
merah - keduanya ternyata digerakkan suara MOMENTUM. Cabang struktur ditutup
dengan test yang memanggil `classify_regime` langsung.

### ✅ `HIGH_VOLATILITY` yang tak pernah menang (2026-08-21)

Dugaan pertama - ia kalah argmax - **salah**. `LOW_VOLATILITY` punya bobot
maksimum yang sama (3,5) dan menang 448 kali; bobot setara tapi hanya satu yang
pernah menang menunjuk ke ambang.

Terukur atas 7.700 pengamatan per interval:

| interval | ATR% maks | jumlah ≥ 3,0% |
|---|---|---|
| 15m | 2,154 | **0** |
| 1h | 3,024 | 1 |
| 4h | 6,435 | 504 |
| 1d | 21,079 | **6.896 (89,6%)** |

`HIGH_VOL_ATR_PCT = 3.0` satu angka mutlak untuk semua timeframe, sementara
ATR% berskala dengan timeframe. Di 15m mustahil; di 1d hampir selalu benar.
**Ambang volatilitas yang tidak berskala dengan timeframe bukan pendeteksi
volatilitas - ia pendeteksi timeframe.**

Sisi sebaliknya sama rusaknya: `LOW_VOL_ATR_PCT = 0.5` sementara median 15m
adalah 0,445 - lebih dari separuh bar 15m otomatis "tenang". 448 baris
`LOW_VOLATILITY` itu bukan deteksi, melainkan tanda bahwa grafiknya 15m.

Diganti rasio terhadap median true range deret itu sendiri
(`atr_relatif`), dengan `HIGH_VOL_RASIO = 1.5` dan `LOW_VOL_RASIO = 0.7` yang
**bebas skala**. Ambang per-timeframe yang dipaskan ke sebaran hari ini adalah
overfitting terhadap enam hari pasar naik pada dua puluh aset kripto, yang
bagian 32 larang.

Terbukti di produksi dari 80 keputusan:

| | sebelum | sesudah |
|---|---|---|
| `HIGH_VOLATILITY` | 0% (nol dari 10.494) | **2,5%** |
| `LOW_VOLATILITY` | 4,3% | **1,25%** |

### ✅ Bagian 31 — 25 skenario wajib (2026-08-21)

`tests/test_skenario_phase15.py`. Setiap skenario memanggil kode produksi yang
sungguhan - `classify_regime`, `Council.convene`, `QualityGate`,
`analyse_funding`, `analyse_open_interest`, `buffer_score`,
`build_reliability`, `detect`, `calibrate` - dan menegaskan **perilaku**, bukan
keberadaan fungsi.

| | skenario |
|---|---|
| 1-8 | bentuk pasar: bullish/bearish terpisah, ranging, breakout, false-breakout→REVERSAL, reversal, volatilitas tinggi/rendah |
| 9-10 | agent sepakat → berarah; berselisih → tidak dipaksa berarah |
| 11-12 | data basi ditolak; umpan yang berhenti bergerak ditandai |
| 13-16 | news shock, funding ekstrem, lonjakan OI, likuidasi terlalu rapat |
| 17-18 | yakin+bukti lemah diturunkan; ragu+bukti kuat dinaikkan |
| 19-21 | WIN, LOSS, kalah berulang menurunkan bobot agent |
| 22-25 | agent membaik/memburuk, drift terdeteksi, sampel kurang tidak menghasilkan angka |

Plus satu penjaga kelengkapan yang menuntut nomor 1-25 utuh, supaya skenario
tidak hilang diam-diam saat ia mulai merepotkan.

**Beberapa skenario lulus dengan cara ARUNA menolak berpendapat** (10 dan 25) -
itu perilaku yang benar, bukan test yang lemah.

Cabut-uji dua kali, keduanya merah: melonggarkan false-breakout menghasilkan
`RANGING`, dan membuang penjaga sampel tipis membuat kalibrator meledak saat
mencoba mengarang angka dari 19 pengamatan.

### ✅ Gate 24 — self-correction dapat di-rollback (2026-08-21)

Bentuknya berbeda dari dugaan audit, dan datanya sendiri yang menceritakan.

**Sisi proposal sudah benar sejak lama, dan tidak disentuh.**
`governance/approval.py` menolak menyetujui proposal yang validasinya tidak
mendukung, dan membalikkan perubahan aktif wajib menjadi proposal baru supaya
tercatat, bukan diam-diam dibatalkan.

**Sisi proposal juga tidak punya apa pun untuk dibalikkan.** Terukur:
`exit-at-target` berstatus APPROVED padahal validasinya sendiri berkata
`verdict: NO_IMPROVEMENT`, `supports_approval: false`, PnL bersih lebih buruk
463.540 - dan ada proposal ke-7 berjudul *"Revert exit-at-target: it was
approved on fabricated numbers"* yang masih DRAFT sejak 2026-08-15.

Jejaknya berhenti di situ: `exit_at_target` **hanya hidup di mesin backtest**
(`cli.py` sendiri menulis *"neither is the live rule"*). Proposal yang
disetujui itu tidak pernah mengubah perilaku hidup, `parameters: []` kosong,
dan tidak ada mekanisme yang menerapkan proposal ke parameter.

**Yang sesungguhnya berubah otomatis adalah kalibrasi**, dan sejak 2026-08-21
angkanya menentukan keyakinan yang diterbitkan - risiko yang perubahan hari itu
sendiri perkenalkan. Sebelum ini ia menimpa dirinya tiap hari tanpa catatan apa
yang hilang dan tanpa jalan kembali.

`governance/rollback.py` menutup itu dengan kelima bidang bagian 23 - `lama`,
`baru`, `alasan`, `pemicu`, `pada` - plus `balikkan()` yang memulangkan nilai
sebelumnya **berikut jejak pembalikannya**: pembalikan yang tidak tercatat
membuat riwayatnya berbohong tentang apa yang pernah aktif. Riwayatnya berbatas
50, dan hanya perubahan yang **benar-benar berubah** yang dicatat.

**Yang sengaja tidak dibangun: pembekuan otomatis saat angkanya memburuk.**
Kalibrasi yang memburuk bisa berarti kalibratornya rusak, atau bisa berarti
pasarnya yang berubah - dan membekukannya pada tebakan pertama akan mengunci
kalibrasi basi di atas pasar yang sudah bergerak. Yang disediakan adalah
kemampuan membalikkan berikut jejaknya; pemicunya tetap keputusan yang
disengaja.

**BUG FOUND (bagian 34), belum diperbaiki:** `revert-exit-at-target` masih
DRAFT. Ia menyebut `exit-at-target` disetujui *"on fabricated numbers"*.
Karena tidak ada jalur yang menerapkan proposal ke parameter hidup, dampaknya
nol - tapi catatan governance-nya berbohong tentang apa yang aktif, dan itu
keputusan operator untuk membereskannya.

### ✅ Gate 13 — error classification (2026-08-21)

`learning/sebab.py`. `loss_autopsies` sudah menyimpan bukti yang kaya - regime,
keadaan berita, tingkat risiko, keyakinan, agent yang dibungkam, keberatan yang
tak terjawab, gerak merugikan terjauh. Yang tidak ada adalah **namanya**:
`FAILURE_HYPOTHESES` memetakan tiga `outcome_class` ke prosa yang menjawab
*apa yang terjadi*, sementara bagian 12 minta *kenapa*.

**Menjalankannya atas 1.433 autopsy nyata menemukan dua cacat yang test
sintetis tidak akan pernah tangkap.**

**1. `NEWS_SHOCK` mustahil menyala.** `news_state` tersimpan sebagai PROSA -
957 berbunyi `NO_RECENT_NEWS`, sisanya `"2 item(s): 1+ / 0- / 1 unreadable"`.
Mencocokkannya dengan kata `NEGATIVE` tidak pernah kena. Sesudah diperbaiki:
0% → **8,8%**.

**2. Pengukuran pertama memberi `BAD_TECHNICAL_SIGNAL` 73,7%** - keranjang
serba-guna yang memakai nama percaya diri, lebih buruk daripada `OTHER` karena
`OTHER` setidaknya jujur. Sebabnya ada di skrip ukurnya: JSON dari MySQL bisa
datang sebagai string, dan `tuple("[]")` tidak kosong. Sesudah dibetulkan:
**36,0%**.

| sebab | % dari 1.433 |
|---|---|
| BAD_TECHNICAL_SIGNAL | 36,0 |
| OTHER | 18,1 |
| AGENT_OVERCONFIDENCE | 13,2 |
| FALSE_BREAKOUT | 13,0 |
| NEWS_SHOCK | 8,8 |
| TIMING_ERROR | 5,9 |
| INSUFFICIENT_DATA | 2,9 |
| WRONG_REGIME | 2,0 |

**Yang tidak muncul, dan kenapa itu bukan cacat.** `LIQUIDITY_EVENT`,
`FUNDING_DISTORTION`, `OI_MISREAD` ada di kosakata karena bagian 12
menyebutnya, tapi autopsy spot tidak menyimpan spread, funding, maupun open
interest - menghasilkannya berarti mengarang. Sebuah test menyebut mereka
supaya penambah bukti itu nanti menemukan kategorinya sudah menunggu.

`RISK_MODEL_ERROR` diukur khusus karena ini bisa jadi pelajaran
`HIGH_VOLATILITY` yang terulang. **Bukan**: 6 baris memenuhi rasio ≥3x (maks
5,24), keenamnya punya sebab yang lebih khusus dan tertangkap lebih dulu.
Ambangnya tidak diturunkan untuk membuatnya muncul - itu overfitting terhadap
enam hari yang bagian 32 larang.

18,1% `OTHER` dibiarkan. Sistem yang mengklasifikasi setiap kekalahan dengan
yakin akan menghasilkan pola yang seluruhnya buatan sendiri.

---

## GATE YANG GAGAL SAAT AUDIT

### ❌ Gate 27 — "Tidak ada WAIT" · Gate 25 — "Final output hanya LONG / SHORT / NO SIGNAL"

Kegagalan terbesar, dan spec menyebutnya dua kali (bagian 8 dan 25).

**Bukti:**

| sumber | WAIT |
|---|---|
| `council_sessions.decision` | **3.871** dari 6.441 (60%) |
| `signal_snapshots.direction` | **5.981** dari 10.494 (57%) |

**Penyebab:** `WAIT` adalah nilai sah di `Decision` dan mengalir dari council
sampai ke `signal_snapshots`. Ia bukan kebocoran - ia dirancang begitu.

**Tindakan:** finalizer yang memetakan setiap hasil analisis internal menjadi
tepat satu dari `LONG` / `SHORT` / `NO SIGNAL`. `WAIT` boleh hidup sebagai
keadaan internal (bagian 25 mengizinkan "internal analysis boleh memiliki
uncertainty") tapi tidak boleh menjadi keputusan yang tersimpan atau terkirim.

**Peringatan yang harus dibaca sebelum mengerjakannya:** 60% keputusan sekarang
WAIT. Memetakannya menjadi NO SIGNAL secara mekanis akan menghasilkan sistem
yang 60% diam - benar menurut huruf spec, dan mungkin bukan yang dimaksud.
Sebagian WAIT mungkin seharusnya LONG/SHORT berkeyakinan rendah. Keputusan
pemetaan itu milik operator, bukan milikku.

### ❌ Gate 8 — "Confidence calibration bekerja"

Pengukurannya bekerja. **Yang diukurnya gagal.**

Bagian 9 mengeja syaratnya: *"Jika ARUNA mengatakan Confidence 80%, maka
secara historis keputusan dengan confidence sekitar 80% harus memiliki tingkat
keberhasilan yang mendekati angka tersebut."*

**Bukti - keyakinan berkorelasi TERBALIK dengan kebenaran:**

| arah | pita keyakinan | n | menang |
|---|---|---|---|
| BUY | ragu (<50%) | 654 | **55,2%** |
| BUY | sangat yakin (≥90%) | 903 | **47,7%** |
| SELL | ragu | 230 | **23,0%** |
| SELL | sangat yakin | 103 | **3,9%** |

Makin yakin ARUNA, makin sering ia salah - di kedua arah. Verdict sistem
sendiri: `OVERCONFIDENT in 80-96%`.

**Tindakan:** terapkan kalibrasi terukur ke angka keyakinan yang **diterbitkan**.
Saat ini kalibrasi diukur, disimpan, diserahkan ke council - dan angka yang
keluar tetap mentah. Ini mengubah logika keputusan, jadi butuh persetujuan
eksplisit.

### ❌ Gate 1 — "Market regime detection bekerja" (SEBAGIAN)

Regime engine bekerja dan dipakai. **Taksonominya tidak lengkap.**

| diminta bagian 2 | ada? |
|---|---|
| TRENDING_BULLISH | ❌ tidak ada |
| TRENDING_BEARISH | ❌ tidak ada |
| HIGH_VOLATILITY | ❌ tidak ada |
| BREAKDOWN | ❌ tidak ada |
| RANGING, LOW_VOLATILITY, BREAKOUT, REVERSAL, UNCERTAIN | ✅ ada |

Yang ada: `TRENDING` (2.987) tanpa arah, dan `ANOMALY` (166) yang tidak
diminta.

**Kenapa ini berarti, bukan sekadar penamaan:** `TRENDING` tanpa arah membuat
bobot agent per-regime tidak bisa membedakan tren naik dari tren turun -
padahal bagian 2 justru menuntut bobot berbeda per regime. Dan datanya
menunjukkan bedanya besar: di TRENDING, BUY menang 49,8% sementara SELL menang
13,8%.

### ❌ Bagian 31 — Required testing (25 skenario)

**Nol dari 25 skenario ada sebagai test.** Spec menuntut minimal: strong
bullish, strong bearish, ranging, breakout, false breakout, reversal, high/low
volatility, agent agreement/disagreement, bad data, missing data, news shock,
funding anomaly, OI anomaly, liquidation spike, high-confidence-weak-evidence,
low-confidence-strong-evidence, WIN, LOSS, repeated LOSS, agent
improvement/degradation, drift, insufficient sample.

Suite yang ada (3.900+ test, exit 0) menguji unit dan integrasi, bukan
skenario pasar bernama ini.

---

## GATE YANG TIDAK BISA KUVERIFIKASI

### ⚠️ Gate 24 — "Self-correction dapat di-rollback"

`model_proposals` (3 baris) dan `proposal_decisions` (1 baris) ada, dengan
kolom `decision`, `decided_by`, `note`, `validation`. Tapi bagian 23 menuntut
`old_value` / `new_value` / `reason` / `trigger` / `timestamp` **dan** jalur
rollback yang benar-benar bisa dijalankan. Aku tidak memverifikasi jalur itu.

### ⚠️ Gate 13 — "Error classification bekerja" (SEBAGIAN)

`loss_autopsies` (223 baris) punya `outcome_class` + `hypothesis`, tapi
taksonominya bukan yang bagian 12 minta:

| ada | diminta bagian 12 |
|---|---|
| TARGET_NOT_REACHED, WRONG_FROM_START, RIGHT_THEN_REVERSED, RIGHT_DIRECTION_BAD_TIMING, NO_POSITION | WRONG_REGIME, BAD_TECHNICAL_SIGNAL, NEWS_SHOCK, FALSE_BREAKOUT, LIQUIDITY_EVENT, FUNDING_DISTORTION, OI_MISREAD, AGENT_OVERCONFIDENCE, INSUFFICIENT_DATA, RISK_MODEL_ERROR, TIMING_ERROR |

Yang ada menjawab *"apa yang terjadi"*; yang diminta menjawab *"kenapa"*.
Hanya `TIMING_ERROR` yang punya padanan (`RIGHT_DIRECTION_BAD_TIMING`).

---

## GATE YANG LULUS

| gate | bukti |
|---|---|
| 2 Agent performance memory | `agent_reliability` 14 baris, breakdown per dimensi |
| 3 Agent weighting | SPEC 30 multiplier + minimum sample |
| 4 Agent specialization | `discovered_patterns` 383; "MOMENTUM lebih kuat di RANGING" |
| 5 Conflict analysis | `agent_objections` 96.322, `agent_rebuttals`, `veto_events` 175 |
| 6 Evidence quality | `signal_quality` rata 73,3 + `quality_detail` berfaktor |
| 7 NO SIGNAL | 315 tersimpan, keputusan resmi |
| 9 Overconfidence detection | verdict `OVERCONFIDENT in 80-96%` |
| 11 LOSS analysis | `loss_autopsies` 223 |
| 14 Decision quality score | `signal_quality`, terpisah dari confidence |
| 15-19 Futures risk, leverage/SL/TP sebagai saran, risk/reward | jalur futures utuh, nol eksekusi |
| 20 Historical validation | `backtest_runs`, walk-forward CONSISTENT |
| 21 Drift detection | "Performance drift: TIDAK TERDETEKSI" |
| 22 Data quality | `QualityGate`, `provider_events` |
| 23 Traceability | seluruh bidang bagian 22 ada di `signal_snapshots` |
| 26, 28 Tanpa auto-trading, tanpa eksekusi | nol jalur order di seluruh kode |
| 29-31 Phase 1-14, Phase 15, tanpa regresi | suite penuh exit 0 |

---

## YANG HARUS DIBACA BERSAMA GATE INI

Tiga angka yang tidak muncul di checklist tapi menentukan artinya.

**1. Akurasi ARUNA di bawah garis dasar paling bodoh.**

| | |
|---|---|
| pasar naik di sampel | 56,6% |
| "selalu BUY", tanpa berpikir | 56,6% |
| ARUNA | **44,2%** |

Bagian 35 menaruh ACCURACY di urutan pertama. Seluruh gate bisa LULUS dan
angka ini tetap membuat Phase 15 belum matang.

**2. Sampelnya enam hari, seluruhnya pasar naik.**

Rentang `market_snapshots`: 2026-08-15 sampai 2026-08-21. SELL menang 1,9% di
horizon 1d - itu bisa berarti SELL rusak, atau berarti SELL belum pernah diuji
di pasar turun. Dengan data yang ada, keduanya tidak bisa dibedakan, dan
menebak di antaranya adalah cara membuat sistem yang yakin dan salah.

**3. Bagian 28 vs urutan yang sudah terjadi.**

Bagian 28 melarang refactor database besar sekarang karena itu Phase 15.1.
Refactor itu **sudah dikerjakan hari ini** atas perintah operator sebelum spec
ini diberikan: database 506 MB → 337 MB, tulis snapshot turun 75,6%, retensi
aktif. Bukan pelanggaran - urutannya saja terbalik dari yang spec bayangkan.

---

## STATUS

**PHASE 15 — NOT READY.**

**Seluruh gate yang gagal saat audit sudah diperbaiki** - Gate 1, 8, 13, 24,
25, 27, cacat volatilitas, dan bagian 31. Setiap perbaikan terverifikasi di
produksi atau di atas data produksi, dengan cabut-uji merah.

### Verifikasi produksi (2026-08-21, sesudah restart 17:50 UTC)

**Gate 24** - pencatat perubahan parameter menyala:

```
kalibrasi: (belum pernah diukur) -> OVERCONFIDENT in 65-80%, 80-96%
(diukur ulang dari 777 klaim terbitan, brier 0.3172; dipicu upkeep.review harian)
```

Kelima bidang bagian 23 ada, dan tersimpan di `app_state`. **777 klaim** persis
yang diramalkan dari perbaikan `SAMPEL_KALIBRASI = 5000`; brier 0,3172 lebih
baik daripada 0,3998 dari sampel tipis sebelumnya.

**Gate 13** - `loss_autopsies.sebab` terisi:

| sebab | n |
|---|---|
| BAD_TECHNICAL_SIGNAL | 404 |
| OTHER | 289 |
| NULL (baris sebelum kolomnya ada) | 222 |
| FALSE_BREAKOUT | 173 |
| AGENT_OVERCONFIDENCE | 163 |
| NEWS_SHOCK | 105 |
| TIMING_ERROR | 82 |
| INSUFFICIENT_DATA | 29 |
| WRONG_REGIME | 14 |

**Gate 1, 25, 27** tetap benar di jendela yang sama: regime `TRENDING_BULLISH`
23 / `BREAKOUT` 7 / `RANGING` 6 / `UNCERTAIN` 5 / `REVERSAL` 3 - nol `TRENDING`
tanpa arah; keputusan `BUY` 24 / `NO_SIGNAL` 27 / `SELL` 1 - nol `WAIT`.

**Nol error** sejak restart.

### Cacat yang dibuat lalu ditemukan saat verifikasi ini

`record_autopsy` menulis kolom eksplisit, dan `sebab` hanya ditambahkan ke
`to_dict()` - yang dipakai keluaran CLI. Klasifikasinya dihitung, terlihat di
layar, dan tidak pernah tersimpan: **keluarga cacat yang sama dengan enam yang
diperbaiki hari ini**, dibuat pada percobaan ketujuh.

Ditutup migrasi 0036 (`sebab VARCHAR(32)` berindeks) plus dua penjaga: satu
menuntut `sebab` ada di SQL **dan** di atribut yang dibaca, satu menghitung
placeholder terhadap kolom supaya penambahan kolom tidak menggeser seluruh
parameter satu posisi.

### ✅ AKURASI — penguncian menunggu candle bar itu (2026-08-21)

Bagian 35 menaruh ACCURACY di urutan pertama. Ini yang pertama menyentuhnya.

**Mekanismenya telanjang di log produksi:**

```
18:00:15.663  upkeep.locked      <- kunci menyala
18:00:32.832  upkeep.refreshed   CRYPTO:15m  <- bar 18:00 tiba 17 detik KEMUDIAN
```

Kunci dan refresh berada di siklus berbeda dan tidak sepakat kapan batas bar
lewat, jadi keputusan dibuat di atas bar yang tutup **satu bar sebelumnya**
padahal yang terbaru tersedia beberapa detik kemudian.

**Ongkosnya**, akurasi BUY dibanding garis dasar horizonnya:

| horizon | bukti bar terbaru | bukti satu bar lalu | yang basi |
|---|---|---|---|
| 15m | **+7,2** | −4,9 | 1.476 dari 2.070 |
| 1h | **+8,9** | −4,9 | 614 dari 1.414 |

**ARUNA punya edge; edge itu dihancurkan keterlambatan 17 detik.**

**Dua kali salah sebelum sampai ke akar.** Dugaan pertama argmax - sudah
terbukti salah pada `HIGH_VOLATILITY`. Kedua, melihat `as_of` di batas bar,
sempat dicurigai kebocoran. Diperiksa: 100% `as_of` jatuh tepat di batas bar
dan yang segar dikunci 19-45 detik SESUDAH batas itu. Bukan kebocoran.

`UpkeepLoop._bukti_siap` - penguncian menunggu sampai candle bar itu
benar-benar diambil. Nol kueri tambahan: refresher sudah tahu apa yang
diambilnya, dan hanya `result.refreshed` yang dicatat, bukan `deferred`.
Barnya sengaja tidak ditandai saat ditunda, pola yang sama dengan penanganan
kegagalan yang sudah ada.

**Penempatan diperbaiki oleh testnya sendiri.** Versi pertama memasang gerbang
di `_horizons_due` dan menjatuhkan 19 test. Test-test itu benar:
`_horizons_due` menjawab pertanyaan kalender - *"bar mana yang berganti, horizon
mana yang sah untuk pasar ini"*. Kesegaran pertanyaan lain, dan `_lock` sudah
punya polanya. Sesudah dipindah, `test_idx_active` dan
`test_upkeep_horizon_match` hijau **tanpa disentuh**.

**Terverifikasi di produksi:**

| | sebelum | sesudah |
|---|---|---|
| umur bukti 15m | 915 detik (satu bar basi) | **34 detik** |
| `as_of` | bar sebelum yang baru tutup | bar yang **baru saja tutup** |

**Konsekuensi yang dipilih sadar:** bar yang candle-nya tidak pernah tiba tidak
menghasilkan prediksi sama sekali - mengurangi sampel, dan lebih baik daripada
prediksi yang terukur berkinerja negatif. Pencacah `lock_menunggu_candle`
membedakan "nol karena candle terlambat" dari "nol karena pasar diam".

**Tidak satu parameter pun disetel.** Yang berubah urutannya, bukan angkanya.

**Yang BELUM bisa diklaim:** kenaikan akurasinya masih **proyeksi** dari
pemisahan historis (+7,2 / +8,9 melawan −4,9), bukan pengamatan. Membuktikannya
butuh berhari-hari prediksi terselesaikan pada kode ini, dan pada pasar yang
tidak hanya naik.

---

### ✅ BUG FOUND ditelusuri, dan bentuknya lebih serius (2026-08-21)

Audit mencatat `revert-exit-at-target` sebagai catatan yang menunggu keputusan
manusia. Menelusuri **kenapa** `exit-at-target` bisa APPROVED padahal
validasinya berkata `supports_approval: false` menemukan yang sebenarnya.

Penjaganya lengkap dan benar - `ready_for_approval` menolak yang sudah
diputuskan, yang tanpa validasi, dan yang buktinya tidak mendukung. Jadi
`approve()` tidak mungkin menghasilkan keadaan itu. Datanya yang bicara:

```
proposal_key: exit-at-target
      status: VALIDATED            <- tabel proposal
    decision: APPROVED             <- tabel keputusan
  decided_at: 2026-08-15 11:39:31  oleh rowan
  updated_at: 2026-08-17 15:29:51  <- validasi ulang, DUA HARI kemudian
```

`governance/service.py` menetapkan `status=VALIDATED` **tanpa syarat** saat
validasi ulang, dan `record_proposal` menimpanya lewat `ON DUPLICATE KEY
UPDATE`. **Validasi rutin membatalkan persetujuan manusia secara diam-diam.**

Tiga akibat, dan yang ketiga paling berbahaya:

* catatan governance berbohong tentang apa yang aktif;
* penjaga "already approved" **dikalahkan** - statusnya sudah bukan APPROVED;
* **perubahan yang sama bisa disetujui dua kali**, masing-masing tercatat
  sebagai keputusan manusia yang terpisah.

Kode ini sudah menyatakan sikapnya di `approval.reject()`: *"Reversing an
active change is a new proposal... rather than quietly undone."* Jalur validasi
melanggarnya.

**Perbaikan kode:** `STATUS_TERMINAL` dieja **sekali** di `proposal.py` dan
dipakai bersama `ready_for_approval` dan `validate_proposal` - dua daftar yang
harus tetap sepakat adalah dua yang suatu saat tidak. Validasinya tetap
disimpan; yang dipertahankan hanya statusnya, karena validasi ulang
`exit-at-target` justru yang menemukan `NO_IMPROVEMENT` dan PnL lebih buruk
463.540.

**Perbaikan data:** status diturunkan DARI tabel keputusan, bukan sebaliknya -
`proposal_decisions` adalah catatan keputusan manusia, `model_proposals.status`
keadaan turunan yang rusak. Kuerinya mencari **semua** baris yang tidak sepakat,
bukan menuliskan `exit-at-target` secara hardcode; ketemu satu. Backup di
`backup/model_proposals_sebelum_perbaikan_status.sql`.

Sesudahnya:

| proposal | status | |
|---|---|---|
| `exit-at-target` | APPROVED | tidak bisa disetujui lagi |
| `revert-exit-at-target` | DRAFT | masih bisa diputuskan |
| `stop-loss-only` | VALIDATED | masih bisa diputuskan |

---

## YANG TERSISA

`revert-exit-at-target` masih DRAFT - dan sekarang itu **bukan cacat melainkan
sistem yang bekerja**: membalikkan perubahan aktif memang harus menjadi
proposal baru yang manusia putuskan. Yang berubah, catatannya sekarang jujur.

**Dan yang tidak ada di checklist mana pun tapi menentukan artinya:** akurasi
arah **44,2%** melawan garis dasar "selalu BUY tanpa berpikir" **56,6%**, di
atas sampel enam hari yang seluruhnya pasar naik.

Delapan perbaikan hari ini membuat ARUNA jauh lebih jujur tentang dirinya -
keyakinan yang sesuai kenyataan, kosakata keputusan yang bersih, regime yang
berarah, volatilitas yang benar-benar terukur, klasifikasi kekalahan yang
menjawab *kenapa*, perubahan parameter yang punya jalan pulang, dan 25 skenario
yang menggigit. **Tidak satu pun membuat prediksinya lebih benar.**

Bagian 35 menaruh ACCURACY di urutan pertama. Kejujuran adalah prasyaratnya,
bukan penggantinya - sistem yang tidak tahu seberapa sering ia salah tidak bisa
diperbaiki. Tapi prasyarat yang sudah dipenuhi tetap bukan hasilnya.

Dan yang tidak ada di checklist tapi menentukan artinya: **akurasi 44,2%
melawan garis dasar "selalu BUY" 56,6%**, di atas sampel enam hari yang
seluruhnya pasar naik. Bagian 35 menaruh ACCURACY di urutan pertama.

Bagian 30 menuntut seluruh gate LULUS. **Phase 16 tidak boleh dimulai.**
