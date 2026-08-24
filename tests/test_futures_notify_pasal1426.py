"""Pesan futures yang hidup, dilengkapi menurut PASAL 14.26.

Tiga bagian yang dulu hilang dari pesan yang benar-benar sampai ke operator:
rezim pasar, blok INVALIDATION, dan kaki ANALYST ONLY. Ketiganya disusun dari
data yang **sudah ada** - tidak satu pun angka baru dikarang di sini.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from aruna.decision.output import KAKI
from aruna.futures.debate import CouncilNote
from aruna.futures.notify import _alert, _invalidation
from aruna.futures.service import _regime_name, attach_regime
from aruna.notify.verdict import VoteSplit

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Nilai:
    value: str


@dataclass(frozen=True)
class _Rezim:
    regime: _Nilai


@dataclass(frozen=True)
class _Konteks:
    regime: _Rezim | None


def _konteks(nama: str | None) -> _Konteks:
    return _Konteks(_Rezim(_Nilai(nama)) if nama else None)


@dataclass(frozen=True)
class FakeSide:
    value: str


@dataclass(frozen=True)
class FakeStop:
    """Bentuknya meniru ``aruna.futures.stops.StopLoss`` yang sungguhan.

    ``price`` adalah harganya; ``invalidation`` adalah KALIMAT yang menjelaskan
    kenapa stopnya di situ. Palsu yang menukar keduanya membuat test lulus di
    atas asumsi yang salah - dan itu persis yang terjadi: bloknya membaca
    kalimat sebagai harga, dan produksi mencatat empat ERROR sebelum ada yang
    melihatnya.
    """

    price: Decimal | None
    invalidation: str = ""


@dataclass(frozen=True)
class FakePlan:
    symbol: str = "BTCUSDT"
    side: FakeSide = FakeSide("LONG")
    horizon_hours: float = 0.25
    #: Harga saat rencana disusun. Ada di ``FuturesPlan`` yang sungguhan
    #: (``src/aruna/futures/plan.py:146``) dan dipakai menghitung waktu masuk.
    reference_price: Decimal | None = Decimal("64120")
    entry: Decimal = Decimal("64120")
    stop: Decimal | None = Decimal("63780")
    target: Decimal = Decimal("64950")
    leverage: int = 3
    quantity: Decimal = Decimal("0.01")
    net_rr: Decimal = Decimal("2.44")
    tick_size: Decimal | None = None
    liquidation: Any = None
    buffer: Any = None
    caveats: tuple[str, ...] = ()
    margin_mode: FakeSide = FakeSide("ISOLATED")
    stop_detail: FakeStop | None = FakeStop(
        Decimal("63780"), "1.5 ATR melawan posisi, tanpa level struktur"
    )


def note(**kw) -> CouncilNote:
    dasar = {
        "symbol": "BTCUSDT",
        "confidence": 0.87,
        "disagreement": 0.2,
        "split": VoteSplit(setuju=(), kontra=(), abstain=()),
    }
    return CouncilNote(**(dasar | kw))


class TestRezimPasar:
    def test_rezim_dicetak_kalau_terbaca(self) -> None:
        """Operator melihat entry, stop, dan target tanpa tahu pasar macam apa
        yang menghasilkannya."""
        teks = _alert(FakePlan(), NOW, note=note(regime="TRENDING UP"))

        assert "REZIM PASAR:" in teks
        assert "TRENDING UP" in teks

    def test_rezim_kosong_tidak_dicetak_sebagai_unknown(self) -> None:
        """UNCERTAIN adalah nama rezim yang sungguhan di sistem ini. Rezim yang
        mengaku tidak diketahui terbaca hampir sama dengannya, dan keduanya
        berarti hal yang sangat berbeda."""
        teks = _alert(FakePlan(), NOW, note=note())

        assert "REZIM PASAR" not in teks
        assert "UNKNOWN" not in teks

    def test_tanpa_catatan_council_pesannya_tetap_terkirim(self) -> None:
        teks = _alert(FakePlan(), NOW, note=None)

        assert "64120" in teks


class TestPenyambunganRezim:
    """Penyambungannya sendiri, bukan hanya pembacanya.

    Ini keluarga cacat yang paling sering muncul di sistem ini: kode yang
    ditulis, diekspor, diuji, dan tidak pernah dilewati jalur hidup.
    """

    def test_rezim_menempel_ke_catatan_council(self) -> None:
        assert attach_regime(note(), _konteks("TRENDING UP")).regime == "TRENDING UP"

    def test_konteks_tanpa_rezim_meninggalkan_catatan_apa_adanya(self) -> None:
        assert attach_regime(note(), _konteks(None)).regime == ""

    def test_konteks_rusak_tidak_menghentikan_rencana(self) -> None:
        """Satu bidang rezim yang bentuknya tak terduga tidak boleh
        menghentikan rencana yang membawa angka keputusan."""

        class Meledak:
            @property
            def regime(self):
                raise RuntimeError("konteks rusak")

        assert attach_regime(note(), Meledak()).regime == ""


class TestDecisionScoreDiPesan:
    """PASAL 14.16 di jalur hidup - sebagai keterangan, bukan gerbang.

    **Labelnya pindah pada 2026-08-24, angkanya tidak.** Bagian 18.17 menuntut
    tujuh keyakinan disebut terpisah, dan Decision Score adalah yang kedua di
    daftar itu. Mencetaknya sekali di barisnya sendiri DAN sekali lagi di dalam
    blok keyakinan akan menampilkan angka yang sama dua kali dengan dua nama.
    """

    #: Komponen berarah penuh: cukup untuk melewati ambang cakupan.
    PENUH: ClassVar[dict[str, float]] = {
        "trend": 1.0, "structure": 1.0, "momentum": 1.0,
        "volume": 1.0, "agreement": 1.0,
    }

    def test_skor_dicetak_kalau_bisa_dinilai(self) -> None:
        teks = _alert(FakePlan(), NOW, note=note(decision_readings=self.PENUH))

        assert "Decision Confidence" in teks
        assert "TIDAK TERUKUR" not in teks.split("Decision Confidence")[1][:40]

    def test_angkanya_tidak_pernah_berdiri_sebagai_persen(self) -> None:
        """PASAL 14.16: skor bukan probabilitas profit."""
        teks = _alert(FakePlan(), NOW, note=note(decision_readings=self.PENUH))

        assert "bukan peluang profit" in teks
        assert "dari 81" in teks

    def test_tanpa_bacaan_angkanya_tidak_dikarang(self) -> None:
        """Barisnya tetap ada, angkanya tidak.

        Bentuk pertama aturan ini menghilangkan seluruh barisnya, dan itu benar
        selama ia berdiri sendiri: satu baris "tidak bisa dinilai" di antara
        angka keputusan tidak menambah apa pun. Di dalam blok tujuh keyakinan
        ia berhenti benar - baris yang hilang dari daftar bernomor membuat
        lapisan yang mati tidak bisa dibedakan dari lapisan yang tidak pernah
        ada (bagian 18.17).

        Yang tetap dijaga adalah yang sebenarnya penting: tidak ada angka yang
        dikarang untuk mengisi tempatnya.
        """
        teks = _alert(FakePlan(), NOW, note=note())

        assert "Decision Confidence" in teks
        assert "bukan peluang profit" not in teks

    def test_bacaan_tipis_tidak_dicetak(self, monkeypatch) -> None:
        """Dan tidak lewat jalan memutar lewat pengecualian.

        Tanpa penjaga cakupan, ``s.value`` yang ``None`` akan meledak di
        pemformatan dan tertangkap penjaga luar - hasilnya sama-sama kosong,
        tapi setiap rencana bercakupan tipis meninggalkan jejak pengecualian di
        log. Log yang penuh alarm palsu adalah log yang berhenti dibaca.
        """
        from aruna.futures import notify as modul

        dicatat: list[str] = []
        monkeypatch.setattr(
            modul,
            "log",
            SimpleNamespace(
                exception=lambda nama, **kw: dicatat.append(nama),
                warning=lambda nama, **kw: None,
            ),
        )

        teks = _alert(
            FakePlan(), NOW, note=note(decision_readings={"trend": 1.0})
        )

        assert "DECISION SCORE" not in teks
        assert "futures.decision_score_failed" not in dicatat

    def test_skor_tidak_menahan_pengiriman(self) -> None:
        """Terukur: kasus paling sering di produksi berhenti di 52% cakupan.
        Menjadikan skor ini syarat kirim akan membungkam ARUNA hampir
        sepenuhnya."""
        teks = _alert(
            FakePlan(), NOW, note=note(decision_readings={"trend": -1.0})
        )

        assert "64120" in teks
        assert "SIDE:" in teks


class TestPenyambungDipanggilJalurHidup:
    """Bukti bahwa ``_plan_one`` benar-benar memanggilnya.

    Tanpa test ini, seluruh penyambungan rezim bisa dihapus dari jalur hidup
    dan setiap unit test tetap hijau - yang persis bagaimana bagian PENILAIAN
    pernah hilang dari pesan tanpa error dan tanpa log.
    """

    class _Berhenti(Exception):
        """Menghentikan ``_plan_one`` tepat sesudah baris yang diuji."""

    @pytest.mark.asyncio
    async def test_attach_regime_dipanggil(self, monkeypatch) -> None:
        import asyncio

        from aruna.core.enums import Decision
        from aruna.futures import service as modul
        from aruna.futures.service import FuturesPlanService

        dicatat: dict = {}

        def _tangkap(catatan, konteks, *, symbol=""):
            dicatat["symbol"] = symbol
            raise self._Berhenti

        monkeypatch.setattr(modul, "attach_regime", _tangkap)
        monkeypatch.setattr(
            modul, "attach_decision_readings", lambda *a, **k: None
        )

        verdict = SimpleNamespace(
            symbol="BTC/USDT", interval="4h",
            decision=SimpleNamespace(value=Decision.BUY.value),
            confidence=0.6, opinions=(),
            protest=SimpleNamespace(objections=(), rebuttals=(), disagreement=0.1),
            veto=SimpleNamespace(vetoes=(), upheld=(), reviews=()),
            judgement=SimpleNamespace(minority_prevailed=False),
        )

        svc = FuturesPlanService.__new__(FuturesPlanService)
        svc._council = SimpleNamespace(convene=lambda ctx: verdict)
        svc._council_store = None
        # Bagian 16.2: jalur ini sekarang menyimpan funding dan open interest.
        svc._metrik = None
        svc._deliberation = SimpleNamespace(
            build_context=lambda *a, **k: asyncio.sleep(
                0, SimpleNamespace(as_of=None)
            )
        )
        svc._resolve_asset = lambda symbol: asyncio.sleep(
            0, SimpleNamespace(id=1, symbol=symbol)
        )
        provider = SimpleNamespace(
            snapshot=lambda symbol: asyncio.sleep(0, SimpleNamespace(symbol=symbol))
        )

        with pytest.raises(self._Berhenti):
            await svc._plan_one(
                provider, "BTCUSDT",
                horizon=SimpleNamespace(value="4h"),
                equity=Decimal("10000"),
                risk_pct=None,
                now=NOW,
            )

        assert dicatat["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_attach_decision_readings_dipanggil(self, monkeypatch) -> None:
        """Penyambungan kedua, diuji dengan cara yang sama - dan karena alasan
        yang sama: kode yang tidak pernah dilewati jalur hidup."""
        import asyncio

        from aruna.core.enums import Decision
        from aruna.futures import service as modul
        from aruna.futures.service import FuturesPlanService

        dicatat: dict = {}

        def _tangkap(catatan, konteks, keputusan, *, symbol=""):
            dicatat["symbol"] = symbol
            raise self._Berhenti

        monkeypatch.setattr(modul, "attach_decision_readings", _tangkap)

        verdict = SimpleNamespace(
            symbol="BTC/USDT", interval="4h",
            decision=SimpleNamespace(value=Decision.BUY.value),
            confidence=0.6, opinions=(),
            protest=SimpleNamespace(objections=(), rebuttals=(), disagreement=0.1),
            veto=SimpleNamespace(vetoes=(), upheld=(), reviews=()),
            judgement=SimpleNamespace(minority_prevailed=False),
        )

        svc = FuturesPlanService.__new__(FuturesPlanService)
        svc._council = SimpleNamespace(convene=lambda ctx: verdict)
        svc._council_store = None
        # Bagian 16.2: jalur ini sekarang menyimpan funding dan open interest.
        svc._metrik = None
        svc._deliberation = SimpleNamespace(
            build_context=lambda *a, **k: asyncio.sleep(
                0, SimpleNamespace(as_of=None)
            )
        )
        svc._resolve_asset = lambda symbol: asyncio.sleep(
            0, SimpleNamespace(id=1, symbol=symbol)
        )
        provider = SimpleNamespace(
            snapshot=lambda symbol: asyncio.sleep(0, SimpleNamespace(symbol=symbol))
        )

        with pytest.raises(self._Berhenti):
            await svc._plan_one(
                provider, "BTCUSDT",
                horizon=SimpleNamespace(value="4h"),
                equity=Decimal("10000"),
                risk_pct=None,
                now=NOW,
            )

        assert dicatat["symbol"] == "BTCUSDT"


class TestPembacaRezim:
    def test_membaca_nilai_enumnya(self) -> None:
        @dataclass(frozen=True)
        class Nilai:
            value: str

        @dataclass(frozen=True)
        class Rezim:
            regime: Nilai

        @dataclass(frozen=True)
        class Konteks:
            regime: Rezim | None

        assert _regime_name(Konteks(Rezim(Nilai("TRENDING UP")))) == "TRENDING UP"

    def test_tanpa_rezim_mengembalikan_kosong(self) -> None:
        @dataclass(frozen=True)
        class Konteks:
            regime: None = None

        assert _regime_name(Konteks()) == ""

    def test_konteks_tanpa_bidang_rezim_tidak_meledak(self) -> None:
        assert _regime_name(object()) == ""


class TestBlokInvalidation:
    def test_level_dan_timeframenya_dicetak(self) -> None:
        """PASAL 14.26: invalidation dan stop loss adalah angka yang sama di
        contohnya, 63.780."""
        teks = _alert(FakePlan(), NOW, note=note())

        assert "INVALIDATION" in teks
        assert "15m" in teks
        assert "63,780" in teks

    def test_long_dibatalkan_oleh_penutupan_di_bawah(self) -> None:
        baris = "\n".join(_invalidation(FakePlan()))

        assert "tutup di bawah" in baris

    def test_short_dibatalkan_oleh_penutupan_di_atas(self) -> None:
        """Salah tanda di sini berarti signal yang sedang salah tidak pernah
        dibatalkan."""
        baris = "\n".join(_invalidation(FakePlan(side=FakeSide("SHORT"))))

        assert "tutup di atas" in baris

    def test_kalimatnya_dibawa_sebagai_catatan_bukan_dibaca_sebagai_harga(
        self,
    ) -> None:
        """``StopLoss.invalidation`` adalah KALIMAT, ``StopLoss.price`` harganya.

        Membaca yang pertama sebagai level memberi ``TypeError`` di dalam
        ``Ambang`` - dan penjaga luar menelannya, jadi yang tampak di produksi
        cuma blok INVALIDATION yang senyap dan empat baris ERROR.
        """
        rencana = FakePlan(
            stop_detail=FakeStop(
                Decimal("63780"),
                "1.5 ATR melawan posisi, tanpa level struktur untuk sandaran",
            )
        )

        baris = "\n".join(_invalidation(rencana))

        assert "63,780" in baris
        assert "1.5 ATR melawan posisi" in baris

    def test_kalimat_tanpa_harga_tetap_bukan_level(self) -> None:
        """Kalau harganya benar-benar tidak ada, kalimatnya tidak boleh naik
        pangkat menjadi angka pengganti."""
        rencana = FakePlan(stop=None, stop_detail=FakeStop(None, "murni ATR"))

        assert _invalidation(rencana) == []

    def test_tanpa_level_tidak_ada_bloknya(self) -> None:
        """Stop rencana ikut kosong: kalau ``stop_detail`` tidak membawa harga,
        stop rencana memang level pembatalnya, dan memakainya benar."""
        assert _invalidation(FakePlan(stop=None, stop_detail=FakeStop(None))) == []
        assert _invalidation(FakePlan(stop=None, stop_detail=None)) == []

    def test_stop_rencana_dipakai_kalau_rinciannya_tidak_membawa_harga(self) -> None:
        """Kebalikannya juga salah: memulangkan blok kosong padahal rencananya
        punya stop akan menghapus keterangan yang datanya ada."""
        baris = "\n".join(_invalidation(FakePlan(stop_detail=FakeStop(None))))

        assert "63,780" in baris

    def test_tanpa_level_bukan_kesalahan(self, monkeypatch) -> None:
        """Rencana yang stopnya murni volatilitas tidak punya level struktur,
        dan itu keadaan wajar. Tanpa penjaga di depan, setiap rencana seperti
        itu akan menuliskan jejak pengecualian - dan log yang penuh alarm palsu
        adalah log yang berhenti dibaca.

        Logger-nya diganti, bukan dibaca lewat ``caplog``: log sistem ini lewat
        structlog dan tidak selalu diteruskan ke logging bawaan, jadi
        ``caplog`` yang kosong tidak membuktikan apa-apa.
        """
        from aruna.futures import notify as modul

        dicatat: list[str] = []
        monkeypatch.setattr(
            modul,
            "log",
            SimpleNamespace(
                exception=lambda nama, **kw: dicatat.append(nama),
                warning=lambda nama, **kw: None,
            ),
        )

        assert _invalidation(FakePlan(stop=None, stop_detail=FakeStop(None))) == []
        assert dicatat == []

    def test_kegagalannya_tidak_menghentikan_pesan(self) -> None:
        """Yang hilang saat ia gagal adalah satu blok keterangan - bukan pesan
        yang membawa entry dan stop."""
        rusak = FakePlan(
            side=FakeSide("ARAH TIDAK DIKENAL"),
            stop_detail=FakeStop(Decimal("63780")),
        )

        assert _invalidation(rusak) == []
        assert "64120" in _alert(rusak, NOW, note=note())


class TestKakiAnalystOnly:
    def test_kaki_selalu_ada(self) -> None:
        """Pesan berisi entry, stop, dan leverage yang terpotong sebelum
        paragraf penjelas sampai terbaca sebagai perintah."""
        teks = _alert(FakePlan(), NOW, note=note())

        for baris in KAKI:
            assert baris in teks

    def test_kakinya_di_akhir_pesan(self) -> None:
        teks = _alert(FakePlan(), NOW, note=note())

        assert teks.rstrip().endswith(KAKI[-1])

    def test_paragraf_penjelasnya_tetap_ada(self) -> None:
        """Kaki dua baris menambah, bukan menggantikan: yang satu selamat dari
        pembacaan sekilas, yang lain menjelaskan."""
        teks = _alert(FakePlan(), NOW, note=note())

        assert "memasang order" in teks
        assert "keputusan Anda" in teks

    def test_penanda_uji_coba_tetap_paling_akhir(self) -> None:
        """Penanda TEST harus tetap yang terlihat di layar kunci."""
        teks = _alert(FakePlan(), NOW, note=note(), test_mode=True)

        assert teks.rstrip().endswith("TEST")


class TestTidakMerusakYangSudahAda:
    def test_angka_keputusan_tetap_lengkap(self) -> None:
        teks = _alert(FakePlan(), NOW, note=note(regime="TRENDING UP"))

        for angka in ("64120", "63780", "64950", "SIDE:", "TIMEFRAME:", "15m"):
            assert angka in teks

    def test_risk_readings_masih_lewat_jalur_yang_sama(self) -> None:
        n = replace(note(regime="CHOPPY"), risk_readings={"volatility": 70.0})
        teks = _alert(FakePlan(), NOW, note=n)

        assert "RISIKO:" in teks
        assert "CHOPPY" in teks
