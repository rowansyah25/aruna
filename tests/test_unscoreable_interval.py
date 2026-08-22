"""Prediksi yang tertahan seri tak terjaga ditutup, bukan digantung selamanya.

Terukur: 88 prediksi IDX terkunci sejak 15 Agustus. Semuanya tertahan oleh satu
sebab yang sama - seri sampling 1m untuk IDX, yang **tidak dijaga refresh mana
pun**. Bukan data yang terlambat; data yang tidak akan pernah datang.

``due()`` mengurutkan dengan ``resolves_at``, jadi yang tertua duduk di kepala
antrean pada setiap pass, selamanya, mendorong prediksi yang benar-benar bisa
diskor ke belakang.

**Yang TIDAK dilakukan penutupan ini**, dan itu yang membuatnya patuh PASAL
11.21: tidak ada outcome ditulis, tidak ada paper trade, tidak ada menang dan
tidak ada kalah. Win rate tidak bergerak satu angka pun. Entry, stop, target,
dan confidence prediksinya tetap persis seperti saat dikunci. Yang berubah
hanya status daur hidupnya - dari "menunggu" menjadi "tidak bisa dijawab" -
dengan alasan terukurnya disimpan supaya keputusan itu bisa diaudit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aruna.core.enums import Horizon, Market
from aruna.signals.models import SignalStatus
from aruna.signals.service import PriceWindow, ResolveResult, SignalService

SAAT = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.status: list[dict] = []

    async def set_status(self, signal_id, status, **kwargs):
        self.status.append({"signal_id": signal_id, "status": status, **kwargs})


def _signal(signal_id: str = "abc"):
    return SimpleNamespace(
        signal_id=signal_id,
        symbol="ANTM",
        market=Market.IDX,
        horizon=Horizon.M15,
        locked_at=SAAT - timedelta(days=3),
        resolves_at=SAAT - timedelta(days=3) + timedelta(minutes=15),
        is_directional=True,
    )


def _service(store: _Store) -> SignalService:
    svc = SignalService.__new__(SignalService)
    svc._store = store
    svc._interval_reported = set()
    svc._claimed = {}
    return svc


class TestDitutupBukanDigantung:
    @pytest.mark.asyncio
    async def test_statusnya_jadi_unscoreable(self) -> None:
        store = _Store()
        hasil = ResolveResult()

        await _service(store)._close_as_unscoreable(
            _signal(), "seri 1m untuk IDX tidak dijaga refresh mana pun", hasil
        )

        assert store.status[0]["status"] is SignalStatus.UNSCOREABLE
        assert hasil.unscoreable == 1

    @pytest.mark.asyncio
    async def test_alasannya_disimpan_supaya_bisa_diaudit(self) -> None:
        store = _Store()
        await _service(store)._close_as_unscoreable(
            _signal(), "seri 1m untuk IDX tidak dijaga refresh mana pun",
            ResolveResult(),
        )

        assert "1m untuk IDX" in store.status[0]["withheld_reason"]

    @pytest.mark.asyncio
    async def test_tidak_ada_waktu_resolusi_dicatat(self) -> None:
        """``resolved_at`` yang terisi akan membuat prediksi ini terbaca seperti
        prediksi yang selesai diskor."""
        store = _Store()
        await _service(store)._close_as_unscoreable(
            _signal(), "alasan", ResolveResult()
        )

        assert store.status[0]["resolved_at"] is None

    @pytest.mark.asyncio
    async def test_tidak_ada_outcome_dan_tidak_ada_paper_trade(self) -> None:
        """PASAL 11.21. Menutup bukan menskor: tidak ada menang, tidak ada
        kalah, dan win rate tidak bergerak."""
        store = _Store()
        hasil = ResolveResult()

        await _service(store)._close_as_unscoreable(_signal(), "alasan", hasil)

        assert hasil.outcomes == []
        assert hasil.scored == []
        assert hasil.trades == {}
        assert hasil.resolved == 0


class TestCabangnyaMemangDicapai:
    """Metode penutupnya sudah ada sejak lama dan hanya dipanggil dari satu
    cabang. Yang baru adalah cabang kedua - dan cabang yang tidak dipanggil
    tidak menutup apa pun."""

    @pytest.mark.asyncio
    async def test_interval_tak_terjaga_ikut_ditutup(self, monkeypatch) -> None:
        store = _Store()
        svc = _service(store)
        svc._claimed = {}

        async def _window(signal, *, moment, require_fresh=True):
            return PriceWindow(
                [], None, "interval_unavailable",
                "the only sampling series still behind is one no refresh set "
                "covers: 1m for IDX",
            )

        async def _get(signal_id):
            return _signal(signal_id), "sidik-jari"

        svc._prices_during = _window
        store.get = _get

        # SPEC 20 memverifikasi bahwa baris yang diskor adalah baris yang
        # ditulis. Verifikasinya diganti di sini karena yang diuji adalah cabang
        # penutupannya, bukan keutuhan sidik jarinya - dan membangun sidik jari
        # asli akan membuat test ini gagal saat definisi sidik jari berubah,
        # untuk alasan yang tidak ada hubungannya dengan apa yang diuji.
        import aruna.signals.service as modul

        monkeypatch.setattr(modul, "verify_integrity", lambda s, f: None)
        monkeypatch.setattr(modul, "is_resolvable", lambda s, reference: (True, ""))

        hasil = ResolveResult()
        await svc._resolve_one("abc", SAAT, hasil)

        assert hasil.unavailable_interval == 1
        assert hasil.unscoreable == 1
        assert store.status[0]["status"] is SignalStatus.UNSCOREABLE

    def test_kedua_cabang_memanggil_penutup_yang_sama(self) -> None:
        import inspect

        sumber = inspect.getsource(SignalService._resolve_one)
        assert sumber.count("_close_as_unscoreable") == 2


class TestDikatakanKeOperator:
    def test_jumlahnya_disebut_di_catatan_run(self) -> None:
        """Menutup prediksi adalah tindakan atas catatan, dan tindakan atas
        catatan harus terbaca tanpa operator perlu menanyakannya."""
        import asyncio
        import inspect

        sumber = inspect.getsource(SignalService.resolve_due)
        assert "result.unavailable_interval:" in sumber
        assert "win rate tidak bergerak" in sumber
        assert asyncio is not None

    def test_catatannya_menyangkal_menang_kalah(self) -> None:
        import inspect

        sumber = inspect.getsource(SignalService.resolve_due)
        assert "tidak ada menang atau kalah" in sumber
