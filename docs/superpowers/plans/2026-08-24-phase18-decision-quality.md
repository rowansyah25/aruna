# Phase 18 — Decision Quality & Confidence Calibration

> **Untuk pelaksana:** jalankan tugas per tugas. **Subagent-driven TIDAK dipakai** —
> operator melarang orkestrasi multi-agent di proyek ini.

**Goal:** Menutup delapan celah nyata di lapisan kendali mutu keputusan — bukan
membangun lapisan baru, karena lapisannya sudah ada dan sudah menggerbangi.

**Tech Stack:** Python 3.13, MySQL 8.4 lewat asyncmy, pytest, ruff.

---

## TEMUAN PALING PENTING: sebagian besar Phase 18 SUDAH ADA

Diukur 2026-08-24 dengan membaca kode, bukan mengira-ngira. Spec Phase 18
menggambarkan lapisan yang seolah belum ada. Kenyataannya `signals/quality.py`
sudah menyusun **skor komposit 0–100 dari delapan belas faktor berbobot**, dan
`gate()`-nya **sudah dipanggil di jalur keputusan**:

```python
# signals/service.py
putusan = quality_gate(quality)
if lockable and not putusan.passed:
    lockable = False
    reason = "quality gate: " + "; ".join(putusan.reasons)
```

Itu §18.42 dan §18.43 apa adanya: gerbang yang menahan, dan yang menahan
menghasilkan NO SIGNAL — bukan membalik LONG jadi SHORT.

### Pemetaan pasal terhadap kode yang ada

| pasal | sudah ada di | status |
|---|---|---|
| 18.4 skor komposit 0–100 | `signals/quality.py` `QualityScore` (18 faktor) | **ada** |
| 18.5 evidence quality | `evidence_factor` | **ada** |
| 18.7 data quality | `data_quality_factor`, `freshness_factor` | **ada** |
| 18.8 agent reliability | `learning/reliability.py` | **ada** |
| 18.9 reliability per rezim | `learning/specialization.py` `RegimeSkill` | **ada** |
| 18.10 agent agreement | `agreement_factor` | **ada** |
| 18.16 risk quality | `risk/score.py`, `risk/gate.py` | **ada** |
| 18.18–18.21 kalibrasi | `learning/calibration.py`, `learning/kalibrator.py` | **ada, JALAN** |
| 18.20 Brier score | `learning/calibration.py` | **ada** |
| 18.29–18.30 NO SIGNAL / missed opportunity | `learning/counterfactual.py` `ghost_signal` | **ada** |
| 18.31–18.33 attribution | `learning/autopsy.py`, `learning/sebab.py` | **ada** |
| 18.34 outcome classification | `OutcomeClass` | **ada** |
| 18.38 minimum sample | `governance/proposal.py` `MIN_VALIDATION_SAMPLE` | **ada** |
| 18.39 overfitting | `backtest/walkforward.py` | **ada** |
| 18.40 data leakage | `DataLeakageError`, `backtest/replay.py` | **ada** |
| 18.42–18.44 quality gate | `signals/quality.py` `gate()` — **inline** | **ada** |
| 18.54 audit trail | jalur audit | **ada** |

Kalibrasinya bahkan sudah berbicara di produksi hari ini:

```
learning.history_applied calibration='OVERCONFIDENT in 50-65%, 65-80%, 80-96%:
stated confidence exceeds accuracy'
```

**Membangun ulang semua itu akan menghasilkan lapisan kedua yang harus
selamanya sepakat dengan yang pertama** — kesalahan yang sudah berulang di
proyek ini dan yang sepanjang Phase 17 justru dihindari berkali-kali.

---

## DELAPAN CELAH YANG NYATA

Yang benar-benar belum ada, diurutkan menurut nilainya:

### 1. Strategy dan Scenario tidak masuk skor mutu (§18.14, §18.15)

`score_signal` menyusun delapan belas faktor. **Tidak satu pun dari Phase 16
atau Phase 17.** Diverifikasi: `aruna.router` dan `aruna.scenario` hanya
diimpor oleh dirinya sendiri, repositorinya, dan fase upkeep-nya — tidak ada
satu berkas pun di `signals/`, `council/`, atau `agents/` yang menyentuhnya.

Ini **persimpangan yang hilang** antara dua jalur yang hari ini berjalan
sendiri-sendiri:

```
JALUR A  MARKET → AGENTS → DEBATE/PROTEST/VETO → judge → gerbang mutu
                → gerbang risiko → PHASE 14 → LONG/SHORT/NO SIGNAL

JALUR B  MARKET → SCAN → PHASE 16 → scenario_evidence   (tak dibaca Jalur A)
         MARKET → SCAN → PHASE 17 → router_pilihan      (tak dibaca Jalur A)
```

Menutup celah ini membuat diagram operator menjadi benar, dan menyambungkan
Phase 16 **dan** 17 sekaligus. Sambungannya sudah tersedia:
`DecisionContext.strategy` sudah ada, sudah berlabel "bukti, bukan perintah",
dan hari ini diisi `Strategist` Phase 12.

### 2. Evidence independence (§18.6)

Tidak ada yang mendeteksi bahwa RSI, MACD, dan momentum mengukur hal yang
berkorelasi. `evidence_factor` menghitung **jumlah** bukti (`target=20`), jadi
sepuluh indikator yang saling menyalin terbaca lebih kuat daripada tiga yang
benar-benar mandiri — persis yang §18.5 larang.

### 3. False confidence detection (§18.22)

Kombinasi "keyakinan tinggi + bukti lemah + risiko tinggi" tidak dideteksi
sebagai satu keadaan. Ketiganya terukur terpisah dan tidak ada yang menyilangkan.

### 4. Confidence ceiling & floor (§18.23, §18.24)

Tidak ada batas atas keyakinan yang mengikat pada mutu data atau keyakinan
rezim. Sinyal berkeyakinan 95% di atas rezim berkeyakinan 42% mungkin hari ini.

### 5. Protest & veto effectiveness (§18.12, §18.13, §18.49, §18.50)

`council/protest.py` mencatat objection dengan `severity` dan `ground`, tapi
**tidak ada yang mengukur apakah protes itu benar** sesudah hasilnya diketahui.
Sama untuk veto. Tanpa ini, protes yang selalu salah tetap berbobot sama dengan
yang selalu benar.

### 6. Decision stability & hysteresis (§18.25–§18.28)

`signals/repetition.py` punya cooldown dan deteksi duplikat, tapi tidak ada
yang mengukur **flip-flop**: LONG → NO SIGNAL → LONG → SHORT dalam empat menit
tanpa perubahan material.

### 7. Ambang mutu yang bernama (§18.41)

`MIN_QUALITY = 60` ada, tapi pita EXCELLENT/HIGH/GOOD/MODERATE/LOW/POOR tidak.
Laporan hari ini menyebut angka tanpa menyebut artinya.

### 8. Confidence separation di keluaran (§18.17, §18.45)

Tujuh keyakinan yang berbeda sudah dihitung terpisah di dalam sistem, tapi
keluarannya belum memisahkannya untuk pembaca.

---

## Global Constraints

- ARUNA **ANALYST ONLY** (§18.1). Dijaga AST, bukan janji di docstring.
- **Gerbang tidak boleh membalik arah** (§18.43). FAIL menghasilkan NO SIGNAL,
  tidak pernah LONG→SHORT.
- **Jangan bangun lapisan kedua.** Tiap celah ditutup dengan menambah faktor
  atau pengukur ke mesin yang sudah ada, bukan dengan mesin baru yang harus
  tetap sepakat.
- **Confidence ≠ certainty** (§18.2, §18.57).
- **Skor mutu historis tidak pernah ditulis ulang** (§18.35).
- **Tidak ada look-ahead** (§18.40).
- Basis data tetap ringan (§18.53): simpan keputusan, bukan tiap perhitungan.
- Ambang yang dipinjam harus dipinjam dari **pertanyaan yang sama**. Sudah
  empat kali jadi bug di proyek ini.

---

## Urutan yang disarankan

Celah 1 lebih dulu, dan sendirian, karena ia satu-satunya yang mengubah
**arsitektur** — sisanya menambah pengukuran di dalam bentuk yang sudah ada.
Setelah Phase 16 dan 17 benar-benar masuk ke skor mutu, celah 2–8 bisa
dikerjakan berurutan tanpa saling mengunci.
