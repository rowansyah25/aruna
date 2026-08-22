"""PASAL 14.2 dan 14.43: WAIT bukan keputusan.

Operator yang menerima "WAIT" tetap tidak tahu harus berbuat apa. Yang boleh
menunggu adalah *waktu masuknya* - keputusannya sendiri harus LONG, SHORT, atau
NO SIGNAL. Bedanya bukan tata bahasa: "WAIT" menyerahkan kembali pertanyaannya
kepada operator, sedangkan "LONG, tunggu pullback" menjawabnya dan menambahkan
syarat.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.decision.final import TERLARANG, FinalError, arah_dari, finalize
from aruna.decision.score import Arah
from aruna.decision.timing import Syarat, Timing, TimingError


class TestBentukKeputusan:
    @pytest.mark.parametrize(
        ("masukan", "arah"),
        [
            ("BUY", Arah.LONG),
            ("LONG", Arah.LONG),
            ("SELL", Arah.SHORT),
            ("SHORT", Arah.SHORT),
            ("NO_SIGNAL", Arah.NO_SIGNAL),
            ("NO SIGNAL", Arah.NO_SIGNAL),
        ],
    )
    def test_arah_yang_dikenali(self, masukan: str, arah: Arah) -> None:
        assert arah_dari(masukan) is arah

    def test_wait_ditolak_bukan_diterjemahkan(self) -> None:
        """Menerjemahkan WAIT diam-diam menjadi NO SIGNAL akan menyembunyikan
        lapisan yang masih mengeluarkan penundaan. Yang dibutuhkan adalah
        kesalahan yang terlihat, supaya pemanggilnya diperbaiki."""
        with pytest.raises(FinalError):
            arah_dari("WAIT")

    def test_flat_juga_ditolak(self) -> None:
        """``side='FLAT'`` adalah bentuk WAIT yang lain di jalur futures, dan
        ia truthy - kelas kesalahan yang sudah empat kali muncul di sistem
        ini."""
        with pytest.raises(FinalError):
            arah_dari("FLAT")

    def test_yang_tidak_dikenali_ditolak(self) -> None:
        with pytest.raises(FinalError):
            arah_dari("MUNGKIN")

    @pytest.mark.parametrize("token", ["WAIT", "FLAT", "HOLD", "NETRAL"])
    def test_penundaan_dan_nilai_asing_dibedakan_di_pesannya(
        self, token: str
    ) -> None:
        """Keduanya ditolak, dan keduanya adalah masalah yang berbeda.

        ``WAIT`` berarti sebuah lapisan masih mengeluarkan penundaan - ada
        kode yang harus diperbaiki, dan PASAL 14.43 menyebut namanya.
        ``MUNGKIN`` berarti nilai yang tidak dikenal sama sekali - mungkin enum
        baru, mungkin data rusak.

        Tanpa test ini ``TERLARANG`` tidak menambah apa pun: kedua token itu
        toh tidak ada di peta, jadi keduanya tetap ditolak dan pembaca lognya
        tidak pernah tahu mana yang mana. Cabut-uji pertama membuktikannya -
        daftarnya bisa dikosongkan dan seluruh test tetap hijau.
        """
        with pytest.raises(FinalError, match=r"14\.43") as terlarang:
            arah_dari(token)
        with pytest.raises(FinalError) as asing:
            arah_dari("MUNGKIN")

        assert "14.43" not in str(asing.value)
        assert str(terlarang.value) != str(asing.value)

    def test_kosong_ditolak(self) -> None:
        """``None`` bukan NO SIGNAL. Yang pertama berarti tidak ada yang
        memutuskan; yang kedua berarti ada yang memutuskan untuk tidak
        mengambil posisi."""
        with pytest.raises(FinalError):
            arah_dari(None)

    def test_daftar_terlarangnya_tidak_kosong(self) -> None:
        assert "WAIT" in TERLARANG
        assert "FLAT" in TERLARANG

    def test_enum_arah_diteruskan_apa_adanya(self) -> None:
        assert arah_dari(Arah.SHORT) is Arah.SHORT

    def test_objek_ber_value_dibaca(self) -> None:
        """Sisi posisi datang sebagai StrEnum di jalur futures, bukan str."""
        from types import SimpleNamespace

        assert arah_dari(SimpleNamespace(value="LONG")) is Arah.LONG

    def test_huruf_kecil_dan_spasi_tidak_menggagalkan(self) -> None:
        assert arah_dari("  long  ") is Arah.LONG


class TestPenundaanPindahKeTiming:
    def test_long_boleh_menunggu_pullback(self) -> None:
        """PASAL 14.43 memberi contohnya sendiri: Decision LONG, Entry Timing
        WAIT FOR PULLBACK."""
        r = finalize(
            "BUY",
            timing=Timing.PULLBACK,
            condition=Syarat(
                zone_low=Decimal("63900"), zone_high=Decimal("64200")
            ),
        )

        assert r.decision is Arah.LONG
        assert r.timing is Timing.PULLBACK

    def test_masuk_sekarang_tidak_perlu_syarat(self) -> None:
        assert finalize("SELL", timing=Timing.NOW).timing is Timing.NOW

    def test_no_signal_tidak_boleh_membawa_timing(self) -> None:
        """Waktu masuk untuk posisi yang tidak diambil adalah keterangan yang
        tidak menerangkan apa pun, dan ia terbaca sebagai ajakan."""
        with pytest.raises(FinalError):
            finalize("NO SIGNAL", timing=Timing.NOW)

    def test_arah_wajib_punya_waktu_masuk(self) -> None:
        """PASAL 14.19, dijaga :class:`Rencana` sendiri. Arah tanpa waktu masuk
        adalah setengah jawaban, dan setengah jawaban di sini berarti operator
        menebak sisanya."""
        with pytest.raises(TimingError):
            finalize("SELL")

    def test_menunggu_tanpa_syarat_ditolak(self) -> None:
        """PASAL 14.20: pesan yang menyuruh menunggu tanpa menyebut apa yang
        ditunggu adalah pesan yang tidak bisa ditindaklanjuti."""
        with pytest.raises(TimingError):
            finalize("BUY", timing=Timing.PULLBACK)

    def test_no_signal_tanpa_timing_sah(self) -> None:
        assert finalize("NO SIGNAL").decision is Arah.NO_SIGNAL
