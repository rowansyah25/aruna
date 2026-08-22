# Kerumunan — mesin simulasi kerumunan milik ARUNA sendiri

> **Untuk pelaksana:** ikuti task satu per satu. Tiap task diakhiri cabut-uji —
> perbaikannya dicabut, testnya dipastikan MERAH, lalu dikembalikan. Repositori
> ini bukan git; cabut-uji adalah satu-satunya bukti bahwa testnya menguji
> sesuatu.

**Tujuan:** mengganti bobot skenario yang sekarang ditetapkan tangan dengan
bobot yang **muncul dari simulasi kerumunan pelaku pasar** — deterministik,
luring, nol dependensi baru, dan selesai dalam milidetik.

**Arsitektur:** kohort pelaku pasar (bukan agent individual) bereaksi terhadap
harga dan terhadap satu sama lain selama beberapa ronde. Tiap lintasan dijalankan
di bawah satu **premis** yang dieja — bukan satu angka acak — sehingga tiap
skenario bisa menyebut asumsi yang melahirkannya. Keluarga skenario ditentukan
dari bentuk lintasannya, dan bobotnya adalah pangsa lintasan yang mendarat di
keluarga itu.

**Tumpukan:** Python 3.13 murni. Tidak ada pustaka baru, tidak ada model, tidak
ada panggilan jaringan.

---

## Kenapa bukan MiroFish

Diperiksa langsung pada 2026-08-22 dengan mengunduh dan membaca sumbernya
(klonnya sudah dihapus lagi). Temuannya, bukan tebakan:

| Hal | MiroFish | Yang ARUNA butuhkan |
|---|---|---|
| Domain | jaringan sosial: `posts`, `comments`, `interview`, `agent-stats` (OASIS/camel-oasis) | mikrostruktur pasar |
| Determinisme | agent LLM — jawaban berbeda tiap jalan | §16.19 menuntut hasil bisa diulang |
| Waktu jalan | alur `create → prepare → generate-profiles → start → poll → report`, subprocess beronde-ronde | `TIMEOUT_DETIK` = 30 detik |
| Biaya | `LLM_API_KEY` berbayar + `ZEP_API_KEY`; README-nya sendiri memperingatkan "konsumsi besar, coba < 40 ronde dulu" | nol |
| Data | konteks pasar dikirim ke DashScope dan Zep Cloud | tetap di mesin |
| Berat | `torch 2.9.1`, `transformers`, `sentence-transformers`, 136 paket | nol dependensi baru |
| Lisensi | AGPL-3.0 | tidak mengikat ARUNA |

Yang menentukan bukan berat atau biayanya, melainkan dua baris pertama. Simulasi
yang butuh menit tidak bisa memberi bukti untuk horizon lima belas menit —
§16.13 sendiri akan membuang hasilnya sebagai basi hampir setiap kali. Dan mesin
yang jawabannya berbeda tiap jalan tidak bisa dievaluasi: skenario yang salah
minggu ini tidak bisa dibedakan dari skenario lain yang kebetulan muncul.

**Yang diambil dari MiroFish adalah idenya**, dan ide itu tidak dipatenkan siapa
pun: banyak pelaku sederhana yang berinteraksi menghasilkan perilaku yang tidak
dimiliki satu pun di antaranya. Tidak ada satu baris kodenya yang disalin.

---

## Yang berubah pada Phase 16 yang sudah ada

Hari ini `mesin.py` menetapkan bobot lewat `_GESER = 5.0` — pergeseran yang
kutulis dengan tangan dan kubela dengan komentar. Itu tebakan yang rapi, dan
`AMBANG_BESAR` serta `BOBOT_DASAR` sejenis dengannya.

Sesudah task ini, bobot **dihitung**: pangsa lintasan yang mendarat di tiap
keluarga. Yang hilang adalah satu konstanta karangan; yang didapat adalah angka
yang bisa dibantah dengan memeriksa lintasannya.

Tiga skenario wajib §16.5 tetap selalu muncul walau nol lintasan mendarat di
sana — dan bobot nol dengan "0/8 lintasan" tertulis di `bukti` lebih jujur
daripada bobot kecil yang dikarang supaya tidak terlihat kosong.

---

## Struktur berkas

- `src/aruna/scenario/kohort.py` — siapa saja yang ada di pasar, dan bagaimana
  masing-masing bereaksi. Data, bukan logika.
- `src/aruna/scenario/premis.py` — kisi asumsi yang divariasikan, dan gerbangnya
  terhadap pemicu.
- `src/aruna/scenario/kerumunan.py` — mesin lintasannya: ronde, aliran, harga,
  kedalaman, likuidasi berantai.
- `src/aruna/scenario/mesin.py` — **diubah**: bobot dari kerumunan.

---

## Task 1: Kohort pelaku pasar

**Files:** buat `src/aruna/scenario/kohort.py`, `tests/test_kohort.py`

**Produces:** `Kohort` dataclass, `KOHORT: tuple[Kohort, ...]`, `aliran(kohort, keadaan) -> float`

Enam kohort, masing-masing dengan alasan yang bisa dibantah:

| Kohort | Bereaksi terhadap | Tanda |
|---|---|---|
| `PENGIKUT_TREN` | gerak harga terakhir | searah |
| `PEMBALIK` | jarak dari harga awal | berlawanan |
| `PEMBUAT_PASAR` | ketidakseimbangan aliran | berlawanan, melemah saat kedalaman tipis |
| `BERUNGKIT` | gerak melawan posisinya | searah **setelah** ambang likuidasi |
| `PEMEGANG` | hampir tidak bereaksi | kecil |
| `PEMBURU_BERITA` | dorongan berita saja | searah dorongan |

- [ ] Pangsa menjumlah 1,0 — kohort yang pangsanya tidak menjumlah satu berarti
      sebagian aliran pasar tidak diwakili siapa pun
- [ ] `PEMBUAT_PASAR` melawan arah: penyedia likuidatas menyerap, bukan mengejar
- [ ] `BERUNGKIT` diam sampai ambangnya lewat, lalu searah — itulah yang membuat
      kaskade mungkin

**Cabut-uji:** buat `PEMBUAT_PASAR` searah → test peredamnya MERAH.

---

## Task 2: Premis yang dieja

**Files:** buat `src/aruna/scenario/premis.py`, `tests/test_premis.py`

**Produces:** `Absorpsi`, `Kedalaman`, `Dorongan` StrEnum; `Premis` dataclass;
`kisi(pemicu) -> tuple[Premis, ...]`

- [ ] Tanpa acak sama sekali — kisi tetap, bukan sampel
- [ ] `Dorongan` hanya divariasikan kalau `BERITA_BESAR` menyala; `Kedalaman
      TIPIS` hanya kalau volatilitas/volume menyala. Premis yang buktinya tidak
      ada adalah karangan berformat
- [ ] Tiap `Premis` punya `.kalimat` yang bisa dibaca manusia — skenario tanpa
      asumsi yang tertulis tidak bisa dibantah
- [ ] Kisi terkecil tetap ≥ 3 lintasan; satu lintasan bukan simulasi

**Cabut-uji:** buang gerbang bukti → test "tanpa berita tidak ada premis
dorongan" MERAH.

---

## Task 3: Mesin lintasan

**Files:** buat `src/aruna/scenario/kerumunan.py`, `tests/test_kerumunan.py`

**Produces:** `Keadaan`, `Lintasan`, `RONDE`, `jalankan(premis, ...) -> Lintasan`,
`simulasikan_kerumunan(pemicu, ...) -> tuple[Lintasan, ...]`

- [ ] Deterministik: masukan sama → lintasan sama, byte per byte. Penjaga AST
      menolak `random`, `time`, `now`
- [ ] Kedalaman menyusut saat ketidakseimbangan besar → gerak berikutnya lebih
      besar dari aliran yang sama. Itu efek orde-dua §16.8, bukan hiasan
- [ ] Likuidasi berantai terpicu dari dalam, bukan dijadwalkan: `BERUNGKIT`
      terlempar, aliran paksanya menggerakkan harga, yang melempar sisanya
- [ ] Lintasan menyimpan `jejak` per ronde — kaskade yang tidak bisa dilihat
      per ronde tidak bisa dibantah
- [ ] Tidak meledak: harga berbatas, ronde berbatas

**Cabut-uji:** buat kedalaman tetap → test kaskade MERAH.

---

## Task 4: Klasifikasi keluarga

**Files:** ubah `kerumunan.py`, `tests/test_kerumunan.py`

**Produces:** `klasifikasi(lintasan) -> str`

- [ ] Enam keluarga, namanya **sama persis** dengan yang `mesin.py` pakai —
      nama yang meleset membuat bobotnya tidak pernah ketemu skenarionya
- [ ] `False Breakout` butuh naik **lalu** kembali; hanya melihat titik akhir
      tidak bisa membedakannya dari `Sideways`
- [ ] Ambang klasifikasinya diturunkan dari ATR, bukan persen mutlak

**Cabut-uji:** klasifikasi hanya dari titik akhir → test False Breakout MERAH.

---

## Task 5: Bobot dari kerumunan

**Files:** ubah `src/aruna/scenario/mesin.py`, `tests/test_scenario_mesin.py`

- [ ] `_bobot` dan `_GESER` **dihapus**; bobot = pangsa lintasan
- [ ] Tiga skenario wajib §16.5 tetap muncul walau nol lintasan
- [ ] `bukti` tiap skenario menyebut "kerumunan: N/M lintasan" — klaim yang bisa
      diperiksa, bukan angka telanjang
- [ ] Jumlah bobot tetap 100; `keyakinan` tetap diturunkan dari bobot
- [ ] Determinisme mesin tetap dijaga penjaga AST yang sudah ada
- [ ] `VERSI` naik ke `internal-2` — §16.19 membandingkan per versi, dan hasil
      dua mesin dalam satu angka tidak mengatakan apa pun tentang keduanya

**Cabut-uji:** kembalikan bobot tetap → test "bobot mengikuti lintasan" MERAH.

---

## Task 6: Ruff, suite, restart, ukur

- [ ] `ruff check src tests`
- [ ] Suite penuh, sendirian
- [ ] Restart, verifikasi lewat `StartTime`
- [ ] Ukur: skenario tersimpan sesudah `internal-2`, bobotnya, `level=error` = 0
- [ ] Laporkan apa adanya, termasuk kalau bobotnya ternyata mirip yang lama

---

## Hasil pelaksanaan — 2026-08-22

Task 1–6 selesai. Tiap task lolos cabut-uji.

**Lima cacat ditemukan, seluruhnya di kode yang kutulis sendiri**, dan tiap satu
lewat pengukuran alih-alih penalaran:

1. **Titik tetap di nol.** Simulasi dimulai dari pasar netral sempurna, dan tiap
   kohort bereaksi terhadap besaran yang semuanya nol. 18 lintasan, semuanya
   rata, semuanya `Sideways`. Mesinnya benar; pertanyaannya yang salah —
   peristiwa pemicunya **adalah** rangsangannya.
2. **Premis tidak bisa membalik kesimpulan.** Kekuatan penyerapan dibagikan ke
   aliran *bersih*, yang hanya mengubah besar umpan balik dan tidak pernah
   tandanya. Peredam menang di tiap premis: 34 dari 36 lintasan `Sideways`.
   Penyerapan adalah sifat pihak yang **menyerap**, jadi dikenakan pada mereka
   saja.
3. **Tren tanpa mekanisme mempertahankan diri.** Pengikut tren hanya melihat
   gerak ronde terakhir, yang meluruh — jadi momentum mati secara struktural.
4. **Kaskade tanpa kolam.** Posisi berungkit terlempar tanpa pernah habis: satu
   lintasan berakhir di **+12,54 ATR**. Ditambah asimetri saturasi — modal
   pengejar habis, modal pemudar tidak — yang membuat tren berhenti sendiri.
5. **Penjaga jadi dinamika.** Batas gerak per ronde menggigit hampir tiap ronde,
   sehingga bentuk lintasannya ditentukan pemotongan alih-alih simulasi.
   Sekarang 2 dari 468 ronde.

**Satu cabut-uji gagal menggigit, dan itu temuan tersendiri.** Mekanisme
kedalaman-menyusut dicabut sepenuhnya dan seluruh test tetap hijau: klaim efek
orde-dua §16.8 tidak diuji apa pun, karena momentum sendirian sudah cukup
mencapai ambang likuidasi. Diperbaiki dengan seam `susut=` yang membuat klaimnya
bisa diadu langsung.

**Satu bug produksi, ketahuan pada hari pertama.** Pemindai menilai bar tertutup
yang sama tiap siklus, jadi satu tembusan AVAX/USDT tersimpan **empat kali** —
`scenario_id` berbeda karena stempel detiknya berbeda, sehingga `INSERT IGNORE`
tidak menahannya, dan jumlah bobot yang seharusnya seratus terbaca empat ratus.
Mode kegagalan yang sama persis dengan `market_snapshots` (62% basis data).
Seluruh test yang ada lolos: masing-masing memanggil `jalankan` sekali, dan satu
panggilan tidak pernah bisa menunjukkan pengulangan. Diperbaiki dengan kunci
per-bar per-simbol, ditambah lima test dan 54 baris ganda dibersihkan.

**Terukur di produksi** sesudah restart:

| Ukuran | Nilai |
|---|---|
| Aset dengan pemicu menyala | 4 |
| Skenario tersimpan (`internal-2`) | 18 |
| Jumlah bobot per aset | 100 di keempatnya |
| `level=error` | 0 |
| Waktu satu simulasi | mikrodetik |

Contoh nyata, AVAX/USDT pada `BREAKOUT_BESAR`: Bullish Continuation 50
(`2/3 lintasan`), Sideways 25 (`1/3`), Bearish Reversal 13 dan False Breakout 12
(keduanya `0/3` — muncul karena §16.5 mewajibkannya, dengan bobot lantai yang
tertulis apa adanya).

## Yang disebut, bukan disembunyikan

- **Kerumunan ini tidak terkalibrasi terhadap pasar sungguhan.** §16.6 sudah
  menyatakan bobot bukan probabilitas, dan itu tetap berlaku. Yang berubah:
  angkanya sekarang keluaran simulasi, bukan tetapan tangan.
- **Pangsa dan reaktivitas kohort adalah kebijakan, bukan pengukuran.** Sama
  seperti `AMBANG_BESAR`. Bedanya, sekarang keduanya ada di satu tempat, dieja
  per kohort, dan akibatnya bisa dilihat dengan menjalankan lintasannya.
- **Enam pemicu dari tiga belas** yang tersambung tidak berubah oleh task ini.
