#!/usr/bin/env python3
"""
telegram_bot.py — Student-facing Telegram bot for RUMMAN search.

Long-polls Telegram Bot API, forwards every text message to the
/search endpoint, and returns the top 3 grounded results.

Environment:
  TELEGRAM_BOT_TOKEN   — bot token from @BotFather
  SEARCH_API_URL       — internal Railway URL of the search service
                         e.g. https://search.railway.internal:8000
                         or   https://<public-domain>
"""

import os
import asyncio
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
SEARCH_API_URL = os.environ["SEARCH_API_URL"].rstrip("/")
TG_BASE        = f"https://api.telegram.org/bot{BOT_TOKEN}"

_NO_RESULTS = (
    "ما لقيت شي في قاعدة البيانات عن هذا السؤال.\n\n"
    "جرّب:\n"
    "• اذكر رمز المادة (مثل: IT362، MGT425)\n"
    "• اسأل عن موضوع محدد (ميدترم، فاينل، ملخص)\n"
    "• مثال: <i>وش يجي بالميدترم IT362</i>"
)
_ERROR   = "حدث خطأ، حاول مرة ثانية."
_WELCOME = (
    "أهلاً! أنا روّمان 📚\n\n"
    "أبحث لك في مواد وملخصات وتجميعات SEU.\n"
    "أرسل سؤالك مباشرة — بالعربي أو بالإنجليزي.\n\n"
    "<b>أمثلة:</b>\n"
    "• وش يجي بالميدترم IT362\n"
    "• ابغى ملخص MGT425\n"
    "• تجميعات FIN101 الفاينل"
)
_IDENTITY = (
    "أنا روّمان 📚 — مساعد طلاب SEU.\n\n"
    "أبحث في مواد الجامعة: تجميعات، ملخصات، أسئلة اختبارات.\n"
    "اسألني عن أي مادة وأجيبك من قاعدة البيانات مباشرة."
)

_META_TRIGGERS = {
    "من انت", "من أنت", "مين انت", "مين أنت",
    "ايش انت", "ايش أنت", "وش انت", "وش أنت",
    "what are you", "who are you",
    "مرحبا", "مرحبً", "هلو", "هاي", "hi", "hello", "السلام عليكم",
}


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

async def _tg(http: httpx.AsyncClient, method: str, **kwargs) -> dict:
    r = await http.post(f"{TG_BASE}/{method}", json=kwargs, timeout=10)
    return r.json()


async def _typing(http: httpx.AsyncClient, chat_id: int) -> None:
    await _tg(http, "sendChatAction", chat_id=chat_id, action="typing")


async def _send(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
    if len(text) > 4096:
        text = text[:4093] + "..."
    await _tg(http, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def _search(http: httpx.AsyncClient, query: str) -> dict | None:
    try:
        r = await http.post(
            f"{SEARCH_API_URL}/search",
            json={"query": query, "limit": 5},
            timeout=20,
        )
        if r.status_code >= 400:
            log.warning("SEARCH_ERROR | status=%d | body=%s", r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as exc:
        log.warning("SEARCH_EXCEPTION | %s", exc)
        return None


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _format_results(data: dict) -> str:
    results = data.get("results") or []
    if not results:
        clarification = (data.get("debug") or {}).get("clarification")
        if clarification:
            return f"سؤالك غير واضح لي — {clarification}"
        return _NO_RESULTS

    lines: list[str] = []
    for i, row in enumerate(results[:3], 1):
        content = (row.get("content") or "").strip()
        if len(content) > 300:
            content = content[:297] + "..."

        meta    = row.get("metadata") or {}
        course  = meta.get("course_code") or row.get("course_code") or ""
        tag     = f" <i>({course})</i>" if course else ""

        lines.append(f"<b>{i}.{tag}</b>\n{content}")

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def _handle(http: httpx.AsyncClient, message: dict) -> None:
    chat_id = message["chat"]["id"]
    text    = (message.get("text") or "").strip()

    if not text:
        return

    if text.startswith("/start"):
        await _send(http, chat_id, _WELCOME)
        log.info("START | chat=%d", chat_id)
        return

    if text.startswith("/"):
        return

    if text.lower().strip("؟?!.") in _META_TRIGGERS:
        await _send(http, chat_id, _IDENTITY)
        return

    log.info("QUERY | chat=%d | q=%.60s", chat_id, text)
    await _typing(http, chat_id)

    data = await _search(http, text)
    if data is None:
        await _send(http, chat_id, _ERROR)
        return

    reply = _format_results(data)
    await _send(http, chat_id, reply)
    log.info("REPLY | chat=%d | count=%d | grounded=%s",
             chat_id, data.get("count", 0), data.get("grounded"))


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("BOT_START | polling Telegram")
    offset: int | None = None

    async with httpx.AsyncClient() as http:
        while True:
            try:
                params: dict = {"timeout": 30, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset

                r = await http.get(
                    f"{TG_BASE}/getUpdates",
                    params=params,
                    timeout=40,
                )
                payload = r.json()

                for update in payload.get("result") or []:
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if msg:
                        await _handle(http, msg)

            except asyncio.CancelledError:
                log.info("BOT_STOP")
                break
            except Exception as exc:
                log.warning("POLL_ERROR | %s", exc)
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
