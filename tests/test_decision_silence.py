"""Akurasi NO SIGNAL (PASAL 14.32, 14.33).

Sistem yang hanya menilai signal yang dikirimnya menilai separuh dari apa yang
dilakukannya. Terukur pada tick dua puluh simbol: lima belas tanpa arah, lima
ditolak karena biayanya - dan tidak satupun dari lima belas itu pernah dinilai
benar atau salah.

Diam yang tidak pernah dinilai adalah tempat paling nyaman untuk sebuah sistem
bersembunyi: ia tidak pernah kalah, karena ia tidak pernah tercatat bertaruh.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.decision import GERAK_BERARTI_PCT, Diam, Vonis, evaluate


def _diam(gerak, *, simbol="BTC/USDT", alasan="bukti bertentangan"):
    return Diam(
        symbol=simbol,
        reason=alasan,
        move_pct=None if gerak is None else Decimal(str(gerak)),
    )


class TestVonisSatuKeputusan:
    def test_pasar_diam_berarti_diamnya_benar(self) -> None:
        assert _diam("0.4").verdict is Vonis.CORRECT

    def test_gerak_besar_berarti_kesempatan_terlewat(self) -> None:
        assert _diam("3.8").verdict is Vonis.MISSED

    def test_turun_jauh_juga_kesempatan_terlewat(self) -> None:
        """SHORT yang terlewat sama hilangnya dengan LONG yang terlewat."""
        assert _diam("-3.8").verdict is Vonis.MISSED

    def test_tepat_di_ambang_terhitung_terlewat(self) -> None:
        assert _diam(GERAK_BERARTI_PCT).verdict is Vonis.MISSED

    def test_sedikit_di_bawah_ambang_masih_benar(self) -> None:
        assert _diam(GERAK_BERARTI_PCT - Decimal("0.01")).verdict is Vonis.CORRECT

    def test_belum_terukur_bukan_benar(self) -> None:
        """Horizon yang belum lewat bukan bukti bahwa diamnya tepat."""
        assert _diam(None).verdict is Vonis.UNKNOWN

    def test_arah_yang_terlewat_disebut(self) -> None:
        assert _diam("3.8").missed_direction == "LONG"
        assert _diam("-3.8").missed_direction == "SHORT"
        assert _diam("0.4").missed_direction is None


class TestLaporan:
    def test_yang_belum_terukur_tidak_masuk_penyebut(self) -> None:
        """Memasukkannya membuat akurasi naik setiap kali ARUNA menambah
        keputusan yang horizonnya belum lewat - angka yang membaik hanya karena
        waktu berjalan."""
        lap = evaluate([_diam("0.4"), _diam("0.3"), _diam(None), _diam(None)])

        assert lap.evidence.total == 2
        assert lap.unknown == 2

    def test_akurasi_dihitung_dari_benar_lawan_terlewat(self) -> None:
        lap = evaluate([_diam("0.4")] * 3 + [_diam("5.0")])

        assert lap.evidence.wins == 3
        assert lap.evidence.losses == 1

    def test_sample_tipis_tidak_menyimpulkan(self) -> None:
        """Gerbang bukti yang sama dengan Phase 12 - tiga dari tiga bukan
        'diamnya selalu tepat'."""
        lap = evaluate([_diam("0.4")] * 3)

        assert not lap.evidence.conclusive
        assert "SAMPLE BELUM CUKUP" in lap.summary()

    def test_yang_terlewat_diurutkan_dari_gerak_terbesar(self) -> None:
        lap = evaluate([_diam("2.5"), _diam("-9.0"), _diam("4.0")])

        assert [abs(d.move_pct) for d in lap.missed] == [
            Decimal("9.0"), Decimal("4.0"), Decimal("2.5")
        ]

    def test_alasan_yang_paling_sering_melewatkan_dihitung(self) -> None:
        """PASAL 14.33 meminta SEBABNYA dicari, bukan angka totalnya."""
        lap = evaluate([
            _diam("5.0", alasan="risiko terlalu tinggi"),
            _diam("6.0", alasan="risiko terlalu tinggi"),
            _diam("4.0", alasan="bukti bertentangan"),
            _diam("0.2", alasan="risiko terlalu tinggi"),
        ])

        assert lap.reasons[0] == ("risiko terlalu tinggi", 2)

    def test_kosong_dikatakan_bukan_nol_persen(self) -> None:
        lap = evaluate([])

        assert lap.accuracy is None
        assert "belum ada" in lap.summary()

    def test_semua_belum_terukur_dikatakan(self) -> None:
        lap = evaluate([_diam(None)] * 5)

        assert lap.accuracy is None
        assert "5 menunggu" in lap.summary()


class TestPeringatannyaIkutTercetak:
    """PASAL 14.33: 'jangan langsung menurunkan threshold hanya untuk mengejar
    missed opportunity'."""

    def test_laporannya_menyebut_larangan_itu(self) -> None:
        teks = "\n".join(evaluate([_diam("5.0")] * 3).report())

        assert "BUKAN izin menurunkan ambang" in teks

    def test_modulnya_tidak_punya_jalur_mengubah_ambang(self) -> None:
        """Ia mengukur, dan berhenti di situ."""
        import inspect

        from aruna.decision import silence

        sumber = inspect.getsource(silence)
        for terlarang in (
            "set_threshold", "update_threshold", "GERAK_BERARTI_PCT =",
        ):
            assert sumber.count(terlarang) <= 1, terlarang


class TestAmbangnyaMasukAkal:
    def test_melebihi_ongkos_bolak_balik(self) -> None:
        """Sebuah 'kesempatan' harus menyisakan sesuatu sesudah biaya - kalau
        tidak, yang terlewat itu kerugian, bukan peluang."""
        assert Decimal("1.0") <= GERAK_BERARTI_PCT

    def test_tidak_setinggi_itu_sampai_tidak_pernah_berbunyi(self) -> None:
        assert Decimal("5.0") >= GERAK_BERARTI_PCT


@pytest.mark.parametrize("gerak", ["0", "-0.0", "0.001"])
def test_tidak_bergerak_sama_sekali_itu_benar(gerak) -> None:
    assert _diam(gerak).verdict is Vonis.CORRECT
