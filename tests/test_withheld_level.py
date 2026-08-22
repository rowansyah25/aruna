"""Penahanan yang menunjuk input berteriak; yang menunjuk keputusan tidak.

Terukur di log produksi: 765 penahanan, **seluruhnya WARNING** - dan 359 di
antaranya adalah ARUNA bekerja persis seperti dirancang (238 masa tenang
sesudah kalah, 121 duplikat dari prediksi yang masih terbuka).

Peringatan yang isinya disiplin yang berjalan benar melatih pembacanya melewati
baris WARNING, dan yang hilang berikutnya adalah 40 gerbang mutu yang
benar-benar menunjuk data bermasalah.
"""

from __future__ import annotations

import pytest

from aruna.signals.withheld import PERLU_PERHATIAN, WithheldCode, classify


class TestPembagiannya:
    def test_yang_menunjuk_input_berteriak(self) -> None:
        assert WithheldCode.STALE_EVIDENCE in PERLU_PERHATIAN
        assert WithheldCode.QUALITY_GATE in PERLU_PERHATIAN

    def test_yang_menunjuk_keputusan_tidak(self) -> None:
        """Masa tenang dan duplikat adalah disiplin yang berjalan benar."""
        assert WithheldCode.COOLDOWN not in PERLU_PERHATIAN
        assert WithheldCode.DUPLICATE not in PERLU_PERHATIAN
        assert WithheldCode.NON_DIRECTIONAL not in PERLU_PERHATIAN
        assert WithheldCode.CONFIDENCE_FLOOR not in PERLU_PERHATIAN

    def test_yang_tidak_bisa_dikelompokkan_berteriak(self) -> None:
        """Penahanan tanpa nama berarti ada keputusan yang diambil sistem dan
        tidak ada yang bisa menjelaskannya. Itu justru yang paling perlu
        dilihat."""
        assert WithheldCode.UNKNOWN in PERLU_PERHATIAN

    def test_tidak_semuanya_berteriak(self) -> None:
        """Kalau seluruh kode masuk, peredamnya tidak meredam apa pun."""
        assert len(PERLU_PERHATIAN) < len(WithheldCode)

    def test_tidak_ada_yang_berteriak_pun_salah(self) -> None:
        """Sebaliknya juga: gerbang mutu yang diam membuat data bermasalah
        lewat tanpa ada yang tahu."""
        assert PERLU_PERHATIAN


class TestKalimatProduksiTerkelompok:
    """Kalimat-kalimat ini diambil apa adanya dari log produksi."""

    @pytest.mark.parametrize(
        ("kalimat", "kode"),
        [
            (
                "cooldown sesudah kalah sampai 2026-08-23T19:25:29.280Z "
                "(dasar 1 horizon, rugi 7.00%)",
                WithheldCode.COOLDOWN,
            ),
            (
                "duplikat prediksi terbuka: arah sama (BUY); entry bergeser "
                "0.06%; target bergeser 0.07%",
                WithheldCode.DUPLICATE,
            ),
            (
                "evidence is 15 minute(s) old against a 15m horizon - stale, "
                "not published as a live signal",
                WithheldCode.STALE_EVIDENCE,
            ),
            ("quality gate: quality 56/100 di bawah 60", WithheldCode.QUALITY_GATE),
            ("quality gate: gerbang gagal: anomaly", WithheldCode.QUALITY_GATE),
        ],
    )
    def test_kalimat_produksi(self, kalimat: str, kode: WithheldCode) -> None:
        assert classify(kalimat) is kode

    def test_dua_yang_paling_sering_tidak_berteriak(self) -> None:
        """238 masa tenang + 121 duplikat = 359 dari 765 peringatan."""
        for kalimat in (
            "cooldown sesudah kalah sampai 2026-08-23T19:25:29.280Z",
            "duplikat prediksi terbuka: arah sama (BUY)",
        ):
            assert classify(kalimat) not in PERLU_PERHATIAN

    def test_gerbang_mutu_tetap_berteriak(self) -> None:
        assert classify("quality gate: gerbang gagal: anomaly") in PERLU_PERHATIAN


class TestJalurHidup:
    def test_service_memakai_pembagiannya(self) -> None:
        """Tanpa ini, ``PERLU_PERHATIAN`` bisa ada, diuji, dan tidak pernah
        dipakai - dan seluruh penahanan tetap WARNING seperti semula."""
        from aruna.signals import service as modul

        assert modul.PERLU_PERHATIAN is PERLU_PERHATIAN
