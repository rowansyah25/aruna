"""Rekam jejak akhirnya terukur di jalur spot (bagian 18.4).

Faktor ``historical`` berbobot tiga - terbesar kedua di antara faktor bernilai -
dan terukur 2026-08-24 ia tidak terukur pada **300 dari 300** snapshot terakhir:
``_score_quality`` mengoper ``accuracy=None, sample=0`` secara harfiah.

Sesudah dirangkai, terukur atas 40 keputusan nyata (20 aset x 2 horizon):
**9 terukur**, dan seluruhnya keputusan BERARAH. Nilainya bervariasi dan
informatif - UNI/USDT SELL memulangkan akurasi 9% dari 34 kasus, yang persis
jenis keterangan yang bagian 18.4 minta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace as NS

from aruna.core.enums import Decision, Horizon, Market
from aruna.memory.korpus import (
    MEMORY_KANDIDAT,
    Korpus,
    PembacaKorpus,
    rekam_jejak,
    serupa,
)
from aruna.memory.outcome import Ringkasan

SAAT = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _ringkasan(**kw: object) -> Ringkasan:
    dasar: dict[str, object] = {
        "total": 100,
        "per_arah": {"LONG": 60, "SHORT": 40},
        "win_rate": {"LONG": 70, "SHORT": None},
        "rentang_similarity": (80, 100),
        "rentang_waktu": None,
        "dinilai": {"LONG": 40, "SHORT": 0},
    }
    dasar.update(kw)
    return Ringkasan(**dasar)  # type: ignore[arg-type]


class TestRekamJejakSatuAturan:
    def test_akurasi_arah_yang_diambil(self) -> None:
        assert rekam_jejak(_ringkasan(), Decision.BUY) == (0.70, 40)

    def test_arah_lain_tidak_dipinjam(self) -> None:
        """Rekam jejak LONG tidak mengatakan apa pun tentang SHORT."""
        assert rekam_jejak(_ringkasan(), Decision.SELL) == (None, 0)

    def test_sampel_yang_DINILAI_bukan_yang_cocok(self) -> None:
        """``per_arah`` ikut menghitung kasus berhasil NEUTRAL. Terukur: satu
        sidik nyata mencocokkan 161 ingatan dan hanya 11 pernah menang atau
        kalah."""
        r = _ringkasan(per_arah={"LONG": 161}, dinilai={"LONG": 11})

        assert rekam_jejak(r, Decision.BUY)[1] == 11

    def test_tak_berarah_tidak_punya_rekam_jejak(self) -> None:
        """Posisi yang tidak diambil tidak punya menang atau kalah."""
        assert rekam_jejak(_ringkasan(), Decision.WAIT) == (None, 0)
        assert rekam_jejak(_ringkasan(), Decision.NO_SIGNAL) == (None, 0)

    def test_gerbang_phase_15_dihormati(self) -> None:
        """Phase 15 menolak mengubah korpus setipis itu menjadi persen."""
        assert rekam_jejak(_ringkasan(total=3), Decision.BUY) == (None, 0)

    def test_kosong_tidak_meledak(self) -> None:
        assert rekam_jejak(None, Decision.BUY) == (None, 0)

    def test_jalur_futures_memakai_aturan_yang_sama(self) -> None:
        """Dua jalur yang menjawab "rekam jejak berapa" dengan dua hitungan
        berbeda akan melaporkan dua angka di bawah satu nama."""
        import inspect

        from aruna.futures import service

        assert "rekam_jejak" in inspect.getsource(service._rekam_jejak)
        assert "memory.korpus" in inspect.getsource(service._rekam_jejak)


class TestPembacaKorpus:
    class _Repo:
        def __init__(self) -> None:
            self.dibaca = 0

        async def hitung_per_timeframe(self, **kw: object) -> dict[str, int]:
            return {"15m": 500}

        async def cari_terhitung(self, **kw: object) -> tuple[list, bool]:
            self.dibaca += 1
            self.terakhir = kw
            return [], False

    async def test_sekali_per_TTL_bukan_sekali_per_sinyal(self) -> None:
        """Yang berbeda per sinyal hanya kemiripannya, dan itu perhitungan
        murni tanpa database. Terukur: kueri 63 ms, `bandingkan` 99 ms - kalau
        kueri ikut per sinyal, enam puluh sinyal per bar membayar 63 ms yang
        jawabannya identik."""
        repo = self._Repo()
        pembaca = PembacaKorpus(repo)

        for _ in range(5):
            await pembaca.baca(
                market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
            )

        assert repo.dibaca == 1

    async def test_ttl_habis_membaca_lagi(self) -> None:
        repo = self._Repo()
        pembaca = PembacaKorpus(repo, ttl_sec=0.0)

        await pembaca.baca(market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT)
        await pembaca.baca(market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT)

        assert repo.dibaca == 2

    async def test_batas_kandidatnya_dipinjam(self) -> None:
        """Batas yang berbeda di dua jalur berarti dua ukuran sampel di bawah
        satu nama "rekam jejak"."""
        repo = self._Repo()
        await PembacaKorpus(repo).baca(
            market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
        )

        assert repo.terakhir["limit"] == MEMORY_KANDIDAT

    async def test_tanpa_repositori_bukan_kegagalan(self) -> None:
        assert await PembacaKorpus(None).baca(
            market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
        ) is None

    async def test_timeframe_tanpa_ingatan_memulangkan_none(self) -> None:
        class _Kosong(TestPembacaKorpus._Repo):
            async def hitung_per_timeframe(self, **kw: object) -> dict:
                return {}

        assert await PembacaKorpus(_Kosong()).baca(
            market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
        ) is None

    async def test_kegagalan_tidak_membekukan_cache(self) -> None:
        """Pembacaan yang gagal tidak boleh membungkam korpus selama lima
        menit - sinyal berikutnya harus boleh mencoba lagi."""

        class _Meledak(TestPembacaKorpus._Repo):
            async def hitung_per_timeframe(self, **kw: object) -> dict:
                self.dibaca += 1
                raise RuntimeError("database berkedip")

        repo = _Meledak()
        pembaca = PembacaKorpus(repo)

        assert await pembaca.baca(
            market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
        ) is None
        assert await pembaca.baca(
            market=Market.CRYPTO, horizon=Horizon.M15, as_of=SAAT
        ) is None
        assert repo.dibaca == 2


class TestKemiripanMemakaiAmbangYangSama:
    def test_ambangnya_dipinjam(self) -> None:
        """"Mirip" harus berarti satu hal di seluruh sistem, atau dua laporan
        tentang ingatan yang sama akan menyebut jumlah kasus yang berbeda."""
        import inspect

        from aruna.memory import korpus

        assert "AMBANG_MIRIP" in inspect.getsource(korpus)

    def test_yang_tidak_mirip_tidak_ikut(self) -> None:
        from aruna.memory.dimensions import Dimensi
        from aruna.memory.fingerprint import Sidik

        sama = Sidik(nilai={Dimensi.REGIME: "RANGING", Dimensi.TREND: "BULLISH"})
        beda = Sidik(nilai={Dimensi.REGIME: "TRENDING", Dimensi.TREND: "BEARISH"})
        korpus = Korpus(
            daftar=(
                NS(sidik=sama, arah="BUY", hasil=NS(name="WIN"), locked_at=SAAT),
            ),
            timeframe="15m",
        )

        assert serupa(korpus, sama).total == 1
        assert serupa(korpus, beda).total == 0


class TestSidikSebentukDenganYangTersimpan:
    """**Regresi yang benar-benar terjadi, 2026-08-24.**

    Versi pertama membangun sidik dari ``DecisionContext``, dan dua dimensi
    rusak diam-diam: ``REGIME`` berisi seluruh repr ``RegimeVerdict(...)``
    sementara ingatan menyimpan ``RANGING``, dan ``RISK_LEVEL``/``NEWS``
    seluruhnya ``UNKNOWN`` karena konteks memang tidak punya bidang itu.

    Yang pertama lebih buruk daripada ``UNKNOWN``: nilai yang terbaca ikut
    penyebut dan **selalu** tidak cocok, jadi kemiripan tiap kasus turun karena
    satu dimensi yang tidak akan pernah bisa sama. Tidak ada satu pun error -
    hanya jumlah kecocokan yang lebih kecil daripada seharusnya.
    """

    def _sidik(self):
        from aruna.signals.service import _sidik_sekarang

        signal = NS(
            symbol="ADA/USDT", market=Market.CRYPTO, regime="RANGING",
            # Bentuk yang sungguhan disimpan `signal_snapshots.news_state` -
            # `band_news` yang menerjemahkannya, dan ia menolak format yang
            # tidak dikenal. Menulis "NO_NEWS" di sini akan menguji ejaan yang
            # tidak pernah dilewati produksi.
            risk_level="MODERATE", news_state="NO_RECENT_NEWS",
            spread_bps=Decimal("4.4553"),
        )
        context = NS(
            value=lambda n: {"realised_volatility": 0.9, "momentum": 1.27,
                             "volume_anomaly": 0.48}.get(n),
            structure=NS(trend="RANGE"),
        )
        return _sidik_sekarang(signal, context, Korpus(daftar=(), timeframe="15m"))

    def test_regime_kata_bukan_objek(self) -> None:
        from aruna.memory.dimensions import Dimensi

        assert self._sidik().nilai[Dimensi.REGIME] == "RANGING"

    def test_risiko_dan_berita_terbaca(self) -> None:
        from aruna.memory.dimensions import Dimensi

        nilai = self._sidik().nilai
        assert nilai[Dimensi.RISK_LEVEL] == "MODERATE"
        assert nilai[Dimensi.NEWS] == "NO_NEWS"

    def test_dimensinya_sama_persis_dengan_ingatan(self) -> None:
        """Sidik yang punya dimensi berbeda dari yang tersimpan membandingkan
        dua hal yang tidak sebanding, dan skornya tetap keluar."""
        from aruna.db.repositories.memory import ingatan_dari_baris

        ingatan = ingatan_dari_baris({
            "signal_id": "x", "symbol": "ADA/USDT", "market_code": "CRYPTO",
            "timeframe": "15m", "arah": "BUY", "hasil": "WIN",
            "move_pct": None, "locked_at": SAAT, "resolved_at": SAAT,
            "model_version": "v", "cakupan": 8, "mutu": "HIGH",
        })

        assert set(self._sidik().nilai) == set(ingatan.sidik.nilai)

    def test_mutu_tetap_tidak_terbaca(self) -> None:
        """QUALITY justru yang sedang dihitung. Mengarangnya membuat sidik jari
        mengandung jawaban dari pertanyaan yang belum ditanyakan."""
        from aruna.memory.dimensions import UNKNOWN, Dimensi

        assert self._sidik().nilai[Dimensi.QUALITY] == UNKNOWN

    def test_timeframe_dari_korpus_bukan_dari_horizon(self) -> None:
        """Phase 15 boleh meminjam timeframe lain; sidik yang tetap mengeja
        horizon aslinya tidak akan cocok dengan satu pun ingatan pinjaman."""
        from aruna.memory.dimensions import Dimensi
        from aruna.signals.service import _sidik_sekarang

        signal = NS(symbol="ADA/USDT", market=Market.CRYPTO, regime="RANGING",
                    risk_level=None, news_state=None, spread_bps=None)
        context = NS(value=lambda n: None, structure=None)
        sidik = _sidik_sekarang(
            signal, context, Korpus(daftar=(), timeframe="1h")
        )

        assert sidik.nilai[Dimensi.TIMEFRAME] == "1h"


class TestBandSatuTempat:
    def test_dua_jalur_memetakan_angka_yang_sama_ke_band_yang_sama(self) -> None:
        """Kalau jalur spot memetakan sendiri, dua ingatan dengan volatilitas
        yang sama bisa tercatat LOW di satu jalur dan MEDIUM di jalur lain."""
        import inspect

        from aruna.memory import teknikal

        assert "dimensi_dari_bacaan" in inspect.getsource(
            teknikal.dimensi_teknikal
        )

    def test_bacaan_kosong_seluruhnya_tidak_terukur(self) -> None:
        from aruna.memory.dimensions import UNKNOWN
        from aruna.memory.teknikal import dimensi_dari_bacaan

        assert set(dimensi_dari_bacaan().values()) == {UNKNOWN}


class TestTerpasangDiJalurHidup:
    async def test_service_menanyakan_korpusnya(self) -> None:
        """Ditulis dan diuji tidak sama dengan dipanggil."""
        from aruna.signals.service import SignalService

        class _Pembaca:
            def __init__(self) -> None:
                self.diminta = 0

            async def baca(self, **kw: object) -> Korpus:
                self.diminta += 1
                return Korpus(daftar=(), timeframe="15m")

        pembaca = _Pembaca()
        svc = object.__new__(SignalService)
        svc._korpus = pembaca

        hasil = await svc._rekam_jejak(
            NS(symbol="ADA/USDT"),
            NS(market=Market.CRYPTO, as_of=SAAT),
            NS(direction=Decision.BUY),
            Horizon.M15,
        )

        assert pembaca.diminta == 1
        assert hasil == {"accuracy": None, "sample": 0}

    async def test_tanpa_pembaca_bukan_kegagalan(self) -> None:
        from aruna.signals.service import SignalService

        svc = object.__new__(SignalService)
        svc._korpus = None

        assert await svc._rekam_jejak(
            NS(symbol="X"), NS(), NS(direction=Decision.BUY), Horizon.M15
        ) == {"accuracy": None, "sample": 0}

    async def test_korpus_gagal_tidak_menghapus_prediksi(self) -> None:
        """Rekam jejak adalah satu faktor di antara dua puluh; kegagalan
        membacanya tidak boleh menghapus prediksinya."""
        from aruna.signals.service import SignalService

        class _Meledak:
            async def baca(self, **kw: object) -> Korpus:
                raise RuntimeError("database berkedip")

        svc = object.__new__(SignalService)
        svc._korpus = _Meledak()

        assert await svc._rekam_jejak(
            NS(symbol="X"), NS(market=Market.CRYPTO, as_of=SAAT),
            NS(direction=Decision.BUY), Horizon.M15,
        ) == {"accuracy": None, "sample": 0}

    def test_score_quality_mengopernya(self) -> None:
        import inspect

        from aruna.signals.service import SignalService

        assert "_rekam_jejak" in inspect.getsource(SignalService._score_quality)

    def test_app_merangkai_pembacanya(self) -> None:
        """Bidang opsional yang tak pernah diisi adalah celah yang sama."""
        import inspect

        from aruna import app

        assert "korpus=self.korpus" in inspect.getsource(app)
