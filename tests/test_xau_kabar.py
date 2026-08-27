"""Kabar lanjutan: hanya saat keadaan berganti, dan jujur saat gagasannya batal."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aruna.analysis.structure import (
    BreakoutState,
    Level,
    StructureReport,
    TrendStructure,
)
from aruna.core.enums import Decision
from aruna.xau.kabar import (
    AMBANG_DEKAT_ATR,
    SISA_HAMPIR_HABIS,
    Kabar,
    Keadaan,
    nilai_kabar,
    susun_kabar,
)

SAAT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _struktur(*harga: float, support: bool = False) -> StructureReport:
    level = tuple(
        Level(price=h, touches=4, is_support=support, last_touch=SAAT) for h in harga
    )
    return StructureReport(
        trend=TrendStructure.UNDETERMINED,
        breakout=BreakoutState.NONE,
        support=level if support else (),
        resistance=() if support else level,
        confirmed_swings=6,
    )


def _nilai(**kw):
    bawaan = dict(
        arah=Decision.BUY,
        entry=Decimal("1000"),
        stop=Decimal("990"),
        target=Decimal("1030"),
        atr=Decimal("5"),
        harga=Decimal("1005"),
        struktur=_struktur(1030.0),
        sisa_bar=30,
    )
    return nilai_kabar(**{**bawaan, **kw})


class TestBerjalanTidakDikabarkan:
    def test_belum_ada_yang_berubah(self) -> None:
        """XAU menick 288 kali sehari; mengabarkan tiap denyut mengubur yang
        penting."""
        k = _nilai()
        assert k.keadaan is Keadaan.BERJALAN
        assert k.perlu_dikabarkan is False


class TestTesisBatal:
    def test_level_hilang_membatalkan(self) -> None:
        """Sinyal berdiri di atas satu alasan: levelnya. Alasannya hilang,
        gagasannya batal."""
        k = _nilai(struktur=_struktur(1200.0))
        assert k.keadaan is Keadaan.TESIS_BATAL
        assert k.menyarankan_tutup is True

    def test_tanpa_level_sama_sekali_membatalkan(self) -> None:
        k = _nilai(struktur=_struktur())
        assert k.keadaan is Keadaan.TESIS_BATAL

    def test_level_bergeser_sedikit_TIDAK_membatalkan(self) -> None:
        """Level bergeser tiap bar karena swing baru masuk hitungan. Menuntut
        kecocokan persis akan membatalkan tiap sinyal dalam satu tick - dan
        fitur ini tak pernah berguna sekali pun."""
        k = _nilai(struktur=_struktur(1032.0))  # bergeser 2, toleransi 0,75 ATR = 3,75
        assert k.keadaan is not Keadaan.TESIS_BATAL

    def test_batal_menang_atas_mendekati_target(self) -> None:
        """Tesis yang sudah batal tidak boleh dilaporkan sebagai kabar baik."""
        k = _nilai(harga=Decimal("1029"), struktur=_struktur(1200.0))
        assert k.keadaan is Keadaan.TESIS_BATAL

    def test_sell_memakai_support(self) -> None:
        k = _nilai(
            arah=Decision.SELL,
            stop=Decimal("1010"),
            target=Decimal("970"),
            struktur=_struktur(970.0, support=True),
        )
        assert k.keadaan is not Keadaan.TESIS_BATAL


class TestKedekatan:
    def test_dekat_stop_dikabarkan(self) -> None:
        k = _nilai(harga=Decimal("992"))  # 2 dari stop = 0,4 ATR
        assert k.keadaan is Keadaan.MENDEKAT_STOP

    def test_dekat_target_dikabarkan(self) -> None:
        k = _nilai(harga=Decimal("1028"))  # 2 dari target = 0,4 ATR
        assert k.keadaan is Keadaan.MENDEKAT_TARGET

    def test_stop_diperiksa_sebelum_target(self) -> None:
        """Kalau keduanya dekat, kabar buruk yang lebih mendesak."""
        k = _nilai(atr=Decimal("40"), harga=Decimal("1005"))
        assert k.keadaan is Keadaan.MENDEKAT_STOP

    def test_ambang_dipakai_dalam_ATR_bukan_harga(self) -> None:
        """Dua dolar dekat di emas yang bergerak $1/bar, jauh di yang $20/bar."""
        jauh = _nilai(harga=Decimal("992"), atr=Decimal("100"))
        assert jauh.keadaan is Keadaan.MENDEKAT_STOP
        dekat = _nilai(harga=Decimal("992"), atr=Decimal("1"))
        assert dekat.keadaan is not Keadaan.MENDEKAT_STOP


class TestHampirHabis:
    def test_sisa_sedikit_dikabarkan(self) -> None:
        k = _nilai(sisa_bar=SISA_HAMPIR_HABIS)
        assert k.keadaan is Keadaan.HAMPIR_HABIS

    def test_masih_banyak_waktu_tidak_dikabarkan(self) -> None:
        assert _nilai(sisa_bar=SISA_HAMPIR_HABIS + 1).keadaan is Keadaan.BERJALAN


class TestPesan:
    def test_pembatalan_mengakui_salah(self) -> None:
        """Operator berhak tahu ARUNA salah baca, bukan cuma bahwa keadaannya
        berubah."""
        k = _nilai(struktur=_struktur(1200.0))
        pesan = susun_kabar(k, arah=Decision.BUY, as_of="12:00")
        assert "BATAL" in pesan
        assert "salah membacanya" in pesan

    def test_pembatalan_tetap_menyatakan_analis_saja(self) -> None:
        """"Sebaiknya ditutup" adalah pembacaan, bukan perintah eksekusi."""
        k = _nilai(struktur=_struktur(1200.0))
        pesan = susun_kabar(k, arah=Decision.BUY, as_of="12:00").lower()
        assert "keputusan anda" in pesan
        assert "menganalisa saja" in pesan
        assert "tidak menempatkan order" in pesan

    def test_kabar_biasa_tidak_menyuruh_tutup(self) -> None:
        k = _nilai(harga=Decimal("1028"))
        pesan = susun_kabar(k, arah=Decision.BUY, as_of="12:00")
        assert "BATAL" not in pesan
        assert "ditutup" not in pesan

    def test_pesan_membawa_angkanya(self) -> None:
        """Kabar tanpa angka tak bisa dibantah."""
        k = _nilai(harga=Decimal("1028"))
        pesan = susun_kabar(k, arah=Decision.BUY, as_of="12:00")
        assert "1,028.00" in pesan
        assert "ATR" in pesan


class TestPenolakan:
    def test_no_signal_tidak_punya_kabar(self) -> None:
        with pytest.raises(ValueError, match="berarah"):
            _nilai(arah=Decision.NO_SIGNAL)


class TestPenutupHorizon:
    """Horizon habis tanpa level tersentuh adalah TITIK KEPUTUSAN.

    Sebelumnya ia berakhir dalam diam - justru keadaan tempat kerugian paling
    sering dibiarkan tumbuh: bukan saat stop tersentuh (itu jelas), melainkan
    saat tidak ada yang terjadi dan tidak ada yang mengatakan apa-apa.
    """

    def _penutup(self, **kw):
        from aruna.xau.kabar import nilai_penutup

        bawaan = dict(
            arah=Decision.BUY,
            target=Decimal("1030"),
            atr=Decimal("5"),
            struktur=_struktur(1030.0),
            arah_benar=True,
            gerak_pct=Decimal("0.35"),
        )
        return nilai_penutup(**{**bawaan, **kw})

    def test_selalu_ada_putusan(self) -> None:
        """Tidak pernah kosong: tahan atau tutup."""
        assert self._penutup().tahan in (True, False)

    def test_arah_benar_dan_level_ada_maka_tahan(self) -> None:
        p = self._penutup()
        assert p.tahan is True
        assert "hidup" in p.alasan

    def test_arah_meleset_maka_tutup(self) -> None:
        """Bertahan pada bacaan yang terbukti meleset adalah menahan kerugian
        demi terlihat konsisten."""
        p = self._penutup(arah_benar=False, gerak_pct=Decimal("-0.42"))
        assert p.tahan is False
        assert "melawan" in p.alasan

    def test_level_hilang_maka_tutup_walau_arah_benar(self) -> None:
        """Gagasannya bukan lambat - ia sudah tidak ada."""
        p = self._penutup(struktur=_struktur(1200.0), arah_benar=True)
        assert p.tahan is False
        assert "sudah tidak ada" in p.alasan

    def test_level_diperiksa_sebelum_arah(self) -> None:
        p = self._penutup(struktur=_struktur(1200.0), arah_benar=False)
        assert p.level_masih_ada is False
        assert "tidak terbaca" in p.alasan

    def test_arah_tak_terukur_maka_tutup(self) -> None:
        """Tidak ada dasar untuk menahan bukan alasan untuk menahan."""
        assert self._penutup(arah_benar=None).tahan is False

    def test_no_signal_ditolak(self) -> None:
        with pytest.raises(ValueError, match="berarah"):
            self._penutup(arah=Decision.NO_SIGNAL)


class TestPesanPenutup:
    def _pesan(self, **kw) -> str:
        from aruna.xau.kabar import nilai_penutup, susun_penutup

        bawaan = dict(
            arah=Decision.BUY,
            target=Decimal("1030"),
            atr=Decimal("5"),
            struktur=_struktur(1030.0),
            arah_benar=True,
            gerak_pct=Decimal("0.35"),
        )
        p = nilai_penutup(**{**bawaan, **kw})
        return susun_penutup(
            p,
            arah=bawaan["arah"],
            entry=Decimal("1000"),
            harga_tutup=Decimal("1003.50"),
            target=Decimal("1030"),
        )

    def test_menyuruh_tahan_dengan_jelas(self) -> None:
        assert "TAHAN DULU" in self._pesan()

    def test_menyuruh_tutup_dengan_jelas(self) -> None:
        assert "SEBAIKNYA DITUTUP" in self._pesan(arah_benar=False)

    def test_mengakui_salah_saat_arah_meleset(self) -> None:
        assert "salah membaca arahnya" in self._pesan(arah_benar=False)

    def test_menyatakan_target_tidak_tersentuh(self) -> None:
        assert "tidak tersentuh" in self._pesan()

    def test_tetap_menyatakan_analis_saja(self) -> None:
        pesan = self._pesan().lower()
        assert "keputusan tetap di anda" in pesan
        assert "tidak" in pesan and "order" in pesan

    def test_membawa_angka_geraknya(self) -> None:
        """Putusan tanpa angkanya tidak bisa dibantah."""
        assert "+0.35%" in self._pesan()
