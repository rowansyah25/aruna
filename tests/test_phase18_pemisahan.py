"""Tujuh keyakinan tidak boleh dilebur jadi satu angka (bagian 18.17, 18.45).

**Celah 8, dan bentuknya sama seperti enam celah sebelumnya di proyek ini.**
Ketujuh angkanya sudah dihitung seluruhnya - council menghitung yang pertama,
``decision.score`` yang kedua, dan lima sisanya adalah faktor di dalam
``QualityScore`` yang sama yang menghasilkan Decision Quality. Lalu
``_mutu_signal`` memulangkan ``float(skor.score)`` dan sembilan belas faktor
lainnya hilang di baris terakhir.

Yang dilihat operator: satu baris ``CONFIDENCE: 81%``. Keyakinan council 81% di
atas rezim yang tak terbaca dan skenario rapuh mencetak baris yang identik
dengan 81% di atas keduanya yang kuat.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from aruna.signals.pemisahan import (
    TIDAK_TERUKUR,
    TUJUH,
    pisahkan,
    render_terpisah,
)
from aruna.signals.quality import Factor, QualityScore


def _mutu(**skor: float | None) -> QualityScore:
    return QualityScore(
        factors=tuple(
            Factor(nama, nilai, 2.0) for nama, nilai in skor.items()
        )
    )


class TestTujuhTerpisah:
    def test_ketujuhnya_disebut(self) -> None:
        nama = [t.nama for t in pisahkan(mutu=_mutu(), confidence=0.81)]

        assert nama == list(TUJUH)

    def test_tujuh_persis_seperti_bagian_18_17(self) -> None:
        """Daftarnya bukan selera. Bagian 18.17 menyebut tujuh, bernomor."""
        assert len(TUJUH) == 7
        assert TUJUH[0] == "Signal Confidence"
        assert TUJUH[-1] == "Decision Quality"

    def test_tiap_keyakinan_punya_sumber_yang_berbeda(self) -> None:
        """**Yang paling mudah salah tanpa terlihat salah.** Tujuh baris yang
        dua di antaranya membaca faktor yang sama akan tampak patuh pada bagian
        18.17 sambil mencetak angka yang sama dua kali - persis peleburan yang
        dilarang, hanya dengan lebih banyak baris.
        """
        mutu = _mutu(
            scenario=0.10, strategy=0.20, regime_clarity=0.30, data_quality=0.40
        )
        nilai = [
            t.nilai
            for t in pisahkan(mutu=mutu, confidence=0.81, decision="+69 dari 100")
        ]

        assert len(set(nilai)) == len(nilai)

    def test_urutan_dari_faktor_mengikuti_tujuh(self) -> None:
        """``_DARI_FAKTOR`` adalah dict dan urutannya menentukan urutan cetak;
        sebuah penyusunan ulang di sana tidak boleh diam-diam berbeda dari
        urutan yang disebut bagian 18.17."""
        from aruna.signals.pemisahan import _DARI_FAKTOR

        assert list(_DARI_FAKTOR) == list(TUJUH[2:6])


class TestTakTerukurBukanNol:
    def test_faktor_hilang_dicetak_tidak_terukur(self) -> None:
        """Strategy Confidence 0/100 adalah tuduhan terhadap router yang
        sebenarnya tidak pernah ditanya."""
        (strategi,) = [
            t
            for t in pisahkan(mutu=_mutu(strategy=None))
            if t.nama == "Strategy Confidence"
        ]

        assert strategi.nilai is None
        assert not strategi.terukur
        assert TIDAK_TERUKUR in strategi.baris()

    def test_nol_tetap_dicetak_nol(self) -> None:
        (strategi,) = [
            t
            for t in pisahkan(mutu=_mutu(strategy=0.0))
            if t.nama == "Strategy Confidence"
        ]

        assert strategi.nilai == "0/100"

    def test_tanpa_mutu_sama_sekali_tidak_meledak(self) -> None:
        daftar = pisahkan(mutu=None, confidence=None, decision="")

        assert len(daftar) == 7
        assert not any(t.terukur for t in daftar)

    def test_yang_tak_terukur_tetap_dicetak(self) -> None:
        """Menyembunyikan barisnya membuat pesan yang KEHILANGAN Phase 17
        terbaca persis seperti pesan yang tidak pernah punya Phase 17."""
        teks = "\n".join(render_terpisah(pisahkan(mutu=_mutu(strategy=None))))

        assert "Strategy Confidence" in teks


class TestSatuanTidakDipaksakan:
    def test_confidence_persen_faktor_per_seratus(self) -> None:
        daftar = {t.nama: t.nilai for t in pisahkan(
            mutu=_mutu(scenario=0.84), confidence=0.81
        )}

        assert daftar["Signal Confidence"] == "81%"
        assert daftar["Scenario Confidence"] == "84/100"

    def test_decision_score_dibawa_apa_adanya(self) -> None:
        """Satu-satunya yang punya tanda, dan satu-satunya yang wajib disertai
        bantahan "bukan peluang profit" (PASAL 14.16). Memaksanya jadi persen
        supaya kolomnya rapi mengubahnya menjadi sesuatu yang bukan dirinya."""
        teks = "+69 dari 100 (bukan peluang profit)"
        (putusan,) = [
            t
            for t in pisahkan(mutu=_mutu(), decision=teks)
            if t.nama == "Decision Confidence"
        ]

        assert putusan.nilai == teks


class TestTerpasangDiPesanFutures:
    """Ditulis dan diuji tidak sama dengan sampai ke operator."""

    def _pesan(self, **kw: object) -> str:
        from aruna.futures.notify import _penilaian

        note = NS(
            confidence=0.81,
            disagreement=0.12,
            regime="TRENDING UP",
            debated=False,
            high_disagreement=False,
            reasons=(),
            split=NS(setuju=(), kontra=(), abstain=(), total="0 VS 0"),
            decision_readings={},
            risk_readings={},
            **kw,
        )
        return "\n".join(_penilaian(NS(buffer=None), note))

    def test_ketujuhnya_sampai_ke_pesan(self) -> None:
        teks = self._pesan(
            mutu=_mutu(
                scenario=0.84, strategy=0.91, regime_clarity=0.86,
                data_quality=1.0,
            )
        )

        for nama in TUJUH:
            assert nama in teks, nama

    def test_lima_dari_quality_score_benar_benar_terbaca(self) -> None:
        """Bukan sekadar labelnya yang muncul - angkanya harus datang dari
        ``QualityScore`` yang dititipkan, bukan dari nilai bawaan."""
        teks = self._pesan(
            mutu=_mutu(
                scenario=0.84, strategy=0.91, regime_clarity=0.86,
                data_quality=1.0,
            )
        )

        assert "84/100" in teks
        assert "91/100" in teks
        assert "86/100" in teks

    def test_tanpa_laporan_mutu_tidak_mencetak_nol(self) -> None:
        teks = self._pesan(mutu=None)

        assert "Scenario Confidence" in teks
        assert TIDAK_TERUKUR in teks
        assert "0/100" not in teks


class TestKalibrasiSampaiKePesan:
    """Bagian 18.45.

    Kalibrasi menjawab pertanyaan yang tidak dijawab angka mana pun di atasnya:
    apakah keyakinan yang barusan dicetak berarti apa yang dikatakannya.
    """

    def _pesan(self, pembelajaran: object) -> str:
        from aruna.futures.notify import _penilaian

        note = NS(
            confidence=0.81, disagreement=0.12, regime="", debated=False,
            high_disagreement=False, reasons=(), mutu=None,
            split=NS(setuju=(), kontra=(), abstain=(), total="0 VS 0"),
            decision_readings={}, risk_readings={}, pembelajaran=pembelajaran,
        )
        return "\n".join(_penilaian(NS(buffer=None), note))

    def test_vonis_dicetak(self) -> None:
        vonis = "OVERCONFIDENT in 50-65%, 65-80%: stated confidence exceeds accuracy"
        teks = self._pesan(NS(kalibrasi=vonis))

        assert vonis in teks

    def test_belum_pernah_diukur_tidak_dicetak_good(self) -> None:
        """Sistem yang belum pernah memeriksa kejujurannya sendiri bukan sistem
        yang terkalibrasi baik."""
        teks = self._pesan(NS(kalibrasi=""))

        assert "KALIBRASI" not in teks

    def test_tanpa_pembelajaran_tidak_meledak(self) -> None:
        assert "KALIBRASI" not in self._pesan(None)


class TestPembacanyaAdaDiSnapshot:
    async def test_snapshot_membaca_kalibrasi_terakhir(self) -> None:
        """Pembaca yang tidak pernah dipanggil adalah celah yang sama, sekali
        lagi. Diuji lewat repositori palsu yang mencatat panggilannya."""
        from aruna.core.enums import Horizon, Market
        from aruna.learning.snapshot import PembacaPembelajaran

        dipanggil: list[str] = []

        class _Kalibrasi:
            async def latest_calibration(self) -> dict[str, str]:
                dipanggil.append("kalibrasi")
                return {"verdict": "CALIBRATED within 10 points"}

        class _Adaptive:
            async def notable_patterns(self, **kw: object) -> list[dict]:
                return []

            async def agent_votes(self) -> list[dict]:
                return []

        hasil = await PembacaPembelajaran(
            learning12=_Adaptive(), kalibrasi_store=_Kalibrasi()
        ).baca(market=Market.CRYPTO, interval=Horizon.M15)

        assert dipanggil == ["kalibrasi"]
        assert hasil.kalibrasi == "CALIBRATED within 10 points"

    async def test_tidak_ditanyakan_ke_repositori_pola(self) -> None:
        """**Cacat yang benar-benar terjadi di produksi, 2026-08-24.**

        ``latest_calibration`` dibaca dari ``learning12`` dan meledak pada tick
        pertama: ``AttributeError: 'LearningRepository' object has no attribute
        'latest_calibration'``. Ada DUA kelas bernama ``LearningRepository`` -
        satu memegang pola dan suara agent, satu memegang autopsi dan
        kalibrasi - dan yang dirangkai sebagai ``learning12`` adalah yang
        pertama.

        Test lamanya hijau karena palsunya punya kedua metode sekaligus, jadi
        bentuk yang salah tidak pernah terlihat. Palsu di sini sengaja
        dipisah: yang memegang pola TIDAK punya ``latest_calibration``, persis
        seperti yang sungguhan.
        """
        from aruna.core.enums import Horizon, Market
        from aruna.learning.snapshot import PembacaPembelajaran

        class _HanyaPola:
            async def notable_patterns(self, **kw: object) -> list[dict]:
                return []

            async def agent_votes(self) -> list[dict]:
                return []

        hasil = await PembacaPembelajaran(learning12=_HanyaPola()).baca(
            market=Market.CRYPTO, interval=Horizon.M15
        )

        assert hasil.kalibrasi == ""

    def test_dirangkai_ke_kelas_yang_benar(self) -> None:
        """Penjaga lamanya memeriksa kelas yang SALAH - ia mengimpor
        ``db.repositories.learning.LearningRepository`` dan bertanya apakah ia
        punya ``latest_calibration``. Jawabannya ya, dan itu tidak pernah
        menjadi pertanyaannya: yang dirangkai ``app.py`` adalah kelas yang
        lain.

        Yang diperiksa sekarang keduanya - kelas mana yang punya metodenya, dan
        kelas mana yang TIDAK.
        """
        from aruna.db.repositories.learning import LearningRepository
        from aruna.db.repositories.learning12 import (
            LearningRepository as AdaptiveRepository,
        )

        assert hasattr(LearningRepository, "latest_calibration")
        assert not hasattr(AdaptiveRepository, "latest_calibration"), (
            "kalau kelas pola akhirnya punya metode ini juga, dua repositori "
            "menjawab satu pertanyaan dan tidak ada yang tahu mana yang dibaca"
        )

    def test_app_mengoper_repositori_kalibrasi(self) -> None:
        """Bidang yang ada tapi tak pernah diisi adalah celah yang sama."""
        import inspect

        from aruna import app

        assert "kalibrasi_store=self.learning_store" in inspect.getsource(app)


class TestSkorTurunanBukanBidangKedua:
    def test_quality_dibaca_dari_mutu(self) -> None:
        """Satu sumber, bukan dua. Bidang tersimpan kedua akan berselisih
        dengan laporannya pada hari salah satunya diperbarui."""
        from aruna.futures.debate import CouncilNote

        note = CouncilNote(
            symbol="BTCUSDT", confidence=0.8, disagreement=0.1,
            split=NS(setuju=(), kontra=(), abstain=()),
            mutu=_mutu(scenario=1.0, strategy=0.0),
        )

        assert note.quality == 50

    def test_tanpa_mutu_skornya_none(self) -> None:
        from aruna.futures.debate import CouncilNote

        note = CouncilNote(
            symbol="BTCUSDT", confidence=0.8, disagreement=0.1,
            split=NS(setuju=(), kontra=(), abstain=()),
        )

        assert note.quality is None
