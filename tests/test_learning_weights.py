"""Bobot agent hanya berubah lewat manusia (PASAL 11.11, 11.16).

Cacat yang ditutup di sini masih menunggu ambangnya saat ditemukan:
``AgentRecord.multiplier`` menghitung bobot dari akurasi, dan
``MeasuredHistory.reliability`` menyerahkannya langsung ke judge. Begitu
seorang agent melewati dua puluh lima opini terskor, bobotnya berubah sendiri -
tanpa proposal, tanpa backtest, tanpa satu pun manusia menyetujuinya.

Cacat yang menunggu ambang untuk menyala tetap cacat. Yang ini akan menyala
diam-diam: bobot bergeser, putusan council ikut bergeser, dan tidak ada satu
baris pun yang menyatakan sesuatu telah berubah.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from aruna.core.enums import AgentRole
from aruna.learning.reliability import (
    MIN_RELIABILITY_SAMPLE,
    AgentRecord,
    ReliabilityReport,
)
from aruna.learning.weights import (
    DEFAULT_WEIGHT,
    MIN_PROPOSAL_DELTA,
    ApprovedWeights,
    propose_weights,
)


def _record(role=AgentRole.TECHNICAL, *, scored=40, correct=32) -> AgentRecord:
    return AgentRecord(role=role, scored=scored, correct=correct)


class TestBobotBerlaku:
    def test_bawaan_semua_setara(self) -> None:
        """Roster tanpa bobot yang disetujui adalah roster yang setara, dan
        itu keadaan yang sah serta bisa dipertahankan."""
        w = ApprovedWeights()
        assert w.for_role(AgentRole.TECHNICAL) == DEFAULT_WEIGHT
        assert w.any_adjusted is False

    def test_yang_disetujui_dipakai(self) -> None:
        w = ApprovedWeights({"TECHNICAL": 1.15})
        assert w.for_role(AgentRole.TECHNICAL) == 1.15
        assert w.for_role(AgentRole.NEWS) == DEFAULT_WEIGHT
        assert w.any_adjusted is True

    def test_menerima_nama_maupun_enum(self) -> None:
        w = ApprovedWeights({"NEWS": 0.9})
        assert w.for_role("NEWS") == w.for_role(AgentRole.NEWS) == 0.9


class TestUsulanBukanPenerapan:
    def test_agent_terukur_menghasilkan_usulan(self) -> None:
        (usul,) = propose_weights([_record()])
        assert usul.role == "TECHNICAL"
        assert usul.current == DEFAULT_WEIGHT
        assert usul.proposed > DEFAULT_WEIGHT

    def test_usulan_membawa_buktinya(self) -> None:
        """Manusia yang menyetujui harus bisa bertanya "apakah dua puluh lima
        ini datang dari satu minggu yang aneh?" - dan angka tidak bisa
        menanyakan itu pada dirinya sendiri."""
        (usul,) = propose_weights([_record(scored=40, correct=32)])
        assert usul.sample == 40
        assert usul.accuracy == 0.8
        assert "40 opini terskor" in usul.summary()

    def test_sampel_kurang_tidak_diusulkan(self) -> None:
        """Bukan diusulkan dengan catatan kecil - tidak diusulkan sama sekali.
        Mengusulkan perubahan untuk agent yang belum terukur berarti mengarang
        angka, yang persis dilarang PASAL 11.16."""
        kurang = _record(scored=MIN_RELIABILITY_SAMPLE - 1, correct=20)
        assert propose_weights([kurang]) == ()

    def test_perubahan_terlalu_kecil_tidak_diusulkan(self) -> None:
        """Proposal yang tidak akan mengubah satu pun putusan melatih
        penyetujunya menyetujui tanpa membaca."""
        # accuracy 0.52 -> multiplier 1.02, delta 0.02 di bawah ambang.
        hampir = _record(scored=100, correct=52)
        assert propose_weights([hampir]) == ()

    def test_dibandingkan_terhadap_bobot_berlaku_bukan_terhadap_satu(self) -> None:
        """Kalau selalu dibandingkan dengan 1,0, sebuah bobot yang sudah
        disetujui akan diusulkan ulang selamanya dengan angka yang sama."""
        sudah = ApprovedWeights({"TECHNICAL": 1.3})
        (usul,) = propose_weights([_record()], sudah)
        assert usul.current == 1.3
        assert usul.delta < 0

    def test_yang_paling_berdampak_lebih_dulu(self) -> None:
        usulan = propose_weights([
            # 0.80 akurasi -> pengali 1.30, dijepit ke 1.20, delta +0.20
            _record(AgentRole.TECHNICAL, scored=40, correct=32),
            # 0.20 akurasi -> pengali 0.70, delta -0.30
            _record(AgentRole.NEWS, scored=40, correct=8),
        ])
        assert [u.role for u in usulan] == ["NEWS", "TECHNICAL"]

    def test_tanpa_rekam_jejak_tidak_ada_usulan(self) -> None:
        assert propose_weights([]) == ()
        assert propose_weights(None) == ()


class TestTidakAdaJalurOtomatis:
    """**Pasal yang dijaga kelas ini ditimpa operator pada 2026-08-25.**

    Dua test di sini dulu mengunci PASAL 11.11: pengukuran tidak boleh berlaku
    tanpa persetujuan manusia. Operator menyatakan langsung bahwa ARUNA harus
    memperbaiki dirinya sendiri tanpa persetujuan per perubahan, jadi keduanya
    DIGANTI - bukan dihapus. Yang dihapus tidak meninggalkan jejak bahwa
    aturannya pernah ada, dan orang berikutnya akan mengira jalur otomatis itu
    kelalaian.

    Terukur sebelum dicabut: `historical_reliability` tercatat tidak tersedia
    pada 100% keputusan, di setiap hari yang tersimpan. Gerbangnya bukan
    memperlambat penerapan - ia meniadakannya, karena tabel yang mengisi
    `approved_weights` tidak pernah ada.

    Yang MASIH dijaga: mengusulkan tetap tidak boleh diam-diam mengubah bobot
    yang tersimpan. Mesin usulan dan bobot berlaku tetap dua hal berbeda.
    """

    def test_mengusulkan_tidak_mengubah_bobot_berlaku(self) -> None:
        """Ini tetap berlaku. `propose_weights` melapor; ia tidak menulis."""
        sudah = ApprovedWeights({"TECHNICAL": 1.0})
        propose_weights([_record()], sudah)
        assert sudah.for_role(AgentRole.TECHNICAL) == 1.0

    def test_yang_diukur_sekarang_BERLAKU_tanpa_disetujui(self) -> None:
        """Kebalikan dari test yang dulu ada di sini, dan itu disengaja.

        Dulu: "history memakai yang disetujui, bukan yang diukur."
        Sekarang: yang diukur berlaku, dan `approved_weights` tidak lagi
        menggerbanginya. Pagarnya pindah ke sampel minimum dan batas pengali -
        lihat `test_bobot_berlaku_tanpa_persetujuan`.
        """
        from aruna.learning.history import MeasuredHistory

        diukur = _record()
        assert diukur.multiplier is not None

        history = MeasuredHistory(
            reliability_report=ReliabilityReport(records=(diukur,)),
            calibration_report=NS(buckets=()),
        )

        assert history.reliability(AgentRole.TECHNICAL) == diukur.multiplier

    def test_belum_terukur_tetap_beda_dari_terukur_dan_kebetulan_satu(self) -> None:
        """Satu-satunya bagian yang TIDAK berubah maknanya.

        ``None`` dulu berarti "belum ada yang menyetujui"; sekarang berarti
        "belum cukup sampel untuk diukur". Yang tetap: ia bukan 1,0. Judge
        mencatat ``None`` sebagai faktor tidak tersedia, dan menyamakannya
        dengan 1,0 akan membuat "tidak diukur" terlihat seperti "diukur dan
        kebetulan netral".
        """
        from aruna.learning.history import MeasuredHistory

        kosong = MeasuredHistory(
            reliability_report=ReliabilityReport(records=()),
            calibration_report=NS(buckets=()),
        )

        assert kosong.reliability(AgentRole.TECHNICAL) is None


def test_ambang_usulan_masuk_akal() -> None:
    assert 0 < MIN_PROPOSAL_DELTA < 0.5
    assert DEFAULT_WEIGHT == 1.0
