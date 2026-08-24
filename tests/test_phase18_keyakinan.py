"""Batas dan peringatan atas keyakinan (bagian 18.22 - 18.24, 18.41).

Tiga celah sekaligus, dan ketiganya berbagi satu gagasan: **confidence bukan
certainty**. Keyakinan 85% tidak menyatakan "85% pasti profit"; ia menyatakan
seberapa jauh bukti yang tersedia mendukung kesimpulan.
"""

from __future__ import annotations

from aruna.signals.keyakinan import (
    AMBANG_MUTU,
    Peringatan,
    PitaMutu,
    langit_langit,
    periksa_keyakinan,
    pita,
)
from aruna.signals.quality import MIN_QUALITY


class TestPitaMutu:
    """Bagian 18.41."""

    def test_tiap_pita_punya_namanya(self) -> None:
        assert pita(95) is PitaMutu.EXCELLENT
        assert pita(85) is PitaMutu.HIGH
        assert pita(75) is PitaMutu.GOOD
        assert pita(65) is PitaMutu.MODERATE
        assert pita(55) is PitaMutu.LOW
        assert pita(40) is PitaMutu.POOR

    def test_tak_terukur_bukan_POOR(self) -> None:
        """Skor yang tidak bisa dihitung dan skor yang dihitung lalu jelek
        adalah dua hal yang sangat berbeda. Menyamakannya membuat tiap sinyal
        yang datanya kurang terlihat buruk alih-alih belum bisa dinilai."""
        assert pita(None) is None

    def test_ambang_layak_sejalan_dengan_gerbang(self) -> None:
        """**Satu titik yang bukan selera.** Pita yang lulus gerbang dan pita
        yang bernama layak harus berpindah bersama - kalau tidak, laporan akan
        menyebut sebuah sinyal "GOOD" sambil gerbangnya menolaknya."""
        moderate = next(b for b, n in AMBANG_MUTU if n is PitaMutu.MODERATE)

        assert moderate == MIN_QUALITY

    def test_urutannya_menurun(self) -> None:
        batas = [b for b, _ in AMBANG_MUTU]

        assert batas == sorted(batas, reverse=True)


class TestLangitLangit:
    """Bagian 18.23."""

    def test_yang_terendah_yang_mengikat(self) -> None:
        """Keyakinan tidak bisa lebih kuat daripada penopangnya yang paling
        lemah."""
        batas, sebab = langit_langit(mutu=88, keyakinan_rezim=42)

        assert batas == 42
        assert "rezim" in sebab

    def test_contoh_spec_apa_adanya(self) -> None:
        """Bagian 18.23: "Regime Confidence 42% - maka Signal Confidence tidak
        boleh menjadi 95% tanpa alasan khusus"."""
        hasil = periksa_keyakinan(95, mutu=88, keyakinan_rezim=42)

        assert hasil.keyakinan == 42
        assert Peringatan.MELAMPAUI_LANGIT_LANGIT in hasil.peringatan

    def test_keyakinan_di_bawah_batas_tidak_disentuh(self) -> None:
        hasil = periksa_keyakinan(38, mutu=88, keyakinan_rezim=42)

        assert hasil.keyakinan == 38
        assert not hasil.peringatan

    def test_tidak_pernah_menaikkan(self) -> None:
        """Keyakinan yang NAIK karena pemeriksaan mutu berarti pemeriksaannya
        menjadi sumber keyakinan, dan itu lingkaran."""
        hasil = periksa_keyakinan(20, mutu=99, keyakinan_rezim=99)

        assert hasil.keyakinan == 20

    def test_tanpa_penopang_tidak_membatasi(self) -> None:
        """`None` berarti tidak ada yang bisa membatasi - bukan berarti bebas.
        Keadaan itu jarang dan pantas terlihat apa adanya, bukan disamarkan
        sebagai batas nol."""
        batas, sebab = langit_langit(mutu=None, keyakinan_rezim=None)

        assert batas is None
        assert "tidak ada penopang" in sebab
        assert periksa_keyakinan(91, mutu=None, keyakinan_rezim=None).keyakinan == 91


class TestKeyakinanPalsu:
    """Bagian 18.22."""

    def test_contoh_spec_apa_adanya(self) -> None:
        """Bagian 18.22: Confidence 92%, Evidence Quality 51, Risk 87."""
        hasil = periksa_keyakinan(92, mutu=51, keyakinan_rezim=95, risiko=87)

        assert Peringatan.KEYAKINAN_PALSU in hasil.peringatan

    def test_ketiganya_harus_bersamaan(self) -> None:
        """**Ini yang membuatnya satu KEADAAN, bukan tiga angka yang kebetulan
        berdekatan.** Keyakinan tinggi dengan bukti kuat wajar; bukti lemah
        dengan keyakinan rendah jujur; risiko tinggi dengan keduanya baik
        adalah keputusan sadar.
        """
        bukti_kuat = periksa_keyakinan(92, mutu=88, keyakinan_rezim=95, risiko=87)
        keyakinan_rendah = periksa_keyakinan(40, mutu=51, keyakinan_rezim=95, risiko=87)
        risiko_wajar = periksa_keyakinan(92, mutu=51, keyakinan_rezim=95, risiko=30)

        for hasil in (bukti_kuat, keyakinan_rendah, risiko_wajar):
            assert Peringatan.KEYAKINAN_PALSU not in hasil.peringatan

    def test_bukti_yang_lulus_gerbang_tidak_disebut_lemah(self) -> None:
        """Kalau ia lulus gerbang, keluhannya milik gerbang - bukan milik
        peringatan ini."""
        hasil = periksa_keyakinan(
            92, mutu=MIN_QUALITY, keyakinan_rezim=95, risiko=95
        )

        assert Peringatan.KEYAKINAN_PALSU not in hasil.peringatan

    def test_risiko_tak_terukur_tidak_memicu(self) -> None:
        """Risiko yang belum dinilai bukan risiko tinggi."""
        hasil = periksa_keyakinan(92, mutu=51, keyakinan_rezim=95, risiko=None)

        assert Peringatan.KEYAKINAN_PALSU not in hasil.peringatan


class TestAlasannyaSelaluDisebut:
    def test_pembatasan_menyebut_angkanya(self) -> None:
        """Keyakinan yang dipotong tanpa menyebut sebabnya tidak bisa
        dibantah."""
        hasil = periksa_keyakinan(95, mutu=88, keyakinan_rezim=42)

        assert any("42" in a for a in hasil.alasan)
        assert any("95" in a for a in hasil.alasan)

    def test_keyakinan_palsu_menyebut_ketiganya(self) -> None:
        hasil = periksa_keyakinan(92, mutu=51, keyakinan_rezim=95, risiko=87)
        teks = " ".join(hasil.alasan)

        assert "92" in teks and "51" in teks and "87" in teks
