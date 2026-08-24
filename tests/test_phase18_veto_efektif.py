"""Veto yang ditolak lalu ternyata benar (bagian 18.13, 18.50).

**Celah 5, dan separuhnya ternyata sudah ada.** `successful_objections` sudah
mengukur keberatan yang dikesampingkan lalu terbukti benar, per penuduh dan per
dasar - dan ia sudah berjalan lewat `learning.review()`. Yang tidak punya
padanan adalah veto: ia tercatat di autopsy sebagai `rejected_vetoes`, tapi tak
pernah diagregasi menjadi angka.

Bentuknya sengaja kembar dengan `ObjectionRecord`, karena pertanyaannya memang
kembar: sebuah keberatan yang dikesampingkan lalu ternyata benar adalah titik
buta, entah ia datang sebagai objection atau sebagai veto.
"""

from __future__ import annotations

from typing import Any

from aruna.learning.autopsy import (
    ObjectionRecord,
    VetoRecord,
    successful_objections,
    veto_ditegakkan,
    vindicated_vetoes,
)


def _v(reason: str, benar: bool | None) -> dict[str, Any]:
    return {"reason": reason, "direction_correct": benar}


def _u(
    reason: str, *, naik: float | None, turun: float | None
) -> dict[str, Any]:
    return {
        "reason": reason,
        "max_favourable_pct": naik,
        "max_adverse_pct": turun,
    }


class TestVetoYangTerbuktiBenar:
    def test_veto_ditolak_lalu_keputusannya_salah(self) -> None:
        """Contoh bagian 18.13: veto atas volatilitas ekstrem ditolak, lalu
        pasar mengalami flash crash. Vetonya benar."""
        hasil = vindicated_vetoes([
            _v("EXTREME_VOLATILITY", False),
            _v("EXTREME_VOLATILITY", False),
            _v("EXTREME_VOLATILITY", True),
        ])

        assert hasil[0].reason == "EXTREME_VOLATILITY"
        assert hasil[0].raised == 3
        assert hasil[0].vindicated == 2
        assert hasil[0].vindication_rate == round(2 / 3, 4)

    def test_veto_yang_tidak_memberi_manfaat(self) -> None:
        """Sebaliknya: ditolak, dan keputusannya ternyata benar. Vetonya tidak
        menambah apa-apa."""
        hasil = vindicated_vetoes([_v("TRADING_HALT", True), _v("TRADING_HALT", True)])

        assert hasil[0].vindicated == 0
        assert hasil[0].vindication_rate == 0.0

    def test_diurutkan_menurut_pembenaran(self) -> None:
        """Yang dicari titik buta yang terus ditunjukkan dan terus
        dikesampingkan - jadi yang paling sering benar harus di atas."""
        hasil = vindicated_vetoes([
            _v("JARANG", False),
            *[_v("SERING", False) for _ in range(3)],
        ])

        assert [r.reason for r in hasil] == ["SERING", "JARANG"]


class TestBatasYangJujur:
    def test_hasil_yang_belum_diketahui_dilewati(self) -> None:
        """Prediksi yang belum tuntas tidak bisa membenarkan maupun
        menyalahkan sebuah veto."""
        hasil = vindicated_vetoes([_v("X", None), _v("X", False)])

        assert hasil[0].raised == 1

    def test_veto_tanpa_alasan_dilewati(self) -> None:
        assert vindicated_vetoes([_v("", False)]) == []

    def test_kosong_tidak_meledak(self) -> None:
        assert vindicated_vetoes([]) == []

    def test_hanya_yang_DITOLAK_yang_bisa_diukur(self) -> None:
        """**Batas yang paling penting di sini, dan ia disengaja.**

        Veto yang DITEGAKKAN menghentikan sinyalnya, jadi tidak ada hasil untuk
        dibandingkan - kita tidak akan pernah tahu apa yang akan terjadi.
        Menghitungnya sebagai "efektif" berarti memberi nilai penuh kepada tiap
        veto yang tak pernah diuji, dan itu justru cara membuat veto yang
        berlebihan terlihat sempurna.

        Test ini menjaga docstringnya menyebut batas itu, karena angka
        "efektivitas veto 75%" yang dibaca tanpa batasnya akan disalahartikan.
        """
        import inspect

        doc = inspect.getdoc(vindicated_vetoes) or ""

        assert "DITOLAK" in doc
        assert "ditegakkan" in doc


class TestVetoDitegakkan:
    """Bagian 18.13, dan ini yang benar-benar menyala di ARUNA.

    Terukur 2026-08-24: dari 279 veto, **nol** pernah ditolak. Ukuran
    `vindicated_vetoes` benar dan tidak akan pernah menyala. Yang ini menjawab
    contoh spec-nya apa adanya - veto atas volatilitas ekstrem, lalu pasar
    bergejolak - dan hasilnya di produksi::

        SEVERE_ANOMALY      579 ditegakkan, 421 diikuti gejolak (72,7%)
        EXTREME_VOLATILITY  121 ditegakkan, 121 diikuti gejolak (100%)
    """

    def test_gejolak_sesudah_veto_terhitung(self) -> None:
        hasil = veto_ditegakkan([
            _u("EXTREME_VOLATILITY", naik=0.2, turun=-9.0),
            _u("EXTREME_VOLATILITY", naik=8.0, turun=-0.1),
            _u("EXTREME_VOLATILITY", naik=0.1, turun=-0.1),
        ])

        assert hasil[0].ditegakkan == 3
        assert hasil[0].diikuti_gejolak == 2

    def test_yang_terjauh_yang_dihitung_bukan_arahnya(self) -> None:
        """Veto menahan sebelum ada posisi, jadi tidak ada "arah yang benar" -
        yang berarti seberapa jauh pasar bergerak, ke mana pun."""
        naik = veto_ditegakkan([_u("X", naik=9.0, turun=-0.1)])
        turun = veto_ditegakkan([_u("X", naik=0.1, turun=-9.0)])

        assert naik[0].diikuti_gejolak == turun[0].diikuti_gejolak == 1

    def test_horizon_yang_belum_tuntas_dilewati(self) -> None:
        """Jangkauan yang tidak tercatat bukan "tidak bergejolak" - ia belum
        bisa dijawab, dan menghitungnya sebagai tenang membuat tiap veto
        terbaru terlihat berlebihan."""
        hasil = veto_ditegakkan([
            _u("X", naik=None, turun=None),
            _u("X", naik=9.0, turun=-0.1),
        ])

        assert hasil[0].ditegakkan == 1

    def test_ambangnya_dipinjam_dari_ghost_signal(self) -> None:
        """Pertanyaannya sama: mulai dari berapa sebuah gerak layak disebut
        gerak. Ambang kedua di sini berarti laporan yang menyebut "veto
        efektif" dan "peluang terlewat" menghitung kejadian yang sama dua
        arah."""
        from aruna.learning.autopsy import AMBANG_GERAK_VETO
        from aruna.learning.counterfactual import GHOST_THRESHOLD_PCT

        assert AMBANG_GERAK_VETO == GHOST_THRESHOLD_PCT

    def test_batasnya_ikut_terbawa_ke_laporan(self) -> None:
        """**Angka 100% yang dibaca tanpa batasnya akan disalahartikan.**
        Gerak besar sesudah veto BUKAN bukti veto itu menyelamatkan uang -
        ARUNA menganalisis saja, tidak ada posisi yang terhindar. Yang terukur
        korelasi, bukan sebab-akibat, dan itu harus ikut ke mana pun angkanya
        pergi."""
        d = veto_ditegakkan([_u("X", naik=9.0, turun=0.0)])[0].to_dict()

        assert "korelasi" in d["caveat"]

    def test_dua_ukuran_veto_menjawab_hal_berbeda(self) -> None:
        """Yang satu "ditolak lalu ternyata benar", yang lain "ditegakkan lalu
        pasarnya bergejolak". Menyatukannya menjadi satu angka "efektivitas
        veto" akan menyembunyikan bahwa yang pertama tak pernah punya data."""
        assert VetoRecord(reason="X").vindication_rate is None
        assert veto_ditegakkan([])==[]


class TestKembarDenganObjection:
    def test_bentuknya_sama(self) -> None:
        """Dua bentuk yang berbeda untuk satu pertanyaan membuat laporannya
        tidak bisa disandingkan - dan operator yang harus mengingat mana yang
        mana."""
        import dataclasses

        veto = {f.name for f in dataclasses.fields(VetoRecord)}
        obj = {f.name for f in dataclasses.fields(ObjectionRecord)}

        assert {"raised", "vindicated"} <= veto
        assert {"raised", "vindicated"} <= obj

    def test_aturan_pembenarannya_sama(self) -> None:
        """Keduanya terbukti benar tepat ketika keputusan yang mereka lawan
        ternyata salah. Kalau aturannya berbeda, dua angka yang tampak
        sebanding sebenarnya menjawab hal yang berbeda."""
        salah = {"direction_correct": False}
        v = vindicated_vetoes([{**salah, "reason": "R"}])
        o = successful_objections([{**salah, "accuser": "A", "ground": "R"}])

        assert v[0].vindication_rate == o[0].vindication_rate == 1.0
