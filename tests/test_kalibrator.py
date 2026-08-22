"""Keyakinan yang diterbitkan harus sesuai kenyataan (bagian 9, 10).

Bagian 9 mengeja syaratnya: *"Jika ARUNA mengatakan Confidence 80%, maka secara
historis keputusan dengan confidence sekitar 80% harus memiliki tingkat
keberhasilan yang mendekati angka tersebut dalam sample yang cukup."*

Terukur 2026-08-21, dan syarat itu dilanggar dengan arah yang terbalik:

    BUY  ragu (<50%)      654 keputusan   menang 55,2%
    BUY  sangat yakin     903 keputusan   menang 47,7%

Makin yakin ARUNA, makin sering ia salah. Verdict sistemnya sendiri:
``OVERCONFIDENT in 80-96%``.

`learning/calibration.py` sudah MENGUKUR ini dengan benar sejak lama. Yang
tidak pernah ada adalah **pemetanya** - sesuatu yang mengubah keyakinan mentah
menjadi keyakinan yang sesuai kenyataan sebelum diterbitkan.
"""

from __future__ import annotations

import pytest

from aruna.learning.calibration import (
    MIN_BUCKET_SAMPLE,
    Bucket,
    CalibrationReport,
)
from aruna.learning.kalibrator import Kalibrator


def _bucket(low: float, high: float, *, n: int, benar: int, rata: float) -> Bucket:
    return Bucket(
        low=low, high=high, predictions=n, correct=benar, mean_confidence=rata
    )


#: Bentuk yang benar-benar terukur di produksi: akurasi TURUN saat keyakinan
#: naik. Dipakai berulang di bawah supaya tiap test menguji keadaan nyata,
#: bukan keadaan yang nyaman.
TERBALIK = CalibrationReport(
    buckets=(
        _bucket(0.35, 0.50, n=654, benar=361, rata=0.44),   # 55,2%
        _bucket(0.50, 0.65, n=981, benar=496, rata=0.58),   # 50,6%
        _bucket(0.65, 0.80, n=740, benar=354, rata=0.73),   # 47,8%
        _bucket(0.80, 0.96, n=903, benar=431, rata=0.90),   # 47,7%
    ),
    total=3278,
    correct=1642,
)


class TestMemetakanKeKenyataan:
    def test_keyakinan_tinggi_diturunkan_ke_akurasi_terukur(self) -> None:
        """Inti bagian 9. Keyakinan 90% yang terukur benar 47,7% harus
        diterbitkan sebagai 47,7%, bukan 90%."""
        hasil = Kalibrator(TERBALIK).kalibrasi(0.90)

        assert hasil.disesuaikan
        assert hasil.nilai == pytest.approx(0.4773, abs=0.001)
        assert hasil.mentah == 0.90

    def test_keyakinan_rendah_dinaikkan_kalau_kenyataannya_lebih_baik(self) -> None:
        """Kalibrasi bekerja dua arah. Bagian 9 menyebut UNDERCONFIDENT sebagai
        keadaan yang harus dideteksi, jadi menaikkan pun bagian dari tugasnya -
        dan pita terendah ARUNA memang terukur lebih baik daripada klaimnya."""
        hasil = Kalibrator(TERBALIK).kalibrasi(0.40)

        assert hasil.disesuaikan
        assert hasil.nilai == pytest.approx(0.5520, abs=0.001)

    def test_memakai_pita_tempat_nilainya_jatuh(self) -> None:
        hasil = Kalibrator(TERBALIK).kalibrasi(0.70)

        assert hasil.pita == "65-80%"
        assert hasil.nilai == pytest.approx(0.4784, abs=0.001)


class TestKapanTIDAKMenyesuaikan:
    """Bagian 10: jangan menghukum berdasarkan satu atau dua trade."""

    def test_sampel_pita_kurang_tidak_disentuh(self) -> None:
        """Pita dengan 19 pengamatan tidak tahu apa-apa tentang akurasinya.
        Menyesuaikan berdasarkan itu adalah mengarang, bukan mengkalibrasi.
        """
        tipis = CalibrationReport(
            buckets=(_bucket(0.80, 0.96, n=MIN_BUCKET_SAMPLE - 1, benar=2,
                             rata=0.90),),
            total=19, correct=2,
        )

        hasil = Kalibrator(tipis).kalibrasi(0.90)

        assert not hasil.disesuaikan
        assert hasil.nilai == 0.90
        assert "sampel" in hasil.alasan.lower()

    def test_tanpa_laporan_tidak_disentuh(self) -> None:
        """Sistem yang belum pernah mengukur tidak boleh berpura-pura sudah."""
        hasil = Kalibrator(None).kalibrasi(0.90)

        assert not hasil.disesuaikan
        assert hasil.nilai == 0.90

    def test_di_luar_seluruh_pita_tidak_disentuh(self) -> None:
        """Keyakinan di bawah lantai kunci atau di atas plafon tidak punya pita
        yang mengukurnya."""
        hasil = Kalibrator(TERBALIK).kalibrasi(0.10)

        assert not hasil.disesuaikan
        assert hasil.nilai == 0.10


class TestKejujuranPeta:
    def test_peta_yang_terbalik_ditandai(self) -> None:
        """Peta kalibrasi yang tidak monoton adalah temuan, bukan detail.

        Ia berarti keyakinan sistem berkorelasi TERBALIK dengan kebenaran -
        dan meratakannya diam-diam (misalnya dengan regresi isotonik) akan
        menyembunyikan justru hal yang paling perlu diketahui operator.
        """
        assert not Kalibrator(TERBALIK).monoton

    def test_peta_yang_wajar_tidak_ditandai(self) -> None:
        wajar = CalibrationReport(
            buckets=(
                _bucket(0.35, 0.50, n=100, benar=42, rata=0.44),
                _bucket(0.50, 0.65, n=100, benar=58, rata=0.58),
                _bucket(0.65, 0.80, n=100, benar=71, rata=0.73),
                _bucket(0.80, 0.96, n=100, benar=88, rata=0.90),
            ),
            total=400, correct=259,
        )

        assert Kalibrator(wajar).monoton

    def test_alasannya_menyebut_angkanya(self) -> None:
        """Operator yang melihat keyakinan turun dari 90% ke 48% harus bisa
        tahu atas dasar apa tanpa menjalankan ulang pengukurannya."""
        alasan = Kalibrator(TERBALIK).kalibrasi(0.90).alasan

        assert "80-96%" in alasan
        assert "903" in alasan


class TestBatasAman:
    def test_hasilnya_selalu_di_dalam_nol_satu(self) -> None:
        ekstrem = CalibrationReport(
            buckets=(_bucket(0.80, 0.96, n=100, benar=100, rata=0.90),),
            total=100, correct=100,
        )

        assert Kalibrator(ekstrem).kalibrasi(0.90).nilai <= 1.0

    def test_pita_tanpa_pengamatan_tidak_membagi_dengan_nol(self) -> None:
        kosong = CalibrationReport(
            buckets=(_bucket(0.80, 0.96, n=0, benar=0, rata=0.0),),
            total=0, correct=0,
        )

        hasil = Kalibrator(kosong).kalibrasi(0.90)

        assert not hasil.disesuaikan
        assert hasil.nilai == 0.90

    def test_kalibrasi_tidak_mengubah_arah_keputusan(self) -> None:
        """Kalibrator hanya menyentuh ANGKA keyakinan. Arah LONG/SHORT/NO SIGNAL
        ditentukan Phase 14 dan tidak boleh berubah karena angka ini - kalau
        berubah, ini bukan kalibrasi melainkan mesin keputusan kedua.
        """
        import ast
        import inspect

        from aruna.learning import kalibrator

        # AST, bukan pencarian teks: docstring modul ini MENJELASKAN kenapa ia
        # tidak menyentuh arah, dan pencarian teks akan tersandung pada
        # penjelasannya sendiri.
        pohon = ast.parse(inspect.getsource(kalibrator))
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
        nama |= {n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)}
        nama |= {
            a.name for n in ast.walk(pohon)
            if isinstance(n, ast.ImportFrom) for a in n.names
        }

        for dilarang in ("Decision", "direction", "arah", "decision"):
            assert dilarang not in nama, dilarang
