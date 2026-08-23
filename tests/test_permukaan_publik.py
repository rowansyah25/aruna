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
    "resample_candles": (
        "Horizon yang tidak ditawarkan provider - 10m di Binance spot, 3/5-hari "
        "di Yahoo. ARUNA aktif di 1m/15m/1h/1d, semuanya native."
    ),
    "is_resampled": "Pendamping `resample_candles`, lihat alasannya.",
    "incomplete_buckets": "Pendamping `resample_candles`, lihat alasannya.",
    "balikkan": (
        "Bagian 23: perubahan parameter otomatis harus bisa dibalikkan. Modulnya "
        "sendiri menyatakan belum ada parameter hidup yang bisa dibalikkan - yang "
        "disediakan KEMAMPUANNYA, bukan pemakaiannya."
    ),
    "idx_tick_size": "Fraksi harga IDX. Dipakai saat menyemai universe, bukan tiap siklus.",
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
}


def _publik(berkas: Path) -> dict[str, str]:
    pohon = ast.parse(berkas.read_text(encoding="utf-8"))
    keluar: dict[str, str] = {}
    for n in pohon.body:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            if not n.name.startswith("_"):
                keluar[n.name] = "fungsi"
        elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
            keluar[n.name] = "kelas"
    return keluar


def _rujukan(berkas: Path) -> Counter:
    """Nama yang DIRUJUK. Definisinya sendiri bukan rujukan."""
    pohon = ast.parse(berkas.read_text(encoding="utf-8"))
    c: Counter = Counter()
    for n in ast.walk(pohon):
        if isinstance(n, ast.Name):
            c[n.id] += 1
        elif isinstance(n, ast.Attribute):
            c[n.attr] += 1
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                c[a.name] += 1
    return c


def _menganggur() -> dict[str, str]:
    berkas = sorted(SRC.rglob("*.py"))
    dirujuk: Counter = Counter()
    for b in berkas:
        dirujuk.update(_rujukan(b))

    keluar: dict[str, str] = {}
    for b in berkas:
        if b.name == "__init__.py":
            continue
        for nama, jenis in _publik(b).items():
            if dirujuk[nama] == 0:
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

    def test_kawat_perangkap_tetap_tidak_menyala(self) -> None:
        """Yang ini justru HARUS tetap menganggur. `RealTradingForbiddenError`
        yang punya pemanggil berarti ada jalur kode yang mencoba eksekusi
        sungguhan - kabar buruk, bukan perbaikan."""
        menganggur = _menganggur()

        assert "RealTradingForbiddenError" in menganggur
        assert "DataLeakageError" in menganggur
