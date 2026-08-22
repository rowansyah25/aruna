"""Pengamat keputusan benar-benar dipanggil jalur hidup (PASAL 14.3, 14.25).

Ini keempat kalinya pola yang sama dijaga di sesi ini: kode yang benar, diuji,
diekspor, dan tidak pernah dilewati jalur hidup. Sebuah pengamat yang tidak
pernah dipanggil mengukur nol keputusan, dan angka nol yang tidak dilaporkan
tidak bisa dibedakan dari sistem yang sehat.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.futures.service import observe_decision

NOW_ARGS = {"horizon": SimpleNamespace(value="4h"), "equity": Decimal("10000")}


def _svc(modul):
    """``FuturesPlanService`` dengan I/O-nya dilepas, cukup sampai baris uji."""
    from aruna.futures.service import FuturesPlanService

    verdict = SimpleNamespace(
        symbol="BTC/USDT", interval="4h",
        decision=SimpleNamespace(value="BUY"),
        confidence=0.6, opinions=(),
        protest=SimpleNamespace(objections=(), rebuttals=(), disagreement=0.1),
        veto=SimpleNamespace(vetoes=(), upheld=(), reviews=()),
        judgement=SimpleNamespace(minority_prevailed=False),
    )
    svc = FuturesPlanService.__new__(FuturesPlanService)
    svc._council = SimpleNamespace(convene=lambda ctx: verdict)
    svc._council_store = None
    # Bagian 16.2: jalur ini sekarang menyimpan funding dan open interest.
    # `None` mematikannya - yang diuji di sini bukan penyimpanannya.
    svc._metrik = None
    svc._deliberation = SimpleNamespace(
        build_context=lambda *a, **k: asyncio.sleep(0, SimpleNamespace(as_of=None))
    )
    svc._resolve_asset = lambda symbol: asyncio.sleep(
        0, SimpleNamespace(id=1, symbol=symbol)
    )
    provider = SimpleNamespace(
        snapshot=lambda symbol: asyncio.sleep(
            0, SimpleNamespace(symbol=symbol, funding=None)
        )
    )
    return svc, provider


class TestPengamatDipanggil:
    class _Berhenti(Exception):
        """Menghentikan ``_plan_one`` tepat di baris yang diuji."""

    @pytest.mark.asyncio
    async def test_observe_decision_dipanggil(self, monkeypatch) -> None:
        from datetime import UTC, datetime

        from aruna.futures import service as modul

        dicatat: dict = {}

        def _tangkap(**kw):
            dicatat.update(kw)
            raise self._Berhenti

        monkeypatch.setattr(modul, "observe_decision", _tangkap)
        # Lapisan di antara council dan baris uji dilewati: yang diperiksa di
        # sini hanya apakah pengamatnya dipanggil sama sekali.
        monkeypatch.setattr(modul, "attach_regime", lambda n, c, **k: n)
        monkeypatch.setattr(
            modul, "attach_decision_readings", lambda n, c, v, **k: n
        )
        monkeypatch.setattr(modul, "build_plan", lambda **k: SimpleNamespace(
            actionable=False, caveats=(), size_detail=None
        ))
        monkeypatch.setattr(modul, "_structure_of", lambda *a: (None, ()))
        monkeypatch.setattr(modul, "_atr_of", lambda *a: None)
        monkeypatch.setattr(modul, "_rebase_ratio", lambda *a: None)
        monkeypatch.setattr(modul, "_hostile", lambda c: False)

        svc, provider = _svc(modul)
        svc._store = None

        with pytest.raises(self._Berhenti):
            await svc._plan_one(
                provider, "BTCUSDT", risk_pct=None,
                now=datetime(2026, 8, 19, tzinfo=UTC), **NOW_ARGS,
            )

        assert dicatat["symbol"] == "BTCUSDT"

    def test_pengamat_rusak_tidak_menjatuhkan_rencana(self, monkeypatch) -> None:
        """Sebuah pengamat yang menjatuhkan rencana adalah kebalikan dari
        gunanya.

        Kegagalannya disuntikkan ke ``amati`` itu sendiri, bukan lewat objek
        yang rusak: pembacaan atribut sudah dijaga di dalam ``amati``, jadi
        objek rusak tidak akan pernah mencapai penjaga luar yang diuji di sini.
        Sebuah test yang mengira ia menguji penjaga luar padahal ditangkap
        penjaga dalam adalah test yang tidak menguji apa pun.
        """
        import aruna.decision.observe as modul_observe

        def _meledak(**_kw):
            raise RuntimeError("bentuknya berubah")

        monkeypatch.setattr(modul_observe, "amati", _meledak)

        # Tidak boleh melempar apa pun ke pemanggil.
        observe_decision(
            context=None, verdict=None, plan=None, note=None, symbol="BTCUSDT"
        )
