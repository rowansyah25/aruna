"""Gerbang risiko (PASAL 13.19-13.21).

Satu kalimat spec yang membentuk seluruh berkas ini:

    Signal Quality tinggi TIDAK otomatis berarti trade layak dilakukan.

Contohnya eksplisit - quality 94/100, confidence 91%, risk 87/100, keputusannya
NO SIGNAL. Jadi yang diuji berulang kali adalah: **kualitas tidak bisa membeli
izin dari risiko.**
"""

from __future__ import annotations

import pytest

from aruna.risk import FAKTOR, assess
from aruna.risk.gate import Keputusan, evaluate


def _risk(nilai: float, **ganti):
    bacaan = {f.key: nilai for f in FAKTOR}
    bacaan.update(ganti)
    return assess(bacaan)


class TestVonisSesuaiTingkatRisiko:
    def test_risiko_rendah_dikirim(self) -> None:
        v = evaluate(_risk(15.0))
        assert v.keputusan is Keputusan.KIRIM
        assert v.boleh_kirim

    def test_risiko_menengah_dikirim(self) -> None:
        assert evaluate(_risk(50.0)).keputusan is Keputusan.KIRIM

    def test_risiko_tinggi_dikirim_dengan_peringatan(self) -> None:
        """PASAL 13.24: boleh dikirim, asal dikirim SEBAGAI signal berisiko
        tinggi - bukan sebagai signal biasa yang kebetulan angkanya besar."""
        v = evaluate(_risk(70.0))
        assert v.keputusan is Keputusan.KIRIM_DENGAN_PERINGATAN
        assert v.boleh_kirim
        assert v.perlu_peringatan

    def test_risiko_sangat_tinggi_ditahan_walau_tanpa_veto(self) -> None:
        """Jalur skor-tinggi, dipisahkan dari jalur veto.

        Versi pertama memakai semua faktor di 90 - dan itu membuat likuidasi
        dan mutu data ikut melewati ambang fatal, jadi yang menahan adalah
        vetonya. Test itu tetap hijau ketika cabang VERY_HIGH dicabut, karena
        ia tidak pernah sampai ke sana.

        Di sini kedua faktor fatal ditahan di bawah ambangnya, jadi yang
        menahan hanya rata-ratanya yang tinggi.
        """
        v = evaluate(_risk(90.0, liquidation_distance=80.0, data_quality=80.0))

        assert not v.risk.vetoed, "kasusnya harus bebas veto"
        assert v.keputusan is Keputusan.TAHAN
        assert not v.boleh_kirim
        assert "sangat tinggi" in v.alasan

    def test_veto_selalu_menahan(self) -> None:
        v = evaluate(_risk(20.0, liquidation_distance=95.0))
        assert v.keputusan is Keputusan.TAHAN
        assert "membatalkan" in v.alasan


class TestKualitasTidakMembeliIzin:
    """PASAL 13.21, dan cara paling kuat menjaganya."""

    def test_gerbangnya_tidak_bisa_membaca_kualitas(self) -> None:
        """Begitu kualitas bisa dibaca di sini, godaan membiarkan 94 melunakkan
        risiko 87 menjadi satu baris yang terlihat masuk akal.

        Ketidaktahuan gerbang ini adalah fiturnya, jadi ia dijaga pada tanda
        tangannya - bukan pada niat siapa pun yang menyuntingnya nanti.
        """
        import inspect

        sig = inspect.signature(evaluate)
        for terlarang in ("quality", "confidence", "kualitas", "keyakinan"):
            assert not any(terlarang in p for p in sig.parameters), terlarang

    def test_modulnya_tidak_menyebut_kualitas_sama_sekali(self) -> None:
        import inspect

        from aruna.risk import gate

        kode = "\n".join(
            b for b in inspect.getsource(gate).splitlines()
            if not b.strip().startswith("#")
        )
        assert "signal_quality" not in kode

    def test_contoh_spec_ditahan(self) -> None:
        """Quality 94, confidence 91, risk 87 -> NO SIGNAL. Yang diberikan ke
        gerbang hanya risikonya, dan itu sudah cukup untuk menahannya."""
        assert evaluate(_risk(87.0)).keputusan is Keputusan.TAHAN


class TestRisikoYangTidakBisaDinilai:
    def test_bawaannya_mengirim_sebagai_peringatan(self) -> None:
        """**Diputuskan pengukuran, bukan prinsip.**

        Terukur sebelum gerbangnya dipasang: cakupan 22-36% pada tick dua puluh
        simbol, dan dua puluh dari dua puluh akan ditahan - bukan karena
        risikonya tinggi, tapi karena rencana REFUSED/WAIT tidak pernah
        menghitung likuidasi, stop atau R:R.

        Yang dikirim tidak pernah menyamar sebagai signal biasa: ia membawa
        pernyataan bahwa angkanya bukan penilaian risiko.
        """
        tipis = assess({"volatility": 10.0})
        v = evaluate(tipis)

        assert v.keputusan is Keputusan.KIRIM_DENGAN_PERINGATAN
        assert "bukan penilaian risiko" in v.alasan

    def test_bisa_diperketat_secara_eksplisit(self) -> None:
        """Dikembalikan begitu cakupan pada rencana aktif konsisten di atas
        60%."""
        tipis = assess({"volatility": 10.0})
        v = evaluate(tipis, tahan_kalau_unknown=True)

        assert v.keputusan is Keputusan.TAHAN
        assert "tidak bisa dinilai" in v.alasan

    def test_pelonggaran_tidak_menyentuh_perlindungan_sesungguhnya(self) -> None:
        """Inti keputusan ini: yang dilepas hanya menahan karena TIDAK TAHU.

        Veto faktor tunggal dan skor VERY_HIGH tetap menahan - dan veto bekerja
        walau cakupannya tipis, karena likuidasi yang terlalu dekat terlihat
        dari satu faktor saja.
        """
        fatal = assess({"volatility": 10.0, "liquidation_distance": 95.0})
        assert evaluate(fatal).keputusan is Keputusan.TAHAN

        tinggi = _risk(90.0, liquidation_distance=80.0, data_quality=80.0)
        assert evaluate(tinggi).keputusan is Keputusan.TAHAN

    def test_veto_menang_atas_pelonggaran(self) -> None:
        """Melonggarkan UNKNOWN tidak boleh ikut melonggarkan veto."""
        bacaan = {f.key: 20.0 for f in FAKTOR[:6]}
        bacaan["liquidation_distance"] = 95.0
        v = evaluate(assess(bacaan), tahan_kalau_unknown=False)

        # Cakupannya tipis DAN ada faktor fatal; yang dilaporkan harus vetonya.
        assert v.keputusan is Keputusan.TAHAN


class TestAlasannyaBisaDibaca:
    @pytest.mark.parametrize("nilai", [15.0, 50.0, 70.0, 90.0])
    def test_selalu_menyebut_alasan(self, nilai) -> None:
        v = evaluate(_risk(nilai))
        assert v.alasan
        assert v.keputusan.value in v.line()

    def test_penilaiannya_ikut_dibawa(self) -> None:
        """Supaya pemanggil bisa mencetak rinciannya tanpa menghitung ulang -
        dua perhitungan atas hal yang sama akan berbeda pendapat suatu hari."""
        r = _risk(30.0)
        assert evaluate(r).risk is r


class TestTetapAnalystOnly:
    def test_menahan_pengiriman_bukan_eksekusi(self) -> None:
        """PASAL 13.1. 'Ditahan' berarti satu pesan tidak dikirim - bukan satu
        order dibatalkan, karena ARUNA tidak punya order untuk dibatalkan."""
        import inspect

        from aruna.risk import gate

        sumber = inspect.getsource(gate).lower()
        for terlarang in (
            "cancel_order", "close_position", "create_order", "set_leverage",
        ):
            assert terlarang not in sumber, terlarang
