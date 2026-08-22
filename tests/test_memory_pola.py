"""PASAL 15.16: pola yang sudah teridentifikasi, dibaca - bukan dihitung ulang.

PASAL 15.33 melarang menggabungkan fungsi Phase 12 dan Phase 15: **Phase 12
MENEMUKAN pola, Phase 15 MENGINGATNYA**. Menghitung ulang di sini akan
menghasilkan dua katalog pola yang bisa berselisih, dan tidak ada yang tahu
mana yang dijalankan - kesalahan yang sama persis dengan "council kedua" yang
rencana Phase 14 tolak.

Terukur 2026-08-21: `discovered_patterns` berisi **368 pola**, dan hanya **57**
yang mengalahkan baseline. Membaca pola yang tidak mengalahkan baseline sebagai
bukti adalah membaca derau yang sudah diberi nama.

Dan pasalnya menutup dengan kalimatnya sendiri: *"Pattern Memory bukan
prediction otomatis."*
"""

from __future__ import annotations

import pytest

from aruna.memory.pola import Pola, cocokkan, dari_baris


def _pola(kunci: str, dim: dict[str, str], *, n: int = 200,
          wr: float = 0.55, beats: bool = True) -> Pola:
    return Pola(
        kunci=kunci, dimensi=dim, sampel=n, win_rate=wr,
        ci=(wr - 0.05, wr + 0.05), beats_baseline=beats,
    )


class TestPembacaan:
    def test_dari_baris_database(self) -> None:
        """Bentuknya disalin dari produksi: `dimensions` adalah JSON."""
        pola = dari_baris({
            "pattern_key": "direction=BUY|horizon=1h",
            "dimensions": '{"horizon": "1h", "direction": "BUY"}',
            "sample_size": 993,
            "win_rate": 0.40282,
            "ci_low": 0.37275,
            "ci_high": 0.43364,
            "beats_baseline": 1,
        })

        assert pola.dimensi == {"horizon": "1h", "direction": "BUY"}
        assert pola.sampel == 993
        assert pola.beats_baseline is True

    def test_json_rusak_tidak_meledak(self) -> None:
        """Kolom yang formatnya berubah adalah kegagalan pembacaan, bukan
        alasan untuk menjatuhkan seluruh keputusan."""
        assert dari_baris({
            "pattern_key": "x", "dimensions": "bukan json",
            "sample_size": 10, "win_rate": 0.5, "ci_low": 0.4,
            "ci_high": 0.6, "beats_baseline": 1,
        }) is None


class TestPencocokan:
    def _katalog(self) -> list[Pola]:
        return [
            _pola("horizon=1h", {"horizon": "1h"}, n=1069),
            _pola("direction=BUY|horizon=1h",
                  {"horizon": "1h", "direction": "BUY"}, n=993),
            _pola("direction=BUY|horizon=1h|symbol=SOL/USDT",
                  {"horizon": "1h", "direction": "BUY", "symbol": "SOL/USDT"},
                  n=196),
            _pola("symbol=BTC/USDT", {"symbol": "BTC/USDT"}, n=215),
        ]

    def test_yang_paling_spesifik_menang(self) -> None:
        """Pola tiga dimensi menerangkan kondisi ini lebih tepat daripada pola
        satu dimensi, meskipun sampelnya lebih kecil."""
        pola = cocokkan(self._katalog(), symbol="SOL/USDT", timeframe="1h",
                        arah="BUY")

        assert pola is not None
        assert pola.kunci == "direction=BUY|horizon=1h|symbol=SOL/USDT"

    def test_yang_tidak_cocok_tidak_dipakai(self) -> None:
        """Pola BTC tidak menerangkan apa pun tentang SOL."""
        pola = cocokkan(self._katalog(), symbol="ADA/USDT", timeframe="1d",
                        arah="SELL")

        assert pola is None

    def test_dimensi_yang_tidak_disebut_pola_tidak_menghalangi(self) -> None:
        """Pola ``horizon=1h`` cocok untuk simbol apa pun di 1h - itu memang
        artinya."""
        pola = cocokkan(self._katalog(), symbol="ADA/USDT", timeframe="1h",
                        arah="SELL")

        assert pola is not None
        assert pola.kunci == "horizon=1h"

    def test_yang_tidak_mengalahkan_baseline_dilewati(self) -> None:
        """Terukur: 311 dari 368 pola TIDAK mengalahkan baseline. Membacanya
        sebagai bukti adalah membaca derau yang sudah diberi nama."""
        katalog = [
            _pola("direction=BUY|horizon=1h",
                  {"horizon": "1h", "direction": "BUY"}, beats=False),
        ]

        assert cocokkan(katalog, symbol="X/USDT", timeframe="1h",
                        arah="BUY") is None

    def test_sampel_tipis_dilewati(self) -> None:
        from aruna.memory.pola import SAMPEL_POLA

        katalog = [
            _pola("horizon=1h", {"horizon": "1h"}, n=SAMPEL_POLA - 1),
        ]

        assert cocokkan(katalog, symbol="X/USDT", timeframe="1h",
                        arah="BUY") is None

    def test_katalog_kosong_bukan_kegagalan(self) -> None:
        assert cocokkan([], symbol="X/USDT", timeframe="1h", arah="BUY") is None

    def test_simbol_perpetual_dijembatani(self) -> None:
        """Pola Phase 12 mengeja ``SOL/USDT``; jalur futures ``SOLUSDT``.
        Jembatan yang sama dengan ingatannya."""
        pola = cocokkan(self._katalog(), symbol="SOLUSDT", timeframe="1h",
                        arah="BUY")

        assert pola is not None
        assert "SOL/USDT" in pola.kunci


class TestBukanRamalan:
    def test_tidak_ada_bidang_prediksi(self) -> None:
        """PASAL 15.16 menutup dengan kalimatnya sendiri: Pattern Memory bukan
        prediction otomatis."""
        pola = _pola("horizon=1h", {"horizon": "1h"})

        assert not hasattr(pola, "prediksi")
        assert not hasattr(pola, "arah_disarankan")

    def test_ringkasnya_tidak_menjanjikan(self) -> None:
        kalimat = _pola("direction=BUY|horizon=1h",
                        {"horizon": "1h", "direction": "BUY"}).ringkas().lower()

        for terlarang in ("pasti", "akan naik", "peluang profit", "prediksi",
                          "chance", "probability"):
            assert terlarang not in kalimat

    def test_ringkasnya_menyebut_sampel_dan_sumbernya(self) -> None:
        """Win rate tanpa jumlah sampel adalah angka yang tidak bisa dinilai,
        dan tanpa sumbernya operator tidak tahu itu temuan Phase 12."""
        kalimat = _pola("horizon=1h", {"horizon": "1h"}, n=1069).ringkas()

        assert "1069" in kalimat
        assert "Phase 12" in kalimat

    def test_bekunya_dijaga(self) -> None:
        from dataclasses import FrozenInstanceError

        pola = _pola("horizon=1h", {"horizon": "1h"})

        with pytest.raises(FrozenInstanceError):
            pola.win_rate = 0.9  # type: ignore[misc]
