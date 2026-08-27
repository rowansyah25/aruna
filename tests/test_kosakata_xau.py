"""Kosakata XAU: BUY / SELL / NO SIGNAL. Tidak ada yang lain.

Spec melarang LONG, SHORT, dan WAIT di modul XAU. `suara.py` dikecualikan
karena ia justru BATAS yang menerjemahkan kosakata dewan - dan pengecualian
itu satu berkas, bukan sebuah kebiasaan.

Penjaga ini ada karena larangan kosakata adalah jenis aturan yang paling mudah
dilanggar tanpa sengaja: `Decision.WAIT` valid secara tipe, lolos type checker,
dan tidak akan pernah melempar apa pun saat dijalankan. Ia hanya akan muncul
di layar operator sebagai kata yang spec-nya larang.
"""

from __future__ import annotations

from pathlib import Path

XAU = Path(__file__).resolve().parent.parent / "src" / "aruna" / "xau"
PENERJEMAH = "suara.py"
TERLARANG = ("Decision.WAIT", "Decision.LONG", "Decision.SHORT")


class TestKosakata:
    def test_kosakata_futures_hanya_di_penerjemah(self) -> None:
        pelanggar: dict[str, list[str]] = {}
        for path in sorted(XAU.rglob("*.py")):
            if path.name == PENERJEMAH:
                continue
            isi = path.read_text(encoding="utf-8")
            kena = [k for k in TERLARANG if k in isi]
            if kena:
                pelanggar[path.name] = kena
        assert not pelanggar, (
            f"kosakata di luar BUY/SELL/NO_SIGNAL bocor ke modul XAU: "
            f"{pelanggar}. Terjemahkan lewat {PENERJEMAH}."
        )

    def test_penerjemahnya_memang_ada_dan_menerjemahkan(self) -> None:
        """Kalau penerjemahnya hilang, test di atas jadi hijau tanpa arti.

        Sebuah pemindai yang tidak menemukan apa-apa karena tidak ada yang
        dipindai terlihat sama persis dengan pemindai yang lulus.
        """
        isi = (XAU / PENERJEMAH).read_text(encoding="utf-8")
        assert "Decision.WAIT" in isi
        assert "Decision.NO_SIGNAL" in isi

    def test_ada_berkas_xau_yang_dipindai(self) -> None:
        """Penjaga kosong adalah penjaga yang tidur."""
        dipindai = [p.name for p in XAU.rglob("*.py") if p.name != PENERJEMAH]
        assert len(dipindai) >= 3, f"terlalu sedikit yang dipindai: {dipindai}"
