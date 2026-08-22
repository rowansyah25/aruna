"""PASAL 15.20, 15.41, 15.42: bukti, jejaknya, dan yang TIDAK boleh ada.

Yang paling mudah dilanggar di seluruh Phase 15 ada di berkas ini: sebuah
bidang bernama ``decision`` di hasil akhirnya. Pemanggil berikutnya akan
membacanya sebagai keputusan, betapa pun dokumennya berkata lain - dan PASAL
15.42 menyatakan memory tidak boleh mengubah keputusan.

Hal kedua: **terhadap apa "mendukung" diukur.** Terukur 2026-08-21 atas 8.366
ingatan sungguhan - win rate dasar BUY 49,7%, SELL **14,6%**, dan WAIT 0,0%
dari 5.030 kasus. Menilai konteks terhadap titik netral 50% akan menyebut
hampir setiap konteks SHORT sebagai CONTRARY, bukan karena buktinya melainkan
karena titik bandingnya dikarang. Karena itu ``dasar`` wajib.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.memory.context import (
    MARGIN_PENGARUH,
    KonteksHistoris,
    Pengaruh,
    susun,
)
from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.outcome import ringkas
from aruna.memory.record import Hasil, Ingatan, Mutu
from aruna.memory.similarity import Kemiripan

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


def _ingatan(nomor: int, *, arah: str, hasil: Hasil) -> Ingatan:
    dikunci = NOW - timedelta(hours=nomor + 1)
    return Ingatan(
        signal_id=f"mem{nomor:013d}",
        sidik=Sidik(nilai={
            **{d: UNKNOWN for d in Dimensi},
            Dimensi.ASSET: "BTC/USDT",
        }),
        arah=arah,
        hasil=hasil,
        move_pct=Decimal("1.0000"),
        locked_at=dikunci,
        resolved_at=dikunci + timedelta(minutes=30),
        model_version="1.0.0+phase10",
        cakupan=95,
        mutu=Mutu.HIGH,
    )


def _pasangan(n: int, *, arah: str, menang: int, skor: int = 90):
    return [
        (
            _ingatan(i, arah=arah,
                     hasil=Hasil.WIN if i < menang else Hasil.LOSS),
            Kemiripan(skor=skor, cakupan=95, cocok=(Dimensi.ASSET,), beda=(),
                      tak_terbaca=(Dimensi.VOLATILITY,)),
        )
        for i in range(n)
    ]


#: Win rate dasar yang sungguhan, terukur: BUY 49,7% dan SELL 14,6%.
DASAR = ringkas(
    _pasangan(1000, arah="BUY", menang=497) + _pasangan(1000, arah="SELL", menang=146)
)


class TestPengaruh:
    def test_sejalan_dengan_dasarnya_disebut_supportive(self) -> None:
        """70% menang terhadap dasar 49,7% - dua puluh poin di atas."""
        cocok = _pasangan(40, arah="BUY", menang=28)

        k = susun(arah_sekarang="LONG", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.SUPPORTIVE

    def test_jauh_di_bawah_dasarnya_disebut_contrary(self) -> None:
        """PASAL 15.20: memory yang berlawanan TIDAK diikuti diam-diam dan
        TIDAK dibuang diam-diam. Ia dinamai."""
        cocok = _pasangan(40, arah="BUY", menang=8)

        k = susun(arah_sekarang="LONG", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.CONTRARY

    def test_selisih_kecil_tetap_netral(self) -> None:
        """Lima poin di atas dasar pada empat puluh kasus adalah dua kasus.
        Menyebutnya "mendukung" berarti membaca derau sebagai bukti."""
        cocok = _pasangan(40, arah="BUY", menang=22)

        k = susun(arah_sekarang="LONG", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.NEUTRAL

    def test_short_dinilai_terhadap_dasar_short(self) -> None:
        """Terukur: dasar SELL 14,6%. Sebuah konteks SHORT dengan win rate 30%
        JAUH DI ATAS dasarnya - dan akan disebut CONTRARY oleh siapa pun yang
        membandingkannya dengan lima puluh."""
        cocok = _pasangan(40, arah="SELL", menang=12)

        k = susun(arah_sekarang="SHORT", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.SUPPORTIVE

    def test_sampel_tidak_cukup_selalu_netral(self) -> None:
        """Tiga kasus tidak boleh menghasilkan SUPPORTIVE - itu confirmation
        bias dengan angka di belakangnya (PASAL 15.38)."""
        cocok = _pasangan(3, arah="BUY", menang=3)

        k = susun(arah_sekarang="LONG", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.NEUTRAL
        assert k.kontribusi == 0

    def test_arah_tanpa_kasus_netral(self) -> None:
        """Empat puluh kasus LONG tidak mengatakan apa pun tentang SHORT."""
        cocok = _pasangan(40, arah="BUY", menang=30)

        k = susun(arah_sekarang="SHORT", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.NEUTRAL

    def test_arah_yang_tidak_dikenali_netral(self) -> None:
        """``WAIT`` bukan arah, dan ingatan tidak punya pendapat tentangnya."""
        cocok = _pasangan(40, arah="BUY", menang=30)

        k = susun(arah_sekarang="WAIT", cocok=cocok, dasar=DASAR, as_of=NOW)

        assert k.pengaruh is Pengaruh.NEUTRAL


class TestTidakMemutuskan:
    def test_tidak_ada_bidang_keputusan(self) -> None:
        """PASAL 15.42. Sebuah bidang ``decision`` di sini akan dibaca
        pemanggil berikutnya sebagai keputusan, betapa pun dokumennya berkata
        lain."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=30),
                  dasar=DASAR, as_of=NOW)

        assert not hasattr(k, "decision")
        assert not hasattr(k, "keputusan")
        assert not hasattr(k, "arah_disarankan")

    def test_dasarnya_wajib(self) -> None:
        """Tanpa dasar, satu-satunya titik banding yang tersisa adalah angka
        yang dikarang - dan 50% akan menyebut hampir setiap konteks SHORT
        sebagai CONTRARY."""
        with pytest.raises(TypeError):
            susun(  # type: ignore[call-arg]
                arah_sekarang="LONG",
                cocok=_pasangan(40, arah="BUY", menang=30),
                as_of=NOW,
            )


class TestKontribusi:
    def test_selalu_di_dalam_nol_seratus(self) -> None:
        for n, menang in ((0, 0), (3, 3), (40, 30), (500, 400)):
            k = susun(arah_sekarang="LONG", cocok=_pasangan(n, arah="BUY", menang=menang),
                      dasar=DASAR, as_of=NOW)
            assert 0 <= k.kontribusi <= 100

    def test_sampel_besar_berkontribusi_lebih(self) -> None:
        kecil = susun(arah_sekarang="LONG", cocok=_pasangan(20, arah="BUY", menang=15),
                      dasar=DASAR, as_of=NOW)
        besar = susun(arah_sekarang="LONG", cocok=_pasangan(200, arah="BUY", menang=150),
                      dasar=DASAR, as_of=NOW)

        assert besar.kontribusi > kecil.kontribusi

    def test_kemiripan_rendah_berkontribusi_lebih_sedikit(self) -> None:
        tinggi = susun(arah_sekarang="LONG",
                       cocok=_pasangan(40, arah="BUY", menang=30, skor=98),
                       dasar=DASAR, as_of=NOW)
        rendah = susun(arah_sekarang="LONG",
                       cocok=_pasangan(40, arah="BUY", menang=30, skor=81),
                       dasar=DASAR, as_of=NOW)

        assert rendah.kontribusi < tinggi.kontribusi

    def test_bukan_probabilitas_profit(self) -> None:
        """PASAL 15.45 mengejanya: "Ini bukan probability profit". Yang
        menahannya bukan kalimat di docstring melainkan tidak adanya satu pun
        jalur dari kontribusi ke angka yang dicetak sebagai peluang."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=40),
                  dasar=DASAR, as_of=NOW)

        assert k.kontribusi != k.ringkasan.win_rate["LONG"]


class TestAudit:
    def test_memory_id_yang_dipakai_dicatat(self) -> None:
        """PASAL 15.41: tiap signal harus bisa menjawab memory mana yang
        dipakai. Konteks tanpa daftar itu tidak bisa diperiksa ulang."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=30),
                  dasar=DASAR, as_of=NOW)

        assert len(k.memory_ids) > 0
        assert all(isinstance(i, str) for i in k.memory_ids)

    def test_as_of_ikut_tercatat(self) -> None:
        """Jejak audit tanpa batas waktunya tidak bisa membuktikan PASAL 15.39
        dipatuhi pada saat keputusan itu dibuat."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=30),
                  dasar=DASAR, as_of=NOW)

        assert k.as_of == NOW

    def test_daftar_id_tidak_membengkak_tanpa_batas(self) -> None:
        """PASAL 14.30 pernah menghasilkan satu baris log 6.000 karakter di
        proyek ini. Lima ratus id di satu baris adalah bentuk yang sama."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(500, arah="BUY", menang=300),
                  dasar=DASAR, as_of=NOW)

        assert len(k.memory_ids) <= 20

    def test_catatan_pemanggil_ikut_terbawa(self) -> None:
        """Pemotongan kandidat terjadi di repositori, dan lapisan murni ini
        tidak bisa mengetahuinya sendiri - jadi pemanggil yang menyebutkannya,
        dan konteksnya yang membawanya sampai ke jejak audit."""
        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=30),
                  dasar=DASAR, as_of=NOW,
                  catatan=("kandidat dipotong pada 5000",))

        assert "kandidat dipotong pada 5000" in k.catatan


class TestAmbangnya:
    def test_marginnya_sepuluh_poin(self) -> None:
        assert MARGIN_PENGARUH == 10

    def test_bentuknya_beku(self) -> None:
        """PASAL 15.25 sejiwa: konteks yang bisa disunting sesudah disusun
        berarti jejak auditnya tidak membuktikan apa pun."""
        from dataclasses import FrozenInstanceError

        k = susun(arah_sekarang="LONG", cocok=_pasangan(40, arah="BUY", menang=30),
                  dasar=DASAR, as_of=NOW)

        assert isinstance(k, KonteksHistoris)
        with pytest.raises(FrozenInstanceError):
            k.pengaruh = Pengaruh.CONTRARY  # type: ignore[misc]
