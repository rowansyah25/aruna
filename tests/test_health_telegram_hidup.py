"""Kesehatan Telegram harus mengikuti bot, bukan membeku saat monitor dirakit.

**Bug produksi, 2026-08-22.** ARUNA mencoba ulang bot yang gagal menyala -
`ArunaApplication._retry_telegram`, ditambahkan 2026-08-19 untuk penyumbatan ISP
Indonesia yang berulang - dan percobaan ulangnya **berhasil**::

    07:19:47  health  DOWN  "a bot token is configured but the bot did not start"
    07:19:49  telegram.reconnected - bot menyala sesudah gagal saat startup
    07:33     health  masih DOWN, empat belas menit kemudian, tanpa transisi baru

Sebabnya satu baris: ``active=bot_started`` dievaluasi **sekali**, saat monitor
dirakit. Bot boleh hidup lagi; pemeriksanya tidak pernah diberi tahu.

Akibatnya bukan angka yang meleset. Operator yang membaca kesehatan menyimpulkan
sinyal tidak terkirim padahal terkirim - dan pada sistem yang seluruh gunanya
mengirim sinyal, itu kebalikan dari yang harus dilakukan pemeriksa kesehatan.

Dua bagian yang tidak bicara: yang satu diperbaiki, yang lain tidak diberi tahu.
"""

from __future__ import annotations

import inspect

import pytest

from aruna.core.enums import HealthStatus
from aruna.health.checks import TelegramCheck


class _Bot:
    """Bot yang keadaannya bisa berubah, seperti aslinya."""

    def __init__(self, started: bool = False) -> None:
        self.started = started
        self.dipanggil = 0

    async def get_me(self) -> dict:
        self.dipanggil += 1
        return {"username": "ArunaAiBOT"}


def _check(bot: _Bot, *, configured: bool = True) -> TelegramCheck:
    return TelegramCheck(
        hidup=lambda: bot.started,
        probe=bot.get_me,
        timeout=1.0,
        configured=configured,
    )


@pytest.mark.asyncio
class TestMengikutiBot:
    async def test_bot_yang_menyambung_ulang_jadi_up(self) -> None:
        """Inti seluruh perbaikan. Pemeriksa yang sama, tanpa dirakit ulang."""
        bot = _Bot(started=False)
        check = _check(bot)

        assert (await check.check()).status is HealthStatus.DOWN

        bot.started = True  # `_retry_telegram` berhasil

        assert (await check.check()).status is HealthStatus.UP

    async def test_bot_yang_mati_lagi_jadi_down(self) -> None:
        """Penjaga harus bekerja dua arah. Yang hanya bisa naik akan melaporkan
        sehat atas bot yang sudah mati."""
        bot = _Bot(started=True)
        check = _check(bot)

        assert (await check.check()).status is HealthStatus.UP

        bot.started = False

        assert (await check.check()).status is HealthStatus.DOWN

    async def test_probe_tidak_dipanggil_saat_bot_mati(self) -> None:
        """`hidup` yang menggerbangi, bukan ada-tidaknya probe - dan gerbangnya
        harus benar-benar menahan, bukan sekadar mengubah pesannya."""
        bot = _Bot(started=False)

        await _check(bot).check()

        assert bot.dipanggil == 0


@pytest.mark.asyncio
class TestTigaKeadaanTetapTerpisah:
    """Yang **tidak** boleh rusak oleh perbaikan ini.

    Tidak dikonfigurasi -> pilihan (DISABLED). Dikonfigurasi tapi tidak pernah
    dicoba -> pilihan (DISABLED). Dikonfigurasi dan gagal -> cacat (DOWN).
    Menyatukan yang tengah dan yang terakhir membuat tiap `plan` dan
    `futures-loop` melaporkan dirinya rusak karena melakukan persis yang
    diminta.
    """

    async def test_tanpa_token_disabled_bukan_down(self) -> None:
        hasil = await _check(_Bot(started=False), configured=False).check()

        assert hasil.status is HealthStatus.DISABLED
        assert "headless" in hasil.message

    async def test_dikonfigurasi_tapi_gagal_down_bukan_disabled(self) -> None:
        hasil = await _check(_Bot(started=False), configured=True).check()

        assert hasil.status is HealthStatus.DOWN
        assert "did not start" in hasil.message

    async def test_probe_none_tetap_ditangani(self) -> None:
        """Perintah sekali-jalan tidak pernah membuat bot sama sekali."""
        check = TelegramCheck(hidup=lambda: False, probe=None, configured=False)

        assert (await check.check()).status is HealthStatus.DISABLED


@pytest.mark.asyncio
class TestKegagalanProbe:
    async def test_probe_yang_meledak_jadi_down_bukan_melempar(self) -> None:
        """Sebuah probe tidak boleh menjatuhkan sapuan kesehatan."""

        class _Rusak(_Bot):
            async def get_me(self) -> dict:
                raise RuntimeError("api.telegram.org tersumbat")

        bot = _Rusak(started=True)
        hasil = await _check(bot).check()

        assert hasil.status is HealthStatus.DOWN
        assert "getMe failed" in hasil.message


class TestBentuknyaMencegahBugnya:
    """Perbaikan yang hanya memperbaiki sekali akan ditulis ulang salah."""

    def test_hidup_wajib_callable_bukan_bool(self) -> None:
        """Inti pencegahannya: ``bool`` tidak bisa dioper lagi, jadi tidak ada
        yang bisa membekukan keadaan ini secara tidak sengaja."""
        tanda = inspect.signature(TelegramCheck.__init__)

        assert "active" not in tanda.parameters, (
            "parameter `active` yang menerima bool adalah bentuk lama; "
            "membekukan keadaan yang bergerak adalah bugnya sendiri"
        )
        assert "hidup" in tanda.parameters

    def test_app_mengoper_callable(self) -> None:
        """Penjaga AST: `app.py` harus mengoper sesuatu yang bisa dipanggil,
        bukan hasil pembacaan sekali. `hidup=bot_started` akan lolos test
        perilaku mana pun yang membangun `TelegramCheck` sendiri."""
        import ast

        from aruna import app

        pohon = ast.parse(inspect.getsource(app))
        for n in ast.walk(pohon):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "TelegramCheck"
            ):
                nilai = {kw.arg: kw.value for kw in n.keywords}
                assert "hidup" in nilai
                # Nama fungsi atau lambda - bukan Name yang menunjuk bool yang
                # sudah dihitung. Keduanya `ast.Name`, jadi yang dituntut di
                # sini namanya: sebuah pembaca, bukan sebuah hasil.
                assert isinstance(nilai["hidup"], ast.Name | ast.Lambda)
                if isinstance(nilai["hidup"], ast.Name):
                    assert nilai["hidup"].id.endswith("hidup"), nilai["hidup"].id
                return

        pytest.fail("TelegramCheck tidak dirakit di app.py")
