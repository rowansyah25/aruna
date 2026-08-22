"""PASAL 15.25: ingatan IMMUTABLE sesudah hasilnya final.

Larangan yang hanya ditulis di dokumen akan dilanggar oleh kode yang tidak
membaca dokumen. Yang menahannya di sini adalah tipe: ``Ingatan`` beku, tanpa
setter, jadi "mengubah outcome" berhenti menjadi pilihan yang tersedia.

§11.21 sudah melarang menghapus LOSS dan mengubah signal lama. Sebuah ingatan
yang bisa disunting adalah jalan memutar untuk keduanya - dan jalan memutar
yang tidak terlihat, karena ia tidak menyentuh tabel aslinya sama sekali.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.record import (
    CAKUPAN_RENDAH,
    CAKUPAN_TINGGI,
    KUNCI_UNIK,
    Hasil,
    Ingatan,
    Mutu,
    mutu_dari,
)

DIKUNCI = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
SELESAI = datetime(2026, 8, 20, 10, 30, tzinfo=UTC)


def _sidik() -> Sidik:
    return Sidik(nilai={
        **{d: UNKNOWN for d in Dimensi},
        Dimensi.ASSET: "BTC/USDT",
        Dimensi.MARKET: "CRYPTO",
        Dimensi.TIMEFRAME: "15m",
        Dimensi.REGIME: "TRENDING",
    })


@pytest.fixture
def ingatan() -> Ingatan:
    return Ingatan(
        signal_id="b6fad072584e423f",
        sidik=_sidik(),
        arah="BUY",
        hasil=Hasil.LOSS,
        move_pct=Decimal("-1.20"),
        locked_at=DIKUNCI,
        resolved_at=SELESAI,
        model_version="1.0.0+phase10",
        cakupan=50,
        mutu=Mutu.MEDIUM,
    )


class TestTidakBisaDiubah:
    def test_hasilnya_tidak_bisa_ditulis_ulang(self, ingatan: Ingatan) -> None:
        with pytest.raises(FrozenInstanceError):
            ingatan.hasil = Hasil.WIN  # type: ignore[misc]

    def test_waktunya_tidak_bisa_ditulis_ulang(self, ingatan: Ingatan) -> None:
        with pytest.raises(FrozenInstanceError):
            ingatan.locked_at = datetime(2020, 1, 1, tzinfo=UTC)  # type: ignore[misc]

    def test_arahnya_tidak_bisa_ditulis_ulang(self, ingatan: Ingatan) -> None:
        """PASAL 15.25 menyebut agent vote dan model version secara terpisah;
        arah adalah bentuk paling ringkas dari keduanya, dan yang paling
        menggoda untuk "dirapikan" belakangan."""
        with pytest.raises(FrozenInstanceError):
            ingatan.arah = "SELL"  # type: ignore[misc]

    def test_kerugiannya_tetap_ada(self, ingatan: Ingatan) -> None:
        """§11.21: DILARANG menghapus LOSS. Ingatan yang bisa dihapus satu per
        satu adalah cherry picking dengan langkah tambahan."""
        assert ingatan.hasil is Hasil.LOSS
        assert ingatan.move_pct < 0


class TestMutu:
    def test_cakupan_penuh_dan_hasil_final_itu_tinggi(self) -> None:
        assert mutu_dari(
            cakupan=100, hasil=Hasil.WIN,
            locked_at=DIKUNCI, resolved_at=SELESAI,
        ) is Mutu.HIGH

    def test_hasil_yang_belum_final_tidak_pernah_tinggi(self) -> None:
        """Ingatan tanpa hasil tidak bisa mengajari apa pun tentang hasil.
        Memberinya bobot tinggi berarti kemiripan dinilai dari kondisinya saja,
        lalu dilaporkan seolah-olah hasilnya sudah diketahui."""
        assert mutu_dari(
            cakupan=100, hasil=Hasil.UNKNOWN,
            locked_at=DIKUNCI, resolved_at=None,
        ) is Mutu.LOW

    def test_hasil_ada_tapi_waktunya_tidak_juga_rendah(self) -> None:
        """PASAL 15.24 menyebut timestamp integrity sebagai faktor mutu.
        Hasil tanpa waktu resolusi tidak bisa disaring PASAL 15.39 - dan yang
        tidak bisa disaring tidak boleh berbobot tinggi."""
        assert mutu_dari(
            cakupan=100, hasil=Hasil.WIN,
            locked_at=DIKUNCI, resolved_at=None,
        ) is Mutu.LOW

    def test_cakupan_tipis_menurunkan_mutu(self) -> None:
        assert mutu_dari(
            cakupan=CAKUPAN_RENDAH - 1, hasil=Hasil.WIN,
            locked_at=DIKUNCI, resolved_at=SELESAI,
        ) is Mutu.LOW

    def test_cakupan_menengah_jadi_medium(self) -> None:
        assert mutu_dari(
            cakupan=(CAKUPAN_RENDAH + CAKUPAN_TINGGI) // 2, hasil=Hasil.WIN,
            locked_at=DIKUNCI, resolved_at=SELESAI,
        ) is Mutu.MEDIUM

    def test_resolusi_sebelum_penguncian_ditolak(self) -> None:
        """Waktu yang terbalik berarti jam yang salah atau baris yang tertukar.
        Menerimanya berarti PASAL 15.39 menyaring terhadap angka yang tidak
        berarti apa-apa - dan penyaringnya akan terlihat bekerja."""
        assert mutu_dari(
            cakupan=100, hasil=Hasil.WIN,
            locked_at=SELESAI, resolved_at=DIKUNCI,
        ) is Mutu.LOW


class TestAntiDuplikat:
    def test_kunci_uniknya_menyebut_signal_id(self) -> None:
        """PASAL 15.26: satu peristiwa satu ingatan. ``signal_id`` sudah unik
        di ``signal_snapshots``, jadi tidak perlu kunci baru yang bisa
        berselisih dengan yang lama."""
        assert "signal_id" in KUNCI_UNIK
