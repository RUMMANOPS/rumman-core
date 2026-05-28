import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

load_dotenv()

WORKER_ID = os.getenv("WORKER_ID", "telegram-backfill-worker-1")
BATCH_SLEEP_SECONDS = int(os.getenv("BACKFILL_SLEEP_SECONDS", "3"))
LEASE_MINUTES = int(os.getenv("BACKFILL_LEASE_MINUTES", "10"))

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def lease_until():
    return (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)).isoformat()


def message_type(msg):
    if getattr(msg, "poll", None):
        return "poll"
    if msg.gif:
        return "gif"
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.document:
        return "file"
    if msg.message:
        return "text"
    return "other"


def file_meta(msg):
    f = getattr(msg, "file", None)
    return {
        "file_name": getattr(f, "name", None),
        "mime_type": getattr(f, "mime_type", None),
        "size_bytes": getattr(f, "size", None),
    }


async def rest_get(http, table, params):
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        params=params,
    )
    if r.status_code >= 400:
        print(f"{table.upper()}_GET_ERROR", r.status_code, r.text, flush=True)
        return []
    return r.json()


async def rest_post(http, table, payload):
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        json=payload,
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        print(f"{table.upper()}_POST_ERROR", r.status_code, r.text, flush=True)
        return "error"
    return "inserted"


async def rest_patch(http, table, filters, payload):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        params=filters,
        json=payload,
    )
    if r.status_code >= 400:
        print(f"{table.upper()}_PATCH_ERROR", r.status_code, r.text, flush=True)
        return False
    return True


async def release_stale_running_jobs(http):
    now = utc_now()

    rows = await rest_get(
        http,
        "telegram_backfill_jobs",
        {
            "status": "eq.running",
            "lease_expires_at": f"lt.{now}",
            "select": "id,chat_name,worker_id,lease_expires_at",
        },
    )

    for job in rows:
        await rest_patch(
            http,
            "telegram_backfill_jobs",
            {"id": f"eq.{job['id']}"},
            {
                "status": "pending",
                "worker_id": None,
                "locked_at": None,
                "lease_expires_at": None,
                "updated_at": utc_now(),
            },
        )
        print(
            f"STALE_JOB_RELEASED | id={job['id']} | chat={job.get('chat_name')} | old_worker={job.get('worker_id')}",
            flush=True,
        )


async def claim_job(http):
    await release_stale_running_jobs(http)

    rows = await rest_get(
        http,
        "telegram_backfill_jobs",
        {
            "status": "eq.pending",
            "order": "created_at.asc",
            "limit": "1",
        },
    )

    if not rows:
        print("NO_PENDING_BACKFILL_JOBS", flush=True)
        return None

    job = rows[0]

    ok = await rest_patch(
        http,
        "telegram_backfill_jobs",
        {
            "id": f"eq.{job['id']}",
            "status": "eq.pending",
        },
        {
            "status": "running",
            "worker_id": WORKER_ID,
            "locked_at": utc_now(),
            "started_at": job.get("started_at") or utc_now(),
            "heartbeat_at": utc_now(),
            "lease_expires_at": lease_until(),
            "updated_at": utc_now(),
        },
    )

    if not ok:
        return None

    fresh = await rest_get(
        http,
        "telegram_backfill_jobs",
        {
            "id": f"eq.{job['id']}",
            "worker_id": f"eq.{WORKER_ID}",
            "status": "eq.running",
            "limit": "1",
        },
    )

    if not fresh:
        print("JOB_CLAIM_LOST", flush=True)
        return None

    claimed = fresh[0]

    print(
        f"JOB_CLAIMED | id={claimed['id']} | chat={claimed.get('chat_name')} | chat_id={claimed['platform_chat_id']}",
        flush=True,
    )

    return claimed


async def heartbeat(http, job_id):
    await rest_patch(
        http,
        "telegram_backfill_jobs",
        {
            "id": f"eq.{job_id}",
            "worker_id": f"eq.{WORKER_ID}",
            "status": "eq.running",
        },
        {
            "heartbeat_at": utc_now(),
            "lease_expires_at": lease_until(),
            "updated_at": utc_now(),
        },
    )


async def build_payload(msg, chat_name, chat_type, chat_id):
    sender = await msg.get_sender()
    mt = message_type(msg)
    fm = file_meta(msg)

    return {
        "platform": "telegram_user_client",
        "platform_message_id": str(msg.id),
        "platform_chat_id": str(chat_id),
        "telegram_chat_type": chat_type,
        "chat_name": chat_name,
        "platform_user_id": str(getattr(sender, "id", "")) if sender else None,
        "platform_username": getattr(sender, "username", None) if sender else None,
        "platform_user_first_name": getattr(sender, "first_name", None) if sender else None,
        "sender_name": (
            " ".join(
                filter(
                    None,
                    [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ],
                )
            )
            if sender
            else None
        ),
        "message_text": msg.message or "",
        "message_type": mt,
        "message_date": msg.date.astimezone(timezone.utc).isoformat() if msg.date else None,
        "has_media": mt not in ["text", "poll", "other"],
        "media_type": mt if mt not in ["text", "poll", "other"] else None,
        "file_name": fm["file_name"],
        "mime_type": fm["mime_type"],
        "reply_to_message_id": str(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
        "edited_at": msg.edit_date.astimezone(timezone.utc).isoformat() if msg.edit_date else None,
        "raw_json": json.loads(json.dumps(msg.to_dict(), default=str)),
        "metadata": {
            "source": "backfill",
            "size_bytes": fm["size_bytes"],
            "is_forward": bool(getattr(msg, "fwd_from", None)),
        },
    }


async def update_job_progress(http, job, processed, last_id, oldest_id, done=False):
    payload = {
        "total_processed": (job.get("total_processed") or 0) + processed,
        "last_processed_message_id": last_id,
        "oldest_reached_message_id": oldest_id,
        "heartbeat_at": utc_now(),
        "updated_at": utc_now(),
    }

    if done:
        payload["status"] = "completed"
        payload["completed_at"] = utc_now()
        payload["locked_at"] = None
        payload["worker_id"] = None
        payload["lease_expires_at"] = None
    else:
        payload["status"] = "pending"
        payload["locked_at"] = None
        payload["worker_id"] = None
        payload["lease_expires_at"] = None

    await rest_patch(
        http,
        "telegram_backfill_jobs",
        {
            "id": f"eq.{job['id']}",
            "worker_id": f"eq.{WORKER_ID}",
        },
        payload,
    )


async def fail_job(http, job, error):
    await rest_patch(
        http,
        "telegram_backfill_jobs",
        {
            "id": f"eq.{job['id']}",
            "worker_id": f"eq.{WORKER_ID}",
        },
        {
            "status": "failed",
            "error_message": str(error),
            "retry_count": (job.get("retry_count") or 0) + 1,
            "heartbeat_at": utc_now(),
            "updated_at": utc_now(),
            "locked_at": None,
            "worker_id": None,
            "lease_expires_at": None,
        },
    )


def is_pdf(fm: dict) -> bool:
    mime = (fm.get("mime_type") or "").lower()
    name = (fm.get("file_name") or "").lower()
    return "pdf" in mime or name.endswith(".pdf")


def is_image(mt: str, fm: dict) -> bool:
    mime = (fm.get("mime_type") or "").lower()
    return mt == "photo" or mime.startswith("image/")


JOB_TARGET_TABLE = {
    "telegram_media":  "source_documents",
    "audio_transcribe": "media_files",
    "embed_chunk":     "document_chunks",
    "pdf_extract":     "source_documents",
}


async def create_processing_job(http: httpx.AsyncClient, job_type: str, payload: dict, target_key: str) -> bool:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        json={
            "job_type": job_type,
            "status": "pending",
            "payload": payload,
            "retry_count": 0,
            "target_table": JOB_TARGET_TABLE.get(job_type, "messages"),
            "target_key": target_key,
        },
    )
    if r.status_code == 409:
        return True  # already queued
    if r.status_code >= 400:
        print(f"PROCESSING_JOB_CREATE_ERROR | type={job_type} | status={r.status_code} | {r.text[:120]}", flush=True)
        return False
    return True


async def maybe_spawn_media_job(http: httpx.AsyncClient, msg, mt: str, fm: dict, chat_id: int):
    """Create a processing_job for actionable media found during backfill."""
    base = {
        "platform_chat_id": str(chat_id),
        "platform_message_id": str(msg.id),
        "file_name": fm.get("file_name"),
        "mime_type": fm.get("mime_type"),
        "caption": (msg.message or "")[:500],
    }

    key = str(msg.id)
    if mt in ("voice", "audio"):
        await create_processing_job(http, "audio_transcribe", base, target_key=key)
    elif mt == "file" and is_pdf(fm):
        await create_processing_job(http, "telegram_media", {**base, "media_type": "pdf"}, target_key=key)
    elif is_image(mt, fm):
        await create_processing_job(http, "telegram_media", {**base, "media_type": "image"}, target_key=key)


async def resolve_entity(client: TelegramClient, chat_id: int, chat_type: str):
    """Resolve a Telegram entity using the correct peer type.
    IDs stored without the -100 prefix must be resolved as PeerChannel,
    not PeerUser, or Telethon will fail to find the entity."""
    if chat_type in ("channel", "megagroup"):
        return await client.get_entity(PeerChannel(chat_id))
    if chat_type == "group":
        return await client.get_entity(PeerChat(chat_id))
    return await client.get_entity(chat_id)


async def process_job(client, http, job):
    chat_id = int(job["platform_chat_id"])
    chat_name = job.get("chat_name") or "Unknown"
    chat_type = job.get("chat_type") or "unknown"
    batch_size = job.get("batch_size") or 500

    entity = await resolve_entity(client, chat_id, chat_type)

    max_id = job.get("oldest_reached_message_id") or job.get("last_processed_message_id")

    processed = 0
    inserted = 0
    duplicate = 0
    media_queued = 0
    last_id = None
    oldest_id = None

    kwargs = {"limit": batch_size}
    if max_id:
        kwargs["max_id"] = int(max_id)

    print(
        f"BACKFILL_BATCH_START | chat={chat_name} | max_id={max_id} | limit={batch_size}",
        flush=True,
    )

    async for msg in client.iter_messages(entity, **kwargs):
        processed += 1
        last_id = msg.id
        oldest_id = msg.id if oldest_id is None else min(oldest_id, msg.id)

        mt = message_type(msg)
        fm = file_meta(msg)
        payload = await build_payload(msg, chat_name, chat_type, chat_id)
        result = await rest_post(http, "messages", payload)

        if result == "inserted":
            inserted += 1
            # Only spawn media jobs for freshly inserted messages — duplicates were
            # either already queued by the live listener or processed in a prior batch.
            if mt not in ("text", "poll", "other"):
                await maybe_spawn_media_job(http, msg, mt, fm, chat_id)
                media_queued += 1
        elif result == "duplicate":
            duplicate += 1

        if processed % 100 == 0:
            await heartbeat(http, job["id"])
            print(
                f"BACKFILL_PROGRESS | chat={chat_name} | processed={processed} | inserted={inserted} | duplicate={duplicate} | media_queued={media_queued}",
                flush=True,
            )

    done = processed == 0

    await update_job_progress(
        http=http,
        job=job,
        processed=processed,
        last_id=last_id or max_id,
        oldest_id=oldest_id or max_id,
        done=done,
    )

    print(
        f"BACKFILL_BATCH_DONE | chat={chat_name} | processed={processed} | inserted={inserted} | duplicate={duplicate} | media_queued={media_queued} | done={done}",
        flush=True,
    )

    if BATCH_SLEEP_SECONDS > 0:
        await asyncio.sleep(BATCH_SLEEP_SECONDS)


NO_JOBS_SLEEP_SECONDS = 30


async def main():
    print("TELEGRAM BACKFILL WORKER STARTING", flush=True)

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )

    await client.start()
    me = await client.get_me()
    print(f"LOGGED_IN: {me.id}", flush=True)

    async with httpx.AsyncClient(timeout=60) as http:
        while True:
            job = await claim_job(http)

            if not job:
                await asyncio.sleep(NO_JOBS_SLEEP_SECONDS)
                continue

            try:
                await process_job(client, http, job)
            except Exception as e:
                print("BACKFILL_JOB_ERROR", str(e), flush=True)
                await fail_job(http, job, e)

    await client.disconnect()
    print("TELEGRAM BACKFILL WORKER STOPPED", flush=True)


asyncio.run(main())
