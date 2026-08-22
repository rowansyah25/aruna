"""Jejak audit satu keputusan (PASAL 14.30).

"Harus dapat direkonstruksi" adalah ujian yang lebih keras daripada "harus
dicatat": baris log yang menyimpan hasil tanpa menyimpan apa yang
menghasilkannya hanya bisa dipercaya atau tidak.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aruna.decision import Arah
from aruna.decision.trail import (
    BERARAH,
    SELALU,
    Jejak,
    Rekaman,
    TrailError,
    record,
    require_reconstructable,
    required_fields,
)

#: Dua puluh tiga kolom PASAL 14.30, diketik ulang dari spesifikasinya.
#:
#: Sengaja tidak diturunkan dari ``Jejak``: daftar yang dibangun dari kodenya
#: sendiri akan tetap cocok betapa pun banyak kolom yang hilang.
PASAL_14_30 = [
    "signal id", "waktu", "aset", "pasar", "timeframe", "rezim pasar",
    "suara agent", "argumen agent", "protes", "veto", "keputusan council",
    "signal quality", "confidence", "risk score", "strategi", "versi model",
    "decision score", "keputusan final", "entry", "stop loss", "take profit",
    "syarat pembatalan", "masa berlaku",
]

LENGKAP = {j: f"nilai-{j.name.lower()}" for j in Jejak}


class TestKelengkapan:
    def test_dua_puluh_tiga_kolom_persis_seperti_pasalnya(self) -> None:
        assert [j.value for j in Jejak] == PASAL_14_30

    def test_kolom_berarah_dan_selalu_menutup_seluruhnya(self) -> None:
        assert BERARAH | set(SELALU) == set(Jejak)
        assert not BERARAH & set(SELALU)
        assert len(BERARAH) == 5

    def test_lengkap_bisa_direkonstruksi(self) -> None:
        assert record(Arah.LONG, LENGKAP).reconstructable


class TestKolomKosongBukanTidakAda:
    def test_kolom_blank_menahan_rekonstruksi(self) -> None:
        r = record(Arah.LONG, LENGKAP | {Jejak.PROTESTS: "   "})

        assert not r.reconstructable
        assert r.missing == (Jejak.PROTESTS,)

    def test_kolom_hilang_sama_dengan_blank(self) -> None:
        kurang = dict(LENGKAP)
        del kurang[Jejak.VETO]

        assert record(Arah.LONG, kurang).missing == (Jejak.VETO,)

    def test_tidak_ada_protes_adalah_pengamatan_dan_diterima(self) -> None:
        """"tidak ada protes" adalah hasil pengamatan; kolom blank berarti
        tidak ada yang tahu apakah lapisan itu berjalan."""
        r = record(
            Arah.LONG,
            LENGKAP | {Jejak.PROTESTS: "tidak ada", Jejak.VETO: "-"},
        )

        assert r.reconstructable

    def test_penyusun_tidak_menolak_yang_setengah_jadi(self) -> None:
        """Rekaman setengah jadi justru bentuk yang perlu dilihat ketika sebuah
        lapisan gagal melapor."""
        r = record(Arah.LONG, {Jejak.ASSET: "BTC/USDT"})

        assert not r.reconstructable
        assert r.isi[Jejak.ASSET] == "BTC/USDT"

    def test_penjaga_menyebut_kolom_yang_hilang(self) -> None:
        kurang = dict(LENGKAP)
        del kurang[Jejak.RISK_SCORE]

        with pytest.raises(TrailError, match="risk score"):
            require_reconstructable(record(Arah.LONG, kurang))

    def test_penjaga_meloloskan_yang_lengkap(self) -> None:
        r = record(Arah.LONG, LENGKAP)

        assert require_reconstructable(r) is r


class TestWajibBergantungArah:
    def test_no_signal_tidak_wajib_punya_entry(self) -> None:
        """Tidak ada yang dimasuki dan tidak ada yang bisa runtuh. Menuntutnya
        akan memaksa lapisan di atasnya mengarang (PASAL 13.26)."""
        tanpa_harga = {j: LENGKAP[j] for j in SELALU}

        assert record(Arah.NO_SIGNAL, tanpa_harga).reconstructable

    def test_keputusan_berarah_tetap_wajib_punya_entry(self) -> None:
        tanpa_harga = {j: LENGKAP[j] for j in SELALU}
        r = record(Arah.LONG, tanpa_harga)

        assert not r.reconstructable
        assert set(r.missing) == set(BERARAH)

    def test_kolom_wajib_dihitung_per_arah(self) -> None:
        assert set(required_fields(Arah.NO_SIGNAL)) == set(SELALU)
        assert set(required_fields(Arah.LONG)) == set(Jejak)
        assert set(required_fields(Arah.SHORT)) == set(Jejak)


class TestSidikJari:
    def test_isi_sama_menghasilkan_sidik_jari_sama(self) -> None:
        assert record(Arah.LONG, LENGKAP).fingerprint == (
            record(Arah.LONG, LENGKAP).fingerprint
        )

    def test_urutan_penyusunan_tidak_mengubahnya(self) -> None:
        """Sidik jari disusun ulang menurut urutan enum, bukan menurut urutan
        yang kebetulan dipakai penyusunnya.

        Dibangun LANGSUNG, bukan lewat ``record``: ``record`` sudah mengurutkan
        menurut enum, jadi lewat sana penjaga di dalam ``fingerprint`` tidak
        pernah tersentuh dan bisa dicabut tanpa satu test pun berubah merah.
        """
        maju = Rekaman(Arah.LONG, tuple((j, LENGKAP[j]) for j in Jejak))
        mundur = Rekaman(Arah.LONG, tuple(reversed(maju.values)))

        assert maju.fingerprint == mundur.fingerprint

    def test_record_juga_menyusun_ulang(self) -> None:
        terbalik = dict(reversed(list(LENGKAP.items())))

        assert record(Arah.LONG, terbalik).values == record(Arah.LONG, LENGKAP).values

    def test_satu_angka_digeser_mengubahnya(self) -> None:
        """PASAL 14.24 tidak bisa ditegakkan oleh niat baik; ia butuh sesuatu
        yang berubah kalau isinya berubah."""
        asli = record(Arah.LONG, LENGKAP)
        diubah = record(Arah.LONG, LENGKAP | {Jejak.ENTRY: "64121"})

        assert asli.fingerprint != diubah.fingerprint
        assert not diubah.unchanged_since(asli.fingerprint)

    def test_arah_ikut_masuk_sidik_jari(self) -> None:
        """Mengubah LONG menjadi SHORT tanpa mengubah satu angka pun adalah
        persis suntingan yang PASAL 14.24 larang."""
        a = record(Arah.LONG, LENGKAP)
        b = replace(a, decision=Arah.SHORT)

        assert a.fingerprint != b.fingerprint

    def test_sidik_jari_yang_sama_dinyatakan_utuh(self) -> None:
        r = record(Arah.LONG, LENGKAP)

        assert r.unchanged_since(r.fingerprint)


class TestLaporan:
    def test_kolom_kosong_ditandai_bukan_disembunyikan(self) -> None:
        kurang = dict(LENGKAP)
        del kurang[Jejak.CONFIDENCE]

        teks = "\n".join(record(Arah.LONG, kurang).report())

        assert "(KOSONG)" in teks
        assert "TIDAK BISA DIREKONSTRUKSI" in teks
        assert "confidence" in teks

    def test_no_signal_tidak_mencetak_kolom_harga(self) -> None:
        tanpa_harga = {j: LENGKAP[j] for j in SELALU}
        teks = "\n".join(record(Arah.NO_SIGNAL, tanpa_harga).report())

        assert "stop loss" not in teks
        assert "TIDAK BISA DIREKONSTRUKSI" not in teks

    def test_sidik_jarinya_ikut_dicetak(self) -> None:
        r = record(Arah.LONG, LENGKAP)

        assert r.fingerprint[:16] in "\n".join(r.report())
