"""Gerbang XAU, dan bukti bahwa tiap penolakan membawa angka penyebabnya.

Dua lapis, dan bedanya disengaja:

* :class:`TestGerbang` berdiri langsung di atas ``putuskan`` sehingga setiap
  cabang penolakan benar-benar dijalankan. Lewat mesin dewan itu mustahil -
  diukur lewat probe, dewan SELALU mengembalikan WAIT atas data sintetis
  (sebagian besar agen abstain karena XAU tak punya volume, berita, maupun
  fundamental), jadi tujuh dari delapan gerbang tak akan pernah tersentuh.

* :class:`TestJalurProduksi` berdiri di atas ``putuskan_dari_dewan`` dengan
  ``Deliberation`` sungguhan, supaya perangkaiannya tidak jadi kode yang diuji
  tapi tak pernah dirangkai.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.agents.deliberation import DeliberationEngine
from aruna.core.enums import AgentRole, DataQuality, Decision, Horizon, Market
from aruna.data.models import Candle, Provenance, Snapshot
from aruna.xau.bukti import rakit_bukti
from aruna.xau.cooldown import Cooldown
from aruna.xau.geometri import Geometri
from aruna.xau.kalender import Dampak, PeristiwaEkonomi, ringkas
from aruna.xau.keputusan import (
    MAX_KONTRADIKSI,
    MIN_RR,
    putuskan,
    putuskan_dari_dewan,
    setup_id_untuk,
)
from aruna.xau.konteks import rakit_konteks
from aruna.xau.suara import RekapSuara, Suara, SuaraAgen
from aruna.xau.timeframes import rakit_tumpukan

SAAT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
PROV = Provenance(source="twelvedata")


def _rekap_bagus() -> RekapSuara:
    """Rincian memakai bentuk yang sama dengan produksi.

    Tuple `(role, suara)` yang lebih ringkas akan lolos seluruh test di berkas
    ini - tak ada yang mengiterasinya - lalu meledak di repositori yang membaca
    `.decision`. Palsu yang bidangnya menyimpang dari yang asli menghijaukan
    suite di atas bug produksi.
    """
    return RekapSuara(
        setuju=4,
        menentang=0,
        netral=5,
        rincian=(
            SuaraAgen(AgentRole.TECHNICAL, Suara.AGREE, Decision.BUY, 0.8, False),
        ),
    )


def _geometri(*, entry="1000", stop="993", target="1020", atr="4.0") -> Geometri:
    """RR = 20/7 = 2,86 dan target 5 ATR: lolos kedua gerbang."""
    return Geometri(
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        atr=Decimal(atr),
        sentuhan_target=5,
    )


def _putuskan(**kw):
    bawaan = dict(
        symbol="XAU/USD",
        arah=Decision.BUY,
        confidence=0.7,
        rekap_suara=_rekap_bagus(),
        geometri=_geometri(),
        saat=SAAT,
    )
    return putuskan(**{**bawaan, **kw})


class TestGerbang:
    def test_semua_lolos_menghasilkan_arah(self) -> None:
        sinyal = _putuskan()
        assert sinyal.keputusan is Decision.BUY
        assert sinyal.alasan is None
        assert sinyal.ada_sinyal is True

    def test_sell_juga_bisa_terbit(self) -> None:
        sinyal = _putuskan(
            arah=Decision.SELL,
            geometri=_geometri(entry="1000", stop="1007", target="980"),
        )
        assert sinyal.keputusan is Decision.SELL

    def test_dewan_tak_berarah_ditolak(self) -> None:
        sinyal = _putuskan(arah=Decision.NO_SIGNAL)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "tidak mengusulkan arah" in sinyal.alasan

    def test_tidak_ada_yang_bersuara_ditolak(self) -> None:
        """Kontradiksi None berarti tidak terukur, bukan nol."""
        sepi = RekapSuara(setuju=0, menentang=0, netral=9, rincian=())
        sinyal = _putuskan(rekap_suara=sepi)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "tidak ada agen yang mengambil arah" in sinyal.alasan

    def test_kontradiksi_tinggi_ditolak(self) -> None:
        terbelah = RekapSuara(setuju=2, menentang=2, netral=5, rincian=())
        sinyal = _putuskan(rekap_suara=terbelah)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "kontradiksi" in sinyal.alasan

    def test_kontradiksi_tepat_di_ambang_lolos(self) -> None:
        """Ambangnya inklusif; tiga setuju satu menentang = 0,5."""
        rekap = RekapSuara(setuju=3, menentang=1, netral=5, rincian=())
        assert rekap.kontradiksi == MAX_KONTRADIKSI
        assert _putuskan(rekap_suara=rekap).keputusan is Decision.BUY

    def test_tanpa_geometri_ditolak(self) -> None:
        sinyal = _putuskan(geometri=None)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "tak diketahui" in sinyal.alasan

    def test_target_di_bawah_lantai_dua_atr_ditolak(self) -> None:
        dekat = _geometri(entry="1000", stop="994", target="1003", atr="4.0")
        assert dekat.target_atr < Decimal("2.0")
        sinyal = _putuskan(geometri=dekat)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "lantai" in sinyal.alasan

    def test_rr_rendah_ditolak(self) -> None:
        """Target jauh dalam ATR tapi stop lebih jauh lagi."""
        jelek = _geometri(entry="1000", stop="980", target="1012", atr="4.0")
        assert jelek.target_atr >= Decimal("2.0")
        assert jelek.rr < MIN_RR
        sinyal = _putuskan(geometri=jelek)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "RR" in sinyal.alasan


class TestPenolakanMembawaAngkanya:
    def test_penolakan_kontradiksi_menyimpan_rekapnya(self) -> None:
        """NO SIGNAL yang cuma berbunyi 'ditolak' tak bisa dibantah."""
        terbelah = RekapSuara(setuju=2, menentang=2, netral=5, rincian=())
        sinyal = _putuskan(rekap_suara=terbelah)
        assert sinyal.rekap is terbelah
        assert "2 setuju, 2 menentang" in sinyal.alasan

    def test_penolakan_rr_menyimpan_geometrinya(self) -> None:
        jelek = _geometri(entry="1000", stop="980", target="1012", atr="4.0")
        sinyal = _putuskan(geometri=jelek)
        assert sinyal.geometri is jelek
        assert f"{jelek.rr:.2f}" in sinyal.alasan

    def test_confidence_tersimpan_walau_ditolak(self) -> None:
        sinyal = _putuskan(arah=Decision.NO_SIGNAL, confidence=0.42)
        assert sinyal.confidence == 0.42


class TestSpreadTidakAktif:
    def test_spread_tak_terukur_dinyatakan_bukan_diloloskan(self) -> None:
        """Gerbang yang selalu lolos lebih buruk daripada tidak ada gerbang."""
        assert _putuskan(spread_bps=None).spread_diukur is False

    def test_spread_terukur_dinyatakan(self) -> None:
        assert _putuskan(spread_bps=Decimal("2.2")).spread_diukur is True

    def test_spread_tak_terukur_tidak_memblokir(self) -> None:
        """Tidak diukur bukan alasan menolak - itu keterangan, bukan putusan."""
        assert _putuskan(spread_bps=None).keputusan is Decision.BUY


class TestCooldown:
    def test_setup_sama_ditahan(self) -> None:
        cd = Cooldown()
        assert _putuskan(cooldown=cd).keputusan is Decision.BUY
        kedua = _putuskan(cooldown=cd, saat=SAAT + timedelta(minutes=5))
        assert kedua.keputusan is Decision.NO_SIGNAL
        assert "baru dikabarkan" in kedua.alasan

    def test_setelah_jeda_boleh_lagi(self) -> None:
        cd = Cooldown()
        _putuskan(cooldown=cd)
        lagi = _putuskan(cooldown=cd, saat=SAAT + timedelta(hours=2))
        assert lagi.keputusan is Decision.BUY

    def test_setup_berbeda_tidak_ikut_ditahan(self) -> None:
        """Level target berganti berarti gagasan baru, dan boleh bicara."""
        cd = Cooldown()
        _putuskan(cooldown=cd)
        lain = _putuskan(
            cooldown=cd,
            geometri=_geometri(target="1040"),
            saat=SAAT + timedelta(minutes=5),
        )
        assert lain.keputusan is Decision.BUY

    def test_penolakan_tidak_dicatat_ke_cooldown(self) -> None:
        """Satu NO SIGNAL tidak boleh membungkam sinyal sungguhan berikutnya."""
        cd = Cooldown()
        terbelah = RekapSuara(setuju=2, menentang=2, netral=5, rincian=())
        _putuskan(cooldown=cd, rekap_suara=terbelah)
        menyusul = _putuskan(cooldown=cd, saat=SAAT + timedelta(minutes=5))
        assert menyusul.keputusan is Decision.BUY


class TestSetupIdMenahanGagasanYangSama:
    """Cacat yang merugikan operator sungguhan, 2026-08-28.

    Tiga SELL terbit dalam 45 menit untuk gagasan yang SAMA - target 4595,80
    lalu 4597,11 lalu 4597,51 - dan ketiganya kena stop. Penandanya berbeda
    cuma karena level struktur bergeser sepersekian poin tiap bar, jadi
    cooldown melihat tiga gagasan dan menahan nol.
    """

    def test_ketiga_target_asli_dianggap_satu_gagasan(self) -> None:
        """Angka aslinya dari kerugian 2026-08-28, bukan karangan."""
        cd = Cooldown()
        atr = Decimal("4")
        cd.catat("SELL", Decimal("4595.80"), SAAT)
        for target in (Decimal("4597.11"), Decimal("4597.51")):
            assert cd.tertahan("SELL", target, SAAT + timedelta(minutes=40), atr), (
                f"target {target} dianggap gagasan baru; cooldown menahan nol"
            )

    def test_batas_ember_tidak_lagi_meloloskan(self) -> None:
        """Membulatkan ke ember memindahkan batasnya, tidak menghapusnya.

        4597,11 dan 4597,51 jatuh di sisi berlawanan garis pembagi saat
        dibulatkan - dan itu sebabnya cooldown membandingkan JARAK.
        """
        cd = Cooldown()
        cd.catat("SELL", Decimal("4597.11"), SAAT)
        assert cd.tertahan(
            "SELL", Decimal("4597.51"), SAAT + timedelta(minutes=5), Decimal("4")
        )

    def test_gagasan_yang_benar_benar_jauh_tidak_tertahan(self) -> None:
        """Penjaganya tidak boleh melebar sampai gagasan berbeda ikut tertelan."""
        cd = Cooldown()
        cd.catat("SELL", Decimal("4595"), SAAT)
        assert not cd.tertahan(
            "SELL", Decimal("4560"), SAAT + timedelta(minutes=5), Decimal("4")
        )

    def test_arah_berlawanan_tidak_tertahan(self) -> None:
        cd = Cooldown()
        cd.catat("SELL", Decimal("4595"), SAAT)
        assert not cd.tertahan(
            "BUY", Decimal("4595"), SAAT + timedelta(minutes=5), Decimal("4")
        )

    def test_cooldown_menahan_sinyal_kedua_yang_levelnya_bergeser(self) -> None:
        """Ujung ke ujung: inilah yang seharusnya terjadi kemarin."""
        cd = Cooldown()
        pertama = _putuskan(
            arah=Decision.SELL,
            cooldown=cd,
            geometri=_geometri(
                entry="4605.22", stop="4611.21", target="4595.80", atr="4.0"
            ),
        )
        kedua = _putuskan(
            arah=Decision.SELL,
            cooldown=cd,
            saat=SAAT + timedelta(minutes=40),
            geometri=_geometri(
                entry="4606.70", stop="4612.02", target="4597.11", atr="4.0"
            ),
        )
        assert pertama.keputusan is Decision.SELL
        assert kedua.keputusan is Decision.NO_SIGNAL
        assert "baru dikabarkan" in kedua.alasan

    def test_tanpa_atr_hanya_target_identik_yang_tertahan(self) -> None:
        """ATR nol tidak boleh membuat seluruh level dianggap satu gagasan."""
        cd = Cooldown()
        cd.catat("SELL", Decimal("4595.80"), SAAT)
        saat = SAAT + timedelta(minutes=5)
        assert cd.tertahan("SELL", Decimal("4595.80"), saat, None)
        assert not cd.tertahan("SELL", Decimal("4560.00"), saat, None)


class TestSetupId:
    def test_tidak_memuat_waktu(self) -> None:
        """Penanda yang memuat waktu akan berbeda tiap bar dan tak pernah menahan."""
        satu = setup_id_untuk("XAU/USD", Decision.BUY, Decimal("1020"))
        dua = setup_id_untuk("XAU/USD", Decision.BUY, Decimal("1020"))
        assert satu == dua

    def test_arah_membedakan(self) -> None:
        assert setup_id_untuk("XAU/USD", Decision.BUY, Decimal("1020")) != (
            setup_id_untuk("XAU/USD", Decision.SELL, Decimal("1020"))
        )

    def test_level_membedakan(self) -> None:
        assert setup_id_untuk("XAU/USD", Decision.BUY, Decimal("1020")) != (
            setup_id_untuk("XAU/USD", Decision.BUY, Decimal("1040"))
        )


class TestJalurProduksi:
    """Perangkainya, dengan Deliberation sungguhan."""

    @pytest.fixture
    def dewan(self):
        candles = []
        for i in range(250):
            buka = AWAL + timedelta(minutes=5 * i)
            h = Decimal(str(round(1000 + 30 * math.sin(i / 8), 2)))
            candles.append(
                Candle(
                    market=Market.FOREX,
                    symbol="XAU/USD",
                    interval=Horizon.M5,
                    open_time=buka,
                    close_time=buka + timedelta(minutes=5),
                    open=h,
                    high=h + 2,
                    low=h - 2,
                    close=h,
                    volume=Decimal(0),
                    provenance=PROV,
                    is_closed=True,
                )
            )
        bukti = rakit_bukti(rakit_tumpukan(candles))
        snap = Snapshot(
            market=Market.FOREX,
            symbol="XAU/USD",
            captured_at=candles[-1].close_time,
            last_price=candles[-1].close,
            provenance=PROV,
            quality=DataQuality.OK,
        )
        ctx = rakit_konteks(bukti, snap)
        return DeliberationEngine().deliberate(ctx), bukti, candles[-1].close

    def test_dewan_wait_jadi_no_signal_bukan_wait(self, dewan) -> None:
        """Kosakata XAU tidak pernah memuat WAIT."""
        deliberation, bukti, harga = dewan
        assert deliberation.outcome is Decision.WAIT
        sinyal = putuskan_dari_dewan(deliberation, bukti, harga)
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "tidak mengusulkan arah" in sinyal.alasan

    def test_confidence_dewan_terbawa(self, dewan) -> None:
        deliberation, bukti, harga = dewan
        sinyal = putuskan_dari_dewan(deliberation, bukti, harga)
        assert sinyal.confidence == deliberation.confidence

    def test_spread_dilaporkan_tidak_diukur(self, dewan) -> None:
        deliberation, bukti, harga = dewan
        assert putuskan_dari_dewan(deliberation, bukti, harga).spread_diukur is False

    def test_berita_benar_benar_diteruskan_ke_gerbang(self, dewan) -> None:
        """Perangkaiannya, bukan logikanya.

        Gerbang beritanya sendiri diuji di ``test_xau_kalender``. Yang diuji di
        sini adalah bahwa ``putuskan_dari_dewan`` MENERUSKANNYA - keluarga
        cacat yang paling sering berulang di repo ini adalah kode yang ditulis,
        diuji, diekspor, lalu tidak pernah dirangkai.

        ``berita_terukur`` dipakai sebagai buktinya karena ia terisi di SETIAP
        jalur pulang, termasuk penolakan arah yang menyala lebih dulu atas data
        sintetis - jadi ia menjawab "parameternya sampai?" tanpa butuh dewan
        yang berarah.
        """
        deliberation, bukti, harga = dewan
        berita = ringkas(
            [
                PeristiwaEkonomi(
                    judul="Fed Chairman Speaks",
                    negara="USD",
                    saat=SAAT + timedelta(minutes=10),
                    dampak=Dampak.HIGH,
                    sumber="uji",
                )
            ],
            sekarang=SAAT,
        )
        assert berita.terukur, "prasyarat: kalender uji harus terbaca"

        polos = putuskan_dari_dewan(deliberation, bukti, harga)
        assert polos.berita_terukur is False, "tanpa kalender: TIDAK AKTIF"

        dengan = putuskan_dari_dewan(deliberation, bukti, harga, berita=berita)
        assert dengan.berita_terukur is True, (
            "kalender tidak sampai ke `putuskan`; gerbangnya ada tapi tidak "
            "pernah dirangkai"
        )


class TestGerbangBeritaDiTerapkan:
    """Gerbangnya benar-benar menolak setup yang kalau tidak, akan menyala."""

    @staticmethod
    def _berita(menit: int, dampak: Dampak):
        return ringkas(
            [
                PeristiwaEkonomi(
                    judul="Prelim Benchmark Payrolls Revision",
                    negara="USD",
                    saat=SAAT + timedelta(minutes=menit),
                    dampak=dampak,
                    sumber="uji",
                )
            ],
            sekarang=SAAT,
        )

    def test_setup_sah_ditolak_menjelang_high(self) -> None:
        polos = _putuskan()
        assert polos.keputusan is Decision.BUY, "prasyarat: setup ini sah"

        sinyal = _putuskan(berita=self._berita(10, Dampak.HIGH))
        assert sinyal.keputusan is Decision.NO_SIGNAL
        assert "rilis HIGH" in sinyal.alasan
        assert sinyal.berita_terukur is True

    def test_setup_sah_tetap_menyala_menjelang_low(self) -> None:
        """Permintaan operator: kalau news-nya LOW, teknikal yang bicara."""
        sinyal = _putuskan(berita=self._berita(1, Dampak.LOW))
        assert sinyal.keputusan is Decision.BUY
        assert sinyal.alasan is None
        assert sinyal.berita_terukur is True

    def test_ditolak_rilis_tidak_mengunci_cooldown(self) -> None:
        """Setup yang dibuang rilis tidak boleh memblokir dirinya sendiri
        sesudah gejolaknya reda - itu akan menghukumnya dua kali."""
        cooldown = Cooldown()
        ditolak = _putuskan(
            berita=self._berita(10, Dampak.HIGH), cooldown=cooldown
        )
        assert ditolak.keputusan is Decision.NO_SIGNAL

        lagi = _putuskan(cooldown=cooldown)
        assert lagi.keputusan is Decision.BUY, (
            "gagasan ini tercatat di cooldown padahal tidak pernah dikabarkan"
        )
