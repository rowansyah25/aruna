"""Dua ketidakcocokan yang membuat ingatan diam selamanya kalau tidak dijembatani.

Terukur 2026-08-21, dan keduanya baru terlihat saat penyambungan hendak
dilakukan - bukan dari kode, bukan dari test, hanya dari menghitung isi
tabelnya:

* ingatan bersimbol ``BTCUSDT``: **nol** (ingatan mengeja ``BTC/USDT``)
* ingatan pada timeframe 4h: **nol** (keputusan futures dibuat di 4h)

Berkas ini menjaga jembatannya, dan menjaga satu hal lagi yang lebih mudah
salah: bahwa ingatan yang **dipinjam** dari timeframe lain selalu mengaku
dipinjam.
"""

from __future__ import annotations

from aruna.memory.lookup import horizon_ingatan, simbol_pasar


class TestEjaanSimbol:
    def test_perpetual_dijembatani_ke_ejaan_ingatan(self) -> None:
        assert simbol_pasar("BTCUSDT") == "BTC/USDT"
        assert simbol_pasar("AVAXUSDT") == "AVAX/USDT"

    def test_yang_sudah_bergaris_miring_dibiarkan(self) -> None:
        assert simbol_pasar("BTC/USDT") == "BTC/USDT"

    def test_yang_bukan_pasangan_usdt_dibiarkan(self) -> None:
        """§33: CRYPTO hanya pasangan USDT. Apa pun di luar itu bukan simbol
        perpetual yang perlu dijembatani, dan memotong empat huruf terakhirnya
        akan merusak namanya."""
        assert simbol_pasar("BBCA") == "BBCA"

    def test_kosong_tidak_meledak(self) -> None:
        assert simbol_pasar(None) == ""


class TestPemilihanHorizon:
    def test_horizonnya_sendiri_selalu_menang(self) -> None:
        """Begitu ingatan 4h melewati ambang, ia dipilih sendiri - tidak ada
        yang perlu diubah, dan tidak ada yang perlu ingat untuk mengubahnya."""
        tf, dipinjam = horizon_ingatan(
            "4h", tersedia={"4h": 500, "1h": 2189}, minimum=20
        )

        assert tf == "4h"
        assert dipinjam is False

    def test_meminjam_tetangga_saat_horizonnya_kosong(self) -> None:
        """Keadaan hari ini: nol ingatan 4h, 2.189 di 1h."""
        tf, dipinjam = horizon_ingatan(
            "4h", tersedia={"4h": 0, "1h": 2189, "1d": 800}, minimum=20
        )

        assert tf == "1h"
        assert dipinjam is True

    def test_yang_dipinjam_selalu_mengaku_dipinjam(self) -> None:
        """PASAL 15.14. Konteks 1h yang dicetak seolah-olah 4h membuat operator
        menimbang bukti yang bukan miliknya - dan tidak ada satu pun cara ia
        bisa mengetahuinya."""
        _, dipinjam = horizon_ingatan(
            "4h", tersedia={"1h": 100}, minimum=20
        )

        assert dipinjam is True

    def test_tetangga_yang_juga_tipis_dilewati(self) -> None:
        tf, _ = horizon_ingatan(
            "4h", tersedia={"4h": 3, "1h": 5, "1d": 900}, minimum=20
        )

        assert tf == "1d"

    def test_tidak_ada_yang_cukup_bukan_kegagalan(self) -> None:
        """PASAL 15.37: ARUNA tetap menganalisis normal tanpa kecocokan
        historis. Yang dilarang adalah mengarang buktinya."""
        tf, dipinjam = horizon_ingatan(
            "4h", tersedia={"4h": 1, "1h": 2}, minimum=20
        )

        assert tf is None
        assert dipinjam is False

    def test_horizon_yang_tidak_dikenal_mencoba_dirinya_sendiri(self) -> None:
        tf, dipinjam = horizon_ingatan(
            "30m", tersedia={"30m": 50}, minimum=20
        )

        assert tf == "30m"
        assert dipinjam is False

    def test_menerima_enum_horizon(self) -> None:
        """Jalur futures mengoper ``Horizon``, bukan string - dan ``.value``
        yang terlewat menghasilkan pencarian yang tidak pernah cocok."""
        from aruna.core.enums import Horizon

        tf, _ = horizon_ingatan(
            Horizon.H4, tersedia={"1h": 100}, minimum=20
        )

        assert tf == "1h"
