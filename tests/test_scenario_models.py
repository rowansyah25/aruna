"""Bentuk satu skenario (bagian 16.7, 16.15).

Bagian 16.15 mengeja sebelas bidang yang wajib ada, dan bagian 16.7
mengulanginya sebagai rincian tiap skenario. Dieja satu per satu di test ini
supaya penghapusan salah satunya gagal keras, bukan diam-diam hilang dari
keluaran.

**Dua batas yang paling mudah dilanggar**, dan keduanya dijaga di sini:

* Bagian 16.6: ``bobot`` **bukan** probabilitas pasar yang terkalibrasi. Nama
  seperti ``probability`` atau ``peluang_profit`` akan membuat pembaca
  berikutnya memperlakukannya begitu, dan bagian 16.1 justru melarang hasil
  simulasi dianggap kepastian.
* Bagian 16.18: Phase 16 tidak menghasilkan LONG atau SHORT. Skenario yang
  membawa bidang arah berhenti menjadi bukti dan menjadi keputusan kedua -
  dan keputusan final tetap milik Phase 14.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.scenario.models import (
    LABEL_BUKTI,
    HasilSkenario,
    Invalidasi,
    Kerapuhan,
    Skenario,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def _invalidasi(*syarat: str) -> Invalidasi:
    return Invalidasi(syarat=tuple(syarat or ("harga kembali di bawah resistance",)))


def _skenario(**kw) -> Skenario:
    dasar = {
        "scenario_id": "s-001",
        "market": "CRYPTO",
        "asset": "BTC/USDT",
        "timestamp": NOW,
        "nama": "Bullish Continuation",
        "deskripsi": "tembusan bertahan dan diikuti volume",
        "kondisi_awal": ("harga > resistance", "volume 2,1x rata-rata"),
        "pemicu": "BREAKOUT",
        "perkembangan": ("OI naik", "leverage naik", "volatilitas naik"),
        "invalidasi": _invalidasi(),
        "risiko": "MEDIUM",
        "keyakinan": 0.55,
        "bobot": 58,
        "bukti": ("struktur: higher high", "volume: 2,1x"),
        "versi_simulasi": "internal-1",
    }
    return Skenario(**(dasar | kw))


class TestSebelasBidang:
    @pytest.mark.parametrize(
        "bidang",
        [
            "scenario_id", "market", "asset", "timestamp", "nama",
            "pemicu", "invalidasi", "risiko", "keyakinan", "bukti",
            "versi_simulasi",
        ],
    )
    def test_bidang_bagian_16_15_ada(self, bidang) -> None:
        assert hasattr(_skenario(), bidang)

    def test_rincian_bagian_16_7_ada(self) -> None:
        s = _skenario()

        assert s.deskripsi
        assert s.kondisi_awal
        assert s.perkembangan


class TestBobotBukanProbabilitas:
    """Bagian 16.6 menyatakannya dengan huruf besar; di sini ia dijaga."""

    def test_tidak_ada_nama_yang_menjanjikan_probabilitas(self) -> None:
        terlarang = {
            "probability", "probabilitas", "chance", "peluang_profit",
            "win_probability", "odds",
        }
        punya = {n for n in dir(_skenario()) if not n.startswith("_")}

        assert not (punya & terlarang)

    def test_labelnya_melekat_di_keluaran(self) -> None:
        """Bagian 16.1: hasil simulasi harus berlabel SIMULATION EVIDENCE,
        bukan FACT dan bukan GUARANTEED PREDICTION."""
        d = _skenario().to_dict()

        assert d["label"] == LABEL_BUKTI
        assert "SIMULATION EVIDENCE" in LABEL_BUKTI

    def test_bobot_dilaporkan_sebagai_relatif(self) -> None:
        d = _skenario().to_dict()

        assert d["bobot"] == 58
        assert "relatif" in d["bobot_catatan"].lower()


class TestSkenarioHarusBisaSalah:
    """Bagian 16.11: setiap skenario punya invalidation condition."""

    def test_tanpa_invalidasi_ditolak(self) -> None:
        """Skenario yang tidak bisa salah bukan skenario - ia keyakinan yang
        dipakaikan format."""
        with pytest.raises(ValueError, match="invalidasi"):
            _skenario(invalidasi=Invalidasi(syarat=()))

    def test_dengan_invalidasi_diterima(self) -> None:
        assert _skenario().invalidasi.syarat


class TestKerapuhan:
    """Bagian 16.10: skenario yang bergantung pada satu syarat itu RAPUH."""

    def test_satu_syarat_berarti_rapuh(self) -> None:
        s = _skenario(invalidasi=_invalidasi("volume di bawah ambang"))

        assert s.kerapuhan is Kerapuhan.RAPUH

    def test_beberapa_syarat_berarti_kokoh(self) -> None:
        s = _skenario(
            invalidasi=_invalidasi(
                "harga kembali di bawah resistance", "volume runtuh"
            )
        )

        assert s.kerapuhan is Kerapuhan.KOKOH

    def test_kerapuhan_ikut_dilaporkan(self) -> None:
        """Kerapuhan yang dihitung tapi tidak dikeluarkan sama saja dengan
        tidak dihitung."""
        assert _skenario().to_dict()["kerapuhan"] == Kerapuhan.RAPUH.value


class TestTidakAdaArah:
    """Bagian 16.18: Phase 16 tidak menghasilkan FINAL LONG atau FINAL SHORT."""

    def test_tidak_ada_bidang_arah(self) -> None:
        punya = {n for n in dir(_skenario()) if not n.startswith("_")}

        assert not (punya & {"direction", "decision", "arah", "keputusan"})

    def test_modulnya_tidak_menyentuh_decision(self) -> None:
        """AST, bukan pencarian teks: docstring modulnya MENJELASKAN kenapa ia
        tidak menyentuh arah, dan pencarian teks akan tersandung pada
        penjelasannya sendiri - persis yang terjadi pada kalibrator."""
        import ast
        import inspect

        from aruna.scenario import models

        pohon = ast.parse(inspect.getsource(models))
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
        nama |= {n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)}
        nama |= {
            a.name for n in ast.walk(pohon)
            if isinstance(n, ast.ImportFrom) for a in n.names
        }

        assert not (nama & {"Decision", "direction", "decision"})


class TestHasilSkenario:
    def test_tiga_hasil_plus_belum(self) -> None:
        """Bagian 16.19 menyebut tiga; ``BELUM`` perlu karena skenario yang
        belum selesai bukan skenario yang salah."""
        assert {h.value for h in HasilSkenario} == {
            "BENAR", "SALAH", "SEBAGIAN", "BELUM"
        }
