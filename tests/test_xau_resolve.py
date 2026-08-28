"""Dua sumbu yang tidak boleh digabung, dan aturan pesimis yang menjaganya."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aruna.core.enums import Decision, Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.geometri import Geometri
from aruna.xau.resolve import (
    HORIZON_BAR,
    LevelTersentuh,
    nilai_hasil,
    r_multiple,
)

AWAL = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
PROV = Provenance(source="twelvedata")
MIGRASI = Path(__file__).resolve().parent.parent / "migrations" / "0047_xau_hasil.sql"


def _sql_yang_dieksekusi() -> str:
    """Migrasi tanpa komentarnya, huruf besar."""
    teks = MIGRASI.read_text(encoding="utf-8").upper()
    return "\n".join(
        b for b in teks.splitlines() if not b.strip().startswith("--")
    )


def _bar(i: int, *, high: str, low: str, close: str) -> Candle:
    buka = AWAL + timedelta(minutes=5 * i)
    return Candle(
        market=Market.FOREX,
        symbol="XAU/USD",
        interval=Horizon.M5,
        open_time=buka,
        close_time=buka + timedelta(minutes=5),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(0),
        provenance=PROV,
        is_closed=True,
    )


def _datar(jumlah: int = HORIZON_BAR, harga: str = "1000") -> list[Candle]:
    """Jalur yang tidak menyentuh apa pun."""
    return [_bar(i, high=harga, low=harga, close=harga) for i in range(jumlah)]


GEO = Geometri(
    entry=Decimal("1000"),
    stop=Decimal("990"),
    target=Decimal("1030"),
    atr=Decimal("5"),
    sentuhan_target=4,
)


class TestDuaSumbuTerpisah:
    def test_arah_benar_walau_stop_kena(self) -> None:
        """Kasus yang taksonomi satu sumbu tidak bisa lihat.

        Kena stop lalu berbalik dan tutup jauh di atas entry: ramalannya BENAR,
        stop-nya yang terlalu ketat. Menggabungkannya jadi satu angka menghapus
        tepat perbedaan yang menentukan apa yang harus diperbaiki.
        """
        jalur = _datar()
        jalur[2] = _bar(2, high="1001", low="985", close="1000")
        jalur[-1] = _bar(HORIZON_BAR - 1, high="1020", low="1015", close="1020")

        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)

        assert hasil.level_tersentuh is LevelTersentuh.STOP
        assert hasil.arah_benar is True

    def test_arah_salah_walau_target_kena(self) -> None:
        """Beruntung, dan bukan bukti apa pun."""
        jalur = _datar()
        jalur[3] = _bar(3, high="1035", low="999", close="1030")
        jalur[-1] = _bar(HORIZON_BAR - 1, high="995", low="980", close="985")

        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)

        assert hasil.level_tersentuh is LevelTersentuh.TARGET
        assert hasil.arah_benar is False

    def test_arah_diukur_pada_tutup_horizon(self) -> None:
        jalur = _datar()
        jalur[-1] = _bar(HORIZON_BAR - 1, high="1012", low="1008", close="1010")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil.harga_tutup == Decimal("1010")
        assert hasil.arah_benar is True


class TestStopMenangSaatKeduanyaKena:
    def test_satu_bar_menyentuh_keduanya_dihitung_stop(self) -> None:
        """Bar M5 tidak menyimpan urutan di dalamnya.

        Menganggap target duluan berarti mengarang keberuntungan yang tak ada
        buktinya; menganggap stop duluan hanya membuat angkanya pesimis. Angka
        optimis yang salah membuat strategi terlihat layak dipakai.
        """
        jalur = _datar()
        jalur[5] = _bar(5, high="1035", low="985", close="1000")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil.level_tersentuh is LevelTersentuh.STOP

    def test_target_lebih_dulu_di_bar_berbeda_tetap_target(self) -> None:
        jalur = _datar()
        jalur[4] = _bar(4, high="1035", low="999", close="1030")
        jalur[9] = _bar(9, high="1001", low="985", close="990")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil.level_tersentuh is LevelTersentuh.TARGET


class TestSell:
    def test_sell_stop_di_atas_target_di_bawah(self) -> None:
        geo = Geometri(
            entry=Decimal("1000"),
            stop=Decimal("1010"),
            target=Decimal("970"),
            atr=Decimal("5"),
            sentuhan_target=4,
        )
        jalur = _datar()
        jalur[6] = _bar(6, high="1001", low="965", close="970")
        jalur[-1] = _bar(HORIZON_BAR - 1, high="975", low="965", close="970")

        hasil = nilai_hasil(1, geo, Decision.SELL, jalur)

        assert hasil.level_tersentuh is LevelTersentuh.TARGET
        assert hasil.arah_benar is True

    def test_sell_yang_naik_adalah_arah_salah(self) -> None:
        geo = Geometri(
            entry=Decimal("1000"),
            stop=Decimal("1010"),
            target=Decimal("970"),
            atr=Decimal("5"),
            sentuhan_target=4,
        )
        jalur = _datar()
        jalur[-1] = _bar(HORIZON_BAR - 1, high="1006", low="1002", close="1005")
        hasil = nilai_hasil(1, geo, Decision.SELL, jalur)
        assert hasil.arah_benar is False


class TestStopMengakhiriSekarang:
    """Level yang tersentuh mengakhiri gagasannya SEKARANG.

    Diukur dari kerugian nyata operator 2026-08-28: tiga sinyal kena stop
    pukul 19:05 dan resolver menunggu sampai 22:10 - tiga jam sebuah hasil
    yang sudah pasti menggantung tanpa dicatat, tanpa result terkirim, dan tak
    terlihat oleh koreksi diri. Menunggu tidak mengubah apa pun yang sudah
    terjadi; ia hanya menunda operator mengetahuinya.
    """

    def test_stop_tersentuh_dinilai_sebelum_horizon_habis(self) -> None:
        jalur = _datar(10)
        jalur[3] = _bar(3, high="1001", low="985", close="988")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil is not None
        assert hasil.level_tersentuh is LevelTersentuh.STOP
        assert hasil.bar_dipakai == 10

    def test_target_tersentuh_dinilai_sebelum_horizon_habis(self) -> None:
        jalur = _datar(10)
        jalur[4] = _bar(4, high="1035", low="1000", close="1032")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil is not None
        assert hasil.level_tersentuh is LevelTersentuh.TARGET

    def test_arah_benar_TIDAK_diisi_saat_dini(self) -> None:
        """Ia bertanya ke mana harga pergi pada TUTUP HORIZON, dan horizon itu
        belum tutup. Mengisinya dari harga saat stop tersentuh menjawab
        pertanyaan yang berbeda dengan nama pertanyaan yang sama."""
        jalur = _datar(10)
        jalur[3] = _bar(3, high="1001", low="985", close="988")
        assert nilai_hasil(1, GEO, Decision.BUY, jalur).arah_benar is None

    def test_horizon_penuh_tetap_mengisi_arah(self) -> None:
        jalur = _datar()
        jalur[3] = _bar(3, high="1001", low="985", close="988")
        jalur[-1] = _bar(HORIZON_BAR - 1, high="1020", low="1015", close="1020")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)
        assert hasil.level_tersentuh is LevelTersentuh.STOP
        assert hasil.arah_benar is True


class TestBelumTuntas:
    def test_jalur_kurang_dari_horizon_tidak_menghasilkan_apa_apa(self) -> None:
        """None = belum selesai. TIDAK_SATU_PUN = selesai tanpa menyentuh.

        Berlaku HANYA saat belum ada level tersentuh - kalau stop sudah kena,
        hasilnya sudah pasti dan menundanya cuma menunda operator tahu.
        """
        assert nilai_hasil(1, GEO, Decision.BUY, _datar(HORIZON_BAR - 1)) is None

    def test_jalur_pas_horizon_dinilai(self) -> None:
        assert nilai_hasil(1, GEO, Decision.BUY, _datar(HORIZON_BAR)) is not None

    def test_jalur_lebih_panjang_dipotong_ke_horizon(self) -> None:
        """Bar sesudah horizon bukan bukti; memakainya adalah look-ahead."""
        panjang = _datar(HORIZON_BAR + 20)
        panjang[HORIZON_BAR + 5] = _bar(
            HORIZON_BAR + 5, high="1035", low="1030", close="1032"
        )
        hasil = nilai_hasil(1, GEO, Decision.BUY, panjang)
        assert hasil.bar_dipakai == HORIZON_BAR
        assert hasil.level_tersentuh is LevelTersentuh.TIDAK_SATU_PUN


class TestNoSignalTidakPunyaHasil:
    def test_menilai_no_signal_ditolak(self) -> None:
        """Sebuah NO SIGNAL tidak menyatakan arah, jadi tak ada hasil yang bisa
        membenarkan atau menyalahkannya."""
        with pytest.raises(ValueError, match="berarah"):
            nilai_hasil(1, GEO, Decision.NO_SIGNAL, _datar())

    def test_storage_menolaknya_juga(self) -> None:
        """Aturan yang cuma hidup di kode berlaku selama penulis berikutnya ingat.

        Yang menulis ke tabel ini nanti adalah pipeline pembelajaran yang belum
        ditulis siapa pun.
        """
        eksekusi = _sql_yang_dieksekusi()
        assert "CHECK (KEPUTUSAN IN ('BUY', 'SELL'))" in eksekusi
        assert "FOREIGN KEY (PREDICTION_ID, KEPUTUSAN)" in eksekusi

    def test_migrasi_tetap_satu_pernyataan_per_titik_koma(self) -> None:
        """Runner memecah berkas tanpa penanganan DELIMITER (lihat 0001).

        Dipindai pada SQL yang dieksekusi saja: komentar di migrasi ini justru
        menjelaskan kenapa DELIMITER tidak dipakai, dan memindai komentar akan
        membuat penjelasan yang benar menjatuhkan tesnya sendiri.
        """
        eksekusi = _sql_yang_dieksekusi()
        assert "DELIMITER" not in eksekusi
        assert "BEGIN" not in eksekusi


class TestHorizonTersimpan:
    def test_horizon_ikut_di_hasil(self) -> None:
        """Horizon yang cuma hidup di kode membuat hasil lama dan baru tak bisa
        dibandingkan setelah angkanya diubah."""
        hasil = nilai_hasil(1, GEO, Decision.BUY, _datar(30), horizon_bar=24)
        assert hasil.horizon_bar == 24
        assert hasil.bar_dipakai == 24


class TestKeluarDiLevel:
    """Harga tutup adalah level yang tersentuh, bukan close bar sesudahnya.

    Ditemukan dari pertanyaan operator atas sebuah kemenangan yang tampak
    janggal: pesannya melaporkan tutup di ATAS target pada sinyal SELL.
    Cacat yang sama membuat tiga stop-out tercatat -1,65 / -1,58 / -1,82 R -
    kerugian yang dilebih-lebihkan oleh harga yang tak pernah ditransaksikan.
    """

    @staticmethod
    def _jalur(*, kena_high: str, kena_low: str, sisa: str) -> list[Candle]:
        """Bar 0 menyentuh level, sisanya melayang jauh di harga lain."""
        return [_bar(0, high=kena_high, low=kena_low, close=sisa)] + [
            _bar(i, high=sisa, low=sisa, close=sisa)
            for i in range(1, HORIZON_BAR)
        ]

    def test_stop_kena_persis_minus_satu_r(self) -> None:
        """Stop-out menurut definisinya -1,00 R.

        Angka lain berarti kerugiannya diukur dari harga yang bukan tempat
        order stop terisi. Di sini harga terus jatuh ke 970 sesudah stop kena:
        memakai close bar akan melaporkan -3,00 R atas risiko yang sudah
        berhenti berjalan di 990.
        """
        jalur = self._jalur(kena_high="1000", kena_low="985", sisa="970")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)

        assert hasil.level_tersentuh is LevelTersentuh.STOP
        assert hasil.harga_tutup == GEO.stop
        r = r_multiple(GEO.entry, GEO.stop, hasil.harga_tutup, Decision.BUY)
        assert r == Decimal("-1")

    def test_target_kena_dipakai_walau_harga_balik(self) -> None:
        """Target tersentuh di tengah bar lalu harga berbalik.

        Persis bentuk sinyal 134 di produksi: target 4574,87 tersentuh, bar-nya
        tutup di 4575,09 - DI ATAS target - dan pesannya jadi terbaca seperti
        target yang tak pernah tercapai.
        """
        jalur = self._jalur(kena_high="1035", kena_low="1000", sisa="1005")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)

        assert hasil.level_tersentuh is LevelTersentuh.TARGET
        assert hasil.harga_tutup == GEO.target
        assert hasil.gerak_pct == Decimal("3")

    def test_tanpa_level_tetap_pakai_penutup(self) -> None:
        """Tidak ada level tersentuh berarti tidak ada order yang terisi.

        Satu-satunya harga yang berarti di sini adalah tempat pasar berada saat
        horizon habis.
        """
        hasil = nilai_hasil(1, GEO, Decision.BUY, _datar(harga="1010"))

        assert hasil.level_tersentuh is LevelTersentuh.TIDAK_SATU_PUN
        assert hasil.harga_tutup == Decimal("1010")

    def test_arah_tetap_diukur_dari_penutup(self) -> None:
        """Sumbu ramalan tidak boleh ikut pindah ke harga keluar.

        Kena stop di 990 lalu berbalik dan tutup di 1020: ramalan BENAR dengan
        stop terlalu ketat. Mengukur arah dari harga keluar akan melaporkannya
        salah, dan menghapus justru pembedaan yang modul ini ada untuk menjaga.
        """
        jalur = self._jalur(kena_high="1000", kena_low="985", sisa="1020")
        hasil = nilai_hasil(1, GEO, Decision.BUY, jalur)

        assert hasil.harga_tutup == GEO.stop
        assert hasil.arah_benar is True
