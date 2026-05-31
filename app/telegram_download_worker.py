#!/usr/bin/env python3
"""
telegram_download_worker.py — Unified Telegram media download worker.

Handles TWO job types in a single process with ONE Telegram session:
  - audio_transcribe: download voice/audio → OpenAI Whisper → media_files
  - telegram_media:   download PDF/image → PyMuPDF + GPT-4o OCR → source_documents → embed_chunk

Single process avoids AuthKeyDuplicatedError that occurs when audio_worker and
media_worker both hold the same Telegram session string from separate containers.

Privacy contract (telegram_media jobs):
  - Raw files never touch Supabase Storage.
  - source_documents carries no sender identity.
"""

import os
import re
import base64
import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, PeerChat
from telethon.errors import AuthKeyDuplicatedError

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

MAX_RETRIES    = 5
SLEEP_SECONDS  = 3
PARALLEL_JOBS  = 10   # concurrent OCR/download tasks per fetch cycle
INSTITUTION    = "SEU"
SEU_TENANT_ID  = "00000000-0000-0000-0000-000000000001"
VISION_MODEL = "gpt-4o"
MAX_VISION_PAGES = 10

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

COURSE_CODE_RE = re.compile(r'\b([A-Z]{2,6}\s*\d{3,4})\b', re.IGNORECASE)
EXAM_KEYWORDS  = re.compile(r'\b(final|midterm|quiz|exam|اختبار|نهائي|منتصف|كويز)\b', re.IGNORECASE)


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _sanitize_text(text: str) -> str:
    """Strip characters PostgreSQL cannot store in text columns (null bytes, etc.)."""
    return text.replace("\x00", "")


def detect_course_code(text: str) -> Optional[str]:
    if not text:
        return None
    m = COURSE_CODE_RE.search(text)
    return m.group(1).upper().replace(" ", "") if m else None


def detect_exam_type(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ("final", "نهائي")):
        return "final"
    if any(k in t for k in ("midterm", "mid", "منتصف")):
        return "midterm"
    if any(k in t for k in ("quiz", "كويز")):
        return "quiz"
    return None


def detect_language(text: str) -> Optional[str]:
    if not text:
        return None
    arabic = sum(1 for c in text if '؀' <= c <= 'ۿ')
    latin  = sum(1 for c in text if c.isalpha() and c.isascii())
    if arabic == 0 and latin == 0:
        return None
    if arabic > latin * 2:
        return "ar"
    if latin > arabic * 2:
        return "en"
    return "mixed"


async def get_pending_jobs(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type":    "in.(audio_transcribe,telegram_media)",
            "status":      "in.(pending,failed)",
            "retry_count": f"lt.{MAX_RETRIES}",
            "limit":       str(PARALLEL_JOBS),
            "order":       "created_at.asc",
        },
    )
    if r.status_code >= 400:
        log("GET_JOBS_ERROR", status=r.status_code, error=r.text[:120])
        return []
    return r.json()


async def update_job(http: httpx.AsyncClient, job_id: str, status: str,
                     result=None, error=None, retry_count=None):
    body = {"status": status}
    if result      is not None: body["result"]      = result
    if error       is not None: body["error"]        = error
    if retry_count is not None: body["retry_count"]  = retry_count
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/processing_jobs?id=eq.{job_id}",
        headers=HEADERS,
        json=body,
    )
    if r.status_code >= 400:
        log("UPDATE_JOB_ERROR", id=job_id, status=r.status_code, error=r.text[:120])


def _bare_channel_id(cid: int) -> int:
    """Strip -100 MTProto prefix from full channel IDs for use with PeerChannel."""
    if cid < 0:
        s = str(abs(cid))
        if s.startswith("100") and len(s) > 4:
            return int(s[3:])
    return abs(cid)


async def resolve_entity(client: TelegramClient, chat_id: str, chat_type: str):
    cid = int(chat_id)
    if chat_type in ("channel", "megagroup"):
        return await client.get_entity(PeerChannel(_bare_channel_id(cid)))
    if chat_type == "group":
        return await client.get_entity(PeerChat(abs(cid)))
    try:
        return await client.get_entity(cid)
    except Exception:
        return await client.get_entity(PeerChannel(_bare_channel_id(cid)))


# ── Audio ──────────────────────────────────────────────────────────────────────

async def transcribe_audio(file_path: str) -> str:
    async with httpx.AsyncClient(timeout=120) as http:
        with open(file_path, "rb") as f:
            files = {
                "file":     (Path(file_path).name, f, "audio/ogg"),
                "model":    (None, "gpt-4o-mini-transcribe"),
                "language": (None, "ar"),
            }
            r = await http.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files=files,
            )
            r.raise_for_status()
            return r.json().get("text", "")


async def handle_audio_transcribe(client: TelegramClient, http: httpx.AsyncClient, job: dict):
    job_id      = job["id"]
    payload     = job.get("payload") or {}
    retry_count = job.get("retry_count") or 0

    chat_id = payload.get("platform_chat_id")
    msg_id  = payload.get("platform_message_id")
    if not chat_id or not msg_id:
        await update_job(http, job_id, "failed", error="missing platform ids",
                         retry_count=retry_count + 1)
        return

    log("AUDIO_JOB_START", id=job_id, chat_id=chat_id, msg_id=msg_id, attempt=retry_count + 1)
    try:
        await update_job(http, job_id, "processing")
        entity = await resolve_entity(client, chat_id, payload.get("chat_type", ""))
        msg    = await client.get_messages(entity, ids=int(msg_id))

        if not msg:
            raise RuntimeError("Telegram message not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = await client.download_media(msg, file=tmpdir)
            if not file_path:
                raise RuntimeError("download failed")

            file_path = str(file_path)
            if file_path.endswith(".oga"):
                new_path = file_path[:-4] + ".ogg"
                Path(file_path).rename(new_path)
                file_path = new_path

            log("AUDIO_DOWNLOADED", id=job_id)
            text = await transcribe_audio(file_path)
            log("AUDIO_TRANSCRIBED", id=job_id, chars=len(text))

        # write transcript back to media_files
        r = await http.patch(
            f"{SUPABASE_URL}/rest/v1/media_files"
            f"?platform_chat_id=eq.{chat_id}&platform_message_id=eq.{msg_id}",
            headers=HEADERS,
            json={"download_status": "processed", "extraction_status": "processed",
                  "extracted_text": text},
        )
        if r.status_code >= 400:
            log("MEDIA_FILES_UPDATE_ERROR", status=r.status_code, error=r.text[:120])

        await update_job(http, job_id, "processed",
                         result={"transcript": text, "char_count": len(text)})
        log("AUDIO_JOB_DONE", id=job_id)

    except Exception as e:
        new_retry = retry_count + 1
        log("AUDIO_JOB_FAILED", id=job_id, attempt=new_retry,
            abandoned=new_retry >= MAX_RETRIES, error=str(e)[:120])
        await update_job(http, job_id, "failed", error=str(e), retry_count=new_retry)


# ── PDF / Image ────────────────────────────────────────────────────────────────

def extract_pdf_text(file_path: str) -> tuple[str, int, str, float]:
    doc = fitz.open(file_path)
    page_count = len(doc)
    pages_text, digital, image_only = [], 0, 0
    for page in doc:
        text = page.get_text().strip()
        if text:
            pages_text.append(text)
            digital += 1
        else:
            pages_text.append(f"[PAGE {page.number + 1}: image-only]")
            image_only += 1
    doc.close()
    if image_only == 0:
        return "\n\n".join(pages_text), page_count, "digital", 1.0
    if digital == 0:
        return "\n\n".join(pages_text), page_count, "needs_ocr", 0.0
    return "\n\n".join(pages_text), page_count, "mixed", digital / page_count


async def ocr_with_vision(ai: AsyncOpenAI, file_path: str, hint: str = "") -> str:
    path, images_b64 = Path(file_path), []
    mime = "image/jpeg"
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(file_path)
        for i in range(min(len(doc), MAX_VISION_PAGES)):
            pix = doc[i].get_pixmap(dpi=150)
            images_b64.append(base64.b64encode(pix.tobytes("png")).decode())
        doc.close()
        mime = "image/png"
    else:
        with open(file_path, "rb") as f:
            images_b64 = [base64.b64encode(f.read()).decode()]
        ext = path.suffix.lower()
        if ext in (".jpg", ".jpeg"): mime = "image/jpeg"
        elif ext == ".png":          mime = "image/png"
        elif ext == ".webp":         mime = "image/webp"

    system_hint = (
        "Extract all text from this academic document. "
        "Preserve structure: question numbers, answer choices, tables. "
        "If Arabic, output Arabic faithfully. Output only extracted text, no commentary."
    )
    if hint:
        system_hint += f" Context: {hint}"

    content = [{"type": "text", "text": system_hint}]
    for b64 in images_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}})

    resp = await ai.chat.completions.create(
        model=VISION_MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return resp.choices[0].message.content or ""


async def create_source_document(http: httpx.AsyncClient, row: dict) -> Optional[str]:
    r = await http.post(f"{SUPABASE_URL}/rest/v1/source_documents", headers=HEADERS, json=row)
    if r.status_code == 409:
        rows = await http.get(
            f"{SUPABASE_URL}/rest/v1/source_documents", headers=HEADERS,
            params={"content_hash": f"eq.{row['content_hash']}", "limit": "1"},
        )
        existing = rows.json()
        return existing[0]["id"] if existing else None
    if r.status_code >= 400:
        log("SOURCE_DOC_ERROR", status=r.status_code, error=r.text[:200])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def create_embed_job(http: httpx.AsyncClient, source_document_id: str) -> bool:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/processing_jobs", headers=HEADERS,
        json={
            "job_type": "embed_chunk", "status": "pending",
            "payload":  {"source_document_id": source_document_id},
            "retry_count": 0,
            "target_table": "document_chunks",
            "target_key":   source_document_id,
        },
    )
    if r.status_code >= 400:
        log("EMBED_JOB_ERROR", status=r.status_code, error=r.text[:120])
        return False
    return True


async def handle_telegram_media(client: TelegramClient, ai: AsyncOpenAI,
                                 http: httpx.AsyncClient, job: dict):
    job_id      = job["id"]
    payload     = job.get("payload") or {}
    retry_count = job.get("retry_count") or 0

    chat_id    = payload.get("platform_chat_id")
    msg_id     = payload.get("platform_message_id")
    media_type = payload.get("media_type", "unknown")
    file_name  = payload.get("file_name") or "unknown"
    mime_type  = payload.get("mime_type") or ""
    caption    = payload.get("caption") or ""

    course_code  = payload.get("course_code")  or detect_course_code(caption) or detect_course_code(file_name)
    exam_type    = payload.get("exam_type")    or detect_exam_type(caption)    or detect_exam_type(file_name)
    academic_year = payload.get("academic_year")
    semester      = payload.get("semester")
    source_type   = "exam" if exam_type else ("study_plan" if "plan" in file_name.lower() else "upload")

    if not chat_id or not msg_id:
        await update_job(http, job_id, "failed", error="missing platform ids",
                         retry_count=retry_count + 1)
        return

    log("MEDIA_JOB_START", id=job_id, chat_id=chat_id, msg_id=msg_id,
        media_type=media_type, file=file_name, attempt=retry_count + 1)

    try:
        await update_job(http, job_id, "processing")
        entity = await resolve_entity(client, chat_id, payload.get("chat_type", ""))
        msg    = await client.get_messages(entity, ids=int(msg_id))

        if not msg:
            raise RuntimeError("Telegram message not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = await client.download_media(msg, file=tmpdir)
            if not file_path:
                raise RuntimeError("Telegram download failed")
            file_path = str(file_path)
            log("MEDIA_DOWNLOADED", id=job_id, path=file_path)

            extracted_text  = ""
            page_count      = None
            extraction_method = None
            ocr_confidence  = None
            hint = f"Course: {course_code or 'unknown'}, type: {exam_type or 'unknown'}"

            if media_type == "pdf" or file_path.lower().endswith(".pdf"):
                text, pages, method, conf = extract_pdf_text(file_path)
                page_count = pages
                if method in ("digital", "mixed"):
                    extracted_text    = text
                    extraction_method = method
                    ocr_confidence    = conf if conf < 1.0 else None
                    if method == "mixed":
                        ocr_text = await ocr_with_vision(ai, file_path, hint=hint)
                        extracted_text = f"{text}\n\n[OCR PAGES]\n{ocr_text}"
                else:
                    log("FULL_OCR", id=job_id, pages=pages)
                    extracted_text    = await ocr_with_vision(ai, file_path, hint=hint)
                    extraction_method = "ocr_vision"
            elif media_type == "image" or any(
                file_path.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp")
            ):
                extracted_text    = await ocr_with_vision(ai, file_path, hint=hint)
                extraction_method = "ocr_vision"
                page_count        = 1
            else:
                raise RuntimeError(f"Unsupported media type: {file_path}")

            extracted_text = _sanitize_text(extracted_text)
            language     = detect_language(extracted_text)
            char_count   = len(extracted_text)
            content_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
            log("MEDIA_EXTRACTED", id=job_id, method=extraction_method, chars=char_count)

        doc_id = await create_source_document(http, {
            "tenant_id":          SEU_TENANT_ID,
            "content_hash":       content_hash,
            "storage_path":       None,
            "raw_file_deleted_at": "now()",
            "file_name":          file_name,
            "mime_type":          mime_type,
            "source_type":        source_type,
            "institution":        INSTITUTION,
            "course_code":        course_code,
            "exam_type":          exam_type,
            "academic_year":      academic_year,
            "semester":           semester,
            "language":           language,
            "page_count":         page_count,
            "extraction_method":  extraction_method,
            "extracted_text":     extracted_text,
            "ocr_confidence":     ocr_confidence,
            "processing_status":  "extracted",
        })

        if not doc_id:
            raise RuntimeError("Failed to create source_document row")

        log("SOURCE_DOC_CREATED", id=job_id, doc_id=doc_id, course=course_code)
        await create_embed_job(http, doc_id)
        await update_job(http, job_id, "processed",
                         result={"doc_id": doc_id, "chars": char_count,
                                 "method": extraction_method, "course": course_code})
        log("MEDIA_JOB_DONE", id=job_id, doc_id=doc_id)

    except Exception as e:
        new_retry = retry_count + 1
        log("MEDIA_JOB_FAILED", id=job_id, attempt=new_retry,
            abandoned=new_retry >= MAX_RETRIES, error=str(e)[:120])
        await update_job(http, job_id, "failed", error=str(e), retry_count=new_retry)


# ── Main loop ──────────────────────────────────────────────────────────────────

CONNECT_RETRY_SECONDS = 180  # wait before retrying after AuthKeyDuplicatedError — 3 min gives Telegram time to release the auth key


async def main():
    log("DOWNLOAD_WORKER_START", max_retries=MAX_RETRIES)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    while True:
        client = TelegramClient(
            StringSession(os.environ.get("TELEGRAM_WORKER_SESSION_STRING") or os.environ["TELEGRAM_SESSION_STRING"]),
            int(os.environ["TELEGRAM_API_ID"]),
            os.environ["TELEGRAM_API_HASH"],
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=30)

            if not await client.is_user_authorized():
                log("SESSION_NOT_AUTHORIZED")
                await client.disconnect()
                return

            me = await client.get_me()
            log("DOWNLOAD_WORKER_READY", user_id=me.id)

            async with httpx.AsyncClient(timeout=120) as http:
                try:
                    from heartbeat import Heartbeat
                    hb = Heartbeat(http, worker_id="media_worker", service_name="media", interval_s=30)
                except Exception:
                    hb = None

                while True:
                    jobs = await get_pending_jobs(http)
                    if not jobs:
                        if hb:
                            await hb.beat(status="idle")
                        await asyncio.sleep(SLEEP_SECONDS)
                        continue

                    if hb:
                        await hb.beat(status="running")

                    log("JOBS_FETCHED", count=len(jobs))

                    async def dispatch(job):
                        jtype = job.get("job_type")
                        if jtype == "audio_transcribe":
                            await handle_audio_transcribe(client, http, job)
                        elif jtype == "telegram_media":
                            await handle_telegram_media(client, ai, http, job)
                        else:
                            log("UNKNOWN_JOB_TYPE", job_type=jtype, id=job["id"])

                    await asyncio.gather(*[dispatch(j) for j in jobs])

        except AuthKeyDuplicatedError:
            log("AUTH_KEY_DUPLICATED_retry_in", seconds=CONNECT_RETRY_SECONDS)
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(CONNECT_RETRY_SECONDS)

        except Exception as e:
            log("UNEXPECTED_ERROR", error=str(e)[:200])
            try:
                await client.disconnect()
            except Exception:
                pass
            raise


if __name__ == "__main__":
    asyncio.run(main())
