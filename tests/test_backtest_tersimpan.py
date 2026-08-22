"""Backtest yang dihitung lalu dibuang tidak pernah menjadi bukti.

Terukur 2026-08-21: `backtest_runs` berisi **nol baris**, dan karena itu
`WALK_FORWARD` dan `OUT_OF_SAMPLE` - dua dari sebelas masukan yang PASAL 14.40
wajibkan - dilaporkan hilang pada setiap keputusan sejak Phase 14 selesai.

Sebabnya bukan mesinnya. `BacktestService` menghitung fold walk-forward,
holdout, dan seluruh peringatannya dengan lengkap; `BacktestRepository`
punya `record_backtest`; dan perintah `aruna backtest` **mencetak hasilnya lalu
membuangnya**. Tidak ada satu pun pemanggil `record_backtest` di seluruh kode.

Keluarga cacat yang sama dengan delapan modul `aruna.decision` yang diam,
dengan mesin korelasi bertabel nol baris, dan dengan `AdaptiveLearningService`
yang hanya belajar saat seseorang mengetik perintahnya.
"""

from __future__ import annotations

import ast
import inspect


class TestPerintahnyaMenyimpan:
    def test_cmd_backtest_memanggil_record_backtest(self) -> None:
        """Tanpa ini, tiap backtest yang dijalankan operator hilang begitu
        terminalnya ditutup - dan `backtest_runs` tetap nol selamanya."""
        from aruna import cli

        pohon = ast.parse(inspect.getsource(cli))
        for n in ast.walk(pohon):
            if (isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and n.name == "_backtest"):
                nama = {
                    getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                    for c in ast.walk(n) if isinstance(c, ast.Call)
                }
                assert "record_backtest" in nama, (
                    "hasil backtest dihitung lalu dibuang - `backtest_runs` "
                    "tidak akan pernah terisi, dan WALK_FORWARD/OUT_OF_SAMPLE "
                    "hilang dari tiap keputusan"
                )
                return
        raise AssertionError("_backtest tidak ada di cli.py")

    def test_repositorinya_punya_pembacanya(self) -> None:
        """Menyimpan tanpa pembaca adalah setengah cacat yang sama."""
        from aruna.db.repositories.backtest import BacktestRepository

        assert hasattr(BacktestRepository, "recent_runs")
