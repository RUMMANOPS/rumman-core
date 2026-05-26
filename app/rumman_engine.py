import os
import json
import asyncio
from datetime import timezone

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

load_dotenv()

ENABLE_BACKFILL = False

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
        json=payload,
    )

    if r.status_code == 409:
        return "duplicate"

    if r.status_code >= 400:
        print(f"{table.upper()}_ERROR", r.status_code, r.text)
        return "error"

    return "inserted"


async def get_current_sync_state(http, platform_chat_id):
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers=HEADERS,
        params={
            "platform_chat_id": f"eq.{platform_chat_id}",
            "select": "total_messages_seen,oldest_message_id,newest_message_id",
            "limit": "1",
        },
    )

    if r.status_code >= 400:
        print("SYNC_STATE_READ_ERROR", r.status_code, r.text)
        return None

    rows = r.json()
    return rows[0] if rows else None


async def update_sync_state(http, payload):
    current = await get_current_sync_state(http, payload["platform_chat_id"])

    current_total = 0
    current_oldest = None

    if current:
        current_total = current.get("total_messages_seen") or 0
        current_oldest = current.get("oldest_message_id")

    message_id = int(payload["platform_message_id"])

    sync_payload = {
        "platform_chat_id": payload["platform_chat_id"],
        "chat_type": payload["telegram_chat_type"],
        "chat_name": payload["chat_name"],
        "backfill_completed": False,
        "oldest_message_id": current_oldest,
        "newest_message_id": message_id,
        "total_messages_seen": current_total + 1,
    }

    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers={
            **HEADERS,
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        params={
            "on_conflict": "platform_chat_id",
        },
        json=sync_payload,
    )

    if r.status_code >= 400:
        print("SYNC_STATE_ERROR", r.status_code, r.text)


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
            "source": source,
            "size_bytes": fm["size_bytes"],
            "is_forward": bool(getattr(msg, "fwd_from", None)),
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
        await update_sync_state(http, payload)


async def historical_backfill(client):
    print("\nBACKFILL DISABLED\n")


async def main():
    print("\nRUMMAN ENGINE STARTING...\n")

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )

    await client.start()

    me = await client.get_me()

    print(f"LOGGED_IN: {me.id}")

    @client.on(events.NewMessage)
    async def new_message_handler(event):
        try:
            chat = await event.get_chat()

            chat_name = (
                getattr(chat, "title", None)
                or getattr(chat, "first_name", None)
                or "Unknown"
            )

            chat_type = (
                "private"
                if event.is_private
                else "channel"
                if event.is_channel
                else "group"
            )

            async with httpx.AsyncClient(timeout=30) as http:
                await process_message(
                    http=http,
                    msg=event.message,
                    chat_name=chat_name,
                    chat_type=chat_type,
                    chat_id=event.chat_id,
                    source="live",
                )

        except Exception as e:
            print("LIVE_ERROR", str(e))

    if ENABLE_BACKFILL:
        await historical_backfill(client)
    else:
        print("\nHISTORICAL BACKFILL DISABLED\n")

    print("\nLIVE LISTENER ACTIVE\n")

    await client.run_until_disconnected()


asyncio.run(main())
