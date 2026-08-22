"""Kuantitas paper trade muat di kolomnya (dilaporkan lewat log produksi).

``Data truncated for column 'quantity' at row 1`` muncul pada **setiap** paper
trade yang ditutup. Kolomnya ``DECIMAL(30,12)``; ``capital / fill``
menghasilkan Decimal dengan dua puluh delapan digit berarti, dan MySQL
memotong sisanya sambil memperingatkan.

Peringatan yang selalu muncul berhenti dibaca, dan yang hilang berikutnya
adalah peringatan yang benar-benar penting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.core.enums import Decision, Horizon, Market
from aruna.signals.models import LockedSignal
from aruna.signals.paper import _QTY, close_trade, open_trade

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

#: Skala kolomnya di basis data. Diketik ulang, bukan diimpor dari kodenya:
#: sebuah test yang membandingkan konstanta dengan dirinya sendiri akan tetap
#: hijau berapa pun angkanya bergeser.
SKALA_KOLOM = 12


def signal(entry: str = "3.51", **kw) -> LockedSignal:
    dasar = {
        "signal_id": "abc123",
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "horizon": Horizon.M15,
        "direction": Decision.BUY,
        "confidence": 0.8,
        "reference_price": Decimal(entry),
        "entry_price": Decimal(entry),
        "target_price": Decimal(entry) * Decimal("1.01"),
        "expected_move_pct": 1.0,
        "locked_at": NOW,
        "as_of": NOW,
        "resolves_at": NOW,
    }
    return LockedSignal(**(dasar | kw))


def desimal(x: Decimal) -> int:
    """Berapa angka di belakang koma."""
    return max(0, -x.as_tuple().exponent)


class TestKuantitasMuatDiKolomnya:
    def test_kuantitas_tidak_melebihi_skala_kolom(self) -> None:
        """Kasus yang menghasilkan peringatan di produksi: 1000 / 3.51."""
        t = open_trade(signal(), capital=Decimal("1000"), opened_at=NOW)

        assert desimal(t.quantity) <= SKALA_KOLOM

    @pytest.mark.parametrize(
        "harga", ["3.51", "0.00001234", "81.51", "45.73", "9.992", "4.408"]
    )
    def test_harga_apa_pun_tetap_muat(self, harga: str) -> None:
        """Harga-harga ini diambil dari baris paper_trades yang sungguhan."""
        t = open_trade(signal(harga), capital=Decimal("1000"), opened_at=NOW)

        assert desimal(t.quantity) <= SKALA_KOLOM

    def test_pembagian_mentah_memang_meluber(self) -> None:
        """Bukti bahwa test di atas menguji sesuatu: tanpa pembulatan,
        angkanya memang melebihi kolomnya."""
        mentah = Decimal("1000") / Decimal("3.51")

        assert desimal(mentah) > SKALA_KOLOM

    def test_skalanya_sama_dengan_kolomnya(self) -> None:
        assert desimal(_QTY) == SKALA_KOLOM


class TestTargetMuatDiKolomnya:
    """``target_price`` - kolom yang paling banyak terpotong di log produksi.

    769 baris ``Data truncated for column 'target_price'``, lebih banyak
    daripada kolom mana pun. Ia satu-satunya harga yang **dihitung**; entry dan
    reference datang apa adanya dari venue.
    """

    def _target(self, atr: float, harga: str, arah: Decision):
        from aruna.signals.lock import _project_target

        konteks = SimpleNamespace(
            reading=lambda nama: SimpleNamespace(
                reliable=True, value=atr
            ) if nama == "atr" else None
        )
        return _project_target(konteks, Decimal(harga), arah)

    @pytest.mark.parametrize("atr", [0.123456789012345, 1.7976931348623157, 0.1])
    def test_atr_apa_pun_tetap_muat(self, atr: float) -> None:
        target, _ = self._target(atr, "45.73", Decision.BUY)

        assert target is not None
        assert desimal(target) <= SKALA_KOLOM

    def test_pembulatannya_menjauh_dari_acuan(self) -> None:
        """Kalau harus meleset, meleset ke arah yang membuat prediksinya lebih
        SULIT dipenuhi - tidak pernah lebih mudah."""
        acuan = Decimal("45.73")
        atr = 0.123456789012345

        naik, _ = self._target(atr, str(acuan), Decision.BUY)
        turun, _ = self._target(atr, str(acuan), Decision.SELL)
        jarak = Decimal(str(atr)) * Decimal("1.5")

        assert naik >= acuan + jarak
        assert turun <= acuan - jarak

    def test_tanpa_atr_tidak_mengarang_target(self) -> None:
        from aruna.signals.lock import _project_target

        konteks = SimpleNamespace(reading=lambda nama: None)

        assert _project_target(konteks, Decimal("45.73"), Decision.BUY) == (
            None, None
        )


class TestPembulatanKeBawah:
    def test_tidak_pernah_mengaku_memegang_lebih_dari_yang_dibeli(self) -> None:
        """Kuantitas yang dibulatkan ke atas berarti posisi yang sedikit lebih
        besar daripada yang dibeli modalnya."""
        modal = Decimal("1000")
        t = open_trade(signal(), capital=modal, opened_at=NOW)

        assert t.quantity * t.entry_price <= modal


class TestAngkaTurunanTetapCocok:
    def test_pnl_diturunkan_dari_kuantitas_yang_tersimpan(self) -> None:
        """Membulatkan hanya saat menulis akan menyimpan kuantitas yang tidak
        lagi cocok dengan PnL di baris yang sama - ketidakcocokan yang tidak
        berbunyi dan membuat rekonsiliasi nanti gagal tanpa sebab yang bisa
        ditebak."""
        t = open_trade(signal(), capital=Decimal("1000"), opened_at=NOW)
        selesai = close_trade(t, exit_price=Decimal("3.60"), closed_at=NOW)

        kotor = (Decimal("3.60") - t.entry_price) * t.quantity

        assert selesai.gross_pnl == kotor.quantize(Decimal("0.01"))
