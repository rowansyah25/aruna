# XAUUSD M5 — Rencana 5: Penjadwalan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ARUNA mengumpulkan keputusan XAU sungguhan tiap bar M5 — termasuk `NO SIGNAL` beserta sebabnya — supaya Rencana 3 punya bahan untuk dipelajari.

**Architecture:** Proses ketiga yang dijaga supervisor, sejajar `futures-loop` dan terpisah dari `aruna run`. Tidak menyentuh `enabled_markets`, tidak masuk pipa upkeep crypto, tidak menyentuh satu berkas pun di `src/aruna/futures/`.

**Tech Stack:** Python 3.13, tidak ada dependensi baru.

## Global Constraints

Sama dengan Rencana 1–2. Yang menentukan bentuk rencana ini:

- **JANGAN MERUSAK FUTURES.** Tak satu berkas di `src/aruna/futures/` disunting; `default_children` hanya ditambah satu entri.
- Modul XAU **terpisah**. `ARUNA_ENABLED_MARKETS` tetap `CRYPTO` — menambahkan `FOREX` di sana akan menyeret XAU ke loop upkeep crypto, yang justru kebalikan dari "modul terpisah".
- **Simpan seluruh hasil**, termasuk `NO SIGNAL` dan sebabnya.
- ARUNA tetap **ANALYST ONLY**.

---

## Keputusan Arsitektur: harga dari bar, bukan dari quote

`rakit_konteks` menuntut sebuah `Snapshot`, dan cara paling jelas mendapatkannya
adalah memanggil `/quote`. Rencana ini **tidak** melakukannya, karena dua alasan
yang keduanya menentukan:

**Kredit.** Satu tarikan M5 tiap bar sudah 288 kredit/hari dari jatah 800.
Menambah satu quote per bar menjadikannya 576 — masih muat, tapi menyisakan
terlalu sedikit untuk menarik histori atau menambah DXY di Rencana 4.

**Kebocoran arah sebaliknya.** Ini yang lebih penting. Sebuah quote diambil
*sesudah* bar terakhir tutup, jadi harganya lebih baru daripada seluruh bukti
yang mendasari keputusan. Keputusan akan berdiri di atas harga yang tidak
pernah dilihat indikator mana pun — bukan kebocoran masa depan dalam arti
biasa, tapi tetap ketidakcocokan antara harga keputusan dan bukti keputusan,
dan itu akan muncul di Rencana 3 sebagai selisih yang tak seorang pun bisa
jelaskan.

Harga diambil dari `close` bar M5 tersettle terbaru — bar yang sama yang
melahirkan `BuktiXau.as_of`. Konsekuensinya `bid`, `ask`, dan `spread_bps`
tetap `None`, yang memang sudah keadaannya: Twelve Data tidak menerbitkannya.

---

## Task 1: Loop XAU

**Files:**
- Create: `src/aruna/xau/loop.py`
- Test: `tests/test_xau_loop.py`

**Interfaces:**
- Consumes: `TwelveDataForexProvider`, `rakit_tumpukan`, `periksa_kelayakan`, `rakit_bukti`, `rakit_konteks`, `DeliberationEngine`, `putuskan_dari_dewan`, `XauRepository`, `Cooldown`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class HasilTick` — `sinyal: SinyalXau | None`, `alasan_lewat: str | None`, `bar: int`
  - `async def satu_tick(...) -> HasilTick`
  - `BAR_DIBUTUHKAN: int = 250`

Satu tick, berurutan, berhenti di penolakan pertama — dan tiap penolakan tetap
tersimpan:

1. Tarik M5 (1 kredit). Gagal → `HasilTick(alasan_lewat=...)`, tidak ada baris.
2. `rakit_tumpukan`
3. `periksa_kelayakan` → tidak layak: simpan `NO_SIGNAL` dengan alasannya, selesai.
4. `rakit_bukti` → `None`: simpan `NO_SIGNAL` "bukti tak terhitung", selesai.
5. `rakit_konteks` dari bar terakhir
6. `DeliberationEngine().deliberate`
7. `putuskan_dari_dewan` (cooldown ikut)
8. `XauRepository.simpan`

**Kenapa kegagalan tarik TIDAK disimpan sebagai NO SIGNAL.** Sebuah baris
`NO_SIGNAL` menyatakan ARUNA menilai dan memutuskan untuk diam. Venue yang
tidak menjawab bukan penilaian — menyimpannya sebagai keputusan akan mencemari
statistik "seberapa sering XAU diam" dengan menit-menit ketika ARUNA tidak
bertanya sama sekali. Itu dilaporkan lewat log dan `alasan_lewat`.

- [ ] **Step 1–6:** tulis test → merah → implementasi → hijau → cabut-uji harga
  bar (ganti ke quote dan pastikan test kecocokan harga merah) → commit.

---

## Task 2: Perintah `xau-loop` dan supervisor

**Files:**
- Modify: `src/aruna/cli.py` (satu subperintah), `src/aruna/supervisor.py` (satu `ChildSpec`)
- Test: `tests/test_xau_loop.py` (bagian supervisor)

Sejajar `futures-loop`:

```
ChildSpec(name="xau-loop", args=["-m", "aruna.cli", "xau-loop",
                                 "--interval", "300", "--hours", str(hours)])
```

**Penjaga yang wajib ada:** sebuah test yang menegaskan `default_children`
tetap memuat `aruna-run` dan `futures-loop`. Menambah proses ketiga adalah
persis momen ketika salah satu dari dua yang lama bisa hilang tanpa seorang pun
menyadarinya sampai futures diam sehari penuh.

---

## Yang TIDAK dikerjakan di rencana ini

- **Telegram XAU.** Telegram mati di pemasangan ini (`health` melaporkan
  `DISABLED`), jadi pemberitahuan tidak bisa diuji dengan jujur. Keputusan tetap
  tersimpan di basis data, yang memang tujuan rencana ini.
- **Sesi ASIA/LONDON/NEW YORK.** Itu Rencana 4. `Snapshot.session` dan
  `market_open` tetap `None` — belum diukur, bukan nol.
- **Menyalakan gerbang spread.** Tidak akan bisa dengan sumber ini.
