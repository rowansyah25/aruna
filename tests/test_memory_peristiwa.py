"""PASAL 15.15: konteks peristiwa - dan bentuk yang datanya sanggup dukung.

Pasalnya membayangkan: EVENT -> Market Condition -> Reaction -> Outcome, dengan
contoh "BTC reaction +4.2%, duration 35 minutes".

**Terukur 2026-08-21, dan itu yang menentukan bentuknya.** ``news_events``
berisi 1.156 baris, tapi:

* **750 di antaranya bersentimen UNKNOWN** - dua pertiga;
* hanya **158 berita** yang tertaut ke aset mana pun (``news_asset_links``
  berisi 177 baris);
* kategorinya IDX - ``BI_RATE``, ``RUPIAH``, ``EARNINGS``, ``MANAGEMENT`` -
  sementara keputusan yang dinilai di sini kripto.

Membangun tabel reaksi peristiwa dari bahan itu akan menghasilkan tabel yang
sebagian besar isinya UNKNOWN, dan angka reaksi yang dihitung dari berita yang
tidak tertaut ke asetnya adalah angka yang dikarang (§13.26).

Yang **bisa** dijawab jujur, dan sudah tersimpan: apa yang terjadi pada
keputusan yang dibuat ketika keadaan berita seperti sekarang. Terukur:

===========  =======  ========  =========
keadaan      menang   kalah     win rate
NEGATIVE     17       56        **23%**
POSITIVE     320      315       50%
NO_NEWS      927      1.262     42%
NEUTRAL      62       74        46%
===========  =======  ========  =========

Selisih itu nyata dan sudah ada di data. Yang belum ada adalah yang membacanya.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.peristiwa import SAMPEL_PERISTIWA, Peristiwa, baca_peristiwa
from aruna.memory.record import Hasil, Ingatan, Mutu

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


def _ingatan(i: int, *, berita: str, hasil: Hasil, arah: str = "BUY") -> Ingatan:
    dikunci = NOW - timedelta(hours=i + 1)
    nilai = {d: UNKNOWN for d in Dimensi}
    nilai[Dimensi.ASSET] = "BTC/USDT"
    nilai[Dimensi.NEWS] = berita
    return Ingatan(
        signal_id=f"mem{i:013d}", sidik=Sidik(nilai=nilai), arah=arah,
        hasil=hasil, move_pct=Decimal("1.0000"), locked_at=dikunci,
        resolved_at=dikunci + timedelta(minutes=30),
        model_version="1.0.0", cakupan=95, mutu=Mutu.HIGH,
    )


def _korpus(negatif_menang: int, negatif_kalah: int, lain: int = 50):
    return (
        [_ingatan(i, berita="NEGATIVE", hasil=Hasil.WIN)
         for i in range(negatif_menang)]
        + [_ingatan(100 + i, berita="NEGATIVE", hasil=Hasil.LOSS)
           for i in range(negatif_kalah)]
        + [_ingatan(500 + i, berita="POSITIVE", hasil=Hasil.WIN)
           for i in range(lain)]
    )


class TestPembacaannya:
    def test_menghitung_hasil_pada_keadaan_berita_yang_sama(self) -> None:
        """Angka sungguhan dari produksi: NEGATIVE 17 menang, 56 kalah."""
        p = baca_peristiwa(_korpus(17, 56), keadaan="NEGATIVE")

        assert p is not None
        assert p.menang == 17
        assert p.kalah == 56
        assert p.win_rate == 23

    def test_yang_bukan_keadaan_sekarang_tidak_ikut(self) -> None:
        p = baca_peristiwa(_korpus(17, 56, lain=200), keadaan="NEGATIVE")

        assert p.total == 73

    def test_keadaan_tak_terbaca_tidak_menghasilkan_apa_pun(self) -> None:
        """UNKNOWN bukan keadaan - ia ketiadaan keadaan, dan mengelompokkan
        ketiadaan menghasilkan statistik tentang tidak ada yang tahu."""
        assert baca_peristiwa(_korpus(17, 56), keadaan=UNKNOWN) is None

    def test_sampel_tipis_tidak_menghasilkan_apa_pun(self) -> None:
        """Lima kasus berita negatif bukan bukti tentang berita negatif."""
        assert baca_peristiwa(_korpus(2, 3), keadaan="NEGATIVE") is None

    def test_yang_tidak_berarah_tidak_dihitung(self) -> None:
        """WAIT tidak mempertaruhkan apa pun - alasan yang sama seperti di
        ``outcome.ringkas``."""
        korpus = _korpus(17, 56) + [
            _ingatan(900 + i, berita="NEGATIVE", hasil=Hasil.LOSS, arah="WAIT")
            for i in range(100)
        ]

        p = baca_peristiwa(korpus, keadaan="NEGATIVE")

        assert p.total == 73

    def test_korpus_kosong_bukan_kegagalan(self) -> None:
        assert baca_peristiwa([], keadaan="NEGATIVE") is None


class TestKalimatnya:
    def test_menyebut_keadaan_jumlah_dan_hasilnya(self) -> None:
        kalimat = baca_peristiwa(_korpus(17, 56), keadaan="NEGATIVE").ringkas()

        assert "NEGATIVE" in kalimat
        assert "73" in kalimat
        assert "23" in kalimat

    def test_tidak_menjanjikan_apa_pun(self) -> None:
        """PASAL 15.48: bukti, bukan jaminan."""
        kalimat = baca_peristiwa(
            _korpus(17, 56), keadaan="NEGATIVE"
        ).ringkas().lower()

        for terlarang in ("pasti", "akan", "peluang profit", "chance",
                          "probability", "prediksi"):
            assert terlarang not in kalimat


class TestBentuknya:
    def test_bekunya_dijaga(self) -> None:
        from dataclasses import FrozenInstanceError

        p = baca_peristiwa(_korpus(17, 56), keadaan="NEGATIVE")

        with pytest.raises(FrozenInstanceError):
            p.menang = 0  # type: ignore[misc]

    def test_tidak_ada_bidang_arah(self) -> None:
        p = baca_peristiwa(_korpus(17, 56), keadaan="NEGATIVE")

        assert not hasattr(p, "arah")
        assert isinstance(p, Peristiwa)

    def test_ambangnya_masuk_akal(self) -> None:
        assert SAMPEL_PERISTIWA >= 20
