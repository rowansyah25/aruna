"""Spot tidak mengirim signal, dan itu keputusan - bukan kebetulan.

Operator, 2026-08-20: *"Spot gausah di kirim sinyalnya dan gausah pakai stop
loss dan jangan kirim signal"*.

**Perilakunya sudah begitu sebelum berkas ini ada, dan itu justru masalahnya.**
Signal spot lahir tanpa `stop`, dan gerbang "tidak bisa ditindaklanjuti"
menahannya - 521 kali dalam satu hari, masing-masing menulis satu baris log
yang berbunyi seperti temuan. Bukan temuan: itu perilaku yang diminta,
tercapai lewat jalan yang tidak pernah menyebutkannya.

Bedanya bukan gaya. Sebuah keputusan yang hanya tersirat dari bidang yang
kosong akan berbalik sendiri pada hari seseorang mengisi bidang itu - tanpa
ada yang menyadari bahwa sebuah keputusan baru saja dibatalkan.

**Yang TIDAK berhenti.** Signal spot tetap dikunci, disimpan, diresolusi, dan
tetap masuk laporan harian beserta win rate-nya. Yang berhenti hanya
pengirimannya. Menghentikan pencatatannya akan menghapus catatan bahwa ARUNA
pernah salah (§11.21), dan itu bukan yang diminta.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aruna.notify.result import SPOT_PUSH_AKTIF, SignalNotifier

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class _Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def ready(self) -> bool:
        return True

    async def send(self, teks: str) -> bool:
        self.sent.append(teks)
        return True

    async def send_id(self, teks: str, **kw) -> int:
        self.sent.append(teks)
        return 1


def _baris(**kw) -> dict:
    dasar = {
        "signal_id": "abc",
        "symbol": "BTC/USDT",
        "direction": "BUY",
        "confidence": 0.8,
        "entry": "100",
        "stop": "95",
        "target": "110",
        "timeframe": "4h",
    }
    return dasar | kw


class TestSpotTidakMengirim:
    def test_saklarnya_mati(self) -> None:
        """Dieja sebagai konstanta, bukan disembunyikan di dalam cabang."""
        assert SPOT_PUSH_AKTIF is False

    @pytest.mark.asyncio
    async def test_tidak_ada_yang_terkirim(self) -> None:
        sender = _Sender()
        n = SignalNotifier(sender=sender)

        assert await n.announce([_baris()], now=NOW) == 0
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_signal_lengkap_pun_tidak_dikirim(self) -> None:
        """Termasuk yang punya stop. Keputusannya "jangan kirim spot", bukan
        "jangan kirim yang datanya kurang" - dan kalau suatu hari ada yang
        mengisi `stop`, keputusan ini tidak boleh berbalik sendiri."""
        sender = _Sender()
        n = SignalNotifier(sender=sender)

        await n.announce(
            [_baris(stop="95"), _baris(signal_id="def", stop="90")], now=NOW
        )

        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_dicatat_sekali_per_siklus_bukan_per_signal(
        self, monkeypatch
    ) -> None:
        """521 baris sehari yang mengeluhkan perilaku yang diminta adalah log
        yang melatih pembacanya melewati baris - dan yang hilang berikutnya
        bukan baris ini."""
        from aruna.notify import result as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: keluar.append((n, k)),
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )

        n = SignalNotifier(sender=_Sender())
        await n.announce(
            [_baris(signal_id=f"s{i}") for i in range(20)], now=NOW
        )

        assert [nama for nama, _ in keluar] == ["signal.spot_push_mati"]
        assert keluar[0][1].get("count") == 20

    @pytest.mark.asyncio
    async def test_siklus_kosong_tidak_mencatat_apa_pun(
        self, monkeypatch
    ) -> None:
        """Tidak ada signal berarti tidak ada yang perlu dikatakan.

        Loop berdetak jauh lebih sering daripada signal muncul. Tanpa syarat
        ini, "spot tidak dikirim" tercetak tiap siklus selamanya - meredam 521
        baris lalu menggantinya dengan yang lain sama saja dengan tidak meredam
        apa pun.
        """
        from aruna.notify import result as modul

        keluar: list[str] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append(n),
                warning=lambda n, **k: keluar.append(n),
                exception=lambda n, **k: keluar.append(f"!{n}"),
            ),
        )

        n = SignalNotifier(sender=_Sender())

        assert await n.announce([], now=NOW) == 0
        assert keluar == []


class TestYangTidakBerhenti:
    def test_gerbang_stop_tidak_dihapus(self) -> None:
        """Jalur futures memakai penyusun yang sama, dan di sana stop WAJIB -
        pesan yang membawa leverage tanpa batas rugi adalah setengah fakta yang
        paling berbahaya di sistem ini."""
        from aruna.notify import result

        assert "signal.not_actionable" in inspect.getsource(result)

    def test_pencatatan_hasil_tidak_disentuh(self) -> None:
        """§11.21: menghentikan pengiriman bukan alasan berhenti mencatat.
        Signal spot tetap dikunci, diresolusi, dan tetap masuk laporan harian
        beserta kekalahannya."""
        from aruna.notify.result import ResultNotifier

        assert hasattr(ResultNotifier, "announce")

    def test_stop_loss_tidak_dikarang_untuk_spot(self) -> None:
        """Operator: *"gausah pakai stop loss"*. Sebuah stop yang dihitung
        hanya supaya gerbangnya lolos adalah angka yang dikarang (§13.26), dan
        ia akan tercetak seolah-olah ARUNA memilihnya."""
        from aruna.signals import lock

        sumber = inspect.getsource(lock)

        assert "_project_stop" not in sumber
