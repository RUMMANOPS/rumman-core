#!/usr/bin/env python3
"""
telegram_bot.py — Student-facing Telegram bot for RUMMAN search.

Long-polls Telegram Bot API, forwards every text message to the
/search endpoint, and returns the top 3 grounded results.

Platform identity:
  Each chat_id is hashed as SHA-256(RUMMAN_USER_SALT:telegram:chat_id).
  Raw chat IDs are never sent to or stored by the platform.

Environment:
  TELEGRAM_BOT_TOKEN   — bot token from @BotFather
  SEARCH_API_URL       — internal Railway URL of the search service
  RUMMAN_USER_SALT     — secret salt for user hash derivation
"""

import os
import re
import asyncio
import hashlib
import logging
import time
import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
SEARCH_API_URL    = os.environ["SEARCH_API_URL"].rstrip("/")
RUMMAN_USER_SALT  = os.environ.get("RUMMAN_USER_SALT", "")
TG_BASE           = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSION_LOCAL_TTL = 25 * 60  # local cache TTL; server TTL is 30 min
_CACHE_MAX_ENTRIES = 50_000   # evict oldest 20% when exceeded

_USER_CACHE:    dict[int, str]              = {}  # chat_id → user_id (no TTL — user IDs are stable)
_SESSION_CACHE: dict[int, tuple[str, float]] = {}  # chat_id → (session_id, expires_monotonic)
_ENROLLED:      dict[int, list[str]]         = {}  # chat_id → enrolled course codes

_COURSE_CODE_RE = re.compile(r'\b([A-Z]{2,6}\d{3,4})\b', re.IGNORECASE)


def _evict_expired_sessions() -> None:
    now = time.monotonic()
    expired = [k for k, (_, exp) in _SESSION_CACHE.items() if exp < now]
    for k in expired:
        del _SESSION_CACHE[k]
    if len(_USER_CACHE) > _CACHE_MAX_ENTRIES:
        # Evict oldest 20% of user cache entries (dict insertion order preserved in Python 3.7+)
        evict_count = len(_USER_CACHE) // 5
        for k in list(_USER_CACHE.keys())[:evict_count]:
            del _USER_CACHE[k]

_NO_RESULTS = (
    "ما لقيت شي واضح في المواد المتاحة عن هذا السؤال.\n\n"
    "جرّب:\n"
    "• اذكر رمز المادة (مثل: IT362، MGT425)\n"
    "• اسأل عن موضوع محدد (ميدترم، فاينل، ملخص)\n"
    "• مثال: <i>وش يجي بالميدترم IT362</i>"
)
_ERROR   = "حدث خطأ، حاول مرة ثانية."
_WELCOME = (
    "أهلاً! أنا رمّان 📚\n\n"
    "مساعدك الأكاديمي لطلاب SEU — أجيبك من مصادر حقيقية:\n"
    "تجميعات اختبارات، ملخصات، وثائق رسمية من الجامعة، ومحتوى المقررات.\n\n"
    "أرسل سؤالك مباشرة — بالعربي أو بالإنجليزي.\n\n"
    "<b>أمثلة:</b>\n"
    "• وش يجي بالميدترم IT362\n"
    "• ابغى ملخص MGT425\n"
    "• تجميعات FIN101 الفاينل\n"
    "• وش متطلبات مادة CS241"
)
_IDENTITY = (
    "أنا رمّان 📚 — مساعدك الأكاديمي الذكي لطلاب SEU.\n\n"
    "أجيبك من مصادر حقيقية:\n"
    "• تجميعات اختبارات سابقة\n"
    "• ملخصات ومواد طلابية\n"
    "• وثائق رسمية من الجامعة\n"
    "• محتوى المقررات والخطط الدراسية\n\n"
    "اسألني عن أي مادة — أسئلة الاختبار، المحتوى، المتطلبات، أو أي شيء تحتاجه."
)

_IDENTITY_TRIGGERS = {
    "من انت", "من أنت", "مين انت", "مين أنت",
    "ايش انت", "ايش أنت", "وش انت", "وش أنت",
    "what are you", "who are you",
}
_GREETING_TRIGGERS = {
    "مرحبا", "مرحبً", "مرحباً", "هلو", "هاي", "اهلين", "أهلين",
    "اهلا", "أهلا", "اهلاً", "أهلاً", "كيف حالك", "كيف الحال",
    "السلام عليكم", "وعليكم السلام", "صباح الخير", "مساء الخير",
    "تصبح على خير", "hi", "hello", "hey", "hii", "helo",
    "good morning", "good evening",
}
_ACK_TRIGGERS = {
    "شكرا", "شكراً", "ثانكس", "ممتاز", "زين", "تمام", "thanks", "thank you",
    "ok", "okay", "👍", "من انا", "من أنا", "مين انا", "مين أنا",
    "ايش انا", "وش انا",
}
# Combined set for quick lookup (used in handle)
_META_TRIGGERS = _IDENTITY_TRIGGERS | _GREETING_TRIGGERS | _ACK_TRIGGERS


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

async def _tg(http: httpx.AsyncClient, method: str, **kwargs) -> dict:
    r = await http.post(f"{TG_BASE}/{method}", json=kwargs, timeout=10)
    return r.json()


async def _typing(http: httpx.AsyncClient, chat_id: int) -> None:
    await _tg(http, "sendChatAction", chat_id=chat_id, action="typing")


async def _send(
    http: httpx.AsyncClient,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    if len(text) > 4096:
        text = text[:4093] + "..."
    kwargs: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    await _tg(http, "sendMessage", **kwargs)


# ---------------------------------------------------------------------------
# Platform identity helpers (fire-and-forget safe — return None on failure)
# ---------------------------------------------------------------------------

def _hash_user(chat_id: int) -> str:
    return hashlib.sha256(
        f"{RUMMAN_USER_SALT}:telegram:{chat_id}".encode()
    ).hexdigest()


async def _get_or_create_user(http: httpx.AsyncClient, chat_id: int) -> str | None:
    if chat_id in _USER_CACHE:
        return _USER_CACHE[chat_id]
    try:
        r = await http.post(
            f"{SEARCH_API_URL}/v1/users/identify",
            json={
                "platform":           "telegram",
                "platform_user_hash": _hash_user(chat_id),
                "tenant_slug":        "seu",
            },
            timeout=5,
        )
        if r.status_code == 200:
            user_id = r.json()["user_id"]
            _USER_CACHE[chat_id] = user_id
            return user_id
    except Exception as exc:
        log.warning("user_identify_failed | chat=%d | %s", chat_id, exc)
    return None


async def _get_or_create_session(
    http: httpx.AsyncClient,
    chat_id: int,
    user_id: str,
) -> str | None:
    cached = _SESSION_CACHE.get(chat_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    try:
        r = await http.post(
            f"{SEARCH_API_URL}/v1/sessions",
            json={
                "user_id":     user_id,
                "platform":    "telegram",
                "tenant_slug": "seu",
            },
            timeout=5,
        )
        if r.status_code == 200:
            session_id = r.json()["session_id"]
            _SESSION_CACHE[chat_id] = (session_id, time.monotonic() + SESSION_LOCAL_TTL)
            return session_id
    except Exception as exc:
        log.warning("session_create_failed | chat=%d | %s", chat_id, exc)
    return None


# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------

async def _synthesize(
    http: httpx.AsyncClient,
    query: str,
    user_id: str | None,
    session_id: str | None,
) -> dict | None:
    try:
        payload: dict = {"query": query, "limit": 5}
        if user_id:
            payload["user_id"] = user_id
        if session_id:
            payload["session_id"] = session_id
        r = await http.post(
            f"{SEARCH_API_URL}/synthesize",
            json=payload,
            timeout=35,
        )
        if r.status_code >= 400:
            log.warning("SYNTHESIZE_ERROR | status=%d | body=%s", r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as exc:
        log.warning("SYNTHESIZE_EXCEPTION | %s", exc)
        return None


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _format_synthesis(data: dict, query: str = "") -> str:
    if not data.get("grounded"):
        # Try to give a more specific message if we can detect a course code
        course_match = _COURSE_CODE_RE.search(query)
        if course_match:
            code = course_match.group(1)
            return (
                f"ما عندي محتوى لمادة <b>{code}</b> في قاعدة البيانات حالياً.\n\n"
                f"جرّب مادة ثانية أو اسألني سؤالاً عاماً."
            )
        return _NO_RESULTS

    # Synthesis succeeded
    answer = (data.get("answer") or "").strip()
    if answer and not data.get("synthesis_failed"):
        sources = data.get("sources") or []
        courses = sorted({s["course_code"] for s in sources if s.get("course_code")})
        footer  = f"\n\n<i>📚 {', '.join(courses)}</i>" if courses else ""
        return answer + footer

    # Synthesis timed out — fall back to chunk display
    chunks = data.get("fallback_chunks") or []
    if not chunks:
        return _NO_RESULTS

    lines: list[str] = []
    for i, row in enumerate(chunks[:3], 1):
        content = (row.get("content") or "").strip()
        if len(content) > 300:
            content = content[:297] + "..."
        course = row.get("course_code") or ""
        tag    = f" <i>({course})</i>" if course else ""
        lines.append(f"<b>{i}.{tag}</b>\n{content}")
    return "\n\n".join(lines)


def _feedback_keyboard(session_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "👍 مفيد",      "callback_data": f"fb:1:{session_id}"},
            {"text": "👎 مش مفيد",  "callback_data": f"fb:0:{session_id}"},
        ]]
    }


# ---------------------------------------------------------------------------
# Feedback callback handler
# ---------------------------------------------------------------------------

async def _handle_callback(http: httpx.AsyncClient, callback_query: dict) -> None:
    cq_id = callback_query["id"]
    data  = callback_query.get("data", "")

    if data.startswith("fb:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            helpful    = parts[1] == "1"
            session_id = parts[2]
            try:
                await http.post(
                    f"{SEARCH_API_URL}/v1/sessions/{session_id}/feedback",
                    json={"helpful": helpful},
                    timeout=5,
                )
                log.info("FEEDBACK | session=%s | helpful=%s", session_id, helpful)
            except Exception as exc:
                log.warning("feedback_post_failed | %s", exc)

    # Always answer to stop the Telegram loading spinner
    await _tg(http, "answerCallbackQuery", callback_query_id=cq_id)


# ---------------------------------------------------------------------------
# /mycourses command
# ---------------------------------------------------------------------------

async def _handle_mycourses(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
    """Save or display enrolled course codes."""
    # Parse course codes from the command arguments
    parts = text.split(None, 1)
    args  = parts[1].strip() if len(parts) > 1 else ""
    codes = [m.upper() for m in _COURSE_CODE_RE.findall(args)]

    if codes:
        _ENROLLED[chat_id] = codes
        codes_str = "،  ".join(codes)
        await _send(http, chat_id,
            f"✅ تم حفظ موادك:\n<b>{codes_str}</b>\n\n"
            "سأفيلتر إجاباتي حسب موادك من الآن.\n"
            "لتغيير الموادك، أرسل الأمر مرة ثانية مع الأكواد الجديدة."
        )
        log.info("MYCOURSES_SET | chat=%d | courses=%s", chat_id, codes)
    else:
        enrolled = _ENROLLED.get(chat_id, [])
        if enrolled:
            courses_str = "،  ".join(enrolled)
            await _send(http, chat_id,
                f"موادك المسجلة:\n<b>{courses_str}</b>\n\n"
                "لتغييرها: /mycourses IT362 CS251 MGT311"
            )
        else:
            await _send(http, chat_id,
                "ما عندك مواد مسجلة حالياً.\n\n"
                "أرسل: <code>/mycourses IT362 CS251</code> لتسجيل موادك\n"
                "وسأخصص إجاباتي لموادك فقط."
            )


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

    if text.lower().startswith("/mycourses"):
        await _handle_mycourses(http, chat_id, text)
        return

    if text.startswith("/"):
        return

    normalized = text.lower().strip("؟?!. \t")
    if normalized in _IDENTITY_TRIGGERS:
        await _send(http, chat_id, _IDENTITY)
        return
    if normalized in _GREETING_TRIGGERS:
        await _send(http, chat_id, _WELCOME)
        return
    if normalized in _ACK_TRIGGERS:
        await _send(http, chat_id, "على الرحب! اسألني عن أي مادة أو اختبار.")
        return

    # If query has no course code but student has enrolled courses, inject them as context
    query = text
    if not _COURSE_CODE_RE.search(text) and chat_id in _ENROLLED and _ENROLLED[chat_id]:
        enrolled_str = " ".join(_ENROLLED[chat_id])
        query = f"{text} (موادي: {enrolled_str})"

    log.info("QUERY | chat=%d | q=%.60s", chat_id, query)
    await _typing(http, chat_id)

    # Resolve identity (non-blocking on failure)
    user_id    = await _get_or_create_user(http, chat_id)
    session_id = await _get_or_create_session(http, chat_id, user_id) if user_id else None

    data = await _synthesize(http, query, user_id, session_id)
    if data is None:
        await _send(http, chat_id, _ERROR)
        return

    reply    = _format_synthesis(data, query=text)
    grounded = data.get("grounded", False)

    # Attach feedback buttons only when results were returned
    markup = _feedback_keyboard(session_id) if grounded and session_id else None
    await _send(http, chat_id, reply, reply_markup=markup)

    log.info("REPLY | chat=%d | sources=%d | grounded=%s | synth_failed=%s | session=%s",
             chat_id, data.get("source_count", 0), grounded,
             data.get("synthesis_failed"), session_id)


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("BOT_START | polling Telegram")
    offset: int | None = None
    _tasks: set[asyncio.Task] = set()

    async with httpx.AsyncClient() as http:
        while True:
            try:
                params: dict = {
                    "timeout":         30,
                    "allowed_updates": ["message", "callback_query"],
                }
                if offset is not None:
                    params["offset"] = offset

                r = await http.get(
                    f"{TG_BASE}/getUpdates",
                    params=params,
                    timeout=40,
                )
                payload = r.json()

                updates = payload.get("result") or []
                if updates:
                    _evict_expired_sessions()
                for update in updates:
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    cb  = update.get("callback_query")
                    if msg:
                        t = asyncio.create_task(_handle(http, msg))
                        _tasks.add(t)
                        t.add_done_callback(_tasks.discard)
                    elif cb:
                        t = asyncio.create_task(_handle_callback(http, cb))
                        _tasks.add(t)
                        t.add_done_callback(_tasks.discard)

            except asyncio.CancelledError:
                log.info("BOT_STOP")
                break
            except Exception as exc:
                log.warning("POLL_ERROR | %s", exc)
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
