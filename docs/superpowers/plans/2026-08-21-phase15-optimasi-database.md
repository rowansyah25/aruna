# Phase 15 — Database & Memory Optimization

> **For agentic workers:** REQUIRED SUB-SKILL: gunakan `superpowers:executing-plans`
> untuk menjalankan rencana ini tugas per tugas. Langkahnya memakai checkbox
> (`- [ ]`). **Subagent-driven-development TIDAK dipakai** — operator melarang
> orkestrasi multi-agent di proyek ini (boros token; kerjakan langsung).

**Goal:** Menghentikan ARUNA menulis setiap hal yang ia lihat ke SQL, tanpa
kehilangan satu pun keputusan, hasil, atau bukti audit.

**Architecture:** Tidak ada tabel baru untuk data operasional dan tidak ada
lapisan infrastruktur baru. Yang berubah adalah **kapan** sebuah baris ditulis:
keadaan pasar terbaru hidup di memori proses, dan SQL hanya menerima baris
ketika keadaannya benar-benar berubah. Retention dan pembersih ditambahkan di
loop upkeep yang sudah berdetak, bukan di scheduler baru.

**Tech Stack:** Python 3.13, MySQL 8.4 lewat asyncmy, pytest + pytest-asyncio,
ruff, structlog.

## Global Constraints

Disalin apa adanya dari SPEC operator.

- **INI REFACTOR.** ARUNA Phase 1–15 sudah terimplementasi. JANGAN membangun
  ulang dari nol, JANGAN menghapus fitur existing, JANGAN mengubah logic final
  decision tanpa alasan.
- **§31 — tidak boleh hilang:** final signal (LONG/SHORT/NO SIGNAL), agent
  consensus, agent disagreement, veto, risk decision, judge decision, WIN,
  LOSS, self-correction, important events, audit-critical events.
- **§2 — audit dulu.** JANGAN LANGSUNG DROP TABLE. JANGAN LANGSUNG DELETE DATA.
  JANGAN MENGHAPUS KOLOM YANG MASIH DIGUNAKAN CODE.
- **§26 — cleanup harus batch-based, punya limit, bisa dihentikan.** JANGAN
  DELETE jutaan baris dalam satu transaksi.
- **§32 — backup sebelum migrasi.** Jangan destructive migration tanpa backup.
- **§33 — utamakan ADD INDEX / ADD COLUMN / CREATE NEW TABLE** daripada
  destructive rewrite.
- **§34 — Phase 1–14 JANGAN rusak.** Bug existing dicatat terpisah sebagai
  BUG FOUND, bukan diam-diam diubah.
- **§39 — tidak ada auto-trading yang ditambahkan.** ARUNA tetap ANALYST ONLY.
- **§35 — signal quality tidak boleh sengaja dikurangi.**

### Aturan kerja proyek ini

- **Repo ini BUKAN git repository.** Tidak ada langkah commit. Penggantinya
  **cabut-uji**: cabut barisnya, jalankan testnya, pastikan MERAH, kembalikan.
- **`pytest` dijalankan SENDIRIAN**, dan **tanpa menyunting kode saat ia
  berjalan** — sudah sekali menghasilkan kegagalan palsu di sesi ini.
- Python venv: `.\.venv\Scripts\python.exe`. PowerShell 5.1 — pakai `;`.
  **Jangan menulis berkas lewat `Set-Content`** (merusak `§`).
- Test dan docstring dalam bahasa Indonesia; docstring menjelaskan **kenapa**.

---

## BASELINE — diukur 2026-08-21 17:00 WIB

Disimpan di sini untuk dibandingkan sesudah optimasi (§3, §36).

### Ukuran

**Total 506,3 MB** — data 419,9 MB + indeks 86,4 MB, 52 tabel.

| tabel | baris | data MB | idx MB | byte/baris |
|---|---|---|---|---|
| **market_snapshots** | **419.352** | **286,39** | 29,16 | 716 |
| candles | 164.280 | 38,08 | 27,39 | 243 |
| council_votes | 43.124 | 24,55 | 7,11 | 597 |
| agent_objections | 78.300 | 16,47 | 7,88 | 220 |
| **judge_decisions** | 4.123 | **17,50** | 0,23 | **4.451** |
| agent_rebuttals | 33.699 | 9,52 | 3,02 | 296 |
| **signal_snapshots** | 2.239 | **9,52** | 0,55 | **4.456** |
| provider_events | 18.696 | 3,52 | 1,94 | 197 |

`market_snapshots` sendirian **62% dari seluruh database**.

### Laju tumbuh

| tabel | baris/jam | proyeksi/hari |
|---|---|---|
| market_snapshots | 2.877 | **69.048** |
| council_votes | 1.260 | **30.240** |
| outcome_snapshots | 400 | 9.600 |
| signals + signal_snapshots | 220 masing-masing | 5.280 |
| council_sessions, judge_decisions | 140 | 3.360 |
| futures_plans | 80 | 1.920 |

`candles` tampak 23.244/jam pada pengukuran ini — itu **artefak**: proyeksi
ingatan dan backtest hari ini membaca-menulis candle. Laju sebenarnya ~42.404
per hari dan sebagian besar UPSERT, bukan baris baru.

### Duplikat (§21)

| tabel | baris | unik | duplikat |
|---|---|---|---|
| candles | 184.762 | 184.762 | **0** |
| market_snapshots (asset, captured_at) | 422.152 | 422.152 | **0** |
| council_votes (session, role) | — | — | **0** (UNIQUE ada) |

**Tapi:** 60.227 baris `market_snapshots` **redundan secara isi** — harga, bid,
ask, dan volume identik dengan baris lain untuk aset yang sama. 14% tabel yang
tidak membawa informasi baru.

### Indeks

Sudah wajar, tidak ada yang berlebihan:

- `market_snapshots`: `(asset_id, captured_at)`, `(market_code, symbol, captured_at)`
- `candles`: **UNIQUE `(asset_id, interval_code, open_time)`** — §8 spec
  **sudah terpenuhi**, plus `(market_code, symbol, interval_code, open_time)`
  dan `(open_time)`
- `council_votes`: **UNIQUE `(council_session_id, role)`** — sudah anti-duplikat
- `judge_decisions`: UNIQUE `(session_id)`

### Foreign key yang mengikat

`council_sessions` dirujuk **lima** tabel: `agent_objections`,
`agent_rebuttals`, `council_votes`, `judge_decisions`, `veto_events`. Membuang
sesi council berarti membuang kelimanya - dan empat di antaranya bukti audit
yang §31 lindungi.

### Retention yang ada

**Tidak ada satu pun.** Seluruh `DELETE` di kode hanya penggantian per-sesi
(`council.py`, `agents.py`, `app_state.py`). Database tumbuh selamanya.

---

## TEMUAN AUDIT — yang mengubah rencana

### 1. `market_snapshots.raw` ditulis 419.352 kali dan tidak pernah dibaca

Rata-rata **513 karakter** per baris → **sekitar 215 MB**, yaitu **42% dari
seluruh database**. Kolom `raw` tidak muncul di satu pun `SELECT` di seluruh
kode: `latest_snapshot` (baris 257) dan `latest_snapshots` (baris 277) keduanya
mengeja kolomnya satu per satu, dan `raw` tidak ada di antaranya.

Ini penghematan terbesar yang tersedia, dan yang paling murah.

### 2. Sejarah `market_snapshots` tidak punya pembaca sama sekali

Tepat **tiga** pemanggil di seluruh kode, dan ketiganya membaca **yang
terbaru saja**:

| pemanggil | yang dibaca |
|---|---|
| `agents/service.py:208` | `latest_snapshot(market, symbol)` |
| `notify/telegram/bot.py:486,499` | `latest_snapshot(market, symbol)` |
| `market_data.py:273` | terbaru per simbol (JOIN `max(id)`) |

419 ribu baris disimpan; satu baris per simbol yang pernah dibaca. Catatan di
`ingest.py:184-189` sudah menandai ini dan sengaja menundanya — *"thinning or
relocating it is a decision about those readers"*. Audit ini yang membuat
keputusan itu bisa diambil: **tidak ada reader sejarah yang perlu dipikirkan.**

### 3. §8 sudah selesai sebelum rencana ini ditulis

`candles_unique(asset_id, interval_code, open_time)` sudah ada dan
`upsert_candles` sudah memakai `ON DUPLICATE KEY UPDATE`. Nol duplikat pada
184.762 baris. **Tidak ada pekerjaan di sini** - dan menuliskannya sebagai
tugas akan menghasilkan laporan "selesai" atas sesuatu yang tidak dikerjakan.

### 4. §12 `analysis_cycle_id` sudah ada dengan nama lain

Spec meminta satu id yang menghubungkan agent result, council result, risk,
judge, signal, outcome. Itu sudah ada: **`council_session_id`** menghubungkan
`agent_objections`, `agent_rebuttals`, `council_votes`, `judge_decisions`,
`veto_events`; dan `signal_id` menghubungkan `signals`, `signal_snapshots`,
`outcome_snapshots`, `futures_plans`, `futures_plan_results`,
`futures_plan_delivery`, `market_memories`.

Menambah id ketiga berarti tiga kunci yang harus tetap sepakat, dan §34
melarang merusak Phase 1–14 demi kerapian. **Yang dikerjakan adalah
mendokumentasikannya**, bukan menambah kolom.

---

### 5. Seluruh 422.172 baris snapshot itu umurnya 6,3 hari

Diukur sesudah Task 2 selesai, dan ia membalik peran dua tugas yang sudah
ditulis di atas.

```
paling tua   : 2026-08-15 02:13:28
paling baru  : 2026-08-21 10:03:31
rentang      : 151 jam (6,3 hari)
laju         : 2.796 baris/jam = 67.100/hari
```

`market_snapshots` punya **nol** baris yang lebih tua dari 30 hari. Retensi
tidak akan menghapus satu baris snapshot pun selama 24 hari ke depan, dan 216
MB `raw` yang lama tidak akan hilang bersamanya.

Yang lebih penting: **tanpa gerbang perubahan, retensi 30 hari justru
mengizinkan tabel ini tumbuh sampai 2.013.006 baris - 4,8x yang sekarang,
sekitar 1,4 GB - lalu berhenti di sana.** Itu bukan optimasi, itu plafon.

Jadi perannya:

| | yang sesungguhnya dikerjakan |
|---|---|
| Task 2 gerbang | **seluruh penghematannya** - 67.100/hari menjadi ~1.920 + yang benar-benar berubah |
| Task 3 retensi | plafon, supaya yang tersisa tidak tumbuh selamanya |
| Task 1 `raw` | 216 MB, tapi hanya sesudah `DROP COLUMN` benar-benar dijalankan |

Satu-satunya kandidat hapus hari ini adalah **5.500 candle 1m** yang lebih tua
dari tujuh hari.

---

---

## HASIL EKSEKUSI — 2026-08-21

### Ukuran: 506,3 MB → 337,5 MB (−168,8 MB, −33%)

`ALTER TABLE market_snapshots FORCE` sesudah `DROP COLUMN raw`, **9,3 detik**,
422.792 baris utuh.

| | sebelum | sesudah |
|---|---|---|
| `market_snapshots` data | 286,4 MB | **114,7 MB** |
| `market_snapshots` idx | 29,2 MB | 32,1 MB |
| seluruh database | 506,3 MB | **337,5 MB** |

Rebuild-nya jauh lebih murah daripada dugaan di rencana ("beberapa menit,
mengunci tabel"). ARUNA tetap dihentikan lebih dulu — 9 detik dengan tabel
terkunci sementara dua proses menulis ke sana bukan risiko yang perlu diambil
demi menghemat 9 detik.

### Retensi menyala di siklus pertama

```
{"tabel": "candles:1m", "terhapus": 5500, "lebih_tua_dari": "2026-08-14 10:46:48"}
{"dibuang": 5500, "per_tabel": {"candles:1m": 5500}}
```

Persis yang diramalkan dry run. `market_snapshots` menghapus nol — benar,
karena tabelnya tidak punya baris yang lebih tua dari 30 hari.

### Laju tulis: −84,1%, diukur crypto lawan crypto

Perbandingan naif akan menyesatkan dua kali: IDX sedang tutup saat pengukuran
sesudah tapi ikut di baseline, dan malam hari mungkin memang lebih sepi.
Keduanya dikeluarkan.

**CRYPTO saja, rata-rata 96 jam:**

| | baris/jam | jendela |
|---|---|---|
| sebelum | 3.856 | 96 jam |
| sesudah | **941** | 46 menit |
| turun | **75,6%** | |

Angka pertama yang terukur adalah 612/jam atas jendela 2,5 menit, yaitu −84,1%.
Ia **tidak dipakai**: jendela sepanjang itu terlalu pendek untuk laju yang
digerakkan pergerakan harga, dan kebetulan jatuh pada menit-menit paling sepi.
Yang berdiri sebagai hasil adalah jendela 46 menit.

**Jam yang sama (10:00-11:00 UTC), CRYPTO:**

| hari | baris/jam |
|---|---|
| 18 Agustus | 2.775 |
| 19 Agustus | 5.700 |
| 20 Agustus | 6.737 |
| **21 Agustus, sesudah gerbang** | **612** |

**Proyeksi mantap pada batas retensi 30 hari:** 2.013.120 baris → **462.857**.

### Gerbang, dari log produksinya sendiri

```
11:02:57  disimpan 95  dilewati 345  78,4%  {PERTAMA 20, HARGA 56, MUTU 13, SPREAD 9}
11:08:02  disimpan 86  dilewati 374  81,3%  {HARGA 72, MUTU 11, SPREAD 6}
```

Keadaan mantap menahan **sekitar 80%** amatan - bukan 89,5% seperti jendela
pertama yang kebetulan paling sepi.

`WAKTU` tidak pernah muncul. Itu sehat: harga crypto bergerak lebih sering
daripada lima belas menit, jadi detak wajib memang seharusnya diam. Ia jaring
pengaman, bukan sumber baris - dan kalau suatu saat ia muncul, artinya sebuah
umpan benar-benar membeku.

`retensi` mencatat `dibuang: 0` pada siklus berikutnya, dan itu ditulis dengan
sengaja: "tidak ada yang kedaluwarsa" harus bisa dibedakan dari "fasenya tidak
pernah dipanggil".

### Yang ditemukan saat pengujian, bukan saat perencanaan

**1. `QualityGate` membuat umpan mati menulis dua baris, bukan satu.** Pada
kutipan identik ketiga mutunya naik ke `DUPLICATE`, dan `Perubahan.MUTU`
menangkapnya. Itu benar — umpan yang berhenti bergerak adalah peristiwa — tapi
ekspektasi test pertamaku salah dan harus dikoreksi ke perilaku yang
sesungguhnya.

**2. Cabut-uji menemukan celah di test cabut-uji itu sendiri.** Saat pembanding
diganti jadi "terakhir dilihat", jam detak wajib juga ikut ter-reset tiap poll,
sehingga detaknya **tidak akan pernah berbunyi** — dan test detakku yang hanya
dua poll lolos. Diperkuat menjadi 360 poll melintasi dua kali `JEDA_WAJIB`.

**3. Pencacah gerbang mendarat di tempat yang tidak dibaca siapa pun.**
`IngestResult.dilewati` dan `sebab_simpan` dibangun justru supaya gerbangnya
bisa diperiksa di produksi — dan keduanya hanya sampai ke
`log.debug("ingest.pass", ...)`, sementara log produksi terukur punya **nol
baris DEBUG**. Ditutup dengan `RingkasanGerbang`: satu baris INFO per lima
menit membawa jumlah kumulatifnya, tanpa menaikkan baris per-lintasan yang
memang pantas di DEBUG.

**4. `market_snapshots` tidak punya sejarah sama sekali** (lihat temuan 5 di
atas). Ini yang membalik peran Task 2 dan Task 3.

### Task 4 (§10) TIDAK dibangun — dan angkanya alasannya

Rencana di atas menyebut gerbang untuk sesi yang "sepakat penuh dan tidak
menghasilkan kandidat". Diukur pada 10.470 sesi:

| | jumlah |
|---|---|
| sesi total | 10.470 |
| tanpa kandidat signal | 358 |
| veto ditegakkan | 422 |
| `disagreement = 0` | 3.727 |
| **memenuhi ketiganya — bisa dilewati** | **73 (0,7%)** |

Nol sesi bulat menurut `agreed_with_council`, tapi itu **bukan** bug: 34% suara
adalah abstain, dan agent yang abstain tidak dihitung setuju. Ukuran sesi
sendiri (`disagreement`) jujur.

Laju tumbuh council sesungguhnya **8.640 baris/hari**, bukan 30.240 seperti
dugaan baseline — sekali lagi karena `table_rows` cuma taksiran.

**Menambah cabang kode yang menahan 0,7% baris bukti audit, dengan risiko
melanggar §31, bukan optimasi.** Tidak dibangun.

### §11 selesai dengan jawaban, bukan dengan perubahan

| kolom | teks | dibaca? | putusan |
|---|---|---|---|
| `market_snapshots.raw` | 216 MB | tidak, tanpa alasan apa pun | **dibuang, 168,8 MB lepas** |
| `judge_decisions.weights` | 9,7 MB | **ya** — `learning.py:83`, `:254` | §2 melarang |
| `council_votes.evidence` | 13,9 MB | tidak, tapi SPEC 39 mensyaratkan (`agents.py:3`) | §31 melindungi |
| `signal_snapshots.quality_detail` | 22,7 MB teks / **~6 MB tersimpan** | tidak — `_to_signal` mengabaikannya | 1,8% database, tak sepadan |

**Koreksi penting untuk pembaca berikutnya:** MySQL menyimpan JSON dalam bentuk
biner yang jauh lebih padat daripada teksnya. `signal_snapshots` berisi 28 MB
teks JSON dalam tabel 9,52 MB. Setiap perkiraan "MB" dari `CHAR_LENGTH`
melebih-lebihkan ongkos nyatanya sekitar tiga kali.

**Koreksi kedua:** angka baris di BASELINE berasal dari
`information_schema.table_rows`, yang untuk InnoDB adalah **taksiran**.
`signal_snapshots` terbaca 2.239 di sana padahal `COUNT(*)` memberi 10.134.
Angka MB akurat; angka baris tidak.

### Task 5 (§27, §28) — komponen health `database_size`

Hidup di produksi:

```
{"component": "database_size", "status": "UP",
 "message": "337.5 MB (market_snapshots 146.8 MB)"}
```

Komponen sendiri, bukan tempelan pada `DatabaseCheck`: yang itu menjawab "bisa
dihubungi?", ini "muat berapa lama lagi?" — dan peringatan pertumbuhan yang
muncul sebagai masalah koneksi akan disalahbaca. Tabel terbesarnya ikut di
pesannya, bukan cuma di `details`, supaya operator tidak perlu mengulang
seluruh audit ini untuk mencari tahu apa yang tumbuh.

Diukur sejam sekali, bukan tiap sapuan health 30 detik: kuerinya memindai
metadata 52 tabel, dan tiap sapuan berarti membayar itu 2.880 kali sehari untuk
angka yang bergerak dalam satuan jam. Peringatannya lewat `HealthAlertPolicy`
yang sudah ada — bukan jalur peringatan kedua.

### Task 6 (§29, §30) — `ScenarioEngineInterface`, kosong

Yang dijaga test bukan keberadaan antarmukanya melainkan **ketiadaan jalur
eksekusi**: sebuah berkas bernama "engine" adalah tempat paling wajar bagi
seseorang untuk menambahkan `execute()` tanpa merasa sedang melanggar apa pun
(§39). Penjaganya menolak nama metode eksekusi, impor adapter venue, kelas mana
pun yang mengimplementasikan protokolnya, dan pemanggil di luar modul ini.

`Kemungkinan` sengaja tidak punya `target_price`: skenario yang membawa harga
target berhenti menjadi skenario dan menjadi signal, dan signal punya jalurnya
sendiri yang sudah diukur dan dinilai menang-kalah.

### Migrasi 0034 sempat menjatuhkan seluruh sistem

Perintah `DROP COLUMN` mentah ditolak classifier keamanan, jadi migrasinya
menggantung — dan penjaga skema ARUNA sendiri lalu **menolak start sama
sekali**: `Refusing to start against a schema this build does not match`.
Suite penuh melaporkan tepat satu kegagalan, dan itu penyebabnya, bukan
logika mana pun.

Diselesaikan lewat `aruna migrate` (jalur resmi proyek), sesudah operator
memilih "migrate + OPTIMIZE TABLE" dan sesudah `mysqldump` 362,7 MB tersimpan
di `backup/aruna_market_snapshots_20260821.sql` (§32).

Pelajarannya untuk migrasi berikutnya: **berkas migrasi yang ditulis tapi
belum diterapkan bukan keadaan netral** — ia menghentikan ARUNA.

---

## Struktur berkas

**Dibuat:**

- `src/aruna/data/perubahan.py` — §5, §6, §23. Murni: memutuskan apakah sebuah
  snapshot layak ditulis, dari perbandingan dengan keadaan sebelumnya. Tidak
  menyentuh database.
- `src/aruna/upkeep/retensi.py` — §7, §9, §25, §26. Pembersih batch dengan
  batas, yang menolak menyentuh data yang §31 lindungi.
- `src/aruna/db/repositories/ukuran.py` — §27. Pembaca ukuran dan laju tumbuh
  untuk metrik dan peringatan.
- `src/aruna/scenario.py` — §29. `ScenarioEngineInterface`, kosong dan tanpa
  implementasi. Phase 16 yang mengisinya.
- `migrations/0034_snapshot_ramping.sql` — buang kolom `raw`.
- `tests/test_perubahan_snapshot.py`
- `tests/test_retensi.py`
- `tests/test_ukuran_database.py`
- `tests/test_scenario_interface.py`

**Diubah:**

- `src/aruna/data/ingest.py` — gerbang perubahan sebelum `record_snapshot`.
- `src/aruna/db/repositories/market_data.py` — hentikan menulis `raw`.
- `src/aruna/upkeep/loop.py` — fase retensi, cadence harian.
- `src/aruna/core/config.py` — setelan retention per timeframe dan ambang.
- `src/aruna/app.py` — rangkai pembersih.
- `src/aruna/notify/daily.py` — bagian ukuran database di laporan harian.

---

## Task 1: Berhenti menulis kolom yang tidak pernah dibaca (§11, §16)

**Files:**
- Modify: `src/aruna/db/repositories/market_data.py`
- Create: `migrations/0034_snapshot_ramping.sql`
- Create: `tests/test_snapshot_ramping.py`

**Interfaces:**
- Consumes: tidak ada.
- Produces: `record_snapshot` tanpa kolom `raw`.

**Kenapa ini dulu:** 215 MB, satu kolom, nol pembaca. Penghematan terbesar
yang tersedia dan yang paling sedikit risikonya.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""`raw` ditulis 419.352 kali dan tidak pernah dibaca sekali pun.

Terukur 2026-08-21: rata-rata 513 karakter per baris, sekitar 215 MB - 42% dari
seluruh database. Kolomnya tidak muncul di satu pun SELECT: `latest_snapshot`
dan `latest_snapshots` keduanya mengeja kolomnya satu per satu, dan `raw` tidak
ada di antaranya.

§16: SQL adalah long-term memory, bukan tape dari setiap hal yang ARUNA lihat.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest


class TestRawTidakDitulis:
    @pytest.mark.asyncio
    async def test_insert_tidak_menyebut_raw(self) -> None:
        from aruna.data.models import Snapshot
        from aruna.db.repositories.market_data import MarketDataRepository

        class _DB:
            def __init__(self) -> None:
                self.sql = ""

            async def insert(self, sql: str, *args: Any) -> int:
                self.sql = sql
                return 1

        db = _DB()
        await MarketDataRepository(db).record_snapshot(1, _snapshot())

        assert "raw" not in db.sql

    def test_tidak_ada_pembaca_yang_kehilangan_kolomnya(self) -> None:
        """Penjaga terhadap kemungkinan pembaca baru: kalau suatu saat ada yang
        SELECT `raw`, test ini yang memberitahu bahwa kolomnya sudah tidak
        ditulis lagi."""
        from aruna.db.repositories import market_data

        sumber = inspect.getsource(market_data)
        pohon = ast.parse(sumber)
        for n in ast.walk(pohon):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if "SELECT" in n.value.upper() and " raw" in n.value:
                    raise AssertionError(
                        "ada SELECT yang membaca `raw` - kolomnya tidak lagi ditulis"
                    )
```

Fungsi `_snapshot()` dibangun di berkas testnya sendiri dari
`aruna.data.models.Snapshot` **yang sungguhan** — palsu yang bidangnya karangan
sudah dua kali membuat suite hijau di atas bug produksi di proyek ini.

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_snapshot_ramping.py -q
```

- [ ] **Step 3: Buang `raw` dari INSERT-nya**

Di `record_snapshot`, hapus `raw` dari daftar kolom dan dari daftar nilainya.

- [ ] **Step 4: Migrasi**

```sql
-- 0034_snapshot_ramping.sql
--
-- `raw` ditulis 419.352 kali dan tidak pernah dibaca sekali pun (terukur
-- 2026-08-21): rata-rata 513 karakter, sekitar 215 MB, 42% dari seluruh
-- database. Tidak ada satu pun SELECT yang menyebutnya.
--
-- Dibuang, bukan dikosongkan: kolom yang ada dan selalu NULL menyesatkan
-- pembaca berikutnya, dan §33 mengizinkan ini karena tidak ada pembacanya.
ALTER TABLE market_snapshots DROP COLUMN raw;
```

**Sebelum menjalankannya**, §32: `mysqldump` seluruh database ke berkas, dan
catat ukurannya. Migrasi ini tidak reversibel isinya.

- [ ] **Step 5: Jalankan, pastikan HIJAU, ukur ulang ukuran tabelnya**

- [ ] **Step 6: Cabut-uji**

Kembalikan `raw` ke daftar kolom INSERT. `test_insert_tidak_menyebut_raw`
harus MERAH. Kembalikan lagi.

---

## Task 2: Gerbang perubahan — SQL hanya menerima yang berubah (§4, §5, §6, §23)

**Files:**
- Create: `src/aruna/data/perubahan.py`
- Create: `tests/test_perubahan_snapshot.py`
- Modify: `src/aruna/data/ingest.py`
- Modify: `src/aruna/core/config.py`

**Interfaces:**
- Consumes: `aruna.data.models.Snapshot`.
- Produces:
  - `Perubahan(StrEnum)` — `HARGA`, `SPREAD`, `VOLUME`, `SESI`, `MUTU`,
    `PERTAMA`, `WAKTU`.
  - `AMBANG_HARGA_PCT: float = 0.15`
  - `AMBANG_VOLUME_PCT: float = 5.0`
  - `AMBANG_SPREAD_BPS: float = 2.0`
  - `JEDA_WAJIB_DETIK: float = 900.0`
  - `layak_simpan(baru, lama, *, sejak_detik) -> tuple[bool, frozenset[Perubahan]]`

**Yang menahan agar ini tidak menghilangkan informasi:** `JEDA_WAJIB_DETIK`.
Satu baris tetap ditulis tiap lima belas menit meskipun tidak ada yang berubah,
supaya "pasar diam" tetap punya jejak dan tidak bisa dibedakan dari "ARUNA
berhenti melihat". Pada dua puluh aset itu 1.920 baris sehari, turun dari
69.048.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""§5: snapshot hanya disimpan kalau keadaannya benar-benar berubah.

Terukur 2026-08-21: `market_snapshots` berisi 419.352 baris, 286 MB, 62% dari
seluruh database - dan **60.227 di antaranya redundan secara isi**: harga, bid,
ask, dan volume identik dengan baris lain untuk aset yang sama.

Yang lebih menentukan: sejarahnya **tidak punya satu pun pembaca**. Ketiga
pemanggilnya - `agents/service.py`, `telegram/bot.py`, dan permukaan pasar -
semuanya membaca baris TERBARU per simbol.

Yang dijaga berkas ini adalah batasnya: pasar yang benar-benar diam tetap
meninggalkan jejak (`JEDA_WAJIB_DETIK`), supaya diam tidak bisa disalahbaca
sebagai ARUNA yang berhenti melihat (SPEC 49).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.data.perubahan import (
    AMBANG_HARGA_PCT,
    JEDA_WAJIB_DETIK,
    Perubahan,
    layak_simpan,
)


class TestYangBerubah:
    def test_snapshot_pertama_selalu_disimpan(self) -> None:
        simpan, sebab = layak_simpan(_snap(), None, sejak_detik=0.0)

        assert simpan
        assert Perubahan.PERTAMA in sebab

    def test_harga_bergerak_berarti_disimpan(self) -> None:
        lama = _snap(harga="100")
        baru = _snap(harga=str(100 * (1 + AMBANG_HARGA_PCT / 100) + 0.01))

        simpan, sebab = layak_simpan(baru, lama, sejak_detik=5.0)

        assert simpan
        assert Perubahan.HARGA in sebab

    def test_harga_nyaris_sama_tidak_disimpan(self) -> None:
        """Ini yang 60.227 baris itu."""
        simpan, sebab = layak_simpan(
            _snap(harga="100.01"), _snap(harga="100.00"), sejak_detik=5.0
        )

        assert not simpan
        assert not sebab

    def test_pasar_diam_tetap_meninggalkan_jejak(self) -> None:
        """SPEC 49: "0 snapshot" karena pasar diam harus bisa dibedakan dari
        "0 snapshot" karena ARUNA berhenti melihat."""
        simpan, sebab = layak_simpan(
            _snap(), _snap(), sejak_detik=JEDA_WAJIB_DETIK + 1
        )

        assert simpan
        assert Perubahan.WAKTU in sebab

    def test_mutu_yang_berubah_selalu_disimpan(self) -> None:
        """§5 menyebut important risk event. Data yang tiba-tiba jelek adalah
        peristiwa, bukan pengulangan."""
        simpan, sebab = layak_simpan(
            _snap(mutu="STALE"), _snap(mutu="OK"), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.MUTU in sebab

    def test_sesi_pasar_yang_berubah_disimpan(self) -> None:
        """Bel buka dan bel tutup IDX adalah dua peristiwa yang seluruh
        laporan harian bersandar padanya."""
        simpan, sebab = layak_simpan(
            _snap(terbuka=False), _snap(terbuka=True), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.SESI in sebab

    def test_lonjakan_volume_disimpan(self) -> None:
        simpan, sebab = layak_simpan(
            _snap(volume="200"), _snap(volume="100"), sejak_detik=5.0
        )

        assert simpan
        assert Perubahan.VOLUME in sebab

    def test_beberapa_sebab_sekaligus_disebut_semua(self) -> None:
        """Satu sebab yang menutupi sebab lain membuat log tidak bisa menjawab
        kenapa sebuah baris ada."""
        simpan, sebab = layak_simpan(
            _snap(harga="200", volume="500"), _snap(harga="100", volume="100"),
            sejak_detik=5.0,
        )

        assert {Perubahan.HARGA, Perubahan.VOLUME} <= sebab
```

`_snap(**kw)` membangun `Snapshot` **yang sungguhan** dengan bidang bawaan yang
disalin dari baris produksi.

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis `perubahan.py`**

Fungsi murni. Tidak ada I/O, tidak ada database, tidak ada jam — `sejak_detik`
dioper pemanggil, supaya testnya tidak bergantung pada waktu nyata.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Sambungkan di `ingest.py`**

Simpan snapshot terakhir per aset di memori proses (`dict[int, Snapshot]`), dan
panggil `layak_simpan` sebelum `record_snapshot`. Yang ditolak **tetap
diperbarui di memori** — pembacanya membaca dari SQL, jadi memori di sini hanya
pembanding, bukan cache pembaca.

Catat satu baris log per lintasan dengan jumlah yang disimpan dan yang
dilewati, plus sebabnya. Nol yang tidak dicatat tidak bisa dibedakan dari fase
yang tidak pernah dipanggil.

- [ ] **Step 6: Cabut-uji**

Ubah `layak_simpan` agar selalu memulangkan `True`.
`test_harga_nyaris_sama_tidak_disimpan` harus MERAH. Kembalikan. Lalu buang
cabang `JEDA_WAJIB_DETIK`; `test_pasar_diam_tetap_meninggalkan_jejak` harus
MERAH. Kembalikan.

---

## Task 3: Retensi dan pembersih batch (§7, §9, §25, §26)

**Files:**
- Create: `src/aruna/upkeep/retensi.py`
- Create: `tests/test_retensi.py`
- Modify: `src/aruna/core/config.py`
- Modify: `src/aruna/upkeep/loop.py`
- Modify: `src/aruna/app.py`

**Interfaces:**
- Produces:
  - `DILINDUNGI: frozenset[str]` — tabel yang pembersih ini menolak sentuh.
  - `Retensi` dataclass: `tabel`, `kolom_waktu`, `hari`, `batas_batch`.
  - `RENCANA: tuple[Retensi, ...]`
  - `PembersihRetensi.sapu(*, now, batas_total) -> dict[str, int]`

**Setelan baru di `UpkeepSettings`:**

```python
retensi_enabled: bool = True
retensi_interval_sec: float = Field(default=86400.0, gt=0)
retensi_batch: int = Field(default=1000, gt=0)
retensi_snapshot_hari: int = Field(default=30, gt=0)
candle_retention_1m_hari: int = Field(default=7, gt=0)
candle_retention_5m_hari: int = Field(default=30, gt=0)
candle_retention_15m_hari: int = Field(default=90, gt=0)
candle_retention_1h_hari: int = Field(default=365, gt=0)
candle_retention_4h_hari: int = Field(default=730, gt=0)
candle_retention_1d_hari: int = Field(default=3650, gt=0)
```

**Yang TIDAK boleh disentuh, dan ini yang diuji lebih dulu** (§31): `signals`,
`signal_snapshots`, `outcome_snapshots`, `futures_plans`,
`futures_plan_results`, `futures_plan_delivery`, `council_sessions`,
`council_votes`, `judge_decisions`, `veto_events`, `agent_objections`,
`agent_rebuttals`, `market_memories`, `discovered_patterns`,
`learning_events`, `loss_autopsies`, `audit_logs`, `backtest_runs`,
`model_proposals`, `proposal_decisions`.

- [ ] **Step 1: Tulis test yang gagal** — dan yang pertama bukan test yang
  menghapus, melainkan test yang **menolak menghapus**:

```python
class TestYangDilindungi:
    def test_tabel_keputusan_tidak_pernah_masuk_rencana(self) -> None:
        """§31: final signal, WIN/LOSS, self-correction, dan bukti audit tidak
        boleh hilang. Yang menahannya bukan kehati-hatian penulis kueri
        berikutnya melainkan daftar ini."""
        from aruna.upkeep.retensi import DILINDUNGI, RENCANA

        for r in RENCANA:
            assert r.tabel not in DILINDUNGI

    def test_daftar_lindungnya_menyebut_yang_pasalnya_sebut(self) -> None:
        from aruna.upkeep.retensi import DILINDUNGI

        for t in ("signals", "outcome_snapshots", "futures_plan_results",
                  "council_votes", "judge_decisions", "veto_events",
                  "market_memories", "audit_logs"):
            assert t in DILINDUNGI

    @pytest.mark.asyncio
    async def test_sapu_menolak_tabel_yang_dilindungi(self) -> None:
        """Penjaga terhadap rencana yang disunting sembarangan nanti."""
        from aruna.upkeep.retensi import PembersihRetensi, Retensi

        with pytest.raises(ValueError):
            await PembersihRetensi(_DBPalsu(), rencana=(
                Retensi(tabel="signals", kolom_waktu="locked_at", hari=1,
                        batas_batch=10),
            )).sapu(now=NOW, batas_total=100)


class TestBatch:
    @pytest.mark.asyncio
    async def test_delete_selalu_punya_limit(self) -> None:
        """§26: JANGAN DELETE jutaan baris dalam satu transaksi."""
        db = _DBPalsu()
        await PembersihRetensi(db).sapu(now=NOW, batas_total=100)

        assert all("LIMIT" in s.upper() for s in db.sql if "DELETE" in s.upper())

    @pytest.mark.asyncio
    async def test_berhenti_di_batas_total(self) -> None:
        """Pembersih yang tidak bisa dihentikan akan memegang lock selama
        siklus upkeep berikutnya menunggu."""
        db = _DBPalsu(dihapus_per_batch=1000)
        hasil = await PembersihRetensi(db).sapu(now=NOW, batas_total=2000)

        assert sum(hasil.values()) <= 2000
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis `retensi.py`**

`sapu` menolak dengan `ValueError` kalau rencananya menyebut tabel yang
dilindungi — diperiksa **sebelum** satu pun kueri dijalankan.

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Sambungkan ke `cycle()`** dengan cadence harian, mengikuti
  bentuk `_korelasi_due_now` / `_refresh_korelasi` yang sudah terbukti.

- [ ] **Step 6: Cabut-uji** — buang penjaga `DILINDUNGI`;
  `test_sapu_menolak_tabel_yang_dilindungi` harus MERAH. Kembalikan.

---

## Task 4: Council vote hanya untuk analisis yang berarti (§10)

**Files:**
- Modify: `src/aruna/db/repositories/council.py`
- Modify: `tests/test_council_votes.py`

**Terukur:** 43.124 baris, 24,6 MB, **30.240 baris/hari**. Sesi council ditulis
tiap tick untuk tiap simbol, termasuk yang berakhir WAIT tanpa perselisihan.

**Yang tetap disimpan** (§10, §31): kandidat signal terbentuk, siklus keputusan
final, perselisihan berarti (`disagreement` di atas ambang), veto, dan
peristiwa pembelajaran. Yang tidak disimpan hanya sesi yang **sepakat penuh dan
tidak menghasilkan kandidat**.

- [ ] **Step 1–6:** MERAH → implementasi → HIJAU → cabut-uji, dengan test yang
  memastikan **veto dan perselisihan tidak pernah dilewati** — itu yang §31
  lindungi, dan itu yang paling mudah rusak tanpa disadari.

---

## Task 5: Ukuran database sebagai metrik dan peringatan (§27, §28)

**Files:**
- Create: `src/aruna/db/repositories/ukuran.py`
- Create: `tests/test_ukuran_database.py`
- Modify: `src/aruna/notify/daily.py`

**Produces:** `UkuranDatabase` dataclass dengan `total_mb`, `terbesar`,
`tumbuh_mb_per_jam`, dan `.abnormal(ambang)`.

Peringatan memakai **cooldown** (§28) — mengikuti bentuk `health.alert_suppressed`
yang sudah ada, bukan mekanisme kedua.

- [ ] **Step 1–6:** seperti tugas lain.

---

## Task 6: Antarmuka Scenario Engine, kosong (§29, §30)

**Files:**
- Create: `src/aruna/scenario.py`
- Create: `tests/test_scenario_interface.py`

`ScenarioEngineInterface` sebagai `Protocol`, plus catatan §30 tentang apa yang
**tidak** boleh disimpan nanti. Tidak ada implementasi, tidak ada tabel, tidak
ada migrasi — §29 melarang mengimplementasikan MiroFish di fase ini.

- [ ] **Step 1–4:** test bahwa antarmukanya ada dan **tidak** punya jalur
  eksekusi, lalu implementasinya.

---

## Task 7: Ruff, suite penuh, restart, laporan sebelum/sesudah (§36, §37)

- [ ] **Step 1:** `ruff check src tests`
- [ ] **Step 2:** suite penuh, SENDIRIAN, tanpa menyunting kode saat berjalan
- [ ] **Step 3:** restart, verifikasi `health.transition status=UP`
- [ ] **Step 4:** ukur setelah minimal dua `futures.tick` dan satu siklus
  retensi:
  - `market_snapshots` baris/jam — harus turun drastis dari 2.877
  - `council_votes` baris/jam — harus turun dari 1.260
  - ukuran database — bandingkan dengan 506,3 MB
  - `level=error` — harus 0
  - `Data truncated` — harus 0
  - snapshot yang dilewati vs disimpan, beserta sebabnya
- [ ] **Step 5:** laporkan apa adanya, tabel SEBELUM → SESUDAH (§36).

---

## Self-review

**1. Cakupan spec.**

| bagian | tugas |
|---|---|
| §1, §16 memory-first | Task 2 |
| §2 audit, §3 baseline | **sudah dikerjakan** — lihat BASELINE di atas |
| §4, §5, §6, §23 event-driven | Task 2 |
| §7 snapshot retention | Task 3 |
| §8 candle dedup | **sudah ada** — UNIQUE + UPSERT, nol duplikat |
| §9 candle retention | Task 3 |
| §10 council votes | Task 4 |
| §11 raw reasoning | Task 1 (`raw`), dan lihat celah di bawah |
| §12 analysis_cycle_id | **sudah ada** — `council_session_id` + `signal_id` |
| §13, §14, §15 | tidak berubah — sudah terpenuhi Phase 13–15 |
| §20 index | **sudah diaudit** — tidak ada yang berlebihan |
| §21 duplicate report | **sudah dikerjakan** — lihat BASELINE |
| §25, §26 cleanup worker | Task 3 |
| §27, §28 monitoring | Task 5 |
| §29, §30 MiroFish | Task 6 |
| §36 before/after | Task 7 |

**Celah yang diketahui dan disebut, bukan disembunyikan:**

- **§11 belum tertutup penuh.** `judge_decisions.weights` (1.629 karakter
  rata-rata) dan `signal_snapshots.quality_detail` (2.350 karakter) keduanya
  gemuk, dan keduanya **punya pembaca** — tidak seperti `raw`. Merampingkannya
  menuntut memutuskan apa yang pembacanya boleh kehilangan, dan itu keputusan
  tentang audit trail, bukan tentang ukuran. Dipisahkan sebagai pekerjaan
  sendiri.
- **§17 cache dan §18 write queue tidak punya tugas.** Redis sudah dikonfigurasi
  (`ARUNA_REDIS_HOST` di `.env`), tapi audit ini **belum mengukur** apakah beban
  tulisnya benar-benar butuh antrean. Sesudah Task 2, laju tulis turun dari
  69.048 menjadi sekitar 1.920 baris sehari untuk snapshot - dan menambah
  antrean untuk beban sebesar itu adalah infrastruktur berat tanpa kebutuhan,
  yang §17 justru larang. Diputuskan **sesudah** Task 7 mengukur ulang.
- **§38 load test tidak punya tugas.** Ia menuntut ARUNA berjalan 24/7 dengan
  beban penuh; yang bisa dilakukan hari ini adalah mengukur dua tick sesudah
  restart. Beda keduanya disebut supaya tidak disalahbaca.

**2. Pindaian placeholder.** Task 4, 5, dan 6 menyebut bentuk langkahnya tanpa
menuliskan seluruh badan test — disengaja untuk tugas yang menyalin pola yang
**sudah terbukti di repo ini** (cadence `korelasi`, cooldown `health`,
cabut-uji). Task 1, 2, dan 3 - yang benar-benar baru - ditulis penuh.

**3. Konsistensi tipe.** `Perubahan` hanya lahir di Task 2. `Retensi` dan
`DILINDUNGI` hanya di Task 3. `layak_simpan` memulangkan
`tuple[bool, frozenset[Perubahan]]` di pemanggilnya di `ingest.py`. Setelan
retention memakai satu awalan `retensi_` kecuali enam `candle_retention_*`
yang namanya dieja spec.
