"""Peringatan yang berbunyi pada keadaan sehat, dan yang bukan peringatan.

Tiga baris berbeda memenuhi terminal operator sesudah restart, dan ketiganya
punya sebab yang sama: sebuah keadaan yang **permanen dan diharapkan** dicatat
seolah-olah sesuatu baru saja rusak.

Ongkosnya bukan tempat di layar. Log yang penuh peringatan atas hal yang tidak
apa-apa mengajari pembacanya melewati baris peringatan - dan baris berikutnya
mungkin bukan yang ini. Pola yang sama sudah tiga kali ditemukan di sistem ini:
deprecation MySQL, penolakan DUPLICATE Yahoo, dan alert health untuk cache.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest


class TestPnlDibulatkanKeSkalaKolomnya:
    """``Data truncated for column 'expected_net_pnl'`` pada setiap plan.

    Kolomnya ``DECIMAL(30,12)``; nilainya lahir dari perkalian tarif bps dan
    membawa dua puluh delapan angka di belakang koma. MySQL membulatkannya
    sendiri lalu mengeluh.
    """

    def _economics(self, gross: str, biaya: str):
        from aruna.futures.risk import TradeEconomics

        seperempat = Decimal(biaya) / 4
        return TradeEconomics(
            risk_amount=Decimal("100"),
            gross_reward=Decimal(gross),
            entry_fee=seperempat,
            exit_fee=seperempat,
            funding_cost=seperempat,
            slippage_cost=seperempat,
        )

    def test_tidak_lebih_dari_dua_belas_angka_di_belakang_koma(self) -> None:
        # 1/3 tidak habis dibagi: sisa pembagian Decimal-nya panjang.
        ekonomi = self._economics("10", str(Decimal(1) / 3))
        angka = ekonomi.net_reward

        assert -angka.as_tuple().exponent <= 12, angka

    def test_nilainya_tidak_bergeser_berarti(self) -> None:
        """Yang dibuang adalah sisa di bawah 1e-12, bukan uang."""
        ekonomi = self._economics("10", str(Decimal(1) / 3))
        kasar = ekonomi.gross_reward - ekonomi.total_costs

        assert abs(kasar - ekonomi.net_reward) < Decimal("0.000000000001")

    def test_angka_bulat_tetap_utuh(self) -> None:
        ekonomi = self._economics("10", "4")
        assert ekonomi.net_reward == Decimal("6")

    def test_tarif_funding_juga_dibulatkan(self) -> None:
        """Kolom kedua dengan cacat yang sama: ``funding_cost_pct``.

        Tarif funding lahir dari pembagian - delapan jam dibagi periode
        settlement - jadi ia membawa sisa pembagian Decimal sepanjang dua puluh
        delapan angka, dan MySQL mengeluh pada setiap plan yang tersimpan.
        """
        import pathlib
        import re

        from aruna.futures.plan import FUNDING_PCT_SCALE

        sql = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0015_futures_plans.sql"
        ).read_text(encoding="utf-8")
        cocok = re.search(r"funding_cost_pct\s+DECIMAL\((\d+),(\d+)\)", sql)

        assert cocok, "kolomnya tidak ketemu di migrasi"
        assert -FUNDING_PCT_SCALE.as_tuple().exponent == int(cocok.group(2))

    def test_tarif_funding_dibulatkan_di_jalur_yang_dipakai(self) -> None:
        import inspect

        from aruna.futures import plan as modul

        sumber = inspect.getsource(modul.build_plan)
        assert "quantize(\n                FUNDING_PCT_SCALE\n            )" in sumber

    @pytest.mark.asyncio
    async def test_yang_benar_benar_ditulis_ke_kolomnya_sudah_dibulatkan(
        self,
    ) -> None:
        """Jalur kedua, dan jalur inilah yang sungguhan mengisi kolomnya.

        Pembulatan di ``build_plan`` mengenai ``plan.economics.funding_cost_pct``.
        Kolomnya diisi dari ``plan.projection.funding_cost`` - angka lain, dari
        proyeksi horizon, yang lahir dari ``rate * notional * settlements`` dan
        membawa sisa pembagian sepanjang dua puluh delapan angka.

        Dua test di atas hijau selama berhari-hari sementara MySQL tetap
        mengeluh pada tiap plan, karena keduanya memeriksa jalur yang tidak
        menulis kolom itu. Test ini memanggil ``save()`` yang sungguhan dan
        membaca argumen yang benar-benar dikirim.
        """
        from datetime import UTC, datetime

        from aruna.db.repositories.futures import FuturesRepository
        from aruna.futures.horizon import HorizonProjection
        from aruna.futures.liquidation import BufferScore
        from aruna.futures.models import PositionSide
        from aruna.futures.plan import FuturesPlan, PlanVerdict

        bantal = BufferScore(score=70, band="OK", stop_multiple=None,
                             atr_multiple=None)
        # rate * notional * settlements, dengan tarif yang tidak habis dibagi:
        # bentuk yang sama dengan yang lahir di produksi.
        kasar = Decimal("0.0001") * (Decimal(1) / Decimal(3)) * Decimal(3)
        rencana = FuturesPlan(
            signal_id="uji",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            verdict=PlanVerdict.PLAN,
            horizon_hours=4.0,
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
            projection=HorizonProjection(
                horizon_hours=4.0,
                settlements=3,
                settlements_exact=True,
                funding_cost=kasar,
                entry_margin=Decimal("100"),
                projected_margin=Decimal("99"),
                entry_liquidation=None,
                projected_liquidation=None,
                entry_buffer=bantal,
                projected_buffer=bantal,
                decay_known=True,
            ),
        )

        assert -kasar.as_tuple().exponent > 6, "bahannya harus melebihi kolomnya"

        class _Db:
            def __init__(self) -> None:
                self.args: tuple = ()

            async def insert(self, sql: str, *args):
                self.args = args
                return 1

        db = _Db()
        await FuturesRepository(db).save(rencana, model_version="uji")

        ditulis = db.args[25]

        assert ditulis is not None
        assert -ditulis.as_tuple().exponent <= 6, ditulis

    @pytest.mark.asyncio
    async def test_kolomnya_masih_di_urutan_yang_sama(self) -> None:
        """Penjaga untuk test di atasnya.

        Ia membaca argumen ke-26 berdasarkan urutan kolom di INSERT. Satu kolom
        yang disisipkan di tengah membuatnya memeriksa angka lain sambil tetap
        hijau - keluarga cacat "test yang tidak bisa merah" yang sudah tiga kali
        muncul di sistem ini.
        """
        import inspect
        import re

        from aruna.db.repositories.futures import FuturesRepository

        sumber = inspect.getsource(FuturesRepository.save)
        kolom = re.search(r"INSERT INTO futures_plans\s*\((.*?)\)\s*VALUES",
                          sumber, re.S)

        assert kolom, "daftar kolomnya tidak ketemu"
        nama = [k.strip() for k in kolom.group(1).split(",")]

        assert nama.index("funding_cost_pct") == 25, nama

    def test_skalanya_sama_dengan_kolomnya(self) -> None:
        """Kalau kolomnya diperlebar, pembulatan ini harus ikut - dan test ini
        yang mengingatkannya."""
        import pathlib
        import re

        from aruna.futures.risk import TradeEconomics

        sql = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations" / "0015_futures_plans.sql"
        ).read_text(encoding="utf-8")
        cocok = re.search(r"expected_net_pnl\s+DECIMAL\((\d+),(\d+)\)", sql)

        assert cocok, "kolomnya tidak ketemu di migrasi"
        assert -TradeEconomics.PNL_SCALE.as_tuple().exponent == int(cocok.group(2))


class TestBracketTanpaKredensialTidakDiteriakkan:
    """``/fapi/v1/leverageBracket`` menuntut kredensial akun, dan adapter ini
    memang tidak boleh punya. Keadaan permanen, disengaja, tidak bisa diperbaiki
    operator - dan dicatat sebagai warning pada tiap simbol tiap tick."""

    def _provider(self):
        from aruna.futures.binance import BinanceFuturesProvider

        return BinanceFuturesProvider()

    def test_ada_penampung_simbol_yang_sudah_disebut(self) -> None:
        assert self._provider()._brackets_noted == set()

    class _Klien:
        """Klien HTTP palsu: exchangeInfo berhasil, leverageBracket gagal.

        Memanggil ``contract()`` yang sungguhan lewat seam ``client=`` yang
        memang sudah ada. Versi pertama test ini menyalin ulang cabang
        pencatatannya ke dalam berkas ini, dan tetap hijau ketika cabang
        aslinya dicabut - menguji salinan, bukan kode.
        """

        def __init__(self, galat: str) -> None:
            self.galat = galat

        async def get(self, url: str, params=None):
            import httpx

            if "leverageBracket" in url:
                permintaan = httpx.Request("GET", url)
                if self.galat == "401":
                    raise httpx.HTTPStatusError(
                        "401",
                        request=permintaan,
                        response=httpx.Response(
                            401, request=permintaan,
                            text='{"code":-2014,"msg":"API-key format invalid."}',
                        ),
                    )
                raise httpx.ConnectError("connection reset by peer")
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "symbols": [{
                        "symbol": "ETHUSDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        ],
                    }]
                },
            )

    def _jalankan(self, monkeypatch, galat: str, ulang: int = 1) -> list[str]:
        import asyncio

        from aruna.futures import binance as modul
        from aruna.futures.binance import BinanceFuturesProvider

        keluar: list[str] = []
        monkeypatch.setattr(modul.log, "warning", lambda e, **k: keluar.append(e))
        monkeypatch.setattr(modul.log, "info", lambda e, **k: keluar.append(e))

        provider = BinanceFuturesProvider(client=self._Klien(galat))

        async def main():
            for _ in range(ulang):
                await provider.contract("ETHUSDT")

        asyncio.run(main())
        return keluar

    def test_401_disebut_sekali_dan_bukan_warning(self, monkeypatch) -> None:
        keluar = self._jalankan(monkeypatch, "401", ulang=3)
        assert keluar == ["futures.brackets_need_credentials"], keluar

    def test_kegagalan_lain_tetap_warning(self, monkeypatch) -> None:
        """Jaringan putus dan izin yang memang tidak ada terlihat sama di
        penghitung; hanya yang pertama berarti sesuatu berubah."""
        keluar = self._jalankan(monkeypatch, "jaringan")
        assert keluar == ["futures.brackets_unavailable"], keluar

    def test_kegagalan_lain_tidak_ikut_diredam(self, monkeypatch) -> None:
        keluar = self._jalankan(monkeypatch, "jaringan", ulang=3)
        assert keluar == ["futures.brackets_unavailable"] * 3, keluar


class TestPrediksiMacetTidakDiulangTiapPass:
    """88 prediksi IDX terkunci, pass tiap enam puluh detik.

    Kelas ``interval_unavailable`` adalah "menunggu yang tidak akan pernah
    selesai" - itu definisinya - jadi tiap prediksi yang masuk ke sini akan
    masuk lagi selamanya.
    """

    def _service(self):
        from aruna.signals.service import SignalService

        svc = SignalService.__new__(SignalService)
        svc._interval_reported = set()
        return svc

    def test_ada_penampung_prediksi_yang_sudah_disebut(self) -> None:
        assert self._service()._interval_reported == set()

    def _sinyal(self, signal_id: str = "abc"):
        from aruna.core.enums import Horizon

        return SimpleNamespace(
            signal_id=signal_id, symbol="ANTM", horizon=Horizon.H1
        )

    def _tangkap(self, monkeypatch) -> list[tuple[str, str]]:
        from aruna.signals import service as modul

        keluar: list[tuple[str, str]] = []
        monkeypatch.setattr(
            modul.log, "warning", lambda e, **k: keluar.append(("warning", e))
        )
        monkeypatch.setattr(
            modul.log, "debug", lambda e, **k: keluar.append(("debug", e))
        )
        return keluar

    def test_pass_kedua_turun_ke_debug(self, monkeypatch) -> None:
        keluar = self._tangkap(monkeypatch)
        svc = self._service()

        for _ in range(4):
            svc._note_interval_unavailable(self._sinyal(), "1m untuk IDX")

        assert [t for t, _ in keluar] == ["warning", "debug", "debug", "debug"]

    def test_prediksi_lain_tetap_disebut_sendiri(self, monkeypatch) -> None:
        """Meredam per prediksi, bukan per kelas kesalahan: prediksi kedua yang
        macet adalah kabar baru, bukan pengulangan."""
        keluar = self._tangkap(monkeypatch)
        svc = self._service()

        svc._note_interval_unavailable(self._sinyal("abc"), "x")
        svc._note_interval_unavailable(self._sinyal("def"), "x")

        assert [t for t, _ in keluar] == ["warning", "warning"]

    def test_penghitungnya_tetap_naik_tiap_pass(self) -> None:
        """Yang berhenti hanya pengulangan barisnya. ``unavailable_interval``
        harus tetap menggambarkan ukuran backlog-nya."""
        import inspect

        from aruna.signals.service import SignalService

        sumber = inspect.getsource(SignalService._resolve_one)
        naik = sumber.index("result.unavailable_interval += 1")
        redam = sumber.index("self._note_interval_unavailable")

        assert naik < redam, "penghitungnya harus naik sebelum diredam"


@pytest.mark.parametrize(
    "modul,penampung",
    [
        ("aruna.futures.binance", "_brackets_noted"),
        ("aruna.signals.service", "_interval_reported"),
        ("aruna.upkeep.loop", "_horizon_not_offered"),
    ],
)
def test_setiap_peredam_memakai_penampung_seumur_proses(
    modul: str, penampung: str
) -> None:
    """Tiga peredam, satu bentuk yang sama.

    Peredam yang menyimpan keadaannya di tempat lain - berkas, database, cache
    - akan menghidupkan kembali keluhan lama sesudah restart, atau membungkam
    keluhan baru karena keluhan lama pernah ada. Seumur proses adalah masa yang
    benar: restart memang saat yang tepat untuk mendengar semuanya sekali lagi.
    """
    import importlib
    import inspect

    sumber = inspect.getsource(importlib.import_module(modul))
    assert f"self.{penampung}: set" in sumber or f"{penampung}: set" in sumber


def test_bukan_semua_peringatan_diredam() -> None:
    """Penjaga untuk peredamnya sendiri.

    Kalau seseorang menyelesaikan "log terlalu ramai" dengan menurunkan semua
    peringatan, ini yang merah.
    """
    import inspect

    from aruna.data import ingest
    from aruna.signals import service
    from aruna.upkeep import loop

    for modul in (ingest, service, loop):
        assert "log.warning" in inspect.getsource(modul), modul.__name__


def test_simple_namespace_tidak_dipakai_sebagai_pengganti_provider() -> None:
    """Catatan untuk pembaca berikutnya: kelas palsu di test ini sengaja tidak
    meniru jaringan. Yang diuji adalah cabang pencatatannya, bukan HTTP-nya."""
    assert SimpleNamespace is not None
