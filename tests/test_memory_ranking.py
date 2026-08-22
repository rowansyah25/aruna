"""PASAL 15.11 dan 15.21: yang baru lebih berat, yang lama tidak pernah nol.

Satu hal harus dieja di sini supaya tidak disalahbaca nanti: **korpus ARUNA
baru beberapa hari**. Terukur 2026-08-21, ``market_memories`` membentang
2026-08-17 sampai 2026-08-20. Pada setengah-umur tiga puluh hari, seluruh bobot
kebaruan sekarang berada di antara 0,91 dan 1,00 - artinya peluruhan praktis
tidak berpengaruh hari ini.

Itu bukan alasan menghapusnya, dan bukan alasan mengecilkan setengah-umurnya
supaya angkanya "terlihat bekerja". Itu alasan menuliskannya, supaya pembaca
berikutnya tidak menyimpulkan bahwa mekanismenya sudah terbukti.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.ranking import (
    SETENGAH_UMUR_HARI,
    bobot_kebaruan,
    peringkat,
)
from aruna.memory.record import Hasil, Ingatan, Mutu
from aruna.memory.similarity import Kemiripan

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


def _ingatan(nomor: int, *, umur_hari: float, mutu: Mutu = Mutu.HIGH) -> Ingatan:
    dikunci = NOW - timedelta(days=umur_hari)
    return Ingatan(
        signal_id=f"mem{nomor:013d}",
        sidik=Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.ASSET: "BTC/USDT",
        }),
        arah="BUY",
        hasil=Hasil.WIN,
        move_pct=Decimal("1.0000"),
        locked_at=dikunci,
        resolved_at=dikunci + timedelta(minutes=30),
        model_version="1.0.0+phase10",
        cakupan=95,
        mutu=mutu,
    )


def _mirip(skor: int) -> Kemiripan:
    return Kemiripan(
        skor=skor, cakupan=95, cocok=(Dimensi.ASSET,), beda=(),
        tak_terbaca=(Dimensi.VOLATILITY,),
    )


class TestKebaruan:
    def test_yang_baru_berbobot_penuh(self) -> None:
        assert bobot_kebaruan(0.0) == pytest.approx(1.0)

    def test_setengah_umur_memberi_setengah_bobot(self) -> None:
        assert bobot_kebaruan(SETENGAH_UMUR_HARI) == pytest.approx(0.5, abs=0.01)

    def test_yang_sangat_tua_tetap_tidak_nol(self) -> None:
        """PASAL 15.21: HISTORICAL VALUE != ZERO. Data lama tetap berguna untuk
        konteks jangka panjang, dan bobot nol sama saja dengan menghapusnya -
        yang pasalnya larang secara eksplisit."""
        assert bobot_kebaruan(3650.0) > 0

    def test_korpus_beberapa_hari_hampir_tidak_meluruh(self) -> None:
        """Terukur: seluruh ingatan berumur 0-4 hari. Test ini ada supaya angka
        yang nyaris seragam itu tidak dibaca sebagai bug oleh yang berikutnya."""
        assert bobot_kebaruan(4.0) > 0.85

    def test_umur_negatif_tidak_menaikkan_bobot(self) -> None:
        """Jam yang salah atau ingatan yang stempel waktunya di masa depan akan
        menghasilkan umur negatif, dan pangkat negatif memberi bobot DI ATAS
        satu - sebuah ingatan yang lebih berharga daripada yang baru saja
        terjadi, karena jamnya rusak."""
        assert bobot_kebaruan(-10.0) <= 1.0


class TestPeringkat:
    def test_yang_lebih_mirip_lebih_dulu(self) -> None:
        cocok = [
            (_ingatan(1, umur_hari=1), _mirip(82)),
            (_ingatan(2, umur_hari=1), _mirip(96)),
        ]

        hasil = peringkat(cocok, as_of=NOW)

        assert hasil[0][0].signal_id == "mem0000000000002"

    def test_pada_kemiripan_sama_yang_lebih_baru_menang(self) -> None:
        """PASAL 15.22 mengurutkan similarity lebih dulu, lalu kebaruan."""
        cocok = [
            (_ingatan(1, umur_hari=90), _mirip(90)),
            (_ingatan(2, umur_hari=1), _mirip(90)),
        ]

        hasil = peringkat(cocok, as_of=NOW)

        assert hasil[0][0].signal_id == "mem0000000000002"

    def test_mutu_rendah_kalah_dari_mutu_tinggi(self) -> None:
        """PASAL 15.24: low-quality memory tidak boleh berbobot tinggi."""
        cocok = [
            (_ingatan(1, umur_hari=1, mutu=Mutu.LOW), _mirip(90)),
            (_ingatan(2, umur_hari=1, mutu=Mutu.HIGH), _mirip(90)),
        ]

        hasil = peringkat(cocok, as_of=NOW)

        assert hasil[0][0].signal_id == "mem0000000000002"

    def test_bobotnya_ikut_dipulangkan(self) -> None:
        """Peringkat tanpa angkanya tidak bisa diperiksa ulang - PASAL 15.41
        menuntut tiap signal bisa menjawab memory mana yang dipakai dan
        seberapa berat."""
        hasil = peringkat([(_ingatan(1, umur_hari=1), _mirip(90))], as_of=NOW)

        assert len(hasil[0]) == 3
        assert 0 < hasil[0][2] <= 1.0

    def test_kosong_tetap_kosong(self) -> None:
        assert peringkat([], as_of=NOW) == []
