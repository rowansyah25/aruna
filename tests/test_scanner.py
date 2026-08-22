"""Pemindai cepat dan antrean analisis (PASAL 14, 15, 38, 39).

Dua bahaya yang berlawanan, dan test di sini menjaga keduanya sekaligus.

Pemindai yang terlalu longgar mengirim segalanya ke council, yang berarti tidak
memilih apa pun dan membakar biaya analisis untuk pasar yang diam. Pemindai
yang terlalu ketat tidak pernah menyala - dan itu **tidak bisa dibedakan dari
pemindai yang rusak**, karena keduanya menghasilkan keluaran yang sama persis.
Karena itu setiap kelas di sini menguji dua arah: bahwa ia menyala saat memang
ada yang bergerak, DAN bahwa ia diam saat tidak.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.scanner import (
    MIN_BASELINE_BARS,
    AnalysisQueue,
    EventKind,
    ScanThresholds,
    SignificantEvent,
    scan,
    scan_symbol,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def bar(
    index: int,
    *,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    volume: float = 1000.0,
) -> dict:
    """Satu bar tenang, kecuali yang sengaja diubah."""
    return {
        "open_time": NOW + timedelta(minutes=15 * index),
        "close_time": NOW + timedelta(minutes=15 * (index + 1)),
        "open": Decimal(str(open_ if open_ is not None else close)),
        "high": Decimal(str(high if high is not None else close + 0.5)),
        "low": Decimal(str(low if low is not None else close - 0.5)),
        "close": Decimal(str(close)),
        "volume": Decimal(str(volume)),
        "is_closed": True,
    }


def quiet(count: int = MIN_BASELINE_BARS + 1) -> list[dict]:
    """Riwayat yang benar-benar tidak melakukan apa-apa."""
    return [bar(i) for i in range(count)]


class TestBuktiTidakCukupBukanPeristiwa:
    """Nol yang berarti "tidak bisa diukur" dilarang (SPEC 4). Garis dasar
    yang dibentuk dari empat bar terlihat persis seperti yang dibentuk dari
    empat ratus."""

    @pytest.mark.parametrize("count", [2, 5, MIN_BASELINE_BARS])
    def test_bar_terlalu_sedikit_tidak_menghasilkan_apa_pun(self, count: int) -> None:
        """Bar terakhir sengaja dibuat MELONJAK.

        Versi pertama test ini memberi volume yang sama ke semua bar, sehingga
        rasionya 1,0 dan tidak ada yang menyala berapa pun ambangnya - ia lulus
        dengan atau tanpa penjaga, yaitu test yang tidak bisa gagal. Dengan
        lonjakan di bar terakhir, menurunkan ``MIN_BASELINE_BARS`` membuat test
        ini merah, yang memang satu-satunya tugasnya.
        """
        bars = [bar(i) for i in range(count - 1)]
        bars.append(bar(count - 1, close=140.0, high=140.0, volume=50_000.0))
        assert scan("BTC/USDT", bars) == []

    @pytest.mark.parametrize("count", [0, 1])
    def test_riwayat_kosong_atau_satu_bar(self, count: int) -> None:
        assert scan("BTC/USDT", [bar(i) for i in range(count)]) == []

    def test_tepat_cukup_barulah_bisa_menyala(self) -> None:
        """Pasangan test di atas: kalau ambangnya salah pasang, yang di atas
        akan lulus karena pemindainya memang tidak pernah menyala."""
        bars = quiet(MIN_BASELINE_BARS + 1)
        bars[-1] = bar(MIN_BASELINE_BARS, volume=10_000.0)
        assert [e.kind for e in scan("BTC/USDT", bars)] == [EventKind.VOLUME_SPIKE]

    def test_bar_cacat_tidak_menghasilkan_peristiwa(self) -> None:
        bars = quiet()
        bars[-1] = dict(bars[-1], close=None, high=None)
        assert scan("BTC/USDT", bars) == []


class TestDiamSaatPasarDiam:
    def test_pasar_tenang_tidak_menghasilkan_apa_pun(self) -> None:
        assert scan("BTC/USDT", quiet(60)) == []

    def test_kenaikan_pelan_bukan_peristiwa(self) -> None:
        """Tren yang rapi bukan kejutan. Kalau ini menyala, setiap bar dalam
        tren akan masuk council."""
        bars = [bar(i, close=100.0 + i * 0.1) for i in range(60)]
        assert scan("BTC/USDT", bars) == []


class TestVolume:
    def test_lonjakan_volume_disebut_dengan_kelipatannya(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, volume=5000.0)
        events = scan("BTC/USDT", bars)

        spike = next(e for e in events if e.kind is EventKind.VOLUME_SPIKE)
        # Severity dinormalkan ke ambangnya sendiri: 5x volume terhadap ambang
        # 3x = 1,67. Angka mentahnya tetap ada, dan kalimatnya tetap menyebut
        # 5,0x - yang dibaca operator adalah pengukurannya, bukan rasio internal.
        assert spike.severity == pytest.approx(5.0 / 3.0, abs=0.01)
        assert spike.evidence["measured"] == pytest.approx(5.0, abs=0.01)
        assert spike.evidence["threshold"] == pytest.approx(3.0)
        assert "5.0x" in spike.detail
        assert spike.evidence["baseline"] == pytest.approx(1000.0)

    def test_di_bawah_ambang_diam(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, volume=2000.0)  # 2x, ambang 3x
        assert not [e for e in scan("BTC/USDT", bars) if e.kind is EventKind.VOLUME_SPIKE]

    def test_ambang_bisa_diketatkan(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, volume=5000.0)
        strict = ScanThresholds(volume_spike=10.0)
        assert not [
            e
            for e in scan("BTC/USDT", bars, thresholds=strict)
            if e.kind is EventKind.VOLUME_SPIKE
        ]

    def test_bar_yang_dinilai_tidak_ikut_membentuk_garis_dasarnya(self) -> None:
        """Membandingkan sesuatu dengan dirinya sendiri selalu menghasilkan
        "biasa" - dan makin besar lonjakannya, makin besar pula rata-rata yang
        ia angkat, sehingga lonjakan raksasa justru meredam dirinya sendiri."""
        bars = quiet(21)
        bars[-1] = bar(20, volume=1000.0 * 21)
        spike = next(e for e in scan("BTC/USDT", bars) if e.kind is EventKind.VOLUME_SPIKE)
        # 21x terhadap 20 bar sebelumnya. Kalau bar itu ikut dirata-rata,
        # pengukurannya akan jatuh ke sekitar 10,5x.
        assert spike.evidence["measured"] == pytest.approx(21.0, abs=0.1)


class TestGerakanDanVolatilitas:
    def test_gerakan_besar_diukur_terhadap_atr(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, open_=100.0, close=110.0, high=110.0, low=100.0)
        events = scan("BTC/USDT", bars)

        move = next(e for e in events if e.kind is EventKind.PRICE_MOVE)
        assert move.severity > 1.5
        assert "naik" in move.detail

    def test_arah_turun_disebut_turun(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, open_=100.0, close=90.0, high=100.0, low=90.0)
        move = next(e for e in scan("BTC/USDT", bars) if e.kind is EventKind.PRICE_MOVE)
        assert "turun" in move.detail

    def test_tanpa_atr_gerakan_tidak_dinilai(self) -> None:
        """Bar datar sempurna memberi ATR nol. Membaginya akan meledak, dan
        memberinya ambang default akan menjadikan setiap gerakan tak terhingga
        kali ATR."""
        flat = [bar(i, close=100.0, high=100.0, low=100.0) for i in range(60)]
        flat[-1] = bar(59, close=100.0, high=100.0, low=100.0, volume=9000.0)
        kinds = {e.kind for e in scan("BTC/USDT", flat)}
        assert kinds == {EventKind.VOLUME_SPIKE}


class TestBreak:
    def test_tembus_ke_atas(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, close=120.0, high=120.0)
        events = scan("BTC/USDT", bars)
        out = next(e for e in events if e.kind is EventKind.BREAKOUT)
        assert out.evidence["prior_high"] == pytest.approx(100.5)

    def test_tembus_ke_bawah(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, close=80.0, low=80.0)
        assert any(e.kind is EventKind.BREAKDOWN for e in scan("BTC/USDT", bars))

    def test_menyentuh_high_saja_bukan_break(self) -> None:
        """Tanpa margin, setiap sentuhan jadi peristiwa dan council dipanggil
        untuk noise."""
        bars = quiet(60)
        bars[-1] = bar(59, close=100.5, high=100.5)
        assert not [e for e in scan("BTC/USDT", bars) if e.kind is EventKind.BREAKOUT]


class TestSeverityBisaDibandingkanAntarJenis:
    """Angka yang mengurutkan antrean harus mengukur hal yang sama.

    Versi pertama menaruh dua besaran berbeda di ``severity``: volume sebagai
    kelipatan rata-rata, break sebagai jarak dalam ATR. Diukur waktu itu -
    break sungguhan 0,40 kalah dari lonjakan volume 3,00 yang tepat menyentuh
    ambangnya, sehingga yang lebih layak dianalisis justru dibuang duluan.
    """

    def test_tepat_di_ambang_selalu_satu(self) -> None:
        bars = quiet(60)
        bars[-1] = bar(59, volume=3000.0)  # persis ambang 3x
        spike = next(
            e for e in scan("BTC/USDT", bars) if e.kind is EventKind.VOLUME_SPIKE
        )
        assert spike.severity == pytest.approx(1.0, abs=0.01)

    def test_break_nyata_mengalahkan_volume_marginal(self) -> None:
        tipis = quiet(60)
        tipis[-1] = bar(59, close=100.9, high=100.9)
        breakout = next(
            e for e in scan("A", tipis) if e.kind is EventKind.BREAKOUT
        )

        marginal = quiet(60)
        marginal[-1] = bar(59, volume=3000.0)
        volume = next(
            e for e in scan("B", marginal) if e.kind is EventKind.VOLUME_SPIKE
        )

        assert breakout.severity > volume.severity, (
            f"break {breakout.severity:.2f} kalah dari volume "
            f"{volume.severity:.2f}"
        )

    def test_angka_mentahnya_tidak_hilang(self) -> None:
        """Normalisasi tidak boleh menghapus apa yang sebenarnya diukur."""
        bars = quiet(60)
        bars[-1] = bar(59, volume=9000.0)
        spike = next(
            e for e in scan("BTC/USDT", bars) if e.kind is EventKind.VOLUME_SPIKE
        )
        assert spike.evidence["measured"] == pytest.approx(9.0, abs=0.01)
        assert spike.evidence["threshold"] == pytest.approx(3.0)


class TestDiamDibedakanDariTidakBisaDiukur:
    """SPEC 4. ``scan`` mengembalikan daftar kosong untuk dua keadaan yang
    berlawanan; ``scan_symbol`` yang memisahkannya."""

    def test_pasar_diam_disebut_sudah_dipindai(self) -> None:
        result = scan_symbol("BTC/USDT", quiet(60))
        assert result.scanned is True
        assert result.events == ()
        assert result.reason is None
        assert result.usable_bars == 59

    def test_bukti_kurang_disebut_belum_dipindai(self) -> None:
        result = scan_symbol("BTC/USDT", quiet(5))
        assert result.scanned is False
        assert result.events == ()
        assert "garis dasar butuh" in (result.reason or "")

    def test_bar_tanpa_harga_tidak_dihitung_sebagai_bukti(self) -> None:
        """Riwayat lima puluh bar yang empat puluh tanpa harga bukan garis
        dasar - dan panjang daftar saja tidak bisa membedakannya."""
        bars = quiet(60)
        for i in range(45):
            bars[i] = dict(bars[i], close=None)
        result = scan_symbol("BTC/USDT", bars)
        assert result.scanned is False
        assert result.usable_bars == 14


class TestTetanggaLangsung:
    def test_true_range_memakai_bar_tetangga_bukan_yang_tersaring(self) -> None:
        """``closes`` sudah tersaring, jadi elemen terakhirnya bisa berasal
        dari dua bar sebelumnya - dan true range lalu diukur terhadap tetangga
        yang salah, diam-diam, karena hasilnya tetap angka yang masuk akal.

        Versi pertama test ini menyusun fixture yang tidak menyalakan apa pun
        di kedua sisi, jadi keduanya sama-sama ``[]`` dan ia lulus dengan atau
        tanpa perbaikan - test yang tidak bisa gagal. Fixture ini dipilih
        supaya bedanya menentukan: harga tetangga yang salah melahirkan
        VOLATILITY_SPIKE palsu, harga tetangga yang benar tidak.
        """
        # Riwayat rapat di 150, jadi ATR-nya kecil.
        bars = [bar(i, close=150.0) for i in range(60)]
        # Tetangga langsung kehilangan harganya. `closes[-1]` karena itu
        # menunjuk bar yang lebih jauh - yang harganya masih 150.
        bars[-2] = dict(bars[-2], close=None)
        # Bar terakhir jauh di bawah, tapi rentangnya sendiri sempit.
        bars[-1] = bar(59, open_=100.0, close=100.0, high=100.5, low=99.5)

        kinds = {e.kind for e in scan("BTC/USDT", bars)}

        # Dengan tetangga yang benar, close sebelumnya tidak diketahui, jadi
        # true range = high - low = 1,0 - sempit, bukan lonjakan volatilitas.
        # Dengan `closes[-1]` = 150, ia jadi |100,5 - 150| = 49,5 kali ATR.
        assert EventKind.VOLATILITY_SPIKE not in kinds, kinds


class TestAntreanMenggabungkan:
    """PASAL 39. Satu simbol menembus tiga kali dalam sepuluh detik adalah satu
    keadaan, bukan tiga pekerjaan."""

    @staticmethod
    def _event(symbol="BTC/USDT", kind=EventKind.BREAKOUT, severity=2.0, offset=0):
        return SignificantEvent(
            symbol=symbol,
            kind=kind,
            severity=severity,
            detail="test",
            at=NOW + timedelta(seconds=offset),
        )

    def test_kunci_sama_digabung_bukan_diantre(self) -> None:
        queue = AnalysisQueue()
        queue.offer(self._event(offset=0, severity=2.0))
        queue.offer(self._event(offset=1, severity=5.0))
        queue.offer(self._event(offset=2, severity=9.0))

        assert len(queue) == 1
        assert queue.stats.coalesced == 2
        assert queue.drain()[0].severity == 9.0

    def test_yang_lebih_tua_tidak_menimpa_yang_lebih_baru(self) -> None:
        queue = AnalysisQueue()
        queue.offer(self._event(offset=10, severity=2.0))
        assert queue.offer(self._event(offset=1, severity=9.0)) is False
        assert queue.drain()[0].severity == 2.0

    def test_jenis_berbeda_tidak_digabung(self) -> None:
        queue = AnalysisQueue()
        queue.offer(self._event(kind=EventKind.BREAKOUT))
        queue.offer(self._event(kind=EventKind.VOLUME_SPIKE))
        assert len(queue) == 2


class TestAntreanBerbatas:
    def test_penuh_membuang_yang_paling_tua(self) -> None:
        queue = AnalysisQueue(max_depth=2)
        queue.offer(TestAntreanMenggabungkan._event(symbol="A", offset=0))
        queue.offer(TestAntreanMenggabungkan._event(symbol="B", offset=5))
        queue.offer(TestAntreanMenggabungkan._event(symbol="C", offset=9))

        assert len(queue) == 2
        assert {e.symbol for e in queue.drain()} == {"B", "C"}

    def test_yang_datang_paling_tua_yang_ditolak(self) -> None:
        """Harga sepuluh detik lalu tidak jadi lebih berguna dengan menunggu."""
        queue = AnalysisQueue(max_depth=2)
        queue.offer(TestAntreanMenggabungkan._event(symbol="A", offset=5))
        queue.offer(TestAntreanMenggabungkan._event(symbol="B", offset=9))
        assert queue.offer(TestAntreanMenggabungkan._event(symbol="C", offset=0)) is False
        assert {e.symbol for e in queue.drain()} == {"A", "B"}

    def test_log_menyebut_yang_dibuang_bukan_yang_datang(self, capsys) -> None:
        """Baris yang seluruh gunanya menyebut korban, menyebut korban yang
        salah: versi pertama selalu mencatat peristiwa yang TIBA, padahal pada
        jalur umum justru yang lama yang dibuang (SPEC 49).

        Dibaca dari stdout, bukan ``caplog``: structlog dirender lewat
        prosesornya sendiri, jadi ``caplog`` menangkap string kosong dan test
        yang memakainya akan lulus tanpa memeriksa apa pun.
        """
        queue = AnalysisQueue(max_depth=1)
        queue.offer(TestAntreanMenggabungkan._event(symbol="LAMA", offset=0))
        capsys.readouterr()
        queue.offer(TestAntreanMenggabungkan._event(symbol="BARU", offset=9))
        keluaran = capsys.readouterr().out

        assert [e.symbol for e in queue.drain()] == ["BARU"]
        assert "symbol=LAMA" in keluaran, keluaran
        assert "symbol=BARU" not in keluaran

    def test_buangan_dihitung_bukan_disembunyikan(self) -> None:
        """Antrean yang membuang diam-diam membuat sistem yang kewalahan
        terlihat seperti pasar yang sepi."""
        queue = AnalysisQueue(max_depth=1)
        queue.offer(TestAntreanMenggabungkan._event(symbol="A", offset=0))
        queue.offer(TestAntreanMenggabungkan._event(symbol="B", offset=5))

        assert queue.stats.dropped_full == 1
        assert "dibuang karena antrean penuh" in queue.stats.summary()

    def test_puncak_kedalaman_tetap_tercatat_setelah_terkuras(self) -> None:
        """Kedalaman sekarang bisa nol justru sesudah insiden."""
        queue = AnalysisQueue(max_depth=10)
        for i in range(4):
            queue.offer(TestAntreanMenggabungkan._event(symbol=f"S{i}", offset=i))
        queue.drain()

        assert len(queue) == 0
        assert queue.stats.peak_depth == 4

    def test_kedalaman_nol_dilarang(self) -> None:
        with pytest.raises(ValueError, match="minimal 1"):
            AnalysisQueue(max_depth=0)


class TestUrutanKeluar:
    def test_paling_parah_keluar_dulu(self) -> None:
        queue = AnalysisQueue()
        queue.offer(TestAntreanMenggabungkan._event(symbol="A", severity=2.0))
        queue.offer(TestAntreanMenggabungkan._event(symbol="B", severity=9.0))
        queue.offer(TestAntreanMenggabungkan._event(symbol="C", severity=5.0))

        assert [e.symbol for e in queue.drain()] == ["B", "C", "A"]

    def test_limit_menyisakan_sisanya_untuk_putaran_berikutnya(self) -> None:
        queue = AnalysisQueue()
        for i, sev in enumerate((2.0, 9.0, 5.0)):
            queue.offer(
                TestAntreanMenggabungkan._event(symbol=f"S{i}", severity=sev)
            )

        first = queue.drain(limit=1)
        assert [e.severity for e in first] == [9.0]
        assert len(queue) == 2
        assert queue.stats.delivered == 1

    def test_severity_sama_diputus_oleh_yang_terbaru(self) -> None:
        queue = AnalysisQueue()
        queue.offer(TestAntreanMenggabungkan._event(symbol="A", severity=3.0, offset=0))
        queue.offer(TestAntreanMenggabungkan._event(symbol="B", severity=3.0, offset=9))
        assert [e.symbol for e in queue.drain()] == ["B", "A"]

