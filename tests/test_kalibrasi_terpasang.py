"""Kalibrasi benar-benar menyentuh angka yang sampai ke operator (bagian 9).

Kalibrator yang benar dan tidak dipanggil adalah kegagalan yang paling sering
terjadi di repo ini. Dan satu jebakan khusus modul ini: kalibrasi yang menimpa
nilai mentahnya akan mengukur dirinya sendiri pada putaran berikutnya, lalu
melaporkan bahwa semuanya baik-baik saja.
"""

from __future__ import annotations

import ast
import inspect
from textwrap import dedent

from aruna.learning.calibration import Bucket, CalibrationReport
from aruna.learning.kalibrator import Kalibrator

TERBALIK = CalibrationReport(
    buckets=(
        Bucket(low=0.80, high=0.96, predictions=903, correct=431,
               mean_confidence=0.90),
    ),
    total=903,
    correct=431,
)


class TestDipakaiSaatMengunci:
    def test_build_signal_menerima_kalibrator(self) -> None:
        from aruna.signals.lock import build_signal

        assert "kalibrator" in inspect.signature(build_signal).parameters

    def test_service_meneruskannya(self) -> None:
        """Parameter yang ada tapi tidak pernah diisi sama saja dengan tidak
        ada."""
        from aruna.signals.service import SignalService

        pohon = ast.parse(dedent(inspect.getsource(SignalService.lock_signals)))
        kata = {
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "build_signal"
            for k in n.keywords
        }

        assert "kalibrator" in kata

    def test_use_history_membangunnya(self) -> None:
        """Laporan kalibrasi tiba lewat `use_history`. Kalau kalibratornya
        tidak dibangun di sana, ia selamanya kosong."""
        from aruna.signals.service import SignalService

        pohon = ast.parse(dedent(inspect.getsource(SignalService.use_history)))
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

        assert "_bangun_kalibrator" in dipanggil


class TestTidakMemakanEkornya:
    def test_pengukur_membaca_confidence_raw(self) -> None:
        """Inti jebakannya. Kalau `resolved()` membaca `s.confidence`, ia
        mengukur keluaran kalibrator dengan kalibrator - dan putaran kedua akan
        melaporkan kalibrasi sempurna di atas sistem yang tidak berubah."""
        from aruna.db.repositories.learning import LearningRepository

        pohon = ast.parse(dedent(inspect.getsource(LearningRepository.resolved)))
        sql = " ".join(
            n.value for n in ast.walk(pohon)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )

        assert "confidence_raw" in sql
        assert "COALESCE" in sql.upper()

    def test_penulis_menyimpan_keduanya(self) -> None:
        from aruna.db.repositories.signals import SignalRepository

        pohon = ast.parse(dedent(inspect.getsource(SignalRepository.lock)))
        sql = " ".join(
            n.value for n in ast.walk(pohon)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )

        assert "confidence_raw" in sql

    def test_placeholder_sama_dengan_nilai(self) -> None:
        """Menambah kolom tanpa menambah placeholder menggeser SELURUH
        parameter satu posisi - dan itu menaruh harga di kolom keyakinan tanpa
        satu pun error."""
        from aruna.db.repositories.signals import SignalRepository

        sumber = inspect.getsource(SignalRepository.lock)
        awal = sumber.index("INSERT INTO signal_snapshots")
        akhir = sumber.index('"""', awal)
        sql = sumber[awal:akhir]

        kolom = sql[sql.index("(") + 1 : sql.index(")")]
        assert len(kolom.split(",")) == sql.count("%s")


class TestPerilakuNyata:
    def test_keyakinan_tinggi_turun_ke_akurasi_terukur(self) -> None:
        """Ujung ke ujung pada nilai produksi: 90% dinyatakan, 47,7% terbukti."""
        hasil = Kalibrator(TERBALIK).kalibrasi(0.90)

        assert hasil.nilai < 0.50
        assert hasil.disesuaikan

    def test_alasannya_masuk_ke_reasoning_prediksi(self) -> None:
        """Prediksi yang angkanya dipetakan harus membawa catatan kenapa -
        bagian dari catatan beku, bukan penanda runtime."""
        from aruna.signals import lock

        pohon = ast.parse(dedent(inspect.getsource(lock.build_signal)))
        teks = " ".join(
            n.value for n in ast.walk(pohon)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )

        assert "dikalibrasi" in teks
