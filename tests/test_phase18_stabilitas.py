"""Keputusan tidak boleh berbalik tanpa konfirmasi (bagian 18.25 - 18.28).

**Celah 6, dan bentuknya halus.** `repetition.py` sudah menahan pengulangan -
kandidat yang arahnya SAMA dengan yang barusan, di setup yang belum berubah,
ditolak sebagai duplikat. Tapi sebuah PEMBALIKAN bukan duplikat: LONG lalu
SHORT punya arah yang berbeda, jadi ia lolos setiap pemeriksaan yang ada.

Urutan yang bagian 18.25 larang karena itu bisa terjadi tanpa satu pun
penjaga::

    10:00 LONG   10:01 NO SIGNAL   10:02 LONG   10:03 SHORT   10:04 LONG
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from aruna.signals.repetition import MATERIAL_MOVE_PCT
from aruna.signals.stabilitas import (
    Peralihan,
    hitung_pembalikan,
    perlu_konfirmasi,
)

SAAT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _s(arah: str) -> NS:
    return NS(direction=NS(value=arah))


class TestPembalikanButuhKonfirmasi:
    def test_flip_tanpa_gerak_ditahan(self) -> None:
        """Contoh bagian 18.27: LONG tidak langsung menjadi SHORT hanya karena
        satu candle bearish kecil."""
        alasan = perlu_konfirmasi(_s("BUY"), _s("SELL"), gerak_pct=-0.1)

        assert alasan
        assert "belum terkonfirmasi" in alasan[0]

    def test_flip_dengan_gerak_melawan_boleh(self) -> None:
        """Klaim lama tidak cukup "kurang didukung" - ia harus terbukti salah.
        Harga yang bergerak melawan LONG sebesar ambang membuktikannya."""
        alasan = perlu_konfirmasi(
            _s("BUY"), _s("SELL"), gerak_pct=-(MATERIAL_MOVE_PCT + 0.1)
        )

        assert alasan == ()

    def test_gerak_MENDUKUNG_arah_lama_tidak_membenarkan_flip(self) -> None:
        """**Yang paling mudah salah.** Harga naik kuat sesudah LONG adalah
        bukti LONG benar - bukan izin untuk berbalik SHORT. Yang dihitung gerak
        MELAWAN, bukan besarnya gerak."""
        alasan = perlu_konfirmasi(_s("BUY"), _s("SELL"), gerak_pct=+5.0)

        assert alasan

    def test_arah_yang_sama_bukan_pembalikan(self) -> None:
        assert perlu_konfirmasi(_s("BUY"), _s("BUY"), gerak_pct=0.0) == ()

    def test_berhenti_berpendapat_bukan_pembalikan(self) -> None:
        """Menuntut konfirmasi untuk BERHENTI akan membuat ARUNA bertahan pada
        pandangannya justru ketika buktinya menghilang - kebalikan dari yang
        dituju bagian 18.27."""
        assert perlu_konfirmasi(_s("BUY"), _s("WAIT"), gerak_pct=0.0) == ()
        assert perlu_konfirmasi(_s("WAIT"), _s("BUY"), gerak_pct=0.0) == ()

    def test_gerak_tak_terukur_MENAHAN(self) -> None:
        """Pembalikan yang tidak bisa dibuktikan terkonfirmasi tidak boleh
        lewat hanya karena pengukurannya gagal."""
        alasan = perlu_konfirmasi(_s("BUY"), _s("SELL"), gerak_pct=None)

        assert alasan
        assert "tidak bisa diperiksa" in alasan[0]

    def test_tanpa_keputusan_sebelumnya_bebas(self) -> None:
        """Sinyal pertama untuk sebuah aset tidak membalik apa pun."""
        assert perlu_konfirmasi(None, _s("SELL"), gerak_pct=None) == ()


class TestAmbangnyaDipinjam:
    def test_memakai_material_move_pct(self) -> None:
        """Pertanyaannya sama dengan yang `is_duplicate` ajukan: berapa gerak
        yang membuat ini keadaan yang berbeda. Ambang kedua akan membuat
        "setup yang sama" dan "pembalikan yang terkonfirmasi" memakai dua
        definisi gerak material."""
        import inspect

        from aruna.signals import stabilitas

        assert "MATERIAL_MOVE_PCT" in inspect.getsource(stabilitas)

    def test_tepat_di_ambang_lolos(self) -> None:
        assert perlu_konfirmasi(
            _s("SELL"), _s("BUY"), gerak_pct=MATERIAL_MOVE_PCT
        ) == ()


class TestCatatanPeralihan:
    """Bagian 18.28."""

    def test_membawa_sebelum_sesudah_dan_sebabnya(self) -> None:
        p = Peralihan(
            symbol="BTC/USDT", horizon="15m", sebelum="BUY", sesudah="SELL",
            pada=SAAT, gerak_pct=-1.2,
        )

        assert "BUY -> SELL" in p.ringkas()
        assert "-1.20%" in p.ringkas()
        assert p.terkonfirmasi

    def test_yang_beralasan_berarti_belum_terkonfirmasi(self) -> None:
        p = Peralihan(
            symbol="BTC/USDT", horizon="15m", sebelum="BUY", sesudah="SELL",
            pada=SAAT, gerak_pct=-0.1, alasan=("belum terkonfirmasi",),
        )

        assert not p.terkonfirmasi

    def test_gerak_tak_terukur_tetap_bisa_diringkas(self) -> None:
        p = Peralihan(
            symbol="X", horizon="1h", sebelum="BUY", sesudah="SELL", pada=SAAT
        )

        assert "tak terukur" in p.ringkas()


class TestPenjagaTerpasangDiJalurHidup:
    """Ditulis dan diekspor tidak sama dengan dipanggil.

    Enam kali di proyek ini sebuah aturan lulus seluruh testnya sementara
    produksi tidak pernah memanggilnya. Test di atas menguji ``perlu_konfirmasi``
    sebagai fungsi; test ini menguji bahwa ``SignalService`` benar-benar
    menanyakannya sebelum meloloskan sebuah pembalikan.
    """

    async def _alasan(self, terbuka: dict, kandidat: dict) -> str | None:
        from aruna.core.enums import Market
        from aruna.signals.service import SignalService

        class _Store:
            async def latest_loss(self, **kw: object) -> None:
                return None

            async def latest_open(self, **kw: object) -> dict:
                return terbuka

        svc = object.__new__(SignalService)
        svc._store = _Store()
        return await svc._repetition_reason(
            NS(symbol="BTC/USDT", id=1),
            Market.CRYPTO,
            NS(value="1h", duration=timedelta(hours=1)),
            NS(regime="SIDEWAYS", locked_at=SAAT, is_directional=True, **kandidat),
            None,
        )

    async def test_pembalikan_tanpa_gerak_ditahan_oleh_service(self) -> None:
        """Pembalikan lolos penjaga duplikat - arahnya memang berbeda - jadi
        tanpa panggilan ini ia lewat tanpa satu pun pemeriksaan."""
        alasan = await self._alasan(
            {"direction": "SELL", "reference_price": 100.0,
             "target_price": 90.0, "regime": "SIDEWAYS"},
            {"direction": NS(value="BUY"), "reference_price": 100.0,
             "target_price": 110.0},
        )

        assert alasan is not None
        assert "belum terkonfirmasi" in alasan

    async def test_pembalikan_yang_terbukti_lewat(self) -> None:
        """Harga yang naik 2% sesudah SELL membuktikan SELL salah."""
        alasan = await self._alasan(
            {"direction": "SELL", "reference_price": 100.0,
             "target_price": 90.0, "regime": "SIDEWAYS"},
            {"direction": NS(value="BUY"), "reference_price": 102.0,
             "target_price": 112.0},
        )

        assert alasan is None


class TestLaporanDuaAngka:
    """Bagian 18.52."""

    def test_terkonfirmasi_dihitung_terpisah(self) -> None:
        """**Satu angka "pembalikan: 4" tidak membedakan dua keadaan yang
        sangat berbeda.** Empat pembalikan yang seluruhnya terkonfirmasi adalah
        pasar yang memang berbalik empat kali; empat yang tak satu pun
        terkonfirmasi adalah ARUNA yang bergoyang.
        """
        riwayat = [
            Peralihan("A", "15m", "BUY", "SELL", SAAT),
            Peralihan("B", "15m", "BUY", "SELL", SAAT, alasan=("x",)),
            Peralihan("C", "15m", "SELL", "BUY", SAAT),
        ]

        assert hitung_pembalikan(riwayat) == (3, 2)

    def test_kosong_tidak_meledak(self) -> None:
        assert hitung_pembalikan(None) == (0, 0)
        assert hitung_pembalikan([]) == (0, 0)
