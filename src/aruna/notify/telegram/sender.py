"""Send-only Telegram client (FUTURES SPEC 48).

The bot cannot live in the planning loop: Telegram allows exactly one
``getUpdates`` consumer per token, so a second poller steals the token from
``aruna run`` and both end up broken. That is a settled question - the loop is
headless.

**But sending is not polling.** ``sendMessage`` is an ordinary POST with no
exclusivity at all, so a headless process can speak without ever listening.
This is that: one method, no Application, no poller, no conflict.

**The token lives in the URL**, which is how Telegram's API works and is exactly
the shape that leaked a credential into ``logs/aruna.log`` once already. So no
error path here ever carries the URL: failures are reported by status code and
a scrubbed body, and the URL is built inside the call and never stored on the
instance where a repr could find it (SPEC 43).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from aruna.core.logging import get_logger
from aruna.core.redaction import REDACTOR

log = get_logger("aruna.telegram.sender")

#: Telegram's hard limit on one message.
MAX_MESSAGE_CHARS = 4096

API_ROOT = "https://api.telegram.org"


class TelegramSender:
    """Posts a message. Never reads one."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        timeout_sec: float = 15.0,
        api_root: str = API_ROOT,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout_sec
        self._api_root = api_root.rstrip("/")
        #: Transport HTTP pengganti, untuk test. ``None`` = jaringan sungguhan.
        #:
        #: Seam ini ada supaya jalur balasan bisa diuji tanpa menyentuh
        #: Telegram: yang perlu dibuktikan adalah bidang apa yang dikirim dan
        #: apa yang dilakukan saat pesan yang dibalas sudah hilang, dan
        #: keduanya tidak bisa dilihat dari luar.
        self._transport: Any = None

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, text: str) -> bool:
        """Deliver one message. Returns whether it was delivered.

        Never raises. A notification that cannot be delivered must not end the
        analysis run that produced it - the plan is already stored, and the
        stored row is the record. Losing the message is a smaller failure than
        losing the loop.
        """
        return await self.send_id(text) is not None

    async def send_id(self, text: str, *, reply_to: int | None = None) -> int | None:
        """Deliver one message and report the id Telegram gave it.

        ``None`` means not delivered. An integer means delivered - and ``0``
        means delivered but the id could not be read back, which is a different
        thing entirely: the caller has still notified the operator and must not
        retry, it simply has nothing to reply to later.

        That distinction is the whole reason this method exists. A futures plan
        records its ``telegram_message_id`` so the RESULT hours later can arrive
        as a reply to the plan it settles, instead of as a loose message the
        reader has to match by hand against twenty symbols.

        ``reply_to`` is dropped and the send retried once if Telegram rejects
        it, because the plan message may have been deleted in the meantime -
        and a result that cannot reply must still be sent (SPEC 11.21 forbids
        hiding a LOSS).
        """
        if not self.configured:
            return None

        body = text if len(text) <= MAX_MESSAGE_CHARS else _truncate(text)
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": body,
            "disable_web_page_preview": True,
        }
        if reply_to is not None:
            payload["reply_to_message_id"] = reply_to

        response = await self._post(payload)
        if response is None:
            return None

        if response.status_code != 200:
            if reply_to is not None:
                log.info("telegram.reply_rejected", status=response.status_code)
                payload.pop("reply_to_message_id")
                return await self._send_payload(payload)
            log.warning(
                "telegram.send_rejected",
                status=response.status_code,
                detail=REDACTOR.scrub_text(response.text)[:200],
            )
            return None
        return _message_id(response)

    async def _send_payload(self, payload: dict[str, Any]) -> int | None:
        """One attempt, already assembled. No further retry."""
        response = await self._post(payload)
        if response is None:
            return None
        if response.status_code != 200:
            log.warning(
                "telegram.send_rejected",
                status=response.status_code,
                detail=REDACTOR.scrub_text(response.text)[:200],
            )
            return None
        return _message_id(response)

    async def _post(self, payload: dict[str, Any]) -> httpx.Response | None:
        """POST to ``sendMessage``. ``None`` on any transport failure.

        The URL is built here and never stored, so no repr of this object can
        leak the token (SPEC 43).
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                return await client.post(
                    f"{self._api_root}/bot{self._token}/sendMessage", json=payload
                )
        except httpx.HTTPError as exc:
            # `str(exc)` on an httpx error carries the request URL, and the URL
            # carries the token. Masked here first, then scrubbed: REDACTOR's
            # Telegram pattern requires a 30+ character secret, so it would
            # keep a short or oddly-shaped token - and whether a credential
            # reaches the log should not depend on how long it happens to be.
            log.warning(
                "telegram.send_failed",
                error=REDACTOR.scrub_text(_mask_url_token(str(exc)))[:200],
            )
            return None


#: ``/bot<token>/`` as it appears in every Telegram API URL.
_URL_TOKEN = re.compile(r"/bot[^/\s]+")


def _mask_url_token(text: str) -> str:
    """Remove the token from any Telegram URL in ``text``.

    Shape-independent, unlike a pattern that has to guess what a secret looks
    like: everything between ``/bot`` and the next ``/`` goes, whatever it is.
    """
    return _URL_TOKEN.sub("/bot***", text)


def _message_id(response: httpx.Response) -> int:
    """The id out of a 200, or ``0`` if it is not readable.

    Never raises and never returns ``None``: the message went out, and the
    caller's decision about whether to send at all was already made. A body we
    cannot parse changes what we can reply to later, not whether the operator
    was told.
    """
    try:
        value = response.json()["result"]["message_id"]
    except (ValueError, KeyError, TypeError):
        log.info("telegram.message_id_unreadable")
        return 0
    return int(value) if isinstance(value, int) else 0


def _truncate(text: str) -> str:
    """Cut to Telegram's limit, on a line boundary, and say that it was cut.

    Cutting mid-number is how a truncated report becomes a wrong one - the
    reader sees `63,0` and has no way to know the rest was removed.
    """
    marker = "\n\n[truncated - send /plans for the full record]"
    room = MAX_MESSAGE_CHARS - len(marker)
    clipped = text[:room]
    if "\n" in clipped:
        clipped = clipped[: clipped.rindex("\n")]
    return clipped + marker


def sender_from(settings: Any) -> TelegramSender:
    """Build a sender from the Telegram settings block."""
    return TelegramSender(
        token=settings.bot_token.get_secret_value(),
        chat_id=settings.chat_id,
    )


__all__ = ["API_ROOT", "MAX_MESSAGE_CHARS", "TelegramSender", "sender_from"]
