import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, PeerChat, PeerUser
from telethon.errors import (
    AuthKeyDuplicatedError,
    FloodWaitError,
    RPCError,
)

load_dotenv()

WORKER_ID = os.getenv("WORKER_ID", "telegram-backfill-worker-1")
BATCH_SLEEP_SECONDS = int(os.getenv("BACKFILL_SLEEP_SECONDS", "3"))
LEASE_MINUTES = int(os.getenv("BACKFILL_LEASE_MINUTES", "10"))

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Dedicated session prevents AUTH_KEY_DUPLICATED with the live listener.
# Generate with: python3 auth_session.py (use personal number, not RUMMAN number)
# Set TELEGRAM_BACKFILL_SESSION_STRING on Railway backfill service.
# Falls back to TELEGRAM_SESSION_STRING if not set (may conflict with listener).
_BACKFILL_SESSION = (
    os.environ.get("TELEGRAM_BACKFILL_SESSION_STRING")
    or os.environ["TELEGRAM_SESSION_STRING"]
)
if not os.environ.get("TELEGRAM_BACKFILL_SESSION_STRING"):
    print(
        "WARN_SESSION_SHARED | TELEGRAM_BACKFILL_SESSION_STRING not set — "
        "sharing session with listener may cause AUTH_KEY_DUPLICATED. "
        "Run auth_session.py with personal number and set the env var.",
        flush=True,
    )


def _channel_id(chat_id: int) -> int:
    """Strip Telegram's full MTProto channel ID (-100XXXXXXXXXX) to bare channel id."""
    if chat_id < 0:
        s = str(abs(chat_id))
        if s.startswith("100") and len(s) > 4:
            return int(s[3:])
    return abs(chat_id)

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
        "tenant_id": SEU_TENANT_ID,
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


async def maybe_spawn_media_job(http: httpx.AsyncClient, msg, mt: str, fm: dict, chat_id: int, chat_type: str):
    """Create a processing_job for actionable media found during backfill."""
    base = {
        "platform_chat_id": str(chat_id),
        "platform_message_id": str(msg.id),
        "chat_type": chat_type,
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
    """Resolve a Telegram entity.
    Channels/supergroups: strip the -100 MTProto prefix before calling PeerChannel —
    PeerChannel requires a positive bare channel id."""
    if chat_type in ("channel", "megagroup"):
        return await client.get_entity(PeerChannel(_channel_id(chat_id)))
    if chat_type == "group":
        return await client.get_entity(PeerChat(abs(chat_id)))
    return await client.get_entity(chat_id)


async def save_partial_progress(http, job, processed, last_id, oldest_id, max_id):
    """Save mid-batch progress and release the lease so the job retries."""
    await rest_patch(
        http,
        "telegram_backfill_jobs",
        {"id": f"eq.{job['id']}", "worker_id": f"eq.{WORKER_ID}"},
        {
            "total_processed": (job.get("total_processed") or 0) + processed,
            "last_processed_message_id": last_id or max_id,
            "oldest_reached_message_id": oldest_id or max_id,
            "status": "pending",
            "worker_id": None,
            "locked_at": None,
            "lease_expires_at": None,
            "heartbeat_at": utc_now(),
            "updated_at": utc_now(),
        },
    )


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

    try:
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
                    await maybe_spawn_media_job(http, msg, mt, fm, chat_id, chat_type)
                    media_queued += 1
            elif result == "duplicate":
                duplicate += 1

            if processed % 100 == 0:
                await heartbeat(http, job["id"])
                print(
                    f"BACKFILL_PROGRESS | chat={chat_name} | processed={processed} | inserted={inserted} | duplicate={duplicate} | media_queued={media_queued}",
                    flush=True,
                )

    except (FloodWaitError,) as e:
        wait = getattr(e, "seconds", 60)
        print(f"FLOOD_WAIT | chat={chat_name} | wait={wait}s | saving_partial", flush=True)
        await save_partial_progress(http, job, processed, last_id, oldest_id, max_id)
        await asyncio.sleep(wait + 5)
        return  # outer loop will re-claim

    except Exception as e:
        # Disconnect / connection errors — save whatever we got and let main() reconnect
        err_str = str(e).lower()
        if "disconnect" in err_str or "connection" in err_str or "not connected" in err_str:
            print(f"BACKFILL_DISCONNECT | chat={chat_name} | saved={processed} | {e}", flush=True)
            await save_partial_progress(http, job, processed, last_id, oldest_id, max_id)
            raise  # bubbles to main() reconnect loop
        raise  # other errors bubble to fail_job

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
RECONNECT_SLEEP_SECONDS = 15


def _is_disconnect(e: Exception) -> bool:
    s = str(e).lower()
    return "disconnect" in s or "connection" in s or "not connected" in s


async def discover_missing_jobs(http: httpx.AsyncClient) -> int:
    """
    Scan telegram_sync_state for chats the live listener has seen but that have no
    backfill job yet. Creates pending jobs so the worker picks them up automatically.
    Returns the number of jobs created.
    """
    # All chats the listener has tracked
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers=HEADERS,
        params={"select": "platform_chat_id,chat_name,chat_type", "limit": "1000"},
    )
    if r.status_code >= 400:
        print(f"DISCOVER_SYNC_READ_ERROR | {r.status_code}", flush=True)
        return 0
    sync_chats = r.json()

    # All existing backfill job chat IDs (normalised to bare positive form)
    r2 = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"select": "platform_chat_id", "limit": "5000"},
    )
    if r2.status_code >= 400:
        print(f"DISCOVER_JOBS_READ_ERROR | {r2.status_code}", flush=True)
        return 0

    existing_norm: set[str] = set()
    for row in r2.json():
        try:
            existing_norm.add(str(_channel_id(int(row["platform_chat_id"]))))
        except (ValueError, TypeError):
            pass

    created = 0
    for chat in sync_chats:
        try:
            norm = str(_channel_id(int(chat["platform_chat_id"])))
        except (ValueError, TypeError):
            continue
        if norm in existing_norm:
            continue
        r3 = await http.post(
            f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
            headers=HEADERS,
            json={
                "platform_chat_id": chat["platform_chat_id"],
                "chat_name": chat.get("chat_name") or "",
                "chat_type": chat.get("chat_type") or "unknown",
                "status": "pending",
                "batch_size": 500,
                "retry_count": 0,
            },
        )
        if r3.status_code in (200, 201):
            existing_norm.add(norm)
            created += 1
            print(
                f"MISSING_JOB_CREATED | chat={chat.get('chat_name')} | id={chat['platform_chat_id']}",
                flush=True,
            )
        elif r3.status_code == 409:
            existing_norm.add(norm)  # race — already exists

    print(f"DISCOVER_DONE | created={created} | total_sync_chats={len(sync_chats)}", flush=True)
    return created


async def main():
    print(f"TELEGRAM BACKFILL WORKER STARTING | session={'dedicated' if os.environ.get('TELEGRAM_BACKFILL_SESSION_STRING') else 'shared_warn'}", flush=True)

    async with httpx.AsyncClient(timeout=120) as http:
        # At startup: create backfill jobs for any chats the listener has seen
        # but that don't have a job yet (e.g. new groups joined while worker was off).
        await discover_missing_jobs(http)

        while True:
            client = TelegramClient(
                StringSession(_BACKFILL_SESSION),
                int(os.environ["TELEGRAM_API_ID"]),
                os.environ["TELEGRAM_API_HASH"],
            )
            try:
                await asyncio.wait_for(client.connect(), timeout=30)
                if not await client.is_user_authorized():
                    print("NOT_AUTHORIZED — check TELEGRAM_SESSION_STRING", flush=True)
                    return

                me = await client.get_me()
                print(f"LOGGED_IN: {me.id}", flush=True)

                # Prime entity cache so PeerChannel lookups succeed without access_hash errors
                print("PRIMING_ENTITY_CACHE", flush=True)
                try:
                    await asyncio.wait_for(client.get_dialogs(limit=200), timeout=120)
                    print("ENTITY_CACHE_READY", flush=True)
                except asyncio.TimeoutError:
                    print("ENTITY_CACHE_TIMEOUT | continuing with partial cache", flush=True)

                while True:
                    job = await claim_job(http)

                    if not job:
                        await asyncio.sleep(NO_JOBS_SLEEP_SECONDS)
                        continue

                    try:
                        await process_job(client, http, job)
                    except AuthKeyDuplicatedError as e:
                        print(f"AUTH_KEY_DUPLICATED | sleeping {RECONNECT_SLEEP_SECONDS}s", flush=True)
                        # process_job already saved partial progress; break inner to reconnect
                        break
                    except Exception as e:
                        if _is_disconnect(e):
                            # process_job saved partial progress; reconnect
                            print(f"RECONNECTING | {e}", flush=True)
                            break
                        print(f"BACKFILL_JOB_ERROR | job={job['id']} | {e}", flush=True)
                        await fail_job(http, job, e)

            except AuthKeyDuplicatedError:
                print(f"AUTH_KEY_DUPLICATED_at_connect | sleeping {RECONNECT_SLEEP_SECONDS}s", flush=True)
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                print(f"CONNECT_FAILED | {e} | sleeping {RECONNECT_SLEEP_SECONDS}s", flush=True)
            except Exception as e:
                if _is_disconnect(e):
                    print(f"OUTER_DISCONNECT | {e} | sleeping {RECONNECT_SLEEP_SECONDS}s", flush=True)
                else:
                    print(f"OUTER_ERROR | {e}", flush=True)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            await asyncio.sleep(RECONNECT_SLEEP_SECONDS)


asyncio.run(main())
