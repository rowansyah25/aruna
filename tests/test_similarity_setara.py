"""Optimasi `bandingkan` tidak boleh menggeser satu jawaban pun.

Sapuan yang memakai fungsi ini memutuskan apakah seluruh mesin ingatan dipakai
(PASAL 15.44). Optimasi yang menggeser jawabannya satu poin mengubah putusan
gerbang, dan itu bukan optimasi melainkan perubahan perilaku yang menyamar
sebagai optimasi.

**Apa yang dioptimasi, dan kenapa.** Terprofil 2026-08-22 pada 900 ingatan:
`bandingkan` memakan **99,6%** waktu sapuan, dengan 39,9 juta panggilan
`diketahui` dan **59,5 juta** `str.upper` - seluruhnya menormalkan teks yang
sama berulang-ulang. Fungsi ini dipanggil n kali lipat n sementara sidiknya cuma
ada n, jadi normalisasinya dipindah ke konstruksi `Sidik`.

Diadu atas 80.000 pasangan sidik produksi saat optimasinya ditulis: nol
perbedaan. Berkas ini yang menjaga angka itu tetap nol.
"""

from __future__ import annotations

import itertools

import pytest

from aruna.memory.dimensions import (
    UNKNOWN,
    Dimensi,
    diketahui,
    normalkan,
    sama,
    sama_ternormalkan,
)
from aruna.memory.fingerprint import Sidik
from aruna.memory.similarity import BOBOT, Kemiripan, bandingkan

_TOTAL_BOBOT = sum(BOBOT.values())

#: Bentuk nilai yang benar-benar muncul di korpus, plus yang menjebak.
NILAI = [
    "TRENDING", "TRENDING_BULLISH", "TRENDING_BEARISH", "RANGING",
    "BREAKOUT", "BREAKDOWN", "HIGH_VOLATILITY", "LOW_VOLATILITY",
    UNKNOWN, "unknown", " UNKNOWN ", "", "   ", None,
    "high", "HIGH", " High ", "BTC/USDT", "0", 0, 0.0, False, True,
]


def _bandingkan_lama(a: Sidik, b: Sidik) -> Kemiripan:
    """Salinan persis versi sebelum optimasi. **Jangan disunting** - gunanya
    justru menjadi pembanding yang tidak ikut berubah."""
    cocok: list[Dimensi] = []
    beda: list[Dimensi] = []
    tak_terbaca: list[Dimensi] = []

    for d in Dimensi:
        kiri, kanan = a.nilai.get(d), b.nilai.get(d)
        if not diketahui(kiri) or not diketahui(kanan):
            tak_terbaca.append(d)
            continue
        (cocok if sama(kiri, kanan) else beda).append(d)

    terbaca = sum(BOBOT.get(d, 0) for d in (*cocok, *beda))
    setuju = sum(BOBOT.get(d, 0) for d in cocok)

    return Kemiripan(
        skor=round(setuju * 100 / terbaca) if terbaca else 0,
        cakupan=round(terbaca * 100 / _TOTAL_BOBOT) if _TOTAL_BOBOT else 0,
        cocok=tuple(cocok),
        beda=tuple(beda),
        tak_terbaca=tuple(tak_terbaca),
    )


def _sidik(*nilai) -> Sidik:
    """Sidik dengan nilai berputar mengisi seluruh dimensi."""
    dims = list(Dimensi)
    return Sidik(nilai={d: nilai[i % len(nilai)] for i, d in enumerate(dims)})


class TestNormalkanSepakatDenganDiketahui:
    """Dua definisi "terbaca" yang melenceng membuat sebagian ingatan
    terbandingkan di satu jalur dan terbuang di jalur lain, tanpa satu pun
    galat."""

    @pytest.mark.parametrize("nilai", NILAI)
    def test_none_persis_saat_tidak_diketahui(self, nilai) -> None:
        assert (normalkan(nilai) is None) is (not diketahui(nilai))

    def test_nol_tetap_dianggap_terbaca(self) -> None:
        """`confidence=0` berarti council menilai dan hasilnya nol - kelas
        kesalahan yang sama dengan `side='FLAT'` yang truthy."""
        assert normalkan(0) is not None
        assert normalkan(0.0) is not None
        assert normalkan("0") is not None

    def test_unknown_apa_pun_bentuknya_ditolak(self) -> None:
        for bentuk in (UNKNOWN, "unknown", " UNKNOWN ", " unknown "):
            assert normalkan(bentuk) is None, bentuk


class TestSamaTernormalkanSetara:
    @pytest.mark.parametrize(
        ("a", "b"),
        list(itertools.product(NILAI, repeat=2)),
    )
    def test_jawabannya_sama(self, a, b) -> None:
        """Untuk nilai yang keduanya terbaca, `sama_ternormalkan` atas bentuk
        yang sudah dinormalkan harus menjawab persis seperti `sama` atas
        bentuk mentahnya."""
        ka, kb = normalkan(a), normalkan(b)
        if ka is None or kb is None:
            return
        assert sama_ternormalkan(ka, kb) is sama(a, b)

    def test_lintas_generasi_regime_tetap_cocok(self) -> None:
        """9.897 ingatan memuat `TRENDING` tanpa arah; menolak kecocokan lintas
        generasi membuang seluruh korpus itu."""
        assert sama_ternormalkan("TRENDING", "TRENDING_BULLISH")

    def test_arah_berlawanan_tetap_tidak_cocok(self) -> None:
        assert not sama_ternormalkan("TRENDING_BULLISH", "TRENDING_BEARISH")


class TestBandinganSetara:
    """Yang paling menentukan: keluarannya bidang per bidang."""

    @pytest.mark.parametrize(
        ("kiri", "kanan"),
        [
            (("TRENDING",), ("TRENDING_BULLISH",)),
            (("TRENDING_BULLISH",), ("TRENDING_BEARISH",)),
            ((UNKNOWN,), ("RANGING",)),
            (("", "  "), (UNKNOWN, None)),
            (("HIGH", "0"), ("high", 0)),
            ((None,), (None,)),
            (("BREAKOUT", "RANGING", UNKNOWN), ("BREAKDOWN", "RANGING", "HIGH")),
        ],
    )
    def test_bidangnya_identik(self, kiri, kanan) -> None:
        a, b = _sidik(*kiri), _sidik(*kanan)
        lama, baru = _bandingkan_lama(a, b), bandingkan(a, b)

        assert baru.skor == lama.skor
        assert baru.cakupan == lama.cakupan
        assert set(baru.cocok) == set(lama.cocok)
        assert set(baru.beda) == set(lama.beda)
        assert set(baru.tak_terbaca) == set(lama.tak_terbaca)

    def test_sapuan_menyeluruh_atas_kombinasi_nilai(self) -> None:
        """Bukan kasus yang kupikirkan, melainkan seluruh silang dari daftar
        bentuk yang benar-benar muncul di korpus."""
        sidik = [_sidik(v) for v in NILAI]
        beda = 0
        for a, b in itertools.product(sidik, sidik):
            lama, baru = _bandingkan_lama(a, b), bandingkan(a, b)
            if (lama.skor, lama.cakupan) != (
                baru.skor,
                baru.cakupan,
            ) or set(lama.cocok) != set(baru.cocok):
                beda += 1

        assert beda == 0, f"{beda} dari {len(sidik) ** 2} pasangan berbeda"

    def test_semua_tak_terbaca_menghasilkan_nol(self) -> None:
        """Jawaban apa pun yang bukan nol di situ berarti ARUNA mengaku
        mengenali kondisi yang tidak pernah ia lihat."""
        kosong = _sidik(UNKNOWN)

        assert bandingkan(kosong, kosong).skor == 0


class TestSidikMenormalkanSekali:
    def test_normal_sejajar_dengan_nilai(self) -> None:
        s = _sidik("TRENDING", UNKNOWN, " high ")

        for d, mentah in s.nilai.items():
            assert s.normal[d] == normalkan(mentah)

    def test_kesamaan_sidik_tidak_ikut_berubah(self) -> None:
        """`normal` bidang turunan. Kalau ia ikut menentukan kesamaan, ada dua
        sumber kebenaran untuk satu pertanyaan."""
        a, b = _sidik("TRENDING"), _sidik("TRENDING")

        assert a == b

    def test_normalisasinya_tidak_dihitung_saat_membandingkan(self) -> None:
        """Penjaga AST atas `bandingkan`: ia harus membaca `normal`, bukan
        menormalkan sendiri. Kalau `diketahui` atau `sama` kembali ke dalamnya,
        kerja n-kuadrat itu kembali juga."""
        import ast
        import inspect

        from aruna.memory import similarity

        pohon = ast.parse(inspect.getsource(similarity.bandingkan).lstrip())
        dipanggil = {
            n.func.id
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        atribut = {
            n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)
        }

        assert "diketahui" not in dipanggil
        assert "sama" not in dipanggil
        assert "normal" in atribut
        assert "upper" not in atribut
        assert "strip" not in atribut
