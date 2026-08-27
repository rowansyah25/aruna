"""Satu konstanta, satu tempat.

**Audit 2026-08-23** menemukan sembilan nama konstanta yang didefinisikan di
lebih dari satu modul. Dua di antaranya nyata:

* ``HIGH_DISAGREEMENT`` bernilai **0,4** di `council.protest` dan **0,75** di
  `futures.debate`. Nama sama, metrik sama, pertanyaan berbeda - yang pertama
  memutuskan apakah ronde review adversarial dijalankan, yang kedua apakah
  selisihnya layak disebut ke pembaca. Yang mengimpor "HIGH_DISAGREEMENT"
  mendapat angka yang berbeda tergantung modul mana yang kebetulan diraih, dan
  itu sudah terjadi: bagian 16.2 menurunkan ambangnya dari yang 0,4.
* ``CASCADE_SHARE`` didefinisikan dua kali dengan nilai yang sama. Dua angka
  yang HARUS sepakat tapi ditulis di dua tempat adalah dua angka yang suatu
  saat tidak sepakat - tanpa satu pun test merah, karena masing-masing menguji
  miliknya sendiri.

Sisanya kebetulan senama, dan itu ditulis di daftar di bawah supaya bedanya
tercatat alih-alih ditemukan ulang tiap audit.

Test ini **tidak** melarang nama yang sama. Ia melarang nama yang sama muncul
tanpa ada yang pernah memutuskan bahwa itu disengaja.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "aruna"

#: Nama yang memang didefinisikan di lebih dari satu modul, berikut alasannya.
#:
#: Menambah baris di sini adalah keputusan, dan keputusan itu yang test ini
#: minta - bukan supaya daftarnya panjang, melainkan supaya tidak ada yang
#: bertambah tanpa seorang pun menyadarinya.
DISENGAJA: dict[str, str] = {
    "BANDS": (
        "pita skor, tapi untuk dua spec yang berbeda: FUTURES SPEC 36 "
        "(safety score) dan SPEC 25 (liquidation score). Bentuknya sama, "
        "yang diskor berbeda."
    ),
    "BASE_URL": "dua venue berbeda - Yahoo, Binance futures, dan Twelve Data.",
    "MIN_TARGET_ATR": (
        "pelajaran yang sama atas dua pasar yang berbeda, dan itu justru "
        "kenapa keduanya TIDAK boleh berbagi satu konstanta. Futures "
        "menetapkannya dari hasil futures; XAU M5 harus menetapkannya dari "
        "hasil XAU. Kalau futures menyetel ulang lantainya - dan modul itu "
        "memang menyetel diri - sebuah impor akan menggeser ambang XAU "
        "diam-diam, atas bukti yang bukan miliknya. Angkanya kebetulan sama "
        "hari ini karena alasannya sama: satu ATR adalah pergerakan khas, "
        "jadi menargetkan satu ATR berarti menargetkan hasil imbang yang "
        "terukur paling buruk. Spec XAU juga menuntut modul terpisah, dan "
        "mengimpor dari `aruna.futures` akan melanggarnya."
    ),
    "BERARAH": (
        "himpunan yang berbeda atas enum yang berbeda: frozenset[Butir] "
        "tiga anggota di `decision.audit`, frozenset[Jejak] lima anggota di "
        "`decision.trail`."
    ),
    "CACHE_TTL_SEC": (
        "dua cache yang berbeda dengan masa berlaku yang kebetulan sama, "
        "masing-masing dibenarkan sendiri di docstring-nya."
    ),
    "INTERVALS": "peta interval dua API yang berbeda - spot dan futures.",
    "KUNCI_STATE": (
        "kunci baris `app_state` yang berbeda: 'perubahan_parameter' dan "
        "'memory_manfaat'. Namanya generik, isinya tidak berhubungan."
    ),
    "MASK": (
        "'[klaim disensor]' menyensor klaim terlarang, '***REDACTED***' "
        "menyensor nilai rahasia. Dua penyamar untuk dua bahaya."
    ),
    "MIN_COVERAGE": (
        "`decision.score` mengimpor dari `risk.score` sejak 2026-08-23, jadi "
        "keduanya satu sumber. Yang ketiga - `signals.quality` - menjawab "
        "pertanyaan lain: sampel minimum sebelum skor mutu berarti, bukan "
        "cakupan komponen skor."
    ),
    "MIN_SAMPLE": (
        "50 di `futures.learning` disamakan dengan MIN_TOTAL_SAMPLE lapisan "
        "spot supaya angka futures dan spot dibaca pada skala yang sama; 30 di "
        "`learning.evidence` datang dari lebar selang kepercayaan. Dua "
        "pertanyaan, dua angka."
    ),
    "PRICE_SCALE": (
        "keduanya DECIMAL(30,12), tapi masing-masing terikat pada KOLOM yang "
        "berbeda - `futures_plans` (migrasi 0015) dan `signal_snapshots`. "
        "Menyatukannya membuat satu tabel diam-diam mengikuti presisi tabel "
        "lain kalau salah satunya berubah."
    ),
    "PUBLIC_ENDPOINTS": "endpoint dua API yang berbeda - spot dan futures.",
    "SOURCE": (
        "label sumber data per modul: 'binance-spot', 'binance-spot-ws', dan "
        "seterusnya. Justru harus berbeda."
    ),
    "URUTAN": (
        "urutan tahap keputusan di `decision.hierarchy` dan urutan tingkat "
        "risiko di `risk.calibration`. Dua enum, dua urutan."
    ),
    "WAJIB": (
        "tahap yang wajib lulus di `decision.hierarchy`, dan masukan yang "
        "wajib ada per fase di `decision.integration`. Dua enum berbeda."
    ),
}


def _konstanta_per_nama() -> dict[str, list[str]]:
    tempat: dict[str, list[str]] = defaultdict(list)
    for berkas in sorted(SRC.rglob("*.py")):
        pohon = ast.parse(berkas.read_text(encoding="utf-8"))
        for n in pohon.body:
            sasaran: list[ast.expr] = []
            if isinstance(n, ast.Assign):
                sasaran = list(n.targets)
            elif isinstance(n, ast.AnnAssign):
                sasaran = [n.target]
            if not sasaran:
                continue
            # ``X = _X`` adalah RE-EXPORT, bukan definisi kedua. Bedanya
            # menentukan: re-export punya satu sumber kebenaran, definisi
            # kedua punya dua yang bisa melenceng.
            if isinstance(getattr(n, "value", None), ast.Name):
                continue
            for t in sasaran:
                if (
                    isinstance(t, ast.Name)
                    and t.id.isupper()
                    and not t.id.startswith("_")
                    and t.id != "__all__"
                ):
                    tempat[t.id].append(str(berkas))
    return tempat


class TestSatuKonstantaSatuTempat:
    def test_tidak_ada_duplikat_yang_belum_diputuskan(self) -> None:
        ganda = {
            nama: berkas
            for nama, berkas in _konstanta_per_nama().items()
            if len(berkas) > 1 and nama not in DISENGAJA
        }

        assert not ganda, (
            "konstanta bernama sama di lebih dari satu modul, dan belum ada "
            "yang memutuskan bahwa itu disengaja:\n"
            + "\n".join(
                f"  {nama}\n" + "\n".join(f"      {b}" for b in berkas)
                for nama, berkas in sorted(ganda.items())
            )
            + "\n\nKalau memang dua hal yang berbeda, tulis alasannya di "
            "DISENGAJA. Kalau satu hal, satu modul yang mendefinisikannya dan "
            "yang lain mengimpor."
        )

    def test_daftar_disengaja_tidak_menyimpan_yang_sudah_lurus(self) -> None:
        """Daftar pengecualian yang tidak pernah dibersihkan berhenti dibaca,
        dan berubah menjadi tempat menyembunyikan hal baru."""
        tempat = _konstanta_per_nama()
        basi = [n for n in DISENGAJA if len(tempat.get(n, [])) <= 1]

        assert not basi, (
            f"tidak ganda lagi, jadi barisnya boleh dihapus: {sorted(basi)}"
        )

    def test_high_disagreement_tinggal_satu(self) -> None:
        """Konflik nyata yang memicu test ini. Disebut namanya supaya
        kemunculannya lagi gagal keras, bukan gagal berbulan-bulan kemudian
        sebagai ambang yang salah di modul yang tidak berhubungan."""
        tempat = _konstanta_per_nama()

        assert len(tempat.get("HIGH_DISAGREEMENT", [])) == 1

    def test_cascade_share_tinggal_satu(self) -> None:
        tempat = _konstanta_per_nama()

        assert len(tempat.get("CASCADE_SHARE", [])) == 1
