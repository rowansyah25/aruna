"""Waktu masuk dan syaratnya (PASAL 14.19, 14.20).

Arah dan waktu adalah dua pertanyaan berbeda. Yang diuji di sini: timing tidak
pernah mengubah arah, timing yang menunggu wajib menyebut apa yang ditunggu,
dan kata-katanya boleh keluar lewat penjaga kosakata PASAL 1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.decision import (
    Arah,
    Rencana,
    Syarat,
    Timing,
    TimingError,
    Umur,
)
from aruna.notify.verdict import InternalVocabularyLeak, guard_public

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

ZONA = Syarat(
    zone_low=Decimal("64000"),
    zone_high=Decimal("64100"),
    confirmation="muncul konfirmasi bullish",
)


class TestArahTidakBerubah:
    def test_timing_tidak_mengubah_keputusan(self) -> None:
        """PASAL 14.19: "decision tetap LONG, tetapi timing entry belum
        optimal." Sistem yang menurunkan LONG menjadi NO SIGNAL karena harganya
        tanggung sedang menjawab pertanyaan yang berbeda."""
        for t in (Timing.PULLBACK, Timing.BREAKOUT, Timing.REJECTION):
            r = Rencana(Arah.LONG, t, ZONA)
            assert r.final() is Arah.LONG

        assert Rencana(Arah.LONG, Timing.NOW).final() is Arah.LONG

    def test_final_hanya_tiga_kemungkinan(self) -> None:
        """"Jika sistem membutuhkan keputusan final tanpa menunggu:
        LONG / SHORT / NO SIGNAL." """
        semua = {
            Rencana(Arah.LONG, Timing.PULLBACK, ZONA).final(),
            Rencana(Arah.SHORT, Timing.NOW).final(),
            Rencana(Arah.NO_SIGNAL).final(),
        }
        assert semua == {Arah.LONG, Arah.SHORT, Arah.NO_SIGNAL}

    def test_menunggu_ditandai_terpisah_dari_arah(self) -> None:
        assert Rencana(Arah.LONG, Timing.PULLBACK, ZONA).waiting
        assert not Rencana(Arah.LONG, Timing.NOW).waiting
        assert not Rencana(Arah.NO_SIGNAL).waiting


class TestSyaratWajib:
    def test_menunggu_tanpa_syarat_ditolak(self) -> None:
        """PASAL 14.20. "Tunggu pullback" tanpa zona harga adalah perintah
        menunggu sesuatu yang tidak disebutkan."""
        for t in (Timing.PULLBACK, Timing.BREAKOUT, Timing.REJECTION):
            with pytest.raises(TimingError, match=r"14\.20"):
                Rencana(Arah.LONG, t)

    def test_masuk_sekarang_yang_bersyarat_ditolak(self) -> None:
        with pytest.raises(TimingError, match="bukan masuk sekarang"):
            Rencana(Arah.LONG, Timing.NOW, ZONA)

    def test_arah_tanpa_timing_ditolak(self) -> None:
        with pytest.raises(TimingError, match="tanpa waktu masuk"):
            Rencana(Arah.SHORT)

    def test_no_signal_tidak_punya_waktu_masuk(self) -> None:
        """Pertanyaan "kapan masuk" tidak punya jawaban ketika tidak ada yang
        dimasuki."""
        with pytest.raises(TimingError, match="tidak ada yang dimasuki"):
            Rencana(Arah.NO_SIGNAL, Timing.PULLBACK, ZONA)

    def test_no_signal_polos_sah(self) -> None:
        assert Rencana(Arah.NO_SIGNAL).timing is None

    def test_no_signal_bukan_anggota_timing(self) -> None:
        """Menaruhnya di enum timing mencampur keputusan dengan waktu - persis
        pencampuran yang PASAL 14.19 tulis untuk dihindari."""
        assert "NO SIGNAL" not in {t.value for t in Timing}


class TestSyarat:
    def test_syarat_kosong_ditolak(self) -> None:
        """Syarat yang tidak pernah terpenuhi dan tidak pernah gagal hanya
        membuat signal menggantung sampai kedaluwarsa."""
        with pytest.raises(TimingError, match="syarat kosong"):
            Syarat()
        with pytest.raises(TimingError, match="syarat kosong"):
            Syarat(confirmation="   ")

    def test_setengah_zona_ditolak(self) -> None:
        """"Harga kembali ke 64.000-" akan dibaca sebagai apa pun di bawahnya,
        termasuk nol."""
        with pytest.raises(TimingError, match="satu sisi"):
            Syarat(zone_low=Decimal("64000"))
        with pytest.raises(TimingError, match="satu sisi"):
            Syarat(zone_high=Decimal("64100"))

    def test_zona_terbalik_ditolak(self) -> None:
        with pytest.raises(TimingError, match="terbalik"):
            Syarat(zone_low=Decimal("64100"), zone_high=Decimal("64000"))

    def test_zona_sama_persis_sah(self) -> None:
        s = Syarat(zone_low=Decimal("64000"), zone_high=Decimal("64000"))
        assert "64,000" in s.line()

    def test_hanya_konfirmasi_sah(self) -> None:
        assert Syarat(confirmation="candle 15m menutup di atas 64.500").line()

    def test_dua_bagian_digabung_dengan_dan(self) -> None:
        """Contoh PASAL 14.20: zona harga AND konfirmasi bullish."""
        teks = ZONA.line()

        assert "64,000 - 64,100" in teks
        assert " DAN " in teks
        assert "bullish" in teks


class TestKalimat:
    def test_lolos_penjaga_kosakata_pasal_1(self) -> None:
        """Menuliskan "WAIT FOR PULLBACK" apa adanya akan membuat setiap pesan
        dengan entry tertunda ditolak penjaganya sendiri."""
        for t in Timing:
            guard_public(t.value)

    def test_penjaganya_memang_menolak_versi_inggrisnya(self) -> None:
        """Bukti bahwa test di atas menguji sesuatu: kalimat aslinya ditolak."""
        with pytest.raises(InternalVocabularyLeak):
            guard_public("WAIT FOR PULLBACK")

    def test_laporan_menyebut_arahnya_tidak_berubah(self) -> None:
        teks = "\n".join(Rencana(Arah.LONG, Timing.PULLBACK, ZONA).report())

        assert "LONG" in teks
        assert "TUNGGU PULLBACK" in teks
        assert "Arahnya tidak berubah" in teks

    def test_laporan_menyebut_syaratnya(self) -> None:
        teks = "\n".join(Rencana(Arah.LONG, Timing.PULLBACK, ZONA).report())

        assert "64,000 - 64,100" in teks

    def test_syarat_selalu_membawa_batas_waktunya(self) -> None:
        """PASAL 14.20: "Jika kondisi tidak terjadi: Signal dapat EXPIRE."
        Syarat tanpa batas waktu terbaca seperti syarat yang berlaku selamanya.
        """
        r = Rencana(Arah.LONG, Timing.PULLBACK, ZONA)

        polos = "\n".join(r.report())
        assert "kedaluwarsa" in polos

        umur = Umur(published_at=NOW, horizon="1h")
        berumur = "\n".join(r.report(umur, NOW + timedelta(minutes=15)))
        assert "sisa 45 menit" in berumur

    def test_masuk_sekarang_tidak_menyebut_syarat(self) -> None:
        teks = "\n".join(Rencana(Arah.LONG, Timing.NOW).report())

        assert "Syarat" not in teks
        assert "Arahnya tidak berubah" not in teks

    def test_laporan_lolos_penjaga_kosakata(self) -> None:
        for r in (
            Rencana(Arah.LONG, Timing.PULLBACK, ZONA),
            Rencana(Arah.SHORT, Timing.BREAKOUT, ZONA),
            Rencana(Arah.LONG, Timing.NOW),
            Rencana(Arah.NO_SIGNAL),
        ):
            guard_public("\n".join(r.report()))
