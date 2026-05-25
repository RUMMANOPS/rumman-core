import os
import json
import asyncio
from datetime import timezone

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

load_dotenv()

LIMIT_PER_CHAT = 20

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

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

async def post(http, table, payload):
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        json=payload
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        print(f"{table.upper()}_ERROR", r.status_code, r.text)
        return "error"
    return "inserted"

async def create_job(http, job_type, target_table, target_key, payload=None, priority=50):
    job = {
        "job_type": job_type,
        "target_table": target_table,
        "target_key": str(target_key),
        "priority": priority,
        "payload": payload or {},
    }
    result = await post(http, "processing_jobs", job)
    if result == "inserted":
        print(f"JOB_CREATED | {job_type} | {target_key}")

async def register_media(http, payload):
    if not payload["has_media"]:
        return

    media = {
        "message_platform": payload["platform"],
        "platform_chat_id": payload["platform_chat_id"],
        "platform_message_id": payload["platform_message_id"],
        "media_type": payload["media_type"],
        "file_name": payload.get("file_name"),
        "mime_type": payload.get("mime_type"),
        "size_bytes": payload.get("metadata", {}).get("size_bytes"),
        "download_status": "pending",
        "extraction_status": "pending",
        "metadata": {
            "chat_name": payload["chat_name"],
            "source": payload["metadata"].get("source"),
        },
    }

    result = await post(http, "media_files", media)

    if result == "inserted":
        print(f"MEDIA_REGISTERED | {media['media_type']} | {media['platform_message_id']}")

        if media["media_type"] in ["photo", "gif", "video"]:
            await create_job(http, "vision_extract", "media_files", media["platform_message_id"], media, priority=60)
        elif media["media_type"] in ["voice", "audio"]:
            await create_job(http, "audio_transcribe", "media_files", media["platform_message_id"], media, priority=70)
        else:
            await create_job(http, "file_parse", "media_files", media["platform_message_id"], media, priority=55)

async def build_payload(msg, chat_name, chat_type, chat_id, source):
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
            " ".join(filter(None, [
                getattr(sender, "first_name", None),
                getattr(sender, "last_name", None),
            ]))
            if sender else None
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
            "source": source,
            "size_bytes": fm["size_bytes"],
            "is_forward": bool(getattr(msg, "fwd_from", None)),
            "forwarded_from": str(getattr(msg, "fwd_from", None)) if getattr(msg, "fwd_from", None) else None,
        },
    }

async def process_message(http, msg, chat_name, chat_type, chat_id, source):
    payload = await build_payload(msg, chat_name, chat_type, chat_id, source)

    result = await post(http, "messages", payload)

    print(
        f"{result.upper()} | "
        f"{chat_name} | "
        f"{payload['message_type']} | "
        f"{payload['platform_message_id']}"
    )

    if result == "inserted":
        await create_job(http, "message_ingested", "messages", payload["platform_message_id"], {
            "message_type": payload["message_type"],
            "has_media": payload["has_media"],
            "source": source,
        })
        await register_media(http, payload)

async def historical_backfill(client):
    print("\nSTARTING_BACKFILL\n")

    async with httpx.AsyncClient(timeout=30) as http:
        async for dialog in client.iter_dialogs():
            if not dialog.is_group and not dialog.is_channel:
                continue

            print(f"\nBACKFILL_CHAT: {dialog.name}")

            chat_type = "channel" if dialog.is_channel else "group"

            async for msg in client.iter_messages(dialog.entity, limit=LIMIT_PER_CHAT):
                try:
                    await process_message(
                        http=http,
                        msg=msg,
                        chat_name=dialog.name,
                        chat_type=chat_type,
                        chat_id=dialog.entity.id,
                        source="backfill"
                    )
                except Exception as e:
                    print("BACKFILL_ERROR", str(e))

    print("\nBACKFILL_COMPLETE\n")

async def main():
    print("\nRUMMAN ENGINE STARTING...\n")

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"]
    )

    await client.start()
    me = await client.get_me()
    print(f"LOGGED_IN: {me.id}")

    @client.on(events.NewMessage)
    async def new_message_handler(event):
        try:
            chat = await event.get_chat()
            chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "Unknown"
            chat_type = "private" if event.is_private else "channel" if event.is_channel else "group"

            async with httpx.AsyncClient(timeout=30) as http:
                await process_message(
                    http=http,
                    msg=event.message,
                    chat_name=chat_name,
                    chat_type=chat_type,
                    chat_id=event.chat_id,
                    source="live"
                )
        except Exception as e:
            print("LIVE_ERROR", str(e))

    @client.on(events.MessageEdited)
    async def edited_message_handler(event):
        try:
            chat = await event.get_chat()
            chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "Unknown"
            print(f"EDIT_DETECTED | {chat_name} | {event.message.id}")

            async with httpx.AsyncClient(timeout=30) as http:
                await create_job(http, "message_edited", "messages", event.message.id, {
                    "platform_message_id": str(event.message.id),
                    "message_text": event.message.message or "",
                }, priority=80)

        except Exception as e:
            print("EDIT_ERROR", str(e))

    await historical_backfill(client)

    print("\nLIVE LISTENER ACTIVE\n")

    await client.run_until_disconnected()

asyncio.run(main())
