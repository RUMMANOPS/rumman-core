#!/usr/bin/env python3
"""
telegram_bot.py — Student-facing Telegram bot for RUMMAN search.

Long-polls Telegram Bot API, classifies every message through a two-layer
router (fast pattern match → gpt-4o-mini LLM classifier), then routes to:
  - Direct response (greeting, capability, identity, meta-conversation)
  - Retrieval + synthesis (academic queries)

Platform identity:
  Each chat_id is hashed as SHA-256(RUMMAN_USER_SALT:telegram:chat_id).
  Raw chat IDs are never sent to or stored by the platform.

Environment:
  TELEGRAM_BOT_TOKEN   — bot token from @BotFather
  SEARCH_API_URL       — internal Railway URL of the search service
  OPENAI_API_KEY       — used by the assistant layer classifier
  RUMMAN_USER_SALT     — secret salt for user hash derivation
"""

import os
import re
import json
import asyncio
import hashlib
import logging
import time
from collections import deque
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
SEARCH_API_URL    = os.environ["SEARCH_API_URL"].rstrip("/")
RUMMAN_USER_SALT  = os.environ.get("RUMMAN_USER_SALT", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
TG_BASE           = f"https://api.telegram.org/bot{BOT_TOKEN}"

_ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SESSION_LOCAL_TTL = 25 * 60
_CACHE_MAX_ENTRIES = 50_000

_USER_CACHE:    dict[int, str]               = {}
_SESSION_CACHE: dict[int, tuple[str, float]] = {}
_ENROLLED:      dict[int, list[str]]         = {}
_PROMPTED_FOR_COURSES: set[int]              = set()
_HISTORY_MAX_TURNS = 6
_HISTORY_CACHE: dict[int, deque]             = {}

_COURSE_CODE_RE = re.compile(r'\b([A-Z]{2,6}\d{3,4})\b', re.IGNORECASE)

_ACADEMIC_KEYWORDS = {
    "اختبار", "امتحان", "ميدترم", "فاينل", "فينال", "كويز",
    "ملخص", "ملخصات", "تجميع", "تجميعات",
    "مادة", "كورس", "مقرر", "مساق",
    "واجب", "اسايمنت", "برجكت", "مشروع",
    "تسليم", "ديدلاين", "موعد",
    "شرح", "سلايد", "بوربوينت",
    "خطة", "تخصص", "برنامج",
    "midterm", "final", "exam", "quiz", "assignment", "project",
    "summary", "notes", "deadline",
}

_COURSE_NUDGE = (
    "\n\n💡 <i>لتحسين إجاباتي لموادك تحديداً، أرسل:\n"
    "<code>/mycourses IT362 CS251 MGT311</code>\n"
    "وسأفيلتر النتائج حسب موادك.</i>"
)

# ---------------------------------------------------------------------------
# Static responses
# ---------------------------------------------------------------------------

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
_CAPABILITY = (
    "أقدر أساعدك في:\n\n"
    "📝 <b>الاختبارات</b> — تجميعات، أسئلة متوقعة، مواضيع مهمة\n"
    "📚 <b>المواد</b> — ملخصات، شروحات، سلايدات\n"
    "🗓 <b>المواعيد</b> — تواريخ تسليم، مواعيد اختبارات\n"
    "📋 <b>الخطط الدراسية</b> — متطلبات، تخصصات، ساعات معتمدة\n"
    "📜 <b>اللوائح</b> — أنظمة الجامعة، إجراءات القبول والتسجيل\n\n"
    "ما أقدر عليه:\n"
    "• أجاوب أسئلة بالعربي والإنجليزي\n"
    "• أرجع دايماً لمصادر حقيقية (رسمية أو طلابية)\n"
    "• أذكر من وين جت المعلومة\n\n"
    "ما أقدر عليه حالياً:\n"
    "• استقبال ملفات مباشرة (قريباً)\n"
    "• الإجابة عن أسئلة خارج السياق الأكاديمي لـ SEU"
)
_USER_IDENTITY = (
    "أنا ما عندي معلومات شخصية عنك سوى رقم محادثتك.\n\n"
    "لو سجلت موادك، أقدر أخصص إجاباتي:\n"
    "<code>/mycourses IT362 CS251 MGT311</code>"
)
_NO_RESULTS = (
    "ما لقيت إجابة في المواد المتاحة.\n\n"
    "جرّب:\n"
    "• اذكر رمز المادة (مثل: IT362، MGT311)\n"
    "• اسأل عن موضوع محدد (ميدترم، فاينل، ملخص)\n"
    "• مثال: <i>وش يجي بالميدترم IT362</i>"
)
_ERROR = "حدث خطأ، حاول مرة ثانية."

# ---------------------------------------------------------------------------
# Quick-start inline keyboard (shown with /start)
# ---------------------------------------------------------------------------

_QUICK_START_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🗂 تجميعات اختبار",  "callback_data": "qs:exam"},
            {"text": "📝 ملخص مادة",       "callback_data": "qs:summary"},
        ],
        [
            {"text": "📅 مواعيد / تسليم",  "callback_data": "qs:deadline"},
            {"text": "📋 خطة دراسية",      "callback_data": "qs:plan"},
        ],
    ]
}

_QS_HINTS: dict[str, str] = {
    "exam": (
        "📝 لتجميعات الاختبارات، أرسل:\n\n"
        "<code>تجميعات [رمز المادة]</code>\n\n"
        "مثال: <code>تجميعات IT362</code>\n"
        "أو: <code>أسئلة فاينل MGT311</code>"
    ),
    "summary": (
        "📚 لملخص مادة، أرسل:\n\n"
        "<code>ملخص [رمز المادة]</code>\n\n"
        "مثال: <code>ملخص CS251</code>\n"
        "أو: <code>شرح مقرر IT362</code>"
    ),
    "deadline": (
        "📅 لمواعيد التسليم والاختبارات، أرسل:\n\n"
        "<code>مواعيد [رمز المادة]</code>\n\n"
        "مثال: <code>مواعيد اختبار MGT311</code>\n"
        "أو: <code>موعد تسليم CS341</code>"
    ),
    "plan": (
        "🎓 للخطة الدراسية، أرسل:\n\n"
        "<code>خطة دراسية [التخصص]</code>\n\n"
        "مثال: <code>خطة تقنية المعلومات</code>\n"
        "أو: <code>متطلبات BSCS</code>"
    ),
}

# ---------------------------------------------------------------------------
# Assistant layer: LLM-based message classifier
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """\
Classify this student message for an Arabic university assistant (RUMMAN).
Return EXACTLY ONE word from this list — nothing else:

greeting     — hi/hello/مرحبا/اهلين/السلام عليكم/صباح الخير or similar
identity_bot — who/what are you | من انت | وش انت | ايش انت
identity_user — who am I | من انا | ما اسمي | من أنا
capability   — can you do X | هل اقدر | هل عندك | وش تقدر | كيف تساعد | هل تستطيع
meta         — comment on previous answer | هل متأكد | راجع | صح ولا غلط | مقتنع | وش مصدرك
ack          — thanks/ok/تمام/شكرا/👍 or very short acknowledgement
off_topic    — clearly unrelated to university/courses (weather, news, politics, jokes)
academic     — anything about courses/exams/deadlines/study materials/university admin

When in doubt choose: academic"""

_CLASSIFY_CACHE: dict[str, str] = {}  # small in-memory cache for repeated queries


async def _classify_message(text: str) -> str:
    """
    Returns one of: greeting, identity_bot, identity_user, capability,
    meta, ack, off_topic, academic.
    Falls back to 'academic' on any error (safe default for retrieval).
    """
    if not _ai:
        return "academic"

    key = text[:200].lower().strip()
    if key in _CLASSIFY_CACHE:
        return _CLASSIFY_CACHE[key]

    try:
        resp = await asyncio.wait_for(
            _ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user",   "content": text[:300]},
                ],
                max_tokens=5,
                temperature=0,
            ),
            timeout=4.0,
        )
        result = resp.choices[0].message.content.strip().lower().split()[0]
        if result not in {
            "greeting", "identity_bot", "identity_user",
            "capability", "meta", "ack", "off_topic", "academic"
        }:
            result = "academic"
    except Exception as exc:
        log.debug("classify_failed | %s — defaulting to academic", exc)
        result = "academic"

    _CLASSIFY_CACHE[key] = result
    return result


def _has_academic_signal(text: str) -> bool:
    """True if text obviously contains academic content — skip LLM classifier."""
    if _COURSE_CODE_RE.search(text):
        return True
    tl = text.lower()
    return any(kw in tl for kw in _ACADEMIC_KEYWORDS)


def _clean(text: str) -> str:
    """Normalize for trigger matching — lowercase, strip punctuation + whitespace."""
    return text.lower().strip("؟?!.,،\t\n\r ")


# ---------------------------------------------------------------------------
# Session eviction
# ---------------------------------------------------------------------------

def _evict_expired_sessions() -> None:
    now = time.monotonic()
    expired = [k for k, (_, exp) in _SESSION_CACHE.items() if exp < now]
    for k in expired:
        del _SESSION_CACHE[k]
    if len(_USER_CACHE) > _CACHE_MAX_ENTRIES:
        evict_count = len(_USER_CACHE) // 5
        for k in list(_USER_CACHE.keys())[:evict_count]:
            del _USER_CACHE[k]


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
# Platform identity helpers
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
# Synthesize (retrieval + answer)
# ---------------------------------------------------------------------------

async def _synthesize(
    http: httpx.AsyncClient,
    query: str,
    user_id: str | None,
    session_id: str | None,
    conversation_history: list[dict] | None = None,
) -> dict | None:
    try:
        payload: dict = {"query": query, "limit": 5}
        if user_id:
            payload["user_id"] = user_id
        if session_id:
            payload["session_id"] = session_id
        if conversation_history:
            payload["conversation_history"] = conversation_history
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
        course_match = _COURSE_CODE_RE.search(query)
        if course_match:
            code = course_match.group(1)
            return (
                f"ما عندي محتوى لمادة <b>{code}</b> في قاعدة البيانات حالياً.\n\n"
                f"جرّب مادة ثانية أو اسألني سؤالاً عاماً."
            )
        return _NO_RESULTS

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
    cq_id   = callback_query["id"]
    data    = callback_query.get("data", "")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")

    if data.startswith("qs:") and chat_id:
        qs_type = data[3:]
        hint = _QS_HINTS.get(qs_type, "اسألني أي سؤال أكاديمي.")
        await _send(http, chat_id, hint)
        await _tg(http, "answerCallbackQuery", callback_query_id=cq_id)
        log.info("QUICK_START | chat=%d | type=%s", chat_id, qs_type)
        return

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

    await _tg(http, "answerCallbackQuery", callback_query_id=cq_id)


# ---------------------------------------------------------------------------
# /mycourses command
# ---------------------------------------------------------------------------

async def _handle_mycourses(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
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
# Planning handler — multi-course context + guidance requests
# ---------------------------------------------------------------------------

_EXAM_PROXIMITY_WORDS = {
    "finals", "final", "midterm", "midterms", "exam", "exams",
    "فاينل", "ميدترم", "اختبار", "الفاينل", "الاختبار",
    "نهائي", "نهاية الفصل", "نهاية الترم", "الترم",
}
_HELP_REQUEST_WORDS = {
    "what can you", "help me", "how can you", "what should i",
    "ساعدني", "وش تقدر", "كيف تساعد", "ابغى مساعدة",
    "شو تقدر", "وش تقدر تسوي", "تقدر تساعد", "ابغى مساعدتك",
}

_PLANNING_SYSTEM = """\
أنت رمّان — مساعد أكاديمي لطلاب SEU.

الطالب أعطاك سياق عن موادهم وطلب منك المساعدة في الاستعداد.
عندك تقرير بالمحتوى المتوفر لكل مادة في قاعدة البيانات.

قواعد الرد:
- رد بعربية خليجية طبيعية ودافئة — مثل زميل ذكي يعرف الجامعة
- اذكر بالضبط وش عندك لكل مادة بصدق ومباشر
- الأولوية: لو عندك "تجميعات اختبارات" ← هذا أهم شيء قبل الفاينل
- لو ما عندك تجميعات: اذكر وش عندك (خطة دراسية، محتوى المادة)
- لو ما عندك شيء لمادة: قول "غطاء محدود حالياً" بصدق
- اختم برسالة واحدة: سؤال عملي أو اقتراح للخطوة التالية
- لا تطول ← واضح ومباشر أفضل
"""


def _is_planning_query(text: str) -> bool:
    """Detect high-intent multi-course context-sharing + guidance requests."""
    codes = _COURSE_CODE_RE.findall(text)
    if len(codes) >= 3:
        return True

    tl = text.lower()
    has_exam = any(w in tl for w in _EXAM_PROXIMITY_WORDS)
    has_help = any(w in tl for w in _HELP_REQUEST_WORDS)
    # Multi-line list = student enumerating their courses
    non_empty_lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 8]
    has_list = len(non_empty_lines) >= 4

    if len(codes) >= 2 and has_exam and has_help:
        return True
    if has_exam and has_list and has_help:
        return True
    return False


async def _handle_planning(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
    """Inventory-first handler for multi-course guidance requests."""
    await _typing(http, chat_id)

    # ── Extract explicit course codes ──────────────────────────────────────
    codes = [m.upper() for m in _COURSE_CODE_RE.findall(text)]

    # ── Extract course names via LLM when codes are absent ────────────────
    names: list[str] = []
    if len(codes) < 2 and _ai:
        try:
            resp = await asyncio.wait_for(
                _ai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": (
                            'Extract university course names from this message. '
                            'Return JSON only: {"courses": ["Course Name 1", ...]}'
                        )},
                        {"role": "user", "content": text[:500]},
                    ],
                    max_tokens=150,
                    temperature=0,
                ),
                timeout=6.0,
            )
            raw = resp.choices[0].message.content.strip()
            data = json.loads(raw)
            names = data.get("courses", []) if isinstance(data, dict) else []
        except Exception as exc:
            log.debug("course_name_extraction_failed | %s", exc)

    # ── Call inventory endpoint ────────────────────────────────────────────
    inventory: dict = {}
    unresolved: list[str] = []
    try:
        r = await http.post(
            f"{SEARCH_API_URL}/v1/courses/inventory",
            json={"codes": codes, "names": names},
            timeout=12,
        )
        if r.status_code == 200:
            data = r.json()
            inventory  = data.get("inventory", {})
            unresolved = data.get("unresolved_names", [])
    except Exception as exc:
        log.warning("inventory_check_failed | %s", exc)

    # Persist discovered courses for session context
    found_codes = list(inventory.keys())
    if found_codes and chat_id not in _ENROLLED:
        _ENROLLED[chat_id] = found_codes

    # ── No courses resolved — ask for codes ───────────────────────────────
    if not inventory and not codes:
        await _send(http, chat_id,
            "ما قدرت أتعرف على رموز موادك تلقائياً.\n\n"
            "أرسلها بالأكواد مثل: <code>MGT325 FIN101 CS251</code>\n"
            "وأعطيك تقرير مفصل عن كل مادة."
        )
        return

    # ── Build inventory summary for LLM ───────────────────────────────────
    inv_lines: list[str] = []
    for code, info in inventory.items():
        name    = info.get("name_ar") or info.get("name_en") or code
        chunks  = info.get("chunk_count", 0)
        labels  = info.get("source_labels") or []
        if chunks == 0:
            inv_lines.append(f"{code} ({name}): غطاء محدود حالياً")
        else:
            src = " + ".join(labels) if labels else "محتوى عام"
            inv_lines.append(f"{code} ({name}): {src} — {chunks} مقطع")
    for name in unresolved[:3]:
        inv_lines.append(f'"{name}": ما عندي رمز لهذي المادة')

    if not _ai:
        lines = ["شفت موادك، هذا اللي عندي:\n"]
        lines += [f"• {l}" for l in inv_lines]
        lines.append("\nمن وين تبي تبدأ؟")
        await _send(http, chat_id, "\n".join(lines))
        return

    prompt = (
        f"رسالة الطالب:\n{text}\n\n"
        f"المحتوى المتوفر في قاعدة البيانات:\n" + "\n".join(inv_lines) +
        "\n\nاكتب رداً مفيداً ومحدداً بالعربية الخليجية."
    )
    try:
        resp = await asyncio.wait_for(
            _ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _PLANNING_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=400,
                temperature=0.4,
            ),
            timeout=10.0,
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as exc:
        log.warning("planning_gen_failed | %s", exc)
        lines = ["شفت موادك، هذا اللي عندي:\n"]
        lines += [f"• {l}" for l in inv_lines]
        lines.append("\nأرسل رمز المادة اللي تبي تبدأ فيها.")
        reply = "\n".join(lines)

    # Store in conversation history
    q = _HISTORY_CACHE.setdefault(chat_id, deque(maxlen=_HISTORY_MAX_TURNS))
    q.append({"role": "user",      "content": text[:200]})
    q.append({"role": "assistant", "content": reply[:400]})

    await _send(http, chat_id, reply)
    log.info("PLANNING | chat=%d | courses=%s | unresolved=%s",
             chat_id, found_codes, unresolved)


# ---------------------------------------------------------------------------
# Message handler — two-layer routing
# ---------------------------------------------------------------------------

async def _handle(http: httpx.AsyncClient, message: dict) -> None:
    chat_id = message["chat"]["id"]
    text    = (message.get("text") or "").strip()

    if not text:
        return

    # ── Hard-coded commands ─────────────────────────────────────────────────
    if text.startswith("/start"):
        await _send(http, chat_id, _WELCOME, reply_markup=_QUICK_START_KEYBOARD)
        log.info("START | chat=%d", chat_id)
        return

    if text.lower().startswith("/mycourses"):
        await _handle_mycourses(http, chat_id, text)
        return

    if text.startswith("/"):
        return

    # ── Layer 1: Planning queries — inventory-first, not retrieval-first ──────
    if _is_planning_query(text):
        await _handle_planning(http, chat_id, text)
        return

    # ── Layer 2: Fast academic signal (skip classifier, go straight to retrieval) ─
    if _has_academic_signal(text):
        await _handle_academic(http, chat_id, text)
        return

    # ── Layer 2: LLM classifier for everything else ─────────────────────────
    category = await _classify_message(text)
    log.info("CLASSIFIED | chat=%d | cat=%s | q=%.60s", chat_id, category, text)

    if category == "greeting":
        await _send(http, chat_id, _WELCOME)
        return

    if category == "identity_bot":
        await _send(http, chat_id, _IDENTITY)
        return

    if category == "identity_user":
        await _send(http, chat_id, _USER_IDENTITY)
        return

    if category == "capability":
        await _send(http, chat_id, _CAPABILITY)
        return

    if category == "ack":
        await _send(http, chat_id, "على الرحب! اسألني عن أي مادة أو اختبار.")
        return

    if category == "meta":
        # Respond to meta-questions about previous answers conversationally
        history = list(_HISTORY_CACHE.get(chat_id, []))
        if history:
            await _handle_meta(http, chat_id, text, history)
        else:
            await _send(http, chat_id,
                "ما عندي جواب سابق في هذه المحادثة أقيّمه.\n"
                "اسألني عن مادة أو موضوع وأجاوبك."
            )
        return

    if category == "off_topic":
        await _send(http, chat_id,
            "أنا متخصص في مساعدة طلاب SEU الأكاديميين.\n\n"
            "اسألني عن مادة، اختبار، أو أي شيء يخص دراستك."
        )
        return

    # Default: academic retrieval
    await _handle_academic(http, chat_id, text)


async def _handle_meta(
    http: httpx.AsyncClient,
    chat_id: int,
    text: str,
    history: list[dict],
) -> None:
    """Generate a conversational response to meta-questions (confidence, correction, etc.)."""
    if not _ai:
        await _send(http, chat_id,
            "إجاباتي مبنية على المصادر المتاحة في قاعدة البيانات فقط.\n"
            "إذا لاحظت خطأ، حاول تسألني بطريقة مختلفة أو اذكر رمز المادة."
        )
        return

    _META_SYSTEM = (
        "أنت رمّان، مساعد أكاديمي لطلاب SEU. "
        "الطالب يعلّق على جوابك السابق أو يسأل عن ثقتك فيه. "
        "رد بصدق وإيجاز بالعربية الخليجية. "
        "إذا سأل هل أنت متأكد: اشرح أن إجاباتك مبنية على مصادر محددة وقد تكون ناقصة. "
        "لا تتذرع، لا تعتذر بشكل مبالغ. جملتان أو ثلاث كافية."
    )
    messages = [{"role": "system", "content": _META_SYSTEM}]
    for turn in history[-4:]:
        messages.append(turn)
    messages.append({"role": "user", "content": text})

    try:
        resp = await asyncio.wait_for(
            _ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=150,
                temperature=0.3,
            ),
            timeout=8.0,
        )
        reply = resp.choices[0].message.content.strip()
        await _send(http, chat_id, reply)
    except Exception as exc:
        log.warning("meta_reply_failed | %s", exc)
        await _send(http, chat_id,
            "إجاباتي مبنية على المصادر المتاحة فقط — قد تكون ناقصة.\n"
            "إذا لاحظت خطأ، اسألني بطريقة مختلفة."
        )


async def _handle_academic(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
    """Full retrieval + synthesis pipeline for academic queries."""
    query = text
    if not _COURSE_CODE_RE.search(text) and chat_id in _ENROLLED and _ENROLLED[chat_id]:
        enrolled_str = " ".join(_ENROLLED[chat_id])
        query = f"{text} (موادي: {enrolled_str})"

    log.info("QUERY | chat=%d | q=%.60s", chat_id, query)
    await _typing(http, chat_id)

    user_id    = await _get_or_create_user(http, chat_id)
    session_id = await _get_or_create_session(http, chat_id, user_id) if user_id else None
    history    = list(_HISTORY_CACHE[chat_id]) if chat_id in _HISTORY_CACHE else None

    data = await _synthesize(http, query, user_id, session_id, history)
    if data is None:
        await _send(http, chat_id, _ERROR)
        return

    reply    = _format_synthesis(data, query=text)
    grounded = data.get("grounded", False)

    if grounded:
        q = _HISTORY_CACHE.setdefault(chat_id, deque(maxlen=_HISTORY_MAX_TURNS))
        q.append({"role": "user",      "content": text})
        q.append({"role": "assistant", "content": (data.get("answer") or "")[:400]})

    if grounded and chat_id not in _PROMPTED_FOR_COURSES and chat_id not in _ENROLLED:
        _PROMPTED_FOR_COURSES.add(chat_id)
        reply = reply + _COURSE_NUDGE

    markup = _feedback_keyboard(session_id) if grounded and session_id else None
    await _send(http, chat_id, reply, reply_markup=markup)

    log.info("REPLY | chat=%d | sources=%d | grounded=%s | synth_failed=%s | session=%s",
             chat_id, data.get("source_count", 0), grounded,
             data.get("synthesis_failed"), session_id)


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("BOT_START | polling Telegram | assistant_layer=%s", "enabled" if _ai else "disabled")
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
                break
            except Exception as exc:
                log.error("POLL_ERROR | %s", exc)
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
