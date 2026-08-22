"""Klasifikasi KENAPA sebuah keputusan salah (bagian 12, Gate 13).

`loss_autopsies` sudah menyimpan bukti yang kaya - regime saat keputusan,
keadaan berita, tingkat risiko, keyakinan, agent yang mendukung dan yang
dibungkam, keberatan yang tak terjawab, veto yang ditolak, dan gerak merugikan
terjauh. Yang tidak ada adalah **namanya**.

`FAILURE_HYPOTHESES` memetakan tiga `outcome_class` ke prosa, dan ketiganya
menjawab *apa yang terjadi*:

    WRONG_FROM_START            -> "bacaan salah sejak awal"
    RIGHT_THEN_REVERSED         -> "bacaan benar, keluarnya tidak"
    RIGHT_DIRECTION_BAD_TIMING  -> "arah benar pada skala waktu yang lebih panjang"

Bagian 12 minta yang lain: *kenapa* bacaannya salah. Dari sebelas kategori yang
disebut, hanya `TIMING_ERROR` punya padanan.

**Yang modul ini sengaja tidak lakukan: menebak.** Beberapa kategori bagian 12
tidak bisa ditentukan dari bukti yang tersimpan pada autopsy spot -
`FUNDING_DISTORTION` dan `OI_MISREAD` milik jalur futures dan tidak pernah ada
di sini. Yang tidak bisa ditentukan menjadi `OTHER`, dan `OTHER` yang jujur
lebih berguna daripada kategori yang terdengar kaya dan salah.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import Decision
from aruna.learning.sebab import SebabKalah, klasifikasi
from aruna.signals.models import OutcomeClass


def _autopsy(**kw):
    from aruna.learning.autopsy import Autopsy

    dasar = {
        "signal_id": "s1",
        "symbol": "BTC/USDT",
        "horizon": "1h",
        "direction": Decision.BUY,
        "confidence": 0.6,
        "outcome_class": OutcomeClass.WRONG_FROM_START,
        "predicted_move_pct": 1.0,
        "actual_move_pct": -1.0,
        "max_adverse_pct": -1.5,
    }
    return Autopsy(**(dasar | kw))


class TestYangBisaDitentukan:
    def test_berita_buruk_saat_keputusan(self) -> None:
        """Bagian 12 `NEWS_SHOCK`. Keadaan berita tersimpan pada autopsy, jadi
        ini bisa ditentukan tanpa menebak."""
        s = klasifikasi(_autopsy(news_state="NEGATIVE"))

        assert s is SebabKalah.NEWS_SHOCK

    def test_berita_dalam_bentuk_prosa_ikut_terbaca(self) -> None:
        """`news_state` tersimpan sebagai PROSA, bukan enum.

        Terukur 2026-08-21 pada 1.433 autopsy: 957 berbunyi ``NO_RECENT_NEWS``
        dan sisanya berbentuk ``"2 item(s): 1+ / 0- / 1 unreadable"``.
        Mencocokkannya dengan kata ``NEGATIVE`` tidak pernah kena, dan
        `NEWS_SHOCK` mustahil menyala - cacat yang hanya terlihat karena
        klasifikasinya dijalankan atas data nyata. Sesudah diperbaiki:
        0% menjadi 8,8%.
        """
        s = klasifikasi(_autopsy(news_state="2 item(s): 1+ / 2- / 0 unreadable"))

        assert s is SebabKalah.NEWS_SHOCK

    def test_berita_tanpa_yang_negatif_bukan_guncangan(self) -> None:
        """Angka sebelum tanda minus adalah jumlahnya. Nol berarti nol."""
        s = klasifikasi(
            _autopsy(
                news_state="2 item(s): 1+ / 0- / 1 unreadable",
                regime="RANGING", confidence=0.5,
            )
        )

        assert s is not SebabKalah.NEWS_SHOCK

    def test_tanpa_berita_terbaru_bukan_guncangan(self) -> None:
        """957 dari 1.433 autopsy berbunyi begini. Menghitungnya sebagai
        guncangan akan membuat dua pertiga kekalahan disalahkan pada berita
        yang tidak ada."""
        s = klasifikasi(
            _autopsy(news_state="NO_RECENT_NEWS", regime="RANGING", confidence=0.5)
        )

        assert s is not SebabKalah.NEWS_SHOCK

    def test_tembusan_yang_berbalik(self) -> None:
        """Bagian 12 `FALSE_BREAKOUT`: regime tembusan, lalu harga berbalik."""
        s = klasifikasi(
            _autopsy(regime="BREAKOUT", outcome_class=OutcomeClass.RIGHT_THEN_REVERSED)
        )

        assert s is SebabKalah.FALSE_BREAKOUT

    def test_arah_benar_horizon_terlalu_pendek(self) -> None:
        """Satu-satunya kategori bagian 12 yang sudah punya padanan sebelum
        modul ini ada."""
        s = klasifikasi(
            _autopsy(outcome_class=OutcomeClass.RIGHT_DIRECTION_BAD_TIMING)
        )

        assert s is SebabKalah.TIMING_ERROR

    def test_regime_bertentangan_dengan_arah(self) -> None:
        """Bagian 12 `WRONG_REGIME`: BUY di tengah tren turun."""
        s = klasifikasi(_autopsy(regime="TRENDING_BEARISH", direction=Decision.BUY))

        assert s is SebabKalah.WRONG_REGIME

    def test_sangat_yakin_lalu_kalah_telak(self) -> None:
        """Bagian 12 `AGENT_OVERCONFIDENCE`. Terukur di produksi: pita >=90%
        menang 47,7%."""
        s = klasifikasi(_autopsy(confidence=0.95, regime="RANGING"))

        assert s is SebabKalah.AGENT_OVERCONFIDENCE

    def test_risiko_sudah_ditandai_tinggi(self) -> None:
        """Bagian 12 `INSUFFICIENT_DATA`: sistem sudah tahu datanya tipis dan
        tetap berpendapat."""
        s = klasifikasi(_autopsy(risk_level="HIGH", regime="RANGING"))

        assert s is SebabKalah.INSUFFICIENT_DATA

    def test_gerak_merugikan_jauh_melebihi_dugaan(self) -> None:
        """Bagian 12 `RISK_MODEL_ERROR`: modelnya tidak salah arah, tapi salah
        memperkirakan seberapa jauh harga bisa melawan."""
        s = klasifikasi(
            _autopsy(
                predicted_move_pct=1.0, actual_move_pct=0.2,
                max_adverse_pct=-8.0, regime="RANGING",
                outcome_class=OutcomeClass.RIGHT_THEN_REVERSED,
            )
        )

        assert s is SebabKalah.RISK_MODEL_ERROR


class TestYangMenolakMenebak:
    def test_tanpa_bukti_apa_pun_menjadi_other(self) -> None:
        """`OTHER` yang jujur lebih berguna daripada kategori yang terdengar
        kaya dan salah."""
        s = klasifikasi(
            _autopsy(regime=None, news_state=None, risk_level=None, confidence=0.5)
        )

        assert s is SebabKalah.OTHER

    def test_regime_yang_tidak_dikenal_tidak_dipaksa(self) -> None:
        s = klasifikasi(_autopsy(regime="SESUATU_YANG_BARU", confidence=0.5))

        assert s is SebabKalah.OTHER

    @pytest.mark.parametrize(
        "tak_terjangkau", ["FUNDING_DISTORTION", "OI_MISREAD", "LIQUIDITY_EVENT"]
    )
    def test_kategori_futures_ada_di_kosakata_tapi_tidak_dihasilkan(
        self, tak_terjangkau
    ) -> None:
        """Bagian 12 menyebutnya, jadi ia ada di kosakata. Tapi autopsy spot
        tidak menyimpan funding, open interest, maupun spread - jadi
        menghasilkannya dari sini berarti mengarang.

        Disebut lewat test, bukan dihilangkan diam-diam: siapa pun yang
        menambah bukti itu nanti akan menemukan kategorinya sudah menunggu.
        """
        assert tak_terjangkau in SebabKalah.__members__


class TestUrutanKekhususan:
    def test_berita_menang_atas_keyakinan(self) -> None:
        """Guncangan berita menerangkan kekalahan lebih baik daripada
        'agent-nya terlalu yakin' - keyakinan tinggi saat berita buruk memang
        seharusnya kalah."""
        s = klasifikasi(_autopsy(news_state="NEGATIVE", confidence=0.95))

        assert s is SebabKalah.NEWS_SHOCK

    def test_regime_bertentangan_menang_atas_keyakinan(self) -> None:
        s = klasifikasi(
            _autopsy(regime="TRENDING_BEARISH", direction=Decision.BUY,
                     confidence=0.95)
        )

        assert s is SebabKalah.WRONG_REGIME

    def test_setiap_sebab_punya_penjelasan(self) -> None:
        """Kategori tanpa kalimat penjelas memaksa pembacanya menebak artinya -
        dan itu mengembalikan masalah yang modul ini tutup."""
        for s in SebabKalah:
            assert s.penjelasan
            assert len(s.penjelasan) > 20


class TestTerpasangDiAutopsy:
    def test_autopsy_membawa_sebabnya(self) -> None:
        a = _autopsy(news_state="NEGATIVE")

        assert a.sebab is SebabKalah.NEWS_SHOCK

    def test_sebabnya_ikut_ke_dict(self) -> None:
        """Kalau ia tidak ikut tersimpan, klasifikasinya dihitung lalu
        dibuang - keluarga cacat yang sudah berulang di repo ini."""
        d = _autopsy(news_state="NEGATIVE").to_dict()

        assert d["sebab"] == SebabKalah.NEWS_SHOCK.value
        assert d["sebab_penjelasan"]

    def test_sebabnya_benar_benar_ditulis_ke_database(self) -> None:
        """Yang paling mudah hilang, dan yang sempat hilang.

        `to_dict()` dipakai keluaran CLI; `record_autopsy` menulis kolom
        eksplisit. Versi pertama modul ini menambahkan `sebab` ke yang pertama
        saja - klasifikasinya terlihat di layar dan tidak pernah tersimpan,
        keluarga cacat yang sama dengan `korelasi` yang tak pernah dipanggil.
        """
        import ast
        import inspect
        from textwrap import dedent

        from aruna.db.repositories.learning import LearningRepository

        pohon = ast.parse(
            dedent(inspect.getsource(LearningRepository.record_autopsy))
        )
        sql = " ".join(
            n.value for n in ast.walk(pohon)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        atribut = {
            n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)
        }

        assert "sebab" in sql
        assert "sebab" in atribut

    def test_jumlah_placeholder_sama_dengan_nilai(self) -> None:
        """Menambah kolom tanpa menambah placeholder menggeser SELURUH
        parameter satu posisi - dan itu menaruh keyakinan di kolom sebab tanpa
        satu pun error."""
        import inspect

        from aruna.db.repositories.learning import LearningRepository

        sumber = inspect.getsource(LearningRepository.record_autopsy)
        awal = sumber.index("INSERT INTO loss_autopsies")
        akhir = sumber.index("ON DUPLICATE", awal)
        sql = sumber[awal:akhir]
        kolom = sql[sql.index("(") + 1 : sql.index(")")]

        assert len(kolom.split(",")) == sql.count("%s")

    def test_hipotesis_lama_tetap_ada(self) -> None:
        """Yang lama menjawab *apa*, yang baru menjawab *kenapa*. Keduanya
        berguna, dan membuang yang lama menghapus keterangan."""
        a = _autopsy()

        assert a.hypothesis
        assert a.sebab is not None
