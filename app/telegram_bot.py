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
    "ما لقيت نتائج في قاعدة البيانات لهذا السؤال.\n"
    "جرّب تصيغ سؤالك بشكل مختلف أو اذكر رمز المادة."
)
_ERROR = "حدث خطأ، حاول مرة ثانية. 🔄"
_WELCOME = (
    "أهلاً! أنا روّمان 📚\n\n"
    "أبحث لك في مواد وملخصات وتجميعات SEU.\n"
    "أرسل سؤالك مباشرة — بالعربي أو بالإنجليزي."
)


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
        if len(content) > 280:
            content = content[:277] + "..."

        sim  = row.get("similarity", 0)
        meta = row.get("metadata") or {}
        course  = meta.get("course_code") or row.get("course_code") or ""
        srctype = meta.get("source_type") or row.get("source_type") or ""

        tag_parts = [p for p in [course, srctype] if p]
        tag = f" <i>({' · '.join(tag_parts)})</i>" if tag_parts else ""

        lines.append(f"<b>{i}.</b>{tag}\n{content}")

    normalized = data.get("normalized_query")
    header = f"<i>تم البحث عن: {normalized}</i>\n\n" if normalized else ""

    return header + "\n\n".join(lines)


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
