"""Gerbang risiko sesudah scenario, sebelum keputusan akhir (diagram operator).

**Risiko masuk DUA KALI di Phase 17, dan itu keputusan operator 2026-08-23.**
Di :func:`~aruna.router.kecocokan.nilai` ia menahan strategi berisiko ekstrem
NAIK ke champion; di sini ia menahan rencana berisiko ekstrem TERBIT walau
strateginya wajar. Yang pertama tentang pilihan, yang kedua tentang keadaan
sekarang - pertanyaan yang berbeda, jadi keduanya berdiri.

**Bentuk `Vonis` yang dipakai di sini yang benar-benar ada.** Rencana Phase 17
menulis `Keputusan.LOLOS` dan `vonis.alasan` sebagai tuple; keduanya salah.
Yang ada `KIRIM`, `KIRIM_DENGAN_PERINGATAN`, `TAHAN`, `alasan: str`, dan
sebuah properti `boleh_kirim` yang sudah menjawab persis pertanyaan ini.
"""

from __future__ import annotations

from typing import Any

from aruna.agents.risk import RiskLevel as AgentRiskLevel
from aruna.risk.gate import Keputusan, Vonis
from aruna.risk.score import Penilaian, RiskLevel
from aruna.router.kecocokan import Kecocokan
from aruna.router.putusan import (
    AlasanKosong,
    PutusanRouter,
    VonisTingkat,
    lolos_gerbang,
)


def _penilaian() -> Penilaian:
    """Bentuknya diambil dari `Penilaian` yang asli, bukan dikarang.

    Test double yang bidangnya beda dari objek asli membuat suite hijau di atas
    bug produksi - cacat yang sudah berulang di proyek ini.
    """
    return Penilaian(score=42.0, level=RiskLevel.MEDIUM, coverage=1.0)


def _vonis(keputusan: Keputusan, alasan: str = "drawdown historis dalam") -> Vonis:
    return Vonis(keputusan=keputusan, alasan=alasan, risk=_penilaian())


def _terpilih(dengan_challenger: bool = True) -> PutusanRouter:
    return PutusanRouter(
        champion=Kecocokan("STR-001", 91, ("rezim cocok",), 900),
        challenger=Kecocokan("STR-005", 84, (), 900) if dengan_challenger else None,
        alasan_kosong="",
        kode_kosong=None,
        regime="TRENDING",
        alasan=("rezim cocok",),
    )


class TestMenahan:
    def test_vonis_tahan_membatalkan_champion(self) -> None:
        """Champion yang lolos peringkat masih bisa gugur di sini - dan
        sebabnya harus tercatat, bukan menghilang sebagai NONE tanpa
        keterangan."""
        hasil = lolos_gerbang(_terpilih(), vonis=_vonis(Keputusan.TAHAN))

        assert hasil.champion is None
        assert "drawdown historis dalam" in hasil.alasan_kosong
        assert hasil.kode_kosong is AlasanKosong.RISIKO_MENAHAN

    def test_challenger_tidak_naik_diam_diam(self) -> None:
        """**Yang paling mudah salah di seluruh Task 9.** Challenger dipilih
        karena kecocokannya, bukan karena ia lebih aman - dan menaikkannya
        ketika champion gugur berarti menerbitkan rencana yang tidak pernah
        dinilai gerbang ini."""
        hasil = lolos_gerbang(_terpilih(), vonis=_vonis(Keputusan.TAHAN))

        assert hasil.challenger is None

    def test_rezimnya_tetap_terbawa(self) -> None:
        """Baris `router_pilihan` menyimpan rezim yang jadi dasar keputusan.
        Menghapusnya saat gerbang menahan membuat penolakan tidak bisa
        dihubungkan kembali dengan keadaan pasarnya."""
        hasil = lolos_gerbang(_terpilih(), vonis=_vonis(Keputusan.TAHAN))

        assert hasil.regime == "TRENDING"


class TestMeloloskan:
    def test_vonis_kirim_membiarkan_champion(self) -> None:
        semula = _terpilih()
        hasil = lolos_gerbang(semula, vonis=_vonis(Keputusan.KIRIM))

        assert hasil.champion is semula.champion
        assert hasil.challenger is semula.challenger
        assert not hasil.alasan_kosong

    def test_peringatan_lolos_tapi_ikut_tercatat(self) -> None:
        """`KIRIM_DENGAN_PERINGATAN` boleh kirim - `boleh_kirim` sudah
        menyatakannya - tapi peringatannya tidak boleh hilang. Rencana yang
        terbit dengan peringatan dan rencana yang terbit bersih adalah dua hal
        yang berbeda, dan barisnya harus bisa membedakannya."""
        hasil = lolos_gerbang(
            _terpilih(),
            vonis=_vonis(Keputusan.KIRIM_DENGAN_PERINGATAN, "volatilitas tinggi"),
        )

        assert hasil.champion is not None
        assert any("volatilitas tinggi" in a for a in hasil.alasan)

    def test_memakai_boleh_kirim_bukan_daftar_keputusan_sendiri(self) -> None:
        """`Vonis.boleh_kirim` sudah menjawab persis pertanyaan ini. Menulis
        ulang daftarnya di sini berarti dua tempat yang harus tetap sepakat,
        dan yang kedua akan diam saat nilai baru ditambahkan ke enum."""
        for keputusan in Keputusan:
            v = _vonis(keputusan)
            hasil = lolos_gerbang(_terpilih(), vonis=v)

            assert (hasil.champion is not None) is v.boleh_kirim


class TestYangTidakDinilai:
    def test_tanpa_champion_tidak_ada_yang_digerbangi(self) -> None:
        semula = PutusanRouter(
            None, None, "keyakinan rezim 20%", AlasanKosong.KEYAKINAN_KURANG
        )
        hasil = lolos_gerbang(semula, vonis=_vonis(Keputusan.TAHAN))

        assert hasil is semula

    def test_gerbang_yang_tidak_berjalan_dicatat_bukan_didiamkan(self) -> None:
        """**Bentuk kegagalan yang paling sulit ditemukan.** Champion yang
        lolos karena gerbangnya berjalan dan champion yang lolos karena
        gerbangnya tidak pernah berjalan terlihat sama persis dari luar - dan
        yang kedua berarti seluruh Task 9 dekoratif.

        Tidak dilempar: fase router tidak boleh menjatuhkan siklus. Dicatat di
        `alasan`, yang memang tersimpan sebagai JSON di `router_pilihan`.
        """
        hasil = lolos_gerbang(_terpilih(), vonis=None)

        assert hasil.champion is not None
        assert any("tidak dijalankan" in a for a in hasil.alasan)

    def test_vonis_berbentuk_asing_diperlakukan_tidak_dinilai(self) -> None:
        """Objek yang tidak punya `boleh_kirim` bukan vonis. Menebak niatnya -
        misalnya menganggapnya lolos - berarti gerbang yang bisa dimatikan
        dengan mengoper benda yang salah."""

        class _Bukan:
            pass

        hasil = lolos_gerbang(_terpilih(), vonis=_Bukan())

        assert hasil.champion is not None
        assert any("tidak dijalankan" in a for a in hasil.alasan)


class TestVonisDariTingkatTersimpan:
    """Yang benar-benar dipakai fase router - `evaluate` tidak bisa."""

    def test_kosakatanya_yang_benar_benar_tersimpan(self) -> None:
        """**Ada DUA enum bernama `RiskLevel` di kode ini**, dan keduanya punya
        `HIGH` dan `LOW` - jadi salah impor tidak meledak, ia DIAM.

        `aruna.risk.score.RiskLevel`  : VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH
        `aruna.agents.risk.RiskLevel` : LOW, MODERATE, HIGH, EXTREME

        Yang tersimpan di `signal_snapshots.risk_level` yang kedua. Terukur
        2026-08-23 atas tujuh hari: MODERATE 12.125, HIGH 3.103, EXTREME 673 -
        tidak satu pun `MEDIUM` atau `VERY_HIGH`.
        """
        for nilai in ("MODERATE", "HIGH", "EXTREME"):
            assert VonisTingkat.dari_tersimpan(nilai) is not None, nilai

        assert VonisTingkat.dari_tersimpan("VERY_HIGH") is None
        assert VonisTingkat.dari_tersimpan("MEDIUM") is None

    def test_dua_enum_risklevel_memang_berbeda(self) -> None:
        """Kalau suatu hari keduanya disatukan, test ini yang memberitahu -
        bukan sebuah baris yang diam-diam berhenti menahan apa pun."""
        from aruna.risk.score import RiskLevel as SkorRiskLevel

        assert {r.value for r in AgentRiskLevel} != {r.value for r in SkorRiskLevel}

    def test_extreme_menahan_high_memperingatkan(self) -> None:
        assert not VonisTingkat.dari_tersimpan("EXTREME").boleh_kirim
        assert VonisTingkat.dari_tersimpan("HIGH").boleh_kirim
        assert VonisTingkat.dari_tersimpan("HIGH").perlu_peringatan
        assert not VonisTingkat.dari_tersimpan("MODERATE").perlu_peringatan

    def test_tingkat_asing_belum_dinilai_bukan_aman(self) -> None:
        """Tingkat baru yang ditambahkan ke enum dan lupa diurus di sini akan
        terbaca "belum dinilai" - terlihat di baris `router_pilihan` - bukan
        lolos diam-diam sebagai aman."""
        assert VonisTingkat.dari_tersimpan("SANTAI") is None
        assert VonisTingkat.dari_tersimpan(None) is None

    def test_tangganya_sepakat_dengan_gerbang_risiko_penuh(self) -> None:
        """**Yang menjaga dua tangga tetap sejajar.** `evaluate` menahan
        VERY_HIGH dan memperingatkan HIGH; ini menahan EXTREME dan
        memperingatkan HIGH. Sejajar karena pertanyaannya memang sama;
        terpisah karena buktinya tidak.

        Kalau salah satu digeser tanpa yang lain, dua gerbang yang mengaku
        menjawab hal yang sama akan memberi jawaban berbeda - dan tidak ada
        yang tahu yang mana yang berlaku.
        """
        from aruna.risk.gate import evaluate
        from aruna.risk.score import Penilaian as P
        from aruna.risk.score import RiskLevel as SkorRiskLevel

        tertinggi = evaluate(P(score=95.0, level=SkorRiskLevel.VERY_HIGH, coverage=1.0))
        tinggi = evaluate(P(score=70.0, level=SkorRiskLevel.HIGH, coverage=1.0))

        assert tertinggi.boleh_kirim is (
            VonisTingkat.dari_tersimpan("EXTREME").boleh_kirim
        )
        assert tinggi.perlu_peringatan is (
            VonisTingkat.dari_tersimpan("HIGH").perlu_peringatan
        )


class TestDuaGerbangMenjawabPertanyaanBerbeda:
    def test_alasan_kedua_gerbang_tidak_sama(self) -> None:
        """Kalau keduanya menghasilkan sebab yang sama, salah satunya
        berlebihan. `RISIKO_MENAHAN` di sini adalah keadaan SEKARANG;
        potongan risiko di `kecocokan.nilai` adalah sejarah strateginya."""
        assert AlasanKosong.RISIKO_MENAHAN is not AlasanKosong.TAK_ADA_YANG_COCOK

    def test_seluruh_sebab_punya_nilai_sendiri(self) -> None:
        """Enum yang dua anggotanya bernilai sama akan menggabungkan dua sebab
        menjadi satu kelompok di laporan, diam-diam."""
        nilai_nilai: list[Any] = [str(a) for a in AlasanKosong]

        assert len(set(nilai_nilai)) == len(nilai_nilai)
