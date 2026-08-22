"""Setiap pengirim yang benar-benar dirangkai harus sepadan bentuknya.

Cacat yang membuat test ini ada, terukur di produksi 2026-08-21: baris pertama
di ``futures_plan_delivery`` tercatat ``telegram_message_id = NULL`` padahal
seluruh suite hijau.

Sebabnya bukan logika, melainkan **bentuk**. ``PlanNotifier`` memanggil
``send_id`` kalau pengirimnya punya, dan jatuh kembali ke ``send`` kalau tidak.
Loop futures merangkai ``TelegramSender`` - yang waktu itu hanya punya
``send``. Fallback-nya bekerja persis seperti dirancang, diam-diam, dan id
pesannya hilang setiap kali.

Test-test lain memakai pengirim tiruan yang **punya** ``send_id``, jadi tidak
satu pun bisa melihatnya. Yang tidak diuji adalah kelas sungguhannya. Itu yang
diuji di sini: bukan apa yang dilakukan pengirim, tapi apakah kelas yang
dirangkai app memenuhi apa yang dipanggil pemanggilnya.
"""

from __future__ import annotations

import inspect

import pytest

from aruna.app import _LateSender
from aruna.notify.telegram.bot import TelegramBot
from aruna.notify.telegram.sender import TelegramSender, sender_from


class _Rahasia:
    def __init__(self, nilai: str) -> None:
        self._nilai = nilai

    def get_secret_value(self) -> str:
        return self._nilai


class _SetelanTelegram:
    bot_token = _Rahasia("123456:" + "A" * 35)
    chat_id = "999"


#: Kelas yang benar-benar diserahkan sebagai ``sender`` di produksi.
#:
#: ``TelegramSender`` dipakai loop futures lewat ``sender_from`` (cli.py);
#: ``_LateSender`` dipakai ResultNotifier, SignalNotifier, dan sisanya (app.py);
#: ``TelegramBot`` adalah yang akhirnya dipanggil ``_LateSender``.
PENGIRIM = [TelegramSender, _LateSender, TelegramBot]


@pytest.mark.parametrize("kelas", PENGIRIM, ids=lambda k: k.__name__)
class TestBentukPengirim:
    def test_punya_send_id(self, kelas) -> None:
        """Tanpa ini, pemanggilnya diam-diam turun ke ``send`` dan id hilang."""
        assert hasattr(kelas, "send_id"), (
            f"{kelas.__name__} dirangkai sebagai sender tapi tidak punya "
            "send_id; PlanNotifier akan jatuh ke send() dan membuang id pesan"
        )

    def test_send_id_asinkron(self, kelas) -> None:
        assert inspect.iscoroutinefunction(kelas.send_id)

    def test_menerima_reply_to_sebagai_kata_kunci(self, kelas) -> None:
        """Pemanggilnya menulis ``send_id(teks, reply_to=...)``. Parameter
        posisional saja akan meledak di produksi, bukan di test."""
        p = inspect.signature(kelas.send_id).parameters.get("reply_to")

        assert p is not None
        assert p.kind is inspect.Parameter.KEYWORD_ONLY
        assert p.default is None, "reply_to harus opsional; jalur plan tidak membalas"

    def test_send_lama_tetap_ada(self, kelas) -> None:
        """Fallback di PlanNotifier dan _LateSender masih memanggilnya."""
        assert inspect.iscoroutinefunction(kelas.send)


class TestPabrikLoopFutures:
    def test_sender_from_menghasilkan_pengirim_ber_id(self) -> None:
        """``sender_from`` adalah satu-satunya jalan pengirim masuk ke loop
        futures. Apa pun yang keluar dari sini harus bisa melaporkan id."""
        s = sender_from(_SetelanTelegram())

        assert s.configured
        assert hasattr(s, "send_id")


class TestNolBukanNone:
    """Nol berarti terkirim tanpa id; ``None`` berarti tidak terkirim.

    Menyamakan keduanya membungkam seluruh hasil yang mengikutinya - dan
    §11.21 melarang menyembunyikan LOSS. Dicek di sini karena tiga kelas harus
    sepakat soal ini, dan ketidaksepakatannya tidak kelihatan dari mana pun.
    """

    @pytest.mark.asyncio
    async def test_late_sender_nol_untuk_bot_tanpa_send_id(self) -> None:
        class BotLama:
            async def send(self, text: str) -> bool:
                return True

        assert await _LateSender(lambda: BotLama()).send_id("halo") == 0

    @pytest.mark.asyncio
    async def test_late_sender_none_saat_botnya_belum_ada(self) -> None:
        assert await _LateSender(lambda: None).send_id("halo") is None

    @pytest.mark.asyncio
    async def test_late_sender_meneruskan_reply_to(self) -> None:
        dilihat: list[int | None] = []

        class Bot:
            async def send(self, text: str) -> bool:  # pragma: no cover
                return True

            async def send_id(self, text: str, *, reply_to: int | None = None) -> int:
                dilihat.append(reply_to)
                return 5

        assert await _LateSender(lambda: Bot()).send_id("halo", reply_to=42) == 5
        assert dilihat == [42]
