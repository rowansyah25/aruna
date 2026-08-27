"""Setiap fungsi/kelas publik punya pemanggil, atau punya ALASAN tertulis.

**Cacat ini sudah muncul lima kali di ARUNA**, dan tiap kali ditemukan ulang
lewat audit manual: `AdaptiveLearningService` yang cuma jalan lewat perintah
manual, pembersih retensi yang lengkap dan tidak pernah menyapu, penilai PASAL
15.44 yang menghitung putusan yang tidak pernah ditulis, `aruna.scenario.evaluasi`
yang punya nol pemanggil, dan `Putusan.diinvalidasi` yang dihitung lalu dibuang.
Semuanya lulus test unitnya.

Akar masalahnya bukan salah satu dari kelimanya. Akarnya: **tidak ada tempat
yang mencatat keputusan.** Sebuah fungsi yang menganggur karena sengaja dan
sebuah fungsi yang menganggur karena lupa disambungkan terlihat sama persis dari
luar, dan audit berikutnya harus memeriksa keduanya lagi dari nol.

Test ini bukan larangan. Ia menuntut **keputusan**: kalau sebuah nama publik
tidak dirujuk di mana pun dalam ``src/``, harus ada baris di :data:`DISENGAJA`
yang menyebut kenapa. Yang ke-27 gagal keras, bukan menunggu audit berikutnya.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "aruna"

#: Nama publik tanpa pemanggil di ``src/``, berikut alasannya.
#:
#: Empat golongan, dan bedanya menentukan tindakan kalau salah satunya berubah.
DISENGAJA: dict[str, str] = {
    # -- 1. Kawat perangkap: ADA supaya tidak pernah menyala ----------------
    "RealTradingForbiddenError": (
        "SPEC 46: MVP paper-trading. **Yang benar-benar menjaga BUKAN kelas ini** "
        "melainkan `AppSettings._enforce_paper_only`, yang menolak "
        "ARUNA_REAL_TRADING_ENABLED=true saat konfigurasi dibaca sehingga ARUNA "
        "tidak menyala sama sekali. Ia melempar ValueError, bukan kelas ini, "
        "karena validator pydantic menuntutnya. Kelas ini disiapkan untuk jalur "
        "eksekusi yang belum ada; hari ini ia dokumentasi berbentuk kode. "
        "Operator memutuskan membiarkannya 2026-08-23."
    ),
    "DataLeakageError": (
        "SPEC 24. Sinyal yang tersentuh data masa depan harus dibatalkan, bukan "
        "diperbaiki diam-diam. Nol pemanggil berarti belum pernah terdeteksi."
    ),
    "NotAuthorizedError": (
        "Perintah dari chat di luar allowlist. Telegram berjalan tanpa token di "
        "pemasangan ini, jadi jalurnya belum pernah dilalui."
    ),
    "ShutdownError": "Komponen gagal saat mematikan diri. Belum pernah terjadi.",
    # -- 2. Kebijakan yang ditegakkan di WAKTU TEST, bukan runtime ----------
    "telegram_allows": (
        "Daftar putih jenis pesan. Penegakannya di test - `test_phase12_learning` "
        "menolak jenis pesan baru yang tidak masuk daftar. Gerbang runtime akan "
        "menduplikasi aturan yang sama di dua tempat."
    ),
    "require_phase": (
        "SPEC 49: fitur yang fasenya belum dibangun tidak boleh tampil bekerja. "
        "Diuji di `test_runtime_state`. Belum ada fitur yang mendahului fasenya."
    ),
    # -- 3. Kemampuan yang belum ada kebutuhannya --------------------------
    # `resample_candles` keluar dari daftar ini 2026-08-27: `aruna.xau.timeframes`
    # memanggilnya untuk merakit M15/H1/H4 dari M5.
    "is_resampled": (
        "Penanda sumber turunan pada `Candle.provenance`. `resample_candles` "
        "menulisnya, tapi belum ada pembaca yang perlu MEMBEDAKAN bar turunan "
        "dari bar native - modul XAU merakit keempat timeframenya dari satu "
        "sumber, jadi di sana semuanya turunan kecuali M5."
    ),
    "incomplete_buckets": (
        "Menjelaskan ember mana yang dibuang `resample_candles` dan kenapa. "
        "`aruna.xau.timeframes` cukup melaporkan timeframe mana yang belum "
        "cukup bahannya lewat `TumpukanTimeframe.kurang()`; rincian per-ember "
        "belum ada yang membacanya."
    ),
    # -- 3b. Rencana XAU 1: lapisan data, pemanggilnya di Rencana 2 ---------
    "rakit_tumpukan": (
        "Rencana XAU 1 membangun lapisan datanya saja: menarik M5 dan "
        "menurunkan M15/H1/H4 darinya. Yang memanggilnya adalah mesin sinyal "
        "di Rencana 2, yang belum ditulis. Diuji penuh di "
        "`test_xau_timeframes`, termasuk cabut-uji `require_closed`. "
        "Keputusan operator 2026-08-27: bangun bertahap, jangan sekali jadi."
    ),
    "periksa_kelayakan": (
        "Gerbang NO SIGNAL untuk data basi/hilang/invalid. Pemanggilnya sama "
        "dengan `rakit_tumpukan` - mesin sinyal Rencana 2. Sengaja TIDAK "
        "dirangkai ke jalur crypto/futures: spec menuntut modul XAU terpisah, "
        "dan futures memakai kosakata LONG/SHORT yang berbeda."
    ),
    "balikkan": (
        "Bagian 23: perubahan parameter otomatis harus bisa dibalikkan. Modulnya "
        "sendiri menyatakan belum ada parameter hidup yang bisa dibalikkan - yang "
        "disediakan KEMAMPUANNYA, bukan pemakaiannya."
    ),
    "idx_tick_size": "Fraksi harga IDX. Dipakai saat menyemai universe, bukan tiap siklus.",
    "Kalibrator": (
        "**Kalibrasi masih DIHITUNG, tapi sejak 2026-08-25 tidak lagi "
        "DITERAPKAN - dan itu keadaan yang harus terlihat, bukan disembunyikan.** "
        "Satu-satunya yang memakai kelas ini `SignalService`, dan jalur spot "
        "dicabut atas keputusan operator. `Terkalibrasi` dari modul yang sama "
        "tetap terpakai lewat `signals.lock`, jadi modulnya tidak boleh dihapus. "
        "Akibat nyata yang terukur: atap keyakinan 0,573 - yang menekan klaim "
        "91% menjadi 47% sesuai akurasi terukurnya - hilang, dan keyakinan "
        "kembali ke nilai mentah beratap 0,95."
    ),
    "gate": (
        "**Gerbang mutu bagian 18.42 - dan ketiadaan pemanggilnya adalah CELAH "
        "yang sengaja dibiarkan terlihat, bukan kode mati.** Sampai 2026-08-25 "
        "satu-satunya pemanggilnya `SignalService`, dan jalur spot dicabut atas "
        "keputusan operator. Jalur futures menghitung `score_signal` tapi TIDAK "
        "pernah menjalankan gerbangnya: rencana ditolak `build_plan` atas alasan "
        "posisi (R:R, buffer likuidasi, council tak mengambil sisi), bukan atas "
        "mutu buktinya. "
        "Menghapus fungsi ini akan membuat celah itu tidak terlihat lagi; "
        "merangkainya ke futures akan MENAMBAH penolakan pada jalur yang sudah "
        "nol PLAN sejak 2026-08-22, jadi itu keputusan operator - bukan "
        "keputusan yang diambil diam-diam saat merapikan."
    ),
    # -- 5. Sedang dibangun: sudah lahir, pemanggilnya belum -----------------
    #
    # Baris di golongan ini WAJIB hilang lagi. `test_daftar_alasan_tidak_
    # menyimpan_yang_sudah_tersambung` gagal begitu pemanggilnya ada, jadi
    # daftar ini tidak bisa menyimpannya diam-diam sesudah tugasnya selesai.
    #
    # **Golongan ini KOSONG lagi per 2026-08-23, dan kosongnya itu buktinya
    # bekerja.** Enam baris pernah berdiri di sini - `susun_peta`,
    # `stabilitas`, `performa_rezim`, `kandidat_layak`, `pilih`, dan
    # `RouterRepository`: seluruh Phase 17 Task 1 sampai 7. Task 8
    # menyambungkan fase routernya ke `UpkeepLoop`, dan keenamnya jatuh sebagai
    # basi dalam satu langkah.
    #
    # Tanpa test ini, keenamnya akan tetap lulus seluruh test unitnya sambil
    # tidak pernah dipanggil - cacat yang di proyek ini sudah enam kali
    # ditemukan lewat audit manual, bukan lewat suite.
    #
    # Satu kesalahan yang layak diingat: alasan tiga baris pertama semula
    # menyebut "pemanggilnya lahir di Task 4". Salah - Task 4 menerima hasilnya
    # sebagai PARAMETER dan tidak memanggil satu pun. Alasan yang menyebut
    # tugas yang sudah selesai membuat barisnya terlihat siap dihapus, lalu
    # tidak dihapus, lalu berhenti dibaca.
    # -- 4. Perkakas pengembangan dan kosakata -----------------------------
    "reset_logging": "Mengembalikan logging antar test. Tidak punya arti di produksi.",
    "reset_settings_cache": "Sama seperti `reset_logging`, untuk cache settings.",
    "clear_context": "Pasangan `bind_context`, dipakai test.",
    "is_configured": "Pemeriksa keadaan logging, dipakai test.",
    "bind_context": (
        "Menempelkan nilai ke tiap baris log berikutnya. Produksi memakai "
        "structlog langsung; ini permukaan yang lebih rapi yang belum dipakai."
    ),
    "LossCause": "Kosakata sebab kerugian. Kolomnya ada, pengisinya belum.",
    "ModelRole": "Kosakata peran model. Dipakai saat lebih dari satu model hidup.",
    "TradingModeFlag": "SPEC 46: MVP punya tepat satu nilai sah di sini.",
    "age_seconds": "Umur sebuah stempel waktu. Pemanggilnya menghitung sendiri.",
    "idx_session": "Sesi bursa IDX. `idx_active` yang dipakai jalur produksi.",
    "is_idx_open": "Pendamping `idx_session`; `IDX_CALENDAR.is_open` yang dipakai.",
    "deadline_from": "Batas horizon absolut. Jalur penguncian menghitungnya sendiri.",
    "decimal_or_none": "Pembantu konversi. Repositori lain memakai `_f` masing-masing.",
    "is_append_only_violation": (
        "Mengenali galat MySQL dari trigger append-only. Belum ada yang "
        "menangkapnya - pelanggarannya naik sebagai DatabaseError biasa."
    ),
    "candidates_from": (
        "Menyusun kandidat dari riwayat seleksi. Jalur pembelajaran memakai "
        "`pilih` yang menyusunnya sendiri."
    ),
    # -- 6. Ditemukan 2026-08-23 saat penghitungnya diperketat --------------
    #
    # Keduanya lolos versi lama karena namanya dipakai sebagai NAMA LAIN di
    # tempat lain - `nilai_satu` ada dua (yang satu di `upkeep/manfaat.py`
    # memang terpakai), dan `tally` terbaca sebagai atribut. Diperiksa satu per
    # satu sebelum ditulis di sini, bukan didaftarkan borongan.
    "nilai_satu": (
        "Bagian 16.19, dan ia BUKAN kembaran `nilai_dari_pasar`. Yang terpakai "
        "menilai dari keluarga jejak harga; yang ini menilai dari RANTAI "
        "KONSEKUENSI - berapa langkah perkembangan yang benar-benar terjadi - "
        "dan hanya ia yang bisa menjawab 'dua dari tiga langkah terjadi'. "
        "Bahannya `perkembangan_terjadi`, satu boolean per langkah, dan "
        "**tidak ada yang menghitungnya**: itu menuntut memeriksa tiap kalimat "
        "rantai terhadap candle. Kemampuannya ada, pemasok buktinya belum."
    ),
}


def _publik(sumber: str) -> dict[str, str]:
    pohon = ast.parse(sumber)
    keluar: dict[str, str] = {}
    for n in pohon.body:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            if not n.name.startswith("_"):
                keluar[n.name] = "fungsi"
        elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
            keluar[n.name] = "kelas"
    return keluar


def _lintas(sumber: str) -> Counter:
    """Rujukan yang hanya mungkin datang dari LUAR modul pendefinisinya.

    Dua bentuk, dan keduanya diperlukan karena kode ini memakai keduanya:
    ``from x import y`` dan ``modul.y(...)``. Meninggalkan yang kedua akan
    menuduh seluruh :mod:`aruna.analysis.indicators` dan
    :mod:`aruna.notify.telegram.formatting` menganggur - terukur 67 tuduhan,
    dua setengah kali daftar keputusan yang ada.

    **Atribut hanya dihitung kalau ia DIPANGGIL.** ``x.nilai`` yang cuma dibaca
    hampir selalu bidang dataclass, bukan fungsi modul; ``x.nilai(...)`` hampir
    selalu sebaliknya. Perbedaan itu menurunkan tuduhan dari 67 ke 33 dan
    sekaligus **menemukan dua yatim nyata** yang versi sebelumnya lewatkan.

    **Batasnya diakui, bukan disembunyikan.** Penghitung ini tetap tidak bisa
    membedakan fungsi modul dari METODE yang namanya sama. Sebuah fungsi
    bernama ``nilai`` akan terlihat terpakai kalau ada kelas mana pun yang
    punya metode ``nilai``. Menutupnya menuntut analisis lingkup sungguhan;
    yang ada sekarang menyaring sebagian besar, dan sisanya harus ditangkap
    dengan cara lain - penjaga AST per-fase seperti `test_scenario_terpasang`.
    """
    pohon = ast.parse(sumber)
    c: Counter = Counter()
    for n in ast.walk(pohon):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            c[n.func.attr] += 1
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                c[a.name] += 1
    return c


def _lokal(sumber: str) -> Counter:
    """Nama telanjang yang DIBACA di dalam satu berkas.

    Dihitung terpisah dari :func:`_lintas`, dan hanya berlaku untuk berkas yang
    mendefinisikan namanya. Sebuah fungsi yang dipanggil tetangganya di modul
    yang sama tidak yatim - `dilabeli_router` di `router/label.py` begitu - tapi
    `ast.Name` di berkas LAIN tidak membuktikan apa pun tentangnya.
    """
    pohon = ast.parse(sumber)
    c: Counter = Counter()
    for n in ast.walk(pohon):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            c[n.id] += 1
    return c


def _menganggur() -> dict[str, str]:
    """Nama publik yang tidak dirujuk di mana pun dalam ``src/``.

    **Cara menghitungnya dikoreksi 2026-08-23, dan koreksinya penting.**

    Versi pertama menjumlahkan seluruh `ast.Name` dari seluruh berkas ke dalam
    satu penghitung. Akibatnya sebuah fungsi yang namanya kebetulan kata umum
    terlihat punya pemanggil karena nama itu dipakai sebagai **variabel lokal
    di berkas lain**. Terbukti pada `router.kecocokan.nilai`: nol pemanggil,
    tapi lolos karena `label.py` memakai `nilai` sebagai variabel biasa.

    Itu lubang di penjaga yang justru ada untuk cacat paling berulang di proyek
    ini - dan lubangnya melebar tepat pada nama berbahasa Indonesia, yang
    dipakai di seluruh kode ini.
    """
    berkas = [b for b in sorted(SRC.rglob("*.py")) if b.name != "__init__.py"]
    sumber = {b: b.read_text(encoding="utf-8") for b in sorted(SRC.rglob("*.py"))}

    lintas: Counter = Counter()
    for teks in sumber.values():
        lintas.update(_lintas(teks))

    keluar: dict[str, str] = {}
    for b in berkas:
        sendiri = _lokal(sumber[b])
        for nama, jenis in _publik(sumber[b]).items():
            if lintas[nama] == 0 and sendiri[nama] == 0:
                keluar[nama] = f"{jenis} di {b.relative_to(SRC.parent.parent)}"
    return keluar


class TestPermukaanPublikPunyaKeputusan:
    def test_tidak_ada_yang_menganggur_tanpa_alasan(self) -> None:
        baru = {
            n: t for n, t in _menganggur().items() if n not in DISENGAJA
        }

        assert not baru, (
            "publik, tapi tidak dirujuk di mana pun dalam src/ - dan belum ada "
            "yang memutuskan kenapa:\n"
            + "\n".join(f"  {n:<32} {t}" for n, t in sorted(baru.items()))
            + "\n\nSambungkan, hapus, atau tulis alasannya di DISENGAJA. "
            "Yang ketiga adalah keputusan, bukan jalan pintas: cacat ini sudah "
            "lima kali muncul di proyek ini, dan tiap kali karena tidak ada "
            "tempat yang mencatat bahwa seseorang pernah memeriksanya."
        )

    def test_daftar_alasan_tidak_menyimpan_yang_sudah_tersambung(self) -> None:
        """Daftar pengecualian yang tidak pernah dibersihkan berhenti dibaca,
        lalu berubah menjadi tempat menyembunyikan hal baru."""
        menganggur = _menganggur()
        basi = sorted(n for n in DISENGAJA if n not in menganggur)

        assert not basi, (
            f"sudah punya pemanggil, jadi barisnya boleh dihapus: {basi}"
        )

class TestPenghitungnyaTidakBisaDIPALSUKAN:
    """**Lubang yang ditemukan 2026-08-23, sesudah penjaga ini dipakai.**

    Penjaga yang bisa dipuaskan oleh kebetulan lebih buruk daripada tidak ada
    penjaga: ia memberi rasa aman tanpa memberi jaminan.
    """

    def test_variabel_lokal_bernama_sama_bukan_pemanggil(self) -> None:
        """Inilah kasusnya, apa adanya. `router.kecocokan.nilai` nol pemanggil,
        tapi versi pertama meloloskannya karena `router/label.py` memakai
        `nilai` sebagai nama variabel biasa.

        Lubangnya melebar tepat pada nama berbahasa Indonesia - `nilai`,
        `hasil`, `bacaan`, `putusan` - yaitu seluruh kosakata kode ini.
        """
        pendefinisi = "def nilai(x):\n    return x\n"
        tetangga = "def lain(row):\n    nilai = row.get('a')\n    return nilai\n"

        lintas = Counter()
        lintas.update(_lintas(pendefinisi))
        lintas.update(_lintas(tetangga))

        assert lintas["nilai"] == 0
        assert _lokal(pendefinisi)["nilai"] == 0

    def test_impor_lintas_modul_tetap_terhitung(self) -> None:
        """Yang benar-benar dipakai harus tetap lolos - kalau tidak, daftar
        DISENGAJA akan membengkak sampai berhenti dibaca."""
        pemakai = "from aruna.router.kecocokan import nilai\n\nnilai(1)\n"

        assert _lintas(pemakai)["nilai"] == 1

    def test_pemanggilan_lewat_atribut_terhitung(self) -> None:
        assert _lintas("import mod\n\nmod.nilai(1)\n")["nilai"] == 1

    def test_pemanggil_di_modul_yang_sama_bukan_yatim(self) -> None:
        """`dilabeli_router` dipanggil `performa_rezim` di berkas yang sama.
        Menghitungnya yatim akan memaksa menulis alasan untuk fungsi yang
        jelas-jelas terpakai."""
        sendiri = (
            "def dilabeli_router(r):\n    return True\n\n"
            "def performa_rezim(rows):\n"
            "    return [r for r in rows if dilabeli_router(r)]\n"
        )

        assert _lokal(sendiri)["dilabeli_router"] == 1

    def test_definisinya_sendiri_bukan_rujukan(self) -> None:
        assert _lokal("def nilai(x):\n    return x\n")["nilai"] == 0
        assert _lintas("class Kecocokan:\n    pass\n")["Kecocokan"] == 0

    def test_atribut_yang_cuma_dibaca_bukan_pemanggil(self) -> None:
        """`x.nilai` yang cuma dibaca hampir selalu bidang dataclass;
        `x.nilai(...)` hampir selalu fungsi. Membedakannya menurunkan tuduhan
        dari 67 ke 33 dan sekaligus menemukan dua yatim nyata."""
        dibaca = "def f(b):\n    return b.nilai\n"
        dipanggil = "def f(m):\n    return m.nilai(1)\n"

        assert _lintas(dibaca)["nilai"] == 0
        assert _lintas(dipanggil)["nilai"] == 1

    def test_batasnya_diakui_bukan_disembunyikan(self) -> None:
        """**Penjaga ini masih punya lubang, dan mengakuinya bagian dari
        gunanya.** Ia tidak bisa membedakan fungsi modul dari METODE yang
        namanya sama - sebuah fungsi `nilai` terlihat terpakai kalau ada kelas
        mana pun yang punya metode `nilai`.

        Test ini ada supaya siapa pun yang bersandar penuh pada penjaga ini
        tahu apa yang TIDAK dijaminnya. Sisanya harus ditangkap penjaga AST
        per-fase, seperti `test_scenario_terpasang`.
        """
        metode = "class A:\n    def nilai(self, x):\n        return x\n\na.nilai(1)\n"

        assert _lintas(metode)["nilai"] == 1


class TestKawatPerangkap:
    def test_kawat_perangkap_tetap_tidak_menyala(self) -> None:
        """Yang ini justru HARUS tetap menganggur. `RealTradingForbiddenError`
        yang punya pemanggil berarti ada jalur kode yang mencoba eksekusi
        sungguhan - kabar buruk, bukan perbaikan."""
        menganggur = _menganggur()

        assert "RealTradingForbiddenError" in menganggur
        assert "DataLeakageError" in menganggur
