"""Id pesan yang dibuang, dan balasan yang karena itu tidak pernah terjadi.

Terukur di produksi 2026-08-21: baris pertama di ``futures_plan_delivery``
tercatat dengan ``telegram_message_id = NULL``. Pengirimannya tercatat - jadi
hasilnya nanti tetap akan dikirim - tapi tanpa id pesan ia tidak bisa
**membalas** rencananya.

Itu yang diminta operator: *"seharusnya sinyal dulu terus reply chat yang mana
hasil resultnya"*. Sebuah RESULT di antara dua puluh simbol tanpa balasan
menuntut pembacanya menggulir mencari rencana mana yang dimaksud.

Sebabnya: ``TelegramSender`` hanya punya ``send()`` yang memulangkan ``bool``.
Telegram mengembalikan ``message_id`` di respons ``sendMessage``, dan kelas ini
membuangnya.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aruna.notify.telegram.sender import TelegramSender

TOKEN = "123456:RAHASIA-SEKALI"


class _Transport(httpx.AsyncBaseTransport):
    """Menjawab seperti Telegram, tanpa jaringan."""

    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.terkirim: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        self.terkirim.append(json.loads(request.content))
        return httpx.Response(
            self.status,
            json=self.payload if self.payload is not None else {"ok": True},
            request=request,
        )


def _sender(transport: _Transport) -> TelegramSender:
    s = TelegramSender(token=TOKEN, chat_id="999")
    s._transport = transport  # seam untuk test; lihat TelegramSender
    return s


class TestIdPesanTerbaca:
    @pytest.mark.asyncio
    async def test_id_dikembalikan(self) -> None:
        t = _Transport({"ok": True, "result": {"message_id": 4242}})

        assert await _sender(t).send_id("halo") == 4242

    @pytest.mark.asyncio
    async def test_respons_tanpa_id_tetap_terkirim(self) -> None:
        """Terkirim tanpa id tercatat berbeda dari tidak terkirim, dan bedanya
        menentukan apakah hasilnya nanti dibungkam atau sekadar tidak bisa
        membalas. ``0`` berarti yang pertama; ``None`` yang kedua."""
        t = _Transport({"ok": True})

        assert await _sender(t).send_id("halo") == 0

    @pytest.mark.asyncio
    async def test_gagal_kirim_menghasilkan_none(self) -> None:
        t = _Transport({"ok": False, "description": "chat not found"}, status=400)

        assert await _sender(t).send_id("halo") is None

    @pytest.mark.asyncio
    async def test_tanpa_konfigurasi_tidak_menyentuh_jaringan(self) -> None:
        """Tanpa token: tidak ada permintaan sama sekali.

        Versi pertama test ini hanya memeriksa hasilnya ``None`` - dan tetap
        hijau saat penjaganya dicabut, karena permintaan ke
        ``.../bot/sendMessage`` tanpa token memang gagal dengan sendirinya.
        Hijau karena jaringannya mati, bukan karena kodenya benar. Yang
        dipastikan sekarang adalah tidak ada yang berangkat.
        """
        t = _Transport({"ok": True, "result": {"message_id": 1}})
        s = TelegramSender(token="", chat_id="")
        s._transport = t

        assert await s.send_id("halo") is None
        assert t.terkirim == []


class TestMembalas:
    @pytest.mark.asyncio
    async def test_balasan_diteruskan_ke_telegram(self) -> None:
        t = _Transport({"ok": True, "result": {"message_id": 7}})
        await _sender(t).send_id("hasil", reply_to=4242)

        assert t.terkirim[0]["reply_to_message_id"] == 4242

    @pytest.mark.asyncio
    async def test_tanpa_balasan_bidangnya_tidak_dikirim(self) -> None:
        """``reply_to_message_id: null`` ditolak sebagian versi API. Bidang yang
        tidak dipakai tidak dikirim sama sekali."""
        t = _Transport({"ok": True, "result": {"message_id": 7}})
        await _sender(t).send_id("halo")

        assert "reply_to_message_id" not in t.terkirim[0]

    @pytest.mark.asyncio
    async def test_pesan_yang_dibalas_hilang_tetap_terkirim(self) -> None:
        """Pesan rencananya bisa sudah dihapus operator. Telegram menolak
        balasan ke pesan yang tidak ada - dan hasilnya tetap harus sampai,
        karena §11.21 melarang menyembunyikan LOSS."""
        panggilan: list[dict] = []

        class _Rewel(_Transport):
            async def handle_async_request(self, request):
                import json

                isi = json.loads(request.content)
                panggilan.append(isi)
                if "reply_to_message_id" in isi:
                    return httpx.Response(
                        400,
                        json={"ok": False, "description": "message to be replied not found"},
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"message_id": 8}},
                    request=request,
                )

        hasil = await _sender(_Rewel()).send_id("hasil", reply_to=1)

        assert hasil == 8
        assert len(panggilan) == 2
        assert "reply_to_message_id" not in panggilan[1]


class TestTokenTidakBocor:
    @pytest.mark.asyncio
    async def test_galat_jaringan_tidak_membawa_token(self, monkeypatch) -> None:
        """``str(exc)`` pada galat httpx membawa URL, dan URL membawa token.
        Penjaga yang sudah ada di ``send`` harus berlaku di sini juga."""
        from aruna.notify.telegram import sender as modul

        keluar: list[str] = []

        class _Meledak(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                raise httpx.ConnectError(f"gagal ke {request.url}", request=request)

        monkeypatch.setattr(
            modul.log,
            "warning",
            lambda e, **k: keluar.append(f"{e} {k}"),
        )

        assert await _sender(_Meledak()).send_id("halo") is None
        assert TOKEN not in " ".join(keluar)

    def test_send_lama_tetap_ada(self) -> None:
        """``send`` dipakai jalur lain yang tidak butuh id. Menghapusnya akan
        memaksa setiap pemanggil peduli pada sesuatu yang tidak mereka pakai."""
        assert hasattr(TelegramSender, "send")
