"""Setiap laporan berarah menyebut versi model yang menghasilkannya.

Operator: "tiap laporan long atau short harus ada keterangan pakai model
berapa."

Alasannya bukan kelengkapan. Rekam jejak yang mencampur beberapa versi model
mengukur rata-rata dari hal-hal yang berbeda - dan rata-rata itu paling stabil
justru ketika satu versi memburuk sementara versi lain menutupinya. Versinya
sudah tersimpan di setiap baris ``signal_snapshots`` sejak lama; yang belum ada
hanyalah menyebutkannya kepada pembacanya.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.core.enums import Decision
from aruna.notify.verdict import VoteSplit, render_analysis

SPLIT = VoteSplit(("TECHNICAL",), ("RISK",))


class TestAnalysisMenyebutVersinya:
    def test_versinya_dicetak(self) -> None:
        teks = render_analysis(
            symbol="BTC/USDT", decision=Decision.BUY, split=SPLIT,
            model_version="aruna-v1.4",
        )
        assert "MODEL:\naruna-v1.4" in teks

    def test_tanpa_versi_tidak_mencetak_baris_kosong(self) -> None:
        """Baris "MODEL:" dengan nilai kosong terbaca sebagai versi bernama
        kosong, bukan sebagai versi yang tidak diketahui."""
        teks = render_analysis(
            symbol="BTC/USDT", decision=Decision.BUY, split=SPLIT
        )
        assert "MODEL:" not in teks

    def test_short_juga_menyebutnya(self) -> None:
        teks = render_analysis(
            symbol="BTC/USDT", decision=Decision.SELL, split=SPLIT,
            model_version="aruna-v1.4",
        )
        assert "MODEL:\naruna-v1.4" in teks


class TestFuturesMenyebutVersinya:
    def _plan(self, **overrides):
        base = {
            "symbol": "BTCUSDT",
            "verdict": __import__(
                "aruna.futures.plan", fromlist=["PlanVerdict"]
            ).PlanVerdict.PLAN,
            "side": SimpleNamespace(value="LONG"),
            "entry": Decimal("63000"),
            "stop": Decimal("61800"),
            "target": Decimal("64500"),
            "quantity": Decimal("0.4"),
            "leverage": 10,
            "margin_mode": SimpleNamespace(value="ISOLATED"),
            "liquidation": SimpleNamespace(price=Decimal("56900")),
            "net_rr": Decimal("1.17"),
            "tick_size": Decimal("0.10"),
            "caveats": (),
        }
        return SimpleNamespace(**(base | overrides))

    @pytest.mark.asyncio
    async def test_versinya_dicetak(self) -> None:
        from aruna.futures.notify import PlanNotifier

        class _Sender:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, text: str) -> bool:
                self.sent.append(text)
                return True

        from datetime import UTC, datetime

        sender = _Sender()
        await PlanNotifier(sender=sender, model_version="futures-f5").announce(
            [self._plan()], now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        )
        assert "MODEL:       futures-f5" in sender.sent[0]

    def test_versinya_dieja_sekali_untuk_pesan_dan_penyimpanan(self) -> None:
        """Dua konstanta yang mengeja versi yang sama boleh berbeda - dan kalau
        berbeda, pesan Telegram akan menyebut versi yang bukan versi yang
        tercatat di database."""
        import inspect

        from aruna.futures.service import FUTURES_MODEL_VERSION, FuturesPlanService

        tanda_tangan = inspect.signature(FuturesPlanService.__init__)
        bawaan = tanda_tangan.parameters["model_version"].default

        assert bawaan == FUTURES_MODEL_VERSION

    def test_cli_memakai_konstanta_yang_sama(self) -> None:
        import inspect

        from aruna import cli

        sumber = inspect.getsource(cli._futures_loop)
        assert "model_version=FUTURES_MODEL_VERSION" in sumber


class TestVersinyaDibawaDariPrediksi:
    """Bukan diketik di lapisan pesan: yang dicetak harus versi yang benar-benar
    menghasilkan keputusan itu, bukan versi yang sedang berjalan sekarang."""

    def test_baris_signal_membawanya(self) -> None:
        from aruna.upkeep.loop import _signal_row

        signal = SimpleNamespace(
            symbol="BTC/USDT", direction=Decision.BUY, confidence=0.7,
            reference_price="63000", target_price="66000",
            horizon=SimpleNamespace(value="15m"), model_version="aruna-v1.4",
        )
        assert _signal_row(signal)["model_version"] == "aruna-v1.4"

    def test_baris_hasil_membawanya(self) -> None:
        from aruna.upkeep.loop import _result_row

        signal = SimpleNamespace(
            symbol="BTC/USDT", direction=Decision.BUY, signal_id="abc",
            reference_price="63000", target_price="66000",
            model_version="aruna-v1.4",
        )
        outcome = SimpleNamespace(
            outcome_class=SimpleNamespace(value="TARGET_REACHED"),
            target_reached=True,
        )
        assert _result_row(signal, outcome)["model_version"] == "aruna-v1.4"

    def test_prediksi_lama_tanpa_versi_tidak_meledak(self) -> None:
        from aruna.upkeep.loop import _signal_row

        signal = SimpleNamespace(
            symbol="BTC/USDT", direction=Decision.BUY, confidence=0.7,
            reference_price="63000", target_price="66000",
            horizon=SimpleNamespace(value="15m"),
        )
        assert _signal_row(signal)["model_version"] is None

    def test_prediksi_menyimpannya(self) -> None:
        """Kolomnya memang sudah ada - itu sebabnya perbaikan ini tidak butuh
        migrasi."""
        from aruna.signals.models import LockedSignal

        assert "model_version" in LockedSignal.__dataclass_fields__
