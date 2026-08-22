"""Syarat pembatalan signal (PASAL 14.21).

Kegagalan yang dijaga di sini: signal yang tesisnya sudah runtuh tapi tidak
pernah dinyatakan runtuh. Ia tetap terlihat aktif, dan operator yang membacanya
tidak punya cara tahu bahwa alasan di baliknya sudah hilang.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from aruna.decision import (
    Ambang,
    Arah,
    Invalidasi,
    InvalidationError,
    Sisi,
    against_entry,
)


@dataclass(frozen=True)
class FakeBar:
    close: Decimal
    is_closed: bool = True


def bar(harga: str, *, tutup: bool = True) -> FakeBar:
    return FakeBar(close=Decimal(harga), is_closed=tutup)


#: Contoh PASAL 14.21: LONG, batal kalau 15m tutup di bawah 63.780.
CONTOH = Invalidasi(
    decision=Arah.LONG,
    levels=(Ambang("15m", Sisi.BELOW, Decimal("63780")),),
    notes=("struktur bullish patah",),
)


class TestWajibAda:
    def test_signal_tanpa_level_ditolak(self) -> None:
        """"Setiap signal wajib memiliki invalidation condition." Tanpa level,
        signalnya tidak akan pernah dibatalkan oleh apa pun."""
        with pytest.raises(InvalidationError, match=r"PASAL 14\.21"):
            Invalidasi(decision=Arah.LONG, levels=())

    def test_kalimat_saja_tidak_cukup(self) -> None:
        """Invalidasi yang hanya berupa kalimat tidak bisa membuat ARUNA
        berhenti apa pun; ia catatan, bukan syarat."""
        with pytest.raises(InvalidationError):
            Invalidasi(
                decision=Arah.LONG,
                levels=(),
                notes=("struktur bullish patah",),
            )

    def test_no_signal_tidak_punya_pembatalan(self) -> None:
        with pytest.raises(InvalidationError, match="tesis"):
            Invalidasi(
                decision=Arah.NO_SIGNAL,
                levels=(Ambang("15m", Sisi.BELOW, Decimal("100")),),
            )

    def test_level_tanpa_timeframe_ditolak(self) -> None:
        with pytest.raises(InvalidationError, match="timeframe"):
            Ambang("  ", Sisi.BELOW, Decimal("63780"))

    def test_harga_tidak_masuk_akal_ditolak(self) -> None:
        for buruk in ("0", "-1"):
            with pytest.raises(InvalidationError, match="tidak masuk akal"):
                Ambang("15m", Sisi.BELOW, Decimal(buruk))


class TestArahnya:
    def test_long_dibatalkan_oleh_tutup_di_bawah(self) -> None:
        assert CONTOH.check({"15m": bar("63779")}).invalidated

    def test_short_dibatalkan_oleh_tutup_di_atas(self) -> None:
        inv = Invalidasi(
            decision=Arah.SHORT,
            levels=(Ambang("15m", Sisi.ABOVE, Decimal("63780")),),
        )
        assert inv.check({"15m": bar("63781")}).invalidated
        assert not inv.check({"15m": bar("63779")}).invalidated

    def test_tanda_terbalik_ditolak(self) -> None:
        """Salah tanda berarti ARUNA tidak pernah membatalkan signal yang
        sedang salah - satu-satunya signal yang perlu dibatalkan."""
        with pytest.raises(InvalidationError, match="terbalik"):
            Invalidasi(
                decision=Arah.LONG,
                levels=(Ambang("15m", Sisi.ABOVE, Decimal("63780")),),
            )
        with pytest.raises(InvalidationError, match="terbalik"):
            Invalidasi(
                decision=Arah.SHORT,
                levels=(Ambang("15m", Sisi.BELOW, Decimal("63780")),),
            )

    def test_satu_level_salah_arah_di_antara_yang_benar_tetap_ditolak(self) -> None:
        with pytest.raises(InvalidationError, match="terbalik"):
            Invalidasi(
                decision=Arah.LONG,
                levels=(
                    Ambang("15m", Sisi.BELOW, Decimal("63780")),
                    Ambang("1h", Sisi.ABOVE, Decimal("63000")),
                ),
            )


class TestPenutupanBukanSundutan:
    def test_tepat_di_level_belum_membatalkan(self) -> None:
        """"closes below 63,780" - 63.780 belum di bawah 63.780."""
        assert not CONTOH.check({"15m": bar("63780")}).invalidated

    def test_ambang_langsung_menolak_bar_berjalan(self) -> None:
        """``Ambang.triggered`` diekspor dan bisa dipanggil tanpa melewati
        ``check``, jadi penjaganya harus ada di kedua tempat.

        Test ini menembak lapisan yang lebih dalam daripada test berikutnya -
        tanpanya, penjaga di dalam ``triggered`` tidak pernah tersentuh dan
        bisa dicabut tanpa satu pun test berubah merah.
        """
        a = Ambang("15m", Sisi.BELOW, Decimal("63780"))

        assert not a.triggered(bar("60000", tutup=False))
        assert a.triggered(bar("60000"))

    def test_bar_berjalan_tidak_membatalkan(self) -> None:
        """Bar berjalan masih berubah sesudah dibaca. Membatalkan signal atas
        harga yang belum final adalah membatalkannya atas angka yang mungkin
        tidak pernah terjadi."""
        hasil = CONTOH.check({"15m": bar("63000", tutup=False)})

        assert not hasil.invalidated
        assert not hasil.conclusive
        assert "15m" in hasil.unchecked


class TestDataHilang:
    def test_bar_tidak_ada_bukan_berarti_aman(self) -> None:
        """Ketiadaan bukti kehancuran bukan bukti ketiadaan kehancuran."""
        hasil = CONTOH.check({})

        assert not hasil.invalidated
        assert not hasil.conclusive
        assert hasil.unchecked == ("15m",)
        assert "belum bisa diperiksa" in hasil.line()

    def test_semua_bar_ada_dan_aman_itu_kesimpulan(self) -> None:
        hasil = CONTOH.check({"15m": bar("64200")})

        assert not hasil.invalidated
        assert hasil.conclusive
        assert "belum terjadi" in hasil.line()

    def test_satu_level_hilang_membuat_seluruhnya_belum_pasti(self) -> None:
        inv = Invalidasi(
            decision=Arah.LONG,
            levels=(
                Ambang("15m", Sisi.BELOW, Decimal("63780")),
                Ambang("1h", Sisi.BELOW, Decimal("63000")),
            ),
        )
        hasil = inv.check({"15m": bar("64200")})

        assert not hasil.conclusive
        assert hasil.unchecked == ("1h",)

    def test_pembatalan_menang_atas_data_yang_hilang(self) -> None:
        """Satu level yang jelas tertembus sudah cukup; level lain yang belum
        bisa diperiksa tidak membatalkan kesimpulan itu."""
        # Yang tidak tersedia sengaja ditaruh LEBIH DULU. Kalau ia di belakang,
        # pemeriksaan berhenti sebelum sempat mencatatnya, dan test ini akan
        # lulus bahkan pada kode yang menyeret keraguan itu ke hasilnya.
        inv = Invalidasi(
            decision=Arah.LONG,
            levels=(
                Ambang("1h", Sisi.BELOW, Decimal("63000")),
                Ambang("15m", Sisi.BELOW, Decimal("63780")),
            ),
        )
        hasil = inv.check({"15m": bar("63500")})

        assert hasil.invalidated
        assert "INVALIDATED" in hasil.line()
        # Jawabannya final, dan tidak menyeret keraguan yang tidak relevan:
        # "dibatalkan, tapi ada satu yang belum saya periksa" mengundang
        # pembaca menimbang ulang sesuatu yang sudah selesai.
        assert hasil.conclusive
        assert hasil.unchecked == ()


class TestTerhadapEntry:
    def test_long_dengan_pembatalan_di_atas_entry_ditolak(self) -> None:
        """Signal yang terbit dan mati dalam satu tarikan napas terlihat di
        layar operator hanya sebagai signal berumur pendek tanpa sebab."""
        with pytest.raises(InvalidationError, match="batal sejak lahir"):
            against_entry(CONTOH, Decimal("63700"))

    def test_short_dengan_pembatalan_di_bawah_entry_ditolak(self) -> None:
        inv = Invalidasi(
            decision=Arah.SHORT,
            levels=(Ambang("15m", Sisi.ABOVE, Decimal("63780")),),
        )
        with pytest.raises(InvalidationError, match="batal sejak lahir"):
            against_entry(inv, Decimal("63900"))

    def test_pembatalan_tepat_di_entry_juga_ditolak(self) -> None:
        with pytest.raises(InvalidationError):
            against_entry(CONTOH, Decimal("63780"))

    def test_susunan_wajar_lolos(self) -> None:
        against_entry(CONTOH, Decimal("64120"))


class TestKalimat:
    def test_laporan_menyebut_level_dan_timeframenya(self) -> None:
        teks = "\n".join(CONTOH.report())

        assert "15m" in teks
        assert "63,780" in teks
        assert "tutup di bawah" in teks

    def test_kalimat_pendamping_ikut_dibawa(self) -> None:
        """Ia menjelaskan kenapa levelnya dipilih."""
        assert "struktur bullish patah" in "\n".join(CONTOH.report())

    def test_peta_sisi_menutup_kedua_arah(self) -> None:
        from aruna.decision import SISI_MEMATIKAN

        assert set(SISI_MEMATIKAN) == {Arah.LONG, Arah.SHORT}
        assert Sisi.BELOW.opposite is Sisi.ABOVE
        assert Sisi.ABOVE.opposite is Sisi.BELOW
