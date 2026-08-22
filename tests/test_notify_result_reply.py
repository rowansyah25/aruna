"""Hasil hanya untuk signal yang benar-benar sampai, dan membalas pesannya.

Dilaporkan operator: *"banyak yang ga ada sinyal tiba-tiba kirim result kan
aneh... seharusnya sinyal dulu terus reply chat yang mana hasil resultnya"*.

Akarnya: dua pengertian yang bergeser. ``published`` menjawab "layak
diterbitkan" dan ditulis saat prediksi dikunci; sesudahnya ada gerbang kedua
yang menolak signal tanpa entry, stop, target, atau timeframe. Terukur: 80
signal ditahan gerbang itu dengan barisnya tetap ``published = TRUE``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.notify.result import ResultNotifier, SignalNotifier
from aruna.notify.verdict import VoteSplit

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class Pengirim:
    """Pengirim yang melaporkan id pesannya, seperti bot sungguhan."""

    def __init__(self) -> None:
        self.terkirim: list[tuple[str, int | None]] = []
        self.berikutnya = 100

    async def send(self, text: str) -> bool:
        self.terkirim.append((text, None))
        return True

    async def send_id(self, text: str, *, reply_to: int | None = None) -> int | None:
        self.terkirim.append((text, reply_to))
        self.berikutnya += 1
        return self.berikutnya


class PengirimLama:
    """Pengirim tanpa ``send_id`` - perakitan lama, atau test lain."""

    def __init__(self) -> None:
        self.terkirim: list[str] = []

    async def send(self, text: str) -> bool:
        self.terkirim.append(text)
        return True


class Penyimpan:
    def __init__(self, jejak: dict[str, int | None] | None = None) -> None:
        self.jejak = dict(jejak or {})
        self.dicatat: list[tuple[str, int | None]] = []

    async def mark_pushed(self, signal_id, *, message_id, at) -> None:
        self.dicatat.append((str(signal_id), message_id))
        self.jejak[str(signal_id)] = message_id

    async def pushed_message_ids(self, signal_ids):
        return {k: v for k, v in self.jejak.items() if k in set(signal_ids)}


def signal(sid: str = "s1", **kw) -> dict:
    dasar = {
        "signal_id": sid,
        "symbol": "BTC/USDT",
        "decision": "BUY",
        "split": VoteSplit((), ()),
        "confidence": 0.8,
        "entry": 64120,
        "stop": 63780,
        "target": 64950,
        "timeframe": "15m",
    }
    return dasar | kw


def hasil(sid: str = "s1", **kw) -> dict:
    dasar = {
        "signal_id": sid,
        "symbol": "BTC/USDT",
        "decision": "BUY",
        "outcome_class": "TARGET_HIT",
    }
    return dasar | kw


@pytest.fixture(autouse=True)
def _spot_push_dinyalakan(monkeypatch):
    """Nyalakan pengiriman spot untuk berkas ini.

    Operator mematikannya pada 2026-08-20 - lihat
    :data:`aruna.notify.result.SPOT_PUSH_AKTIF`. Jalur kirimnya utuh dan
    teruji; yang berubah hanya sakelarnya. Berkas ini menguji jalur itu -
    termasuk urutan "sinyal dulu, hasilnya membalas" yang diminta operator -
    jadi ia menyalakannya sendiri.
    """
    from aruna.notify import result as modul

    monkeypatch.setattr(modul, "SPOT_PUSH_AKTIF", True)


class TestJejakDitulisOlehYangMengirim:
    @pytest.mark.asyncio
    async def test_signal_terkirim_dicatat_dengan_id_pesannya(self) -> None:
        toko = Penyimpan()
        n = SignalNotifier(sender=Pengirim(), store=toko)

        assert await n.announce([signal()], now=NOW) == 1
        assert toko.dicatat == [("s1", 101)]

    @pytest.mark.asyncio
    async def test_signal_yang_ditahan_tidak_dicatat(self) -> None:
        """Inti bugnya: yang ditahan gerbang "bisa dieksekusi" tidak pernah
        sampai, jadi tidak boleh meninggalkan jejak yang mengaku sampai."""
        toko = Penyimpan()
        n = SignalNotifier(sender=Pengirim(), store=toko)

        assert await n.announce([signal(stop=None)], now=NOW) == 0
        assert toko.dicatat == []

    @pytest.mark.asyncio
    async def test_pengirim_tanpa_id_tetap_tercatat_terkirim(self) -> None:
        """Nol berarti "terkirim, id tidak diketahui" - bukan "gagal"."""
        toko = Penyimpan()
        n = SignalNotifier(sender=PengirimLama(), store=toko)

        assert await n.announce([signal()], now=NOW) == 1
        assert toko.dicatat == [("s1", None)]

    @pytest.mark.asyncio
    async def test_penyimpanan_rusak_tidak_membatalkan_kiriman(self) -> None:
        """Pesannya sudah sampai ke operator; tulisan yang gagal adalah
        kehilangan yang jauh lebih kecil."""

        class Rusak(Penyimpan):
            async def mark_pushed(self, *a, **k):
                raise RuntimeError("basis data mati")

        n = SignalNotifier(sender=Pengirim(), store=Rusak())

        assert await n.announce([signal()], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_tanpa_penyimpanan_tetap_mengirim(self) -> None:
        n = SignalNotifier(sender=Pengirim(), store=None)

        assert await n.announce([signal()], now=NOW) == 1


class TestHasilTanpaSignalTidakDikirim:
    @pytest.mark.asyncio
    async def test_hasil_dari_signal_yang_tidak_pernah_sampai_diredam(self) -> None:
        """*"masak tiba-tiba result aja tanpa sinyal kan aneh"*."""
        kirim = Pengirim()
        n = ResultNotifier(sender=kirim, store=Penyimpan({}), warmup=False)

        assert await n.announce([hasil()], now=NOW) == 0
        assert kirim.terkirim == []

    @pytest.mark.asyncio
    async def test_hasil_dari_signal_yang_sampai_tetap_dikirim(self) -> None:
        kirim = Pengirim()
        n = ResultNotifier(
            sender=kirim, store=Penyimpan({"s1": 101}), warmup=False
        )

        assert await n.announce([hasil()], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_hanya_yang_sampai_yang_lolos(self) -> None:
        kirim = Pengirim()
        n = ResultNotifier(
            sender=kirim, store=Penyimpan({"s2": 202}), warmup=False
        )

        assert await n.announce([hasil("s1"), hasil("s2")], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_pencarian_gagal_mengirim_semuanya(self) -> None:
        """Satu bug pencarian tidak boleh membungkam kabar bahwa ARUNA salah
        (PASAL 11.21)."""

        class Rusak(Penyimpan):
            async def pushed_message_ids(self, ids):
                raise RuntimeError("basis data mati")

        n = ResultNotifier(sender=Pengirim(), store=Rusak(), warmup=False)

        assert await n.announce([hasil()], now=NOW) == 1

    @pytest.mark.asyncio
    async def test_tanpa_penyimpanan_mengirim_semuanya(self) -> None:
        """Arah kegagalan yang benar: perakitan yang belum lengkap tidak
        membungkam apa pun."""
        n = ResultNotifier(sender=Pengirim(), store=None, warmup=False)

        assert await n.announce([hasil()], now=NOW) == 1


class TestHasilMembalasSignalnya:
    @pytest.mark.asyncio
    async def test_hasil_membalas_pesan_signalnya(self) -> None:
        """*"reply chat yang mana hasil resultnya"*."""
        kirim = Pengirim()
        n = ResultNotifier(
            sender=kirim, store=Penyimpan({"s1": 101}), warmup=False
        )

        await n.announce([hasil()], now=NOW)

        assert kirim.terkirim[-1][1] == 101

    @pytest.mark.asyncio
    async def test_tanpa_id_tetap_dikirim_tanpa_balasan(self) -> None:
        """Terkirim tanpa id tercatat: hasilnya tetap sampai, hanya tidak bisa
        membalas. Membungkamnya akan menukar satu ketidaknyamanan dengan satu
        kabar yang hilang."""
        kirim = Pengirim()
        n = ResultNotifier(
            sender=kirim, store=Penyimpan({"s1": None}), warmup=False
        )

        assert await n.announce([hasil()], now=NOW) == 1
        assert kirim.terkirim[-1][1] is None

    @pytest.mark.asyncio
    async def test_pengirim_lama_tetap_bekerja(self) -> None:
        kirim = PengirimLama()
        n = ResultNotifier(
            sender=kirim, store=Penyimpan({"s1": 101}), warmup=False
        )

        assert await n.announce([hasil()], now=NOW) == 1
        assert len(kirim.terkirim) == 1


class TestUjungKeUjung:
    @pytest.mark.asyncio
    async def test_kirim_signal_lalu_hasilnya_membalas(self) -> None:
        """Urutan yang diminta operator: sinyal dulu, hasilnya membalasnya."""
        toko = Penyimpan()
        kirim = Pengirim()

        await SignalNotifier(sender=kirim, store=toko).announce(
            [signal()], now=NOW
        )
        await ResultNotifier(sender=kirim, store=toko, warmup=False).announce(
            [hasil()], now=NOW
        )

        assert len(kirim.terkirim) == 2
        assert kirim.terkirim[0][1] is None          # signalnya tidak membalas
        assert kirim.terkirim[1][1] == 101           # hasilnya membalas signal

    @pytest.mark.asyncio
    async def test_signal_ditahan_maka_hasilnya_juga(self) -> None:
        toko = Penyimpan()
        kirim = Pengirim()

        await SignalNotifier(sender=kirim, store=toko).announce(
            [signal(stop=None)], now=NOW
        )
        await ResultNotifier(sender=kirim, store=toko, warmup=False).announce(
            [hasil()], now=NOW
        )

        assert kirim.terkirim == []
